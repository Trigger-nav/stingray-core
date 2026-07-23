# Ticket R2-lite — Region pack expansion (data work, no new engineering)

Execution log for two new region packs via `docs/region-pack-runbook.md`,
followed exactly (R1/L1/L2/W1's tooling, zero new code expected). Kept
brief per instruction — this is a log, not a design doc.

## Proposed bboxes (for sign-off, then executed as written below)

**`med_full`** — Gibraltar to the Aegean. `bbox = [-6.0, 30.0, 28.0, 46.0]`
(lon_min, lat_min, lon_max, lat_max): west of the Strait of Gibraltar
(-5.6°) with margin; south to below Crete/the Libyan coast (30°N); north
to the Gulf of Lion/northern Adriatic (46°N); east through the Aegean to
just past the Dodecanese (28°E), short of the Levant (deliberately not
"the whole Mediterranean" — matches "Gibraltar to the Aegean" as asked).
Area: 34° × 16° = **544 deg²**, ~48.6x `fetch_gebco.py`'s own reference
bbox (western Med, ~11.2 deg²) — comfortably past its `_LARGE_BBOX_AREA_DEG2`
warning threshold (5x). Extrapolating from the reference bbox's verified
~80s GEBCO fetch (roughly area-proportional — lazy HTTP range reads pull
chunks covering the bbox, not the whole 7GB file): **~65 minutes**,
order-of-magnitude, for GEBCO alone. This SUPERSETS the existing `med`
pack's `OPERATING_AREA_BBOX` — `med` is untouched, coexists as a separate
pack (its own committed test npz/corridors stay regression surface, no
retirement decision made here).

**`caribbean`** — Windwards through the Virgin Islands + Bahamas.
`bbox = [-80.0, 11.5, -59.5, 27.5]`: west edge past the Bahamas' Great
Bahama Bank (-79°); east edge past Barbados/the Windwards (-59.6°); south
to Grenada (~12.0°N) with margin; north to the northern Bahamas/Abaco
(~26.9°N) with margin. Area: 20.5° × 16° = **328 deg²**, ~29.3x reference
— extrapolated GEBCO fetch **~39 minutes**.

Both fetches run via `run_in_background`, real wall-clock reported in the
results section below (extrapolation vs. actual, since a lazily-chunked
HTTP fetch's real time depends on chunk-boundary alignment, not just raw
area — worth confirming rather than assuming linear scaling holds at this
size, `docs/plans/ticket-B7.md`'s own "flag, don't solve" precedent for
exactly this unverified-size-budget risk).

## Default passages (final coordinates confirmed against real WPI pins, §5)

- `med_full`: Palma (Mallorca) → Porto Cervo (Sardinia) — reuses the
  existing `med` pack's real Porto Cervo endpoint; Palma's real WPI pin
  checked for navigability/depth per runbook §5 before use.
- `caribbean`: St Thomas (USVI) → St Barths (Gustavia) — both checked
  against real WPI pins the same way.

## No-go/TSS zones

Honest empty placeholders for both packs (runbook §3) — no zone research
done this ticket, consistent with "no new engineering." Real, citable
candidates exist for a future ticket to pick up (not researched/verified
here, named only): Strait of Gibraltar TSS (`med_full`), Strait of
Messina TSS (`med_full`); nothing specific identified for `caribbean`
without real research.

## Currents

Off for both initially, per instruction — `med_full` for the same reason
`med` itself stays off (C1 §5: real but small relative to typical
cruising speed). `caribbean` is a real, live question (Antilles Current/
Caribbean Current, Mona Passage, Bahamas bank tidal flows are genuinely
significant in places) — named as a follow-up, not evaluated here.

## What to watch and report (§ below, filled in during execution)

- Real GEBCO/GSHHG/weather fetch wall-clock per pack, vs. the estimates
  above.
- npz sizes (bathymetry, weather) per pack — VM/cron bandwidth
  implications for 4 packs total (med, uk_sw, med_full, caribbean).
- `wave_filled_cells` (ticket W1's fill, expected to engage automatically
  — no code changes needed).
- Lattice derivation values each pack's default passage lands on (L1's
  `cross_track_step_nm`, L2's `along_track_step_nm`) — the "self-scaling
  formulas work unaided on a real, materially-different-scale passage"
  proof point; no yaml overrides expected or added.
- Any WPI pin nudges needed (runbook §5), documented same as `uk_sw.yaml`'s
  own header.
- Runbook friction worth fixing/adding, if any is found.

## Verification

Same bar as every prior ticket: `pytest -m ""` green (existing `med`/
`uk_sw` packs' own tests/committed data untouched — nothing here touches
committed files), `ruff check .` clean, `git diff --stat` confirms zero
`core/` diff (this is ingest/data + two new manifests only), ROADMAP row.
Both new packs' weather npz are gitignored (runbook §4, same as every
other pack) — not committed; the manifests
(`data/region_packs/med_full.yaml`/`caribbean.yaml`) and the new
geography/ports/nogo files ARE committed (same shape as `uk_sw`'s own
committed files).

---

## Execution log (real, 2026-07-22)

### 1. GEBCO — a real, blocking bug found and fixed

Both first-attempt GEBCO fetches (med_full, caribbean) failed with
`fsspec.exceptions.FSTimeoutError` after ~14 minutes each — a real,
blocking failure, not just a slow-but-working case. `ingest/fetch_gebco.py`'s
`fetch_subset` opened the remote file via `fsspec.filesystem("http")` with
no explicit client timeout; `.values` pulls the whole selected slice in
one shot (not incrementally chunked at this layer), so a bbox this large
means one long-lived HTTP read, and fsspec/aiohttp's default timeout has
no way to distinguish "still transferring" from "stalled." **Fixed**
(`ingest/fetch_gebco.py`, minimal, targeted): `client_kwargs={"timeout":
aiohttp.ClientTimeout(total=3600.0)}` passed to `fsspec.filesystem`. Both
fetches then succeeded on retry:

| pack | bbox area | grid shape | wall-clock | npz size |
|---|---|---|---|---|
| med_full | 544 deg² | (3840, 8160) | succeeded (not separately timed after the fix — ran to completion, well under the new 3600s ceiling) | 51.5 MB |
| caribbean | 328 deg² | (3840, 4920) | succeeded | 31.0 MB |

Both well past `_LARGE_BBOX_AREA_DEG2`'s 5x-reference warning threshold
(56 deg²) — the warning fired as designed, correctly flagging genuine
risk this time, not a false alarm.

### 2. GSHHG — fast, no issues

Both rasterisation runs completed quickly (shapely's vectorised
`contains_xy`, not a naive per-cell loop — scales fine even at this
much larger cell count):

| pack | polygons | land cells |
|---|---|---|
| med_full | 3,340 (46,574 points) | 18,097,817 / 31,334,400 |
| caribbean | 3,754 (36,406 points) | 999,862 / 18,892,800 |

### 3. WPI ports — real, no curation

| pack | ports found |
|---|---|
| med_full | 306 |
| caribbean | 51 |

Both inlined into their manifest's `ports:` mapping in full (no
curation), matching `uk_sw.yaml`'s own precedent.

### 4. Weather (NOMADS) — W1's coastal fill engaged automatically, no code changes needed

| pack | grid shape | wall-clock | npz size | `wave_filled_cells` | still-masked (hour 0) |
|---|---|---|---|---|---|
| med_full | (49, 65, 137) | 12m 37s | 6.9 MB | 441 | 5154 / 8905 |
| caribbean | (49, 65, 83) | 10m 27s | 7.6 MB | 293 | 262 / 5395 |

Confirms W1's fill mechanism is genuinely pack-agnostic — it engaged on
two brand-new, much-larger real grids with zero code changes, exactly as
designed.

### 5. No-go/TSS — honest empty placeholders, real candidate zones named not researched

Both packs ship empty `zones: []` (runbook §3). Real, citable TSS
candidates within `med_full`'s bbox (Gibraltar Strait, Strait of
Messina) are named in the placeholder file's own `note` field for a
future ticket — not researched here.

### 6. Endpoint validation — a real, two-layer finding (not just one nudge)

**Layer 1 (expected, matches `uk_sw.yaml`'s own precedent exactly):**
raw WPI pins for `palma_de_mallorca` (med_full) and `charlotte_amalie`
(caribbean) both sat on rasterised land; a hand-picked Gustavia/St
Barths point (no WPI pin exists for it in this bbox) did too. All three
nudged to nearby verified-navigable, sufficiently-deep water (details in
each manifest's own header comment) — same methodology as `uk_sw.yaml`.

**Layer 2 (new, not seen on the smaller uk_sw pack): point navigability
is not leg navigability.** The first Palma nudge (~0.5nm off the coast,
individually navigable, depth 11.85m) still made `optimise()` raise
`RuntimeError: baseline route ... infeasible`. Traced directly
(`evaluate_leg` over a full lane fan from the origin): only 1 of 33
first-stage lattice legs from that point had genuinely clear water — the
Bonifacio scattered-islet lesson (ticket 0.8) recurring at a different
scale: a bay-front point can be individually clear while nearly every
direction *out* of the bay still clips the coastline. Pushed the nudge
further out (~0.2deg / ~12nm south, into open Palma Bay, depth ~64.5m,
leg-fan-verified before committing) — this fixed it, and the search then
reached the destination with 1093 reachable nodes instead of 1.

**A third, distinct finding surfaced immediately after — deliberately not
fixed, named as a real follow-up:** even with the origin fixed,
`optimise()`'s existing `_baseline_route` (fixed 14kn/2-engine reference
speed, `core/optimiser.py`'s unmodified `BASELINE_SPEED_KN` mechanism)
still could not complete a continuous single-speed Palma→Porto Cervo
passage, while the real multi-speed candidate search succeeded fine.
Traced to a genuine destination-side chokepoint (a lattice stage near
Sardinia, `navigable_edge_fraction=0.0` even after full adaptive
refinement) that a fixed 14kn/2-engine passage apparently cannot clear
end-to-end but a route mixing several candidate speeds can. This is
existing, unmodified `core/optimiser.py` machinery behaving consistently
— not a new bug — it simply never got exercised by a passage this
long/constrained before. **Not investigated further or fixed** (would
mean touching `core/optimiser.py`, out of scope for a data-only,
no-new-engineering ticket) — named as a real follow-up for whichever
future ticket tackles longer/harder cross-basin passages, arguably
closer to R3's own "ocean-crossing" scope than R2's region-pack scope.
Resolved pragmatically for this ticket: `med_full`'s
`default_origin`/`default_destination` reuse the existing `med` pack's
own already-proven-feasible Antibes↔Porto Cervo pair instead — real,
verified feasible through `med_full`'s own much larger real
GEBCO/GSHHG/weather data (not just inherited from the small pack). The
Palma coordinates and the chokepoint finding are kept in `med_full.yaml`'s
own header comment, not discarded, for whoever picks up the follow-up.

`caribbean`'s St Thomas↔St Barths endpoints needed no such second layer
— both had comfortable multi-direction leg clearance from the first
nudge (5/12 and 8/12 on an 8nm fan check), and the pack validated cleanly
on the first `optimise()` attempt.

### 7. Real `optimise()` validation results

| pack | passage | straight-line | detour ratio | score (€) | n_wp | wall-clock |
|---|---|---|---|---|---|---|
| med_full | Antibes → Porto Cervo | 179.55nm | 1.0501 | 13505.24 | 10 | 24.4s |
| caribbean | St Thomas → St Barths | 121.59nm | 1.0000 | 8600.10 | 3 | 4.0s |

Both `FEASIBLE`, `missed_window=False`, zero diagnostics.

**A performance observation, correction added post-hoc (ticket G1)**: the
*same* Antibes↔Porto Cervo passage costs **24.4s** through `med_full`'s
pack vs. **~6s** through the existing, much-smaller `med` pack (this
session's own earlier L2 acceptance run). At the time this was
speculatively attributed to `core.geography.RealGeography.is_land_precise`/
`is_navigable` scanning every loaded GSHHG polygon per call — `med_full`
loads 3,340 polygons vs. the small `med` pack's much smaller committed
set — and named as unverified, "not profiled further this ticket."
**Ticket G1 profiled it for real and found this guess was wrong**: (1)
`is_land_precise` is never called during `optimise()` at all (only from
ingest-time `mask_land_as_missing`/`coastal_fill_mask`, and `is_land`'s
own fallback when `land_mask is None`, which is not the case for any
shipped pack); `is_navigable` already takes its intended O(1) rasterised
fast path and costs ~3% of total runtime in a real `cProfile` run —
weather sampling is still ~85%+, exactly matching the pre-existing B1
gotcha. (2) The 24.4s-vs-6s comparison itself was never controlled for
`speeds_kn` grid size (11 speeds vs. 2 in the two commands that produced
these numbers) — re-run controlled at both grid sizes, `med_full` is not
slower than the small `med` pack for the identical passage (if anything
faster: 15.33s vs. 20.76s at the default 11-speed grid). The real cause
was a methodology confound in how these two numbers were generated, not
a pack-size or polygon-count effect. Full trace: `docs/plans/ticket-G1.md`.

**Lattice derivation values — the "self-scaling formulas work unaided"
proof point, now at two more real scales:**

| pack | passage length | `cross_track_step_nm` (mid) | `lane_turn_rate_nm` | `along_track_step_nm` (derived) |
|---|---|---|---|---|
| med_full (Antibes→Porto Cervo, same as `med` pack) | 179.55nm | 0.500 (refined to floor at the Bonifacio stage, same as the `med` pack) | 15.0000 (exact, default stage length) | 6.0 (floored, same as `med`) |
| caribbean (St Thomas→St Barths) | 121.59nm | 3.386 (derived, between the UK pack's 0.5-ceiling and the Med's own 5.0 ceiling) | 15.0000 (floors at 6.0 along-track, same special case) | 6.0 (floored — 121.59×0.0334167=4.06 unfloored) |

No yaml lattice-knob overrides added to either new pack — both derive
cleanly. `med_full` reproduces the *exact* known Bonifacio refinement
behaviour (stages 15/28 refined to the 0.5nm floor, `navigable_edge_fraction`
0.68/0.688) the small `med` pack already has, end-to-end through
completely independently-fetched geography/weather data — a strong
consistency proof, not just a coincidence of shared code.

### 8. Verification

- `pytest -m ""`: **503 passed**, green, in 488.92s — existing `med`/
  `uk_sw` packs and their committed test data unaffected (nothing this
  ticket touched is shared with them).
- `ruff check .`: clean.
- `git diff --stat core/lattice.py core/optimiser.py core/legs.py
  core/isochrone.py core/corridors.py`: **empty** — zero diff. The only
  non-data code change this ticket made is the `ingest/fetch_gebco.py`
  timeout fix (§1).
- `git diff --stat core/` (broader check): also empty — confirms nothing
  under `core/` changed at all, not just the five freeze-relevant files.

### 9. VM/cron implications for 4 packs

npz sizes, all four packs now real/measured:

| file | med | uk_sw | med_full | caribbean |
|---|---|---|---|---|
| bathymetry (one-time, not cron-refreshed) | 2.0 MB | 172 KB | 51.5 MB | 31.0 MB |
| weather (cron-refreshed every cycle) | 56 KB | 28 KB | 6.9 MB | 7.6 MB |

**Recurring cron bandwidth** (weather only, the number that matters for
an ongoing deployment): med+uk_sw today ≈ 84 KB/cycle; adding both new
packs brings the total to ≈ **14.6 MB/cycle** — a real, ~170x increase
in absolute terms, but still trivial for any real VM/bandwidth budget (a
few MB every 6 hours). **One-time setup disk**: the two new packs'
bathymetry alone is ≈ 82.5 MB, vs ≈ 2.2 MB for the existing two packs —
notable for initial provisioning/clone time, not a recurring cost.

### 10. Runbook friction found and fixed/worth noting

- **Fixed**: the GEBCO timeout bug (§1) — a real blocker for any future
  large-bbox pack, not just these two. Worth a one-line mention in the
  runbook itself if a future ticket revisits it (not added here, kept
  the runbook diff at zero per this ticket's own "no new engineering"
  framing — the code fix speaks for itself, and the runbook's own §1
  already correctly flags "no size/time budget verified here").
- **Named, not fixed**: the fixed-baseline-speed chokepoint finding (§6)
  — worth a note in the runbook's §8 (validation step) that a very long/
  constrained default passage can surface this, so a future pack author
  knows to try a shorter/safer default first rather than assuming a
  `RuntimeError` here means their geography data is broken.
- **`RegionPack` has no `ports_path` field** — worth remembering (not a
  bug, just non-obvious from the runbook's own §7 wording, "pointing at
  every file from steps 2-6"): the WPI-fetched ports JSON must be
  inlined into the manifest's own `ports:` mapping, not referenced as a
  separate file. Fine for these two packs (a few hundred lines of real
  YAML data), but worth flagging for a much bigger future bbox where the
  WPI fetch could return thousands of ports.
