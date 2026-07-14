"""Vessel-role opportunistic weather pull (ticket B1 design 6): the bridge
PC never runs `ingest.*` (heavy GRIB fetch is cloud-side only, design 6/11
-- also what keeps a Windows/macOS PyInstaller build free of cfgrib's
eccodes dependency). Instead it periodically pulls the already-cropped,
compact npz from its configured cloud instance's `GET /v1/weather/
latest.npz`, writing it to the exact same local path `api/state.py`'s
hot-swap watcher already monitors -- no separate reload path, this feeds
that one mechanism, same as cron does on the cloud side.

Stdlib `urllib.request`, not a new HTTP-client dependency -- matches
`ingest/fetch_grib_ecmwf.py`'s existing precedent (ticket 0.5) of stdlib
Range requests, and keeps `api`'s extras minimal.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import urllib.error
import urllib.request

from api.config import Settings
from core.regionpack import RegionPack

logger = logging.getLogger(__name__)


def _basic_auth_header(config: Settings) -> dict[str, str]:
    if not (config.auth_user or config.auth_password):
        return {}
    token = base64.b64encode(f"{config.auth_user}:{config.auth_password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def sync_once_blocking(
    config: Settings, pack_id: str, weather_npz_path: str, last_modified: str | None
) -> str | None:
    """Blocking HTTP fetch -- call via `asyncio.to_thread`, never directly
    on the event loop. Returns the new `Last-Modified` header value on a
    successful download, or `None` if nothing changed (304) or the fetch
    failed. Failure is logged, never raised -- this is opportunistic,
    matching PRODUCT_SPEC.md §7's "intermittent, expensive satcom"
    connectivity assumption: a missed sync just means planning continues
    on last-synced weather until the next attempt.

    `pack_id`/`weather_npz_path` (ticket R1): this vessel pulls only the
    pack(s) its own `region_packs` configures it to care about -- a
    UK-only bridge PC never fetches the Med pack's npz just because the
    cloud instance happens to also serve it. Each pack keeps its own
    independent conditional-HTTP state (`last_modified`), so one pack's
    fetch failure/staleness never blocks another's."""
    if not config.cloud_weather_url:
        return None
    url = config.cloud_weather_url.rstrip("/") + f"/v1/weather/latest.npz?pack={pack_id}"
    headers = _basic_auth_header(config)
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            data = response.read()
            new_last_modified = response.headers.get("Last-Modified")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None
        logger.warning("weather sync fetch failed for pack %r: HTTP %d", pack_id, exc.code)
        return None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        logger.warning("weather sync fetch failed for pack %r: %s", pack_id, exc)
        return None

    # Atomic write -- api/state.py's hot-swap watcher only ever sees a
    # complete file, never a partial one mid-download.
    tmp_path = weather_npz_path + ".tmp"
    os.makedirs(os.path.dirname(weather_npz_path) or ".", exist_ok=True)
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, weather_npz_path)
    return new_last_modified


class WeatherSyncLoop:
    """Vessel-role-only background task (api/main.py's lifespan only
    starts this when `config.role == "vessel"`). Ticket R1: syncs every
    pack in `region_packs` (this instance's own configured packs, from
    `api.state.load_region_packs` -- typically just one for a
    single-region bridge PC) independently, each with its own
    conditional-HTTP state."""

    def __init__(self, config: Settings, region_packs: dict[str, RegionPack]) -> None:
        self._config = config
        self._region_packs = region_packs
        self._last_modified: dict[str, str | None] = dict.fromkeys(region_packs)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._config.weather_sync_interval_s)
            for pack_id, pack in self._region_packs.items():
                try:
                    new_last_modified = await asyncio.to_thread(
                        sync_once_blocking,
                        self._config,
                        pack_id,
                        pack.weather_npz_path,
                        self._last_modified[pack_id],
                    )
                    if new_last_modified:
                        self._last_modified[pack_id] = new_last_modified
                        logger.info(
                            "weather sync: pulled a fresh npz for pack %r from the cloud instance",
                            pack_id,
                        )
                except Exception:
                    logger.exception(
                        "weather sync loop iteration failed for pack %r, will retry", pack_id
                    )
