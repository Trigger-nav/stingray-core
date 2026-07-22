"""Route distillation (ticket S1): a bounded, provably-non-worsening
post-search pass that simplifies a candidate's lattice/corridor polyline
down to the deliberate waypoints a navigator would actually write down.
Runs once per candidate, strictly *after* the search has already picked
a track and a speed/engine-config per leg -- no new physics, no new
hard-constraint logic, no new cost model; every proposed shortcut is
re-verified through the exact same `core.legs.evaluate_leg` the search
itself trusts (the Bonifacio scattered-islet lesson, ticket 0.8, applied
at a different scale -- two waypoints can each read as clear water while
the direct line between them doesn't).

Deliberately has **no** dependency on `core.optimiser` (avoiding a
circular import: `core.optimiser.optimise()` is this module's only
caller) -- `leg_targets`/`alteration_list` are rebuilt by the caller via
the existing, unchanged `core.optimiser._build_leg_targets`/
`_build_alteration_list`, not duplicated here. `Candidate.side` is
likewise entirely untouched by this module -- callers must capture it
from the *original*, pre-distillation track separately.

Full design, the correctness reasoning behind the pass-level backstop
(not a per-merge whole-remaining-track re-check), and the same-speed
removal precondition: `docs/plans/ticket-S1.md`.
"""

from __future__ import annotations

from core.geography import Geography
from core.legs import LegResult, evaluate_leg
from core.twin import VesselTwin
from core.units import LatLon, distance_m, m_to_nm
from core.weather import WeatherField
from core.weights import Weights

# A generous safety cap, never expected to bind: each pass that returns a
# change removes at least one waypoint (docs/plans/ticket-S1.md Sec 1's
# own termination argument), so passes are already bounded by the
# original waypoint count ("tens", per that section) -- this is
# defensive-only, not a tuned value.
MAX_PASSES = 50


def _leg_cost_eur(leg: LegResult, weights: Weights) -> float:
    """The exact weighted-score formula the search itself costs every
    edge with (`core/optimiser.py`'s `_lattice_search`/`_dp_route`) --
    distillation must compare on the identical basis the search already
    used, not a different one."""
    return (
        weights.fuel_eur_per_kg * leg.fuel_kg
        + weights.time_eur_per_min * leg.duration_h * 60
        + weights.comfort_eur_per_index_point * leg.comfort
        + weights.wear_eur_per_index_point * leg.wear
    )


def _leg_hard_constraints_ok(leg: LegResult) -> bool:
    """The identical five-flag check every search call site already
    applies (`core/isochrone.py`'s `_best_feasible_duration_h`,
    `core/optimiser.py`'s `_lattice_search`/`_dp_route`) -- no new hard
    constraint, no relaxed one."""
    return (
        leg.navigable
        and leg.depth_ok
        and not leg.slam_event
        and not leg.overload
        and not leg.current_exceeds_stw
    )


def _evaluate_whole_track(
    track: list[LatLon],
    stw_ms_per_leg: list[float],
    engines_per_leg: list[int],
    t0_h: float,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    weights: Weights,
    depth_exempt_points: tuple[LatLon, ...],
    ref_lat_deg: float,
) -> dict:
    """One honest, end-to-end walk of `track` from `t0_h` -- the same
    accumulation `core.optimiser._lattice_route_result`/`_dp_route`
    already do post-search, just against however many waypoints `track`
    currently has. This *is* the "total-score comparison over the whole
    track, not local" the ticket's correctness point asks for -- called
    once per candidate simplified track, not per removal attempt (see
    `distill_track`'s docstring for why that's still correct and stays
    O(passes x n), not O(passes x n^2))."""
    t = t0_h
    fuel_kg = comfort = wear = max_hs = cost = 0.0
    duration_per_leg: list[float] = []
    for i in range(1, len(track)):
        leg = evaluate_leg(
            track[i - 1],
            track[i],
            stw_ms_per_leg[i - 1],
            t,
            weather,
            geography,
            twin,
            engines_per_leg[i - 1],
            depth_exempt_points=depth_exempt_points,
            ref_lat_deg=ref_lat_deg,
        )
        cost += _leg_cost_eur(leg, weights)
        fuel_kg += leg.fuel_kg
        comfort += leg.comfort
        wear += leg.wear
        max_hs = max(max_hs, leg.max_hs)
        duration_per_leg.append(leg.duration_h)
        t += leg.duration_h
    distance_nm = sum(
        m_to_nm(distance_m(track[i - 1], track[i], ref_lat_deg)) for i in range(1, len(track))
    )
    return {
        "track": tuple(track),
        "stw_ms_per_leg": list(stw_ms_per_leg),
        "engines_per_leg": list(engines_per_leg),
        "duration_per_leg": duration_per_leg,
        "duration_h": t - t0_h,
        "fuel_kg": fuel_kg,
        "comfort_index": comfort,
        "wear_index": wear,
        "max_hs_m": max_hs,
        "distance_nm": distance_nm,
        "cost": cost,
    }


def _sweep_once(
    track: list[LatLon],
    stw_ms_per_leg: list[float],
    engines_per_leg: list[int],
    t0_h: float,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    weights: Weights,
    depth_exempt_points: tuple[LatLon, ...],
    ref_lat_deg: float,
) -> tuple[list[LatLon], list[float], list[int]] | None:
    """One left-to-right sweep (`docs/plans/ticket-S1.md` Sec 1). Proposes
    a simplified `(track, stw_ms_per_leg, engines_per_leg)` -- the caller
    (`distill_track`) is responsible for the whole-track backstop
    evaluation/accept-reject decision; this function only proposes, never
    decides the pass is a net improvement. Returns `None` if no removal
    was found (the sweep converged -- nothing left to try).

    A candidate interior waypoint is only ever attempted for removal when
    the (already-decided) leg feeding into it and the *original* leg
    leaving it share the same `(stw_ms, active_engines)` -- a deliberate,
    conservative precondition (see the plan's Sec 1) that keeps this a
    pure geometry pass, never a re-optimisation: a genuine speed/engine
    change point is a real planning decision, not a quantisation
    artefact, and is never touched. `t` (elapsed time) is threaded
    forward correctly through every decision, so even a *rejected*
    merge's "keep" bookkeeping reflects every earlier accepted merge's
    real time-shift -- the reason this stays correct without needing to
    re-evaluate anything downstream of the current position within one
    sweep.

    Cost: at most 3 `evaluate_leg` calls per original interior waypoint
    (the pending leg alone, the next original leg alone, and the
    shortcut -- only when the same-speed precondition holds; otherwise
    just 1), i.e. O(n) per sweep, not O(n^2)."""
    n = len(track)
    kept: list[LatLon] = [track[0]]
    kept_stw: list[float] = []
    kept_engines: list[int] = []
    t = t0_h
    removed_any = False

    pending_idx = 1  # track[] index the still-uncommitted incoming leg currently targets
    pending_stw = stw_ms_per_leg[0]
    pending_engines = engines_per_leg[0]

    while pending_idx < n - 1:
        next_idx = pending_idx + 1
        pending_leg = evaluate_leg(
            kept[-1],
            track[pending_idx],
            pending_stw,
            t,
            weather,
            geography,
            twin,
            pending_engines,
            depth_exempt_points=depth_exempt_points,
            ref_lat_deg=ref_lat_deg,
        )
        same_speed = (pending_stw, pending_engines) == (
            stw_ms_per_leg[pending_idx],
            engines_per_leg[pending_idx],
        )
        if same_speed:
            next_leg = evaluate_leg(
                track[pending_idx],
                track[next_idx],
                pending_stw,
                t + pending_leg.duration_h,
                weather,
                geography,
                twin,
                pending_engines,
                depth_exempt_points=depth_exempt_points,
                ref_lat_deg=ref_lat_deg,
            )
            shortcut_leg = evaluate_leg(
                kept[-1],
                track[next_idx],
                pending_stw,
                t,
                weather,
                geography,
                twin,
                pending_engines,
                depth_exempt_points=depth_exempt_points,
                ref_lat_deg=ref_lat_deg,
            )
            if _leg_hard_constraints_ok(shortcut_leg) and _leg_cost_eur(
                shortcut_leg, weights
            ) <= _leg_cost_eur(pending_leg, weights) + _leg_cost_eur(next_leg, weights):
                # Accept: extend the pending (uncommitted) leg past
                # track[pending_idx] without committing it -- skip it
                # entirely. Do not advance `t` -- the pending leg's own
                # duration is still undetermined until it's finally
                # committed (either at the next rejected attempt, or at
                # the destination below).
                removed_any = True
                pending_idx = next_idx
                continue
        # Reject (or a genuine speed/engine change point): commit the
        # pending leg as a real, kept waypoint.
        kept.append(track[pending_idx])
        kept_stw.append(pending_stw)
        kept_engines.append(pending_engines)
        t += pending_leg.duration_h
        pending_stw, pending_engines = stw_ms_per_leg[pending_idx], engines_per_leg[pending_idx]
        pending_idx = next_idx

    # The final pending leg always terminates at the fixed destination --
    # never itself a removal candidate (track[-1] never moves).
    kept.append(track[-1])
    kept_stw.append(pending_stw)
    kept_engines.append(pending_engines)

    if not removed_any:
        return None
    return kept, kept_stw, kept_engines


def distill_track(
    track: tuple[LatLon, ...],
    stw_ms_per_leg: list[float],
    engines_per_leg: list[int],
    t0_h: float,
    weather: WeatherField,
    geography: Geography,
    twin: VesselTwin,
    weights: Weights,
    depth_exempt_points: tuple[LatLon, ...],
    ref_lat_deg: float,
) -> dict:
    """Repeats `_sweep_once` (always in the current track's own
    left-to-right order -- deterministic, same request always returns
    the same result) until a sweep proposes nothing, or a sweep's result
    -- evaluated honestly end-to-end via `_evaluate_whole_track`, the
    actual whole-track backstop -- is not better than the track *before*
    that sweep, in which case that sweep's changes are discarded and
    distillation stops (`docs/plans/ticket-S1.md` Sec 1's pass-level
    accept/reject judgment call, signed off in review).

    Returns a dict: `track`, `stw_ms_per_leg`, `engines_per_leg`,
    `duration_per_leg`, `duration_h`, `fuel_kg`, `comfort_index`,
    `wear_index`, `max_hs_m`, `distance_nm`, `cost` -- everything the
    caller (`core.optimiser.optimise`) needs to rebuild `leg_targets`/
    `alteration_list` (via the existing, unmodified
    `_build_leg_targets`/`_build_alteration_list`) and a final
    `Candidate`. Never returns or touches a `side` value -- see this
    module's own docstring."""
    current = _evaluate_whole_track(
        list(track),
        list(stw_ms_per_leg),
        list(engines_per_leg),
        t0_h,
        weather,
        geography,
        twin,
        weights,
        depth_exempt_points,
        ref_lat_deg,
    )

    for _ in range(MAX_PASSES):
        swept = _sweep_once(
            list(current["track"]),
            current["stw_ms_per_leg"],
            current["engines_per_leg"],
            t0_h,
            weather,
            geography,
            twin,
            weights,
            depth_exempt_points,
            ref_lat_deg,
        )
        if swept is None:
            break  # converged: this sweep found nothing left to remove

        new_track, new_stw, new_engines = swept
        candidate = _evaluate_whole_track(
            new_track,
            new_stw,
            new_engines,
            t0_h,
            weather,
            geography,
            twin,
            weights,
            depth_exempt_points,
            ref_lat_deg,
        )
        if candidate["cost"] > current["cost"] + 1e-9:
            # Pass-level backstop: this sweep's proposed changes, taken
            # together, would make the whole track worse (the rare
            # weather/current-timing-interaction case the plan's Sec 1
            # names) -- discard them and stop, rather than trying to
            # isolate which individual merge caused it.
            break
        current = candidate

    return current
