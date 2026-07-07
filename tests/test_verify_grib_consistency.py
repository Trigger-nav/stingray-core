import pytest

from core.weather import WeatherSample
from ingest.verify_grib_consistency import (
    circular_direction_diff_deg,
    compare_samples,
    summarise,
)


def _sample(hs_m=1.0, wave_from_deg=90.0, wind_u_ms=1.0, wind_v_ms=0.0):
    return WeatherSample(
        hs_m=hs_m,
        period_peak_s=6.0,
        period_mean_s=5.0,
        wave_from_deg=wave_from_deg,
        wind_u_ms=wind_u_ms,
        wind_v_ms=wind_v_ms,
        current_u_ms=0.0,
        current_v_ms=0.0,
    )


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (0.0, 0.0, 0.0),
        (0.0, 180.0, 180.0),
        (350.0, 10.0, 20.0),  # wraps across 0/360
        (10.0, 350.0, 20.0),
        (90.0, 270.0, 180.0),
        (45.0, 90.0, 45.0),
    ],
)
def test_circular_direction_diff_deg(a, b, expected):
    assert circular_direction_diff_deg(a, b) == pytest.approx(expected)


def test_compare_samples_returns_none_when_either_missing():
    missing = WeatherSample(
        hs_m=float("nan"),
        period_peak_s=float("nan"),
        period_mean_s=float("nan"),
        wave_from_deg=float("nan"),
        wind_u_ms=0.0,
        wind_v_ms=0.0,
        current_u_ms=0.0,
        current_v_ms=0.0,
    )
    assert compare_samples(missing, _sample()) is None
    assert compare_samples(_sample(), missing) is None


def test_compare_samples_computes_expected_diffs():
    a = _sample(hs_m=1.0, wave_from_deg=10.0, wind_u_ms=3.0, wind_v_ms=4.0)  # speed 5
    b = _sample(hs_m=1.5, wave_from_deg=30.0, wind_u_ms=0.0, wind_v_ms=0.0)  # speed 0
    result = compare_samples(a, b)
    assert result["hs_diff_m"] == pytest.approx(0.5)
    assert result["wind_speed_diff_ms"] == pytest.approx(5.0)
    assert result["wave_dir_diff_deg"] == pytest.approx(20.0)


def test_summarise_empty_is_explicit_not_silent():
    summary = summarise([])
    assert summary["n"] == 0
    assert "no comparable" in summary["verdict"]


def test_summarise_flags_agreement_near_zero():
    comparisons = [
        {"hs_diff_m": 0.1, "wind_speed_diff_ms": 0.5, "wave_dir_diff_deg": d}
        for d in (5.0, 10.0, 15.0)
    ]
    summary = summarise(comparisons)
    assert "consistent" in summary["verdict"]


def test_summarise_flags_suspected_direction_convention_flip_near_180():
    comparisons = [
        {"hs_diff_m": 0.1, "wind_speed_diff_ms": 0.5, "wave_dir_diff_deg": d}
        for d in (170.0, 175.0, 178.0)
    ]
    summary = summarise(comparisons)
    assert "WW3_DIRECTION_IS_TO_CONVENTION" in summary["verdict"]
    assert "probably wrong" in summary["verdict"]


def test_summarise_inconclusive_band_neither_flags():
    comparisons = [{"hs_diff_m": 0.1, "wind_speed_diff_ms": 0.5, "wave_dir_diff_deg": 90.0}]
    summary = summarise(comparisons)
    assert "inconclusive" in summary["verdict"]
