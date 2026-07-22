# Ticket N1 — Non-finite leg cost becomes an explicit, diagnosable prune

**Status: done (2026-07-22).** Approved as written, including the
`_dp_route` finding (verified against the code — line 749's `best is
None` unconditional accept — a real correctness fix, not just
diagnosability), plus one discretionary tweak: the `weather_data_gap`
diagnostic trigger fires on `missed_window is True` as well as an empty
pool. Implemented in the plan's own order, all five steps green at every
checkpoint (`pytest -m ""`: 483 after step 3, 489 after step 4, both
unmodified-plus-new-tests, `ruff check .` clean throughout). One real,
necessary deviation from the plan found during implementation: `PruneStats`
lives in `core/legs.py`, not `core/optimiser.py` as originally specified
— `core/optimiser.py` imports *from* `core/isochrone.py`, so a
`core/optimiser.py`-hosted `PruneStats` would have created a circular
import the moment isochrone's own functions needed to accept the same
type; `core/legs.py` is the natural shared home (both `core/optimiser.py`
and `core/isochrone.py` already depend on it one-way). See CLAUDE.md's
gotcha for the full trace, findings, and test-construction notes.

**Plan only — no implementation in this pass.** L1's own named fast-follow
(`docs/plans/ticket-L1.md` §1c), elevated at L1's review to an immediate
ticket rather than a someday item: R1's arbitrary-endpoint routing and
per-vessel favourites are live product surface, so a real user-placed
endpoint near a coarse weather grid's fully-masked stencil hits this today,
silently, as an unexplained "no feasible route."

## Context

`docs/plans/ticket-L1.md` §1c found, empirically, that a fully-masked
weather-interpolation stencil (`GriddedWeatherField.sample()`'s
`bilinear_masked`, correctly returning `NaN` when *every* corner of the
query stencil is missing data — verified directly against
`core/gridding.py`'s source, not assumed) produces a `LegResult` with
`fuel_kg=nan`/`comfort=nan`/`max_hs=nan`. That NaN then **silently
vanishes** from `core/optimiser.py`'s `_lattice_search`: the Dijkstra/A*
state update `if new_g < best_node.get(new_state, ...).g:` is always
`False` when `new_g` is NaN (`nan < x` is `False` for every `x` in
Python), so the edge is dropped with zero diagnostic — not pruned like
`navigable`/`depth_ok`/`slam_event`/`overload`/`current_exceeds_stw`
(five visible, intentional hard constraints), just invisibly absent.

## Verifying the freeze-compatibility argument against the code, not assuming it

L1's own scope sketch claimed: *"a silently-dropped NaN edge and an
explicitly-pruned edge produce identical search results — this ticket
changes diagnosability, not reachability."* Checked directly against all
three-ish call sites, this holds for two of them and is **wrong for the
third**:

1. **`_lattice_search`** (`core/optimiser.py`): confirmed true. `new_g =
   node.g + leg_cost` where `leg_cost` is NaN makes `new_g` NaN;
   `new_g < best_node.get(new_state, default_inf).g` is `False`
   regardless of the default, so the state is never added to `best_node`
   or pushed to the heap — bit-for-bit the same outcome as an explicit
   `continue` at the top of the loop (where the five existing hard
   constraints already `continue`). A NaN-cost edge today is
   *functionally* pruned; it just isn't *visibly* pruned.
2. **`core/isochrone.py`'s `_best_feasible_duration_h`**: confirmed safe,
   for a more specific and interesting reason than "it never reads
   fuel/comfort" (L1's own note) — **`duration_h` itself cannot become
   NaN**, only `+inf`, traced all the way through
   `core.units.resolve_ground_speed_ms`. When `current_u_ms`/
   `current_v_ms` are NaN (the *same* `bilinear_masked` all-corners-NaN
   case, on the current field instead of the wave field), `remainder =
   stw_ms**2 - cross_track_water**2` becomes NaN, and `if remainder < 0:`
   — the guard meant to catch "current exceeds vessel speed" and raise
   `ValueError` — is **also** silently bypassed (`NaN < 0` is `False`,
   same failure shape as the search's own comparison bug, one level
   down). `math.sqrt(NaN)` doesn't raise either, so
   `resolve_ground_speed_ms` returns NaN with no exception. Back in
   `leg_navigation`, `ground_speed_ms > 0` is then `False` for NaN, so
   `duration_h` falls to the `else: float("inf")` branch — **not NaN**.
   `+inf` is well-ordered (`x < inf` behaves correctly for every finite
   `x`), so `_best_feasible_duration_h`'s `if best is None or
   leg.duration_h < best:` handles it exactly like a genuine
   too-slow-to-be-feasible leg — correctly, if for the wrong stated
   reason (this leg isn't *physically* infeasible, its current data is
   just missing — `current_exceeds_stw` stays `False`, a real but
   secondary mislabelling, not fixed here, see Scope cuts).
3. **`_dp_route`** (`core/optimiser.py`): **the freeze-compatibility
   claim is false here.** Its acceptance rule is `if best is None or
   cost < best["cost"]:` — when `best is None` (the first candidate
   tried for a given `(i, k)` DP cell), a NaN-cost edge is **accepted
   unconditionally**, the `cost < best["cost"]` comparison never runs.
   Once accepted, that NaN-cost `best` entry can **never be replaced** —
   `finite_cost < nan` is `False` for every finite cost, so a later,
   genuinely better candidate for the same cell loses to a state that
   was never actually comparable in the first place. Order-dependent
   (whichever `pk` neighbour happens to be tried first for a given `k`),
   silent, and — unlike `_lattice_search` — can propagate all the way to
   `end["cost"]` and out through `Candidate.score_eur`, i.e. **a real,
   already-shippable path to returning a `NaN`-scored candidate to a
   caller**, not merely a dropped option. This is a genuine bug beyond
   "make an already-safe outcome diagnosable" — `_dp_route` only matters
   for the Med pack today (`legacy_corridors`, R1), but N1's fix corrects
   it regardless of which pack calls it.

**Conclusion**: the reachability-preserving argument holds for
`_lattice_search`/isochrone (genuinely just a diagnosability fix there),
and does **not** hold for `_dp_route` (a real correctness fix, not just a
diagnosability one). Both get the identical fix below regardless — the
point of a uniform, LegResult-level guard is that it's correct either way
without needing to reason about each call site's own comparison
semantics separately.

## Design

### 1. `LegResult.non_finite_cost: bool = False` — the sixth hard-constraint flag

Computed once in `evaluate_leg` (`core/legs.py`), the single shared place
this module's own docstring already commits to for hard-constraint
semantics — same precedent as C1's `current_exceeds_stw`. Checks
`math.isnan(x)` — **`isnan`, not `isfinite`** — on `duration_h`,
`fuel_kg`, `comfort`, `wear`, and `max_hs`:

```python
non_finite_cost = any(
    math.isnan(x) for x in (duration_h, fuel_kg, comfort, wear, max_hs)
)
```

**Why `isnan` specifically, not `isfinite` (a real precision point, not a
style preference)**: `duration_h` legitimately becomes `+inf` under two
already-correct, already-shipped codepaths (`current_exceeds_stw`'s own
`ValueError` catch, and `ground_speed_ms <= 0`) — and `+inf` is
well-ordered under `<`, so every existing comparison
(`_lattice_search`'s `new_g < best.g`, `_dp_route`'s `cost <
best["cost"]`, isochrone's `duration_h < best`) already handles it
correctly today, including in `_dp_route`, which — unlike NaN — never
gets *stuck* on an `inf` value (a later finite cost always beats it,
`finite < inf` is `True`). Checking `isfinite` would also flag these
already-safe `inf` cases, changing real, currently-correct behaviour for
no reason; `isnan` targets exactly, and only, the newly-found problem.
`max_hs` is included even though it's not part of the cost formula
`_lattice_search`/`_dp_route` actually sum — a NaN there can still poison
`SearchNode.max_hs`/`Candidate.max_hs_m` via `max(node.max_hs, leg.max_hs)`
(Python's `max()` behaviour with a NaN operand isn't a relied-upon
contract), so it's guarded by the same flag rather than left as a second,
separate risk.

**Why this doesn't need to separately audit `slam_event`/`overload`
under NaN weather inputs**: it plausibly could be unreliable too
(`weather.hs_m > wp.slamming_hs_threshold_m` is `False` for NaN `hs_m`,
same "NaN silently reads as the falsy branch" shape) — but it doesn't
matter: a `non_finite_cost=True` leg is excluded outright regardless of
what its other flags say, the same way a `navigable=False` leg's
`overload` value is never inspected either. One flag, checked once, makes
every other field's reliability under a NaN input moot.

### 2. All three-ish search call sites gain the same `or leg.non_finite_cost` clause

Exactly the same shape as C1's `current_exceeds_stw` rollout — additive
to each site's existing condition, nothing restructured:

- `core/optimiser.py`'s `_lattice_search`: `if not (leg.navigable and
  leg.depth_ok) or leg.slam_event or leg.overload or
  leg.current_exceeds_stw or leg.non_finite_cost: continue`
- `core/optimiser.py`'s `_dp_route`: the existing three-part check
  (`navigable`/`depth_ok`, `slam_event`/`overload`,
  `current_exceeds_stw`, each already its own `if ...: continue`) gains
  a fourth: `if leg.non_finite_cost: continue` — this is the line that
  actually fixes the real bug (§ above): the NaN-cost edge is now
  `continue`d *before* the `best is None` accept-unconditionally branch
  ever sees it, so it can never enter `best[k]` in the first place.
- `core/isochrone.py`'s `_best_feasible_duration_h`: same four-part
  `if ...: continue` gains the fifth condition. Expected to rarely if
  ever fire in practice (§ above: `duration_h` itself can't be NaN), kept
  for uniformity and because a `LegResult` with NaN `fuel_kg`/`comfort`
  arriving here is just as untrustworthy as one `_lattice_search` sees,
  even though isochrone doesn't read those fields today.

### 3. `PruneDiagnostic(code="weather_data_gap", ...)` — surfacing the why

A small, explicit counter, **not** deep instrumentation of the hot search
loop. New `core/optimiser.py` dataclass:

```python
@dataclass
class PruneStats:
    non_finite_cost_count: int = 0
```

`optimise()` constructs exactly one `PruneStats()` per call and threads it
as an optional parameter (`prune_stats: PruneStats | None = None`,
default `None` so every existing caller/test of `_lattice_search`/
`_dp_route`/`_best_feasible_duration_h` — none of which pass this today —
is unaffected) into every `_lattice_route_result`/`_dp_route` call inside
`optimise()`'s own body (primary lattice, opposite-side lattice, and each
`_dp_route` call in the corridor-DP grid loop). Each call site increments
it (`if prune_stats is not None: prune_stats.non_finite_cost_count += 1`)
in the same branch that now `continue`s on `leg.non_finite_cost` — one
extra line next to a check that's already there, not a new pass over the
data. Overhead: one integer increment behind an `is not None` guard, on a
codepath that already samples real weather per edge (the B1 profiling
gotcha's own ~690k-call dominant cost) — immaterial.

**Trigger, kept simple rather than trying to attribute precisely which
search attempt failed because of it** (considered and rejected: mirroring
`route_side_unreachable`'s own per-search-attempt scoping would need
`PruneStats` threaded back out of a failed `_lattice_search`/`_dp_route`
call specifically, not just a shared running total — real plumbing for
a "small ticket" to take on, and per-attempt attribution isn't needed for
the diagnostic to already be useful). At the end of `optimise()`, once
`missed_window`/`candidates_all`/`pool` are known: if
`prune_stats.non_finite_cost_count > 0` **and** (the request ended up
with zero feasible candidates in the pool **or** `missed_window is
True`), append the diagnostic — a stricter trigger than "any prune
occurred at all" (most `non_finite_cost` prunes will be harmless,
alternative-route-existed cases that never need surfacing), but not
stricter than it should be: **review addition** — an empty-pool-only
trigger would miss the case where a data gap prunes away the *fast*
route specifically while a slower, still-feasible one survives, which
shows up as a real `missed_window` (the caller's ETA window can't be
met) rather than an empty pool, and deserves the same explanation:

```python
if prune_stats.non_finite_cost_count > 0 and (not pool or missed_window):
    diagnostics.append(
        PruneDiagnostic(
            code="weather_data_gap",
            message=(
                f"{prune_stats.non_finite_cost_count} leg(s) had unusable weather "
                "data (a gap in the source grid near the route) and were excluded "
                "from the search — this may be why no feasible route was found."
            ),
        )
    )
```

Not scoped to `_baseline_route`'s own internal `_lattice_route_result`
call — the baseline already raises a distinct, explicit `RuntimeError`
with its own clear message on infeasibility; a second diagnostic
mechanism there would be redundant. The underlying prune fix (§1/§2)
still applies to the baseline's search regardless, since it's baked into
`evaluate_leg`/`_lattice_search` unconditionally, independent of whether
`prune_stats` tracking is wired up for that particular call.

## Freeze compatibility, restated precisely

- **`_lattice_search`/isochrone**: reachability-identical, verified
  above — this really is a pure diagnosability change for these two.
- **`_dp_route`**: **not** reachability-identical — this is the one real,
  intentional behaviour change in the ticket, and a strict improvement
  (a NaN-cost edge can no longer poison a DP cell or leak a NaN
  `score_eur` to a `Candidate`). Stated plainly, not glossed over as if
  it fit the same "diagnosability only" framing as the other two.
- **No new cost terms or search features** — `non_finite_cost` is a hard
  constraint, exactly the same kind the five existing ones already are;
  the weighted-score formula itself is untouched.
- **`pytest -m ""` expected green, unmodified**, for every test that
  doesn't specifically fabricate a NaN-producing weather fixture.
  **Checked directly during planning, not left as an assumption**:
  `grep -rn "nan" tests/` finds real NaN-fixture coverage, but every hit
  is at the weather-field/gridding layer (`test_weather.py`,
  `test_gridding.py`, ingest-layer tests validating `bilinear`/
  `bilinear_masked`/land-masking behaviour itself) — **none** exercise
  `evaluate_leg`, `_lattice_search`, `_dp_route`, or
  `core/isochrone.py` with a NaN-producing weather input. N1 is
  additive at the leg-cost/search layer with no existing test to
  reconcile against.

## Tests

- `tests/test_legs.py`: a fabricated `WeatherField` test double whose
  `.sample()` returns a `WeatherSample` with `hs_m=float("nan")` (or
  `current_u_ms`/`current_v_ms` NaN, for the second pathway) at a
  specific query point; assert `evaluate_leg(...).non_finite_cost is
  True` and that a leg evaluated *away* from that point (finite weather)
  has `non_finite_cost is False`. A companion test asserting `duration_h`
  specifically comes back `+inf`, not `nan`, under NaN current — the
  direct regression test for the "verified against the code, not
  assumed" finding above.
- `tests/test_optimiser_constraints.py` (matching the existing A5/B5/C1
  pattern): a fabricated weather fixture with a NaN patch at one specific
  lattice point on an otherwise-navigable passage; assert the leg through
  that exact point is excluded (never appears in any candidate's track)
  while a route through the *rest* of the open lattice is still found —
  the `_lattice_search`-side regression test.
- **The `_dp_route` regression test — the one that actually proves a real
  bug is fixed, not just made visible**: construct a small corridor where
  a NaN-cost edge is the *first* candidate considered for some `(i, k)`
  cell (matching today's real failure mode) and a strictly better
  finite-cost edge is considered *after* it. Assert the finite candidate
  wins — i.e., that this test would have **failed** against the
  pre-N1 code (`best is None` accepting the NaN edge unconditionally,
  then never replaced). This is the test that demonstrates §3's freeze-
  compatibility finding empirically, not just via code-reading.
- `tests/test_optimiser_diagnostics.py` (new): three end-to-end
  `optimise()` tests. (1) A fabricated weather field whose NaN patch is
  wide enough to force every route into a real detour, combined with a
  `latest_arrival_h` tighter than even the detoured duration — the
  destination drops out of `reachable` entirely, `candidates_all`/`pool`
  end up empty; assert `weather_data_gap` fires. **A real implementation
  finding, not glossed over**: this scenario necessarily has
  `missed_window is True` too — isolating "empty pool *without*
  `missed_window`" turned out not to be reliably constructible.
  `_baseline_route` shares `baseline_reachable` with the candidate
  search's own `reachable` whenever no window is set (both derived from
  the same `arrival_times_within` pass), so whatever poisons the
  candidate search into finding nothing also poisons baseline into
  finding nothing, and baseline's own `RuntimeError` fires before
  `optimise()` ever returns a `PlanResult` to inspect — the only way
  `baseline_reachable` genuinely diverges from `reachable` (a generous
  `DEFAULT_HORIZON_H`, independent of a tight request window) is exactly
  the `missed_window` case. Not a test weakness — the `or` is satisfied
  either way, and the test still faithfully exercises the `not pool` leg.
  (2) The review-added trigger leg, isolated cleanly instead: a harmless
  prune (real alternative routes exist, `pool` is non-empty) combined
  with an *independently* tight `latest_arrival_h` (the ordinary
  `eta_window_infeasible` mechanism, unrelated to the poison) — proves
  the `or missed_window` leg fires on a genuinely non-empty pool, which
  is what the review addition was for; the trigger condition itself
  doesn't require the window miss to be *caused by* the same prune, only
  that both are true, so this is a faithful test of the actual code, not
  a weaker substitute. (3) A companion test: the same harmless poison,
  no window pressure — candidates are found and `weather_data_gap` does
  *not* fire (the "most `non_finite_cost` prunes are harmless, don't
  over-report" design point, § above).
- `pytest -m ""` full suite green otherwise unmodified — the standard
  checkpoint every prior ticket in this project has held itself to.

## Scope cuts (explicit)

- **Fixing the weather-grid coverage gap itself at ingest time** (a finer
  UK grid, a coverage-completeness check in `ingest/fetch_grib_nomads.py`,
  etc.) — out of scope; N1 makes the *existing* gap diagnosable and
  correctly pruned, it doesn't make the gap smaller or go away.
- **Self-scaling `along_track_step_nm`** — L1's own named follow-up,
  unblocked by N1 (a finer along-track step can now trigger this
  codepath without silently corrupting a search or a `Candidate`'s
  score), but not built here; a separate future ticket's job.
- **Correctly attributing the `current`-NaN-via-`resolve_ground_speed_ms`
  case as its own distinct reason** (rather than an unlabelled
  `duration_h=inf`, `current_exceeds_stw=False`) — a real, secondary
  mislabelling found while verifying the freeze-compatibility argument
  (§ above), not fixed here: it's already safely handled by the general
  `non_finite_cost` guard's `fuel_kg`/`comfort`/`wear`/`max_hs` checks in
  practice (those are essentially always NaN too when weather data is
  missing at a point, since `hs_m` and current come from the same masked
  stencil query), so the *practical* gap this ticket cares about is
  already closed; a cleaner, separately-labelled reason is a polish item,
  not a correctness one.
- **Per-search-attempt diagnostic attribution** (mirroring
  `route_side_unreachable`'s own scoping exactly) — considered, rejected
  as disproportionate plumbing for this ticket's size; the single shared
  `PruneStats` counter is simpler and already closes the real gap (an API
  caller learns *that* a data gap likely caused the failure, which is the
  stated goal — not *which specific search attempt* hit it first).
- **Any cost-model or new search-feature change** — `non_finite_cost` is
  a hard constraint of the same kind the five existing ones already are;
  nothing about the weighted-score formula, the lattice, or the search
  algorithm changes.

## ROADMAP row text (proposed)

> **N1 — Non-finite leg cost becomes an explicit prune** | A NaN leg cost
> (from a fully-masked weather-grid stencil near a coastline — a real,
> live risk given R1's arbitrary endpoints/favourites, not just a bench
> scenario) used to vanish silently from the search (`nan < x` is always
> `False` in Python) with zero diagnostic; `_dp_route` had a worse,
> related bug where a NaN-cost edge could be accepted and then never
> replaced, potentially leaking a `NaN` `score_eur` to a `Candidate`.
> `LegResult.non_finite_cost` (checked via `math.isnan`, not `isfinite`
> — `+inf` was already handled correctly everywhere and needed no
> change) makes this a sixth explicit hard constraint, the same shape as
> C1's `current_exceeds_stw`; a new `PruneDiagnostic(code=
> "weather_data_gap")` surfaces it when it's likely the reason a request
> came back with nothing. Found and elevated by `docs/plans/ticket-L1.md`
> §1c. Full design and the real fix trace: `docs/plans/ticket-N1.md`. |
> Small, targeted — one new hard-constraint flag following an established
> pattern, one real bug fix in `_dp_route`, one new diagnostic code. |

## CLAUDE.md gotcha entry (proposed, to add on completion)

- A new gotcha recording: the exact freeze-compatibility trace (why it
  holds for `_lattice_search`/isochrone and doesn't for `_dp_route`, and
  what `_dp_route`'s real, distinct bug was), the `isnan`-not-`isfinite`
  precision point (`+inf` already handled correctly everywhere), and the
  `resolve_ground_speed_ms`/`resolve_course_to_steer_deg` NaN-bypasses-
  the-`remainder < 0`-guard finding (a second, related silent-NaN-`False`
  shape one level below the search itself) as background for anyone next
  touching weather-grid resolution, `evaluate_leg`, or the current-triangle
  math.

## Implementation order

1. `core/legs.py`: `LegResult.non_finite_cost`, computed in `evaluate_leg`.
   Unit tests (`tests/test_legs.py`) first, in isolation, per this
   project's own standing convention.
2. `core/optimiser.py`'s `_lattice_search`/`_dp_route`, `core/isochrone.py`'s
   `_best_feasible_duration_h`: add the `or leg.non_finite_cost` /
   `if leg.non_finite_cost: continue` clause. Run `pytest -m ""` —
   expected green, unmodified, before `PruneStats` wiring begins
   (isolates "does the hard-constraint fix alone change anything" from
   "does the diagnostic wiring behave correctly," the same per-step
   isolation discipline S1/L1 both held to).
3. `PruneStats` + `weather_data_gap` diagnostic wiring in `optimise()`.
   `pytest -m ""` again.
4. Tests: the `_dp_route` regression test (the one proving a real bug
   fix, not just visibility), the end-to-end `optimise()` diagnostic
   tests.
5. Docs: ROADMAP row, CLAUDE.md gotcha (both above).

## Verification

- `pytest -m ""` green at each step above, `ruff check .` clean
  throughout.
- The `_dp_route` regression test specifically: confirm it fails against
  a deliberately-reverted pre-N1 version of `_dp_route` (a real check
  that the test exercises the bug, not just the fix's own code path) —
  matching this project's standing bias toward verifying a regression
  test actually regresses before trusting it.
- `grep -rn "nan" tests/` already checked during planning (§ Freeze
  compatibility) — no existing test relies on today's silent-NaN-drop
  behaviour at the leg-cost/search layer.

### Critical files for implementation

- `core/legs.py` (`LegResult.non_finite_cost`, `evaluate_leg`)
- `core/optimiser.py` (`_lattice_search`, `_dp_route`, `PruneStats`,
  `optimise()`'s diagnostic wiring)
- `core/isochrone.py` (`_best_feasible_duration_h`)
- `tests/test_legs.py`, `tests/test_optimiser_constraints.py`, a new or
  existing end-to-end diagnostics test file
