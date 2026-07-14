#!/usr/bin/env python3
"""Fetch named ports from NGA's World Port Index (WPI, Pub 150) for a
region pack's `RegionPack.ports` (ticket R1). Real, live endpoint --
verified against msi.nga.mil directly during this ticket (2026-07-14),
not guessed: `GET https://msi.nga.mil/api/publications/world-port-index
?output=json` returns `{"ports": [...]}`, one record per port, no
authentication required. The endpoint supports server-side filters
(`regionName=`/`countryName=`/`portName=`/`harborSize=`) but **no
lat/lon or bbox filter** -- confirmed by inspecting the live site's own
React bundle (`msi.nga.mil`'s query-builder UI only offers those four
fields), so this script fetches the whole database (2,951 ports,
~6.3MB, one request) and filters to `--bbox` client-side, the same
shape every other `ingest/fetch_*.py` script's `--bbox` already takes.

Each port's `latitude`/`longitude` are sexagesimal strings (e.g.
`"51°30'00\"N"`, `"2°43'00\"W"`) -- `_parse_dms` converts to decimal
degrees; verified against a real response for "ENGLAND SW COAST"
(Plymouth/Falmouth country) during this ticket.

Usage:
    python3 -m ingest.fetch_wpi_ports --bbox LON_MIN LAT_MIN LON_MAX LAT_MAX \\
        --out data/region_packs/ports_<pack>.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import urllib.request

logger = logging.getLogger(__name__)

WPI_API_URL = "https://msi.nga.mil/api/publications/world-port-index?output=json"

_DMS_RE = re.compile(r'(\d+)\D(\d+)\D([\d.]+)\D*([NSEW])')


def _parse_dms(value: str) -> float:
    """`"51°30'00\"N"` -> 51.5, `"2°43'00\"W"` -> -2.716666...
    Regex matches degrees/minutes/seconds/hemisphere without anchoring on
    the exact `°`/`'`/`"` punctuation -- verified against every distinct
    pattern present in a real full-database fetch during this ticket
    (1-3 digit degrees, 1-2 digit minutes/seconds, optional decimal
    seconds -- see this module's docstring)."""
    m = _DMS_RE.match(value.strip())
    if not m:
        raise ValueError(f"unrecognised WPI lat/lon format: {value!r}")
    deg, minutes, seconds, hemi = m.groups()
    decimal = float(deg) + float(minutes) / 60 + float(seconds) / 3600
    return -decimal if hemi in ("S", "W") else decimal


def fetch_all_ports() -> list[dict]:
    """One unfiltered GET -- the whole WPI database, ~6.3MB. No
    server-side bbox filter exists (see module docstring); filtering
    happens in `ports_within_bbox` instead."""
    with urllib.request.urlopen(WPI_API_URL, timeout=60) as response:  # noqa: S310
        data = json.load(response)
    return data["ports"]


def ports_within_bbox(
    ports: list[dict], bbox: tuple[float, float, float, float]
) -> dict[str, tuple[float, float]]:
    """Filters to `bbox` (lon_min, lat_min, lon_max, lat_max) client-side,
    keyed by a normalised port name -- matching `RegionPack.ports`'
    `dict[str, LatLon]` schema. A name collision within the filtered set
    (rare at bbox scale, but WPI port names aren't globally unique) is
    disambiguated with the port's own WPI number, logged loudly rather
    than silently dropping one of the two real ports."""
    lon_min, lat_min, lon_max, lat_max = bbox
    result: dict[str, tuple[float, float]] = {}
    for port in ports:
        try:
            lat = _parse_dms(port["latitude"])
            lon = _parse_dms(port["longitude"])
        except (ValueError, KeyError):
            logger.warning("skipping port with unparseable lat/lon: %r", port.get("portName"))
            continue
        if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
            continue
        name = re.sub(r"[^a-z0-9]+", "_", port["portName"].strip().lower()).strip("_")
        if name in result:
            disambiguated = f"{name}_{port['portNumber']}"
            logger.warning(
                "port name %r collides within this bbox -- disambiguating as %r",
                name,
                disambiguated,
            )
            name = disambiguated
        result[name] = (lat, lon)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        required=True,
        metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
    )
    parser.add_argument("--out", required=True, help="output JSON path")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    bbox = tuple(args.bbox)
    logger.info("fetching full WPI database from %s", WPI_API_URL)
    ports = fetch_all_ports()
    logger.info("%d ports in the full database", len(ports))

    filtered = ports_within_bbox(ports, bbox)
    logger.info("%d ports within bbox %s", len(filtered), bbox)
    if not filtered:
        logger.warning(
            "no WPI ports found within this bbox -- the pack's RegionPack.ports "
            "will be empty, which is valid (a pack can rely on arbitrary "
            "endpoints/favourites alone) but worth double-checking the bbox"
        )

    with open(args.out, "w") as f:
        json.dump({name: [lat, lon] for name, (lat, lon) in filtered.items()}, f, indent=2)
    logger.info("wrote %s", args.out)


if __name__ == "__main__":
    main()
