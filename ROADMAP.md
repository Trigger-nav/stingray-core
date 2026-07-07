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
| 0.5 | GRIB pipeline in production: NOMADS + ECMWF open data, cfgrib parsing, scheduled | Extends `ingest/fetch_grib_nomads.py` |
| 0.6 | Twin v1 offline: parametric components, fit/validate tooling | Naval-arch consult engaged here |
| 0.7 | **Savings-verification methodology** (counterfactual baseline maths, written + reviewed) | The sales claim depends on this; agree it before any pilot |
| 0.8 | Routing safety constraints: min depth, TSS, no-go polygons from chart data **+ fine lattice refinement (adaptive resolution) near tight constraints** | Advisory-only posture still requires sane routes. **0.4 re-review finding:** chart data alone won't be enough at Bonifacio — the open lattice can't thread the real ~3nm Lavezzi–Sardinia channel at the current 5nm lane spacing either, so every plan currently detours east-about (+~20nm), which is safe but silently forecloses tight ETA windows a strait transit would meet. Needs adaptive/local lattice refinement near narrow constrained passages, not just no-go polygon data. See `CLAUDE.md`'s Bonifacio gotcha. |

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
