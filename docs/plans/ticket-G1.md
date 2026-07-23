# Ticket G1 — Spatial indexing for RealGeography point queries

## Part 1 result, stated up front: **the premise does not hold — recommend stopping here, no Part 2**

Per this ticket's own instruction ("if polygon scanning turns out NOT to
be dominant, say so and stop — the ticket's premise fails and that's a
finding, not a failure"), Part 1 was run to completion, three
independent ways, and all three agree: `RealGeography.is_land_precise`/
`is_navigable` do not dominate `optimise()`'s cost on `med_full`, and
the originally-observed "24.4s vs 6s" comparison that motivated this
ticket was not actually comparing like with like.

### 1a. Code-level check — the fast path is already engaged

`grep`-confirmed: `is_land_precise` (the O(polygons) ray-cast) is called
from exactly two places in this codebase — `ingest/grib_common.py`'s
`mask_land_as_missing`/`coastal_fill_mask` (ingest-time, not part of any
`optimise()` call) and `core/geography.py`'s own `is_land` as a fallback
**only when `self._land_mask is None`**. Confirmed directly against the
loaded `med_full` pack: `geography._land_mask is None` → `False` — the
rasterised mask (`fetch_gshhg.py`'s own `land_mask` output, written for
every pack including `med_full`/`caribbean`) is present, so `is_land`
takes its intended O(1) `core.gridding.nearest()` array-index path, not
the polygon scan. `is_navigable` (`core/legs.py`'s `_navigable_along_leg`,
the actual per-leg-sample call site, `@lru_cache`'d per unique `(p, q)`
edge) calls `is_land`, not `is_land_precise` — the fast path was already
the one in use, exactly as `core/legs.py`'s own existing comment says it
must be ("Distance-based sampling is only practical because
`RealGeography.is_land` is a rasterised O(1) lookup... not the
O(polygons) ray-cast it would have been before that").

### 1b. Real `cProfile` of a `med_full` `optimise()` call

Antibes→Porto Cervo through `med_full`, default (11-value) speed grid,
`region_pack=pack`, real weather/geography:

```
246,412,405 function calls in 42.06s (cProfile overhead included --
                                        real wall-clock for this exact
                                        call, no profiler, was 15.33s
                                        -- see 1c)

ncalls      cumtime  function
521,452     28.833s  core/weather.py:269 sample()
4,693,068   25.788s  core/weather.py:278 at()
7,300,328   20.542s  core/gridding.py:50 bilinear_masked()
485,671      1.323s  core/geography.py:429 is_navigable()   <- 3% of total
   (is_land_precise: does not appear in the profile at all -- zero calls)
```

`is_navigable` costs **~3% of total runtime**, using the intended fast
path. Weather sampling (`GriddedWeatherField.sample`/`.at`/
`bilinear_masked`) is **~85%+** — this is not a new finding, it's a
direct reconfirmation of the pre-existing B1 profiling gotcha
(`CLAUDE.md`: "`optimise()` cost is dominated by uncached weather
sampling, not lattice-building"), which already correctly named this
exact mechanism.

### 1c. The real explanation for the original "24.4s vs 6s" comparison — a speed-grid-size confound, not a pack-size effect

The two numbers being compared in `docs/plans/ticket-R2-lite.md` §7 were
never controlled for the same request: the "~6s" reference for the small
`med` pack came from ticket L2's own acceptance script, which passed
`speeds_kn=(12.0, 14.0)` explicitly (2 speeds); R2-lite's own med_full
validation passed no explicit `speeds_kn`, defaulting to
`feasible_speeds_kn(vessel)` — **11 speeds** (6.0 through 16.0kn). Total
weather-sample volume scales with `(distinct lattice edges) ×
(speeds × engine configs)`, so an 11-speed run doing ~5.5x the work of a
2-speed run on an unrelated axis was always going to look slower,
independent of which pack it ran against.

Controlling for this directly, same passage, same vessel, real
geography/weather for both packs:

| speed grid | `med` (small, 3.45°×3.25° bbox) | `med_full` (34°×16° bbox, 3,340 polygons) |
|---|---|---|
| `(12.0, 14.0)` — 2 speeds | 5.79s | **3.92s** |
| default — 11 speeds | 20.76s | **15.33s** |

**`med_full` is not slower than the small `med` pack for the identical
passage at either speed-grid size — if anything, faster in both real
runs here** (plausibly run-to-run system-load/caching variance, not
investigated further since the direction alone already refutes the
premise). The polygon count (3,340 vs a much smaller committed set) and
bbox area (34°×16° vs 3.45°×3.25°) demonstrably do not drive `optimise()`
cost in either direction here.

### What this means for the still-open "live production run took 48s" data point

Not explained by this investigation, and **deliberately not chased
further under this ticket** — 1a/1b/1c together already rule out
geography/polygon-count as the cause (whatever explains a real 48s
production run, it is not `is_land_precise`/`is_navigable`, both directly
measured here). The most likely candidate, consistent with this
project's own prior findings, is worker-pool concurrency/contention on
the production VM (multiple real jobs sharing a small core count — the
same class of effect `CLAUDE.md`'s B2 gotcha already named as "a real
risk the server-side fix doesn't address" when discussing job-queue
soft-deadline handling) or simply a larger effective request (a wider
speed grid, a tighter ETA window forcing extra search, or corridor-DP
attempts) than the controlled local comparison above used. Worth a
separate, real investigation if it recurs or gets worse — out of scope
here, since it isn't the geography-indexing question this ticket was
about.

## Recommendation: do not build Part 2

Building a spatial index over `RealGeography`'s loaded polygons would be
real, working code for a problem the data says does not exist today.
Concretely weighed against this ticket's own acceptance criteria:

- **(4)'s own explicit memory-cost concern is the deciding factor**: the
  production VM is a 4GB box already loading four packs per worker: an
  index (per-polygon bounding boxes + a uniform grid bucketing structure,
  duplicated per loaded `RealGeography` instance, per worker process)
  would be a real, permanent memory cost added for a code path that
  measures at ~3% of `optimise()`'s own runtime. That trade doesn't
  clear the bar the ticket itself set.
- The bit-identical-results acceptance bar (1) and the Bonifacio-suite
  regression bar (2) are real, achievable engineering asks — this isn't
  a "too hard" call. It's a "not needed" call, on the actual measured
  numbers.
- If a *future* pack ships with many real no-go/TSS zones (`is_nogo`
  does the identical unconditional `any(_point_in_polygon(...) for poly
  in self._nogo_polygons)` linear scan `is_land_precise` does, and *is*
  on `is_navigable`'s hot path, unlike `is_land_precise`) — today's
  packs all have zero (`med_full`, `caribbean`) or a small handful
  (`med`, `uk_sw`) of no-go zones, so this is currently free, but is the
  one part of this ticket's premise that could become real later. Named
  here, not built — revisit if a pack's own no-go-zone count ever
  becomes large enough to show up in a profile the way weather sampling
  does today.

## Correction to a prior speculative claim (found while investigating this ticket)

`docs/plans/ticket-R2-lite.md` §7 and its own `ROADMAP.md`/`CLAUDE.md`
entries recorded a *plausible, explicitly-named-as-unconfirmed* guess
("plausibly `RealGeography.is_land_precise`/`is_navigable`'s per-call
linear scan... not re-profiled further this ticket") for the original
24.4s-vs-6s observation. This ticket's own real profiling now shows that
guess was wrong — the real explanation is the speed-grid-size confound
in §1c above. Since the guess was recorded as a named, load-bearing
"finding" in three places (a plan file, `ROADMAP.md`, `CLAUDE.md`),
leaving it uncorrected would mislead a future reader into re-chasing a
dead end. **Part of this ticket's own work, not a separate ticket**:
update all three to state the real cause plainly, cross-referencing this
plan — matching this project's own precedent (`docs/plans/ticket-L2.md`'s
"Follow-up (ticket W1)" section correcting its own prior conclusion
in place, rather than leaving a stale claim to rot).

## Verification

- Real, direct `cProfile` run (§1b) and the controlled 2-speed/11-speed
  comparison (§1c) are both genuine `optimise()` calls against real
  `med`/`med_full` geography and weather — not mocked, not estimated.
- No code changes in `core/` (nothing built) — `git diff --stat core/`
  after this ticket: empty.
- The only diffs this ticket makes: `docs/plans/ticket-R2-lite.md`,
  `ROADMAP.md`, `CLAUDE.md` (correcting the speculative claim per the
  section above), plus this plan file and R2-lite's own R2-lite-row
  correction, plus a new, small ROADMAP row for G1 itself recording the
  negative result.

## ROADMAP row text (proposed)

> **G1 — Spatial indexing for RealGeography point queries (investigated,
> not built)** | Motivated by `docs/plans/ticket-R2-lite.md`'s own
> speculative guess that `med_full`'s 3,340 GSHHG polygons were driving
> its measured slower `optimise()` wall-clock via `is_land_precise`/
> `is_navigable`'s per-call linear polygon scan. **Real profiling (this
> project's own standing "profile first" discipline, the B1 gotcha's own
> precedent) refutes the premise, three independent ways**: (1)
> `is_land_precise` is called from zero places in `optimise()`'s own hot
> path (only ingest-time `mask_land_as_missing`/`coastal_fill_mask`, and
> `is_land`'s own fallback when `land_mask is None` — not the case for
> any shipped pack); `is_navigable` already takes its intended O(1)
> rasterised-lookup fast path. (2) A real `cProfile` run shows
> `is_navigable` at ~3% of total `optimise()` cost; weather sampling
> (`GriddedWeatherField.sample`/`bilinear_masked`) at ~85%+ — a direct
> reconfirmation of the pre-existing B1 profiling gotcha, not a new
> finding. (3) The original 24.4s-vs-6s comparison was never controlled
> for speed-grid size (11 speeds vs 2) — re-run with the same speed grid
> both ways, `med_full` is not slower than the small `med` pack for the
> identical passage at either grid size (if anything, faster: 15.33s vs
> 20.76s at the default 11-speed grid). **No Part 2 built** — an index
> would add real per-worker memory cost (the production VM is a 4GB box
> already loading four packs per worker) for a code path measuring ~3%
> of runtime; doesn't clear the bar the ticket's own acceptance criteria
> set. Corrected the now-known-wrong speculative claim in
> `docs/plans/ticket-R2-lite.md`/its own `ROADMAP.md`/`CLAUDE.md` entries
> rather than leaving it to mislead a future reader. The real "live
> production run took 48s" data point remains unexplained (geography
> polygon scanning is now ruled out either way) — worker-pool
> concurrency/contention is the most likely candidate, named as a
> separate follow-up if it recurs, not this ticket's job. Full trace:
> `docs/plans/ticket-G1.md`. | Investigation only — a real, measured
> negative result, not a partial delivery; zero `core/` diff. |

## CLAUDE.md gotcha entry (proposed, to add on completion)

- A new gotcha recording: `RealGeography.is_land_precise` is never on
  `optimise()`'s own hot path (ingest-time only); `is_navigable` already
  uses its intended O(1) rasterised fast path and costs ~3% of a real
  `med_full` `optimise()` call's runtime, reconfirming (not superseding)
  the B1 profiling gotcha's weather-sampling-dominates finding. The
  real lesson for future comparisons: **always control for `speeds_kn`
  grid size before attributing a wall-clock difference to pack/bbox
  size** — the ticket-R2-lite-era 24.4s-vs-6s comparison that motivated
  this investigation was an 11-speed run compared against a 2-speed one,
  not a real pack-size effect (re-run controlled: `med_full` was not
  slower than the small `med` pack at either grid size). `is_nogo` does
  do the identical unconditional linear polygon scan `is_land_precise`
  does, and unlike it, *is* on the hot path — currently free (today's
  packs have zero-to-a-handful of no-go zones) but the one part of this
  ticket's original premise that could become real if a future pack
  ships many real no-go/TSS zones; not indexed, named as a revisit
  trigger.

## Implementation order (docs-only — no Part 2 code)

1. This plan (done).
2. Correct `docs/plans/ticket-R2-lite.md` §7's speculative claim
   in place, cross-referencing this plan.
3. Correct `ROADMAP.md`'s R2-lite row (the same speculative sentence)
   and add G1's own new row.
4. Correct `CLAUDE.md`'s R2-lite gotcha entry, and add G1's own new
   gotcha entry.
5. `git diff --stat core/` confirmed empty; nothing to `ruff check`
   beyond the touched docs (no code changed).

### Critical files
- `docs/plans/ticket-R2-lite.md` (correction)
- `ROADMAP.md` (R2-lite row correction + new G1 row)
- `CLAUDE.md` (R2-lite gotcha correction + new G1 gotcha)
