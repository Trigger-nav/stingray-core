#!/usr/bin/env python3
"""Cloud-role cron wrapper (ticket R1): fetches weather for every pack
listed in a `region_packs.yaml` manifest (the same file
`api.state.load_region_packs` reads), instead of N independent crontab
lines -- a packs manifest that's also cron's single source of truth for
which bboxes get fetched keeps crontab and the packs list from silently
drifting apart. Each pack's fetch is exactly the same
`ingest.fetch_grib_nomads`/`fetch_grib_ecmwf` invocation B7 Part 1
already built (`--bbox`/`--out`/`--coastline-path`/etc, subprocessed so
this stays a thin orchestration layer, not a reimplementation of either
fetcher's own logic) -- just looped over every configured pack.

Usage:
    python3 -m ingest.fetch_all_packs --packs-manifest data/region_packs.yaml \\
        [--source nomads|ecmwf|both]
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys

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
        for module in modules:
            if fetch_one(module, pack) != 0:
                failures += 1

    if failures:
        logger.error("%d fetch(es) failed -- see above", failures)
        sys.exit(1)


if __name__ == "__main__":
    main()
