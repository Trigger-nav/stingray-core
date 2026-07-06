"""Corridor DP optimiser: ports the demo's weighted scalarisation, ETA-window
handling, and candidate diversity ~1:1 (section D), while fixing the demo
shortcuts scoped to ticket 0.2:

- A3: single- vs twin-engine choice is evaluated as a real candidate
  dimension (best config per corridor/speed wins), not asserted by a
  speed-threshold branch.
- A4: leg duration comes from speed-over-ground (STW + current, via
  `resolve_ground_speed_ms`); fuel/motion/wear always take STW.
- A5: land/no-go transitions are pruned from the DP frontier, never costed.
- B5: wear-policy hard constraints (slamming, max continuous load) are
  pruned the same way, using the same mechanism as A5.
- B3: `PlanResult.baseline_provisional` flags that any savings-vs-baseline
  comparison is provisional pending ticket 0.7's counterfactual definition.
- New C bullet: `Candidate` exposes execution setpoints — `leg_targets`
  (per-leg course, current-corrected course-to-steer, target STW, eta) and
  `alteration_list` (course changes >8°, ported from the demo's
  `nextAlteration`/`posAtTime`) — computed once per surfaced candidate, not
  derived live from track geometry the way the demo's Underway UI does it.

Per-leg speed (open time-expanded graph search) is ticket 0.4's job — every
candidate here still commands one constant speed for the whole passage,
same as the demo (see the ticket 0.2 plan's explicit scope boundary).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.corridors import REF_LAT_DEG, Corridor, corridor_east, corridor_west, offset_point
from core.geography import Geography
from core.twin import VesselTwin
from core.units import (
    LatLon,
    bearing_deg,
    distance_m,
    interpolate_point,
    kn_to_ms,
    m_to_nm,
    ms_to_kn,
    resolve_course_to_steer_deg,
    resolve_ground_speed_ms,
)
from core.vessel_spec import VesselSpec
from core.weather import WeatherField, WeatherSample
from core.weights import Weights, combine_weights, weights_from_mission

DEFAULT_SPEEDS_KN: tuple[float, ...] = (10, 11, 12, 13, 14, 15, 16, 17)
BASELINE_SPEED_KN = 14.0
LEG_SAMPLE_FRACTIONS: tuple[float, ...] = (0.15, 0.3, 0.45, 0.6, 0.75, 0.9)


@dataclass(frozen=True)
class PlanRequest:
    weather: WeatherField
    geography: Geography
    vessel: VesselSpec
    pace: float
    comfort: float
    latest_arrival_h: float | None = None
    departure_t0_h: float = 0.0
    speeds_kn: tuple[float, ...] = DEFAULT_SPEEDS_KN


@dataclass(frozen=True)
class LegTarget:
    from_point: LatLon
    to_point: LatLon
    course_deg: float
    cts_deg: float
    target_stw_kn: float
    eta_h: float


@dataclass(frozen=True)
class Alteration:
    position: LatLon
    time_h: float
    new_cts_deg: float


@dataclass(frozen=True)
class Candidate:
    corridor_name: str
    side: str
    speed_kn: float
    active_engines: int
    track: tuple[LatLon, ...]
    duration_h: float
    distance_nm: float
    fuel_kg: float
    comfort_index: float
    wear_index: float
    max_hs_m: float
    score_eur: float
    meets_eta_window: bool | None
    leg_targets: tuple[LegTarget, ...]
    alteration_list: tuple[Alteration, ...]


@dataclass(frozen=True)
class PlanResult:
    candidates: tuple[Candidate, ...]
    baseline: Candidate
    weights: Weights
    missed_window: bool
    baseline_provisional: bool = True


@dataclass
class _LegResult:
    duration_h: float
    fuel_kg: float
    comfort: float
    wear: float
    max_hs: float
    navigable: bool
    slam_event: bool
    overload: bool


@dataclass
class _LegNavigation:
    course_deg: float
    cts_deg: float
    duration_h: float
    weather_sample: WeatherSample


def _leg_navigation(
    p: LatLon, q: LatLon, stw_ms: float, t0_h: float, weather: WeatherField
) -> _LegNavigation:
    """Shared course/CTS/duration/weather-sample computation (A4, new C
    bullet) — used both for leg costing (`_evaluate_leg`) and for building
    the execution setpoints (`_build_leg_targets`), so the current-triangle
    math and weather-time sampling happen in exactly one place."""
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

    return _LegNavigation(
        course_deg=course_deg, cts_deg=cts_deg, duration_h=duration_h, weather_sample=w
    )


def _evaluate_leg(
    p: LatLon,
    q: LatLon,
    stw_ms: float,
    t0_h: float,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    active_engines: int,
) -> _LegResult:
    nav = _leg_navigation(p, q, stw_ms, t0_h, weather)
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

    return _LegResult(
        duration_h=nav.duration_h,
        fuel_kg=fuel_result.fuel_kg_per_h * nav.duration_h,
        comfort=comfort_rate * nav.duration_h,
        wear=wear_result.wear_rate_per_h * nav.duration_h,
        max_hs=w.hs_m,
        navigable=navigable,
        slam_event=wear_result.slam_event,
        overload=wear_result.overload,
    )


def _build_leg_targets(
    track: tuple[LatLon, ...], stw_ms: float, speed_kn: float, t0_h: float, weather: WeatherField
) -> tuple[LegTarget, ...]:
    targets = []
    t = t0_h
    for i in range(1, len(track)):
        nav = _leg_navigation(track[i - 1], track[i], stw_ms, t, weather)
        t += nav.duration_h
        targets.append(
            LegTarget(
                from_point=track[i - 1],
                to_point=track[i],
                course_deg=nav.course_deg,
                cts_deg=nav.cts_deg,
                target_stw_kn=speed_kn,
                eta_h=t,
            )
        )
    return tuple(targets)


def _build_alteration_list(leg_targets: tuple[LegTarget, ...]) -> tuple[Alteration, ...]:
    """Ported from the demo's `nextAlteration` (8 deg threshold), but
    precomputed for the whole passage rather than derived live from the
    current vessel position (new C bullet — 'core provides targets only')."""
    alterations = []
    for i in range(1, len(leg_targets)):
        h1, h2 = leg_targets[i - 1].course_deg, leg_targets[i].course_deg
        dh = ((h2 - h1 + 540) % 360) - 180
        if abs(dh) > 8:
            alterations.append(
                Alteration(
                    position=leg_targets[i].from_point,
                    time_h=leg_targets[i - 1].eta_h,
                    new_cts_deg=leg_targets[i].cts_deg,
                )
            )
    return tuple(alterations)


def _dp_route(
    corridor: Corridor,
    stw_ms: float,
    speed_kn: float,
    active_engines: int,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    weights: Weights,
    t0_h: float,
) -> dict | None:
    states: dict[int, dict] = {
        0: {
            "cost": 0.0,
            "t": t0_h,
            "fuel": 0.0,
            "comfort": 0.0,
            "wear": 0.0,
            "max_hs": 0.0,
            "path": [0],
        }
    }
    n = len(corridor.points)
    for i in range(1, n):
        next_states: dict[int, dict] = {}
        k_max = corridor.max_offset_steps[i]
        for k in range(-k_max, k_max + 1):
            q = offset_point(corridor, i, k)
            best = None
            for pk, s in states.items():
                if abs(pk - k) > 1:
                    continue
                p = offset_point(corridor, i - 1, pk)
                leg = _evaluate_leg(p, q, stw_ms, s["t"], weather, geography, twin, active_engines)
                if not leg.navigable:  # A5: hard prune, never costed
                    continue
                if leg.slam_event or leg.overload:  # B5: hard prune, never costed
                    continue
                cost = (
                    s["cost"]
                    + weights.fuel_eur_per_kg * leg.fuel_kg
                    + weights.time_eur_per_min * leg.duration_h * 60
                    + weights.comfort_eur_per_index_point * leg.comfort
                    + weights.wear_eur_per_index_point * leg.wear
                )
                if best is None or cost < best["cost"]:
                    best = {
                        "cost": cost,
                        "t": s["t"] + leg.duration_h,
                        "fuel": s["fuel"] + leg.fuel_kg,
                        "comfort": s["comfort"] + leg.comfort,
                        "wear": s["wear"] + leg.wear,
                        "max_hs": max(s["max_hs"], leg.max_hs),
                        "path": [*s["path"], k],
                    }
            if best is not None:
                next_states[k] = best
        states = next_states
        if not states:
            return None  # every option pruned at this speed/config -> infeasible

    end = states.get(0) or min(states.values(), key=lambda s: s["cost"])
    track = tuple(offset_point(corridor, i, k) for i, k in enumerate(end["path"]))
    distance_nm = sum(
        m_to_nm(distance_m(track[i - 1], track[i], REF_LAT_DEG)) for i in range(1, len(track))
    )
    leg_targets = _build_leg_targets(track, stw_ms, speed_kn, t0_h, weather)
    return {
        "duration_h": end["t"] - t0_h,
        "fuel_kg": end["fuel"],
        "comfort_index": end["comfort"],
        "wear_index": end["wear"],
        "max_hs_m": end["max_hs"],
        "track": track,
        "distance_nm": distance_nm,
        "cost": end["cost"],
        "leg_targets": leg_targets,
        "alteration_list": _build_alteration_list(leg_targets),
    }


def _baseline_route(
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    t0_h: float,
    speed_kn: float = BASELINE_SPEED_KN,
) -> Candidate:
    """Do-nothing: straight down the west-corridor centreline, no micro-
    routing, twin-engine. Provisional (B3) until ticket 0.7."""
    corridor = corridor_west()
    stw_ms = kn_to_ms(speed_kn)
    pts = corridor.points
    t, fuel, comfort, wear, max_hs = t0_h, 0.0, 0.0, 0.0, 0.0
    for i in range(1, len(pts)):
        leg = _evaluate_leg(
            pts[i - 1], pts[i], stw_ms, t, weather, geography, twin, active_engines=2
        )
        t += leg.duration_h
        fuel += leg.fuel_kg
        comfort += leg.comfort
        wear += leg.wear
        max_hs = max(max_hs, leg.max_hs)
    distance_nm = sum(
        m_to_nm(distance_m(pts[i - 1], pts[i], REF_LAT_DEG)) for i in range(1, len(pts))
    )
    leg_targets = _build_leg_targets(tuple(pts), stw_ms, speed_kn, t0_h, weather)
    return Candidate(
        corridor_name=corridor.name,
        side=corridor.side,
        speed_kn=speed_kn,
        active_engines=2,
        track=tuple(pts),
        duration_h=t - t0_h,
        distance_nm=distance_nm,
        fuel_kg=fuel,
        comfort_index=comfort,
        wear_index=wear,
        max_hs_m=max_hs,
        score_eur=float("nan"),
        meets_eta_window=None,
        leg_targets=leg_targets,
        alteration_list=_build_alteration_list(leg_targets),
    )


def optimise(request: PlanRequest) -> PlanResult:
    twin = VesselTwin(request.vessel)
    mission_weights = weights_from_mission(request.pace, request.comfort)
    weights = combine_weights(mission_weights, request.vessel.wear_policy)

    grid: list[dict] = []
    for corridor_fn in (corridor_west, corridor_east):
        corridor = corridor_fn()
        for speed_kn in request.speeds_kn:
            stw_ms = kn_to_ms(speed_kn)
            for active_engines in range(1, len(request.vessel.engines) + 1):
                result = _dp_route(
                    corridor,
                    stw_ms,
                    speed_kn,
                    active_engines,
                    request.weather,
                    request.geography,
                    twin,
                    weights,
                    request.departure_t0_h,
                )
                if result is None:
                    continue
                grid.append(
                    {
                        "corridor": corridor,
                        "speed_kn": speed_kn,
                        "active_engines": active_engines,
                        "result": result,
                    }
                )

    # A3: keep only the best-scoring engine config per (corridor, speed) —
    # single-/twin-engine advantage must emerge from comparison, not a branch.
    best_by_key: dict[tuple[str, float], dict] = {}
    for item in grid:
        key = (item["corridor"].name, item["speed_kn"])
        if key not in best_by_key or item["result"]["cost"] < best_by_key[key]["result"]["cost"]:
            best_by_key[key] = item

    candidates_all = [
        Candidate(
            corridor_name=item["corridor"].name,
            side=item["corridor"].side,
            speed_kn=item["speed_kn"],
            active_engines=item["active_engines"],
            track=item["result"]["track"],
            duration_h=item["result"]["duration_h"],
            distance_nm=item["result"]["distance_nm"],
            fuel_kg=item["result"]["fuel_kg"],
            comfort_index=item["result"]["comfort_index"],
            wear_index=item["result"]["wear_index"],
            max_hs_m=item["result"]["max_hs_m"],
            score_eur=item["result"]["cost"],
            meets_eta_window=(
                item["result"]["duration_h"] <= request.latest_arrival_h
                if request.latest_arrival_h is not None
                else None
            ),
            leg_targets=item["result"]["leg_targets"],
            alteration_list=item["result"]["alteration_list"],
        )
        for item in best_by_key.values()
    ]

    missed_window = False
    if request.latest_arrival_h is not None:
        feasible = [c for c in candidates_all if c.duration_h <= request.latest_arrival_h]
        if feasible:
            pool = feasible
        else:
            missed_window = True
            pool = sorted(candidates_all, key=lambda c: c.duration_h)[:4]
    else:
        pool = list(candidates_all)

    pool.sort(key=(lambda c: c.duration_h) if missed_window else (lambda c: c.score_eur))

    picks: list[Candidate] = []
    if pool:
        picks.append(pool[0])
        for c in pool:
            if len(picks) >= 3:
                break
            if all(p.side != c.side or abs(p.speed_kn - c.speed_kn) >= 2 for p in picks):
                picks.append(c)

    baseline = _baseline_route(request.weather, request.geography, twin, request.departure_t0_h)

    return PlanResult(
        candidates=tuple(picks), baseline=baseline, weights=weights, missed_window=missed_window
    )
