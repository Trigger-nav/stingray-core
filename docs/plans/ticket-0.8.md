# Ticket 0.8 — Routing safety constraints + adaptive lattice refinement

## Context

`core/twin.py`/`core/optimiser.py`'s hard-constraint mechanism (A5: prune,
never cost) currently covers land and a synthetic `NOGO` reserve list —
minimum depth is loaded (`RealGeography.depth_m`, real GEBCO data since
ticket 0.3) but never checked, and there's no TSS concept at all
(`CORE_PORTING_NOTES.md`'s A5 note: "Same for minimum depth (ticket
0.8)"). Separately, ticket 0.4's review found the open lattice can't
thread the real Bonifacio Strait at 5nm lane spacing — every plan
currently detours east-about (+~20nm), silently foreclosing tight ETA
windows a strait transit would meet (`CLAUDE.md`'s Bonifacio gotcha,
`tests/test_optimiser_regression.py`'s
`test_bonifacio_unreachable_at_current_lattice_resolution_is_diagnosed`
and `test_charter_window_infeasible_reflects_vessel_envelope_not_an_arbitrary_number`).

Acceptance (per this ticket's brief): real TSS lane geometry,
chart-derived no-go polygons, a minimum-depth hard constraint, adaptive
lattice refinement near tight constraints — and, concretely, the strait
transit becomes feasible again and the charter-window regression flips
back to feasible.

## Real-data sourcing: what's actually available (checked live, not assumed)

Spent real effort confirming what's freely obtainable before designing
around it — same discipline as ticket 0.5's NOMADS/ECMWF checks:

- **`protectedplanet.net` (WDPA)**: the REST API requires a free signup
  token I don't have this session (401 without one); direct bulk-download
  CDN guesses 404'd, and the real bulk file is a multi-GB global dataset
  regardless — not a fit for "one representative passage."
- **`marineregions.org` (VLIZ)**: the gazetteer *search* REST API works,
  tokenless, and returns real citable records — confirmed live: MRGID
  28301 "Bouches de Bonifacio, Iles des Moines" (Natura 2000 SCI), MRGID
  27539 "Iles Lavezzi, Bouches de Bonifacio" (Natura 2000 SPA), MRGID 3331
  "Strait of Bonifacio". Their WFS `gazetteer_polygon` layer (also
  tokenless, confirmed working against a sample feature) returned zero
  features for these specific MRGIDs, though — the real designated-area
  *polygons* live at EEA's own Natura2000 WFS, which needs more query
  iteration than this session's checks resolved (400 on a first guess).
- **EEA Natura2000 WFS, French INPN/data.gouv.fr open data**: real,
  genuinely free sources for the *French* side of these designations, but
  not resolved to a working query live in this session.
- **OSM/OpenSeaMap via Overpass API**: the realistic free source for TSS
  *lane* geometry (`seamark:type=separation_zone` etc. are real OSM/S-101
  tags navigational contributors maintain) — both the main instance and a
  mirror timed out/errored live in this session (busy public
  infrastructure, not a hard no).

**None of these resolved to a one-shot, verified, machine-readable
polygon pull in this session** — unlike GRIB's NOMADS/ECMWF, there's no
single stable free API for chart-grade TSS/MPA vector data. Real,
licensed alternative (SHOM/IIM ENC) is commercial. Decision, matching
ticket 0.5's eccodes-gap precedent: **build the real mechanism now**
(loadable data files, hard-constraint wiring, ingest script scaffolding
against the confirmed-working marineregions.org search), **flag exact
Bonifacio-specific vertex precision as needing one more sourcing pass**
(EEA WFS query iteration, or Overpass retried when public instances
aren't busy) rather than block the ticket on it or invent coordinates.
The geometry used for the two NOGO zones keeps today's real, correctly-
*named* reserves, re-derived from the confirmed-real gazetteer centroids
with a documented approximate extent — a genuine improvement in
provenance over today's fully-invented box coordinates, honestly labelled
as still-approximate.

## The actual Bonifacio blocker, found empirically (not the story CLAUDE.md's gotcha assumed)

Traced the real west-side lattice search failure directly against
`RealGeography` before designing the fix, rather than assume "scattered
islets" was the whole story:

- Per-stage **point** navigability near the strait is mostly fine at the
  current 5nm lattice (e.g. stage 27: 16/17 lanes navigable).
- Per-edge (leg) navigability between consecutive stages, **restricted to
  the west half of the lattice** (`lane <= 0`, the filter
  `optimiser.py` actually uses for the opposite-side diversity search),
  is genuinely degraded through the strait itself (stage 27->28: 12/18,
  67%) — a real, if partial, scattered-islet effect. This part matches
  the existing gotcha.
- But the **hard failure** (0/0 navigable edges, full stop) is at stage
  28->29 and beyond — the destination-approach stages. Traced further:
  stage 29's entire west-lane range, **including lane 0 — the literal
  unoffset rhumb-line point** — sits on real Sardinian coastline
  (confirmed: `is_land=True`, not a NOGO reserve). The unfiltered/primary
  search reaches the destination fine (by using positive lanes near the
  end, i.e. curving back toward/through the centreline); the west-filtered
  search cannot, because `opposite_filter = lambda lane: lane <= 0` is
  applied identically at *every* stage across the whole passage, forcing
  the final approach through land that has nothing to do with routing
  west of Corsica.

**So the fix has two independent parts, both real, both necessary:**
(1) the side-diversity filter is architecturally wrong — a constant
per-stage lane-sign constraint doesn't model "west of Corsica," it models
"west everywhere, including at the destination's front door," and (2) the
genuinely constrained mid-strait stages need finer lateral resolution to
route cleanly through the scattered islets once the filter stops
forcing a walk into Sardinia. Adaptive refinement alone would not have
fixed this; the filter fix alone gets much closer than expected but still
needs (2) for a *clean* (not just barely-surviving) threaded path — both
land in this ticket regardless, and get validated together during
implementation.

## Amendments (review, before implementation)

1. **Required: exempt final-approach legs from the depth prune.** Ports
   and anchorages are frequently in nearshore water that doesn't meet a
   generic open-water minimum-depth margin — a marina approach, a shallow
   anchorage bay. Applying the hard depth constraint uniformly would prune
   the very last leg(s) into the declared origin/destination, making
   *every* endpoint that isn't in deep water unreachable — including the
   default Porto Cervo destination, quite possibly. B6's `_validate_endpoint`
   already accepts responsibility for endpoint depth plausibility (the
   3-50m anchorage band); the last ~1.5nm of track into a validated
   endpoint is "pilotage scope" — precise final-approach hazard avoidance
   is a captain/local-knowledge job, not this optimiser's. `evaluate_leg`
   gains a depth-check exemption radius around declared endpoints (design
   section 2), with regression tests proving both that it works for a
   shallow port/anchorage and that it doesn't leak into a blanket "ignore
   depth near the destination region" hole.
2. **Adaptive refinement's multi-pass behaviour, defined.** One refinement
   pass (5nm -> 1.25nm) might not be enough for a genuinely narrow gap.
   Refinement iterates: re-probe the refined stage, refine again if still
   degraded, up to a bounded number of passes / a minimum step floor
   (below which further refinement isn't worth the lane-count cost). If a
   stage is still degraded at the floor, `build_lattice` does *not* error
   or block — the search already handles a genuinely-impassable region by
   finding no path there, same as today, and adaptive refinement is a
   best-effort improvement, not a completeness guarantee. It does emit a
   diagnostic (the `PruneDiagnostic` pattern from the 0.4 follow-ups) so a
   still-degraded stage after max refinement is visible, not silent.
3. **Empty distinguishing region, handled gracefully.** For an arbitrary
   origin/destination pair (not the Med default), the "stages spanning
   Corsica" region the side-diversity fix relies on (design section 6)
   may not exist at all — nothing forces a passage to pass anywhere near
   Corsica. When the region is empty, the opposite-side search runs
   *unconstrained* rather than erroring or applying a meaningless
   constraint; the existing `secondary["side"] != primary["side"]` check
   then correctly finds no genuine diversity and reports
   `route_side_unreachable` through the normal path — no special-casing
   needed at the call site, just at the region-computation itself (return
   "no constraint" rather than an empty-but-still-enforced range).
4. **`optimise()`-level runtime check added to verification**, not just
   the internal `_lattice_route_result`/lattice-diagnostic checks this
   plan already had — the full public API path (candidates, diagnostics,
   `missed_window`) gets exercised directly, not just the lower-level
   functions underneath it.

## Design

### 1. `core/vessel_spec.py` — draft + under-keel clearance

- `HullParticulars` gains `draft_m: float` (a real gap — currently
  absent entirely).
- `VesselSpec` gains `min_under_keel_clearance_m: float` — a simple flat
  safety margin (not vessel-class-derived UKC policy; that's a naval-arch
  question, same provisional status as ticket 0.6's fitted coefficients).
  Shipped default spec gets a real-ish placeholder (~2.5m draft for a 45m
  displacement motoryacht, ~2m UKC margin), marked provisional same as
  everything else in that YAML.

### 2. `core/legs.py` — minimum depth as a hard constraint (A5 pattern)

- New `_leg_depth_ok(p, q, geography, min_depth_m, *, exempt_points=(),
  exempt_radius_nm=1.5)`, same fixed-distance sampling + `lru_cache`
  pattern as `_navigable_along_leg` (reuses the same sample points —
  worth checking depth alongside land at the same interval rather than a
  second independent sampling pass). Each sampled point within
  `exempt_radius_nm` of *any* `exempt_points` entry skips the depth check
  for that sample (land/no-go checks still apply — the pilotage exemption
  is depth-only, per amendment 1).
- `evaluate_leg` gains `depth_exempt_points: tuple[LatLon, ...] = ()`,
  threaded through to `_leg_depth_ok`. Callers pass the request's real
  origin/destination: `core/optimiser.py`'s `_lattice_search` already has
  `lattice.origin`/`lattice.destination` in scope (no new parameter
  threading needed there); `_dp_route` uses `corridor.points[0]`/`[-1]`;
  `core/isochrone.py`'s functions already take `lattice` too. One
  mechanism, no call site needs bespoke plumbing.
- `LegResult` gains `depth_ok: bool` alongside `navigable` (kept
  separate, not folded into one flag — so a future prune-diagnostic can
  say *why* an edge was pruned, matching the 0.4-followup
  `PruneDiagnostic` precedent). Every hard-constraint prune site
  (`core/optimiser.py`'s `_lattice_search`/`_dp_route`,
  `core/isochrone.py`'s `_best_feasible_duration_h`) prunes on
  `navigable and depth_ok`, same mechanism as the existing
  `slam_event`/`overload` checks.

### 3. `core/geography.py` + new data files — real no-go, TSS separation zones

- `NOGO` moves from a hardcoded Python list to
  `data/geography/nogo_western_med.json` (matching the coastline/
  bathymetry file-loading pattern; per B6/ROADMAP's "per-region-pack
  data" direction — not a new global constant). Polygon-shaped (not just
  boxes) so precision can improve later without a schema change; today's
  vertices are still simple approximate boxes, re-derived from the real
  marineregions.org gazetteer centroids, labelled `"precise_boundary_verified": false`
  in the data file with the real MRGID citations.
- New `data/geography/tss_western_med.json`: Bonifacio Strait TSS as a
  **separation-zone polygon**, reusing the exact same no-go hard-constraint
  mechanism (a zone vessels must not transit) — tractable and real, not
  dependent on unresolved lane-geometry sourcing. Lane-*direction*
  compliance (which side, which heading) is **not** enforced as a hard
  constraint this ticket: modelling full COLREG Rule 10 lane discipline
  needs the lane geometry this session couldn't source precisely, and
  advisory-only design principle #2 argues against hard-enforcing a
  heading rule from placeholder geometry. Scoped down to the separation
  zone only, flagged clearly in the data file and docstring as a
  deliberate simplification, with the lane/heading model left as a
  documented ticket-0.8-followup once real geometry is sourced.
- `ingest/fetch_nogo_polygons.py` (new): queries marineregions.org's
  confirmed-working gazetteer search for the named reserves, writes the
  citation + approximate-extent data file, prints a loud note about the
  unresolved EEA/Overpass precision follow-up (same honesty pattern as
  `ingest/verify_grib_consistency.py`'s first-real-run checklist).

### 4. `core/lattice.py` — per-stage resolution + adaptive refinement

- `Lattice.cross_track_step_nm` becomes per-stage (`tuple[float, ...]`,
  not a scalar) — `Lattice.point(stage, lane)` looks up that stage's own
  step. `max_lane_per_stage` (already per-stage) falls out of the
  existing bbox-clipping logic unchanged when called with a stage's own
  (possibly finer) step.
- `build_lattice(..., adaptive_refinement=True)` (default on): after
  building the coarse base lattice, probes each stage-pair's edge
  navigability the same way this plan's investigation did by hand (cheap:
  `RealGeography.is_navigable` is an O(1) raster lookup). Any stage-pair
  falling below a navigable-edge-fraction threshold gets its *own* stage
  rebuilt at a finer step (e.g. 5nm -> 1.25nm), bounded to a local lateral
  window so lane count doesn't blow up across the whole lattice — only
  genuinely constrained stages get finer, open-water stages stay coarse
  and cheap.
- **Multi-pass, bounded (amendment 2).** One pass might not be enough for
  a genuinely narrow gap: after refining, re-probe the *refined* stage the
  same way; if still below threshold, refine again (5nm -> 1.25nm ->
  ~0.3nm), up to `MAX_REFINEMENT_PASSES` (e.g. 3) or a `MIN_STEP_NM` floor
  (e.g. 0.5nm), whichever binds first — below that floor, the lane-count
  cost isn't worth another pass. If a stage is still degraded at the
  floor/pass limit, `build_lattice` does not error or block (the search
  already handles a genuinely-impassable region by finding no path there,
  same as it does today for any hazard) — it records a
  `LatticeRefinementDiagnostic`-style note (stage index, final step,
  final navigable-edge-fraction) on the returned `Lattice` so a
  still-degraded stage is visible rather than silently accepted, echoing
  the `PruneDiagnostic` pattern from the 0.4 follow-ups.

### 5. `core/isochrone.py` — turn allowance in physical distance, not lane-index count

- `LANE_TURN_RATE` (a fixed lane-*index* count) becomes
  `LANE_TURN_RATE_NM` (a fixed *distance*, e.g. 10nm/stage), converted to
  a lane-index range per stage-pair using the **finer** of the two
  adjacent stages' steps — conservative, and what makes the coarse/fine
  boundary (where adaptive refinement kicks in) behave sanely: a stage
  transitioning from 5nm to 1.25nm spacing doesn't suddenly get an
  unrealistic physical turn allowance just because the index-count stayed
  fixed. `_neighbour_lanes` and `core/optimiser.py`'s equivalent inline
  computation both move to this helper (one implementation, not two, of
  what's meant to be identical logic — the two are already meant to
  match per `core/legs.py`'s "shared hard-constraint semantics" precedent).

### 6. `core/optimiser.py` — fix the side-diversity filter

- `lane_filter` signature changes from `(next_lane) -> bool` to
  `(stage, next_lane) -> bool` (the actual bug: the current filter can't
  see which stage it's constraining, so it applies the same rule at the
  destination's front door as it does mid-strait).
- New diversity mechanism: instead of constraining *every* stage's sign,
  require the *opposite-side* search to pass through the mid-passage
  "distinguishing region" (the stage-index range actually spanning
  Corsica, derivable from the lattice the same way this plan's
  investigation located it — stage centres whose longitude falls within
  Corsica's span) on the requested side; lane choice is unconstrained
  everywhere else, so the route can legitimately curve back toward/past
  the centreline approaching either endpoint. `_route_signature`'s
  existing side classification (mean longitude within the Corsica lat
  band) already provides exactly the geometry needed to define this
  region — reused, not reinvented.
- **Empty region, handled gracefully (amendment 3).** For an arbitrary
  origin/destination pair, no stage may fall within Corsica's span at
  all. The region-computation helper returns `None` (not an empty range)
  in that case, and the caller reads `None` as "no constraint" — the
  opposite-side search then runs fully unconstrained, and the existing
  `secondary["side"] != primary["side"]` comparison correctly finds no
  genuine diversity and reports `route_side_unreachable` through the
  normal path. No special-casing at the search call site, only at the
  region computation itself.

## Tests

- `tests/test_legs.py`/`test_optimiser_constraints.py` additions: a
  segment shallower than `draft_m + min_under_keel_clearance_m` is pruned
  outright (same B5 slamming-test pattern: infeasible at every
  weight/pace, not merely down-ranked); a segment meeting depth is
  unaffected.
- **Pilotage-exemption regression tests (amendment 1, required):** using
  real depth data, confirm (a) a port/anchorage endpoint whose real depth
  sits below the generic `draft_m + min_under_keel_clearance_m` margin is
  still reachable end-to-end via `optimise()` — the final-approach leg(s)
  within the exemption radius aren't pruned solely for depth; (b) a
  shallow patch *outside* the exemption radius (elsewhere along the same
  track) is still correctly pruned — proving the exemption is scoped to
  the declared endpoints, not a blanket regional pass. Uses the B6
  anchorage-endpoint test fixtures (`test_optimiser_endpoints.py`'s
  `PLAUSIBLE_ANCHORAGE`) as a starting point; real depths checked against
  `RealGeography` during implementation to pick a genuinely-shallow real
  point rather than assume one.
- `tests/test_geography.py`/`test_real_geography.py` additions: NOGO
  loads from the new data file with the same zones as before (regression:
  existing no-go tests keep passing); TSS separation zone is a hard
  no-go the same way.
- `tests/test_lattice.py` additions: adaptive refinement produces a finer
  step exactly at stages whose edge-navigability probe falls below
  threshold, coarse elsewhere; a stage requiring more than one pass
  refines iteratively up to the pass/floor limit and reports the
  refinement diagnostic when still degraded at that limit (amendment 2);
  `LANE_TURN_RATE_NM`-based neighbour range scales correctly across a
  coarse/fine stage boundary.
- **Empty distinguishing region (amendment 3):** an origin/destination
  pair nowhere near Corsica (e.g. two points both well east or well west
  of it) doesn't crash or misbehave — the opposite-side search runs
  unconstrained and diversity gracefully reports unavailable rather than
  erroring on an empty required-stage range.
- **The two flipped regression tests** (re-derive exact numbers empirically
  during implementation, same as tickets 0.5/0.6's thresholds):
  - `test_bonifacio_unreachable_at_current_lattice_resolution_is_diagnosed`
    gets rewritten to assert the *opposite*: under calm weather, both
    sides are now reachable (`{c.side for c in result.candidates} ==
    {"W", "E"}`, no `route_side_unreachable` diagnostic) — pinning the
    fix, not just removing the old assertion.
  - `test_charter_window_infeasible_reflects_vessel_envelope_not_an_arbitrary_number`'s
    13.5h window flips to `missed_window is False` once the shorter west
    transit is available; re-derive the actual fastest feasible duration
    against the fixed code and pick a window that's still meaningfully
    tight (infeasible via the old east-only routing, feasible via the new
    west route) rather than reusing 13.5h by coincidence.

## Docs

- `CLAUDE.md`: rewrite the Bonifacio gotcha's "still open" framing to
  "resolved" (dated when implemented), replacing the old "scattered
  islets are the whole story" narrative with this investigation's actual
  finding (filter bug + genuine mid-strait resolution need), and add the
  TSS-lane-geometry sourcing gap as a new, explicit follow-up (parallel to
  the WW3-direction-convention-style "needs one more verification pass"
  notes).
- `ROADMAP.md`: mark 0.8 done, noting the deliberate TSS lane-direction
  scope cut and the precise-no-go-boundary sourcing follow-up.

## Verification

- `pytest -m ""` (full suite) green, `ruff check .` clean.
- Re-run this plan's own diagnostic (west-filtered `_lattice_route_result`
  against `RealGeography`) after implementing — confirm it now returns a
  result, not `None`.
- **`optimise()`-level runtime check (amendment 4):** not just the
  internal diagnostic above — call the full public `optimise()` on the
  default Antibes<->Porto Cervo request under calm weather and confirm
  directly on the returned `PlanResult`: both `"W"` and `"E"` appear
  across `result.candidates`, no `route_side_unreachable` diagnostic:
  then a second `optimise()` call with a charter-tight `latest_arrival_h`
  (the regression test's re-derived value) confirms
  `result.missed_window is False`. Run both by hand during implementation
  before locking in the regression tests' exact numbers, then again as
  part of the automated suite.
