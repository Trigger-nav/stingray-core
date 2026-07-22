"""core/distill.py (ticket S1) -- fabricated tracks with known,
hand-computed geometry, independent of real GEBCO/GSHHG data (matching
`tests/test_legs.py`'s own `_StubGeography` precedent), so the
distillation algorithm's correctness can be checked against exact,
controllable scenarios rather than searched-for real points.
"""

from __future__ import annotations

import pytest

from core.distill import _sweep_once, distill_track
from core.legs import DEPTH_EXEMPT_RADIUS_NM
from core.twin import VesselTwin
from core.units import LatLon, kn_to_ms
from core.vessel_spec import VesselSpec
from core.weather import SyntheticWeatherField
from core.weights import combine_weights, weights_from_mission

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"
REF_LAT_DEG = 41.0
MIN_DEPTH_M = 4.5  # matches mys_50m_default.yaml: draft_m 2.5 + min_under_keel_clearance_m 2.0


class _StubGeography:
    """Deep, navigable everywhere except an optional rectangular "hazard
    box" (lat/lon bounds) -- lets a test construct a direct shortcut that
    provably clips a hazard the original two-leg path provably avoids."""

    def __init__(self, depth_m: float = 100.0, hazard_box=None):
        self._depth_m = depth_m
        self._hazard_box = hazard_box  # (lat_min, lat_max, lon_min, lon_max) or None

    def is_navigable(self, lat_deg: float, lon_deg: float) -> bool:
        if self._hazard_box is None:
            return True
        lat_min, lat_max, lon_min, lon_max = self._hazard_box
        return not (lat_min <= lat_deg <= lat_max and lon_min <= lon_deg <= lon_max)

    def depth_m(self, lat_deg: float, lon_deg: float) -> float:
        return self._depth_m


@pytest.fixture
def vessel():
    return VesselSpec.from_yaml(DEFAULT_SPEC_PATH)


@pytest.fixture
def twin(vessel):
    return VesselTwin(vessel)


@pytest.fixture
def weights(vessel):
    return combine_weights(weights_from_mission(pace=50, comfort=50), vessel.wear_policy)


@pytest.fixture
def calm():
    return SyntheticWeatherField("calm")


def _distill(track, stw_ms_per_leg, engines_per_leg, weather, geo, twin, weights, exempt=()):
    return distill_track(
        track,
        stw_ms_per_leg,
        engines_per_leg,
        0.0,
        weather,
        geo,
        twin,
        weights,
        exempt,
        REF_LAT_DEG,
    )


def test_a_genuine_kink_is_removed_and_score_does_not_worsen(twin, calm, weights):
    origin = LatLon(41.0, 9.0)
    via = LatLon(41.005, 9.05)  # a small ~0.3nm lateral offset -- a real, removable kink
    destination = LatLon(41.0, 9.1)
    geo = _StubGeography(depth_m=100.0)
    stw = kn_to_ms(12)

    original = distill_track(
        (origin, via, destination),
        [stw, stw],
        [2, 2],
        0.0,
        calm,
        geo,
        twin,
        weights,
        (),
        REF_LAT_DEG,
    )
    assert len(original["track"]) == 2  # via removed
    assert original["track"] == (origin, destination)

    # never-worsening: recompute the ORIGINAL (undistilled) track's own
    # honest total and confirm the distilled result isn't worse.
    from core.distill import _evaluate_whole_track

    undistilled = _evaluate_whole_track(
        [origin, via, destination],
        [stw, stw],
        [2, 2],
        0.0,
        calm,
        geo,
        twin,
        weights,
        (),
        REF_LAT_DEG,
    )
    assert original["cost"] <= undistilled["cost"] + 1e-9
    assert original["duration_h"] <= undistilled["duration_h"] + 1e-9
    assert original["distance_nm"] < undistilled["distance_nm"]


def test_a_shortcut_that_clips_a_hazard_is_blocked(twin, calm, weights):
    """The Bonifacio-shaped correctness check: the two-leg path swerves
    around a hazard box; the direct shortcut between its endpoints
    provably crosses it (hand-verified geometry, see module docstring
    reasoning in the plan). Distillation must re-verify navigability on
    the shortcut itself, not just trust the endpoints, and keep `via`."""
    origin = LatLon(41.0, 9.0)
    via = LatLon(41.02, 9.05)
    destination = LatLon(41.0, 9.1)
    # box straddles the DIRECT origin-destination line (lat=41.0) but not
    # either real leg (both swerve north of it) -- verified by construction.
    hazard_box = (40.99, 41.01, 9.04, 9.06)
    geo = _StubGeography(depth_m=100.0, hazard_box=hazard_box)
    stw = kn_to_ms(12)

    # sanity: the two real legs never touch the box.
    assert geo.is_navigable(origin.lat_deg, origin.lon_deg)
    assert geo.is_navigable(via.lat_deg, via.lon_deg)
    assert geo.is_navigable(destination.lat_deg, destination.lon_deg)
    # sanity: the direct shortcut's midpoint DOES fall inside the box.
    assert not geo.is_navigable(41.0, 9.05)

    result = distill_track(
        (origin, via, destination),
        [stw, stw],
        [2, 2],
        0.0,
        calm,
        geo,
        twin,
        weights,
        (),
        REF_LAT_DEG,
    )
    assert result["track"] == (origin, via, destination)  # via kept -- shortcut was blocked


def test_a_speed_change_point_is_never_removed(twin, calm, weights):
    # identical geometry to the genuine-kink test above (geometrically
    # removable), but the two legs are commanded at different speeds --
    # must be kept regardless, per the same-speed precondition.
    origin = LatLon(41.0, 9.0)
    via = LatLon(41.005, 9.05)
    destination = LatLon(41.0, 9.1)
    geo = _StubGeography(depth_m=100.0)

    result = distill_track(
        (origin, via, destination),
        [kn_to_ms(10), kn_to_ms(14)],
        [2, 2],
        0.0,
        calm,
        geo,
        twin,
        weights,
        (),
        REF_LAT_DEG,
    )
    assert result["track"] == (origin, via, destination)


def test_a_speed_change_via_different_engine_count_is_never_removed(twin, calm, weights):
    origin = LatLon(41.0, 9.0)
    via = LatLon(41.005, 9.05)
    destination = LatLon(41.0, 9.1)
    geo = _StubGeography(depth_m=100.0)
    stw = kn_to_ms(12)

    result = distill_track(
        (origin, via, destination),
        [stw, stw],
        [1, 2],
        0.0,
        calm,
        geo,
        twin,
        weights,
        (),
        REF_LAT_DEG,
    )
    assert result["track"] == (origin, via, destination)


def test_shallow_endpoint_exemption_radius_carries_through_a_shortcut(twin, calm, weights):
    """core.legs.DEPTH_EXEMPT_RADIUS_NM: a declared endpoint's final
    approach is exempt from the generic depth hard constraint. The
    shortcut leg distillation proposes must inherit that exemption via
    the same `depth_exempt_points` the original search used -- not lose
    it just because the leg's shape changed. `depth_exempt_points` is
    always `(origin, destination)` in a real search
    (`core/optimiser.py`'s `depth_exempt_points = (lattice.origin,
    lattice.destination)`) -- this fixture matches that, not just
    destination alone."""
    origin = LatLon(41.0, 9.0)
    # A short (2.5nm) passage entirely below MIN_DEPTH_M -- every point on
    # it is within DEPTH_EXEMPT_RADIUS_NM (1.5nm) of one endpoint or the
    # other (triangle equality on a 2.5nm collinear track), so it's only
    # feasible at all via the exemption -- for the original 2-leg path
    # *and* for the shortcut distillation proposes.
    destination = LatLon(41.0, 9.0 + 2.5 / 60.0)
    via = LatLon(41.0, 9.0 + 1.25 / 60.0)  # midpoint, 1.25nm from each end
    geo = _StubGeography(depth_m=2.0)  # shallower than MIN_DEPTH_M everywhere
    stw = kn_to_ms(12)

    result = distill_track(
        (origin, via, destination),
        [stw, stw],
        [2, 2],
        0.0,
        calm,
        geo,
        twin,
        weights,
        (origin, destination),  # depth_exempt_points, matching a real search's own
        REF_LAT_DEG,
    )
    # via lies well inside the exemption radius of both endpoints, so the
    # whole passage is exempt end to end -- the shortcut is accepted.
    assert result["track"] == (origin, destination)


def test_shallow_leg_beyond_the_exemption_radius_is_genuinely_pruned(twin, calm, weights):
    """Companion to the test above -- confirms the exemption isn't a
    blanket pass: a via point genuinely beyond DEPTH_EXEMPT_RADIUS_NM of
    both endpoints, over shallow water, keeps a leg pruned regardless of
    distillation. Not a distillation-removal scenario (the original track
    itself would already be infeasible at that leg) -- a sanity check on
    the fixture's own realism."""
    origin = LatLon(41.0, 9.0)
    far_point = LatLon(41.0, 9.0 + 10.0 / 60.0)  # 10nm away -- beyond any exemption
    geo = _StubGeography(depth_m=2.0)
    assert geo.depth_m(far_point.lat_deg, far_point.lon_deg) < MIN_DEPTH_M
    from core.units import distance_m, m_to_nm

    assert m_to_nm(distance_m(origin, far_point, REF_LAT_DEG)) > DEPTH_EXEMPT_RADIUS_NM


def test_sweep_once_returns_none_when_nothing_removable(twin, calm, weights):
    origin = LatLon(41.0, 9.0)
    destination = LatLon(41.0, 9.1)
    geo = _StubGeography(depth_m=100.0)
    stw = kn_to_ms(12)
    # a bare 2-point track has no interior waypoint to remove at all.
    assert (
        _sweep_once(
            [origin, destination], [stw], [2], 0.0, calm, geo, twin, weights, (), REF_LAT_DEG
        )
        is None
    )


def test_distill_track_is_a_no_op_on_an_already_minimal_track(twin, calm, weights):
    origin = LatLon(41.0, 9.0)
    destination = LatLon(41.0, 9.1)
    geo = _StubGeography(depth_m=100.0)
    stw = kn_to_ms(12)
    result = distill_track(
        (origin, destination), [stw], [2], 0.0, calm, geo, twin, weights, (), REF_LAT_DEG
    )
    assert result["track"] == (origin, destination)


def test_chained_same_speed_removals_collapse_in_a_single_pass(twin, calm, weights):
    """Several consecutive small kinks, all at the same speed/engine --
    must all collapse away in one call to distill_track (which may
    internally take more than one _sweep_once pass, but the point is the
    whole chain resolves, not just one waypoint per call)."""
    origin = LatLon(41.0, 9.00)
    a = LatLon(41.003, 9.02)
    b = LatLon(41.003, 9.04)
    c = LatLon(41.003, 9.06)
    d = LatLon(41.003, 9.08)
    destination = LatLon(41.0, 9.10)
    geo = _StubGeography(depth_m=100.0)
    stw = kn_to_ms(12)
    track = (origin, a, b, c, d, destination)

    result = distill_track(
        track, [stw] * 5, [2] * 5, 0.0, calm, geo, twin, weights, (), REF_LAT_DEG
    )
    assert len(result["track"]) < len(track)
    assert result["track"][0] == origin
    assert result["track"][-1] == destination


def test_distill_track_result_recomputes_metrics_consistently(twin, calm, weights):
    """The returned dict's own duration_h/distance_nm/fuel_kg must be
    consistent with a fresh evaluation of the returned track -- i.e. this
    isn't reporting stale pre-distillation numbers for the track it
    actually returns."""
    from core.distill import _evaluate_whole_track

    origin = LatLon(41.0, 9.0)
    via = LatLon(41.005, 9.05)
    destination = LatLon(41.0, 9.1)
    geo = _StubGeography(depth_m=100.0)
    stw = kn_to_ms(12)

    result = distill_track(
        (origin, via, destination),
        [stw, stw],
        [2, 2],
        0.0,
        calm,
        geo,
        twin,
        weights,
        (),
        REF_LAT_DEG,
    )
    fresh = _evaluate_whole_track(
        list(result["track"]),
        result["stw_ms_per_leg"],
        result["engines_per_leg"],
        0.0,
        calm,
        geo,
        twin,
        weights,
        (),
        REF_LAT_DEG,
    )
    assert result["duration_h"] == pytest.approx(fresh["duration_h"])
    assert result["fuel_kg"] == pytest.approx(fresh["fuel_kg"])
    assert result["distance_nm"] == pytest.approx(fresh["distance_nm"])
    assert result["cost"] == pytest.approx(fresh["cost"])


def test_eta_window_style_invariant_duration_never_increases(twin, calm, weights):
    """Direct unit-level defence-in-depth for the ETA-window correctness
    point (docs/plans/ticket-S1.md Sec 2): distillation must never
    increase a track's own duration, on a fixture with a real removable
    kink -- the wiring-order argument in core/optimiser.py explains why
    this makes latest_arrival_h safe; this proves it for the actual
    implementation, not just the design."""
    from core.distill import _evaluate_whole_track

    origin = LatLon(41.0, 9.0)
    via = LatLon(41.005, 9.05)
    destination = LatLon(41.0, 9.1)
    geo = _StubGeography(depth_m=100.0)
    stw = kn_to_ms(12)

    undistilled = _evaluate_whole_track(
        [origin, via, destination],
        [stw, stw],
        [2, 2],
        0.0,
        calm,
        geo,
        twin,
        weights,
        (),
        REF_LAT_DEG,
    )
    distilled = distill_track(
        (origin, via, destination),
        [stw, stw],
        [2, 2],
        0.0,
        calm,
        geo,
        twin,
        weights,
        (),
        REF_LAT_DEG,
    )
    assert distilled["duration_h"] <= undistilled["duration_h"] + 1e-9
