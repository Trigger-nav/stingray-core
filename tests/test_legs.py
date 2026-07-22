import dataclasses

import pytest

from core.legs import DEPTH_EXEMPT_RADIUS_NM, _leg_depth_ok, evaluate_leg, leg_navigation
from core.twin import VesselTwin
from core.units import LatLon, kn_to_ms
from core.vessel_spec import VesselSpec
from core.weather import SyntheticWeatherField

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"

MIN_DEPTH_M = 4.5  # matches mys_50m_default.yaml: draft_m 2.5 + min_under_keel_clearance_m 2.0


class _StubGeography:
    """Minimal, controllable `Geography`-shaped stub — everywhere is
    navigable water at a fixed depth unless a coordinate is explicitly
    listed as land. Deliberately independent of any real GEBCO/GSHHG data
    so the depth hard-constraint/pilotage-exemption logic can be tested
    against exact, known depths rather than searched-for real points."""

    def __init__(self, depth_m: float, land_points: frozenset[tuple[float, float]] = frozenset()):
        self._depth_m = depth_m
        self._land_points = land_points

    def is_land(self, lat_deg: float, lon_deg: float) -> bool:
        return (lat_deg, lon_deg) in self._land_points

    def is_nogo(self, lat_deg: float, lon_deg: float) -> bool:
        return False

    def is_navigable(self, lat_deg: float, lon_deg: float) -> bool:
        return not (self.is_land(lat_deg, lon_deg) or self.is_nogo(lat_deg, lon_deg))

    def depth_m(self, lat_deg: float, lon_deg: float) -> float:
        return self._depth_m


@pytest.fixture
def vessel():
    return VesselSpec.from_yaml(DEFAULT_SPEC_PATH)


@pytest.fixture
def twin(vessel):
    return VesselTwin(vessel)


@pytest.fixture
def calm():
    return SyntheticWeatherField("calm")


class _ConstantCurrentWeatherField:
    """Wraps `SyntheticWeatherField("calm")`, overriding only
    `current_u_ms`/`current_v_ms` to a fixed, known test value -- ticket
    C1: lets these tests exercise the current-triangle math with a real,
    controllable current without a full `GriddedWeatherField` npz
    round-trip."""

    def __init__(self, current_u_ms: float, current_v_ms: float):
        self._inner = SyntheticWeatherField("calm")
        self._current_u_ms = current_u_ms
        self._current_v_ms = current_v_ms

    def sample(self, lat_deg, lon_deg, t_h):
        s = self._inner.sample(lat_deg, lon_deg, t_h)
        return dataclasses.replace(
            s, current_u_ms=self._current_u_ms, current_v_ms=self._current_v_ms
        )


def test_deep_leg_is_depth_ok():
    p, q = LatLon(41.0, 9.0), LatLon(41.0, 9.1)
    geo = _StubGeography(depth_m=100.0)
    assert _leg_depth_ok(p, q, geo, MIN_DEPTH_M, ()) is True


def test_shallow_leg_without_exemption_is_pruned():
    p, q = LatLon(41.0, 9.0), LatLon(41.0, 9.1)
    geo = _StubGeography(depth_m=2.0)
    assert _leg_depth_ok(p, q, geo, MIN_DEPTH_M, ()) is False


def test_shallow_leg_entirely_within_exemption_radius_is_ok():
    # a short leg (well under DEPTH_EXEMPT_RADIUS_NM) right next to the
    # exempt point -- every sample along it falls inside the radius.
    origin = LatLon(41.13, 9.55)
    p, q = origin, LatLon(41.135, 9.555)
    geo = _StubGeography(depth_m=2.0)
    assert _leg_depth_ok(p, q, geo, MIN_DEPTH_M, (origin,)) is True


def test_shallow_leg_far_from_any_exempt_point_is_still_pruned():
    # exempt_points is non-empty, but nowhere near this leg -- the
    # exemption must not leak beyond its radius.
    origin = LatLon(43.55, 7.17)  # Antibes, nowhere near the leg below
    p, q = LatLon(41.0, 9.0), LatLon(41.0, 9.1)
    geo = _StubGeography(depth_m=2.0)
    assert _leg_depth_ok(p, q, geo, MIN_DEPTH_M, (origin,)) is False


def test_shallow_leg_only_partially_within_exemption_radius_is_pruned():
    # a leg long enough that its far end sits outside DEPTH_EXEMPT_RADIUS_NM
    # from the exempt point -- since every sample must be either exempt or
    # deep enough, one out-of-radius shallow sample fails the whole leg.
    origin = LatLon(41.0, 9.0)
    far_end = LatLon(41.0, 9.0 + (2 * DEPTH_EXEMPT_RADIUS_NM) / 60.0)  # ~2x the radius away
    geo = _StubGeography(depth_m=2.0)
    assert _leg_depth_ok(origin, far_end, geo, MIN_DEPTH_M, (origin,)) is False


def test_depth_exemption_does_not_affect_land_no_go_navigability():
    # the pilotage exemption is depth-only -- a land point inside the
    # exemption radius must still read as not navigable.
    origin = LatLon(41.13, 9.55)
    land_point = LatLon(41.131, 9.551)
    geo = _StubGeography(depth_m=100.0, land_points=frozenset({(41.131, 9.551)}))
    assert geo.is_navigable(land_point.lat_deg, land_point.lon_deg) is False
    # and depth_ok is independent -- deep water there still reads depth_ok
    assert _leg_depth_ok(origin, land_point, geo, MIN_DEPTH_M, (origin,)) is True


def test_evaluate_leg_wires_depth_ok_through(twin, calm):
    p, q = LatLon(41.0, 9.0), LatLon(41.0, 9.1)
    stw_ms = kn_to_ms(12)

    deep = _StubGeography(depth_m=100.0)
    result_deep = evaluate_leg(p, q, stw_ms, 0.0, calm, deep, twin, active_engines=2)
    assert result_deep.navigable is True
    assert result_deep.depth_ok is True

    shallow = _StubGeography(depth_m=2.0)
    result_shallow = evaluate_leg(p, q, stw_ms, 0.0, calm, shallow, twin, active_engines=2)
    assert result_shallow.navigable is True  # land/no-go unaffected
    assert result_shallow.depth_ok is False


def test_evaluate_leg_depth_exempt_points_reach_shallow_endpoint(twin, calm):
    origin = LatLon(41.13, 9.55)
    approach = LatLon(41.132, 9.552)  # short leg into the declared endpoint
    stw_ms = kn_to_ms(12)
    shallow = _StubGeography(depth_m=2.0)

    unexempt = evaluate_leg(origin, approach, stw_ms, 0.0, calm, shallow, twin, active_engines=2)
    assert unexempt.depth_ok is False

    exempt = evaluate_leg(
        origin,
        approach,
        stw_ms,
        0.0,
        calm,
        shallow,
        twin,
        active_engines=2,
        depth_exempt_points=(approach,),
    )
    assert exempt.depth_ok is True


# --- Ticket C1: current-exceeds-STW hard constraint --------------------


def test_leg_navigation_current_exceeding_stw_is_flagged_not_raised():
    # p -> q runs due east (~90 deg bearing); a due-north current is pure
    # cross-track for this leg. 10kn cross-current vs. a 6kn STW candidate
    # -- before ticket C1 this raised ValueError uncaught out of
    # leg_navigation; now it must be caught and flagged instead.
    p, q = LatLon(41.0, 9.0), LatLon(41.0, 9.1)
    strong_cross_current = _ConstantCurrentWeatherField(
        current_u_ms=0.0, current_v_ms=kn_to_ms(10)
    )
    nav = leg_navigation(p, q, kn_to_ms(6), 0.0, strong_cross_current)
    assert nav.current_exceeds_stw is True
    assert nav.duration_h == float("inf")


def test_leg_navigation_weak_cross_current_is_not_flagged():
    # a cross-current well within STW must resolve normally, not flag.
    p, q = LatLon(41.0, 9.0), LatLon(41.0, 9.1)
    weak_cross_current = _ConstantCurrentWeatherField(current_u_ms=0.0, current_v_ms=kn_to_ms(2))
    nav = leg_navigation(p, q, kn_to_ms(12), 0.0, weak_cross_current)
    assert nav.current_exceeds_stw is False
    assert nav.duration_h < float("inf")


def test_leg_navigation_strong_following_current_speeds_up_without_flagging():
    # a strong ALONG-track (following) current is physically fine
    # regardless of magnitude relative to STW -- only the cross-track
    # component can exceed STW. Must not be flagged, and must measurably
    # reduce duration_h vs. a zero-current baseline.
    p, q = LatLon(41.0, 9.0), LatLon(41.0, 9.1)  # due east
    stw_ms = kn_to_ms(10)
    following_current = _ConstantCurrentWeatherField(current_u_ms=kn_to_ms(10), current_v_ms=0.0)
    zero_current = SyntheticWeatherField("calm")

    nav_following = leg_navigation(p, q, stw_ms, 0.0, following_current)
    nav_zero = leg_navigation(p, q, stw_ms, 0.0, zero_current)

    assert nav_following.current_exceeds_stw is False
    assert nav_following.duration_h < nav_zero.duration_h


def test_evaluate_leg_current_exceeds_stw_prunes_leg_without_crashing(twin):
    p, q = LatLon(41.0, 9.0), LatLon(41.0, 9.1)
    deep = _StubGeography(depth_m=100.0)
    strong_cross_current = _ConstantCurrentWeatherField(
        current_u_ms=0.0, current_v_ms=kn_to_ms(10)
    )
    result = evaluate_leg(
        p, q, kn_to_ms(6), 0.0, strong_cross_current, deep, twin, active_engines=2
    )
    assert result.current_exceeds_stw is True
    # every other hard-constraint flag is independent -- this isn't a
    # land/depth/wear-policy prune, just current-exceeds-STW.
    assert result.navigable is True
    assert result.depth_ok is True
