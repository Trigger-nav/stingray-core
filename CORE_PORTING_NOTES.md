# Core Porting Notes — demo → production (tickets 0.2, 0.4, 0.5)

Review of `prototype/stingray_planner.html` `<script id="core">` before porting. **The component structure, weighted scalarisation, ETA-window handling, candidate diversity, and regression behaviours carry over. The items below do not.**

## A. Demo shortcuts that must NOT be ported as-is

**A1. Twin: cubic calm-water law.** `fuel ∝ v³` is wrong near hull speed (~16–17 kn for a 50m displacement hull) where wave-making steepens the curve. Port as a *fitted resistance curve* (parametric in Froude number, sea-trial anchored), not a hard-coded exponent. Constants become fitted parameters with priors (Phase 1).

**A2. Twin: multiplicative added resistance.** The demo multiplies calm power by an Hs-based factor. Physically added resistance is *additive* power (R_aw · v), and should come from a STAWAVE-class semi-empirical model conditioned on the wave spectrum. The demo also ignores **wave period entirely** — added resistance and motions are strongly period-dependent. Fix at the schema level (see B2).

**A3. Twin: flat SFOC.** Demo fuel is proportional to power with a 0.94 single-engine fudge. The entire engine-config optimisation rests on the real U-shaped SFOC curve (per-engine fuel map, RPM × load). Model each engine's map explicitly; single-engine advantage/penalty must *emerge* from the map, not be asserted.

**A4. STW = SOG conflation.** The demo has no currents or leeway. Production must distinguish speed through water (drives fuel) from speed over ground (drives ETA), with a current field input — 0.5–1 kn matters over a 200 nm passage. Add currents to the weather schema now, even if v1 fills it with zeros.

**A5. Penalty-based land/no-go avoidance.** Demo uses large additive penalties (1e7 land, 5e4 reserve). Penalties leak under weight changes. Production: **hard constraints** — infeasible edges are pruned from the graph, never costed. Same for minimum depth (ticket 0.8).

## B. Conventions to fix before writing code

**B1. Units policy.** Demo mixes knots/nm/litres freely. Production core: **SI internally** (m/s, metres, kg fuel), marine units only at API boundaries. This is the classic source of silent bugs — decide it in ticket 0.2, enforce with typed quantities or naming conventions, test conversions.

**B2. Weather field schema.** Define once, in ingest: significant wave height, **peak/mean period**, direction (**normalised to "coming from", −180…180 longitudes**), separate wind-sea/swell partitions where available, 10 m wind u/v components, surface current u/v. Interpolation: bilinear in space, **linear in time**, directions via vector components (the demo's nearest-neighbour direction sampling is a hack). **Land cells: treat as missing data, never as calm** — the demo's live-field builder writes 0 over land, which bleeds artificial calm into coastal interpolation. This is the most dangerous silent-bug in the demo.

**B3. Baseline definition is open.** Demo baseline = west-corridor centreline at 14 kn — arbitrary. The counterfactual baseline definition *is* ticket 0.7 and underpins the commercial savings claim. Until 0.7 lands, mark all "savings vs baseline" numbers as provisional in code and output.

**B4. Objective weights need documented units.** Demo scaling (fuel ≈ €1/L, time up to ≈ €25/min, comfort ×30, wear ×18) was tuned by eye. Production: express all objectives in a common unit (€ equivalent), document each conversion, and make the slider→weights mapping a tested, versioned function.

**B6. Routing endpoints are arbitrary navigable points, not a port enum (July 2026).** `PORTS` and `OPERATING_AREA_BBOX` become per-region-pack *data* (roadmap R1) — new code must not deepen them as global constants. `PlanRequest` takes origin/destination as `LatLon` (port, named anchorage, or dropped pin); endpoint validation = navigable +, for anchorages, plausible anchoring depth (e.g. 3–50 m band via `Geography.depth_m`). Lattice construction already derives from origin/destination, so this is mostly de-hardcoding, not redesign.

**B5. Slider model changed (spec §4.2, July 2026).** Per-passage inputs are now two: Pace (economy↔schedule) and Comfort (crew transit↔owner aboard). **Wear is no longer a passage slider** — it becomes a per-vessel policy (fixed weight + hard constraints: max continuous load, slamming-avoidance threshold) from `VesselSpec`/settings, applied under every plan and reported as an output metric. The demo still shows three sliders; port the two-slider + policy model, not the demo's.

## C. Optimiser design notes (ticket 0.4)

- **State space:** time-expanded — node = (position, time-bucket). Design edge choice to include **speed per leg** from day one, even if v1 enumerates constant speeds like the demo; ETA windows and "slow down to let the front pass" tactics need it, and retrofitting the state space is expensive.
- **Search:** A* over a corridor-bounded lattice around the great circle (bounded compute for edge hardware) with an admissible heuristic (remaining distance at max speed / calm-water minimum cost). Fall back to full DP on the lattice if heuristic admissibility gets awkward with scalarised multi-objective costs.
- **Carry over from demo:** weighted scalarisation with hard ETA-window filter (feasible-set-first, fastest-first ordering when infeasible — keep this exact behaviour, it's tested and correct); Pareto-ish candidate diversity (generalise the "different side / ≥2 kn apart" rule to route-signature clustering).
- **Weather-time coupling:** demo samples weather at leg-midpoint time — acceptable; production should interpolate along the leg. Keep legs short enough (≤ 30 min) that midpoint sampling is second-order.
- **Plan API must expose execution setpoints (added July 2026, Underway page):** each `Candidate` needs, beyond the track: per-leg course and target speed (STW), an **alteration list** (position, time, new course — the demo derives this from track geometry with an 8° threshold; make it a first-class output), and **current-corrected Course to Steer** (CTS ≠ track course once A4's current field exists — CTS is what the helm steers to make good the track). The UI's target-vs-live comparison consumes these plus bus actuals; core provides targets only.

## D. What is genuinely fine to port 1:1

Mission presets and slider semantics; ETA-window UX contract (hard constraint, ✓/✗ per plan, infeasible → warning + fastest-first); candidate card content (fuel, ETA, comfort index, wear index, max seas, one-line reasoning); the reasoning-sentence generator concept; regression scenarios (mistral ⇒ comfort-heavy routes lee-side; calm ⇒ corridors converge, speed varies by preset; impossible window ⇒ flagged).

## E. Suggested ticket 0.2 acceptance criteria

1. `core/` library with typed interfaces: `WeatherField.sample(lat, lon, t)`, `Geography.is_navigable / depth`, `VesselTwin.fuel_rate / motion / wear`, `Optimiser.plan(request) → [Candidate]` — no I/O anywhere in `core/`.
2. Demo regression scenarios reproduced as pytest fixtures and passing (plan-shape assertions, not exact numbers).
3. Units tests: knots↔m/s, nm↔m, direction-convention round-trips.
4. Land-as-missing-data behaviour under test (B2).
5. A `VesselSpec` data file (YAML) holding all twin constants — nothing numeric hard-coded in model classes.
