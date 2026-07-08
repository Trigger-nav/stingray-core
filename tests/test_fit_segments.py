import pytest

from fit.segments import DEFAULT_MAX_GAP_S, DEFAULT_MIN_DURATION_S, extract_steady_state_segments
from fit.telemetry import TelemetrySample

SAMPLE_INTERVAL_H = 1.0 / 60.0


def _steady_block(
    t0_h, n, *, stw_ms=5.0, heading_deg=180.0, active_engines=2, fuel_kg_per_h=100.0, hs_m=0.5
):
    return [
        TelemetrySample(
            t_h=t0_h + i * SAMPLE_INTERVAL_H,
            stw_ms=stw_ms,
            heading_deg=heading_deg,
            active_engines=active_engines,
            fuel_kg_per_h=fuel_kg_per_h,
            hs_m=hs_m,
            period_peak_s=7.0,
            wave_from_deg=0.0,
        )
        for i in range(n)
    ]


def test_single_long_steady_block_becomes_one_segment():
    samples = _steady_block(0.0, 10)
    segments = extract_steady_state_segments(samples, min_duration_s=5 * 60)
    assert len(segments) == 1
    assert segments[0].n_samples == 10
    assert segments[0].mean_stw_ms == pytest.approx(5.0)
    assert segments[0].active_engines == 2


def test_short_block_below_min_duration_is_dropped():
    samples = _steady_block(0.0, 2)  # 2 samples = 1 minute, well under 5 min default
    segments = extract_steady_state_segments(samples, min_duration_s=DEFAULT_MIN_DURATION_S)
    assert segments == []


def test_speed_jump_splits_into_two_segments():
    block_a = _steady_block(0.0, 10, stw_ms=5.0)
    block_b = _steady_block(10 * SAMPLE_INTERVAL_H, 10, stw_ms=9.0)
    segments = extract_steady_state_segments(block_a + block_b, min_duration_s=5 * 60)
    assert len(segments) == 2
    assert segments[0].mean_stw_ms == pytest.approx(5.0)
    assert segments[1].mean_stw_ms == pytest.approx(9.0)


def test_heading_jump_splits_into_two_segments():
    block_a = _steady_block(0.0, 10, heading_deg=180.0)
    block_b = _steady_block(10 * SAMPLE_INTERVAL_H, 10, heading_deg=90.0)
    segments = extract_steady_state_segments(block_a + block_b, min_duration_s=5 * 60)
    assert len(segments) == 2


def test_engine_config_change_splits_into_two_segments():
    block_a = _steady_block(0.0, 10, active_engines=2)
    block_b = _steady_block(10 * SAMPLE_INTERVAL_H, 10, active_engines=1)
    segments = extract_steady_state_segments(block_a + block_b, min_duration_s=5 * 60)
    assert len(segments) == 2
    assert {s.active_engines for s in segments} == {1, 2}


def test_fuel_jump_uncorrelated_with_speed_splits_segments():
    """The tank-transfer-artefact shape: one sample's fuel_kg_per_h spikes
    while speed/heading/hs stay identical. Should still break the run."""
    samples = _steady_block(0.0, 15, fuel_kg_per_h=100.0)
    corrupted = list(samples)
    mid = len(corrupted) // 2
    corrupted[mid] = TelemetrySample(
        t_h=corrupted[mid].t_h,
        stw_ms=corrupted[mid].stw_ms,
        heading_deg=corrupted[mid].heading_deg,
        active_engines=corrupted[mid].active_engines,
        fuel_kg_per_h=250.0,  # spike, no speed/heading/hs change
        hs_m=corrupted[mid].hs_m,
        period_peak_s=corrupted[mid].period_peak_s,
        wave_from_deg=corrupted[mid].wave_from_deg,
    )
    segments = extract_steady_state_segments(corrupted, min_duration_s=3 * 60)
    # the corrupted sample itself is excluded from every segment
    for seg in segments:
        assert not (seg.t_start_h <= corrupted[mid].t_h <= seg.t_end_h)
    # clean data before and after the spike both survive as real segments
    assert len(segments) == 2
    for seg in segments:
        assert seg.mean_fuel_kg_per_h == pytest.approx(100.0)


def test_time_gap_beyond_max_gap_splits_segments():
    block_a = _steady_block(0.0, 10)
    gap_h = (DEFAULT_MAX_GAP_S * 2) / 3600.0
    block_b = _steady_block(10 * SAMPLE_INTERVAL_H + gap_h, 10)
    segments = extract_steady_state_segments(block_a + block_b, min_duration_s=5 * 60)
    assert len(segments) == 2


def test_empty_input_returns_empty_list():
    assert extract_steady_state_segments([]) == []


def test_segment_time_span_matches_first_and_last_sample():
    samples = _steady_block(1.5, 10)
    segments = extract_steady_state_segments(samples, min_duration_s=5 * 60)
    assert segments[0].t_start_h == pytest.approx(samples[0].t_h)
    assert segments[0].t_end_h == pytest.approx(samples[-1].t_h)
