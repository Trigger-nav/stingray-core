#!/usr/bin/env python3
"""
Cross-source consistency check (ticket 0.5's "first real run" checklist,
CLAUDE.md) — compares NOMADS- and ECMWF-sourced weather at the same real
points/times. Rough agreement is the closest thing to an ex-post
correctness check available without eccodes-verified ground truth (see
CORE_PORTING_NOTES.md's B2/CLAUDE.md's GRIB-conventions gotcha), and it
doubles as an *empirical* check of the WW3 direction-convention assumption
(`ingest.grib_common.WW3_DIRECTION_IS_TO_CONVENTION`): if independently-
modelled wave direction from the two sources agrees to within a plausible
few tens of degrees, that supports the current (from-convention, unflipped)
assumption; if they disagree by something close to 180 degrees, that's
strong evidence the toggle needs flipping.

Usage: python3 -m ingest.verify_grib_consistency
       [--nomads PATH] [--ecmwf PATH] [--hours H1,H2,...]
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from core.weather import GriddedWeatherField, WeatherSample

# A handful of representative points within OPERATING_AREA_BBOX, away from
# the coastline (so both sources should have real, non-missing data).
CHECK_POINTS = [
    (42.0, 8.0),  # west of Corsica, open water
    (41.55, 9.65),  # north Tyrrhenian, east of Bonifacio — replaces (41.6, 9.2),
    # which is inside Corsica and was (correctly) land-masked on every run
    (43.2, 9.3),  # Ligurian, off Cap Corse
]
DEFAULT_HOURS = (0.0, 6.0, 12.0, 24.0)

DIRECTION_AGREEMENT_THRESHOLD_DEG = 60.0
DIRECTION_FLIP_SUSPECT_THRESHOLD_DEG = 120.0


def circular_direction_diff_deg(a_deg: float, b_deg: float) -> float:
    """Smallest angle between two compass directions, in [0, 180]."""
    diff = abs(a_deg - b_deg) % 360.0
    return min(diff, 360.0 - diff)


def wind_speed_ms(sample: WeatherSample) -> float:
    return math.hypot(sample.wind_u_ms, sample.wind_v_ms)


def compare_samples(nomads: WeatherSample, ecmwf: WeatherSample) -> dict | None:
    """Pure comparison of two co-located samples -- returns None if either
    source reports missing (nothing to compare), otherwise a dict of the
    differences this checklist item cares about."""
    if nomads.is_missing or ecmwf.is_missing:
        return None
    return {
        "hs_diff_m": abs(nomads.hs_m - ecmwf.hs_m),
        "wind_speed_diff_ms": abs(wind_speed_ms(nomads) - wind_speed_ms(ecmwf)),
        "wave_dir_diff_deg": circular_direction_diff_deg(nomads.wave_from_deg, ecmwf.wave_from_deg),
    }


def summarise(comparisons: list[dict]) -> dict:
    """Aggregate stats + the direction-convention verdict this check
    exists for."""
    if not comparisons:
        return {"n": 0, "verdict": "no comparable (non-missing on both sides) points/times found"}
    dir_diffs = [c["wave_dir_diff_deg"] for c in comparisons]
    mean_dir_diff = sum(dir_diffs) / len(dir_diffs)
    if mean_dir_diff >= DIRECTION_FLIP_SUSPECT_THRESHOLD_DEG:
        verdict = (
            f"mean wave-direction disagreement {mean_dir_diff:.0f} deg is close to 180 -- "
            "WW3_DIRECTION_IS_TO_CONVENTION in ingest/grib_common.py is probably wrong, flip it"
        )
    elif mean_dir_diff <= DIRECTION_AGREEMENT_THRESHOLD_DEG:
        verdict = f"mean wave-direction disagreement {mean_dir_diff:.0f} deg -- consistent"
    else:
        verdict = (
            f"mean wave-direction disagreement {mean_dir_diff:.0f} deg -- "
            "inconclusive, inspect manually"
        )
    return {
        "n": len(comparisons),
        "mean_hs_diff_m": sum(c["hs_diff_m"] for c in comparisons) / len(comparisons),
        "mean_wind_speed_diff_ms": sum(c["wind_speed_diff_ms"] for c in comparisons)
        / len(comparisons),
        "mean_wave_dir_diff_deg": mean_dir_diff,
        "verdict": verdict,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nomads", default="data/weather/nomads_western_med.npz")
    parser.add_argument("--ecmwf", default="data/weather/ecmwf_western_med.npz")
    parser.add_argument(
        "--hours", default=",".join(str(h) for h in DEFAULT_HOURS), help="comma-separated hours"
    )
    args = parser.parse_args()

    nomads_field = GriddedWeatherField.from_npz(Path(args.nomads))
    ecmwf_field = GriddedWeatherField.from_npz(Path(args.ecmwf))
    hours = [float(h) for h in args.hours.split(",")]

    comparisons = []
    for lat, lon in CHECK_POINTS:
        for t_h in hours:
            n = nomads_field.sample(lat, lon, t_h)
            e = ecmwf_field.sample(lat, lon, t_h)
            comparison = compare_samples(n, e)
            if comparison is None:
                print(f"  ({lat}, {lon}) @ t={t_h}h: missing on one/both sides, skipped")
                continue
            comparisons.append(comparison)
            print(
                f"  ({lat}, {lon}) @ t={t_h}h: "
                f"hs_diff={comparison['hs_diff_m']:.2f}m "
                f"wind_diff={comparison['wind_speed_diff_ms']:.2f}m/s "
                f"dir_diff={comparison['wave_dir_diff_deg']:.0f}deg"
            )

    summary = summarise(comparisons)
    print(f"\n{summary['n']} comparable points/times")
    print(summary["verdict"])


if __name__ == "__main__":
    main()
