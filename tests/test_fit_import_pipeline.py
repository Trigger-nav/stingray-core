"""Amendment 1's regression tests (ticket B7 plan review) -- the original
draft's canonical_rows_to_telemetry_samples -> unchanged
extract_steady_state_segments path silently produced
vessel_id=None, passage_id=None, fuel_noise_multiplier=1.0 on every
high-frequency segment regardless of source. This file proves the fix:
identity and per-source noise actually reach the resulting segments.
"""

import pytest

from fit.calm_resistance import DEFAULT_FUEL_NOISE_STD_FRACTION
from fit.import_adapters import ELogbookAdapter, MonitoringCsvAdapter
from fit.import_pipeline import (
    DEFAULT_LOW_FREQ_NOISE_MULTIPLIER,
    _source_fuel_noise_multiplier,
    canonical_rows_to_telemetry_samples,
    daily_rows_to_segments,
    rows_to_segments,
    stamp_segment_provenance,
)
from fit.import_schema import (
    SOG_FALLBACK_NOISE_MULTIPLIER,
    SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION,
    CanonicalImportRow,
)
from fit.segments import SteadyStateSegment


def _hf_row(
    t_epoch_s,
    *,
    vessel_id="v1",
    passage_id="p1",
    source="monitoring_csv",
    stw_kn=10.0,
    sog_kn=None,
):
    return CanonicalImportRow(
        t_epoch_s=t_epoch_s,
        lat_deg=41.0,
        lon_deg=8.0,
        vessel_id=vessel_id,
        passage_id=passage_id,
        source=source,
        stw_kn=stw_kn,
        sog_kn=sog_kn,
        heading_deg=90.0,
        active_engines=2,
        fuel_kg_per_h=25.0,
        hs_m=1.0,
        period_peak_s=6.0,
        wave_from_deg=180.0,
    )


def test_source_fuel_noise_multiplier_known_sources():
    assert _source_fuel_noise_multiplier("monitoring_csv") == pytest.approx(1.0)
    expected = SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION["e_logbook"] / DEFAULT_FUEL_NOISE_STD_FRACTION
    assert _source_fuel_noise_multiplier("e_logbook") == pytest.approx(expected)


def test_source_fuel_noise_multiplier_stacks_sog_fallback():
    base = _source_fuel_noise_multiplier("e_logbook")
    with_sog = _source_fuel_noise_multiplier("e_logbook", uses_sog_fallback=True)
    assert with_sog == pytest.approx(base * SOG_FALLBACK_NOISE_MULTIPLIER)


def test_source_fuel_noise_multiplier_unknown_source_falls_back_to_default():
    assert _source_fuel_noise_multiplier("some_unseen_source") == pytest.approx(1.0)


def test_stamp_segment_provenance_sets_identity_and_multiplier():
    seg = SteadyStateSegment(
        t_start_h=0, t_end_h=1, mean_stw_ms=5, mean_heading_deg=0, active_engines=1,
        mean_fuel_kg_per_h=10, mean_hs_m=1, mean_period_peak_s=6, mean_wave_from_deg=0,
        duration_h=1, n_samples=1,
    )
    stamped = stamp_segment_provenance(
        [seg], vessel_id="v9", passage_id="p9", source="e_logbook"
    )[0]
    assert stamped.vessel_id == "v9"
    assert stamped.passage_id == "p9"
    expected = _source_fuel_noise_multiplier("e_logbook")
    assert stamped.fuel_noise_multiplier == pytest.approx(expected)
    # original untouched (dataclasses.replace doesn't mutate)
    assert seg.vessel_id is None


def test_canonical_rows_to_telemetry_samples_drops_identity():
    rows = [_hf_row(0.0), _hf_row(60.0)]
    samples = canonical_rows_to_telemetry_samples(rows)
    assert not hasattr(samples[0], "vessel_id")
    assert samples[0].stw_ms == pytest.approx(10.0 * 0.5144444444444445)


def test_canonical_rows_to_telemetry_samples_raises_on_missing_environment():
    row = CanonicalImportRow(
        t_epoch_s=0.0, lat_deg=41.0, lon_deg=8.0, vessel_id="v1", passage_id="p1",
        source="e_logbook", sog_kn=9.0, heading_deg=90.0, active_engines=1, fuel_kg_per_h=20.0,
        # no hs_m/period_peak_s/wave_from_deg
    )
    with pytest.raises(ValueError, match="hs_m"):
        canonical_rows_to_telemetry_samples([row])


def test_amendment_1_regression_elogbook_import_carries_identity_and_source_weighting(tmp_path):
    """The exact scenario named in review: a high-frequency e-logbook
    import must (a) produce segments with real vessel_id/passage_id
    (usable by passage_holdout_split), and (b) get e-logbook's noise
    fraction, not the 0.03 default."""
    import csv

    header = [
        "local_datetime", "timezone", "lat_deg", "lon_deg", "sog_kn", "heading_deg",
        "active_engines", "fuel_l_per_h", "hs_m", "period_peak_s", "wave_from_deg",
    ]
    path = tmp_path / "elog.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i in range(10):
            ts = f"2026-01-01 00:{i:02d}:00"
            w.writerow([ts, "UTC", "41.0", "8.0", "10.0", "90", "2", "29.4", "1.0", "6.0", "180"])

    rows = ELogbookAdapter().parse(path, vessel_id="mys50", passage_id="passage-1")
    segments = rows_to_segments(rows, min_duration_s=60.0)

    assert len(segments) >= 1
    seg = segments[0]
    assert seg.vessel_id == "mys50"
    assert seg.passage_id == "passage-1"
    expected_multiplier = _source_fuel_noise_multiplier("e_logbook", uses_sog_fallback=True)
    assert seg.fuel_noise_multiplier == pytest.approx(expected_multiplier)
    assert seg.fuel_noise_multiplier != pytest.approx(1.0)


def test_high_frequency_batches_are_grouped_independently_by_vessel_passage_source():
    p1_rows = [
        _hf_row(t, vessel_id="v1", passage_id="p1", source="monitoring_csv")
        for t in range(0, 600, 60)
    ]
    p2_rows = [
        _hf_row(t, vessel_id="v1", passage_id="p2", source="monitoring_csv")
        for t in range(10_000, 10_600, 60)
    ]
    segments = rows_to_segments(p1_rows + p2_rows, min_duration_s=60.0)
    passage_ids = {s.passage_id for s in segments}
    assert passage_ids == {"p1", "p2"}


def test_rows_to_segments_widens_noise_for_a_mixed_stw_sog_group():
    """Review fix: any(), not all() -- a group with *some* rows missing
    STW (falling back to SOG) must still get the wider SOG-fallback
    band, not just a group where every row lacks STW. The conservative
    choice for a batch with partial STW dropout."""
    mixed_rows = [
        _hf_row(t, source="e_logbook", stw_kn=10.0, sog_kn=None) for t in range(0, 300, 60)
    ]
    mixed_rows += [
        _hf_row(t, source="e_logbook", stw_kn=None, sog_kn=10.0) for t in range(300, 600, 60)
    ]
    all_stw_rows = [
        _hf_row(t, source="e_logbook", stw_kn=10.0, sog_kn=None) for t in range(0, 600, 60)
    ]

    mixed_segments = rows_to_segments(mixed_rows, min_duration_s=60.0)
    clean_segments = rows_to_segments(all_stw_rows, min_duration_s=60.0)

    expected_widened = _source_fuel_noise_multiplier("e_logbook", uses_sog_fallback=True)
    expected_clean = _source_fuel_noise_multiplier("e_logbook", uses_sog_fallback=False)
    assert mixed_segments[0].fuel_noise_multiplier == pytest.approx(expected_widened)
    assert clean_segments[0].fuel_noise_multiplier == pytest.approx(expected_clean)
    assert mixed_segments[0].fuel_noise_multiplier > clean_segments[0].fuel_noise_multiplier


def test_daily_rows_to_segments_uses_per_source_multiplier_times_low_freq_multiplier():
    row = CanonicalImportRow(
        t_epoch_s=0.0, lat_deg=41.0, lon_deg=8.0, vessel_id="v1", passage_id="p1",
        source="noon_report", sog_kn=9.0, heading_deg=90.0, active_engines=2,
        fuel_kg_per_h=100.0, hs_m=1.0, period_peak_s=6.0, wave_from_deg=180.0,
        is_low_frequency=True,
    )
    segs = daily_rows_to_segments([row])
    assert len(segs) == 1
    seg = segs[0]
    assert seg.n_samples == 1
    assert seg.vessel_id == "v1" and seg.passage_id == "p1"
    source_multiplier = _source_fuel_noise_multiplier("noon_report", uses_sog_fallback=True)
    expected = source_multiplier * DEFAULT_LOW_FREQ_NOISE_MULTIPLIER
    assert seg.fuel_noise_multiplier == pytest.approx(expected)


def test_daily_rows_to_segments_raises_on_missing_environment():
    row = CanonicalImportRow(
        t_epoch_s=0.0, lat_deg=41.0, lon_deg=8.0, vessel_id="v1", passage_id="p1",
        source="noon_report", sog_kn=9.0, heading_deg=90.0, active_engines=2,
        fuel_kg_per_h=100.0, is_low_frequency=True,
    )
    with pytest.raises(ValueError, match="hs_m"):
        daily_rows_to_segments([row])


def test_daily_rows_to_segments_raises_on_missing_heading_or_engines():
    """Review fix: a plain noon report typically has neither heading nor
    active_engines -- these must not be silently defaulted (0.0/1), since
    added_resistance.py uses mean_heading_deg for the relative wave angle
    and active_engines is central to calm/SFOC identifiability (0.6
    finding #1)."""
    base = dict(
        t_epoch_s=0.0, lat_deg=41.0, lon_deg=8.0, vessel_id="v1", passage_id="p1",
        source="noon_report", sog_kn=9.0, fuel_kg_per_h=100.0, hs_m=1.0,
        period_peak_s=6.0, wave_from_deg=180.0, is_low_frequency=True,
    )
    with pytest.raises(ValueError, match="heading_deg"):
        daily_rows_to_segments([CanonicalImportRow(active_engines=2, **base)])
    with pytest.raises(ValueError, match="active_engines"):
        daily_rows_to_segments([CanonicalImportRow(heading_deg=90.0, **base)])


def test_rows_to_segments_combines_low_and_high_frequency_rows():
    high = [_hf_row(t, passage_id="p1") for t in range(0, 600, 60)]
    low = [
        CanonicalImportRow(
            t_epoch_s=100_000.0, lat_deg=41.0, lon_deg=8.0, vessel_id="v1", passage_id="p2",
            source="noon_report", sog_kn=9.0, heading_deg=90.0, active_engines=2,
            fuel_kg_per_h=100.0, hs_m=1.0, period_peak_s=6.0, wave_from_deg=180.0,
            is_low_frequency=True,
        )
    ]
    segments = rows_to_segments(high + low, min_duration_s=60.0)
    passage_ids = {s.passage_id for s in segments}
    assert "p1" in passage_ids
    assert "p2" in passage_ids


def test_monitoring_csv_source_multiplier_is_one_matching_the_default_fraction():
    # monitoring_csv's provisional fraction deliberately equals fit's own
    # global default -- so a monitoring_csv-sourced import behaves
    # identically to a pre-B7 (unstamped) segment.
    assert _source_fuel_noise_multiplier("monitoring_csv") == pytest.approx(1.0)
    _ = MonitoringCsvAdapter  # imported for parity with other adapter test files
