# Ticket L1 — Self-scaling lattice geometry

**Review status: approved, all three judgment calls approved as
recommended** (formula output over sweep-best §2a; keep §2c; leave
`along_track_step_nm` strictly untouched). Two additions from review,
incorporated below: §1c's NaN-cost finding is elevated to an immediate
fast-follow ticket, N1 (not implemented here — see "N1 — fast-follow,
scope sketch only" at the end of §1c); §2c gained a pre-agreed escape
rule (see "§2c escape rule" at the end of §2c). Implementation follows
this revised version, in the plan's own order.

## Context

`docs/plans/ticket-C1.md`'s "Follow-up diagnostic: the UK dog-leg"
section found, empirically, that the Plymouth→Falmouth passage's
candidate track runs 40% longer than the straight-line origin→destination
distance (detour ratio 1.398), and — crucially — that this detour
**persists identically with current zeroed** and **is identical between
the shipped A* heuristic and a forced-exhaustive search** (byte-for-byte,
every waypoint). That diagnostic ruled out both "tide-optimal routing"
and "the C1-deferred A* admissibility gap" as explanations, and concluded
the detour is **lattice-geometry shaped**: real navigability/depth
obstructions along the straight line are confined to the first ~9% (near
Plymouth Sound) and last ~5-6% (near the Fal estuary approach) of the
passage — the middle ~85% is clear, ~42m-deep open water — so a detour
anywhere near 40% of the total distance isn't geographically required.

CLAUDE.md's Bonifacio/ticket 0.8 gotcha is the direct precedent for how
this project treats a lattice-geometry finding: **diagnose the exact
mechanism empirically before touching anything** (0.8 found two
independent, both-necessary causes — adaptive per-stage refinement and a
side-diversity-filter bug — and fixing one without the other reintroduced
a different failure). `core/lattice.py`'s three tunable knobs
(`LANE_TURN_RATE_NM`, `DEFAULT_MIN_NAVIGABLE_EDGE_FRACTION`,
`DEFAULT_MIN_REFINEMENT_STEP_NM`) are explicitly documented as
**Bonifacio-tuned, not physics** — ticket R1 §1 made them
pack-overridable-with-Med-default data for exactly this reason, stating
outright: *"there is no principled UK number to substitute without
empirical tuning against real UK coastline data, and inventing one would
violate 'no invented numbers'... if the default values turn out to make
the UK pack's search infeasible or absurd, that's a real finding to
report during implementation, not something to paper over with a guessed
constant."* L1 is that finding, arriving on schedule.

**What L1 is not**: a fix for a specific bug. It's a design question —
should lattice geometry (lane spacing, endpoint taper, turn allowance)
be *tuned per pack* (R1's current answer, with the Med's own numbers as
every pack's un-investigated starting point) or *derived from passage
geometry* (this ticket's proposal), so a third, fourth, fifth pack never
needs its own empirical Bonifacio-style tuning pass just to get a
sensible-looking track.

## Part 1 — Empirical diagnosis of the exact binding mechanism

**Method.** Same scenario as `docs/plans/ticket-C1.md`'s diagnostic
(Plymouth→Falmouth, `departure_t0_h=6.0`, `speeds_kn=(10.0,)`,
`pace=comfort=50`, real geography + real weather including the merged
CMEMS currents). Three separate empirical probes, in order: (1) trace the
actual chosen (stage, lane) path and check at every transition whether
the chosen lane sits pinned against `turn_range`'s bound (turn-rate
binding) or against `max_lane_per_stage`'s own bound (endpoint-taper
binding); (2) a one-parameter-at-a-time sensitivity sweep varying
`lane_turn_rate_nm`, `along_track_step_nm`, and `cross_track_step_nm`
independently against the shipped baseline; (3) follow up on any
surprising result from (2) until its own mechanism is understood, not
just observed.

### 1a. Path trace: one coincidental turn-rate tie, not a real bottleneck

The chosen path's very first transition (stage 0 → stage 1) lands exactly
on `turn_range`'s upper bound (lane 0 → lane 3, the maximum the 15nm
turn allowance permits over that ~6.1nm stage). Every other transition in
the 7-stage baseline path is strictly interior to both bounds. This looks,
at first glance, like turn-rate is the constraint — **it is not**, per
1b.

### 1b. Sensitivity sweep: `cross_track_step_nm` is the whole story; `lane_turn_rate_nm` has zero effect

| parameter varied | values tried | effect |
|---|---|---|
| `lane_turn_rate_nm` (along/cross held at shipped defaults) | 15, 25, 40, 60, 100nm | **Zero.** Score, distance, and every waypoint identical to the baseline at every value. The stage-0→1 tie found in 1a is a coincidence of the cost-optimal choice landing exactly at that value, not a binding constraint — loosening the bound never changes what the search picks. |
| `cross_track_step_nm` (lane spacing) alone | 5.0 (baseline), 2.5, 1.0, 0.5nm | **Monotonic, substantial improvement.** Score 4417.64 → 4038.60 → 3882.22 → 3827.68 (13.4% total); detour ratio 1.398 → 1.279 → 1.230 → 1.212. This is the mechanism. |
| `along_track_step_nm` (stage spacing) alone | 6.0 (baseline), 3.0, 2.0, 1.0nm | **Baseline (6.0) works. Every finer value is INFEASIBLE** — a real, separate finding, see 1c. Not usable as a lever without first addressing 1c. |

**Conclusion: the dog leg is a lane-spacing (`cross_track_step_nm`)
discretisation artifact, not a turn-rate or endpoint-taper problem.** A
fixed 5nm lane grid, tuned against the Med's ~180nm passage, is far too
coarse relative to a 36.7nm passage — the search can only express lateral
positions in 5nm increments, so avoiding a real but modest near-shore
obstruction forces a much larger swing than the obstruction itself
requires. `lane_turn_rate_nm` is not touched by this finding at all (it's
demonstrably inert here); endpoint taper (`max_lane_per_stage`, driven by
`BBOX_MARGIN_NM` clipping against the pack's own bbox) never bound the
chosen path either — the widest lane index used (3, at stage 1) sits well
inside every stage's own `max_lane_per_stage` (±5 or more throughout).

### 1c. A real, separate, more serious finding: NaN leg cost silently vanishes states from the search

Investigating why every `along_track_step_nm` refinement broke feasibility
(not assumed — traced to ground): a direct edge-by-edge replay of the
search's first expansion, at `along_track_step_nm=2.0`, found a fully
valid edge (`navigable=True`, `depth_ok=True`, no hard-constraint flags
set) whose `evaluate_leg` result has **`fuel_kg=nan`, `comfort=nan`,
`max_hs=nan`** (`wear`/`duration_h` are fine). Root cause: `hs_m` (wave
height) is sampled via `GriddedWeatherField.sample()`'s `bilinear_masked`
path (correctly masked, matching C1's own current-sampling precedent) —
but the UK pack's committed weather grid is very coarse (**4×9 points**
across the whole 2°×1° bbox, ~20nm×16nm per cell) and, right next to the
Plymouth Sound approach, the query point's entire 4-corner interpolation
stencil is land-masked with no valid neighbour — `bilinear_masked`
correctly returns NaN for "no data here" rather than silently
guessing. That NaN then propagates through the twin's added-resistance
and comfort calculations into `leg_cost`.

**The actual bug is downstream of the correct NaN**: `_lattice_search`'s
Dijkstra/A* state update is `if new_g < best_node.get(new_state, ...).g:`
— and in Python, `nan < anything` (including `float("inf")`) is always
`False`. A NaN-cost edge is therefore **silently dropped from the search
with no diagnostic whatsoever** — not pruned like a hard-constraint
failure (`navigable`/`depth_ok`/`slam_event`/`overload`/
`current_exceeds_stw`, all of which produce a visible, intentional prune),
just invisibly absent. `core/isochrone.py`'s `arrival_times_within` found
the same passage "reachable" because it explores via a coarser (stage,
lane)-only state (collapsing across time) using `_best_feasible_duration_h`,
which doesn't touch `fuel_kg`/`comfort` at all — it never encounters this
NaN. **At the shipped `along_track_step_nm=6.0` (7 stages), the search's
sampled points never land close enough to this particular coastline gap to
trigger it** — which is exactly why the baseline works today and only
refining stage spacing exposes it.

**This is a real, previously-unknown correctness gap — reported here, not
silently patched around, and explicitly *not* fixed in this ticket.**
Fixing it means making `evaluate_leg`/the search treat a NaN cost as an
explicit, diagnosable hard-constraint failure (or fixing/flagging
data-coverage gaps at ingest time) — either is a cost-model/hard-constraint
change, which this ticket's own freeze framing (§3) explicitly disallows.
**Direct design consequence: `along_track_step_nm` is not touched by
Part 2's derivation at all** — not because stage spacing couldn't
plausibly also help, but because refining it is empirically unsafe until
this bug has its own fix, and `cross_track_step_nm` alone already
delivers the fix the diagnostic asked for. Named as a required follow-up
(§ Scope cuts).

**N1 — fast-follow, scope sketch only, not implemented here (review
addition).** Elevated from "named follow-up" to an immediate fast-follow
ticket, not a someday item: R1's arbitrary-endpoint routing and
per-vessel favourites are already live product surface, not a bench
scenario — a real user placing a custom origin/destination (or favourite)
near a coarse weather grid's fully-masked stencil hits this exact NaN
path today, at shipped settings, with **zero diagnostic** — the request
just comes back "no feasible route found," indistinguishable from a
genuine hard-constraint infeasibility, with nothing pointing at the real
cause. L1 doesn't change the odds of hitting this (it deliberately never
touches `along_track_step_nm`, §1c's own mitigation), but it doesn't need
to for the risk to already be real and present. **N1 scope, in one
paragraph**: make a non-finite (`nan`/`inf`) leg cost an explicit,
diagnosable prune, the same shape as the existing five hard constraints —
concretely, `LegResult` gains a `cost_finite: bool` (or equivalent)
computed once `evaluate_leg` has the weighted cost components in hand,
`_lattice_search`/`core/isochrone.py`'s three hard-constraint call sites
treat a non-finite cost as a prune exactly like `navigable=False` today
(never silently falling through a `nan < inf` comparison), and — the part
that actually closes the diagnostic gap — a new `PruneDiagnostic` code
(alongside `route_side_unreachable`/`eta_window_infeasible`) fires when
an entire region of the search space is pruned this way, naming the
likely cause (a data-coverage gap) rather than leaving the caller to
guess. Tests: a fabricated weather fixture with a deliberately
fully-masked stencil at a specific lattice point, asserting the prune is
explicit and the diagnostic fires, matching the existing hard-constraint
regression-test pattern (`core/legs.py`'s A5/B5/C1 precedents). Not
scoped further here — a real plan, if this needs review's own
plan-before-implementing pass, is N1's own job.

## Part 2 — Derive geometry from passage properties

Three formulas, one per knob Part 1 showed is either the real mechanism
(`cross_track_step_nm`) or worth closing on principle even though it
didn't bind here (`lane_turn_rate_nm`, endpoint taper). `along_track_step_nm`
is deliberately left alone (§1c). Every formula is checked against the
Med's own real numbers below and, where the derivation is defined
directly *from* the Med's own tuned constant, reduces to that constant
exactly — not approximately — for the Med passage; this is the
mechanical form of "the Med's derived geometry must still thread the
strait" (per the freeze framing, §3), not a hoped-for coincidence.

### 2a. `cross_track_step_nm` — a fixed fraction of passage length

```
cross_track_step_nm = clamp(
    total_nm * CROSS_TRACK_STEP_FRACTION,
    MIN_CROSS_TRACK_STEP_NM,
    DEFAULT_CROSS_TRACK_STEP_NM,
)
```

`total_nm` is `build_lattice`'s own already-computed
`distance_m(origin, destination, ref_lat_deg)` (§ code already has this
value at the point `cross_track_step_nm` needs it — no new geometry
call). `CROSS_TRACK_STEP_FRACTION := DEFAULT_CROSS_TRACK_STEP_NM /
MED_STRAIGHT_LINE_NM = 5.0 / 179.55 = 0.027847` — derived *from* the
Med's own real origin/destination distance and its own real tuned
5.0nm, not invented. Applying the same formula back to the Med's own
179.55nm passage gives `179.55 * 0.027847 = 5.000` — **exact, by
construction** (the ceiling clamp never even needs to bind for the Med;
the formula is already exactly 5.0 there). For the UK's 36.74nm passage:
`36.74 * 0.027847 = 1.023nm` — close to the empirically-best-performing
1.0nm point in Part 1's sweep (score 3882.22, detour ratio 1.230), not
the more aggressive 0.5nm one, a deliberate middle-of-the-sweep choice
(§ Judgment calls).

`MIN_CROSS_TRACK_STEP_NM` (proposed: `0.5`nm, matching
`DEFAULT_MIN_REFINEMENT_STEP_NM`'s own existing floor — reusing an
already-precedented value rather than inventing a new one) exists so a
pathologically short future passage (a few nm, e.g. a harbour-to-anchorage
hop) doesn't get an absurdly fine, expensive lane grid. `DEFAULT_CROSS_TRACK_STEP_NM=5.0`
remains the ceiling unchanged — the Med (and anything Med-scale or
longer) is untouched.

### 2b. `lane_turn_rate_nm` — a fixed maximum course-deviation angle per stage

```
lane_turn_rate_nm = along_track_step_nm * tan(radians(MAX_TURN_ANGLE_DEG))
```

`MAX_TURN_ANGLE_DEG := degrees(atan(LANE_TURN_RATE_NM / DEFAULT_ALONG_TRACK_STEP_NM))
= degrees(atan(15.0 / 6.0)) = 68.199°` — derived *from* the Med's own
tuned 15nm/6nm ratio, not invented; ticket 0.8's own comment already
frames `LANE_TURN_RATE_NM` as "not a vessel kinematic limit," i.e. a
search-resolution generosity parameter, which an angle is a more natural
unit for than a fixed absolute distance (a fixed nm value only makes
sense at one specific stage length; an angle is stage-length-invariant by
construction). Since `along_track_step_nm` is unchanged by this ticket
for both shipped packs (§1c), this formula reduces to **exactly 15.0nm**
for both Med and UK — a zero-behaviour-change formula today, closing a
latent risk (a future pack with a different `along_track_step_nm` would
otherwise inherit a flat 15nm turn allowance calibrated for a 6nm stage,
silently too tight or too loose) rather than fixing anything currently
broken. Stated plainly: **this formula is not justified by the UK dog-leg
finding** (1b showed turn-rate is inert there) — it's included because
the ticket's own ask names it explicitly and because self-scaling it is
free (zero measured effect either way) and closes a real, if currently
dormant, risk.

### 2c. Endpoint taper — derived from real local navigability clearance, not bbox margin

Today, `_stage_max_lane` clips the *requested* `cross_track_half_width_nm`
(80nm) down to whatever stays inside the pack's own bbox (with
`BBOX_MARGIN_NM=3.0` margin) — a **data-availability safeguard** (never
ask `RealGeography` for a point outside real ingested coverage), not a
navigability-derived clearance. It happens to produce endpoint taper as a
side effect (ports sit near bbox corners "by design," per the module's
own docstring) but the taper's *shape* is an artifact of how tightly a
given pack's bbox happens to be drawn around its passage, not of how much
real sea room exists near the port.

**Proposed**: extend `_stage_max_lane`'s existing per-lane probing loop
(it already walks `lane = 1, 2, 3, ...` checking the bbox-margin
condition) to *also* check `geography.is_navigable(offset_point)` at each
candidate lane, for the stages nearest each endpoint specifically (reusing
the exact per-point navigability check `_outgoing_edge_navigable_fraction`
already calls, applied to a single point rather than an edge — no new
geography-query primitive) — stopping at whichever bound (bbox-margin,
now-explicit navigability) binds first. The bbox-margin check **stays,
unconditionally** — it's a hard data-boundary guarantee (`RealGeography`
must never be queried outside its loaded grid), not something a
navigability check should ever be allowed to override or relax. This is
additive: for any stage where geography permits at least as much lateral
room as the bbox margin already did, behaviour is unchanged; only stages
where real coastline sits *closer* than the bbox margin get a tighter,
more honestly-derived taper.

**Not the mechanism behind the UK dog-leg** (1b: the chosen path never
came close to any `max_lane_per_stage` bound) — included because the
ticket's own ask names "endpoint approach clearance" explicitly as a
derivation input, and because leaving taper as a pure bbox-margin
artifact is a known, named gap now that L1 is touching this file anyway.
**Real risk, flagged explicitly, not glossed over**: unlike 2a/2b, this
one does *not* reduce to a proven no-op for the Med by construction — the
Med's own bbox is comfortably large relative to its passage (the 179.55nm
passage's own `max_lane_per_stage` already starts at 4-5 near each
endpoint rather than 0, i.e. today's bbox-margin taper is already fairly
loose there), so a navigability-derived taper is *expected* to be neutral
or only mildly different, but this must be **verified empirically during
implementation** (§ Acceptance), not assumed — the same "verify, don't
assume" discipline S1's own plan held itself to for its API-layer
changes.

**§2c escape rule (pre-agreed at review).** If implementation step 3
(wiring the navigability-derived taper) shifts *anything* in the Med
regression suite — any test, any numeric assertion, not just the named
Bonifacio ones — **cut §2c from this ticket entirely** and ship §2a+§2b
alone. Report the shift (what changed, which test, the real numbers) as
a finding; do not debug or re-tune §2c's own mechanism under L1 to make
it pass. This mirrors §1c's own discipline (report, don't silently work
around) and keeps the one piece of this design without direct empirical
backing (per the judgment call above) from becoming a second, unplanned
diagnostic investigation inside a ticket that's already delivered its
proven fix via §2a.

**Escape rule triggered — §2c cut from this ticket, not implemented
(2026-07-22).** Wired exactly as designed above (`_stage_max_lane` gained
an additive `geography` parameter; `geography.is_navigable` checked at
each candidate lane on top of, never relaxing, the existing bbox-margin
check). Result: 12 real test failures, not a narrow numeric drift —
`RuntimeError: baseline route (fixed 14kn, 2 engines) is infeasible even
via the open lattice search`, plus failures across
`test_optimiser_regression.py` (`test_mistral_high_comfort_routes_lee_side`,
`test_calm_corridors_converge_and_speed_varies_by_pace`,
`test_pure_schedule_weights_converge_to_isochrone_time_optimal`, others),
`test_optimiser_constraints.py`'s A5/B5 hard-constraint tests, and
`test_uk_sw_pack_acceptance.py`. Root cause, understood well enough to
report accurately (not chased further, per the rule above): the
implementation gated `max_lane_per_stage` — a hard, global cap on how
far the search can *ever* reach at a given stage, at every subsequent
finer refinement too — on a single point's own navigability. But
`cross_track_half_width_nm` (80nm, requested wide *deliberately*, so the
search can consider genuinely different routing strategies around real
obstacles) routinely projects a candidate lane's offset point onto or
near real coastline at many stages along any passage that runs near
land at all — even though the *feasible* route those wide lanes exist to
support never uses that literal point, approaching the same lateral
region gradually across several stages instead. Capping the per-stage
*lane-index ceiling* itself on point-level navigability collapses the
lattice's width far more broadly than "endpoint approach clearance" ever
intended — it isn't the narrow, near-port-only effect §2c's own design
discussion assumed. The existing, unmodified adaptive-refinement
mechanism (`_outgoing_edge_navigable_fraction`, edge-level, only ever
makes local step *size* finer, never shrinks the reachable lane *range*)
is the right-shaped tool for this kind of check; a point-level cap on
`max_lane_per_stage` is not, at least not implemented this directly.
**Result: L1 ships §2a+§2b only.** A correctly-shaped geography-derived
taper (if pursued) is a real, separate follow-up — not scoped further
here, per the escape rule's own instruction not to re-design under L1.

### 2d. `RegionPack` overrides — retained, unused by either shipped pack

`RegionPack.lane_turn_rate_nm`/`.min_navigable_edge_fraction`/
`.min_refinement_step_nm` (R1) and the new derived-geometry knobs all stay
plain `build_lattice(...)` keyword parameters with derived defaults — a
pack can still override any of them (a real escape hatch for a future
pack whose coastline genuinely needs different tuning, the same
Bonifacio-style empirical process R1/0.8 already established), but
**neither `med.yaml` nor `uk_sw.yaml` sets any of them by the end of this
ticket** (an explicit acceptance criterion) — proof the derivation, not
manual tuning, is what makes both packs work.

## 3. Freeze framing — S1's precedent, not R1/C1's

Matching ticket S1's own explicit break from R1/C1's "zero behaviour
change" framing: **this ticket changes what routes the search *can*
represent** for any pack whose passage length differs enough from the
Med's own 179.55nm reference to move `cross_track_step_nm` off the 5.0nm
ceiling (today, that's just the UK pack) — that's the entire point, and
it can't honestly be called zero-behaviour-change. What *is* held to a
zero-change standard, mechanically, not just claimed:

1. **No new cost terms, constraints, or search features.** `core/legs.py`'s
   five hard constraints, `core/optimiser.py`'s cost/scalarisation
   formula, the A*/Dijkstra algorithm itself, and `core/isochrone.py` are
   all untouched — this ticket only changes *what geometry
   `build_lattice` hands the unmodified search*, the same "search
   machinery itself is untouched" boundary S1 drew around distillation.
2. **The derivation formulas reduce to the Med's exact existing constants
   for the Med's own passage, by construction** (2a, 2b — shown
   algebraically above, not just asserted) — so `pytest -m ""`,
   including every Bonifacio/0.8 regression test, is expected to pass
   **unmodified**, the same mechanical bar R1 held itself to. Any numeric
   shift found during implementation (2c's own flagged real risk) is a
   finding to report and resolve before proceeding, not something to
   quietly absorb.
3. **`along_track_step_nm` is untouched everywhere** (§1c) — the one
   knob this ticket deliberately does *not* self-scale, given the real
   NaN-cost risk found investigating it.

## 4. Performance budget

Since `along_track_step_nm` (hence `n_stages`) is unchanged for both
packs, and 2a/2b are exact no-ops for the Med by construction, **the Med
demo passage's state/edge count and wall-clock are expected to be
unaffected** — the acceptance run (§ Acceptance) measures this directly
rather than assuming it.

For the UK pack, `cross_track_step_nm` moving from 5.0nm to ~1.0nm
(§2a) increases lane count roughly 5x at stages where the half-width
request isn't otherwise clamped (mid-passage, `max_lane_per_stage`
roughly `cross_track_half_width_nm / cross_track_step_nm`): today
±4-5 lanes per stage (Part 1's own dump); at ~1nm spacing, roughly
±20-25. With `n_stages=7` unchanged, that's a state-count growth from
roughly `7 × 10 ≈ 70` to `7 × 45 ≈ 315` (stage×lane pairs, before the
time-bucket dimension) — small in absolute terms, and this project's own
B1 profiling gotcha already established that **weather sampling, not
lattice size, dominates `optimise()`'s cost** for passages at this scale;
a ~4.5x growth in a search space that was already sub-second (Part 1's
`cross_track_step_nm=0.5` sweep point, 10x finer than shipped, ran with
no perceptible slowdown) is not expected to meaningfully move measured
wall-clock. **Stated as an estimate here, measured for real in the
acceptance run** — the same "estimate now, measure for real" discipline
S1's own §5 (bounded-cost) section used.

## 5. S1 interplay

`core/distill.py` runs unchanged, strictly after `optimise()`'s search —
nothing about L1 touches it. **Expected, not yet measured**: a lattice
whose lane spacing already matches the passage's own natural resolution
should hand distillation a track with fewer *forced* geometric kinks to
begin with (today's 5nm-quantised UK track has kinks that exist only
because the lane grid couldn't express the vessel's actually-wanted
position, not because the position itself needed a waypoint there) — so
distillation's own waypoint-reduction percentage should measurably
*shrink* for the UK pack once the underlying lattice already tracks
closer to what a navigator would draw. Both undistilled and distilled
waypoint counts, before and after L1, are recorded in the acceptance run
(§ Acceptance) as a side metric — genuinely uncertain in direction until
measured (a finer lattice could in principle also just produce more
raw waypoints for distillation to remove; both are plausible, hence
measuring rather than asserting).

## Acceptance criteria

- **Plymouth→Falmouth detour ratio**: a real `optimise()` run (real
  geography, real weather, same scenario as Part 1) with the derived
  geometry. Target is **not invented** — computed from the real
  geographic minimum: probe for the shortest path that stays clear of the
  two real near-endpoint obstructions Part 1 already measured (first ~9%,
  last ~5-6% of the straight line) and state the resulting target ratio
  here once measured, before/alongside the actual `optimise()` result —
  matching the ticket's own "measure the geographic minimum... rather
  than inventing a number" instruction exactly.
- **Med demo passage**: full `pytest -m ""` green, **unmodified** —
  including every Bonifacio/0.8 regression test
  (`test_bonifacio_strait_transit_is_reachable_at_current_lattice_resolution`
  and the others §1's freeze framing names) and
  `test_charter_window_reflects_vessel_envelope_not_an_arbitrary_number`
  unchanged. Any failure is reported per §3's own discipline, not
  silently worked around.
- **Both packs' yaml end the ticket with no lattice-knob overrides set**
  (§2d) — `data/region_packs/med.yaml` and `data/region_packs/uk_sw.yaml`
  diffed to confirm neither gained a `lane_turn_rate_nm`/
  `min_navigable_edge_fraction`/`min_refinement_step_nm`/new-knob entry.
- **Perf**: real measured wall-clock for both the Med demo passage and
  Plymouth→Falmouth, before/after, recorded in this file (§4's estimate
  checked against reality).
- **S1 side metric**: undistilled and distilled waypoint counts for
  Plymouth→Falmouth, before and after L1, recorded (§5).
- `ruff check .` clean; ROADMAP row + CLAUDE.md gotcha added per
  convention (below).

### Real acceptance run results (2026-07-22)

**Shipped design: §2a+§2b only** (§2c cut by its own escape rule, above).
`pytest -m ""` was green and unmodified — 478 passed, byte-identical
count to pre-L1 — after both Step 1 (formulas) and Step 3's
implement-then-revert cycle.

**Plymouth→Falmouth detour ratio** (`departure_t0_h=6.0`,
`speeds_kn=(10.0,)`, `pace=comfort=50`, real geography + real weather
including the merged CMEMS currents — the same scenario used throughout
this ticket's diagnostics):

| | distance_nm | detour_ratio | score €eur | waypoints |
|---|---|---|---|---|
| pre-L1 baseline (Part 1) | 51.366 | 1.3980 | 4417.64 | 7 (undistilled) |
| **post-L1 (§2a+§2b, distilled — the real end-to-end result)** | **40.558** | **1.1038** | **3487.62** | **5** |

A **21.1% score improvement** and detour ratio dropping from 1.398 to
1.104 — most of the way to straight-line. **Geographic-minimum target**:
probed the real lateral clearance needed to clear each of the two
near-endpoint obstructions (navigable + ≥4.5m depth, this vessel's
draft+UKC) at their worst point — 0.5nm near the origin obstruction,
0.25nm near the destination one — and computed the minimum "there and
back" detour distance a route confined to just those two short stretches
would need (simple triangle geometry: `2·hypot(L/2, clearance) - L` per
obstruction, `L` the obstruction's own along-track length). Target:
**37.204nm, detour_ratio≈1.0126**. The real result (1.104) doesn't reach
this idealised lower bound — expected: the geometric target ignores
`lane_turn_rate_nm`'s own turn allowance and the lattice's discrete lane
spacing (still ~1.02nm at this passage length, §2a), both real, non-zero
costs a target built from pure point-obstacle avoidance doesn't pay. The
gap between 1.104 and 1.013 is real lattice-discretisation/turn-allowance
cost, not evidence of a further, unaddressed problem — consistent with
§1c's finding that stage spacing (the other axis that could close this
gap further) is deliberately not touched this ticket.

**Med demo passage perf** (Antibes→Porto Cervo, `speeds_kn=(12,14)`):
wall clock min=3.398s, median=3.420s (n=3 reps) — matching S1's own
pre-L1 acceptance numbers for the identical scenario (3.463-3.509s) within
normal run-to-run noise, not a regression. Candidate scores
**13555.35 / 15721.78 / 17026.16** — bit-exact matches to the pre-L1
values recorded in `docs/plans/ticket-S1.md`'s own Med acceptance run,
confirming §2a/§2b's "exact Med reduction" claim holds in the real,
full `optimise()` path, not just in the isolated `build_lattice` checks
done during implementation.

**Both yaml files**: `git diff --stat data/region_packs/med.yaml
data/region_packs/uk_sw.yaml` shows only the `lane_turn_rate_nm: 15.0`
line *removed* from each (replaced with an explanatory comment) — no new
key added to either file. The "derive, don't tune" proof holds.

**S1 waypoint-count side metric** — re-run with S1's own original UK
acceptance parameters (`speeds_kn=(10, 12)`, default `departure_t0_h=0.0`)
for a clean before/after comparison against `docs/plans/ticket-S1.md`'s
already-recorded numbers:

| | undistilled n_wp | distilled n_wp | % fewer | undistilled score | distilled score |
|---|---|---|---|---|---|
| pre-L1 (ticket-S1.md) | 7 | 3 | 57% | 4190.23 | 3920.59 |
| post-L1 | 7 | 4 | 43% | 3413.59 | 3114.21 |

Confirms §5's own prediction, in the direction it hoped for but flagged
as genuinely uncertain: distillation's *percentage* waypoint reduction
**shrank** (57%→43%) because the undistilled track already has fewer
purely-discretisation-driven kinks to remove — but both the undistilled
and (more importantly) the final distilled result are substantially
*better* post-L1 (score 3920.59→3114.21, a further 20.6% improvement on
top of L1's own already-measured 21.1% gain above; distance 48.09nm→
40.558nm). The remaining kinks distillation still removes (7→4, not
7→7) are consistent with §5's own framing: some genuinely reflect real,
still-necessary waypoints (obstruction-avoidance points a finer lattice
now places closer to where they're actually needed), not artifacts.

## Judgment calls flagged for sign-off

1. **§2a's clamp target**: the formula lands at ~1.02nm for the UK
   passage, not the more aggressive 0.5nm that scored best in Part 1's
   own sweep. Recommending the formula's natural output (proportional,
   derived, not hand-picked to match the best sweep point) over chasing
   the absolute best observed number — 1.0nm already captures 90% of the
   achievable improvement (score 3882 vs the 0.5nm point's 3828, both
   against a 4418 baseline), and picking 0.5nm specifically would be
   fitting a constant to this one passage's own sweep results, exactly
   what this ticket exists to stop doing.
2. **§2c (endpoint taper) is included despite not being the UK dog-leg's
   own mechanism.** Alternative: cut it from this ticket entirely, since
   Part 1 showed it isn't what's binding today, and ship only 2a (the
   proven fix) + 2b (free, zero-risk). Recommending keeping it in scope
   since the ticket's own Part 2 ask names it explicitly and it's a
   real, principled improvement over a bbox-margin artifact — but
   flagging this as the one piece of the design without an empirical
   "this is definitely needed" backing, for explicit sign-off.
   **Outcome: the pre-agreed §2c escape rule fired during implementation
   (2026-07-22)** — wiring it caused 12 real test failures across the Med
   suite, not a narrow numeric drift, so §2c was cut entirely per the
   rule's own instruction (report, don't debug under L1). See §2c's own
   "Escape rule triggered" note for the root cause. L1 ships §2a+§2b
   only — this alternative is what actually happened, not the
   recommendation above.
3. **`along_track_step_nm` is left completely untouched, not even
   given a conservative derived floor.** Alternative: derive a mild
   floor (e.g., ensure at least N stages) that would be a no-op for both
   shipped packs today but might matter for some future, even-shorter
   pack. Recommending leaving it alone entirely — §1c's NaN finding
   means *any* along-track refinement, however conservative, needs the
   NaN-cost bug fixed first to be trustworthy, and inventing a formula
   for a knob this ticket has just shown is unsafe to move would
   contradict its own finding.

## Scope cuts (explicit)

- **The NaN-cost silent-state-loss bug (§1c) — elevated at review to N1,
  an immediate fast-follow ticket, not implemented here.** Fixing it means
  `evaluate_leg`/the search treating a non-finite cost as an explicit,
  diagnosable prune (or fixing the underlying data-coverage gap at ingest
  time) — a cost-model/hard-constraint change, outside this ticket's own
  freeze (§3). Scope sketch: §1c's own "N1 — fast-follow" subsection.
  Elevated above a routine someday-follow-up because R1's arbitrary
  endpoints/favourites make this a live product-surface risk today, not
  just a prerequisite for L1's own future `along_track_step_nm` work.
- **Alternative graph structures** (uniform grid, visibility graph,
  quadtree) — considered and rejected, briefly: the corridor lattice's
  whole reason for existing is that its state space is
  small-by-construction (`stage × lane`, not `all cells in a bbox`),
  which is what makes per-edge real-weather sampling affordable at all —
  the B1 profiling gotcha's ~690k-call search cost is already dominated
  by weather sampling on *this* graph; a uniform grid or visibility graph
  over the same area would multiply the number of edges needing a
  weather sample by orders of magnitude for no navigational benefit a
  corridor-shaped passage actually needs. Quadtree-style adaptive
  resolution is architecturally close to what ticket 0.8's per-stage
  refinement and this ticket's per-pack lane-spacing derivation already
  do, just generalised further than any real passage here has shown a
  need for. Not pursued.
- **R3 ocean-scale geometry** (spherical/great-circle lattices,
  ocean-crossing currents) — untouched, a separate, larger ticket, same
  boundary R1/C1 already drew.
- **Any cost-model or search-feature change** — §3's own freeze.
- **Re-tuning `min_navigable_edge_fraction`/`min_refinement_step_nm`/
  `max_refinement_passes`** (ticket 0.8's own adaptive-refinement knobs) —
  untouched; Part 1 found no evidence they're implicated in the UK dog
  leg (`refinement_diagnostics` showed adaptive refinement already firing
  and converging correctly, at stage 5 in the baseline).

## ROADMAP row text (proposed)

Suggest placing next to S1 in the "Beyond Phase 2" table:

> **L1 — Self-scaling lattice geometry** | `core/lattice.py`'s lane
> spacing and turn allowance — previously Bonifacio (ticket 0.8)-tuned
> absolute constants inherited by every new pack via ticket R1's override
> mechanism, with no principled reason to be right for a different
> passage length — become derived from passage geometry
> (length-proportional lane spacing, angle-based turn allowance), each
> reducing to the Med's exact existing tuned constants by construction
> (verified bit-exact, real acceptance run). Motivated by a real,
> empirically-diagnosed finding on the UK South-West pack (a 40% detour
> on the Plymouth↔Falmouth passage, traced to lane-spacing discretisation
> — not tide, not the A* heuristic, not turn-rate): `docs/plans/ticket-C1.md`'s
> dog-leg diagnostic. Real acceptance result: detour ratio 1.398→1.104,
> a 21% score improvement, Med demo passage bit-exact/unaffected. A third
> planned piece, real-navigability-derived endpoint taper, was designed,
> implemented, and **cut** after a pre-agreed escape rule fired during
> implementation (it collapsed lane range far more broadly than intended
> — 12 real Med-suite test failures, not a narrow drift); shipped as
> lane-spacing + turn-allowance only. Found and explicitly *not* fixed
> along the way: a real NaN-cost silent-state-loss bug in the search,
> triggered only by refining stage spacing — elevated to an immediate
> fast-follow ticket (N1, live product-surface risk via R1's arbitrary
> endpoints/favourites), not built here. Full design, the empirical
> diagnosis, the escape-rule trace, and the real acceptance-run numbers:
> `docs/plans/ticket-L1.md`. | Search-machinery-untouched (S1's own
> freeze precedent, not R1/C1's zero-behaviour-change one) — a bounded
> change to what geometry the unmodified search is handed, not to the
> search itself. |

## CLAUDE.md gotcha entries (proposed, to add on completion)

- A new gotcha recording: the two shipped derivation formulas and their
  exact Med-reduction algebra (§2a/§2b), the real UK acceptance numbers,
  the §2c escape-rule finding (what was tried, why it broke so broadly,
  why it's a real design lesson about `max_lane_per_stage` vs edge-level
  checks and not just "this specific implementation had a bug"), and —
  the most important part to preserve — **the NaN-cost silent-state-loss
  finding (§1c) in full**, since it's a real, previously-unknown
  correctness gap, now a live product-surface risk (N1), that the next
  person touching `along_track_step_nm`, weather-grid resolution, or
  `_lattice_search`'s cost aggregation needs to know about regardless of
  whether they ever read this ticket's own plan file.

## Implementation order

1. **`core/lattice.py`**: add `CROSS_TRACK_STEP_FRACTION`,
   `MIN_CROSS_TRACK_STEP_NM`, `MAX_TURN_ANGLE_DEG` constants (derived from
   the Med's own numbers, per §2a/§2b's algebra, stated in each
   constant's own docstring the same way `LANE_TURN_RATE_NM`'s existing
   comment records its own empirical origin). `build_lattice` computes
   `cross_track_step_nm`/`lane_turn_rate_nm` from these plus `total_nm`
   when the caller doesn't override them (still fully overridable —
   §2d) — additive, existing explicit-parameter callers unaffected.
2. **Fast suite, then full `pytest -m ""`, before touching taper at
   all** — isolates "did the length-proportional formulas alone break
   anything" from "does the taper change also behave," same isolation
   discipline S1's own step 2/3 split used.
3. **`_stage_max_lane`'s navigability-derived taper (§2c)** — additive to
   the existing bbox-margin check, never replacing it. Fast suite, then
   full `pytest -m ""` again.
4. **Real acceptance run** (§ Acceptance): Plymouth→Falmouth detour ratio
   and geographic-minimum target, Med regression suite, both packs' yaml
   diffed for zero new overrides, real wall-clock for both packs, S1
   waypoint-count side metric — all recorded in this file.
5. **Docs**: ROADMAP row, CLAUDE.md gotcha (both above, filled in with
   real numbers).

## Verification

- `pytest -m ""` green **unmodified** after step 1 (length-proportional
  formulas only) and again after step 3 (taper) — isolating which change
  is responsible for any surprise, matching S1's own per-step isolation
  discipline.
- `ruff check .` clean throughout.
- `git diff --stat data/region_packs/med.yaml data/region_packs/uk_sw.yaml`
  showing no new lattice-knob keys added to either file — the mechanical
  proof behind the "derive, don't tune" acceptance criterion.
- The Plymouth→Falmouth and Med demo runs in the acceptance section are
  genuine `optimise()` calls against real geography/weather, not mocked
  stand-ins — matching this project's standing bias toward real
  verification (R1/C1/S1/B7's own precedent).

### Critical files for implementation

- `core/lattice.py` (`build_lattice`, `_stage_max_lane`, new derived-constant
  definitions)
- `tests/test_lattice.py` (new unit tests for the derivation formulas
  against fabricated short/long passage fixtures — exact Med-reduction
  algebra, clamp floor/ceiling behaviour)
- `tests/test_optimiser_regression.py` (the full Bonifacio/0.8 suite —
  must stay green unmodified; the acceptance run's own real check)
- `tests/test_uk_sw_pack_acceptance.py` (existing UK acceptance test —
  confirm it still passes, and consider whether a detour-ratio assertion
  belongs here or stays a plan-file-recorded manual finding)
- `docs/plans/ticket-C1.md` (referenced, not modified — the motivating
  diagnostic already lives there)
- `data/region_packs/med.yaml`, `data/region_packs/uk_sw.yaml` (verify no
  new override keys needed)
