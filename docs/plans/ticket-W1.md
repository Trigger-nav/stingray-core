# Ticket W1 — Coastal weather coverage (nearest-valid fill for masked cells covering real navigable water)

**Sequencing note, stated up front:** this ticket is planned now but its own
acceptance run (re-running L2's §1b stage-spacing sweep and the S1
distillation check against the refilled grid) requires ticket L2
(self-scaling stage spacing, `docs/plans/ticket-L2.md`) to have landed
first — L2 is approved-planned but not yet implemented as of this writing.
Implementation of W1 itself has no code dependency on L2 (it's a pure
ingest-layer change); only the *acceptance measurement* is sequenced after
L2, per the original instruction ("After L2 lands: plan ticket W1...").

## Context

`docs/plans/ticket-L2.md` §1's along-track sensitivity sweep found that
finer `along_track_step_nm` values on the real UK Plymouth–Falmouth
passage go infeasible with `non_finite_cost_count > 0` (N1's diagnostic
firing, not silent loss) — the search runs into real cells with NaN
weather. The motivating hypothesis for this ticket was threefold:

1. L2's own sweep is directly blocked by these NaN cells (confirmed
   already, in `docs/plans/ticket-L2.md`).
2. **Hypothesised**: S1's route distillation might be silently declining a
   shortcut near the residual Plymouth-approach kink because the shortcut
   edge reads `non_finite_cost=True`.
3. N1's `weather_data_gap` diagnostic can fire today for any user-placed
   endpoint/favourite near a harbour, if the nearest weather cells are
   masked.

**Part 1 below empirically tests hypothesis 2 and finds it false** — a
real, honest, and important correction to the ticket's own motivation.
**Investigating the actual masked-cell population in the real UK pack also
surfaces a second, larger finding not in the original framing at all**
(see "A larger finding" below): most of the UK grid's masked cells are
NOT land-mislabelled — the ticket's design still applies to them, but the
framing needs broadening. Both findings are folded into the design below,
flagged explicitly for sign-off.

## Part 1 — Empirical verification: does S1 distillation get blocked by `non_finite_cost`?

**Method**: ran a real `optimise(..., distill=True)` against the UK pack's
real geography/weather (Plymouth–Falmouth, `speeds_kn=(10.0,)`,
`departure_t0_h=6.0`, `pace=comfort=50` — the same scenario L1/L2/N1 all
used), with `core.legs.evaluate_leg` monkeypatched to log every call
`core.distill.distill_track` makes internally (34 calls, one real
distillation pass).

**Result: zero of the 34 calls returned `non_finite_cost=True`.** The
distilled result does have a residual kink (drops from 7 to 5 waypoints,
merging two pairs but not the pair spanning the visible kink point at
`(50.147, -4.373)`). The two calls that *did* get rejected were both
blocked by genuine hard constraints:

```
LatLon(50.347, -4.15) -> LatLon(50.143, -4.542)  navigable=False depth_ok=False
LatLon(50.347, -4.15) -> LatLon(50.138, -4.710)  navigable=False depth_ok=False
```

Both are real coastline/shoal-water blocks (a straight line from the
origin that far south cuts across Rame Head/the western Sound approach) —
not a weather data gap. **Hypothesis 2 is refuted.** W1 does not smooth
this particular visible kink; that's a distillation/lattice geometry
question (already covered under L1/L2's own scope), not a weather-coverage
one.

This finding does **not** remove W1's motivation — hypotheses 1 and 3
still stand on their own (L2's sweep and N1's diagnostic both engage
`non_finite_cost` for real, independent of distillation) — it just
narrows what W1 is honestly for. Recorded here rather than silently
dropped, per this project's own practice of stating refuted hypotheses,
not just confirmed ones (the L2/N1 plans did the same).

## A larger finding: most masked UK cells aren't land-mislabelled at all

The ticket's own framing ("water-mislabelled-as-land cells") assumes the
masked cells are cases where `mask_land_as_missing`'s single-reference-point
GSHHG check (`is_land_precise`) returns `True` for a point that's really
surrounded by navigable water — the Plymouth-Sound-sized islet/headland
case. Checking the real, currently-fetched UK pack npz (`hs_m`, 4×9 grid,
0.25° spacing) directly refutes that this is the dominant case:

```
total cells: 36, masked (NaN at hour 0): 31
  of those 31: is_land_precise(reference point) == True:  14   ("genuinely land-referenced" cells)
               is_land_precise(reference point) == False: 17   ("masked for another reason")
```

**17 of 31 masked cells are NaN despite their own reference point reading
as open water per GSHHG** — e.g. `(50.00, -5.50)` and `(50.25, -4.75)`
both read `is_land_precise=False` yet are `NaN` in the fetched `hs_m`
field. This isn't a `core/` or `ingest/` bug: NOAA WW3 (the wave model
behind `fetch_grib_nomads.py`) carries its **own internal land-sea mask**,
independently of GSHHG, and is well known to under-resolve complex
nearshore geometry — a real, expected characteristic of the source model,
not a defect in this repo's normalisation. `mask_land_as_missing` only
ever *adds* NaN (never removes an upstream one), so these 17 cells were
already NaN in the raw GRIB response before this repo's own masking ran at
all.

**This matters for design, not just framing.** A fill mechanism gated on
"was this cell masked because `is_land_precise` said so" would fix only 14
of the 31 masked cells (and, per the water-fraction analysis below, fewer
than that survive a real navigability check) — missing the majority,
larger-impact case entirely. The two situations are indistinguishable to
every downstream consumer (`core.legs.evaluate_leg`'s `non_finite_cost`,
L2's sweep, N1's diagnostic all just see "NaN here") and the correct fix —
substitute the nearest real value where the cell's own footprint is
genuinely navigable water — is identical either way. **Recommendation,
flagged for explicit sign-off:** gate the fill on "is this cell currently
NaN and does its footprint contain majority real navigable water",
regardless of *why* it's NaN — not on re-deriving whether
`mask_land_as_missing` specifically was the cause. This is a broadening of
the ticket's literal framing, kept because it's mechanically identical
work and leaves real, significant coverage (the 17-cell majority) on the
table if declined.

## Part 2 — Design

### 2a. Where: one shared pair of functions in `ingest/grib_common.py`

Confirmed by grep: `mask_land_as_missing` is already called from all four
fetchers needing it — `fetch_grib_nomads.py` (`hs_m`, `period_s`,
`dir_deg`), `fetch_grib_ecmwf.py` (`hs_m`, `period_peak_s`,
`period_mean_s`, `wave_from_deg`), `fetch_era5_track.py` (same four), and
`fetch_currents_cmems.py` (`current_u`, `current_v`) — confirming the
user's own steer ("one shared place, most likely"). Wind is never masked
anywhere (confirmed via the same grep) — the ticket's "fill wind/wave/
current" framing is corrected here: **wind needs no fill, only wave and
current fields do**, since wind is the one field category that already
has real over-land values by design (an existing, pre-W1 convention, see
`core.weather.GriddedWeatherField`'s docstring).

Two new functions, added next to `mask_land_as_missing`:

```python
def coastal_fill_mask(
    lats: np.ndarray,
    lons: np.ndarray,
    geography: _LandChecker,
    *,
    water_fraction_threshold: float = 0.5,
    sample_grid_n: int = 9,
) -> np.ndarray:
    """(nlat, nlon) bool array: True where this cell's own footprint
    (bounded by half the grid's own dlat/dlon in each direction around its
    reference point) is majority real navigable water per GSHHG
    (`geography.is_land_precise`, sampled on an `sample_grid_n` x
    `sample_grid_n` sub-grid) -- independent of whether the cell is
    currently masked or why. Ingest-time only (not a hot path), same
    precision-over-speed tradeoff `mask_land_as_missing` already makes."""

def apply_coastal_fill(
    values: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    fill_mask: np.ndarray,
    *,
    ref_lat_deg: float,
    max_fill_radius_nm: float = 60.0,
) -> tuple[np.ndarray, int, int]:
    """For every (iy, ix) where fill_mask[iy, ix] is True and values[:, iy, ix]
    is currently all-NaN (mirrors mask_land_as_missing's own time-invariant-
    mask assumption): find the nearest cell -- by real distance
    (core.units.distance_m at ref_lat_deg), among cells whose values[0] is
    NOT NaN in the *original* array -- and copy its whole time series.
    Cells whose nearest real-data neighbour is farther than
    max_fill_radius_nm are left untouched (still NaN) -- the
    land=missing-never-calm convention wins over an unreliably distant
    substitute. Returns (filled_values, n_filled, n_skipped_too_far)."""
```

Split into two functions deliberately: `coastal_fill_mask`'s geometry
(land-fraction sampling) is identical across every field sharing one
lat/lon grid — computed **once** per fetcher run and reused across all of
that run's wave (or current) fields, rather than recomputed per field.
`apply_coastal_fill` is the cheap per-field step (nearest-neighbour lookup
+ copy).

**Consequence, stated explicitly:** because the land mask geometry is
field-independent, `n_filled`/`n_skipped_too_far` are identical across
every wave field in one fetcher run (all share the same masked-cell set)
and separately identical across the two current fields. Provenance
therefore records **one count per field group** (`wave_filled_cells`,
`current_filled_cells`), not one per individual field — stated here
because the original instruction's "per-field filled-cell count" phrasing
could be read either way; per-individual-field counts would always be
duplicates of the group count by construction, so recording them
separately would be redundant, not more informative.

Call-site pattern (one example, `fetch_grib_nomads.py`; identical shape at
the other three sites):

```python
fill_mask = coastal_fill_mask(lats, lons, geography)
hs_m = mask_land_as_missing(np.stack(hs_frames), lats, lons, geography)
hs_m, n_filled, n_skipped = apply_coastal_fill(hs_m, lats, lons, fill_mask, ref_lat_deg=ref_lat_deg)
period_s, _, _ = apply_coastal_fill(mask_land_as_missing(...), lats, lons, fill_mask, ref_lat_deg=ref_lat_deg)
dir_deg, _, _ = apply_coastal_fill(mask_land_as_missing(...), lats, lons, fill_mask, ref_lat_deg=ref_lat_deg)
```

(`n_filled`/`n_skipped` only captured once per field group, per the point
above — the repeated calls for `period_s`/`dir_deg` discard their
counts.)

### 2b. Judgment calls, flagged for explicit sign-off

1. **`water_fraction_threshold=0.5`** (majority water), not "any
   intersection" as the original instruction literally read. Verified
   empirically why the literal reading is wrong: sampling the real UK
   grid's masked cells at `sample_grid_n=9` (81 sub-points/cell), an
   "any water at all" gate accepts **27 of 31** masked cells — including
   cells with water fractions as low as 0.07–0.21, which are overwhelmingly
   Dartmoor/inland Devon with a sliver of coastal water in one corner.
   Filling those with open-ocean wave data would misrepresent a
   near-entirely-land cell, not fix a coastal-water gap. A 0.5 threshold
   drops this to **18 of 31** cells, all genuinely majority-water. The
   motivating case (the grid cell nearest the real Plymouth-Sound origin,
   `(50.25, -4.25)`, water fraction 0.827) clears the stricter threshold
   comfortably either way — the refinement doesn't cost the ticket its own
   reason for existing, it just stops over-filling clearly-inland cells.
2. **`max_fill_radius_nm=60.0`**. The real UK grid's 18 threshold-passing
   cells have nearest-real-neighbour distances from 9.6nm up to 53.4nm
   (the corner cells, where the grid's genuine wave-data coverage is
   sparsest — see "A larger finding" above). 60nm covers all 18 in the
   real pack while still being a real, checkable bound rather than
   unconditional fill-at-any-distance — same shape as C1's 6-hour
   time-coverage hard-cutoff precedent (a bounded "hold the nearest real
   value" tolerance, with a hard stop past it). A future, sparser pack
   could have cells that *don't* clear this radius; they correctly stay
   `NaN` rather than being filled from an implausibly distant sample.
3. **Broadened trigger** ("any NaN cell over majority-navigable water",
   not "specifically land-mask-caused NaN") — see "A larger finding"
   above. This is the highest-impact judgment call in this plan; flagged
   most prominently.

### 2c. Provenance and `/v1/health`

`GriddedWeatherField` (`core/weather.py`) already carries three
provenance triples (`cycle`/`fetched`/`source`, `current_cycle`/
`current_fetched`/`current_source`) loaded from optional npz keys via the
established `"key" in grid` tolerant pattern (npz files written before a
given ticket simply lack the key — C1 set this precedent for
`current_*`). Add two more optional int fields the same way:

```python
wave_filled_cells: int | None = None
current_filled_cells: int | None = None
```

`None` for an npz written before this ticket (tolerant load) or for a
pack that doesn't fetch that field group at all (Med currents, still off
per C1) — the same "not modelled, not indistinguishable from modelled-and-
zero" signal C1 established for `current_source`. Each of the four
fetchers writes its own count(s) into the npz it produces;
`fetch_currents_cmems.py`/`merge_currents.py` write `current_filled_cells`
onto the merged output specifically (matching how `current_cycle`/etc.
already flow through that merge step).

`api/schemas.py`'s `HealthOut` gains the matching two fields,
`wave_filled_cells: int | None = None` / `current_filled_cells: int |
None = None`, populated from the loaded `GriddedWeatherField` in
`api/routes.py`'s `health()` the same way the existing provenance fields
already are.

### 2d. Land=missing-never-calm convention, respected explicitly

Every filled value is a **real, already-fetched** value copied from a
nearby real ocean cell — never a synthetic/interpolated/zero value. Cells
that stay fully land (no water in their footprint at all — 4 of the UK
pack's 31 masked cells, e.g. Dartmoor's interior) are untouched, still
`NaN`, exactly as today. **Why nearest-valid is conservative, stated as a
judgment with limits, not fact** (per the original instruction): open
water generally has equal-or-greater `Hs`/current speed than a sheltered
inshore position at the same moment (less fetch-limited, less
land-shadowed) — so substituting a nearby open-water sample for a
sheltered coastal cell tends to *overstate* sea state there, which is the
safer direction for a routing/comfort tool to err. This is a directional
tendency, not a guarantee (a sheltered anchorage in a strong tidal
narrows can, per C1, see current *exceed* nearby open water) — stated
here as the actual argument, not asserted as fact.

## Freeze compliance

Zero `core/` changes. Confirmed by design, not just intent: every touched
symbol lives in `ingest/grib_common.py` (new functions), the four
`ingest/fetch_*.py` fetchers (new call-site wiring), `core/weather.py`
(two new optional provenance fields on `GriddedWeatherField`, additive,
following the exact `current_*` C1 precedent — this is `core/` in name
only, a plumbing-through of already-established provenance shape, not
optimiser/lattice/search logic), and `api/schemas.py`/`api/routes.py`
(surfacing). `git diff --stat` against `core/lattice.py`,
`core/optimiser.py`, `core/legs.py`, `core/isochrone.py`,
`core/corridors.py` must show zero diff — the mechanical check, same as
every prior ticket touching the routing freeze's neighbourhood.

## Tests

- `tests/test_ingest_grib_common.py` (existing file, extended):
  - `coastal_fill_mask` against a fabricated small grid + a fabricated
    `_LandChecker` with a known polygon: assert a cell whose footprint is
    entirely inside the polygon reads `False`; a cell straddling the
    boundary at a known, hand-computed fraction reads `True`/`False`
    correctly either side of `water_fraction_threshold`.
  - `apply_coastal_fill` against a fabricated `(n_hours, nlat, nlon)`
    array with a known NaN cell and a known nearest real neighbour at a
    known distance: assert the filled cell equals the neighbour's values
    exactly; assert a cell whose only real neighbour is farther than
    `max_fill_radius_nm` stays `NaN` and is counted in
    `n_skipped_too_far`; assert a cell not in `fill_mask` is never
    touched even if `NaN`; assert a cell already non-`NaN` is never
    touched even if `fill_mask` is `True` there (fill only ever replaces
    `NaN`, never overwrites a real fetched value).
- `tests/test_ingest_fetch_grib_nomads.py`/`..._ecmwf.py` (existing files,
  extended): one small fabricated fixture per fetcher asserting the new
  `wave_filled_cells` count lands correctly in the written npz.
- `tests/test_ingest_fetch_currents_cmems.py` (existing file, extended):
  same shape for `current_filled_cells`.
- `tests/test_core_weather.py` (existing file, extended): `from_npz`
  tolerates an npz missing `wave_filled_cells`/`current_filled_cells`
  (pre-W1 npz compatibility, mirrors the existing `current_*` tolerance
  test).
- `tests/test_api_health.py` (existing file, extended): `HealthOut`
  surfaces the two new fields from a fabricated `AppState`; both `None`
  when the loaded field lacks them.
- **Real re-ingest of the UK pack** (manual, documented, not part of
  automated CI — matches this project's own precedent for real-network
  ingest steps, e.g. B7's live CDS run): re-run `fetch_grib_nomads.py`
  (and, if time allows, `fetch_currents_cmems.py`) for the UK bbox with
  the fill wired in; record the real before/after masked-cell count and
  `wave_filled_cells`/`current_filled_cells` values actually observed.

## Acceptance criteria

1. `coastal_fill_mask`/`apply_coastal_fill` unit tests green, including
   the "never overwrite real data" and "respects max_fill_radius_nm"
   regression tests.
2. A real UK-pack re-ingest with the fill wired in: record the actual
   `wave_filled_cells` count (expected, from the analysis above: 18,
   modulo any small change in the live-fetched data between planning and
   implementation) and confirm the previously-masked cell nearest the
   Plymouth-Sound origin (`(50.25, -4.25)`) is now filled.
3. **Sequenced after L2 lands** (this ticket's own stated dependency): via
   `docs/plans/ticket-L2.md`'s own instrumented sweep script
   (`l2_along_track_sweep.py`-equivalent), re-run L2's §1b finer-along-
   track sweep against the refilled grid. Record whether the previously-
   infeasible finer values (4.5nm and below) become feasible, and whether
   `non_finite_cost_count` drops at every step. **If finer stage spacing
   becomes feasible and helps the detour ratio, write a documented
   follow-up note re-evaluating L2's own floor-clamp conclusion** (its
   "finer along-track never helps" finding was measured against the
   gappy pre-W1 grid — L2's plan explicitly anticipated this) — do not
   silently amend L2's already-shipped conclusion without that note.
4. Re-run the S1 distillation trace from Part 1 above against the refilled
   grid, for completeness (expected: no change, since Part 1 already
   found zero `non_finite_cost` calls in the distillation path even
   *before* the fill — record this explicitly rather than assuming).
5. Med: regression suite unmodified. The committed Med test npz is
   untouched (fill only applies to fresh ingests going forward); confirm
   via `git status`/`git diff --stat` that no committed `data/` npz
   changed.
6. Full `pytest -m ""` green, `ruff check .` clean.
7. `git diff --stat` confirms zero touch to `core/lattice.py`,
   `core/optimiser.py`, `core/legs.py`, `core/isochrone.py`,
   `core/corridors.py` (the freeze-compliance mechanical check).

### Real acceptance-run results (2026-07-22, implementation)

1. `coastal_fill_mask`/`apply_coastal_fill` unit tests: 14 new tests,
   all green (`tests/test_grib_common.py`), including the never-overwrite
   and max-radius-skip regressions.
2. **Real UK-pack re-ingest** (`python3 -m ingest.fetch_grib_nomads
   --bbox -5.5 49.8 -3.5 50.8 ...`, live NOMADS run, 2026-07-22):
   `wave_filled_cells = 18`, exactly matching the planning-pass count.
   31 of 36 cells masked before the fill; 13 remain masked after (4 pure
   land + 9 below the 0.5 water-fraction threshold). The cell nearest the
   Plymouth-Sound origin, `(50.25, -4.25)`, was confirmed `NaN` before
   this ticket and now holds a real value (`hs_m = 0.345m` at hour 0).
3. **L2's §1b sweep, re-run against the refilled grid — every value now
   feasible.** `non_finite_cost_count = 0` at every `along_track_step_nm`
   tested (7.0 down to 1.0nm), where 3.0/2.0/1.0nm were previously
   infeasible with real, nonzero counts. Detour ratios (via the same raw
   `_lattice_route_result` sweep L1/L2 used) ranged from 1.0215 (best, at
   3.0nm/13 stages) to 1.0430 (worst, at the shipped 6.0nm/7 stages) —
   **finer now measurably helps**, the opposite of L2's own pre-W1
   finding. Full table and the L2 floor-clamp re-evaluation:
   `docs/plans/ticket-L2.md`'s new "Follow-up (ticket W1)" section.
   **The real, headline, product-visible number** (via the actual
   `optimise()` call, still at the shipped, L2-floored 6.0nm spacing —
   `optimise()` has no request-level override for stage spacing):
   **detour ratio 1.104 → 1.0412**, score 3487.62 → 3378.85 — a large,
   real improvement from this ticket's ingest-layer fix alone, zero
   change to search/lattice code. This is the number to report
   prominently, per instruction.
4. S1 distillation trace re-run against the refilled grid: **unchanged**,
   as predicted — zero of 35 `evaluate_leg` calls inside `distill_track`
   return `non_finite_cost=True` (was zero pre-fill too, 34 calls then vs
   35 now — the coastal fill changed the track distillation operates on,
   hence the call-count difference, but not the underlying finding). The
   3 rejected shortcuts are still blocked by genuine `navigable=False`/
   `depth_ok=False`. Part 1's original conclusion holds: W1 does not
   smooth a distillation-blocked kink because there wasn't one.
5. Med: confirmed via `git status`/`git diff --stat` — no committed
   `data/` npz changed; the Med regression suite ran unmodified (see #6).
6. Full `pytest -m ""`: 494 passed (fast-suite portion), full run
   including slow/`RealGeography` tests confirmed separately — green,
   `ruff check .` clean throughout.
7. `git diff --stat core/lattice.py core/optimiser.py core/legs.py
   core/isochrone.py core/corridors.py` — only `core/lattice.py` shows a
   diff, and it is entirely L2's own approved formula change (see that
   ticket), not W1's. W1 itself touched zero lines in any of the five
   freeze-relevant files.

## Scope cuts (explicit)

- No re-ingest or re-verification of the Med pack — Med's masked-cell
  population wasn't characterised in this planning pass; the mechanism
  applies identically if/when Med is re-ingested, but that's not part of
  this ticket's acceptance run.
- No change to `core.weather.GriddedWeatherField.sample`/
  `bilinear_masked` interpolation itself — the fill happens once, at
  ingest, producing ordinary real (non-NaN) values; the sampling/
  interpolation path downstream is completely unmodified and doesn't need
  to be, by design.
- No attempt to fix the *root cause* of the 17 non-land-masked NaN cells
  (WW3's own internal nearshore land-sea mask) — that's a source-model
  limitation, not something this repo's ingest code can correct; the fill
  is a principled mitigation, not a fix to NOAA's model.
- No wind-field changes — wind is never masked, confirmed, nothing to
  fill.
- No change to `current_exceeds_stw`/A* heuristic current-awareness (C1's
  own named, deliberately-deferred limitation) — orthogonal to this
  ticket.
- No retroactive re-evaluation of L1/L2's own acceptance numbers beyond
  what acceptance-criterion 3 above explicitly asks for.

## Docs

- `ROADMAP.md`: new row under the Phase 0→1 bridge table (or nearest
  appropriate section), summarising the two real findings (S1
  hypothesis refuted; majority of real masked cells aren't
  land-mislabelled, broadened trigger) and the real UK fill counts once
  implemented.
- `CLAUDE.md`: new gotcha entries for (1) the S1-distillation hypothesis
  being tested and refuted, with the real evaluate_leg trace numbers; (2)
  the WW3-own-internal-mask finding — most of the real UK pack's masked
  cells aren't GSHHG land-mask false positives at all, and the fill
  mechanism is deliberately gated on "NaN + majority-water footprint",
  not "was `is_land_precise` the cause"; (3) the water-fraction-threshold
  and max-fill-radius judgment calls with their real supporting numbers.

## Implementation order

1. `coastal_fill_mask`/`apply_coastal_fill` in `ingest/grib_common.py` +
   unit tests (fabricated fixtures only, no real ingest yet). `pytest -m
   ""` checkpoint.
2. Wire into `fetch_grib_nomads.py`; fabricated-fixture test for
   `wave_filled_cells` provenance. `pytest -m ""` checkpoint.
3. Wire into `fetch_grib_ecmwf.py`, `fetch_era5_track.py`,
   `fetch_currents_cmems.py`/`merge_currents.py` (current-field
   provenance). `pytest -m ""` checkpoint.
4. `core/weather.py`'s two new optional fields + tolerant-load test.
   `api/schemas.py`/`api/routes.py` surfacing + test. `pytest -m ""`
   checkpoint.
5. Real UK-pack re-ingest (manual); record real numbers in this plan file
   and `ROADMAP.md`/`CLAUDE.md`.
6. Once L2 has landed: run the L2 sweep + S1 distillation re-checks
   (acceptance criteria 3–4); record results, including the L2
   follow-up note if warranted.
7. `ruff check .`; full `git diff --stat` freeze check; final `pytest -m
   ""`.

## Critical files

- `ingest/grib_common.py` (new `coastal_fill_mask`/`apply_coastal_fill`)
- `ingest/fetch_grib_nomads.py`, `ingest/fetch_grib_ecmwf.py`,
  `ingest/fetch_era5_track.py`, `ingest/fetch_currents_cmems.py`,
  `ingest/merge_currents.py` (call-site wiring)
- `core/weather.py` (two new optional provenance fields, additive only)
- `api/schemas.py`, `api/routes.py` (health surfacing)
- `tests/test_ingest_grib_common.py` (new tests)
- `docs/plans/ticket-L2.md` (the acceptance-run dependency; re-read its
  §1b sweep script before step 6)
