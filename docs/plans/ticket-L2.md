# Ticket L2 — Self-scaling stage spacing

**Plan only — no implementation in this pass.** L1's own named follow-up
(`docs/plans/ticket-L1.md` §1c), unblocked by N1: `along_track_step_nm`
was left completely untouched in L1 because refining it silently
corrupted the search (a NaN leg cost vanishing from `_dp_route`'s/
`_lattice_search`'s state comparisons with zero diagnostic). N1 made
non-finite leg cost an explicit, diagnosable prune (`LegResult.
non_finite_cost`, a real `_dp_route` correctness fix, the
`weather_data_gap` diagnostic) — the prerequisite this ticket needed is
done.

**Headline finding, stated up front rather than buried**: re-verifying
L1's own sensitivity sweep against post-N1 code confirms the *safety*
claim (refining `along_track_step_nm` now fails loudly, never silently)
— but a deeper sweep than L1 ran finds the *effect* is not what a naive
reading of L1's own `cross_track_step_nm` precedent would suggest.
Unlike lane spacing (a clean, monotonic improvement in L1), stage spacing
has a **non-monotonic** relationship with detour ratio on the real
Plymouth→Falmouth passage — finer is not better, and is often worse or
outright infeasible. The recommended design (§2) derives
`along_track_step_nm` for principled consistency and to guard against a
different, real risk (an excessive stage count on a much-longer-than-Med
future passage) — but, backed by the real numbers below, it is a
**deliberate no-op for both shipped packs today**, not a further
Plymouth→Falmouth improvement. Stated honestly per the ticket's own
instruction, not forced into matching L1's own narrative shape.

## Follow-up (ticket W1, 2026-07-22) — the non-monotonic finding below was measured against gappy data, and W1 flips it

**This ticket shipped as designed and its own code is unchanged by this
note** — the floor-clamp formula (§2a) is still correct and still needed
for the coarsening-direction risk it was built for. But its own
*motivating* finding — "finer along-track spacing doesn't help the real
Plymouth↔Falmouth passage" — was measured against the UK pack's pre-W1
weather grid, which had a real, substantial coastal coverage gap (31 of
36 cells masked, only 14 of those genuinely land — see
`docs/plans/ticket-W1.md`). W1's coastal fill (real UK re-ingest,
2026-07-22) closes most of that gap, and re-running this ticket's own §1b
sweep against the refilled grid changes the answer:

| `along_track_step_nm` | `n_stages` | `non_finite_cost_count` (was nonzero pre-W1) | detour ratio (post-W1 fill) |
|---|---|---|---|
| 7.0 | 6 | 0 | 1.0367 |
| 6.0 (shipped default) | 7 | 0 | 1.0430 |
| 5.5 / 5.0 | 8 | 0 | 1.0307 |
| 4.8 / 4.5 | 9 | 0 | 1.0345 |
| 3.5 | 11 | 0 | 1.0221 |
| **3.0** | **13** | **0** | **1.0215 (best)** |
| 2.0 | 19 | 0 | 1.0252 |
| 1.228 (this ticket's own unfloored UK formula output) | 31 | 0 | 1.0312 |
| 1.0 | 38 | 0 | 1.0371 |

**Every single value in the sweep is now feasible** (`non_finite_cost_count
= 0` throughout, where 3.0/2.0/1.0nm were previously infeasible with
nonzero counts) — the coastal fill, not a change to this ticket's own
code, is what closed the gap. And unlike the pre-W1 sweep, refining now
**does** help: 3.0nm gives both the best score and the best detour ratio
of any value tested, better than the shipped 6.0nm default.

**The real, headline, product-visible number** (via the actual `optimise()`
call path, not the isolated sweep — includes baseline computation,
distillation, diagnostics): at the shipped 6.0nm stage spacing (this
ticket's own floor keeps the UK pack there regardless — `optimise()` has
no request-level override for `along_track_step_nm`, by this ticket's own
§2b/Scope-cuts decision), the coastal fill alone moves the real
Plymouth↔Falmouth detour ratio from **1.104** (L1's own recorded number,
and this ticket's own §6 acceptance number) to **1.0412** — a large,
real improvement from W1's ingest-layer fix alone, with zero change to
this ticket's code. The remaining gap to L1's geometric-minimum target
(~1.013) is now much smaller than before.

**What this means for this ticket's own floor-clamp conclusion**: §2a's
formula and its coarsening-direction rationale still stand — a floor is
still the right shape for guarding against a future long-haul pack. But
the *reason given* for floor-over-refine on the UK pack specifically
("finer is not a lever that helps here," §1b) no longer holds against
current (post-W1) data. Not reverted or re-coded here (out of scope for
a documentation follow-up, and W1's own plan named this exact
possibility rather than prescribing an automatic fix) — a genuine,
now-real opportunity (`along_track_step_nm≈3.0` outperforms the shipped
default) is left as a named, real follow-up: either loosen the floor for
packs where refining is now known to help, or add W1's `docs/plans/
ticket-W1.md` §2c-adjacent per-stage adaptive along-track refinement
(the "bigger alternative" both L2's own Judgment call 2 and this note
flag, not yet built). `tests/test_uk_sw_pack_acceptance.py`'s `<1.30`
detour bound is unaffected either way (1.0412 is comfortably inside it)
but is now much looser than the achieved ratio — worth tightening in
whatever ticket picks this up, not silently tightened here.

## Part 1 — Re-verify N1's safety claim, then look harder than L1 did

### 1a. N1's safety claim, re-verified empirically (not assumed)

Same scenario as L1's own sweep (Plymouth→Falmouth, `departure_t0_h=6.0`,
`speeds_kn=(10.0,)`, `pace=comfort=50`, real geography + real weather
including the merged CMEMS currents), `along_track_step_nm` swept with
`PruneStats` threaded through `arrival_times_within`/
`_lattice_route_result` (the exact functions `optimise()` itself calls):

| `along_track_step_nm` | `n_stages` | outcome | `non_finite_cost_count` |
|---|---|---|---|
| 6.0 (shipped default) | 7 | feasible, score 3799.71 | 664 |
| 3.0 | 13 | **infeasible** | 30 |
| 2.0 | 19 | **infeasible** | 42 |
| 1.0 | 38 | **infeasible** | 22 |

Every previously-silent infeasibility (L1 §1b: "every finer value is
INFEASIBLE", no further explanation possible at the time) now has a
**nonzero, directly observable** `non_finite_cost_count` — the exact
mechanism that would drive `optimise()`'s own `weather_data_gap`
diagnostic once `along_track_step_nm` is wired through it (not yet true
today — `optimise()` doesn't expose this as a request-level parameter,
§2). **N1's safety claim holds**: refining stage spacing can still make
this specific real passage infeasible (the underlying data-coverage gap
in the UK pack's coarse committed weather grid, `docs/plans/ticket-L1.md`
§1c, is unchanged — N1 never promised to make bad data good, only to
stop it from silently corrupting a search), but it now fails *loudly*,
every time, with a real count behind it.

### 1b. Looking harder: the effect is non-monotonic, not "finer is better"

L1's own sweep only tried four round values (6/3/2/1nm) and found three
infeasible with no further texture. A finer sweep, informed by knowing
`n_stages = max(2, round(total_nm / along_track_step_nm) + 1)` — i.e.
many *continuous* `along_track_step_nm` values map to the *same*
`n_stages` — reveals the real shape:

| `along_track_step_nm` | `n_stages` | outcome |
|---|---|---|
| 7.0 | 6 | feasible, detour_ratio **1.1908** |
| 6.5 – 5.8 (incl. shipped 6.0) | 7 | feasible, detour_ratio 1.2032 |
| 5.5 – 5.0 | 8 | feasible, detour_ratio **1.2502** (worse) |
| 4.8 | 9 | feasible, detour_ratio 1.2624 (worse still) |
| 4.5 and finer | 9+ | **infeasible** |

**Fewer stages (6) score better than the shipped default (7), which
scores better than more stages (8, 9) — refining is not a lever that
helps here, unlike `cross_track_step_nm` in L1.** Confirmed this is a
genuine geometry effect, not a tide/current-timing artifact that
happened to be measured at `departure_t0_h=6.0`: re-run with currents
zeroed (matching L1's own original dog-leg-diagnostic technique)
reproduces the **identical** detour-ratio sequence
(1.1908/1.2032/1.2502/1.2624) at every stage count — only the fuel-cost
component of `score_eur` shifts, never the geometry. The mechanism:
`build_lattice`'s stage centres sit at fixed fractions `i/(n_stages-1)`
along the straight origin→destination line, and the passage's two real
obstructions (L1's own finding: non-navigable/sub-depth points confined
to the first ~9% and last ~5-6% of the line) sit at *fixed* along-track
fractions independent of `n_stages` — different stage counts land stage
boundaries at different fractions relative to those fixed obstruction
edges, sometimes favourably (6 stages), sometimes not (8-9 stages), a
discretisation-phase effect with no reason to be monotonic in the first
place. `cross_track_step_nm` never had this problem because lane
position is continuous, not tied to a fixed along-track phase.

### 1c. The `lane_turn_rate_nm` interaction, checked directly (not assumed safe)

`along_track_step_nm` moving off its default is the one condition under
which L1's `MAX_TURN_ANGLE_DEG`-derived `lane_turn_rate_nm` formula
actually runs (L1's own special-case branch returns the literal
`LANE_TURN_RATE_NM` constant, bit-exact, only when
`along_track_step_nm == DEFAULT_ALONG_TRACK_STEP_NM`) — this sweep is
the first time it's exercised against a real scenario. Checked directly:
at `along_track_step_nm=5.0`, the derived `lane_turn_rate_nm` is
`12.5000` (via `5.0 * tan(radians(68.1986))`, not the special-cased
`15.0`) — confirmed by explicitly overriding it back to `15.0`/`12.5` and
re-running: **zero effect on score or detour ratio either way**, matching
L1's own original "`lane_turn_rate_nm` is demonstrably inert on this
passage" finding. The non-monotonic behaviour in §1b is not a
turn-rate artifact.

## Part 2 — Design, informed by §1's real numbers

### 2a. `along_track_step_nm` — same formula shape as L1 §2a, opposite clamp direction

```
along_track_step_nm = max(
    DEFAULT_ALONG_TRACK_STEP_NM,
    total_nm * ALONG_TRACK_STEP_FRACTION,
)
```

`ALONG_TRACK_STEP_FRACTION := DEFAULT_ALONG_TRACK_STEP_NM /
MED_STRAIGHT_LINE_NM = 6.0 / 179.5507750858526 = 0.0334167...` — the same
ratio-anchoring principle as L1's `CROSS_TRACK_STEP_FRACTION`, but
`DEFAULT_ALONG_TRACK_STEP_NM` is now a **floor** (`max`), not a ceiling
(`min`) — the reverse of `cross_track_step_nm`'s own clamp direction,
deliberately, because §1b found finer-than-default stage spacing helps
nowhere and actively hurts a real passage; the same "no principled reason
a fixed absolute value is right at every scale" critique L1 levelled at
`cross_track_step_nm` still applies here, just in the coarsening
direction: a future pack much *longer* than the Med's own 179.55nm
reference would, under today's fixed 6.0nm default, get a needlessly
large stage count (and search cost) — the floor formula fixes that
direction without touching the (found-to-be-harmful) refining direction
for anything Med-length or shorter.

**Rounding-direction care, the reverse of L1 §2a's own — flagged
explicitly since it's easy to get backwards**: L1's ceiling clamp
(`min(computed, DEFAULT)`) needed the fraction rounded *up* so the raw
product landed a hair *above* the default, guaranteeing `min()` returns
the literal default. A floor clamp (`max(computed, DEFAULT)`) needs the
**opposite** — the fraction rounded *down*, so the raw product lands a
hair *below* the default, guaranteeing `max()` returns the literal
default. Verified directly, not assumed: `0.033417` (rounded up, L1's
own direction) gives `179.5507750858526 * 0.033417 = 6.000048...` —
`max(6.0, 6.000048) = 6.000048`, **not** bit-exact. `0.0334167` (rounded
down) gives `5.999994...` — `max(6.0, 5.999994) = 6.0`, exact.
`ALONG_TRACK_STEP_FRACTION = 0.0334167`.

**Real numbers for both shipped packs, both floored to the literal
default**:
- Med (179.5507750858526nm): `179.5507750858526 * 0.0334167 =
  5.999994...` → `max(6.0, 5.999994) = 6.0` exactly. Zero change,
  by construction, matching L1's own "exact Med reduction" bar.
- UK (36.742372356262734nm): `36.742372356262734 * 0.0334167 =
  1.227809...` → `max(6.0, 1.227809) = 6.0` exactly. **Also zero
  change** — the formula's own natural (unfloored) output would have
  landed at ~1.23nm, squarely in §1b's infeasible zone; the floor
  correctly keeps the shipped UK pack at exactly today's working value,
  not a regression waiting to happen.

**This is a deliberate, evidence-based no-op for both shipped packs.**
Framed honestly, not as a shortfall: the ticket's own value is (1)
closing out L1's named follow-up formally, with real data, rather than
leaving `along_track_step_nm` a permanently-open question; (2) a real,
principled fix for the *coarsening* direction (a future long-haul pack),
which was never addressed before; (3) proof, via §1, that the seemingly
obvious "finer must be better" extension of L1's own success does not
hold here, which is itself worth knowing and recording before anyone
tries it again differently.

### 2b. `MIN_ALONG_TRACK_STEP_NM` — not needed

`cross_track_step_nm` needed a floor (`MIN_CROSS_TRACK_STEP_NM`) because
its own clamp could otherwise drive it arbitrarily fine for a
pathologically short passage. `along_track_step_nm`'s clamp is the
opposite shape (a floor at the default, no ceiling) — there is no
symmetrical "arbitrarily coarse" risk to bound, since `n_stages = max(2,
...)` already guarantees at least 2 stages regardless (unchanged,
pre-existing behaviour, not a new risk this ticket introduces). No new
constant needed.

## 3. Freeze framing

Same S1/L1 precedent — the search machinery itself (hard constraints,
cost formula, A*/DP algorithm) is untouched; this is a bounded change to
what geometry `build_lattice` derives before handing it to the
unmodified search.

- **Med**: exact reduction to `6.0`, verified in §2a — `pytest -m ""`,
  including the full Bonifacio/0.8 suite, expected green and
  **unmodified**.
- **UK**: exact reduction to `6.0` too (§2a) — the shipped pack's own
  behaviour, hence the acceptance-test detour ratio, is **unchanged**
  from L1's own already-recorded result. Stated plainly per §1's own
  finding, not glossed over: this ticket does not move that number.
- **A real, if narrow, behaviour change exists**: any *future* pack
  whose passage is longer than the Med's own 179.55nm reference will get
  a coarser-than-6.0nm stage spacing where today it would get a fixed
  6.0nm regardless of length. No such pack ships today (Med and UK are
  both at or under the Med reference length), so this is inert for the
  current acceptance surface — exercised only by the synthetic unit test
  in §5, not a real pack.

## 4. Performance

Given §2a's floor makes this a no-op for both shipped packs, **there is
no search-cost growth to measure for Plymouth→Falmouth or the Med demo
passage** — both keep their exact current `n_stages`. This is itself the
real, measured perf result (stated honestly, not padded with a
hypothetical-growth estimate nobody will hit): real wall-clock for both
passages is checked in the acceptance run (§6) and expected identical to
L1's own already-recorded numbers, within normal run-to-run noise.

For the *coarsening* direction (a future long-haul pack), the effect is
a **reduction**, not growth: `n_stages` scales as
`~total_nm / along_track_step_nm`, so a passage `k` times the Med's own
length gets `along_track_step_nm` scaled by `k` too (once past the
floor), keeping `n_stages` roughly constant rather than growing linearly
with passage length the way a fixed 6.0nm step would. Quantified directly
in the synthetic unit test (§5): a fabricated ~360nm passage (2× Med
length) should derive `along_track_step_nm ≈ 12.0nm`, keeping `n_stages`
close to the Med's own ~31, not the ~61 a fixed 6.0nm step would give.

## 5. Tests

- `tests/test_lattice.py`: unit tests for the derivation formula against
  fabricated short/long passage fixtures — exact Med-reduction algebra
  (bit-exact, per §2a's own verified numbers), the UK-length case
  landing on the floor, and a ~2×-Med-length fixture confirming the
  formula *coarsens* correctly (derived `along_track_step_nm` ≈ double
  the default, `n_stages` roughly halved vs. what the fixed default
  would have given) — this is also the only test that exercises L1's
  `MAX_TURN_ANGLE_DEG` trig-formula branch against anything other than a
  hand-picked value, since neither shipped pack ever leaves the special-
  cased default under this ticket's own floor.
- `tests/test_optimiser_regression.py`: the full existing Bonifacio/0.8
  suite, confirmed green and unmodified (§3).
- `tests/test_uk_sw_pack_acceptance.py`: existing
  `test_uk_sw_pack_detour_ratio_stays_reasonable` (L1's own `<1.30`
  regression guard) re-run and confirmed still passing, **unchanged** —
  per §1's own finding, this ticket doesn't move the detour ratio, so
  the bound doesn't need tightening (checked explicitly, not assumed;
  recorded in the real acceptance run, §6).
- No new NaN/`PruneStats`-specific tests needed here — N1 already covers
  that mechanism's own correctness exhaustively; this ticket only adds a
  geometry-deriving formula on top of it.

## 6. Acceptance criteria

- **Plymouth→Falmouth detour ratio**: real `optimise()` run, same
  scenario as L1's own acceptance run. **Recorded honestly**: expected
  to match L1's own already-recorded 1.104 exactly (§2a's floor is a
  no-op for this pack) — the remaining gap to the geometric minimum
  (~1.013) is **not closed by this ticket**, and §1's own real sweep
  data is the explanation (finer stage spacing does not help this
  passage, confirmed empirically, not assumed away).
- **Med demo passage**: full `pytest -m ""` green, **unmodified**,
  including every Bonifacio/0.8 regression test.
- **Both packs' yaml end the ticket with no lattice-knob overrides
  set** — same "derive, don't tune" proof as L1; `along_track_step_nm`
  was never a `RegionPack` field to begin with (only a `build_lattice`
  parameter), so this is mechanically guaranteed rather than needing a
  new check, but confirmed via `git diff --stat` on both yaml files
  regardless, for the record.
- **Perf**: real measured wall-clock for both passages, recorded,
  expected unchanged from L1's own numbers (§4).
- **UK acceptance test's `<1.30` detour bound**: checked against the
  real result and left **unchanged** — not meaningfully loose, since the
  real achieved ratio doesn't move (§1's own finding, not an oversight).
- `ruff check .` clean; ROADMAP row + CLAUDE.md gotcha added per
  convention (below).

*(Real numbers — the sweep tables in §1, the exact floored values in
§2a, the acceptance-run detour ratio/perf — already measured during this
planning pass and recorded above; not fabricated ahead of a real run,
matching R1/C1/S1/L1/N1's own precedent. The implementation's own
acceptance run should re-confirm these against the final, wired-in code
path, not just the direct `build_lattice`/`_lattice_route_result` calls
used during planning.)*

## Judgment calls flagged for sign-off

1. **Ship a formula that's a deliberate no-op for both real packs,
   rather than abandoning the ticket entirely.** Alternative: given §1's
   finding, simply close L1's own named follow-up with a documentation-
   only note ("re-verified safe per N1, found not to help, not pursued
   further") and write no new code at all. Recommending shipping the
   floored formula anyway — it's a real, if narrow, fix for the
   coarsening direction (§3), costs nothing for either shipped pack
   (exact no-op, verified), and formally closes the "is
   `along_track_step_nm` self-scaling now?" question with working code
   and a real test, rather than leaving it as a standing question mark
   for the next person to re-litigate from scratch.
2. **No adaptive, per-stage along-track refinement** (mirroring ticket
   0.8's own per-stage `cross_track_step_nm` refinement, which
   *is* sensitive to local geography rather than a single global value).
   This would be the "real" fix for §1b's non-monotonic finding — locally
   refining stage spacing only near the two known real obstructions,
   leaving the open-water middle at the coarse default — but it's
   materially more search machinery (a second refinement axis, real
   design work, its own empirical tuning story likely) than a "self-
   scaling" ticket in this series has taken on so far. Named as a real,
   larger follow-up, not built here — flagging for explicit sign-off
   since it's the more ambitious alternative to today's narrower
   recommendation, not a check-the-box formality.

## Scope cuts (explicit)

- **Adaptive per-stage along-track refinement** (mirroring ticket 0.8's
  cross-track precedent) — the judgment call above's alternative,
  explicitly not built here.
- **Fixing the UK pack's underlying weather-grid coarseness** (the real,
  root reason finer stage spacing goes infeasible) — an ingest/data
  concern, not a lattice-geometry one; out of scope, same boundary L1's
  own §1c already drew.
- **Any cost-model, hard-constraint, or search-algorithm change** — this
  ticket only derives a `build_lattice` input parameter.
- **Threading `along_track_step_nm` through `RegionPack`/`PlanRequest`
  as an overridable field** — not needed; `build_lattice`'s own
  parameter (defaulting to the derived value when unset, exactly like
  L1's `cross_track_step_nm`/`lane_turn_rate_nm`) is the only place this
  needs to live, matching L1's own precedent exactly.

## ROADMAP row text (proposed)

> **L2 — Self-scaling stage spacing** | L1's own named follow-up
> (`docs/plans/ticket-L1.md` §1c), unblocked by N1's non-finite-cost
> fix. Re-verified N1's safety claim empirically first (every previously-
> silent infeasibility now carries a real, nonzero `non_finite_cost_count`
> — never silent) — then found, via a finer sweep than L1 ran, that
> stage-spacing refinement has a **non-monotonic** relationship with
> detour ratio on the real Plymouth↔Falmouth passage (confirmed a pure
> geometry effect, not a tide-timing artifact: identical with currents
> zeroed), unlike lane spacing's clean, monotonic L1 result — finer stage
> spacing is not a lever that helps here. `along_track_step_nm` derives
> from passage length with the Med-anchored ratio L1 established, but as
> a **floor** (not a ceiling, the reverse clamp direction from L1's
> `cross_track_step_nm`) — a real fix for the opposite risk (an
> excessive stage count on a future much-longer-than-Med passage), and a
> deliberate, verified no-op for both shipped packs today. Full design,
> the real sweep data, and the acceptance-run numbers:
> `docs/plans/ticket-L2.md`. | Small — one formula, one clamp-direction
> reversal from L1's own precedent, honest about not moving the headline
> metric it was named to chase. |

## CLAUDE.md gotcha entry (proposed, to add on completion)

- A new gotcha recording: the non-monotonic stage-spacing finding in
  full (the real sweep table, the currents-zeroed disentangling check
  proving it's pure geometry), the reversed clamp-direction/rounding
  care vs. L1's own `cross_track_step_nm` (a real, easy-to-get-backwards
  detail worth preserving), and the honest "this ships as a verified
  no-op for both real packs" framing — the next person tempted to try
  "just refine the along-track step further" on this same passage should
  find this gotcha before re-deriving the same negative result from
  scratch.

## Implementation order

1. `core/lattice.py`: `ALONG_TRACK_STEP_FRACTION` constant (with the
   rounding-direction reasoning in its own docstring, mirroring L1's own
   `CROSS_TRACK_STEP_FRACTION` comment style), `build_lattice`'s
   `along_track_step_nm` parameter gains the same `None`-sentinel-derive
   pattern as `cross_track_step_nm`/`lane_turn_rate_nm`.
2. `pytest -m ""` — expected green, **unmodified**, before anything else
   (isolates "does the formula alone change anything" — it shouldn't,
   for either shipped pack, per §2a's own bit-exact verification).
3. Tests (§5): the derivation-formula unit tests (including the
   coarsening-direction synthetic fixture), re-confirm the UK acceptance
   test's existing `<1.30` bound still passes unchanged.
4. Real acceptance run (§6): Plymouth→Falmouth detour ratio (expected
   unchanged from L1), Med regression suite, both packs' yaml diffed,
   real wall-clock for both passages.
5. Docs: ROADMAP row, CLAUDE.md gotcha (both above, filled in with real
   numbers once the implementation's own acceptance run re-confirms
   this planning pass's figures).

## Verification

- `pytest -m ""` green and unmodified after step 1, before any test
  additions — same isolation discipline as every prior ticket in this
  series.
- `ruff check .` clean throughout.
- `git diff --stat data/region_packs/med.yaml data/region_packs/uk_sw.yaml`
  showing no new lattice-knob keys — the "derive, don't tune" proof,
  mechanically guaranteed here (§6) but checked anyway for the record.
- The Plymouth→Falmouth and Med demo runs in the acceptance section are
  genuine `optimise()` calls against real geography/weather, not mocked
  stand-ins — matching this project's standing bias toward real
  verification.

### Critical files for implementation

- `core/lattice.py` (`build_lattice`, new `ALONG_TRACK_STEP_FRACTION`
  constant)
- `tests/test_lattice.py` (new derivation-formula unit tests)
- `tests/test_uk_sw_pack_acceptance.py` (re-confirm existing bound,
  no change expected)
- `data/region_packs/med.yaml`, `data/region_packs/uk_sw.yaml` (verify
  no new override keys needed — none should be)
