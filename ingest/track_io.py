"""CSV read/write for `core.track.TrackPoint` (ticket B7 Part 1). One
column per `TrackPoint` field, `TRACK_CSV_COLUMNS`-ordered header —
`ingest/fetch_era5_track.py`'s annotator (Part 2) writes this format;
`fit/import_adapters.py`'s `AnnotatedTrackCsvAdapter` (Part 3) reads it
back. Deliberately the one place in `ingest/` that duplicates a few lines
of CSV logic rather than importing `fit/` directly -- see
`core/track.py`'s module docstring for why (`ingest/`/`fit/` never
depend on each other; they agree on a file format instead).
"""

from __future__ import annotations

import csv
from pathlib import Path

from core.track import TRACK_CSV_COLUMNS, TrackPoint

_OPTIONAL_FIELDS = (
    "hs_m",
    "period_peak_s",
    "period_mean_s",
    "wave_from_deg",
    "wind_u_ms",
    "wind_v_ms",
)


def write_track_csv(path: str | Path, points: list[TrackPoint]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRACK_CSV_COLUMNS)
        writer.writeheader()
        for p in points:
            row = {name: getattr(p, name) for name in TRACK_CSV_COLUMNS}
            for name in _OPTIONAL_FIELDS:
                if row[name] is None:
                    row[name] = ""
            writer.writerow(row)


def read_track_csv(path: str | Path) -> list[TrackPoint]:
    path = Path(path)
    points: list[TrackPoint] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            kwargs: dict[str, float | None] = {
                "t_epoch_s": float(row["t_epoch_s"]),
                "lat_deg": float(row["lat_deg"]),
                "lon_deg": float(row["lon_deg"]),
            }
            for name in _OPTIONAL_FIELDS:
                value = row.get(name, "")
                kwargs[name] = float(value) if value not in ("", None) else None
            points.append(TrackPoint(**kwargs))
    return points
