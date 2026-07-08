#!/usr/bin/env python3
"""Thin CLI over `fit/pipeline.py`'s `fit_twin` (ticket 0.6).

Two modes:
- `--synthetic-demo`: runs the full pipeline against
  `fit/synthetic.py`'s generator (known ground truth, realistic noise +
  junk) and prints the validation report -- works today, no real data.
- `--telemetry PATH --base-spec PATH.yaml --out PATH.yaml`: the eventual
  Phase 1 real-data entry point. `--base-spec` supplies the fixed fields
  (hull, engine names/MCR, hotel load, comfort, wear policy) this ticket
  doesn't fit -- typically the same synthetic-placeholder YAML used
  today, since only `calm_resistance`/engine SFOC/`added_resistance` get
  replaced.

Usage: python3 -m fit.cli --synthetic-demo
       python3 -m fit.cli --telemetry samples.csv --base-spec spec.yaml --out fitted.yaml
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from pathlib import Path

import yaml

from core.vessel_spec import VesselSpec
from fit.pipeline import FittedTwin, fit_twin
from fit.synthetic import default_synthetic_conditions, generate_synthetic_telemetry
from fit.telemetry import TelemetrySample

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"


def _load_telemetry_csv(path: Path) -> list[TelemetrySample]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [
            TelemetrySample(
                t_h=float(row["t_h"]),
                stw_ms=float(row["stw_ms"]),
                heading_deg=float(row["heading_deg"]),
                active_engines=int(row["active_engines"]),
                fuel_kg_per_h=float(row["fuel_kg_per_h"]),
                hs_m=float(row["hs_m"]),
                period_peak_s=float(row["period_peak_s"]),
                wave_from_deg=float(row["wave_from_deg"]),
            )
            for row in reader
        ]


def _spec_to_dict(spec: VesselSpec) -> dict:
    d = asdict(spec)
    d["engines"] = list(d["engines"])
    return d


def _print_report(fitted: FittedTwin) -> None:
    report = fitted.fit_report
    print("\n=== Calm resistance + SFOC fit ===")
    print(
        f"  engine configs present: {sorted(report.calm_resistance_result.engine_configs_present)}"
    )
    print(f"  training RMSE: {report.calm_resistance_result.residual_rmse:.2f} kg/h")
    for name, val in report.calm_resistance_result.params.items():
        shift = report.calm_resistance_result.prior_shift_sigma[name]
        print(f"  {name}: {val:.4g}  (prior shift: {shift:+.2f} sigma)")
    print("\n=== Added resistance fit ===")
    print(
        f"  engine configs present: {sorted(report.added_resistance_result.engine_configs_present)}"
    )
    print(f"  training RMSE: {report.added_resistance_result.residual_rmse:.2f} kg/h")
    for name, val in report.added_resistance_result.params.items():
        shift = report.added_resistance_result.prior_shift_sigma[name]
        print(f"  {name}: {val:.4g}  (prior shift: {shift:+.2f} sigma)")
    print("\n=== Holdout validation ===")
    v = report.validation
    print(f"  n_holdout: {v.n_holdout}")
    print(f"  RMSE: {v.rmse_kg_per_h:.2f} kg/h")
    print(f"  mean bias: {v.mean_bias_kg_per_h:+.2f} kg/h")
    print(f"  90% error band: +/- {v.error_band_kg_per_h:.2f} kg/h")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-demo", action="store_true")
    parser.add_argument("--telemetry", default=None, help="CSV path (real-data mode)")
    parser.add_argument("--base-spec", default=DEFAULT_SPEC_PATH)
    parser.add_argument("--out", default=None, help="write the fitted VesselSpec here as YAML")
    args = parser.parse_args()

    base_spec = VesselSpec.from_yaml(args.base_spec)

    if args.synthetic_demo:
        conditions = default_synthetic_conditions()
        samples, junk_intervals = generate_synthetic_telemetry(base_spec, conditions)
        print(
            f"generated {len(samples)} synthetic samples across {len(conditions)} conditions "
            f"({len(junk_intervals)} corrupted as junk)"
        )
        fitted = fit_twin(samples, base_spec)
    elif args.telemetry:
        samples = _load_telemetry_csv(Path(args.telemetry))
        print(f"loaded {len(samples)} telemetry samples from {args.telemetry}")
        fitted = fit_twin(samples, base_spec)
    else:
        parser.error("pass either --synthetic-demo or --telemetry PATH")
        return

    _print_report(fitted)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            yaml.safe_dump(_spec_to_dict(fitted.spec), f, sort_keys=False)
        print(f"\nwrote fitted spec -> {out_path}")


if __name__ == "__main__":
    main()
