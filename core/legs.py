"""Single-leg evaluation: course/CTS/duration (A4) and full leg costing
(fuel/comfort/wear + hard-constraint checks, A5/B5). Shared by
`core/optimiser.py` (the production search) and `core/isochrone.py` (the
pre-pruning pass and time-optimal cross-check oracle, ticket 0.4) — both
need identical hard-constraint semantics, so the logic lives in one place
rather than being duplicated (and risking drift) across two modules.
"""

from __future__ import annotations

from dataclasses import dataclass

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

LEG_SAMPLE_FRACTIONS: tuple[float, ...] = (0.15, 0.3, 0.45, 0.6, 0.75, 0.9)


@dataclass
class LegResult:
    duration_h: float
    fuel_kg: float
    comfort: float
    wear: float
    max_hs: float
    navigable: bool
    slam_event: bool
    overload: bool


@dataclass
class LegNavigation:
    course_deg: float
    cts_deg: float
    duration_h: float
    weather_sample: WeatherSample


def leg_navigation(
    p: LatLon, q: LatLon, stw_ms: float, t0_h: float, weather: WeatherField
) -> LegNavigation:
    """Shared course/CTS/duration/weather-sample computation (A4) — used
    both for leg costing (`evaluate_leg`) and for building execution
    setpoints (`core/optimiser.py`'s `_build_leg_targets`), so the
    current-triangle math and weather-time sampling happen in exactly one
    place."""
    leg_distance_m = distance_m(p, q, REF_LAT_DEG)
    course_deg = bearing_deg(p, q, REF_LAT_DEG)
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


def evaluate_leg(
    p: LatLon,
    q: LatLon,
    stw_ms: float,
    t0_h: float,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    active_engines: int,
) -> LegResult:
    """Full leg costing: fuel/comfort/wear at commanded STW, plus the two
    hard-constraint checks (A5 land/no-go via `navigable`, B5 wear-policy
    via `slam_event`/`overload`) every search (production A*, DP fallback,
    isochrone pre-pass/oracle) prunes on identically."""
    nav = leg_navigation(p, q, stw_ms, t0_h, weather)
    w = nav.weather_sample

    navigable = all(
        geography.is_navigable(pt.lat_deg, pt.lon_deg)
        for pt in (*(interpolate_point(p, q, f) for f in LEG_SAMPLE_FRACTIONS), q)
    )

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
        slam_event=wear_result.slam_event,
        overload=wear_result.overload,
    )
