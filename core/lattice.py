"""Open lattice around the rhumb line between two ports (ticket 0.4) —
replaces the two hand-drawn, name-fixed corridors (`core/corridors.py`,
still used as the fallback/fast path) with a single lattice wide enough
that routing around Corsica (Bonifacio strait vs east-about) *emerges*
from the search against real geography, rather than being told there are
exactly two named options.

Geometry matches `core/corridors.py`'s approach exactly (straight lat/lon
interpolation + perpendicular offset in an equirectangular approximation
anchored at `REF_LAT_DEG`) — appropriate at this ~200nm passage scale, and
consistent with the rest of the codebase.

**Adaptive refinement (ticket 0.8).** `cross_track_step_nm` is per-stage,
not a single scalar — most of the lattice stays at the coarse default
(cheap), but a stage whose outgoing edges are found to be poorly
navigable (a real, scattered-hazard-field effect found empirically at
Bonifacio — see `docs/plans/ticket-0.8.md`) gets refined to a finer local
step, bounded to a lateral window so lane count doesn't blow up lattice-
wide. `LANE_TURN_RATE_NM` (a physical lateral-distance turn allowance,
not a fixed lane-*index* count) is what makes a coarse/fine stage
boundary behave sanely — see `Lattice.turn_range`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from core.geography import OPERATING_AREA_BBOX, Geography
from core.legs import _navigable_along_leg
from core.units import EARTH_NM_PER_DEG_LAT, LatLon, distance_m, interpolate_point, m_to_nm

REF_LAT_DEG = 42.3

DEFAULT_ALONG_TRACK_STEP_NM = 6.0
DEFAULT_CROSS_TRACK_STEP_NM = 5.0
DEFAULT_CROSS_TRACK_HALF_WIDTH_NM = 80.0
BBOX_MARGIN_NM = 3.0

# Max lateral change per along-track stage, in nautical miles (not a fixed
# lane-index count — see module docstring; this is a lattice-resolution
# parameter bounding how far apart two consecutive waypoints' lateral
# offsets can be, not a vessel kinematic limit — the vessel steers each
# leg at its own heading regardless, same as any course change between
# waypoints). Originally ported from the prior fixed-index-count turn
# rate (+-2 lanes at the 5nm default step ~= 10nm) when converting to
# distance for ticket 0.8's per-stage resolution. 15nm, not 10nm: found
# empirically that 10nm was exactly too tight to let a route pushed west
# through the Bonifacio reef field curve back to the destination approach
# in the few remaining stages before Porto Cervo (threshold was between
# 11-12nm; 15nm gives real margin, not a value sitting right on the edge
# of a real route becoming infeasible again) — see
# docs/plans/ticket-0.8.md.
LANE_TURN_RATE_NM = 15.0

# Adaptive refinement tuning (ticket 0.8). 0.75, not a round 0.7 or 0.8 --
# found empirically that Bonifacio's real worst coarse-resolution stage
# sits at exactly 0.700 (see docs/plans/ticket-0.8.md), which a `< 0.7`
# threshold misses entirely by sitting right on the boundary; 0.75 gives
# it real margin without being so aggressive it refines stages that are
# only trivially imperfect (the 0.95-0.99 stages nearby, real GSHHG
# fidelity noise rather than an actual constraint).
DEFAULT_MIN_NAVIGABLE_EDGE_FRACTION = 0.75
DEFAULT_MAX_REFINEMENT_PASSES = 3
DEFAULT_MIN_REFINEMENT_STEP_NM = 0.5
REFINEMENT_STEP_FACTOR = 4.0

# Ticket L1: self-scaling lattice geometry. `DEFAULT_CROSS_TRACK_STEP_NM`
# (5.0nm) and `LANE_TURN_RATE_NM` (15.0nm) above are both real, but
# absolute-distance Bonifacio-era tuning against the Med's own ~180nm
# Antibes<->Porto Cervo passage -- neither has a principled reason to be
# right for a passage of very different length (ticket R1 §1 named this
# risk explicitly; `docs/plans/ticket-C1.md`'s UK dog-leg diagnostic and
# `docs/plans/ticket-L1.md`'s own empirical follow-up confirmed it: a
# fixed 5nm lane grid on a 36.7nm passage is far too coarse, forcing a
# 40%-longer detour to express a lateral position the grid can only round
# to the nearest 5nm). `build_lattice`'s `cross_track_step_nm`/
# `lane_turn_rate_nm` parameters derive from these two ratios (below) when
# the caller passes `None` (the new default) instead of an explicit
# value -- both ratios are computed *from* the Med's own real numbers, so
# applying either formula back to the Med's own ~179.55nm passage
# reproduces `DEFAULT_CROSS_TRACK_STEP_NM`/`LANE_TURN_RATE_NM` exactly,
# not approximately (`docs/plans/ticket-L1.md` §2a/§2b has the full
# algebra and the real sensitivity-sweep data these ratios are grounded
# in -- `lane_turn_rate_nm` in particular was found to have *zero*
# measured effect on the UK dog-leg; it's self-scaled here on principle,
# not because it was the mechanism).

# Fraction of straight-line passage length that reproduces today's
# DEFAULT_CROSS_TRACK_STEP_NM on the Med's own real ~179.5508nm passage:
# 5.0 / 179.5507750858526 = 0.0278473 (verified directly, not
# hand-computed). A short passage gets proportionally finer lane spacing;
# DEFAULT_CROSS_TRACK_STEP_NM remains the ceiling (a passage at or longer
# than Med-scale is unaffected). Deliberately rounded slightly *up* from
# that exact ratio (0.027848, not 0.027847) so that `total_nm *
# CROSS_TRACK_STEP_FRACTION` for the Med's own real passage evaluates to
# a hair above DEFAULT_CROSS_TRACK_STEP_NM, not a hair below -- verified
# directly: 0.027847 lands at 4.99995 (a hair *under* the ceiling, which
# would silently produce a non-default value the ceiling clamp was
# supposed to prevent), 0.027848 lands at 5.00013. `build_lattice`'s
# `min(computed, DEFAULT_CROSS_TRACK_STEP_NM)` ceiling clamp then returns
# the literal `DEFAULT_CROSS_TRACK_STEP_NM` value exactly regardless of
# which side of 5.0 the raw product lands on, but only the "hair above"
# case is the intended, checked one.
CROSS_TRACK_STEP_FRACTION = 0.027848

# Floor for the derived cross_track_step_nm, so a pathologically short
# future passage (a harbour-to-anchorage hop) doesn't get an absurdly
# fine, expensive lane grid. Reuses DEFAULT_MIN_REFINEMENT_STEP_NM's own
# already-precedented 0.5nm value rather than inventing a new one.
MIN_CROSS_TRACK_STEP_NM = 0.5

# Maximum per-stage course-deviation angle that reproduces today's
# LANE_TURN_RATE_NM at the Med's own DEFAULT_ALONG_TRACK_STEP_NM stage
# length: atan(15.0 / 6.0) = 68.1986 degrees -- a deliberately generous
# angle (LANE_TURN_RATE_NM's own comment already frames it as "not a
# vessel kinematic limit"), expressed as an angle rather than a fixed nm
# value because an angle is stage-length-invariant by construction; a
# fixed nm value only means what it was tuned to mean at one specific
# stage length.
MAX_TURN_ANGLE_DEG = 68.1986


@dataclass(frozen=True)
class RefinementDiagnostic:
    """A stage whose outgoing edges were still below
    `min_navigable_edge_fraction` after refinement hit its pass/floor
    limit — visible, not silently accepted (amendment 2)."""

    stage: int
    final_step_nm: float
    navigable_edge_fraction: float


@dataclass(frozen=True)
class Lattice:
    origin: LatLon
    destination: LatLon
    stage_centres: tuple[LatLon, ...]
    # per-stage symmetric lane range [-max_lane_per_stage[i], +max_lane_per_stage[i]] —
    # the ports themselves sit near OPERATING_AREA_BBOX's corners (little room
    # before the bbox edge), so width must taper near the endpoints and can
    # only open up mid-passage, same shape core/corridors.py's Corridor
    # objects already hand-tuned per corridor; here it falls out of the bbox
    # geometry automatically instead.
    max_lane_per_stage: tuple[int, ...]
    cross_track_step_nm: tuple[float, ...]
    refinement_diagnostics: tuple[RefinementDiagnostic, ...] = field(default_factory=tuple)
    # Ticket R1: carried on the built Lattice itself (rather than threaded
    # as a parameter through every isochrone.py/optimiser.py call site)
    # so .point()/.turn_range() stay self-contained -- the pack that built
    # this lattice is what determines these, and every caller already has
    # the Lattice object in hand.
    ref_lat_deg: float = REF_LAT_DEG
    lane_turn_rate_nm: float = LANE_TURN_RATE_NM

    @property
    def n_stages(self) -> int:
        return len(self.stage_centres)

    def point(self, stage: int, lane: int) -> LatLon:
        return _offset_point(
            self.stage_centres, stage, lane, self.cross_track_step_nm[stage], self.ref_lat_deg
        )

    def turn_range(self, from_stage: int, from_lane: int) -> range:
        """Lane-index range reachable at `from_stage + 1` from `from_lane`,
        given the fixed *physical* lateral turn allowance
        `self.lane_turn_rate_nm`. The one place this computation happens —
        `core/isochrone.py` and `core/optimiser.py` both call this rather
        than each re-deriving it (they're meant to be identical, same
        precedent as `core/legs.py`'s shared hard-constraint semantics).
        See `_turn_range` for why it's physical-position-based, not
        lane-index arithmetic."""
        return _turn_range(
            self.cross_track_step_nm,
            self.max_lane_per_stage,
            from_stage,
            from_lane,
            self.lane_turn_rate_nm,
        )


def _turn_range(
    steps: tuple[float, ...] | list[float],
    max_lane_per_stage: tuple[int, ...] | list[int],
    from_stage: int,
    from_lane: int,
    lane_turn_rate_nm: float = LANE_TURN_RATE_NM,
) -> range:
    """Lane-index range at `from_stage + 1` within `LANE_TURN_RATE_NM` of
    `from_lane`, computed in physical lateral position (`lane * that
    stage's own step`) rather than lane-index arithmetic — index deltas
    only mean the same physical distance when both stages share one step.
    An earlier version used the *finer* of the two steps to compute an
    index-count range and applied that same *index* range at the
    *coarser* stage — which silently grants a much larger-than-intended
    physical turn at the coarse end (found empirically: it let an
    east-side search wander through an adaptively-refined fine stage and
    back out at a coarse neighbour with an effectively unbounded turn,
    corrupting east-side routing that used to work fine before adaptive
    refinement existed at all — see docs/plans/ticket-0.8.md). Converting
    through physical position is correct regardless of which side of the
    boundary is finer. Free function (not a `Lattice` method) so
    `build_lattice`'s refinement pass can call it on the working
    (not-yet-a-`Lattice`) `steps`/`max_lane_per_stage` lists it's still
    mutating — `Lattice.turn_range` is a thin wrapper over this once the
    lattice is built."""
    to_stage = from_stage + 1
    from_step = steps[from_stage]
    to_step = steps[to_stage]
    from_physical_nm = from_lane * from_step
    next_max_lane = max_lane_per_stage[to_stage]
    lo = max(-next_max_lane, math.ceil((from_physical_nm - lane_turn_rate_nm) / to_step))
    hi = min(next_max_lane, math.floor((from_physical_nm + lane_turn_rate_nm) / to_step))
    return range(lo, hi + 1)


def _offset_point(
    centres: tuple[LatLon, ...], i: int, k: int, step_nm: float, ref_lat_deg: float = REF_LAT_DEG
) -> LatLon:
    """Perpendicular offset by `k` lanes of `step_nm` each — same
    construction as `core/corridors.py`'s `offset_point`, generalised to
    arbitrary lane spacing rather than a fixed corridor's `offset_nm * k`."""
    pts = centres
    a = pts[max(0, i - 1)]
    b = pts[min(len(pts) - 1, i + 1)]
    klon = math.cos(math.radians(ref_lat_deg))
    dy = b.lat_deg - a.lat_deg
    dx = (b.lon_deg - a.lon_deg) * klon
    length = math.hypot(dx, dy) or 1e-9
    px, py = -dy / length, dx / length
    offset_nm = step_nm * k
    return LatLon(
        pts[i].lat_deg + (py * offset_nm) / EARTH_NM_PER_DEG_LAT,
        pts[i].lon_deg + (px * offset_nm) / (EARTH_NM_PER_DEG_LAT * klon),
    )


def _within_bbox_with_margin(
    lat_deg: float,
    lon_deg: float,
    margin_deg: float,
    bbox: tuple[float, float, float, float] = OPERATING_AREA_BBOX,
) -> bool:
    lon_min, lat_min, lon_max, lat_max = bbox
    return (
        lat_min + margin_deg <= lat_deg <= lat_max - margin_deg
        and lon_min + margin_deg <= lon_deg <= lon_max - margin_deg
    )


def _stage_max_lane(
    stage_centres: tuple[LatLon, ...],
    stage: int,
    step_nm: float,
    cross_track_half_width_nm: float,
    margin_deg: float,
    bbox: tuple[float, float, float, float] = OPERATING_AREA_BBOX,
    ref_lat_deg: float = REF_LAT_DEG,
) -> int:
    requested_max_lane = max(1, round(cross_track_half_width_nm / step_nm))
    stage_max = 0
    for lane in range(1, requested_max_lane + 1):
        p_pos = _offset_point(stage_centres, stage, lane, step_nm, ref_lat_deg)
        p_neg = _offset_point(stage_centres, stage, -lane, step_nm, ref_lat_deg)
        if not (
            _within_bbox_with_margin(p_pos.lat_deg, p_pos.lon_deg, margin_deg, bbox)
            and _within_bbox_with_margin(p_neg.lat_deg, p_neg.lon_deg, margin_deg, bbox)
        ):
            break
        stage_max = lane
    return stage_max


def _outgoing_edge_navigable_fraction(
    stage_centres: tuple[LatLon, ...],
    steps: list[float],
    max_lane_per_stage: list[int],
    stage: int,
    geography: Geography,
    ref_lat_deg: float = REF_LAT_DEG,
    lane_turn_rate_nm: float = LANE_TURN_RATE_NM,
) -> float:
    """Fraction of (lane at `stage`) x (turn-reachable lane at `stage+1`)
    pairs that are actually leg-navigable — the same edge-level signal
    this ticket's investigation checked by hand (point navigability alone
    understates a scattered-islet field: two endpoints can each be clear
    water with real hazards on the straight line between them).

    Computed *separately* for the west half (lane <= 0) and east half
    (lane >= 0) of the stage, returning the worse of the two — found
    empirically to matter: Bonifacio's degradation is asymmetric (the
    west half is materially worse than the east), and averaging over the
    whole stage diluted the signal below any reasonable threshold,
    triggering no refinement at all where it was actually needed."""

    def half_fraction(lanes: range) -> float:
        total = 0
        navigable = 0
        for lane in lanes:
            p = _offset_point(stage_centres, stage, lane, steps[stage], ref_lat_deg)
            if not geography.is_navigable(p.lat_deg, p.lon_deg):
                continue
            for next_lane in _turn_range(
                steps, max_lane_per_stage, stage, lane, lane_turn_rate_nm
            ):
                q = _offset_point(
                    stage_centres, stage + 1, next_lane, steps[stage + 1], ref_lat_deg
                )
                if not geography.is_navigable(q.lat_deg, q.lon_deg):
                    continue
                total += 1
                if _navigable_along_leg(p, q, geography, ref_lat_deg):
                    navigable += 1
        # No navigable starting lane on this half at all -> refinement
        # can't help (finer sampling of dry land is still dry land); that
        # is core/optimiser.py's side-diversity fix's job, not this one's
        # — don't force a refinement pass over it.
        return 1.0 if total == 0 else navigable / total

    west = half_fraction(range(-max_lane_per_stage[stage], 1))
    east = half_fraction(range(0, max_lane_per_stage[stage] + 1))
    return min(west, east)


def build_lattice(
    origin: LatLon,
    destination: LatLon,
    *,
    geography: Geography | None = None,
    adaptive_refinement: bool = True,
    along_track_step_nm: float = DEFAULT_ALONG_TRACK_STEP_NM,
    cross_track_step_nm: float | None = None,
    cross_track_half_width_nm: float = DEFAULT_CROSS_TRACK_HALF_WIDTH_NM,
    min_navigable_edge_fraction: float = DEFAULT_MIN_NAVIGABLE_EDGE_FRACTION,
    max_refinement_passes: int = DEFAULT_MAX_REFINEMENT_PASSES,
    min_refinement_step_nm: float = DEFAULT_MIN_REFINEMENT_STEP_NM,
    ref_lat_deg: float = REF_LAT_DEG,
    bbox: tuple[float, float, float, float] = OPERATING_AREA_BBOX,
    lane_turn_rate_nm: float | None = None,
) -> Lattice:
    """Build the open lattice, clipping the requested half-width down to
    whatever actually stays inside `OPERATING_AREA_BBOX` (with margin) at
    every stage — the concrete fix for the 0.3 "operating-area bounds"
    follow-up: the lattice itself never asks `RealGeography` for a point
    outside the area real data covers, so `OutOfOperatingAreaError` is a
    pure defensive backstop, not something normal operation should trip.

    Adaptive refinement (ticket 0.8) runs when `geography` is given and
    `adaptive_refinement` is true (both are opt-outable — tests that don't
    care about real-geography fidelity, or that construct a lattice before
    any `Geography` is available, get the pre-0.8 uniform-step behaviour
    by just not passing `geography`). Each stage's *own* outgoing edge
    (to `stage + 1`) is probed; below `min_navigable_edge_fraction`, that
    stage's step is refined (divided by `REFINEMENT_STEP_FACTOR`) and
    re-probed, repeating up to `max_refinement_passes` or until
    `min_refinement_step_nm` is reached, whichever binds first. A stage
    still below threshold at that limit is recorded in
    `Lattice.refinement_diagnostics`, not silently accepted (amendment 2)
    — the search itself already handles a genuinely-impassable region by
    finding no path there, same as it always has for any hazard.

    Ticket L1: `cross_track_step_nm`/`lane_turn_rate_nm` default to `None`
    ("derive from passage geometry") rather than a fixed constant — the
    module docstring above `CROSS_TRACK_STEP_FRACTION`/`MAX_TURN_ANGLE_DEG`
    has the full derivation and the empirical grounding
    (`docs/plans/ticket-L1.md`). An explicit value (as every pre-L1 caller
    already passes, and as any future pack needing genuine Bonifacio-style
    empirical tuning still can) always wins — this is additive, not a
    behaviour change for any caller that already specifies these.
    """
    total_nm = m_to_nm(distance_m(origin, destination, ref_lat_deg))
    if cross_track_step_nm is None:
        cross_track_step_nm = max(
            MIN_CROSS_TRACK_STEP_NM,
            min(total_nm * CROSS_TRACK_STEP_FRACTION, DEFAULT_CROSS_TRACK_STEP_NM),
        )
    if lane_turn_rate_nm is None:
        # Bit-exact at the Med's own stage length, not merely close --
        # avoids a tan(radians(...)) round-trip's inherent floating-point
        # noise (~1e-5nm) for the one case (along_track_step_nm unchanged)
        # every shipped pack actually hits today.
        lane_turn_rate_nm = (
            LANE_TURN_RATE_NM
            if along_track_step_nm == DEFAULT_ALONG_TRACK_STEP_NM
            else along_track_step_nm * math.tan(math.radians(MAX_TURN_ANGLE_DEG))
        )
    n_stages = max(2, round(total_nm / along_track_step_nm) + 1)
    stage_centres = tuple(
        interpolate_point(origin, destination, i / (n_stages - 1)) for i in range(n_stages)
    )

    margin_deg = BBOX_MARGIN_NM / EARTH_NM_PER_DEG_LAT
    steps = [cross_track_step_nm] * n_stages
    max_lane_per_stage = [
        _stage_max_lane(
            stage_centres, i, steps[i], cross_track_half_width_nm, margin_deg, bbox, ref_lat_deg
        )
        for i in range(n_stages)
    ]

    diagnostics: list[RefinementDiagnostic] = []
    if geography is not None and adaptive_refinement:
        for stage in range(n_stages - 1):
            passes = 0
            frac = _outgoing_edge_navigable_fraction(
                stage_centres,
                steps,
                max_lane_per_stage,
                stage,
                geography,
                ref_lat_deg,
                lane_turn_rate_nm,
            )
            while (
                frac < min_navigable_edge_fraction
                and passes < max_refinement_passes
                and steps[stage] > min_refinement_step_nm
            ):
                steps[stage] = max(min_refinement_step_nm, steps[stage] / REFINEMENT_STEP_FACTOR)
                max_lane_per_stage[stage] = _stage_max_lane(
                    stage_centres,
                    stage,
                    steps[stage],
                    cross_track_half_width_nm,
                    margin_deg,
                    bbox,
                    ref_lat_deg,
                )
                frac = _outgoing_edge_navigable_fraction(
                    stage_centres,
                    steps,
                    max_lane_per_stage,
                    stage,
                    geography,
                    ref_lat_deg,
                    lane_turn_rate_nm,
                )
                passes += 1
            if frac < min_navigable_edge_fraction:
                diagnostics.append(
                    RefinementDiagnostic(
                        stage=stage, final_step_nm=steps[stage], navigable_edge_fraction=frac
                    )
                )

    return Lattice(
        origin=origin,
        destination=destination,
        stage_centres=stage_centres,
        max_lane_per_stage=tuple(max_lane_per_stage),
        cross_track_step_nm=tuple(steps),
        refinement_diagnostics=tuple(diagnostics),
        ref_lat_deg=ref_lat_deg,
        lane_turn_rate_nm=lane_turn_rate_nm,
    )
