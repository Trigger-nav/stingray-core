"""Single-leg evaluation: course/CTS/duration (A4) and full leg costing
(fuel/comfort/wear + hard-constraint checks, A5/B5). Shared by
`core/optimiser.py` (the production search) and `core/isochrone.py` (the
pre-pruning pass and time-optimal cross-check oracle, ticket 0.4) — both
need identical hard-constraint semantics, so the logic lives in one place
rather than being duplicated (and risking drift) across two modules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from core.corridors import REF_LAT_DEG
from core.geography import Geography
from core.twin import VesselTwin
from core.units import (
    LatLon,
    bearing_deg,
    distance_m,
    interpolate_point,
    m_to_nm,
    ms_to_kn,
    resolve_course_to_steer_deg,
    resolve_ground_speed_ms,
)
from core.weather import WeatherField, WeatherSample

# Fixed-distance navigability sampling, not a fixed *count* of fractional
# samples: a small fixed number of fractions (e.g. 6) spaces samples many
# nm apart on a long/diagonal leg, wide enough to step clean over a narrow
# GSHHG headland or islet between two samples (found via review: a lattice
# leg clipped land at a point that fell in the gap between the 0.45 and 0.6
# fractional samples). Distance-based sampling is only practical because
# `RealGeography.is_land` is a rasterised O(1) lookup (ticket 0.4), not the
# O(polygons) ray-cast it would have been before that.
#
# 0.25nm, not the initially-suggested ~0.5nm: verifying at 0.5nm still let
# a ~0.49nm-wide islet slip between two samples on a real lattice route
# (found while fixing this) — production sampling needs to be at least as
# fine as the regression test's verification resolution (0.25nm, see
# tests/test_optimiser_regression.py's fine-sampling check), or the two
# are just racing at different resolutions.
NAVIGABILITY_SAMPLE_INTERVAL_NM = 0.25

# Minimum-depth pilotage exemption (ticket 0.8): the last stretch of track
# into a declared origin/destination is a captain/local-knowledge job, not
# this optimiser's -- ports and anchorages are frequently in nearshore
# water that doesn't meet a generic open-water margin (B6's
# `_validate_endpoint` already owns endpoint depth plausibility). Depth-
# only: land/no-go checks still apply within this radius.
DEPTH_EXEMPT_RADIUS_NM = 1.5


@dataclass
class LegResult:
    duration_h: float
    fuel_kg: float
    comfort: float
    wear: float
    max_hs: float
    navigable: bool
    depth_ok: bool
    slam_event: bool
    overload: bool


@dataclass
class LegNavigation:
    course_deg: float
    cts_deg: float
    duration_h: float
    weather_sample: WeatherSample


def leg_navigation(
    p: LatLon,
    q: LatLon,
    stw_ms: float,
    t0_h: float,
    weather: WeatherField,
    ref_lat_deg: float = REF_LAT_DEG,
) -> LegNavigation:
    """Shared course/CTS/duration/weather-sample computation (A4) — used
    both for leg costing (`evaluate_leg`) and for building execution
    setpoints (`core/optimiser.py`'s `_build_leg_targets`), so the
    current-triangle math and weather-time sampling happen in exactly one
    place."""
    leg_distance_m = distance_m(p, q, ref_lat_deg)
    course_deg = bearing_deg(p, q, ref_lat_deg)
    mid = interpolate_point(p, q, 0.5)

    approx_duration_h = m_to_nm(leg_distance_m) / max(ms_to_kn(stw_ms), 1e-6)
    mid_t_h = t0_h + approx_duration_h / 2
    w = weather.sample(mid.lat_deg, mid.lon_deg, mid_t_h)

    ground_speed_ms = resolve_ground_speed_ms(stw_ms, course_deg, w.current_u_ms, w.current_v_ms)
    cts_deg = resolve_course_to_steer_deg(stw_ms, course_deg, w.current_u_ms, w.current_v_ms)
    duration_h = (
        m_to_nm(leg_distance_m) / ms_to_kn(ground_speed_ms) if ground_speed_ms > 0 else float("inf")
    )

    return LegNavigation(
        course_deg=course_deg, cts_deg=cts_deg, duration_h=duration_h, weather_sample=w
    )


@lru_cache(maxsize=200_000)
def _navigable_along_leg(
    p: LatLon, q: LatLon, geography: Geography, ref_lat_deg: float = REF_LAT_DEG
) -> bool:
    """Sample every ~`NAVIGABILITY_SAMPLE_INTERVAL_NM` along the leg (not p
    itself — matches the prior convention of relying on the previous leg's
    endpoint check / the origin being pre-validated; q is always included).

    Memoised: this result depends only on (p, q, geography, ref_lat_deg) —
    never on speed, engine count, or time — but every search tries several
    speeds and engine configs per (p, q) edge (up to 8 speeds x 2 engines =
    16 calls), each re-running the identical distance-sampled geography
    check. Caching this specifically (not all of `evaluate_leg`, which
    *does* vary per speed/engine) turned a 4x sampling-density increase
    (the 0.25nm fix above) into a slowdown large enough to matter (~10x on
    a full lattice search) — `LatLon` is a frozen/hashable dataclass and
    `Geography` instances are stable per search, so this is a safe,
    pure-function cache."""
    leg_distance_nm = m_to_nm(distance_m(p, q, ref_lat_deg))
    n_samples = max(1, math.ceil(leg_distance_nm / NAVIGABILITY_SAMPLE_INTERVAL_NM))
    return all(
        geography.is_navigable(pt.lat_deg, pt.lon_deg)
        for pt in (interpolate_point(p, q, i / n_samples) for i in range(1, n_samples + 1))
    )


@lru_cache(maxsize=200_000)
def _leg_depth_ok(
    p: LatLon,
    q: LatLon,
    geography: Geography,
    min_depth_m: float,
    exempt_points: tuple[LatLon, ...],
    ref_lat_deg: float = REF_LAT_DEG,
) -> bool:
    """Same fixed-distance sampling as `_navigable_along_leg` (a separate
    pass, not merged into it, for one-responsibility-per-function clarity
    -- negligible extra cost, since both are memoised per unique (p, q)
    edge regardless of how many speed/engine combinations evaluate it).
    Samples within `DEPTH_EXEMPT_RADIUS_NM` of any `exempt_points` entry
    (a declared origin/destination) skip the depth check -- pilotage
    scope, see the module-level constant's docstring. Land/no-go is
    unaffected; this is depth-only."""
    leg_distance_nm = m_to_nm(distance_m(p, q, ref_lat_deg))
    n_samples = max(1, math.ceil(leg_distance_nm / NAVIGABILITY_SAMPLE_INTERVAL_NM))
    for i in range(1, n_samples + 1):
        pt = interpolate_point(p, q, i / n_samples)
        if any(
            m_to_nm(distance_m(pt, ep, ref_lat_deg)) <= DEPTH_EXEMPT_RADIUS_NM
            for ep in exempt_points
        ):
            continue
        if geography.depth_m(pt.lat_deg, pt.lon_deg) < min_depth_m:
            return False
    return True


def evaluate_leg(
    p: LatLon,
    q: LatLon,
    stw_ms: float,
    t0_h: float,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    active_engines: int,
    depth_exempt_points: tuple[LatLon, ...] = (),
    ref_lat_deg: float = REF_LAT_DEG,
) -> LegResult:
    """Full leg costing: fuel/comfort/wear at commanded STW, plus the hard-
    constraint checks (A5 land/no-go via `navigable`, minimum depth via
    `depth_ok` -- ticket 0.8, B5 wear-policy via `slam_event`/`overload`)
    every search (production A*, DP fallback, isochrone pre-pass/oracle)
    prunes on identically. `depth_exempt_points` is normally a request's
    real origin/destination (`Lattice.origin`/`.destination`, or a
    corridor's first/last waypoint) -- see `_leg_depth_ok`."""
    nav = leg_navigation(p, q, stw_ms, t0_h, weather, ref_lat_deg)
    w = nav.weather_sample

    navigable = _navigable_along_leg(p, q, geography, ref_lat_deg)
    min_depth_m = twin.spec.hull.draft_m + twin.spec.min_under_keel_clearance_m
    depth_ok = _leg_depth_ok(p, q, geography, min_depth_m, depth_exempt_points, ref_lat_deg)

    fuel_result = twin.fuel_rate(
        v_ms=stw_ms, weather=w, heading_deg=nav.course_deg, active_engines=active_engines
    )
    comfort_rate = twin.motion(v_ms=stw_ms, weather=w, heading_deg=nav.course_deg)
    wear_result = twin.wear(
        v_ms=stw_ms,
        weather=w,
        heading_deg=nav.course_deg,
        load_fraction=max(fuel_result.per_engine_load_fraction),
        active_engines=active_engines,
    )

    return LegResult(
        duration_h=nav.duration_h,
        fuel_kg=fuel_result.fuel_kg_per_h * nav.duration_h,
        comfort=comfort_rate * nav.duration_h,
        wear=wear_result.wear_rate_per_h * nav.duration_h,
        max_hs=w.hs_m,
        navigable=navigable,
        depth_ok=depth_ok,
        slam_event=wear_result.slam_event,
        overload=wear_result.overload,
    )
