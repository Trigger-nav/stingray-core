# Stingray — Working Prototype Roadmap

**Version 0.1 · July 2026 · Target: sea-trial milestone in ~6 months with 2–4 engineers + naval-architecture consultancy.**

Goal: from the current demo (validated UX + optimiser shape, synthetic everything) to software running against a real vessel and real weather, producing advisory passages and voyage debriefs.

**Carries forward from the demo:** mission-weights model, vessel-state/chat UX, twin component structure, DP optimiser shape, ingest scripts.
**Gets replaced:** synthetic twin coefficients, hand-drawn coastlines/bathymetry, corridor-limited routing, deterministic chat, everything in-browser.

---

## Phase 0 — Bench (weeks 1–8)

*Exit criteria: optimiser produces plans on real forecasts over real geography; verification methodology signed off.*

| # | Task | Notes |
|---|---|---|
| 0.1 | Monorepo scaffold: core (Python or Rust), CI, tests | Decision: Python first for model velocity; port hot paths later |
| 0.2 | Port demo optimiser core with unit tests | Weights model, constraints, ETA window carry over 1:1 |
| 0.3 | Real geography: GSHHG coastline land mask + GEBCO bathymetry | Free, global; replaces hand-drawn polygons |
| 0.4 | Open graph search: time-expanded A*/DP over sea-space lattice | Corridor DP becomes the fallback/fast path |
| 0.5 | GRIB pipeline in production: NOMADS + ECMWF open data, cfgrib parsing | **Done and verified end-to-end (2026-07-07):** both fetchers run live against real endpoints; cross-source consistency check passed (wave direction 16° mean disagreement — WW3 from-convention empirically confirmed); cfgrib fixture tests pass. Scheduling deliberately out of scope (edge-first — belongs with the Phase 1+ edge device, not this bench milestone). Residual: tighten `_find_var` matching (real cfgrib names now known). |
| 0.6 | Twin v1 offline: fit/validate tooling for the parametric components | **Done (2026-07-08), built against synthetic data per the agreed approach — did not wait for the naval-arch consult.** New `fit/` package (depends on `core/` one-way + `scipy`, own extras group — see CLAUDE.md's package-layout note): steady-state segment extraction (rejects manoeuvring/transients/tank-transfer artefacts, `fit/segments.py`); sequential prior-regularised least-squares fit of calm resistance + SFOC (`fit/calm_resistance.py`) then added resistance (`fit/added_resistance.py`, calm/SFOC held fixed); priors as per-parameter distributions (`fit/priors.py`, every one **provisional with a real published-method source**, pending naval-arch review); holdout validation, not in-sample point estimates (`fit/validate.py`). **Acceptance test passes** (`tests/test_fit_acceptance.py`): synthetic telemetry from a known, deliberately off-prior `VesselSpec` with realistic input+output noise + injected junk → fitted predictions agree with ground truth within 15% across a held-out grid, junk correctly excluded. Also verifies a required review amendment: single-engine-config data (a real identifiability degeneracy between the power curve and SFOC, see CLAUDE.md's gotcha) degrades gracefully rather than producing a confident-but-meaningless fit. `python3 -m fit.cli --synthetic-demo` runs the whole thing standalone. Naval-arch review of the model forms + prior values is the still-pending follow-up — a later, short review alongside 0.7's draft, before the Phase 1 pilot fit (ticket 1.5), not a blocker to this ticket. |
| 0.7 | **Savings-verification methodology** (counterfactual baseline maths, written + reviewed) | **Drafted (v0.1, July 2026):** `docs/methodology/savings-verification.md` + branded Word export. Core construction: model-counterfactual baseline with same-passage bias correction (savings = difference of two twin evaluations under identical weather — correlated model error cancels); P10 reporting for contractual claims; full-denominator anti-gaming rules; Phase 1 crossover validation programme. Pending: naval-arch + counterparty review (open questions listed in the doc); countersigned version removes `baseline_provisional=True`. |
| 0.8 | Routing safety constraints: min depth, TSS, no-go polygons from chart data **+ fine lattice refinement (adaptive resolution) near tight constraints** | **Done (2026-07-08).** Minimum depth is a hard constraint (`core/legs.py`: vessel `draft_m` + `min_under_keel_clearance_m`, pruned like land/no-go), with a `DEPTH_EXEMPT_RADIUS_NM=1.5` pilotage exemption near declared origin/destination (real ports/anchorages are frequently shallower than open-water margins — captain/local-knowledge scope, not this optimiser's, with regression tests covering both a real shallow port approach and a real shallow anchorage). No-go zones (`core/geography.RealGeography`) now load real, cited zones (marineregions.org gazetteer, MRGIDs 26848/3457) from `data/geography/nogo_western_med.json` instead of synthetic placeholders. Bonifacio Strait TSS is modelled as a **separation zone only** (hard no-go, `data/geography/tss_western_med.json`) — directional lane compliance is explicitly out of scope this ticket (no free, verified source for the real IMO lane geometry found); the file is clearly labelled placeholder. Adaptive per-stage lattice refinement (`core/lattice.py`'s `build_lattice(..., geography=...)`) handles Bonifacio's genuine scattered-islet resolution need, refining down to a 0.5nm floor with multi-pass diagnostics when still degraded at the limit. **This, combined with fixing a side-diversity-filter bug** (was constraining lane sign passage-wide instead of just the Corsica-spanning region, silently forcing the west route onto real Sardinian coastline at the final approach) **restores a feasible west-of-Corsica strait transit** — both regression tests flipped from diagnosing the strait as unreachable to asserting it's reachable (`tests/test_optimiser_regression.py`), and the charter-window regression now finds the tighter window feasible via the west route. See `CLAUDE.md`'s Bonifacio gotcha for the full empirical trace and `docs/plans/ticket-0.8.md` for the real-data sourcing record. Follow-ups: real TSS lane geometry (chart/IMO routeing-system publication), and precise no-go boundaries — Scandola/Iles Lavezzi are real, cited rectangular bounding boxes (marineregions.org), not the legal designation polygons themselves (each zone's `precise_boundary_verified` flag in the data file). |

## Phase 0→1 bridge — the live planner (agreed July 2026)

With Phase 0 complete, the core has no user-facing surface: the hosted demo still runs its synthetic JS brain. The bridge closes that gap and starts Phase 1's non-code tracks, **with a feature freeze on the optimiser until ticket 1.5 (real-data twin validation) renders its verdict** — every new feature before then builds on unvalidated foundations (risk #4).

| # | Task | Notes |
|---|---|---|
| B1 | API service: FastAPI wrapper over `core.optimiser.optimise` + scheduled GRIB fetch | Cheap single VM, password-protected; the deployment target the UI needs |
| B2 | Connect the demo UI to the API: swap the `<script id="core">` block for API calls | 1–2 weeks; keeps the validated UX, replaces the synthetic brain — the "live planner" for pilot/investor demos |
| B3 | Pilot recruitment pack: one-pager, data-agreement outline, install survey | `docs/pilot/` — feeds ticket 1.1 |
| B4 | Naval-arch engagement: single short review covering 0.6 model forms/priors + 0.7 methodology | Both artefacts ready; engage before Phase 1 fit |
| B5 | Capture kit BOM + order (ticket 1.2 hardware) | Long-lead item; order early |
| B6 | **Historical-data acquisition** — season(s) of monitoring-system exports, e-logbook dumps, noon reports, bunker records from candidate vessels/fleets | Potentially the fastest route to ticket 1.5's verdict: real-data twin fits *before* any hardware goes aboard, and it pre-builds 0.7's B2 historical baseline. A management company can provide fleet history under one NDA. Limits (don't oversell): usually no wave/motion data, coarse fuel resolution — accelerates the cold start and validates fitting on real-world mess; doesn't replace the capture kit. Technical enabler: **ERA5 reanalysis hindcast ingest** (small variant of the 0.5 pipeline — reanalysis instead of forecasts) to reconstruct weather along historical tracks. Ask is already in `docs/pilot/install-survey.md`. |
| B7 | **Historical-fit readiness** (enables B6; the fit pipeline itself is already passage-agnostic — `fit/` never touches routing) | Four parts: (1) *R1-lite for the data layer*: bbox as a parameter end-to-end in ingest/geography loading, track-driven (compute covering bbox from a historical track, fetch ERA5/GSHHG/GEBCO for it) — routing/lattice untouched; (2) *ERA5 track annotator*: (t, lat, lon) rows → hs/period/wave-dir/wind appended from reanalysis (CDS API, free registered key — not anonymous like NOMADS); (3) *import layer*: canonical telemetry schema + per-source adapters (monitoring CSV, e-logbook, noon reports), incl. a low-frequency entry path for pre-aggregated daily rows (bypasses segment extraction, wide uncertainty) and per-source STW-vs-SOG handling via the existing input-noise terms; per-source units/timezone audit (B1 at the import boundary); (4) *passage-level holdout* in `fit/validate.py` (segment-level holdout flatters autocorrelated historical data; validate across vessels where possible). |

Production bridge app (TS/React, offline-capable — ticket 2.2) starts during Phase 1, maturing alongside the data the capture kits gather.

## Phase 1 — Real data (weeks 6–16, overlaps Phase 0)

*Exit criteria: 30+ sea-days of telemetry from ≥1 vessel; twin v1 prediction error quantified and acceptable (fuel ±10% per passage at NMEA-tier).*

| # | Task | Notes |
|---|---|---|
| 1.1 | Recruit 1–2 friendly pilot yachts (LOI, data agreement) | Yacht-management relationships start here too |
| 1.2 | Capture kit from off-the-shelf parts: industrial ARM box, Actisense/Yacht Devices NMEA 2000 gateway, USB IMU, LTE | No custom hardware until rev B; ~€2k/kit |
| 1.3 | Edge logger: bus ingestion, signal normalisation, local store, store-and-forward sync | First real encounter with PGN variability — budget time |
| 1.4 | Minimal cloud: ingest API, TimescaleDB, auth, one ops dashboard | Boring tech, single provider, IaC |
| 1.5 | Fit twin v1 to pilot-vessel data; publish error bands | **Make-or-break validation of the core bet** |
| 1.6 | Install-survey checklist + commissioning runbook | Feeds the one-day-install claim with evidence |

## Phase 2 — Closed loop (weeks 14–26)

*Exit criteria: sea-trial passage planned by Stingray; predicted vs actual within stated confidence; first voyage debrief produced.*

| # | Task | Notes |
|---|---|---|
| 2.1 | Twin online learning on the edge; confidence intervals surfaced end-to-end | Kalman bias states + residual model per architecture §4 |
| 2.2 | Planner web app (demo UX rebuilt properly), served from edge box, offline-capable | Vessel-state visual + plan cards + mission presets |
| 2.3 | ECDIS route export (RTZ) | Keeps navigation in navigation software |
| 2.4 | Chat v1: LLM grounded in plan/twin state; demo's deterministic answers become its tools | Guardrails: never invents numbers; every figure from the twin |
| 2.5 | Live re-optimisation underway (forecast drift + performance drift triggers) | Quiet-by-default thresholds |
| 2.6 | Voyage debrief report generator (plan vs actual vs counterfactual) | The compliance/SEEMP evidence artefact — also the sales artefact |
| 2.7 | **Sea trial** on pilot vessel; publish results internally | The "working prototype" milestone |

## Beyond Phase 2 — route coverage (single route → global)

Superyacht movements are concentrated: Med summer, Caribbean winter, one transatlantic repositioning corridor, plus a handful of growing regions. **~6 region packs + 1 ocean corridor cover ~90% of superyacht miles** — global coverage is staged expansion, not a rewrite.

| Stage | Scope | Nature of work |
|---|---|---|
| R1 — Parameterise the region (during Phase 1) | Any A→B within one bbox becomes a "region pack": rasterised land-mask + bathymetry tiles + WPI port entries, generated by the existing ingest scripts over any bbox. **Endpoints are arbitrary navigable points — ports, named anchorages, dropped pins (anchorages are the common case for this fleet); per-vessel saved favourites in v1.** Hand-drawn corridors demoted to Med-only legacy. | Refactoring, not new science. **Engineering rule now: `OPERATING_AREA_BBOX` and `PORTS` become per-region-pack data — new code must not deepen them as global constants.** |
| R2 — Region pack pipeline (Phase 2) | Cloud-side pack generation (full Med, Caribbean/Bahamas, US East Coast, N. Europe, Red Sea/Gulf, SE Asia), synced to vessels ahead of need — the chart-folio model mariners already know. | Engineering + storage discipline (GEBCO fits on the edge box; masks tile to ~100s MB/region). |
| R3 — Ocean passages | Proper spherical geometry (equirectangular + single REF_LAT break beyond ~500nm; lattices follow great circles); receding-horizon planning (forecast skill ~10 days < transat duration → blend climatology/pilot-chart statistics beyond the horizon); currents become a real field (Gulf Stream > weather on this corridor — A4 graduates from zeros); tropical-cyclone forecast cones as time-dependent hard-constraint polygons. | The real algorithmic step. |
| R4 — Global topology | Two-level planner: coarse precomputed global sea graph decides strategy (straits, Suez vs Cape, canals as scheduled/fee-bearing edges); the existing regional lattice refines each section unchanged. Global TSS/ECA/MPA/security-area data. | Hierarchical routing is standard; the heavy lift is data licensing/curation, not code. |

Nothing in the current architecture fights this: edge-first survives (packs sync ahead, planning stays offline), the twin is geography-agnostic, the time-expanded search generalises. Only the geometry layer and corridor assumptions are Med-shaped today.

## Deferred (post-prototype, pre-product)

Custom edge hardware rev B (IEC 60945 EMC), vendor monitoring-system adapters (Böning/Praxis), fleet weather-correction network, fleet dashboard for managers, ENC licensing (S-57/S-63 via PRIMAR/UKHO), flag/class written comfort (start conversations earlier, per spec §11.4), anchor/hotel-load optimisation.

## Top risks

1. **Twin accuracy on thin data** (1.5) — mitigate with sister-ship priors, flowmeter upgrade on pilot vessel if needed.
2. **Bus-data chaos** (1.3) — mitigate with survey checklist and generous commissioning budget.
3. **Pilot vessel availability** (1.1) — recruit two, assume one.
4. **Scope creep toward product** — the sea trial needs one vessel and one passage type; resist fleet features until 2.7 is done.
