import math

import pytest

from core.units import (
    LatLon,
    bearing_deg,
    components_from_direction,
    direction_from_components,
    distance_m,
    kn_to_ms,
    m_to_nm,
    ms_to_kn,
    nm_to_m,
    normalize_bearing_deg,
    normalize_lon_deg,
    resolve_course_to_steer_deg,
    resolve_ground_speed_ms,
)


def test_kn_ms_round_trip():
    for v in [0.0, 1.0, 12.3, 17.0, 30.0]:
        assert ms_to_kn(kn_to_ms(v)) == pytest.approx(v)


def test_nm_m_round_trip():
    for d in [0.0, 1.0, 5.5, 200.0]:
        assert m_to_nm(nm_to_m(d)) == pytest.approx(d)


@pytest.mark.parametrize(
    "raw,expected",
    [(190, -170), (-190, 170), (0, 0), (350, -10), (-350, 10), (180, -180)],
)
def test_normalize_lon_deg(raw, expected):
    assert normalize_lon_deg(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw,expected", [(370, 10), (-10, 350), (0, 0), (360, 0)])
def test_normalize_bearing_deg(raw, expected):
    assert normalize_bearing_deg(raw) == pytest.approx(expected)


@pytest.mark.parametrize("from_deg", [0, 45, 90, 135, 180, 225, 270, 315, 359])
def test_direction_component_round_trip(from_deg):
    speed = 12.5
    u, v = components_from_direction(speed, from_deg)
    rt_speed, rt_from_deg = direction_from_components(u, v)
    assert rt_speed == pytest.approx(speed)
    diff = (rt_from_deg - from_deg + 180) % 360 - 180
    assert diff == pytest.approx(0, abs=1e-6)


def test_distance_and_bearing_north_south():
    ref_lat = 42.0
    a = LatLon(42.0, 8.0)
    b = LatLon(43.0, 8.0)
    assert distance_m(a, b, ref_lat) == pytest.approx(nm_to_m(60.0), rel=1e-6)
    assert bearing_deg(a, b, ref_lat) == pytest.approx(0.0, abs=1e-6)
    assert bearing_deg(b, a, ref_lat) == pytest.approx(180.0, abs=1e-6)


def test_resolve_ground_speed_zero_current_equals_stw():
    assert resolve_ground_speed_ms(8.0, 90.0, 0.0, 0.0) == pytest.approx(8.0)


def test_resolve_ground_speed_along_track_current_adds():
    # track due north (bearing 0), current due north too -> pure add
    got = resolve_ground_speed_ms(8.0, 0.0, current_u_ms=0.0, current_v_ms=1.5)
    assert got == pytest.approx(9.5)


def test_resolve_ground_speed_against_track_current_subtracts():
    got = resolve_ground_speed_ms(8.0, 0.0, current_u_ms=0.0, current_v_ms=-1.5)
    assert got == pytest.approx(6.5)


def test_resolve_ground_speed_cross_current_reduces_speed_over_ground():
    # track due north, current due east (pure cross-track) -> crabbing costs SOG
    got = resolve_ground_speed_ms(8.0, 0.0, current_u_ms=1.5, current_v_ms=0.0)
    assert got < 8.0
    assert got == pytest.approx(math.sqrt(8.0**2 - 1.5**2))


def test_resolve_ground_speed_current_exceeds_stw_raises():
    with pytest.raises(ValueError):
        resolve_ground_speed_ms(1.0, 0.0, current_u_ms=5.0, current_v_ms=0.0)


def test_cts_equals_course_with_zero_current():
    for brg in (0.0, 45.0, 123.0, 270.0):
        assert resolve_course_to_steer_deg(8.0, brg, 0.0, 0.0) == pytest.approx(brg)


def test_cts_unchanged_by_along_track_current():
    # track due north, current due north (pure along-track) -> no crab needed
    cts = resolve_course_to_steer_deg(8.0, 0.0, current_u_ms=0.0, current_v_ms=1.5)
    assert cts == pytest.approx(0.0, abs=1e-6)


def test_cts_offset_by_cross_track_current_in_correct_direction():
    # track due north, current due east -> must steer a bit west (negative/
    # 360-side) of north to hold the track, i.e. crab into the current.
    cts = resolve_course_to_steer_deg(8.0, 0.0, current_u_ms=1.5, current_v_ms=0.0)
    diff = (cts - 0.0 + 180) % 360 - 180
    assert diff < 0
    assert abs(diff) == pytest.approx(math.degrees(math.asin(1.5 / 8.0)))


def test_cts_and_ground_speed_are_mutually_consistent():
    # the steered heading, sailed at stw, should reconstruct the same
    # ground velocity that resolve_ground_speed_ms predicts along-track.
    stw, brg, cu, cv = 8.0, 40.0, 1.2, -0.6
    cts = resolve_course_to_steer_deg(stw, brg, cu, cv)
    sog = resolve_ground_speed_ms(stw, brg, cu, cv)
    water_u, water_v = stw * math.sin(math.radians(cts)), stw * math.cos(math.radians(cts))
    ground_u, ground_v = water_u + cu, water_v + cv
    track_u, track_v = math.sin(math.radians(brg)), math.cos(math.radians(brg))
    cross_u, cross_v = math.cos(math.radians(brg)), -math.sin(math.radians(brg))
    along = ground_u * track_u + ground_v * track_v
    cross = ground_u * cross_u + ground_v * cross_v
    assert along == pytest.approx(sog, abs=1e-6)
    assert cross == pytest.approx(0.0, abs=1e-6)
