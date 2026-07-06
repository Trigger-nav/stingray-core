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
| 0.8 | Routing safety constraints: min depth, TSS, no-go polygons from chart data | Advisory-only posture still requires sane routes |

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

## Deferred (post-prototype, pre-product)

Custom edge hardware rev B (IEC 60945 EMC), vendor monitoring-system adapters (Böning/Praxis), fleet weather-correction network, fleet dashboard for managers, ENC licensing (S-57/S-63 via PRIMAR/UKHO), flag/class written comfort (start conversations earlier, per spec §11.4), anchor/hotel-load optimisation.

## Top risks

1. **Twin accuracy on thin data** (1.5) — mitigate with sister-ship priors, flowmeter upgrade on pilot vessel if needed.
2. **Bus-data chaos** (1.3) — mitigate with survey checklist and generous commissioning budget.
3. **Pilot vessel availability** (1.1) — recruit two, assume one.
4. **Scope creep toward product** — the sea trial needs one vessel and one passage type; resist fleet features until 2.7 is done.
