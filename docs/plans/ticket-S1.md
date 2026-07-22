# Ticket S1 — Route distillation (waypoint simplification as a post-search pass)

**Review status: approved with 1 required amendment, incorporated below.**
All three judgment calls approved as recommended (pass-level backstop;
same-speed precondition; default-on with the stated freeze framing).
Implementation follows this revised version, in the plan's own order.

## Context

Lattice tracks are polylines through discrete stage/lane points
(`core/lattice.py`'s `Lattice.point(stage, lane)`); corridor-DP tracks are
polylines through a hand-drawn corridor's own linearly-interpolated
segments (`core/corridors.py`'s `_seg`). Both carry small quantisation
kinks a navigator's own plan wouldn't have — a stage-to-stage lane shift
that's really part of one smooth turn gets rendered as several small
course changes; a corridor's `_seg(a, b, n)` interpolation points between
two real hand-picked corners are pure geometry filler, never a real
waypoint. Costs computed on these polylines are honest — `evaluate_leg`
is the same function the search itself scores every edge with, so
current numbers aren't flattered by any of this — but the kinks add
low-single-digit-percent distance where the lattice can't represent a
true diagonal, and they make plans look rough compared to what a captain
would actually write down.

**This ticket is a post-search pass, not a search change.** It runs once
per candidate, after the search has already picked a track, and only
ever *removes* waypoints whose removal is independently re-verified safe
and non-worsening through the exact same machinery (`core/legs.py`'s
`evaluate_leg`) the search used to build the track in the first place —
no new physics, no new hard-constraint logic, no new cost model.

**Two directly relevant precedents, read before designing:**

- **CLAUDE.md's B1 profiling gotcha**: `optimise()`'s ~19s cold-call cost
  against real geography/weather is dominated by ~690k `evaluate_leg`
  calls during the *search* (every candidate speed × engine config ×
  lattice edge, weather-sampled at a different position and time every
  call — not cacheable). This is why S1's fix is a bounded post-pass over
  the handful of already-chosen candidate tracks, not a finer lattice —
  a finer lattice would multiply the *search's* own dominant cost;
  distillation only touches the small number of tracks the search has
  already committed to, at their already-chosen speeds. §5 below
  quantifies this precisely, the same way the B1 gotcha itself was
  reached empirically (profiled, not assumed).
- **CLAUDE.md's Bonifacio/ticket 0.8 trace**: the concrete, cited reason
  the correctness checks below aren't paranoia. Bonifacio's real
  scattered-islet field is exactly the shape of hazard where two lattice
  waypoints can each individually read as clear water while the *direct*
  line between them clips a reef the adaptively-refined lattice was
  specifically built to route around (ticket 0.8's whole finding).
  Distillation proposing a shortcut between two non-adjacent-in-hazard
  waypoints is structurally the same risk, at a different scale — the
  reason every proposed shortcut is re-verified through the identical
  `evaluate_leg`/`_navigable_along_leg` sampling density the search
  itself trusts, not a cheaper approximation.

## Design

### 0. Scope boundary: where distillation lives, and where it doesn't

**New module, `core/distill.py`** — not folded into `core/optimiser.py`
(which is already large) or `core/legs.py` (whose own stated job is
single-leg evaluation, not multi-leg track surgery). Exposes one
entry point, `distill_track(...)` (§1), called from exactly one place:
**inside `optimise()`, after a candidate's raw result dict is built by
`_lattice_route_result`/`_dp_route`, before `_candidate_from_result`
turns it into a `Candidate`.**

**`_lattice_route_result`, `_dp_route`, `_baseline_route`, and
`core/isochrone.py` are all untouched — confirmed by design, not
incidentally.** Every test that calls these directly (`tests/test_optimiser_constraints.py`'s
`test_a4_cross_current_appears_in_cts_not_just_speed`/
`test_a4_cross_current_appears_in_cts_via_lattice_search`, among others)
bypasses `optimise()` entirely and is therefore structurally unaffected
by S1 — not because the numbers happen to stay close, but because
distillation's only call site is inside `optimise()` itself, strictly
after these functions return. This is the cleanest possible freeze
boundary: the search machinery (lattice construction, A*/DP, isochrone
pre-pass, heuristic) never calls into `core/distill.py`, and
`core/distill.py` never calls back into search machinery beyond the
same `evaluate_leg` every leg-costing call site already uses.

**The do-nothing baseline (`_baseline_route`) is never distilled** — a
fixed reference must stay exactly what "do nothing, fixed 14kn/2-engine,
lattice-routed" actually produces, not a touched-up version of it. Ticket
scope, restated from the request.

### 1. The distillation algorithm

**Precondition for attempting to remove a waypoint: the two legs
adjacent to it must already share the same (`stw_ms`, `active_engines`)
— a deliberate, conservative scope boundary, flagged for sign-off.**
Speed and engine config are chosen *per edge* by the search (ticket 0.4's
whole point); a waypoint where speed genuinely changes is a real planning
decision, not a quantisation artefact, and merging across it would mean
re-choosing a speed for the new leg — a small re-optimisation, not
geometry cleanup. Restricting removal to same-speed-neighbour pairs keeps
this a *pure* polyline simplification: no new decision is ever made, only
redundant points removed. Two consequences worth stating plainly:

- **Corridor-DP candidates are always eligible everywhere** — `_dp_route`
  commands one fixed `(stw_ms, active_engines)` for the *entire* corridor
  (confirmed by reading `_dp_route`'s signature directly: `stw_ms`,
  `speed_kn`, `active_engines` are single scalars for the whole call, not
  per-leg), so every interior waypoint on a corridor-DP track qualifies
  for the precondition trivially. These tracks (linearly-interpolated
  `_seg()` filler between a handful of real hand-picked corners) are
  expected to distil the most aggressively.
- **Lattice-search candidates are only partially eligible** — wherever
  the search happened to pick the same speed/engine on two consecutive
  edges (common in calm/steady conditions, less so where local
  weather/current genuinely favours a speed change), those points are
  simplification candidates; a genuine speed-change point is always kept.
  `stw_ms_per_leg`/`engines_per_leg` (already computed internally by
  `_reconstruct_lattice_route`, currently discarded after
  `_lattice_route_result` returns) need to be threaded out in the result
  dict so `distill_track` can see them — the one additive change to
  `_lattice_route_result`'s/`_dp_route`'s return shape this ticket needs
  (new dict keys, nothing removed).

**Per-pass mechanics — a single left-to-right sweep, O(n) leg
evaluations, correctly time-propagated:**

Walk the track in order, maintaining a running elapsed time `t` (starting
at the candidate's own `t0_h`) and a `kept` list (starts with the fixed
origin). For each interior waypoint `i`, in order:

1. If `i`'s incoming leg (`kept[-1] -> track[i]`) and outgoing leg
   (`track[i] -> track[i+1]`) don't share `(stw_ms, active_engines)`:
   keep `i`, advance `t` by the incoming leg's real duration (evaluated
   at the *current* `t`, not the original search's own timing — this is
   what makes the sweep correctly time-propagated even for waypoints that
   end up kept), continue.
2. Otherwise, evaluate the shortcut leg `kept[-1] -> track[i+1]` at the
   shared speed/engine, at the *current* `t` (`evaluate_leg`, same
   `depth_exempt_points` the candidate's own search used — §2). If it
   fails any hard constraint (`navigable`, `depth_ok`, `slam_event`,
   `overload`, `current_exceeds_stw` — literally the same five-flag check
   every search call site already applies, no new ones), keep `i` and
   advance as in (1).
3. Otherwise, compare weighted cost: `evaluate_leg(kept[-1], track[i]) +
   evaluate_leg(track[i], track[i+1])` (both at the correct `t`) vs. the
   shortcut leg's own cost, using the exact same weighted-score formula
   the search itself costs edges with (`weights.fuel_eur_per_kg *
   fuel_kg + weights.time_eur_per_min * duration_h * 60 +
   weights.comfort_eur_per_index_point * comfort +
   weights.wear_eur_per_index_point * wear`). If the shortcut is not
   worse, **accept**: drop `i` from `kept`, advance `t` by the shortcut
   leg's duration instead, and the *next* candidate (`i+1`) is now
   compared against `kept[-1]` (unchanged) with the shortcut leg's
   `(stw_ms, active_engines)` inherited as the "incoming" pair for step
   1's precondition check — chained same-speed removals collapse in a
   single pass, not one waypoint per pass. If worse, keep `i` and advance
   as in (1).

**Why this is "locally decided but not locally unsound," and where the
ticket's "total-score comparison over the whole track, not local"
requirement actually gets enforced:** step 3's comparison is *not* the
naive trap the ticket warns against — because `t` is threaded correctly
through the whole sweep, every comparison already reflects every earlier
accepted removal's real time-shift, not stale pre-pass timing. What a
purely-local, correctly-timed comparison *can't* see is a subtler,
real effect: shaving a few minutes off arrival at the merge point shifts
every *downstream* leg's own weather/current sample time, which can
change that leg's own cost — usually negligibly (weather/current fields
interpolate smoothly between hourly samples, and a kink-removal's time
saving is minutes, not hours, so the resulting downstream cost swing is
bounded and small by that same smoothness, not by assumption) but not
provably never in the wrong direction. **The backstop, not the per-waypoint
check, is what makes the whole-track claim rigorous**: after a pass
finishes building its candidate `kept` track, evaluate the *entire* new
track end-to-end from `t0_h` exactly once (reusing `_build_leg_targets`'s
own walk, itself O(n)) to get its true total fuel/duration/comfort/wear/
cost, and compare that true total against the true total of the track
*before* this pass (already known). **If the pass's aggregate result is
not worse, keep it and iterate another pass on the new track; if it came
out worse despite every individual step 3 decision looking locally sound
(the rare weather-timing-interaction case above), discard the whole
pass's changes and stop** — simpler and safer than trying to isolate
which specific merge caused the regression, at the cost of possibly
leaving a few individually-valid merges on the table in that rare case.
**Judgment call, flagged for sign-off**: this pass-level accept/reject
(not a finer per-merge rollback) is the proposed design; a stricter
per-merge whole-remaining-track re-verification is correctness-equivalent
but O(n²) per pass, contradicting the O(passes × n) budget (§5) — not
recommended, but named as the fallback if the pass-level backstop ever
proves too coarse in practice.

**Termination/determinism**: repeat passes, always sweeping in the
current track's own left-to-right order (never a different order, never
randomised — same request always produces the same distilled result),
until a pass makes zero removals or is discarded by the whole-track
backstop. Bounded above by the original waypoint count (each pass removes
at least one waypoint or the loop ends), so this always terminates.

### 2. The correctness points, addressed one by one (per the ticket's own list)

- **Time-dependence**: handled by construction — §1's sweep threads `t`
  forward correctly through every decision, and the pass-level backstop
  is a genuine whole-track re-evaluation, not a per-leg approximation.
- **ETA window**: **resolved by wiring order, not a separate check** —
  §3 wires distillation to run on every candidate *before* `optimise()`'s
  own `missed_window`/pool/sort/picks logic reads `c.duration_h` (today's
  code already computes `missed_window` from `candidates_all` *after*
  candidates exist — moving distillation to happen as each candidate is
  built means every downstream decision already sees final, distilled
  durations). Since distillation only ever keeps duration equal or
  reduces it (the pass-level backstop guarantees the accepted result
  isn't worse), a candidate that met `latest_arrival_h` before distillation
  still meets it after, structurally. **Still write the direct unit-level
  assertion the ticket asks for** (`distill_track(...).duration_h <=`
  original duration, on a fixture with a real removable kink) as
  defence-in-depth on `core/distill.py` itself, independent of the wiring
  argument — the wiring argument explains *why* it should always hold,
  the test *proves* it does for the actual implementation, not just the
  design on paper.
- **Depth-exempt endpoints**: `distill_track` is called with the exact
  same `depth_exempt_points` tuple the candidate's own search used
  (`(lattice.origin, lattice.destination)` for lattice candidates,
  `(corridor.points[0], corridor.points[-1])` for corridor-DP) — since
  distillation only ever removes *interior* waypoints, `track[0]`/
  `track[-1]` are never touched, so every merged/shortcut leg's own
  `_leg_depth_ok` check continues to treat proximity to the real,
  unmoved endpoints as exempt exactly as before. Two existing tests
  already exercise this scenario end-to-end through `optimise()` and
  must keep passing unmodified: `tests/test_optimiser_endpoints.py`'s
  `test_shallow_port_is_reachable_under_depth_enforcement` and
  `test_shallow_anchorage_is_reachable_under_depth_enforcement` (both
  assert `candidate.track[-1] == <the real shallow endpoint>` through a
  full `optimise()` call, not a bypassed helper) — real, existing
  regression coverage for exactly this correctness point, not new
  fixtures S1 has to invent.
- **Candidate metrics recomputed from the distilled track**: the
  whole-track backstop evaluation (§1) *is* the recomputation —
  `fuel_kg`/`comfort_index`/`wear_index`/`max_hs_m`/`duration_h`/
  `distance_nm`/`cost` all come from walking the final `kept` track once,
  the same accumulation `_lattice_route_result`/`_dp_route` already do
  today, just against fewer waypoints. `leg_targets`/`alteration_list`
  are rebuilt by calling the **existing, unchanged** `_build_leg_targets`/
  `_build_alteration_list` against the distilled track — no new code
  for either, direct reuse. `speed_kn` (lattice candidates: already a
  derived `distance_nm / duration_h` average, recomputed the same way
  post-distillation, consistent with how it's already computed today;
  corridor-DP candidates: a single commanded STW for the whole corridor,
  structurally unchanged since only same-speed merges are ever accepted)
  and `active_engines` (re-run `_majority_by_duration` on the distilled
  per-leg engine/duration lists, for full consistency, though very
  unlikely to change the pick given only quantisation-level time shifts)
  are both recomputed too — nothing about the returned `Candidate` is
  allowed to describe a track other than the one actually returned.
- **`Candidate.side`/`_route_signature`**: computed from the **original**
  (pre-distillation) search track, exactly as today, and carried through
  unchanged — `_route_signature` reads track *latitude* to find the
  Corsica-band-relevant stretch and average longitude within it; since
  distillation only removes points that were geometrically redundant
  (collinear-ish with their neighbours, by construction of the
  cost/hard-constraint check), recomputing on the distilled track would
  very likely give the identical label anyway, but this ticket doesn't
  rely on that coincidence — `side` is captured once, before distillation
  runs, same as `optimise()`'s existing corridor-DP path already captures
  `item["corridor"].side` independently of the DP's own optimisation.
  Documented explicitly in `core/distill.py`'s own docstring, since it's
  the one field distillation deliberately does *not* touch.
- **Baseline not distilled**: §0.

### 3. Wiring into `optimise()`

`_lattice_route_result`/`_dp_route`'s returned dicts gain two new keys
(`stw_ms_per_leg`, `engines_per_leg` — already-computed internal state,
just newly exposed). Immediately after each raw result dict is obtained
— for `primary`, `secondary`, and every corridor-DP `result` in the
`best_by_key` loop, **before** `_candidate_from_result` is called and
**before** the `missed_window`/`pool.sort()`/`picks` logic runs — call
`distill_track(result, weights, request.weather, request.geography,
twin, depth_exempt_points, ref_lat_deg)`, which returns an updated dict
(same shape, `track`/`duration_h`/`fuel_kg`/etc. replaced, `side`
untouched) that then feeds into `_candidate_from_result` exactly as
before. This ordering is what makes §2's ETA-window argument hold
structurally rather than needing a bolt-on re-check afterward.

**Judgment call, flagged for sign-off, per the ticket's own prompt**:
`PlanRequest.distill: bool = True` — an escape hatch, default **on**.
Recommending default-on because that's what actually ships the
improvement (a `False`-by-default field would mean nothing downstream —
`api/`, the demo — ever sees a distilled track without an explicit new
wiring change beyond this ticket, defeating the point), and because the
ticket's own framing ("applied to every candidate inside `optimise()`
before return") reads as describing default production behaviour, not
an opt-in. The trade-off this creates, stated plainly: **unlike R1/C1,
S1 is not a zero-behaviour-change ticket** — every existing caller of
`optimise()` that doesn't pass `distill=False` will see genuinely
different (shorter, slightly-better-scoring) tracks by default. §4 below
is where this gets addressed head-on rather than glossed over.

**REQUIRED AMENDMENT (review) — `PlanRequest.distill` crosses the API
schema-parity boundary and needs a mirror, not an exemption.**
`tests/test_api_schema_parity.py` enforces field-name parity between
every `core.optimiser.PlanRequest` field and `api/schemas.py`'s
`PlanRequestIn` (ticket B1 contract point 3) — a new core-side field with
no matching API-side field fails that test by design, the same
drift-detection mechanism ticket R1's `pack_id`/`region_pack` pair
already had to satisfy (there, a deliberately *not* 1:1-named mirror,
needing an explicit allow-list entry; here, a plain same-name,
same-default mirror, needing none). Fixed: `api/schemas.py`'s
`PlanRequestIn` gains `distill: bool = True` (identical name and default
to the core-side field — no allow-list entry needed, this is the
straightforward case the parity test's PAIRS table already handles for
every other same-named field), threaded through `api/convert.py`'s
`plan_request_from_in` (`region_pack=..., distill=body.distill,` alongside
every other passthrough field) and `api/state.py`'s `PlanJobPayload`
(the worker-process boundary also needs to carry this value across the
`ProcessPoolExecutor` submission, the same way `pace`/`comfort`/etc.
already do). Default `True` end-to-end, matching the core-side default,
so the hosted demo and every existing caller see the improvement with
*zero* client-side change — only a client that explicitly wants the raw,
undistilled track (a debugging tool, a future admin view) ever needs to
pass `distill: false`. **Test**: a real `POST /v1/plans` with
`{"distill": false, ...}` round-trips to a result whose candidate tracks
match calling `optimise()` directly with `PlanRequest(..., distill=False)`
— i.e. genuinely undistilled, not just accepted-and-ignored.

### 4. Freeze justification — a different discipline from R1/C1, stated explicitly

R1 and C1 both used "zero behaviour change" (numeric or otherwise) on the
Med as their freeze defence — `pytest -m ""` passing byte-identically was
the mechanical proof. **S1 cannot honestly claim that, and shouldn't try
to** — its entire purpose is to change the returned track (fewer
waypoints, equal-or-better numbers). The freeze defence here is
different, and needs to be stated as such rather than forced into the
same shape:

1. **The search machinery itself is untouched** (§0) — no lattice
   resolution change, no turn-rate change, no side-diversity-filter
   change, no new hard constraint, no new cost term. This is the actual
   scope boundary the pre-1.5 freeze cares about (no *new optimiser
   features*) — S1 adds a bounded, provably-non-worsening post-pass, it
   does not change what the search itself is capable of finding or how
   it scores anything during the search.
2. **Plan-*shape* assertions must hold unmodified** — every test
   asserting `side`, west/east set membership, `missed_window`
   booleans, `route_side_unreachable`/other diagnostic codes, hard-constraint
   pruning behaviour (`slamming`/`overload`/`current_exceeds_stw` still
   excludes a speed from the top-level pool), and endpoint identity
   (`track[0]`/`track[-1]`) is exactly the kind of thing that must not
   move, and by §0/§2's design, doesn't.
3. **Plan-*numeric* assertions are individually re-verified, not
   blanket-preserved** — this is the real, honest difference from R1/C1.
   `tests/test_optimiser_regression.py`'s
   `test_pure_schedule_weights_converge_to_isochrone_time_optimal` is the
   one test in the suite already flagged as worth a close look: it
   compares a (now-to-be-distilled) candidate's `duration_h` against
   `core.isochrone.time_optimal_route`'s oracle duration within a 5%
   relative tolerance — the oracle itself is **not** distilled (out of
   scope, a test-only cross-check function, §0's boundary applies to it
   too). Since distillation only ever shortens the candidate's own
   duration, and the tolerance is generous (5%) against an expected
   low-single-digit-percent effect, this is expected to keep passing —
   but "expected" is not "verified," and this is named explicitly as the
   first thing to check, unmodified, during implementation, before
   assuming anything. No other numeric assertion in the current suite
   was found tight enough to be at comparable risk (§ Verification lists
   what was checked). **Any numeric assertion that does turn out to be
   too tight is a finding to report, not silently loosen** — per the
   ticket's own explicit instruction.

### 4.1 Step 3 results (found during implementation)

**`test_pure_schedule_weights_converge_to_isochrone_time_optimal`: passed
unmodified with distillation on**, exactly as §4.3 predicted — confirmed
via both the fast suite and a full `pytest -m ""` run. No change needed.

**A real, unexpected regression was found and fixed — not the flagged
test, a different one:**
`test_bonifacio_strait_transit_is_reachable_at_current_lattice_resolution`
(ticket 0.8's own regression test, `{c.side for c in result.candidates} ==
{"W", "E"}`) failed with distillation on: the final top-3
`result.candidates` came back all-`W`, no `E`.

**Root cause, traced to ground before any fix was attempted (not assumed):**
not a bug in `distill_track` or in `_apply_distillation`'s wiring —
distillation correctly, provably-non-worsened every candidate's cost, and
the full pre-pick candidate pool (`candidates_all`) still contained
improved-cost `E`-side candidates from both the lattice search and the
`East of Corsica` corridor-DP grid, confirmed by direct inspection.
The actual cause: `optimise()`'s pre-existing `picks` selection (§3's
"missed_window/pool.sort()/picks logic", untouched by S1's own design) is
a **greedy, cost-order-dependent top-3** — take the cheapest candidate,
then walk the rest of the cost-sorted pool taking up to 2 more that are
"diverse" (different `side`, or ≥2kn speed difference) from what's
already picked, **stopping as soon as 3 are found** — it never guaranteed
every reachable side would be represented; it happened to work out that
way before. Distillation's per-side cost improvements are real but
uneven (the Bonifacio `W` corridor, which winds around the Iles Lavezzi
islets per the ticket 0.8 gotcha, had more removable kinks than `E`) —
here, that reordered the cost-sorted pool enough that a slower `W`
candidate (11kn) dropped below the `E` candidate's cost, and the greedy
loop reached and grabbed that `W` candidate (already diverse in speed
from the two faster `W` picks) before ever reaching `E`, filling all 3
slots without it.

**User's call, via explicit sign-off (not defaulted to the smaller fix):**
presented with the choice between (a) accepting this as an
always-latent, pre-existing gap and loosening the test to check the full
pool rather than the top-3 picks, or (b) fixing the `picks` algorithm
itself to guarantee side coverage — the user chose (b): treat it as a
real product gap distillation happened to expose, not a test-fixture
mismatch to work around.

**Fix, in `core/optimiser.py`'s `picks` construction (`optimise()`,
end)** — two changes, both required (the first alone broke a second,
different test):
1. After taking the single cheapest candidate (`pool[0]`, unchanged),
   walk the distinct `side` values present in `pool` in cost-sorted
   first-occurrence order; for each not yet represented in `picks`, add
   its cheapest representative (`pool` is already cost-sorted, so this is
   a plain `next()`) — up to the existing 3-slot cap. Only then does the
   original greedy cost-order fill (unchanged logic) claim any slots left
   over.
2. **Re-sort `picks` at the end**, by the same key `pool` itself was
   sorted with (`duration_h` when `missed_window`, else `score_eur`).
   Needed because change (1) inserts a side's representative out of
   `pool`'s own order (a same-side-as-`picks[0]` candidate that's
   actually cheaper/faster than the newly-added other-side representative
   can still appear later in `pool`) — without the re-sort, this broke
   `test_impossible_eta_window_flags_and_orders_fastest_first`'s
   fastest-first-ordering assertion (caught by the full suite, fixed
   before moving on).

Both `test_bonifacio_strait_transit_is_reachable_at_current_lattice_resolution`
and `test_impossible_eta_window_flags_and_orders_fastest_first` pass
after both changes; a subsequent full `pytest -m ""` run showed exactly
the one still-expected `test_api_schema_parity` failure (Step 4's job)
and nothing else. See CLAUDE.md for the permanent gotcha entry.

### 5. Bounded cost, quantified against the B1 profiling gotcha

Per candidate: each pass is O(n) `evaluate_leg` calls for the sweep
itself (§1, step 1-3, one or two evaluations per interior waypoint) plus
one more O(n) whole-track backstop evaluation — call it ~3n per pass.
Tracks are tens of waypoints (n ≈ 10-40 for a lattice candidate at the
default 6nm along-track step over a 40-200nm passage; a corridor-DP
track is a fixed ~15-20 points). Passes converge quickly in practice
(each pass either removes several waypoints or terminates; expect 2-4
passes to convergence, not dozens). `optimise()` builds a bounded number
of candidates before picking the top 3 diverse ones — 2 lattice
candidates (primary + opposite-side) plus, for the Med pack specifically,
up to `len(speeds_kn) × 2 corridors` corridor-DP results before
`best_by_key` dedup (≈ 20-24 for the shipped default speed grid) — call
it **≤ 30 candidates distilled per `optimise()` call, worst case (Med
pack; the UK pack has no legacy corridors at all, so only the 1-2 lattice
candidates there)**.

Rough worst-case total: 30 candidates × 4 passes × 3n (n≈20) ≈ **7,200
`evaluate_leg` calls** — against the ~690k calls the *search itself*
already makes per the B1 gotcha, this is **≈1% overhead**, and the true
number is expected to be well under this worst-case bound (most
candidates converge in fewer passes, and the UK pack's candidate count is
far smaller with no corridor-DP grid at all). **Stated as an estimate
here, to be measured for real during the acceptance run** (§ Acceptance)
— matching this project's own standing bias toward measuring rather than
assuming, the same discipline the B1 gotcha itself was reached with.

## Part 2 — local corridor re-search (named follow-up, not built)

Once ticket 1.5 renders the freeze verdict, a natural next step: rather
than only ever *removing* waypoints from the search's own output,
re-search a narrow band around the distilled route at finer lattice
resolution — the same adaptive-refinement mechanism ticket 0.8 already
built for Bonifacio (`core/lattice.py`'s per-stage refinement), applied
locally around wherever distillation's own hard-constraint checks came
close to failing, rather than passage-wide. This is real new search
machinery (a second, narrower search informed by the first), not a
bounded post-pass — squarely the kind of thing the freeze exists to gate,
which is why it's named here and not built. One paragraph, no further
design in this ticket.

## Implementation order

1. **`core/distill.py`**: `distill_track(...)` and its helpers (§1),
   built and unit-tested in isolation against small fabricated tracks
   with known, hand-computed geometry (a genuine collinear kink to
   remove; a kink whose shortcut clips a fabricated no-go zone, to prove
   the hard-constraint re-check actually blocks it; a speed-change point
   that must never be removed; a shallow-endpoint-adjacent leg to prove
   the exemption radius carries through) — no real geography/weather
   needed for this step, matching every other core/ module's own
   fabricated-fixture-first testing precedent.
2. **`_lattice_route_result`/`_dp_route`**: add `stw_ms_per_leg`/
   `engines_per_leg` to the returned dict (additive, existing keys
   unchanged) — `pytest -m ""` must still pass unmodified after this
   step alone, before distillation is wired in at all, isolating "did
   this additive dict change break anything" from "does distillation
   itself behave correctly."
3. **Wire `distill_track` into `optimise()`** (§3), `PlanRequest.distill:
   bool = True`. Run the **fast** suite first for a quick signal, then
   the **full** `pytest -m ""` — per §4, this is *expected* to pass
   unmodified on every shape/diagnostic/endpoint-identity assertion, and
   `test_pure_schedule_weights_converge_to_isochrone_time_optimal`
   specifically needs a close look (§4.3) even though it's expected to
   still pass. Report, don't silently fix, anything that doesn't.
4. **`api/` layer**: `PlanRequestIn.distill: bool = True` mirror
   (required amendment, above), threaded through `api/convert.py` and
   `api/state.py`'s `PlanJobPayload`; `tests/test_api_schema_parity.py`
   green with no new allow-list entry needed (a plain same-name mirror).
   Confirm `Candidate.track`'s shorter length round-trips cleanly through
   the rest of `api/convert.py`/`api/schemas.py` (a plain list, expected
   to need zero further changes — verify, don't just assume, matching
   this ticket's own stated discipline).
5. **Real acceptance run** (below): Med demo passage and UK
   Plymouth-Falmouth passage, real geography + real weather, before/after
   waypoint counts and metrics recorded in this file, R1-acceptance
   style.
6. **Docs**: ROADMAP row, CLAUDE.md gotcha entry, both below.

## Acceptance criteria

- **Every distilled candidate, for both the Med demo passage and the UK
  Plymouth-Falmouth passage (real geography + real weather)**:
  1. Total weighted score ≤ its own undistilled score (never worse).
  2. Every leg of the distilled track passes all five hard-constraint
     checks (`navigable`, `depth_ok`, no `slam_event`/`overload`, not
     `current_exceeds_stw`) at the same sampling density production
     already uses.
  3. Materially fewer waypoints than the undistilled track. Expected,
     stated here ahead of measurement per the ticket's own request:
     corridor-DP candidates (Med only) distil aggressively, likely close
     to their small number of real hand-picked corners (e.g.
     `corridor_west`'s ~20 interpolated points toward its 7 real
     corners); lattice-search candidates distil more modestly, a rough
     top-of-plan estimate of 30-60% fewer waypoints, **measured for real
     during implementation, not assumed** — real numbers recorded here
     once run.
  4. Identical `side`/`corridor_name` labelling to the undistilled
     candidate (§2).
- `pytest -m ""` green; any test needing an edit reported before touching
  it, per §4.3.
- A real `POST /v1/plans` with `distill: false` round-trips to a
  genuinely undistilled result (required amendment, above).
- `ruff check .` clean.

### Real acceptance run results (2026-07-21)

Real geography + real weather, `PlanRequest(distill=False)` vs
`PlanRequest(distill=True)` compared directly, matched pairwise by
`(corridor_name, side, speed_kn)`. 3 repeated `optimise()` calls per
config (min/median wall-clock reported — the B1 profiling gotcha's own
finding that repeated calls in a warm process don't get artificially
faster from caching applies here too, so repeats give a genuinely
independent noise estimate, not a warm-cache illusion).
`distill_track` itself was directly instrumented (not inferred from a
wall-clock difference, which is dominated by the search's own larger,
noisier cost) to isolate distillation's real overhead.

**Med: Antibes → Porto Cervo** (`data/weather/ecmwf_western_med.npz`,
`speeds_kn=(12, 14)`):

| candidate | waypoints before → after | score €, before → after |
|---|---|---|
| Lattice route, W, 14kn | 31 → 6 (**81% fewer**) | 13830.17 → 13555.35 |
| East of Corsica, E, 12kn | 14 → 7 (**50% fewer**) | 17029.52 → 17026.16 |

`optimise()` wall clock: 3.463-3.484s undistilled, 3.509-3.528s
distilled. `distill_track` itself: **0.047s/`optimise()` call** (27
calls total across 3 candidates × 3 reps) — **≈1.3% of total
`optimise()` cost**, matching §5's B1-derived ≤1% estimate.

**UK: Plymouth → Falmouth** (`data/region_packs/uk_sw/weather_uk_sw.npz`,
`speeds_kn=(10, 12)`):

| candidate | waypoints before → after | score €, before → after |
|---|---|---|
| Lattice route, side=None, 12kn | 7 → 3 (**57% fewer**) | 4190.23 → 3920.59 |

`optimise()` wall clock: 0.089-0.089s undistilled, 0.090-0.091s
distilled. `distill_track` itself: **0.0014s/`optimise()` call** — no
corridor-DP grid on this pack (no `legacy_corridors`), so only the one
lattice candidate.

**Acceptance criteria, checked against these real runs:**
1. Score never worsened — every distilled candidate's score ≤ its own
   undistilled score, verified by direct assertion in the acceptance
   script for all 3 matched pairs above (no `AssertionError`).
2. Hard-constraint feasibility — not independently re-checked in this
   script (an attempt to reconstruct per-leg speeds from the public
   `Candidate.speed_kn` average would have been wrong for multi-speed
   candidates, since that field doesn't expose the per-leg profile); this
   is instead provable by construction (§1: every kept leg is either an
   original search-verified leg, or a shortcut that passed
   `_leg_hard_constraints_ok` before being accepted) and directly covered
   by `tests/test_distill.py`'s
   `test_a_shortcut_that_clips_a_hazard_is_blocked` and
   `test_shallow_endpoint_exemption_radius_carries_through_a_shortcut`.
3. Materially fewer waypoints — **50-81%** fewer across all 3 matched
   candidates, at or above the plan's own top-of-plan 30-60% estimate for
   lattice candidates, and the corridor-DP candidate (`East of Corsica`)
   distilled less aggressively than the plan's "close to its ~7 real
   corners" guess (14→7, not down near 7-8) — worth noting as a real,
   moderate miss on that specific sub-estimate, not a correctness
   problem: `corridor_west`'s own DP output for this exact scenario
   wasn't in the matched pool this run (present only in one of the two
   configs' pools, see the `common_keys` filter), so it wasn't measured
   directly here.
4. `side`/`corridor_name` identical between distilled and undistilled for
   every matched candidate — verified by direct assertion (no
   `AssertionError`).

*(Historical placeholder, superseded by the real numbers above — kept for
context on what was originally expected before measurement:)*

*(Real before/after numbers — waypoint count, distance, fuel, duration,
wall-clock overhead — to be recorded here once the implementation and
acceptance run happen, matching R1's/C1's own "real run recorded in the
plan file" precedent. Not fabricated ahead of a real run.)*

## Scope cuts (explicit)

- **Part 2** (local corridor re-search at finer resolution) — named
  follow-up only, deferred until after ticket 1.5's freeze verdict, not
  built here.
- **Any lattice/step-size change** — `core/lattice.py`'s
  `along_track_step_nm`/`cross_track_step_nm`/adaptive-refinement
  parameters are untouched; distillation works on whatever polyline the
  existing search produces, never asks it to produce a different one.
- **Any search-machinery change** — A*/DP, the heuristic, the isochrone
  pre-pass, side-diversity filtering: all untouched (§0).
- **Demo/UI work** — fewer waypoints improves the rendered track for
  free, through the existing `api/convert.py` plumbing; no
  `prototype/`-side change is this ticket's job.
- **Baseline distillation** — the do-nothing reference stays exactly
  what "do nothing" produces (§0).
- **Merging across a genuine speed change** (trying multiple candidate
  speeds at a merge point to widen what's removable) — deliberately
  out of scope, would blur this ticket's "pure geometry cleanup" boundary
  into a small re-optimisation; named as Part 2-adjacent future work if
  it ever proves worth doing, not sketched further here.

## Judgment calls flagged for sign-off (collected)

1. **Pass-level accept/reject for the whole-track backstop**, not a
   finer per-merge rollback (§1) — recommended: simpler, still correct,
   O(n) not O(n²) per pass; the alternative (stricter, costlier) is named
   as a fallback if this ever proves too coarse.
2. **Same-`(stw_ms, active_engines)`-on-both-sides precondition** for
   attempting a removal at all (§1) — recommended: keeps this a pure
   geometry pass, not a re-optimisation; explicitly limits how much a
   speed-varying lattice candidate can distil (corridor-DP candidates are
   unaffected by this limit, being single-speed already).
3. **`PlanRequest.distill: bool = True`** (default on, an escape hatch to
   turn off, not the default rollout mechanism) (§3) — recommended,
   with the explicit consequence (§4) that this is not a zero-behaviour-
   change ticket the way R1/C1 were, stated plainly rather than glossed
   over.

## ROADMAP row text (proposed)

Suggest placing next to C1 in the "Beyond Phase 2" table (both are
optimiser-adjacent work under the pre-1.5 freeze, both landed around the
same time):

> **S1 — Route distillation** | A bounded, provably-non-worsening
> post-search pass (`core/distill.py`) that simplifies each candidate's
> lattice/corridor polyline down to the deliberate waypoints a navigator
> would actually write down — quantisation kinks removed via the exact
> same `evaluate_leg` hard-constraint/cost machinery the search itself
> trusts, re-verified on every proposed shortcut (the Bonifacio
> scattered-islet lesson, ticket 0.8, applied at a different scale), never
> the search machinery itself. Search-machinery-untouched freeze defence
> (no lattice/turn-rate/heuristic/side-diversity change) — but unlike
> R1/C1, deliberately *not* a zero-behaviour-change ticket: plan-shape
> assertions (side, window feasibility, hard-constraint pruning, endpoint
> identity) hold unmodified by design; plan-numeric assertions are
> individually re-verified against the (expected, bounded, real) small
> improvement this pass actually makes. Full design and the real
> before/after measurements: `docs/plans/ticket-S1.md`. | Optimiser-
> adjacent, not a new optimiser feature — a bounded post-pass over
> already-chosen candidates, quantified at ~1% overhead against the B1
> profiling gotcha's own ~690k-call search cost. |

## CLAUDE.md gotcha entries (proposed, to add on completion)

- A new gotcha recording: the freeze-defence distinction from R1/C1
  (§4), the same-speed-precondition design decision and why (§1), the
  pass-level-backstop-not-per-merge-rollback choice and why (§1), the
  `test_pure_schedule_weights_converge_to_isochrone_time_optimal` check
  performed during implementation and its real outcome, and the real
  measured overhead/waypoint-reduction numbers from the acceptance run
  (§ Acceptance) once real.

## Verification

- `pytest -m ""` green after step 2 (additive dict keys only, before
  distillation is wired in) — isolates that change from distillation's
  own correctness.
- `pytest -m ""` green after step 3 (distillation wired in) — expected
  unmodified; `test_pure_schedule_weights_converge_to_isochrone_time_optimal`
  specifically checked and its outcome recorded, not assumed.
- `ruff check .` clean throughout.
- The Med + UK acceptance runs (§ Acceptance) are genuine `optimise()`
  calls against real geography/weather, not mocked stand-ins — matching
  this project's standing bias toward real verification (B7's live CDS
  run, R1's live WPI/UK ingest, C1's live CMEMS research).
- Tests already read directly, confirmed relevant and expected to hold,
  during this planning pass (not exhaustive of the whole suite, but the
  ones a track-shape-changing ticket most plausibly threatens):
  `test_every_returned_track_is_navigable_at_fine_resolution` (the
  central guard against a distillation bug allowing a shortcut through
  land — finer sampling than production's own, independent check),
  `test_mistral_high_comfort_routes_lee_side`,
  `test_calm_corridors_converge_and_speed_varies_by_pace`,
  `test_impossible_eta_window_flags_and_orders_fastest_first`,
  `test_charter_window_reflects_vessel_envelope_not_an_arbitrary_number`,
  `test_bonifacio_strait_transit_is_reachable_at_current_lattice_resolution`,
  `test_slamming_speed_is_pruned_outright_not_downranked`,
  `test_a5_no_amount_of_weighting_routes_through_a_nogo_zone`,
  `test_shallow_port_is_reachable_under_depth_enforcement`,
  `test_shallow_anchorage_is_reachable_under_depth_enforcement`.

### Critical files for implementation

- `core/distill.py` (new)
- `core/optimiser.py` (`_lattice_route_result`/`_dp_route` dict additions,
  `optimise()` wiring, `PlanRequest.distill`)
- `tests/test_optimiser_regression.py` (the one test flagged for a close
  look, §4.3 — not expected to need editing, but must be checked)
- `api/schemas.py` (`PlanRequestIn.distill` mirror, required amendment)
- `api/convert.py` (`plan_request_from_in` passthrough)
- `api/state.py` (`PlanJobPayload.distill`, worker-process boundary)
- `tests/test_api_schema_parity.py` (no new allow-list entry needed, but
  must stay green with the new field present on both sides)
