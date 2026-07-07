"""Optimiser: open time-expanded A*/DP search over the sea-space lattice
(ticket 0.4), with the ticket 0.2 corridor DP retained as the fallback/fast
path per the roadmap. Ports the demo's weighted scalarisation, ETA-window
handling, and candidate diversity ~1:1 (section D), while fixing the demo
shortcuts and later porting-notes additions:

- A3: single- vs twin-engine choice is a real per-*leg* dimension now (not
  just per-passage) — best config per edge wins, never asserted.
- A4: leg duration comes from speed-over-ground (STW + current, via
  `core.legs`); fuel/motion/wear always take STW.
- A5: land/no-go transitions are pruned from the search frontier, never costed.
- B5: wear-policy hard constraints (slamming, max continuous load) are
  pruned the same way, using the same mechanism as A5.
- B3: `PlanResult.baseline_provisional` flags that any savings-vs-baseline
  comparison is provisional pending ticket 0.7's counterfactual definition.
- C bullet (execution setpoints): `Candidate` exposes `leg_targets`
  (per-leg course, current-corrected course-to-steer, target STW, eta —
  now genuinely per-leg since speed can vary leg to leg) and
  `alteration_list` (course changes >8°), computed once per candidate.

**Ticket 0.4 additions:**
- State space: node = (stage, lane, time_bucket) over `core.lattice`'s open
  lattice — not the two named corridors. Per-leg speed *and* engine config
  are chosen per edge (`core.legs.evaluate_leg` already takes both per call;
  what's new is the search choosing them at every edge, not once per whole
  passage).
- Search: A* (heapq) with an admissible heuristic (remaining distance at
  max candidate speed, costed at calm-water-minimum fuel rate at that speed
  — both are lower bounds given zero current in the current weather model,
  A4's v1 boundary condition) — falls back to the same search with the
  heuristic disabled (equivalent to exhaustive Dijkstra/DP) if the
  heuristic run doesn't find the destination.
- Candidate diversity — route-signature clustering (`_route_signature`)
  replaces the hardcoded corridor-name-based `side`, computed from any
  track's geometry relative to Corsica. The final candidate pool merges
  the lattice search's results with the legacy corridor-DP grid (cheap to
  compute, and IS the roadmap's literal fallback/fast path) — so a lattice
  search failure degrades gracefully to exactly the ticket 0.2 behaviour,
  with no special-case branching needed.
- Isochrone pre-pass (`core.isochrone.reachable_within`) prunes the lattice
  to what's reachable within the ETA window (or a generous default) before
  the A*/DP runs.

**CORE_PORTING_NOTES.md §B6 (de-hardcoding pass):** `PlanRequest.origin`/
`destination` are now arbitrary `LatLon` (port, named anchorage, or dropped
pin), not an implicit reference to the Med `PORTS` pair — `core.lattice`
already took origin/destination as parameters (ticket 0.4), so this was
mostly removing `optimise()`'s own internal `PORTS["antibes"]`/
`PORTS["portocervo"]` references, not a redesign. Endpoints are validated
(navigable always; a plausible anchoring-depth band additionally, when the
caller flags an endpoint as an anchorage rather than a maintained port) —
see `_validate_endpoint`. The legacy corridor-DP grid (`corridor_west`/
`corridor_east`) is Med-specific hand-drawn data (per ROADMAP.md's "Beyond
Phase 2" notes) and only makes sense for the exact pair its waypoints were
drawn for — `optimise()` only includes it in the merged candidate pool when
the request's origin/destination match that pair, not for an arbitrary A→B.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from core.corridors import PORTS, REF_LAT_DEG, Corridor, corridor_east, corridor_west, offset_point
from core.geography import Geography
from core.isochrone import LANE_TURN_RATE, reachable_within
from core.lattice import Lattice, build_lattice
from core.legs import evaluate_leg, leg_navigation
from core.twin import VesselTwin, calm_power_kw
from core.units import LatLon, distance_m, kn_to_ms, m_to_nm, ms_to_kn
from core.vessel_spec import VesselSpec
from core.weather import WeatherField
from core.weights import Weights, combine_weights, weights_from_mission

DEFAULT_SPEEDS_KN: tuple[float, ...] = (10, 11, 12, 13, 14, 15, 16, 17)
BASELINE_SPEED_KN = 14.0
DEFAULT_TIME_BUCKET_H = 1.0
DEFAULT_HORIZON_H = 48.0

# Legacy default endpoints (B6: not to be deepened as global constants —
# this is the one place they're allowed to live on as a default, for
# backward compatibility with the existing Med corridor tests/callers).
DEFAULT_ORIGIN = PORTS["antibes"]
DEFAULT_DESTINATION = PORTS["portocervo"]

# Plausible anchoring depth band (B6) — checked only when a request flags
# an endpoint as an anchorage rather than a maintained port (a real port's
# pin can legitimately read as much deeper/shallower than typical anchoring
# depth at this coarse a geography resolution; a claimed anchorage that
# reads outside this band is very likely a bad pin, not a real spot).
ANCHORAGE_MIN_DEPTH_M = 3.0
ANCHORAGE_MAX_DEPTH_M = 50.0

# Corsica's rough centroid/lat-band, for classifying which side of it a
# route passes — the generalised replacement for the hardcoded corridor
# name/side (C: "generalise... to route-signature clustering").
CORSICA_LAT_BAND = (41.3, 43.0)
CORSICA_REF_LON = 9.0


def _validate_endpoint(point: LatLon, geography: Geography, is_anchorage: bool, label: str) -> None:
    if not geography.is_navigable(point.lat_deg, point.lon_deg):
        raise ValueError(f"{label} ({point.lat_deg}, {point.lon_deg}) is not navigable")
    if is_anchorage:
        depth_m = geography.depth_m(point.lat_deg, point.lon_deg)
        if not (ANCHORAGE_MIN_DEPTH_M <= depth_m <= ANCHORAGE_MAX_DEPTH_M):
            raise ValueError(
                f"{label} ({point.lat_deg}, {point.lon_deg}) has depth {depth_m:.1f}m, "
                f"outside the plausible anchoring band "
                f"[{ANCHORAGE_MIN_DEPTH_M}, {ANCHORAGE_MAX_DEPTH_M}]m"
            )


@dataclass(frozen=True)
class PlanRequest:
    weather: WeatherField
    geography: Geography
    vessel: VesselSpec
    pace: float
    comfort: float
    origin: LatLon = DEFAULT_ORIGIN
    destination: LatLon = DEFAULT_DESTINATION
    origin_is_anchorage: bool = False
    destination_is_anchorage: bool = False
    latest_arrival_h: float | None = None
    departure_t0_h: float = 0.0
    speeds_kn: tuple[float, ...] = DEFAULT_SPEEDS_KN

    def __post_init__(self) -> None:
        _validate_endpoint(self.origin, self.geography, self.origin_is_anchorage, "origin")
        _validate_endpoint(
            self.destination, self.geography, self.destination_is_anchorage, "destination"
        )


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


def _route_signature(track: tuple[LatLon, ...]) -> str:
    """West/east of Corsica, derived from track geometry rather than a
    fixed corridor name — works for any route the lattice search produces."""
    lat_min, lat_max = CORSICA_LAT_BAND
    relevant = [p.lon_deg for p in track if lat_min <= p.lat_deg <= lat_max]
    if not relevant:
        relevant = [p.lon_deg for p in track]
    mean_lon = sum(relevant) / len(relevant)
    return "W" if mean_lon < CORSICA_REF_LON else "E"


def _build_leg_targets(
    track: tuple[LatLon, ...],
    stw_ms_per_leg: list[float],
    t0_h: float,
    weather: WeatherField,
) -> tuple[LegTarget, ...]:
    targets = []
    t = t0_h
    for i in range(1, len(track)):
        stw_ms = stw_ms_per_leg[i - 1]
        nav = leg_navigation(track[i - 1], track[i], stw_ms, t, weather)
        t += nav.duration_h
        targets.append(
            LegTarget(
                from_point=track[i - 1],
                to_point=track[i],
                course_deg=nav.course_deg,
                cts_deg=nav.cts_deg,
                target_stw_kn=ms_to_kn(stw_ms),
                eta_h=t,
            )
        )
    return tuple(targets)


def _build_alteration_list(leg_targets: tuple[LegTarget, ...]) -> tuple[Alteration, ...]:
    """Ported from the demo's `nextAlteration` (8 deg threshold), but
    precomputed for the whole passage rather than derived live from the
    current vessel position (C bullet — 'core provides targets only')."""
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


# ---------------------------------------------------------------------------
# Ticket 0.4: A*/DP over the open lattice.
# ---------------------------------------------------------------------------


def _heuristic_cost_eur(
    current: LatLon, destination: LatLon, weights: Weights, vessel: VesselSpec, max_speed_kn: float
) -> float:
    """Admissible lower bound on remaining cost-to-go: fastest possible
    time at max speed, costed at best-case (calm-water, optimal-SFOC) fuel
    rate. Real conditions can only be slower and thirstier — admissible
    given zero current in the current weather model (A4's v1 boundary
    condition; a strong following current could in principle beat this,
    but v1 currents are always zero)."""
    if max_speed_kn <= 0:
        return 0.0
    remaining_nm = m_to_nm(distance_m(current, destination, REF_LAT_DEG))
    min_time_h = remaining_nm / max_speed_kn
    time_cost = weights.time_eur_per_min * min_time_h * 60

    max_speed_ms = kn_to_ms(max_speed_kn)
    calm_power_total_kw = calm_power_kw(max_speed_ms, vessel)
    best_sfoc = min(e.sfoc_base_g_per_kwh for e in vessel.engines)
    min_fuel_kg_per_h = calm_power_total_kw * best_sfoc / 1000.0 + vessel.hotel_load_fuel_kg_per_h
    fuel_cost = weights.fuel_eur_per_kg * min_fuel_kg_per_h * min_time_h

    return time_cost + fuel_cost


@dataclass
class _SearchNode:
    g: float
    elapsed_h: float
    fuel_kg: float
    comfort: float
    wear: float
    max_hs: float


State = tuple[int, int, int]  # (stage, lane, time_bucket)


def _lattice_search(
    lattice: Lattice,
    reachable: set[tuple[int, int]] | None,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    vessel: VesselSpec,
    weights: Weights,
    speeds_kn: tuple[float, ...],
    engine_configs: tuple[int, ...],
    t0_h: float,
    time_bucket_h: float,
    use_heuristic: bool,
    lane_filter=None,
) -> tuple[State, dict[State, _SearchNode], dict[State, tuple[State, float, int]]] | None:
    """A* (or, with `use_heuristic=False`, an equivalent exhaustive
    Dijkstra/DP sweep) over the time-expanded lattice. Returns
    (goal_state, nodes_by_state, predecessor) or None if unreachable."""
    max_speed_kn = max(speeds_kn)
    destination_stage = lattice.n_stages - 1
    start: State = (0, 0, 0)
    best_node: dict[State, _SearchNode] = {start: _SearchNode(0.0, t0_h, 0.0, 0.0, 0.0, 0.0)}
    predecessor: dict[State, tuple[State, float, int]] = {}

    def heuristic(stage: int, lane: int) -> float:
        if not use_heuristic:
            return 0.0
        return _heuristic_cost_eur(
            lattice.point(stage, lane), lattice.destination, weights, vessel, max_speed_kn
        )

    heap: list[tuple[float, float, State]] = [(heuristic(0, 0), 0.0, start)]

    while heap:
        _, g, state = heapq.heappop(heap)
        if g > best_node[state].g + 1e-9:
            continue  # stale entry, a cheaper path to this state was found since
        stage, lane, _tb = state
        if stage == destination_stage and lane == 0:
            return state, best_node, predecessor

        if stage + 1 >= lattice.n_stages:
            continue
        node = best_node[state]
        next_max_lane = lattice.max_lane_per_stage[stage + 1]
        lane_lo = max(-next_max_lane, lane - LANE_TURN_RATE)
        lane_hi = min(next_max_lane, lane + LANE_TURN_RATE)
        for next_lane in range(lane_lo, lane_hi + 1):
            if lane_filter is not None and not lane_filter(next_lane):
                continue
            if reachable is not None and (stage + 1, next_lane) not in reachable:
                continue
            p, q = lattice.point(stage, lane), lattice.point(stage + 1, next_lane)
            for speed_kn in speeds_kn:
                stw_ms = kn_to_ms(speed_kn)
                for active_engines in engine_configs:
                    leg = evaluate_leg(
                        p, q, stw_ms, node.elapsed_h, weather, geography, twin, active_engines
                    )
                    if not leg.navigable or leg.slam_event or leg.overload:
                        continue
                    leg_cost = (
                        weights.fuel_eur_per_kg * leg.fuel_kg
                        + weights.time_eur_per_min * leg.duration_h * 60
                        + weights.comfort_eur_per_index_point * leg.comfort
                        + weights.wear_eur_per_index_point * leg.wear
                    )
                    new_g = node.g + leg_cost
                    new_elapsed = node.elapsed_h + leg.duration_h
                    new_bucket = int((new_elapsed - t0_h) / time_bucket_h)
                    new_state: State = (stage + 1, next_lane, new_bucket)
                    if new_g < best_node.get(new_state, _SearchNode(float("inf"), 0, 0, 0, 0, 0)).g:
                        best_node[new_state] = _SearchNode(
                            g=new_g,
                            elapsed_h=new_elapsed,
                            fuel_kg=node.fuel_kg + leg.fuel_kg,
                            comfort=node.comfort + leg.comfort,
                            wear=node.wear + leg.wear,
                            max_hs=max(node.max_hs, leg.max_hs),
                        )
                        predecessor[new_state] = (state, stw_ms, active_engines)
                        heapq.heappush(
                            heap, (new_g + heuristic(stage + 1, next_lane), new_g, new_state)
                        )

    return None


def _reconstruct_lattice_route(
    lattice: Lattice,
    goal_state: State,
    nodes_by_state: dict[State, _SearchNode],
    predecessor: dict[State, tuple[State, float, int]],
) -> tuple[tuple[LatLon, ...], list[float], list[int], list[float]]:
    states = [goal_state]
    while states[-1] in predecessor:
        states.append(predecessor[states[-1]][0])
    states.reverse()

    track = tuple(lattice.point(stage, lane) for stage, lane, _ in states)
    stw_ms_per_leg: list[float] = []
    engines_per_leg: list[int] = []
    duration_per_leg: list[float] = []
    for i in range(1, len(states)):
        _, stw_ms, active_engines = predecessor[states[i]]
        stw_ms_per_leg.append(stw_ms)
        engines_per_leg.append(active_engines)
        duration_per_leg.append(
            nodes_by_state[states[i]].elapsed_h - nodes_by_state[states[i - 1]].elapsed_h
        )
    return track, stw_ms_per_leg, engines_per_leg, duration_per_leg


def _majority_by_duration(values: list[int], durations_h: list[float]) -> int:
    totals: dict[int, float] = {}
    for v, d in zip(values, durations_h, strict=True):
        totals[v] = totals.get(v, 0.0) + d
    return max(totals, key=lambda k: totals[k])


def _lattice_route_result(
    lattice: Lattice,
    reachable: set[tuple[int, int]] | None,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    vessel: VesselSpec,
    weights: Weights,
    speeds_kn: tuple[float, ...],
    engine_configs: tuple[int, ...],
    t0_h: float,
    lane_filter=None,
) -> dict | None:
    search = _lattice_search(
        lattice,
        reachable,
        weather,
        geography,
        twin,
        vessel,
        weights,
        speeds_kn,
        engine_configs,
        t0_h,
        DEFAULT_TIME_BUCKET_H,
        use_heuristic=True,
        lane_filter=lane_filter,
    )
    if search is None:
        # Fall back to an exhaustive sweep (heuristic disabled) — the
        # roadmap's "if heuristic admissibility gets awkward" contingency.
        search = _lattice_search(
            lattice,
            reachable,
            weather,
            geography,
            twin,
            vessel,
            weights,
            speeds_kn,
            engine_configs,
            t0_h,
            DEFAULT_TIME_BUCKET_H,
            use_heuristic=False,
            lane_filter=lane_filter,
        )
    if search is None:
        return None

    goal_state, nodes_by_state, predecessor = search
    track, stw_ms_per_leg, engines_per_leg, duration_per_leg = _reconstruct_lattice_route(
        lattice, goal_state, nodes_by_state, predecessor
    )
    goal_node = nodes_by_state[goal_state]
    distance_nm = sum(
        m_to_nm(distance_m(track[i - 1], track[i], REF_LAT_DEG)) for i in range(1, len(track))
    )
    leg_targets = _build_leg_targets(track, stw_ms_per_leg, t0_h, weather)
    return {
        "duration_h": goal_node.elapsed_h - t0_h,
        "fuel_kg": goal_node.fuel_kg,
        "comfort_index": goal_node.comfort,
        "wear_index": goal_node.wear,
        "max_hs_m": goal_node.max_hs,
        "track": track,
        "distance_nm": distance_nm,
        "cost": goal_node.g,
        "leg_targets": leg_targets,
        "alteration_list": _build_alteration_list(leg_targets),
        "speed_kn": distance_nm / (goal_node.elapsed_h - t0_h)
        if goal_node.elapsed_h > t0_h
        else 0.0,
        "active_engines": _majority_by_duration(engines_per_leg, duration_per_leg),
        "side": _route_signature(track),
    }


# ---------------------------------------------------------------------------
# Ticket 0.2: corridor DP — retained as the fallback/fast path.
# ---------------------------------------------------------------------------


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
                leg = evaluate_leg(p, q, stw_ms, s["t"], weather, geography, twin, active_engines)
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
    leg_targets = _build_leg_targets(track, [stw_ms] * (len(track) - 1), t0_h, weather)
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
    lattice: Lattice,
    reachable: set[tuple[int, int]] | None,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    t0_h: float,
    speed_kn: float = BASELINE_SPEED_KN,
) -> Candidate:
    """Do-nothing reference: fixed speed/engine-config (14kn, 2 engines),
    routed via the open lattice search — not a straight centreline walk,
    and not `corridor_west`.

    Why not corridor_west (found during ticket 0.4 review): its D->D2
    waypoint segment cuts directly across the real Bonifacio Strait / Iles
    Lavezzi archipelago — dozens of scattered granite islets between
    Corsica and Sardinia (CLAUDE.md's Bonifacio Strait gotcha; see also
    `core/corridors.py`'s `corridor_west` docstring). Under `RealGeography`
    it is infeasible at *every* speed/engine combination — verified by
    widening its lateral-offset allowance to +-5 lanes and turn-rate to
    +-3, which still finds no path (a two-waypoint straight-line segment
    just doesn't thread a scattered reef field). Properly routing this
    strait needs the real TSS lane geometry and chart-derived no-go data —
    that's ticket 0.8's job, not something to improvise here from raw
    coastline polygons. The lattice search already reliably finds a
    feasible route through this exact area (that's the point of ticket
    0.4), so it backs the baseline too, at a fixed reference speed/config
    rather than letting mission weights pick the "best" one — still a
    do-nothing reference, not a recommendation. Provisional (B3) until
    ticket 0.7 regardless of any of the above.

    `reachable` is an isochrone pre-pass over a *generous* horizon — an
    unpruned lattice search is dramatically slower, so this must not be
    skipped, but it also must not reuse the candidate search's own
    `reachable` unmodified: that one is pruned to the *requested* ETA
    window, which can be arbitrarily tight (or, in one regression test,
    deliberately impossible) — reusing it here would prune away the
    destination itself and make this do-nothing reference come back
    infeasible even though a real route exists. `optimise()` passes a
    dedicated `baseline_reachable`, computed with `DEFAULT_HORIZON_H`
    whenever the request's own window is tighter than that.
    """
    neutral_weights = combine_weights(
        weights_from_mission(pace=50, comfort=50), twin.spec.wear_policy
    )
    result = _lattice_route_result(
        lattice,
        reachable,
        weather,
        geography,
        twin,
        twin.spec,
        neutral_weights,
        (speed_kn,),
        (2,),
        t0_h,
    )
    if result is None:
        raise RuntimeError(
            "baseline route (fixed 14kn, 2 engines) is infeasible even via the open "
            "lattice search — this should not happen given its demonstrated coverage; "
            "investigate before trusting any PlanResult from this geography/weather."
        )
    return Candidate(
        corridor_name="Lattice route (baseline reference — see _baseline_route docstring)",
        side=_route_signature(result["track"]),
        speed_kn=speed_kn,
        active_engines=2,
        track=result["track"],
        duration_h=result["duration_h"],
        distance_nm=result["distance_nm"],
        fuel_kg=result["fuel_kg"],
        comfort_index=result["comfort_index"],
        wear_index=result["wear_index"],
        max_hs_m=result["max_hs_m"],
        score_eur=float("nan"),
        meets_eta_window=None,
        leg_targets=result["leg_targets"],
        alteration_list=result["alteration_list"],
    )


def _candidate_from_result(
    result: dict,
    corridor_name: str,
    side: str,
    speed_kn: float,
    active_engines: int,
    latest_arrival_h: float | None,
) -> Candidate:
    return Candidate(
        corridor_name=corridor_name,
        side=side,
        speed_kn=speed_kn,
        active_engines=active_engines,
        track=result["track"],
        duration_h=result["duration_h"],
        distance_nm=result["distance_nm"],
        fuel_kg=result["fuel_kg"],
        comfort_index=result["comfort_index"],
        wear_index=result["wear_index"],
        max_hs_m=result["max_hs_m"],
        score_eur=result["cost"],
        meets_eta_window=(
            result["duration_h"] <= latest_arrival_h if latest_arrival_h is not None else None
        ),
        leg_targets=result["leg_targets"],
        alteration_list=result["alteration_list"],
    )


def optimise(request: PlanRequest) -> PlanResult:
    twin = VesselTwin(request.vessel)
    mission_weights = weights_from_mission(request.pace, request.comfort)
    weights = combine_weights(mission_weights, request.vessel.wear_policy)
    engine_configs = tuple(range(1, len(request.vessel.engines) + 1))

    candidates_all: list[Candidate] = []

    # --- Ticket 0.4: open lattice search (primary path) ---
    lattice = build_lattice(request.origin, request.destination)
    horizon_h = (
        request.latest_arrival_h if request.latest_arrival_h is not None else DEFAULT_HORIZON_H
    )
    reachable = reachable_within(
        lattice,
        request.weather,
        request.geography,
        twin,
        request.speeds_kn,
        engine_configs,
        request.departure_t0_h,
        horizon_h,
    )
    # The baseline must reflect the *actual* achievable passage regardless
    # of the requested ETA window — it's a do-nothing reference, not
    # subject to schedule pressure. Reusing `reachable` above would be
    # wrong when the window is tight (or, as a regression test deliberately
    # does, impossibly tight): the pre-pass would prune away the
    # destination itself, and the baseline would come back infeasible even
    # though a real route exists. Only recompute when the request's own
    # horizon was actually tighter than the generous default.
    baseline_reachable = (
        reachable
        if horizon_h >= DEFAULT_HORIZON_H
        else reachable_within(
            lattice,
            request.weather,
            request.geography,
            twin,
            request.speeds_kn,
            engine_configs,
            request.departure_t0_h,
            DEFAULT_HORIZON_H,
        )
    )

    primary = _lattice_route_result(
        lattice,
        reachable,
        request.weather,
        request.geography,
        twin,
        request.vessel,
        weights,
        request.speeds_kn,
        engine_configs,
        request.departure_t0_h,
    )
    if primary is not None:
        candidates_all.append(
            _candidate_from_result(
                primary,
                "Lattice route",
                primary["side"],
                primary["speed_kn"],
                primary["active_engines"],
                request.latest_arrival_h,
            )
        )
        # opposite-side candidate for diversity — lane sign correlates with
        # side (west corridor waypoints all had negative cross-track offset
        # relative to the direct line, east corridor all positive).
        opposite_filter = (
            (lambda lane: lane <= 0) if primary["side"] == "E" else (lambda lane: lane >= 0)
        )
        secondary = _lattice_route_result(
            lattice,
            reachable,
            request.weather,
            request.geography,
            twin,
            request.vessel,
            weights,
            request.speeds_kn,
            engine_configs,
            request.departure_t0_h,
            lane_filter=opposite_filter,
        )
        if secondary is not None and secondary["side"] != primary["side"]:
            candidates_all.append(
                _candidate_from_result(
                    secondary,
                    "Lattice route",
                    secondary["side"],
                    secondary["speed_kn"],
                    secondary["active_engines"],
                    request.latest_arrival_h,
                )
            )

    # --- Ticket 0.2: corridor DP grid — Med-specific hand-drawn waypoints
    # (B6/ROADMAP.md "Beyond Phase 2"), only meaningful for the exact
    # origin/destination pair they were drawn for. Computed too when it
    # applies, both as the roadmap's literal fallback/fast path (if the
    # lattice search above found nothing) and as a cheap source of extra
    # diversity otherwise — for any other origin/destination, skipped
    # entirely; the lattice search + baseline are origin/destination-general
    # on their own. ---
    grid: list[dict] = []
    if request.origin == DEFAULT_ORIGIN and request.destination == DEFAULT_DESTINATION:
        for corridor_fn in (corridor_west, corridor_east):
            corridor = corridor_fn()
            for speed_kn in request.speeds_kn:
                stw_ms = kn_to_ms(speed_kn)
                for active_engines in engine_configs:
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

    best_by_key: dict[tuple[str, float], dict] = {}
    for item in grid:
        key = (item["corridor"].name, item["speed_kn"])
        if key not in best_by_key or item["result"]["cost"] < best_by_key[key]["result"]["cost"]:
            best_by_key[key] = item

    for item in best_by_key.values():
        candidates_all.append(
            _candidate_from_result(
                item["result"],
                item["corridor"].name,
                item["corridor"].side,
                item["speed_kn"],
                item["active_engines"],
                request.latest_arrival_h,
            )
        )

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

    baseline = _baseline_route(
        lattice,
        baseline_reachable,
        request.weather,
        request.geography,
        twin,
        request.departure_t0_h,
    )

    return PlanResult(
        candidates=tuple(picks), baseline=baseline, weights=weights, missed_window=missed_window
    )
