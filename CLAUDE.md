# Stingray — Project Context

Stingray (Stingray Marine Technology, stingraymarinetechnology.com) is a **passage & performance optimisation** platform for superyachts: multi-objective optimisation of route, speed, and vessel setup (engine config, trim) against a captain-selectable mix of fuel economy, ETA, comfort, and wear — sold to yacht management companies as an efficiency + IMO/SEEMP compliance tool. Advisory-only by design (no control outputs); the captain always retains authority.

## Key documents (read before large tasks)

- `PRODUCT_SPEC.md` — PRD; §4 differentiators, §11 resolved decisions
- `TECHNICAL_ARCHITECTURE.md` — target architecture; §4 twin, §5 optimiser, §6 weather pipeline
- `ROADMAP.md` — **current work plan; Phase 0 tickets 0.1–0.8 are the active scope**
- `BUSINESS_CASE.md` — market/pricing context
- `prototype/` — deployed HTML demo (github.com → trigger-nav.github.io/stingray-demo). Its `<script id="core">` block contains the validated v0 twin + DP optimiser to port in ticket 0.2. Treat as reference, not production code.

## Current state (July 2026)

Demo complete and hosted (synthetic vessel/chart data, live weather via Open-Meteo, refreshed 6-hourly by GitHub Actions). Phase 0 (bench) is starting: production optimiser core on real geography and real forecasts. No real vessel data yet — that's Phase 1.

## Non-negotiable design principles

1. **Edge-first:** all planning/inference must run offline on modest ARM hardware; cloud is never in the critical path.
2. **Advisory-only:** no control outputs, ever, in this phase.
3. **Graceful degradation:** every feature must define behaviour at each sensor tier (full integration / NMEA + engine fuel rate / NMEA + manual fuel).
4. **No invented numbers:** every prediction carries a confidence interval; the twin is physics-informed and interpretable (separate calm-water, added-resistance, SFOC, motion components), not a monolithic black box.
5. **Privacy:** yacht position data is owner-sensitive; per-vessel isolation, anonymised aggregates only.

## Phase 0 engineering conventions

- Python 3.11+, `pyproject.toml`, `pytest`, `ruff`; type hints throughout. Port hot paths to Rust later only if profiling demands it.
- Package layout: `core/` (twin, optimiser, weather) as a library with no I/O side effects; `ingest/` for data acquisition; thin CLIs on top.
- Real geography: GSHHG coastlines (land mask), GEBCO 2024 bathymetry. Real weather: NOAA NOMADS GRIB2 + ECMWF open data, parsed with cfgrib (needs `brew install eccodes`).
- Every optimiser change must keep the regression suite green: known scenario → expected plan-shape assertions (e.g. mistral + comfort-heavy weights ⇒ lee-side routing; ETA window infeasible ⇒ flagged, fastest-first ordering).
- Ticket 0.7 (savings-verification methodology) is a written deliverable, not code — do not skip it; the commercial claim depends on it.

## Gotchas

- The demo twin's coefficients are synthetic. Port the *structure*, not the constants; constants become fitted parameters with priors in Phase 1.
- Bonifacio Strait: TSS + Bouches de Bonifacio reserve; routing must respect no-go polygons and minimum-depth constraints (ticket 0.8).
- Time-dependent costs: weather varies over the passage — the search must be over a time-expanded graph, not a static one.
- GRIB conventions vary (0–360 vs −180–180 longitudes; wave direction "from" vs "to"). Normalise at the ingest boundary and test it.
