"""Env-driven settings for the planner service (ticket B1). A plain
dataclass + `from_env()`, not a new dependency (pydantic-settings isn't in
pyproject.toml) -- matches this project's "boring tech" bias.

`role` is the one flag that makes the identical codebase behave correctly
on either deployment target (contract point 2, docs/plans/ticket-B1.md
design 5): `cloud` serves `GET /v1/weather/latest.npz` and expects an
external cron to keep the local npz fresh (`ingest.fetch_grib_*`, never
imported here); `vessel` runs the opportunistic pull task
(api/weather_sync.py) instead and never touches `ingest.*` at all -- which
is also what keeps the Windows/macOS PyInstaller build free of cfgrib's
eccodes system dependency.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

Role = Literal["cloud", "vessel"]


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw is not None else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw is not None else default


@dataclass(frozen=True)
class Settings:
    role: Role = "vessel"
    host: str = "127.0.0.1"
    port: int = 8000

    vessel_spec_path: str = "data/vessel_specs/mys_50m_default.yaml"
    weather_npz_path: str = "data/weather/ecmwf_western_med.npz"
    # capture/'s local SQLite store (design 4/8) -- the planner process
    # only ever reads this file (GET /v1/telemetry/status); capture/service.py
    # is the sole writer, in a separate OS process.
    telemetry_db_path: str = "data/telemetry/telemetry.sqlite3"
    # None -> RealGeography()'s own committed-data defaults
    # (core.geography.DEFAULT_COASTLINE_PATH etc.).
    coastline_path: str | None = None
    bathymetry_path: str | None = None
    nogo_path: str | None = None
    tss_path: str | None = None

    pool_size: int | None = None  # None -> os.cpu_count()

    auth_user: str = ""
    auth_password: str = ""

    # vessel-role only (api/weather_sync.py).
    cloud_weather_url: str | None = None
    weather_sync_interval_s: float = 900.0  # 15 min, opportunistic

    # both roles (api/state.py's hot-swap watcher).
    weather_watch_interval_s: float = 30.0

    # job store eviction (api/jobs.py) -- bridge PCs run for weeks without
    # a restart, so the in-memory job dict needs bounded growth.
    job_ttl_s: float = 86_400.0  # 24h past finished_at
    job_max_size: int = 5_000
    job_eviction_interval_s: float = 300.0  # 5 min sweep cadence

    @classmethod
    def from_env(cls) -> Settings:
        role = os.environ.get("STINGRAY_ROLE", "vessel")
        if role not in ("cloud", "vessel"):
            raise ValueError(f"STINGRAY_ROLE must be 'cloud' or 'vessel', got {role!r}")
        pool_size_raw = os.environ.get("STINGRAY_POOL_SIZE")
        return cls(
            role=role,  # type: ignore[arg-type]
            host=os.environ.get("STINGRAY_HOST", "127.0.0.1"),
            port=_env_int("STINGRAY_PORT", 8000),
            vessel_spec_path=os.environ.get(
                "STINGRAY_VESSEL_SPEC_PATH", "data/vessel_specs/mys_50m_default.yaml"
            ),
            weather_npz_path=os.environ.get(
                "STINGRAY_WEATHER_NPZ_PATH", "data/weather/ecmwf_western_med.npz"
            ),
            telemetry_db_path=os.environ.get(
                "STINGRAY_TELEMETRY_DB_PATH", "data/telemetry/telemetry.sqlite3"
            ),
            coastline_path=os.environ.get("STINGRAY_COASTLINE_PATH"),
            bathymetry_path=os.environ.get("STINGRAY_BATHYMETRY_PATH"),
            nogo_path=os.environ.get("STINGRAY_NOGO_PATH"),
            tss_path=os.environ.get("STINGRAY_TSS_PATH"),
            pool_size=int(pool_size_raw) if pool_size_raw is not None else None,
            auth_user=os.environ.get("STINGRAY_API_USER", ""),
            auth_password=os.environ.get("STINGRAY_API_PASSWORD", ""),
            cloud_weather_url=os.environ.get("STINGRAY_CLOUD_WEATHER_URL"),
            weather_sync_interval_s=_env_float("STINGRAY_WEATHER_SYNC_INTERVAL_S", 900.0),
            weather_watch_interval_s=_env_float("STINGRAY_WEATHER_WATCH_INTERVAL_S", 30.0),
            job_ttl_s=_env_float("STINGRAY_JOB_TTL_S", 86_400.0),
            job_max_size=_env_int("STINGRAY_JOB_MAX_SIZE", 5_000),
            job_eviction_interval_s=_env_float("STINGRAY_JOB_EVICTION_INTERVAL_S", 300.0),
        )
