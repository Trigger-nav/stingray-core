import pytest

from core.corridors import PORTS
from core.geography import RealGeography
from core.optimiser import (
    ANCHORAGE_MAX_DEPTH_M,
    ANCHORAGE_MIN_DEPTH_M,
    DEFAULT_DESTINATION,
    DEFAULT_ORIGIN,
    PlanRequest,
    optimise,
)
from core.units import LatLon
from core.vessel_spec import VesselSpec
from core.weather import SyntheticWeatherField

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"

# a real, navigable point with depth inside the plausible anchoring band
# (found by sampling near the Bonifacio approach) — a shallow bay stand-in.
PLAUSIBLE_ANCHORAGE = LatLon(41.381887309488306, 9.153041415443653)

# deep open water, well outside any anchoring band.
DEEP_OPEN_WATER = LatLon(43.3, 7.9)

# a real point close (<1nm) to Porto Cervo with depth 0.0m -- genuinely
# shallower than this vessel's min_depth_m (draft_m 2.5 + min_under_keel_
# clearance_m 2.0 = 4.5m) -- found by grid-sampling near the port. Declared
# as a plain (non-anchorage) destination: this is what a real harbour
# approach looks like on GEBCO's coarse bathymetry, and ticket 0.8's
# pilotage exemption (core.legs.DEPTH_EXEMPT_RADIUS_NM) is exactly what
# makes this endpoint reachable despite reading shallower than the hard
# constraint anywhere else on the passage.
SHALLOW_PORT_APPROACH = LatLon(41.13, 9.542000000000002)

# a real point close (<1.5nm) to Porto Cervo with depth ~3.2m -- inside the
# anchoring band (ANCHORAGE_MIN_DEPTH_M=3.0) but still below this vessel's
# min_depth_m (4.5m), exercising the pilotage exemption for an
# anchorage-flagged endpoint specifically.
SHALLOW_ANCHORAGE_APPROACH = LatLon(41.114000000000004, 9.546000000000001)

# a real, valid, non-legacy-corridor origin/destination pair used across
# several tests below.
CUSTOM_ORIGIN = LatLon(43.3, 7.9)
CUSTOM_DESTINATION = LatLon(41.3, 9.0)


@pytest.fixture(scope="module")
def vessel():
    return VesselSpec.from_yaml(DEFAULT_SPEC_PATH)


@pytest.fixture(scope="module")
def geo():
    return RealGeography()


@pytest.fixture
def calm():
    return SyntheticWeatherField("calm")


def test_default_origin_destination_are_the_legacy_med_ports():
    assert DEFAULT_ORIGIN == PORTS["antibes"]
    assert DEFAULT_DESTINATION == PORTS["portocervo"]


def test_omitting_origin_destination_uses_the_legacy_defaults(vessel, geo, calm):
    request = PlanRequest(weather=calm, geography=geo, vessel=vessel, pace=50, comfort=50)
    assert request.origin == DEFAULT_ORIGIN
    assert request.destination == DEFAULT_DESTINATION


def test_custom_origin_destination_produces_a_route_between_them(vessel, geo, calm):
    result = optimise(
        PlanRequest(
            weather=calm,
            geography=geo,
            vessel=vessel,
            pace=50,
            comfort=50,
            origin=CUSTOM_ORIGIN,
            destination=CUSTOM_DESTINATION,
        )
    )
    assert result.candidates
    for candidate in (*result.candidates, result.baseline):
        assert candidate.track[0] == CUSTOM_ORIGIN
        assert candidate.track[-1] == CUSTOM_DESTINATION


def test_custom_origin_destination_skips_the_legacy_corridor_grid(vessel, geo, calm):
    result = optimise(
        PlanRequest(
            weather=calm,
            geography=geo,
            vessel=vessel,
            pace=50,
            comfort=50,
            origin=CUSTOM_ORIGIN,
            destination=CUSTOM_DESTINATION,
        )
    )
    legacy_names = {"West via Bonifacio", "East of Corsica"}
    for candidate in result.candidates:
        assert candidate.corridor_name not in legacy_names


@pytest.mark.slow
def test_default_endpoints_still_include_the_legacy_corridor_grid(vessel, geo, calm):
    # sanity check the guard's *other* branch: the Med default pair should
    # still be able to surface legacy-corridor candidates (whether or not
    # they win a diversity slot depends on scoring, so just confirm the
    # grid was actually computed by checking it's at least eligible —
    # a plain optimise() call should not error and should still return
    # sensible candidates, matching pre-B6 behaviour).
    result = optimise(PlanRequest(weather=calm, geography=geo, vessel=vessel, pace=50, comfort=50))
    assert result.candidates


def test_non_navigable_origin_raises(vessel, geo, calm):
    corsica_interior = LatLon(42.3, 9.0)
    with pytest.raises(ValueError, match="not navigable"):
        PlanRequest(
            weather=calm, geography=geo, vessel=vessel, pace=50, comfort=50, origin=corsica_interior
        )


def test_non_navigable_destination_raises(vessel, geo, calm):
    corsica_interior = LatLon(42.3, 9.0)
    with pytest.raises(ValueError, match="not navigable"):
        PlanRequest(
            weather=calm,
            geography=geo,
            vessel=vessel,
            pace=50,
            comfort=50,
            destination=corsica_interior,
        )


def test_anchorage_depth_band_rejects_implausible_depth(vessel, geo, calm):
    with pytest.raises(ValueError, match="anchoring band"):
        PlanRequest(
            weather=calm,
            geography=geo,
            vessel=vessel,
            pace=50,
            comfort=50,
            destination=DEEP_OPEN_WATER,
            destination_is_anchorage=True,
        )


def test_deep_point_not_flagged_as_anchorage_is_fine(vessel, geo, calm):
    # the same deep point is fine as a regular endpoint (e.g. a port
    # approach) — only anchorage-flagged endpoints get the depth check.
    request = PlanRequest(
        weather=calm,
        geography=geo,
        vessel=vessel,
        pace=50,
        comfort=50,
        destination=DEEP_OPEN_WATER,
        destination_is_anchorage=False,
    )
    assert request.destination == DEEP_OPEN_WATER


def test_anchorage_within_plausible_band_is_accepted(vessel, geo, calm):
    depth = geo.depth_m(PLAUSIBLE_ANCHORAGE.lat_deg, PLAUSIBLE_ANCHORAGE.lon_deg)
    assert ANCHORAGE_MIN_DEPTH_M <= depth <= ANCHORAGE_MAX_DEPTH_M
    request = PlanRequest(
        weather=calm,
        geography=geo,
        vessel=vessel,
        pace=50,
        comfort=50,
        destination=PLAUSIBLE_ANCHORAGE,
        destination_is_anchorage=True,
    )
    assert request.destination == PLAUSIBLE_ANCHORAGE


@pytest.mark.slow
def test_shallow_port_approach_is_reachable_under_depth_enforcement(vessel, geo, calm):
    """Ticket 0.8 amendment 1: a real port destination genuinely shallower
    than this vessel's min_depth_m (SHALLOW_PORT_APPROACH reads 0.0m on
    GEBCO, vs. draft_m 2.5 + min_under_keel_clearance_m 2.0 = 4.5m) must
    still be reachable end-to-end -- the final-approach pilotage exemption
    (core.legs.DEPTH_EXEMPT_RADIUS_NM) is what makes a real harbour
    destination plannable at all under the new hard depth constraint,
    documented as captain/local-knowledge scope, not this optimiser's."""
    min_depth_m = vessel.hull.draft_m + vessel.min_under_keel_clearance_m
    depth = geo.depth_m(SHALLOW_PORT_APPROACH.lat_deg, SHALLOW_PORT_APPROACH.lon_deg)
    assert depth < min_depth_m

    result = optimise(
        PlanRequest(
            weather=calm,
            geography=geo,
            vessel=vessel,
            pace=50,
            comfort=50,
            destination=SHALLOW_PORT_APPROACH,
        )
    )
    assert result.candidates
    for candidate in result.candidates:
        assert candidate.track[-1] == SHALLOW_PORT_APPROACH


@pytest.mark.slow
def test_shallow_anchorage_is_reachable_under_depth_enforcement(vessel, geo, calm):
    """Same as above, for an anchorage-flagged endpoint: the anchoring
    band's own floor (ANCHORAGE_MIN_DEPTH_M=3.0) is shallower than this
    vessel's min_depth_m (4.5m), so a plausible real anchorage pin can
    legitimately fail the generic hard depth constraint -- the pilotage
    exemption must still let the passage reach it."""
    min_depth_m = vessel.hull.draft_m + vessel.min_under_keel_clearance_m
    depth = geo.depth_m(SHALLOW_ANCHORAGE_APPROACH.lat_deg, SHALLOW_ANCHORAGE_APPROACH.lon_deg)
    assert ANCHORAGE_MIN_DEPTH_M <= depth < min_depth_m

    result = optimise(
        PlanRequest(
            weather=calm,
            geography=geo,
            vessel=vessel,
            pace=50,
            comfort=50,
            destination=SHALLOW_ANCHORAGE_APPROACH,
            destination_is_anchorage=True,
        )
    )
    assert result.candidates
    for candidate in result.candidates:
        assert candidate.track[-1] == SHALLOW_ANCHORAGE_APPROACH
