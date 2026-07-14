"""fit.pipeline's fit_twin/fit_twin_from_segments wiring (ticket B7 Part
4), including the regression guard for the two gaps found during plan
review and implementation: (1) fit_twin originally had no way to select
passage_holdout_split at all; (2) fit_twin's own internal
extract_steady_state_segments call always discards identity, so
holdout_group_by is only reachable via the new fit_twin_from_segments
entry point on real B7 import data.
"""

from __future__ import annotations

import csv
from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest

from core.vessel_spec import VesselSpec
from fit.import_adapters import MonitoringCsvAdapter
from fit.import_pipeline import rows_to_segments
from fit.pipeline import fit_twin, fit_twin_from_segments
from fit.synthetic import default_synthetic_conditions, generate_synthetic_telemetry

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"
MS_TO_KN = 1.943844


def _make_passage_csv(path, passage_id, t0_epoch_s, n=10):
    # Two engine configs across two halves -- extract_steady_state_segments
    # treats an active_engines change as a hard segment boundary, so this
    # deterministically yields >=2 segments per passage (a single constant
    # stream collapses to exactly one segment, which is too few for a
    # meaningful train/holdout split in these tests).
    header = [
        "timestamp_utc", "lat_deg", "lon_deg", "stw_kn", "heading_deg",
        "active_engines", "fuel_kg_per_h", "hs_m", "period_peak_s", "wave_from_deg",
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i in range(n):
            ts = datetime.fromtimestamp(t0_epoch_s + i * 60.0, tz=UTC).isoformat()
            engines = 2 if i < n // 2 else 1
            row = [ts, "41.0", "8.0", "12.0", "90", str(engines), "180.0", "0.1", "6.0", "180"]
            w.writerow(row)


def test_fit_twin_default_call_shape_is_unaffected(tmp_path):
    base_spec = VesselSpec.from_yaml(DEFAULT_SPEC_PATH)
    ground_truth = replace(
        base_spec,
        calm_resistance=replace(
            base_spec.calm_resistance,
            linear_coefficient=base_spec.calm_resistance.linear_coefficient * 1.1,
        ),
    )
    conditions = default_synthetic_conditions()
    samples, _ = generate_synthetic_telemetry(
        ground_truth, conditions, rng=np.random.default_rng(1)
    )

    # no holdout_group_by -- identical call shape to every pre-B7 caller.
    fitted = fit_twin(samples, base_spec, rng=np.random.default_rng(2))
    assert fitted.fit_report.validation.n_groups_train is None
    assert fitted.fit_report.validation.n_groups_holdout is None


def test_fit_twin_holdout_group_by_raises_because_its_own_segments_have_no_identity():
    base_spec = VesselSpec.from_yaml(DEFAULT_SPEC_PATH)
    conditions = default_synthetic_conditions()
    samples, _ = generate_synthetic_telemetry(base_spec, conditions, rng=np.random.default_rng(3))

    # fit_twin's own extract_steady_state_segments call always produces
    # vessel_id=passage_id=None segments -- passage_holdout_split correctly
    # refuses ("needs at least 2 distinct groups, got 1: {None}").
    with pytest.raises(ValueError, match="at least 2"):
        fit_twin(samples, base_spec, holdout_group_by="passage_id", rng=np.random.default_rng(4))


def test_fit_twin_from_segments_end_to_end_with_real_import_data(tmp_path):
    base_spec = VesselSpec.from_yaml(DEFAULT_SPEC_PATH)
    t0 = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    all_rows = []
    for i, passage_id in enumerate(("passage-1", "passage-2", "passage-3")):
        path = tmp_path / f"{passage_id}.csv"
        _make_passage_csv(path, passage_id, t0 + i * 200_000.0)
        all_rows += MonitoringCsvAdapter().parse(path, vessel_id="mys50", passage_id=passage_id)

    segments = rows_to_segments(all_rows, min_duration_s=60.0)
    assert len({s.passage_id for s in segments}) == 3

    fitted = fit_twin_from_segments(
        segments,
        base_spec,
        holdout_group_by="passage_id",
        holdout_fraction=0.4,
        rng=np.random.default_rng(5),
    )
    validation = fitted.fit_report.validation
    assert validation.n_groups_train is not None
    assert validation.n_groups_holdout is not None
    assert validation.n_groups_train + validation.n_groups_holdout == 3


def test_fit_twin_from_segments_without_holdout_group_by_matches_flat_split_behaviour(tmp_path):
    base_spec = VesselSpec.from_yaml(DEFAULT_SPEC_PATH)
    t0 = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    path = tmp_path / "one_passage.csv"
    _make_passage_csv(path, "passage-1", t0, n=20)
    rows = MonitoringCsvAdapter().parse(path, vessel_id="mys50", passage_id="passage-1")
    segments = rows_to_segments(rows, min_duration_s=60.0)

    fitted = fit_twin_from_segments(segments, base_spec, rng=np.random.default_rng(6))
    assert fitted.fit_report.validation.n_groups_train is None
    assert fitted.fit_report.validation.n_groups_holdout is None
