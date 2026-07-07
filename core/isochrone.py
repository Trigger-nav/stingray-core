"""Isochrone supporting roles (ticket 0.4, per `TECHNICAL_ARCHITECTURE.md`
§5's July 2026 decision record): isochrone methods were evaluated and
rejected as the *core* search method (frontier-dominance pruning assumes
single-objective time-optimality and open water, both false here). They're
retained in two supporting roles, both implemented here:

1. `reachable_within` — a cheap, single-objective (time-only) Dijkstra
   wavefront over the lattice, used to prune the lattice *before* the full
   multi-objective A*/DP runs (`core/optimiser.py`), bounding the search to
   what's reachable within the ETA window.
2. `time_optimal_route` — the actual time-optimal route (fastest feasible
   path, ignoring fuel/comfort/wear entirely) — a **test oracle only**, not
   part of the production planning path. Under pure-schedule weights the
   production search's route must converge to this one.

Both respect the same hard constraints (A5 land/no-go, B5 wear-policy
slamming/overload) as the production search — reusing `core/legs.py`'s
`evaluate_leg`, so there's exactly one place hard-constraint semantics live.
"""

from __future__ import annotations

import heapq

from core.geography import Geography
from core.lattice import Lattice
from core.legs import evaluate_leg
from core.twin import VesselTwin
from core.units import LatLon, kn_to_ms
from core.weather import WeatherField

Node = tuple[int, int]  # (stage, lane)

# Max lane change per along-track stage. Ported from the demo's corridor
# turn-rate limit (which used +-1 against its own already-bent, hand-drawn
# centreline); the open lattice offsets from the *straight* rhumb line, so
# routing around Corsica needs a materially larger lateral excursion in the
# same number of stages -- +-1 measurably under-serves it (verified: it
# made the legacy corridor-DP grid consistently beat the lattice search on
# its own best side, which isn't a hard-constraint issue, just too little
# room to bend).
LANE_TURN_RATE = 2


def _neighbour_lanes(lattice: Lattice, stage: int, lane: int) -> range:
    next_max_lane = lattice.max_lane_per_stage[stage]
    return range(
        max(-next_max_lane, lane - LANE_TURN_RATE), min(next_max_lane, lane + LANE_TURN_RATE) + 1
    )


def _best_feasible_duration_h(
    p: LatLon,
    q: LatLon,
    t_h: float,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    speeds_kn: tuple[float, ...],
    engine_configs: tuple[int, ...],
) -> float | None:
    """Fastest feasible leg duration trying every (speed, engine-config)
    combination — engine count doesn't change duration directly, but *does*
    change overload feasibility (fewer engines -> higher per-engine load),
    so it must be tried alongside speed, not fixed, for a correct
    over-approximation of what's reachable/feasible at all."""
    best: float | None = None
    for speed_kn in speeds_kn:
        stw_ms = kn_to_ms(speed_kn)
        for active_engines in engine_configs:
            leg = evaluate_leg(p, q, stw_ms, t_h, weather, geography, twin, active_engines)
            if not leg.navigable or leg.slam_event or leg.overload:
                continue
            if best is None or leg.duration_h < best:
                best = leg.duration_h
    return best


def reachable_within(
    lattice: Lattice,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    speeds_kn: tuple[float, ...],
    engine_configs: tuple[int, ...],
    t0_h: float,
    max_hours: float,
) -> set[Node]:
    """Nodes reachable from (stage=0, lane=0) within `max_hours`, choosing
    the fastest *feasible* (speed, engine-config) independently per edge —
    same per-edge minimisation as `time_optimal_route` (not just the single
    fastest nominal speed/config: that combination can itself be infeasible
    everywhere, e.g. an engine-overload speed, which would otherwise make
    this wrongly prune everything). This is a conservative
    over-approximation of what the full multi-objective search could still
    reach — it never prunes a node reachable via *some* available choice."""
    start: Node = (0, 0)
    best_arrival: dict[Node, float] = {start: t0_h}
    heap: list[tuple[float, Node]] = [(t0_h, start)]
    reachable: set[Node] = set()
    deadline_h = t0_h + max_hours

    while heap:
        t, node = heapq.heappop(heap)
        if t > best_arrival.get(node, float("inf")):
            continue
        reachable.add(node)
        stage, lane = node
        if stage + 1 >= lattice.n_stages:
            continue
        for next_lane in _neighbour_lanes(lattice, stage + 1, lane):
            p, q = lattice.point(stage, lane), lattice.point(stage + 1, next_lane)
            best_leg_duration = _best_feasible_duration_h(
                p, q, t, weather, geography, twin, speeds_kn, engine_configs
            )
            if best_leg_duration is None:
                continue
            arrival = t + best_leg_duration
            if arrival > deadline_h:
                continue
            next_node = (stage + 1, next_lane)
            if arrival < best_arrival.get(next_node, float("inf")):
                best_arrival[next_node] = arrival
                heapq.heappush(heap, (arrival, next_node))

    return reachable


def time_optimal_route(
    lattice: Lattice,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    speeds_kn: tuple[float, ...],
    engine_configs: tuple[int, ...],
    t0_h: float,
) -> tuple[tuple[LatLon, ...], float] | None:
    """Fastest feasible route end-to-end, choosing the best (speed,
    engine-config) independently per edge (a valid Dijkstra formulation —
    each edge's minimum-duration choice is a purely local decision).
    Test oracle only: ignores fuel/comfort/wear scoring entirely. Returns
    (track, duration_h), or None if the destination is unreachable."""
    start: Node = (0, 0)
    destination_stage = lattice.n_stages - 1
    best_arrival: dict[Node, float] = {start: t0_h}
    predecessor: dict[Node, Node] = {}
    heap: list[tuple[float, Node]] = [(t0_h, start)]

    while heap:
        t, node = heapq.heappop(heap)
        if t > best_arrival.get(node, float("inf")):
            continue
        stage, lane = node
        if stage == destination_stage:
            continue
        for next_lane in _neighbour_lanes(lattice, stage + 1, lane):
            p, q = lattice.point(stage, lane), lattice.point(stage + 1, next_lane)
            best_leg_duration = _best_feasible_duration_h(
                p, q, t, weather, geography, twin, speeds_kn, engine_configs
            )
            if best_leg_duration is None:
                continue
            next_node = (stage + 1, next_lane)
            arrival = t + best_leg_duration
            if arrival < best_arrival.get(next_node, float("inf")):
                best_arrival[next_node] = arrival
                predecessor[next_node] = node
                heapq.heappush(heap, (arrival, next_node))

    # the destination is the literal port (stage_centres[-1], lane 0) — not
    # "some lane near the last stage", which would let the route converge
    # to a point 20-30nm off the actual destination.
    best_dest: Node = (destination_stage, 0)
    if best_dest not in best_arrival:
        return None

    path = [best_dest]
    while path[-1] in predecessor:
        path.append(predecessor[path[-1]])
    path.reverse()

    track = tuple(lattice.point(stage, lane) for stage, lane in path)
    duration_h = best_arrival[best_dest] - t0_h
    return track, duration_h
