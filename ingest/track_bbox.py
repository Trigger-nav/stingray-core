#!/usr/bin/env python3
"""Compute the covering bbox (and time range) for a historical track CSV
(ticket B7 Part 1) -- the first step of the Cloud VM runbook-style flow
for a new track-driven region: this bbox is what `fetch_gebco.py`/
`fetch_gshhg.py`/`fetch_grib_ecmwf.py`/`fetch_grib_nomads.py`'s `--bbox`
and `ingest/fetch_era5_track.py`'s annotator (Part 2) both consume.

Usage: python3 -m ingest.track_bbox TRACK_CSV [--margin-deg DEG]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from core.track import covering_bbox, covering_time_range_s
from ingest.track_io import read_track_csv


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("track_csv", help="a core.track.TrackPoint CSV (ingest/track_io.py)")
    parser.add_argument("--margin-deg", type=float, default=0.25)
    args = parser.parse_args()

    points = read_track_csv(args.track_csv)
    bbox = covering_bbox(points, margin_deg=args.margin_deg)
    t_min, t_max = covering_time_range_s(points)

    print(f"{len(points)} points loaded from {args.track_csv}")
    print(f"bbox (lon_min lat_min lon_max lat_max): {bbox[0]} {bbox[1]} {bbox[2]} {bbox[3]}")
    print(
        f"time range: {datetime.fromtimestamp(t_min, tz=UTC).isoformat()} to "
        f"{datetime.fromtimestamp(t_max, tz=UTC).isoformat()} "
        f"({(t_max - t_min) / 86400:.1f} days)"
    )
    print(f"--bbox {bbox[0]} {bbox[1]} {bbox[2]} {bbox[3]}")


if __name__ == "__main__":
    main()
