# Stingray — Project Context

Stingray (Stingray Marine Technology, stingraymarinetechnology.com) is a **passage & performance optimisation** platform for superyachts: multi-objective optimisation of route, speed, and vessel setup (engine config, trim) against a captain-selectable mix of fuel economy, ETA, comfort, and wear — sold to yacht management companies as an efficiency + IMO/SEEMP compliance tool. Advisory-only by design (no control outputs); the captain always retains authority.

## Key documents (read before large tasks)

- `PRODUCT_SPEC.md` — PRD; §4 differentiators, §11 resolved decisions
- `TECHNICAL_ARCHITECTURE.md` — target architecture; §4 twin, §5 optimiser, §6 weather pipeline
- `ROADMAP.md` — **current work plan; Phase 0 tickets 0.1–0.8 are the active scope**
- `CORE_PORTING_NOTES.md` — **read before tickets 0.2/0.4/0.5**; demo shortcuts that must not be ported, conventions (units, weather schema), acceptance criteria
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
- Bonifacio Strait: TSS + Bouches de Bonifacio reserve; routing must respect no-go polygons and minimum-depth constraints (ticket 0.8). **Concrete finding (ticket 0.4 review, still open):** the strait is a scattered field of dozens of small real granite islets (Iles Lavezzi) — `core/corridors.py`'s `corridor_west()` hand-drawn D→D2 waypoint segment cuts straight through them and is infeasible against `RealGeography` at every speed/engine combination, confirmed not fixable by widening its lateral-offset/turn-rate allowance (a two-waypoint straight segment can't thread a scattered reef field). Needs the real TSS lane geometry + chart-derived no-go data in ticket 0.8, not a patch to the existing waypoints. `core/optimiser._baseline_route` currently routes around this by using the open lattice search (ticket 0.4) instead of `corridor_west` — see that function's docstring. **Update (0.4 re-review):** the lattice can't thread the real ~3nm Lavezzi–Sardinia channel at 5nm lane spacing either — *all* current plans go east-about Corsica (+~20nm), which is safe but suboptimal, and makes tight ETA windows infeasible that a strait transit would meet. Ticket 0.8's scope therefore includes fine lattice refinement near constraints (adaptive resolution), not just TSS/no-go data. Related: the candidate speed set should derive from the vessel's feasible load envelope (17 kn is silently overload-pruned by the wear policy on the default 50m spec — regression expectations must follow the physics, and `PlanResult` should carry prune diagnostics so absent options are explainable on the bridge).
- Time-dependent costs: weather varies over the passage — the search must be over a time-expanded graph, not a static one.
- GRIB conventions vary (0–360 vs −180–180 longitudes; wave direction "from" vs "to"). Normalise at the ingest boundary and test it. **Ticket 0.5 findings (confirmed live against real NOMADS/ECMWF endpoints during scoping, `docs/plans/ticket-0.5.md`):** NOMADS' `filter_gfs_0p25_1hr.pl`/`filter_gfswave.pl` grib-filter endpoints do real server-side bbox subsetting (tiny downloads, hourly GFS wind + WW3 wave through 48h+); ECMWF open data has no server-side subsetting but ships a `.index` byte-offset sidecar per file enabling cheap HTTP Range requests for a single param out of a whole-globe file. ECMWF's `mwd` (wave stream) is confirmed WMO from-convention. **NOAA WW3's `DIRPW`/`WVDIR`/`SWDIR` from/to convention is NOT empirically verified** — no `eccodes` available in the scoping sandbox to decode a real file (`ingest.grib_common.WW3_DIRECTION_IS_TO_CONVENTION`, currently `False`, is the single toggle if this turns out wrong). **First real run checklist**, once `eccodes` is installed (`brew install eccodes` / `apt-get install libeccodes-dev`): (1) run `python3 -m ingest.fetch_grib_nomads` and `fetch_grib_ecmwf`, confirm both succeed; (2) run `python3 -m ingest.verify_grib_consistency` — cross-source agreement on wave direction both sanity-checks the whole pipeline *and* empirically confirms/refutes the WW3 convention assumption (agreement near 0° confirms it; near 180° means flip the toggle); (3) `tests/test_grib_parsing.py`'s `pytest.importorskip("cfgrib")`-guarded tests (real committed GRIB2 fixtures under `tests/fixtures/grib/`) should now run instead of skip — check they pass, and tighten the permissive variable-name matching in `ingest/fetch_grib_nomads.py::_find_var` once the real cfgrib-mapped names for WW3's fields are confirmed.
