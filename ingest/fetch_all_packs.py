#!/usr/bin/env python3
"""Cloud-role cron wrapper (ticket R1): fetches weather for every pack
listed in a `region_packs.yaml` manifest (the same file
`api.state.load_region_packs` reads), instead of N independent crontab
lines -- a packs manifest that's also cron's single source of truth for
which bboxes get fetched keeps crontab and the packs list from silently
drifting apart. Each pack's wind/wave fetch is exactly the same
`ingest.fetch_grib_nomads`/`fetch_grib_ecmwf` invocation B7 Part 1
already built (`--bbox`/`--out`/`--coastline-path`/etc, subprocessed so
this stays a thin orchestration layer, not a reimplementation of either
fetcher's own logic) -- just looped over every configured pack.

Ticket C1: a pack with `RegionPack.currents_dataset_id` set also gets a
currents fetch (`ingest.fetch_currents_cmems`) + merge
(`ingest.merge_currents`) step, run *after* that pack's own wind/wave
fetch succeeds (the merge needs the wind/wave npz to already exist as its
target). A currents-step failure is deliberately isolated from the
wind/wave path -- see `run_currents_step`'s docstring.

Usage:
    python3 -m ingest.fetch_all_packs --packs-manifest data/region_packs.yaml \\
        [--source nomads|ecmwf|both]
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from core.regionpack import RegionPack

logger = logging.getLogger(__name__)

FETCHER_MODULES = {
    "nomads": "ingest.fetch_grib_nomads",
    "ecmwf": "ingest.fetch_grib_ecmwf",
}


def load_packs(packs_manifest_path: str) -> list[RegionPack]:
    with open(packs_manifest_path) as f:
        raw = yaml.safe_load(f)
    return [RegionPack.from_yaml(p) for p in raw["packs"]]


def fetch_one(module: str, pack: RegionPack) -> int:
    """One subprocess invocation of an existing fetcher, unchanged --
    this function's only job is building the right `--bbox`/`--out`/
    geography-path args for `pack` and reporting the exit code, never
    reimplementing the fetch itself."""
    cmd = [
        sys.executable,
        "-m",
        module,
        "--bbox",
        str(pack.bbox[0]),
        str(pack.bbox[1]),
        str(pack.bbox[2]),
        str(pack.bbox[3]),
        "--out",
        pack.weather_npz_path,
        "--coastline-path",
        pack.coastline_path,
        "--bathymetry-path",
        pack.bathymetry_path,
        "--nogo-path",
        pack.nogo_path,
        "--tss-path",
        pack.tss_path,
    ]
    logger.info("pack %r: running %s", pack.pack_id, " ".join(cmd))
    result = subprocess.run(cmd)  # noqa: S603 -- fixed module names, no shell, trusted args
    if result.returncode != 0:
        logger.error(
            "pack %r: %s failed with exit code %d", pack.pack_id, module, result.returncode
        )
    return result.returncode


def run_currents_step(pack: RegionPack) -> None:
    """Ticket C1, required amendment: a currents-step failure (fetch or
    merge) must never block that pack's wind/wave fetch or hot-swap --
    the npz's `current_u_ms`/`current_v_ms` simply stay at whatever they
    were before this cycle (a previous successful merge's real values, or
    zeros if this is the first cycle / currents were just enabled).
    Deliberately swallows *any* failure here (subprocess non-zero exit,
    or an unexpected raised exception) and logs at `WARNING` naming the
    pack, which step failed, and the error -- this function's whole
    reason to exist is that `main()`'s own per-pack loop must never see a
    currents-step failure at all, so it can't accidentally be counted
    toward the wind/wave failure total that controls `sys.exit(1)`.
    No-op entirely when `pack.currents_dataset_id` is unset (every pack
    that hasn't opted into currents -- exactly today's behaviour)."""
    if pack.currents_dataset_id is None:
        return
    try:
        with tempfile.TemporaryDirectory() as tmp:
            currents_npz = str(Path(tmp) / f"currents_{pack.pack_id}.npz")
            fetch_cmd = [
                sys.executable,
                "-m",
                "ingest.fetch_currents_cmems",
                "--bbox",
                str(pack.bbox[0]),
                str(pack.bbox[1]),
                str(pack.bbox[2]),
                str(pack.bbox[3]),
                "--dataset-id",
                pack.currents_dataset_id,
                "--out",
                currents_npz,
                "--coastline-path",
                pack.coastline_path,
                "--bathymetry-path",
                pack.bathymetry_path,
                "--nogo-path",
                pack.nogo_path,
                "--tss-path",
                pack.tss_path,
            ]
            logger.info("pack %r: running currents fetch: %s", pack.pack_id, " ".join(fetch_cmd))
            result = subprocess.run(fetch_cmd)  # noqa: S603
            if result.returncode != 0:
                logger.warning(
                    "pack %r: currents fetch failed (exit %d) -- keeping previous/zero "
                    "currents for this pack, wind/wave unaffected",
                    pack.pack_id,
                    result.returncode,
                )
                return

            merge_cmd = [
                sys.executable,
                "-m",
                "ingest.merge_currents",
                "--weather-npz",
                pack.weather_npz_path,
                "--currents-npz",
                currents_npz,
                "--pack-id",
                pack.pack_id,
            ]
            logger.info("pack %r: running currents merge: %s", pack.pack_id, " ".join(merge_cmd))
            result = subprocess.run(merge_cmd)  # noqa: S603
            if result.returncode != 0:
                logger.warning(
                    "pack %r: currents merge failed (exit %d) -- keeping previous/zero "
                    "currents for this pack, wind/wave unaffected",
                    pack.pack_id,
                    result.returncode,
                )
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, this is amendment 1's whole point
        logger.warning(
            "pack %r: currents step raised an unexpected error (%s) -- keeping "
            "previous/zero currents for this pack, wind/wave unaffected",
            pack.pack_id,
            exc,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs-manifest", default="data/region_packs.yaml")
    parser.add_argument("--source", choices=["nomads", "ecmwf", "both"], default="both")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    packs = load_packs(args.packs_manifest)
    logger.info(
        "fetching weather for %d configured pack(s): %s",
        len(packs),
        [p.pack_id for p in packs],
    )

    modules = (
        list(FETCHER_MODULES.values())
        if args.source == "both"
        else [FETCHER_MODULES[args.source]]
    )
    failures = 0
    for pack in packs:
        pack_wind_wave_ok = True
        for module in modules:
            if fetch_one(module, pack) != 0:
                failures += 1
                pack_wind_wave_ok = False

        # Currents only merges onto a wind/wave npz that actually exists
        # and is fresh -- skip entirely if this cycle's own wind/wave
        # fetch failed (a stale npz from a previous cycle is still there,
        # untouched, exactly as if currents were disabled this cycle too).
        if pack_wind_wave_ok:
            run_currents_step(pack)

    if failures:
        logger.error("%d fetch(es) failed -- see above", failures)
        sys.exit(1)


if __name__ == "__main__":
    main()
