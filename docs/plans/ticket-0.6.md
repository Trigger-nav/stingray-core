# Ticket 0.6 — Twin v1 offline: fit/validate tooling for the parametric components

## Context

`core/twin.py` (ticket 0.2) already has the right *structure* — calm-water
resistance (A1), added resistance (A2), and per-engine SFOC (A3) are
separate, interpretable, physics-informed components, not a black box. What
it doesn't have is real *numbers*: every coefficient in
`data/vessel_specs/mys_50m_default.yaml` is a synthetic placeholder,
`VesselSpec.provisional=True` flags that everywhere it's loaded.

ROADMAP.md's 0.6 row (agreed July 2026) is explicit that this ticket
doesn't wait on the naval-arch consult — that's a human/business process
outside this session anyway. Instead: build the *tooling* now, prove it
works via a synthetic self-consistency test (generate telemetry from a
twin with **known** parameters, confirm the pipeline recovers them), and
seed priors from published methods (Holtrop–Mennen, STAWAVE, standard SFOC
shapes) marked provisional. The naval-arch consult becomes a later, short
review of this ticket's model forms + priors — not a blocker to writing
code today.

## Scope (per ROADMAP.md's 0.6 row)

**In scope:**
(a) Fitting pipeline for calm-water curve, added resistance, SFOC maps.
(b) Steady-state segment extraction (reject manoeuvring/transients/
tank-transfer artefacts).
(c) Priors as per-parameter distributions, seeded from published methods,
provisional.
(d) Fit quality as error bands / holdout validation, not point estimates.
(e) The core acceptance test: synthetic telemetry → known-parameter
recovery + junk rejection.

**Explicitly out of scope** (not in ROADMAP's 0.6 row, would be scope
creep):
- Comfort/wear coefficient fitting — only calm-resistance/added-resistance/
  SFOC are named.
- Real telemetry ingestion (NMEA bus parsing, edge logger) — that's
  Phase 1's 1.2/1.3.
- Online/Bayesian/Kalman estimation — that's ticket 2.1, needs live
  telemetry this phase doesn't have.
- Actually engaging the naval architect — a business process, not code.
  This ticket produces exactly what they'd need to review.

## Key design decision: a new `fit/` package, not `core/` or `ingest/`

Fitting needs real nonlinear least-squares (`core/twin.py`'s calm-power
Froude-steepening term and added-resistance period-response term are both
nonlinear in their parameters — a hand-rolled solver would be reinventing
`scipy.optimize.least_squares` worse). `core/` is a hard numpy+PyYAML-only
boundary (`pyproject.toml`'s own comment, CLAUDE.md's package-layout
convention) — adding scipy there isn't an option. `ingest/`'s stated
purpose is network data acquisition, a different concern.

New sibling package `fit/`, same shape as `ingest/`: a library (no I/O
side effects beyond what a thin CLI does) depending on `core/` (one-way,
same direction `ingest/` already depends on `core/`) plus a new `fit`
extras group in `pyproject.toml` (`scipy>=1.11`; numpy/PyYAML already in
core deps).

**Priors live in `fit/priors.py`, not `core/vessel_spec.py`.** Considered
adding prior fields directly onto `CalmResistanceCurve`/
`AddedResistanceCoefficients`/`EngineSpec` — rejected: those dataclasses
are consumed on the optimiser's hot path and are already fully specified
by ticket 0.2; priors are exclusively a *fitting-time* input, never read
by `core/twin.py` or `core/optimiser.py`. Keeping them in `fit/` preserves
the one-way dependency and leaves the stable, tested `core/vessel_spec.py`
untouched. The fitted *output* of this pipeline is an ordinary
`VesselSpec` (`provisional=True`) plus a separate `FitReport` — that's
what satisfies ROADMAP's "priors as per-parameter distributions" in
spirit: the distributions inform the fit, the fit produces the same
`VesselSpec` shape everything else already consumes.

## Amendments (review, before implementation)

1. **Power/SFOC identifiability degeneracy (required fix).** `fit_calm_resistance`
   jointly fits calm-power's 4 parameters and SFOC's 3-per-engine parameters
   against `fuel_kg_per_h`. If the near-calm segments only ever cover *one*
   `active_engines` value, `fuel_kg_per_h(v) = hotel + P(v)*SFOC(P(v)/(n·mcr))/1000`
   collapses to a single curve in `v` with a fixed `n` — a given curve shape
   can come from a slightly steeper `P(v)` compensated by a shifted `SFOC`,
   or vice versa, and the data can't tell them apart. Varying `n` at
   *overlapping* speeds breaks this: the same true `P(v)` then gets sampled
   at a different SFOC load-fraction operating point for each `n`, which
   pins both curves independently. Fix, three parts: (a) every conditions
   grid used for fitting (the acceptance test's synthetic generator, and
   the docstring's guidance for real data collection) must include both
   engine configs at overlapping speeds, not just one; (b) a dedicated
   acceptance-test scenario feeds *single-config-only* data and asserts
   graceful degradation, not silent nonsense — the prior-regularised
   least-squares objective is provably well-posed even in a fully
   degenerate data direction (the per-parameter prior term
   `(param - prior.mean)/prior.std` is its own full-rank quadratic in
   parameter space, so a data direction with zero curvature just leaves
   the fit at the prior mean along that direction, it can't wander), so
   "graceful" means: the confounded parameters' fitted values stay close
   to their priors (small `prior_shift_sigma`, see addition 1) rather than
   drifting to an overfit-but-meaningless solution; (c) `fit_calm_resistance`'s
   docstring explains this degeneracy explicitly, in the terms above, so
   nobody "fixes" the single-engine-config case by just loosening tolerances.

2. **Per-parameter prior-shift diagnostics in `FitResult`.** Every fitted
   parameter gets a `prior_shift_sigma: dict[str, float]` entry,
   `(fitted - prior.mean) / prior.std` — how many prior-standard-deviations
   the data moved each parameter. This is the mechanism amendment 1's
   graceful-degradation test actually asserts against (small shift under
   degenerate data), and generally useful: a large shift on a real fit is
   worth a naval architect's attention (either the prior was off, or
   something's wrong with the segment data), independent of the
   degeneracy question.

3. **Input noise in the synthetic generator.** `generate_synthetic_telemetry`
   previously only perturbed the *output* (`fuel_kg_per_h`). Real telemetry
   also has noisy *inputs* — measured STW has sensor noise, Hs from a wave
   buoy/model has its own uncertainty. Added `stw_noise_std_ms`/
   `hs_noise_std_m`: the *recorded* `stw_ms`/`hs_m` in each `TelemetrySample`
   is perturbed away from the true value used to compute the true
   `fuel_kg_per_h` — an errors-in-variables setup, a materially harder and
   more realistic test than output-noise-only.

4. **CLI module naming fixed.** Design section 9 named the module
   `fit/cli.py` in its header but `fit/fit_twin.py` in the usage line, and
   used `fit_twin` for both the orchestration function (`fit/pipeline.py`)
   and the would-be CLI module — confusing. Settled: orchestration function
   `fit_twin()` stays in `fit/pipeline.py`; CLI module is `fit/cli.py`,
   invoked as `python3 -m fit.cli`. Updated throughout below.

## Design

### 1. `fit/telemetry.py` — data contract

- `TelemetrySample`: `t_h, stw_ms, heading_deg, active_engines,
  fuel_kg_per_h, hs_m, period_peak_s, wave_from_deg` — the flowmeter-tier
  observable (richest signal; NMEA+manual-fuel tiers are a Phase 1 concern
  once real sensor data exists, per the sensor-tier matrix). One sample
  per short interval, matching how real telemetry would arrive.
- No "is this junk" field — real telemetry never arrives pre-labelled;
  that's exactly what segment extraction (below) has to figure out.

### 2. `fit/segments.py` — steady-state extraction (scope item b)

- `SteadyStateSegment`: a contiguous run of samples with near-constant
  `stw_ms`/`heading_deg`/`active_engines`, aggregated to one fit-ready row
  (mean speed, mean heading, mean hs/period, mean fuel rate, duration,
  sample count).
- `extract_steady_state_segments(samples, *, min_duration_s, speed_tol_kn,
  heading_tol_deg, fuel_jump_tol_fraction) -> list[SteadyStateSegment]`:
  sliding-window extraction rejecting (1) windows with speed or heading
  variance above tolerance (manoeuvring/transients), (2) windows shorter
  than `min_duration_s`, (3) windows containing a fuel-rate discontinuity
  unexplained by a corresponding speed/sea-state change (tank-transfer
  artefact — a sudden jump/drop in `fuel_kg_per_h` with no matching
  physical cause).

### 3. `fit/priors.py` — scope item c

- `Prior`: `mean: float, std: float, source: str`. Every prior's `source`
  is a real citation (paper/method name), not a made-up number — where
  the honest answer is "informed by general displacement-hull literature,
  not a rigorous Holtrop–Mennen regression" (the current `HullParticulars`
  has only length/beam/block-coefficient — not enough inputs for a full
  H–M regression), the `source` string says exactly that, flagged for
  naval-arch review to confirm or extend `HullParticulars` if fuller
  fidelity is wanted. No invented numbers presented as more precise than
  they are (design principle #4).
- `CalmResistancePriors`, `AddedResistancePriors`, `EngineSfocPriors`
  dataclasses mirroring the shape of their `core.vessel_spec` counterparts
  (one `Prior` per fitted field). `hull_speed_froude` is **not** a free
  fit parameter (see `fit/calm_resistance.py` below) — no prior needed for
  it, it's derived from hull form directly.
- `DEFAULT_PRIORS`: seeded values for a generic displacement motoryacht
  hull (order-of-magnitude ranges from published planing/displacement
  hull resistance literature and typical medium-speed diesel SFOC curve
  shapes) — every one provisional, every one with a `source`.

### 3b. `fit/result.py` — shared `FitResult` shape

Both `fit_calm_resistance` and `fit_added_resistance` return this, so it's
defined once:

- `FitResult`: `params: dict[str, float]` (the fitted values, by the same
  field names as the corresponding `core.vessel_spec` dataclass),
  `prior_shift_sigma: dict[str, float]` (amendment 2 —
  `(fitted - prior.mean) / prior.std` per parameter), `residual_rmse:
  float` (in-sample, training-set only — `fit/validate.py`'s holdout
  metrics are the ones that matter for reported quality),
  `engine_configs_present: frozenset[int]` (which `active_engines` values
  appeared in the segments this fit actually used — amendment 1's
  diagnostic for whether the fit was in the identifiable regime).

### 4. `fit/calm_resistance.py` — scope item a, part 1

- `fit_calm_resistance(segments, hull, engines, prior, *, hs_threshold_m)
  -> FitResult`: filters to near-calm segments (`hs_m <= hs_threshold_m`),
  then jointly fits `CalmResistanceCurve`'s 4 free parameters
  (`linear_coefficient, cubic_coefficient, steepening_coefficient,
  steepening_exponent`) **and** each engine's 3 SFOC parameters via
  `scipy.optimize.least_squares` against observed `fuel_kg_per_h` —
  matches `TECHNICAL_ARCHITECTURE.md`'s own component table ("Learned
  from: Shaft power/fuel vs STW" for calm resistance).
- `hull_speed_froude` is fixed (from a hull-form-derived prior, not fit):
  correlates too strongly with `steepening_exponent` to be identifiable
  from noisy fuel-rate data alone — fitting both would let the optimiser
  trade one off against the other and converge to a curve that fits the
  training data but means nothing physically. Interpretability over
  degrees of freedom (design principle #4).
- Residual vector = data misfit (predicted vs observed `fuel_kg_per_h`,
  weighted by `1/est. measurement noise`) **plus** a prior-penalty term
  per free parameter (`(param - prior.mean) / prior.std`) — a standard
  MAP/ridge-regularised nonlinear least squares, not unconstrained
  curve-fitting. This is what keeps a thin/noisy synthetic dataset from
  wandering to a nonsensical but data-fitting solution, and (amendment 1)
  what keeps it well-posed even when the data can't identify every
  parameter on its own.
- **Docstring documents the power/SFOC identifiability degeneracy**
  (amendment 1) explicitly: `fuel_kg_per_h` is a function of total power
  `P(v)` and load fraction `P(v)/(n·mcr)` jointly; at a single fixed `n`
  this collapses to one curve in `v` that many `(P-shape, SFOC-shape)`
  combinations can fit equally well. Segments spanning multiple
  `active_engines` values at overlapping speeds sample the *same* `P(v)`
  at *different* SFOC operating points, which is what actually separates
  the two curves. `fit_calm_resistance` doesn't refuse single-config data
  (real early telemetry may well be single-config for a while) — the
  prior-regularised objective keeps it well-posed regardless (previous
  bullet) — but it reports which `active_engines` values were present in
  the fitted segments in `FitResult` so a caller can tell whether the fit
  was in the identifiable regime.

### 5. `fit/added_resistance.py` — scope item a, part 2

- `fit_added_resistance(segments, hull, calm_curve, engines, prior) ->
  FitResult`: uses the **already-fitted** calm curve + SFOC to convert
  every segment's observed `fuel_kg_per_h` back to implied total power,
  subtracts `calm_power_kw` at that segment's speed, and fits the
  residual to `added_power_kw`'s functional form (`scale,
  period_reference_s, head_factor, following_factor`) vs
  (hs, period, encounter angle) — matches the architecture table's
  "Learned from: Observed speed loss vs sea state". Same
  prior-regularised least-squares approach as calm resistance.
- Sequential, not joint, by design: fits calm resistance from low-Hs
  segments first, added resistance from the rest holding calm fixed —
  standard ship-trials-analysis practice (calm curve from calm-water
  runs, added resistance from seaway data), and avoids a much harder
  simultaneous 11-parameter identifiability problem.

### 6. `fit/validate.py` — scope item d

- `holdout_split(segments, holdout_fraction, rng) -> (train, holdout)`.
- `validate_fit(fitted_spec, holdout_segments) -> ValidationReport`:
  predicts `fuel_kg_per_h` for every holdout segment via the fitted
  `VesselTwin`, reports RMSE, mean bias, and a 90%-coverage error band
  (from residual distribution on the holdout set — not the in-sample
  training residual, which would be optimistic). This is what actually
  answers "how good is this fit", not the training-set fit quality.

### 7. `fit/synthetic.py` — the acceptance test's data generator

- `generate_synthetic_telemetry(ground_truth_spec, conditions, *,
  fuel_noise_std_fraction, stw_noise_std_ms, hs_noise_std_m, junk_fraction,
  rng) -> (samples, junk_indices)`: for each condition (speed, heading, sea
  state, engine config), computes the **true** `fuel_kg_per_h` via the real
  `core.twin.VesselTwin` against `ground_truth_spec` at the *true*
  `stw_ms`/`hs_m`, then records `TelemetrySample`s with (amendment 3)
  independently noised `stw_ms`/`hs_m` **and** `fuel_kg_per_h` — an
  errors-in-variables setup (the fit only ever sees noisy inputs, same as
  real telemetry would give it, not the clean values used to generate the
  truth). Injects a `junk_fraction` of segments as either (a) a
  manoeuvring transient (rapidly varying heading/speed within the window)
  or (b) a tank-transfer artefact (an isolated fuel-rate spike/drop
  uncorrelated with power). Returns which indices were injected as junk,
  for the test to check against what `extract_steady_state_segments`
  actually rejected.
- `default_synthetic_conditions(engine_configs=(1, 2), speeds_kn=...) ->
  list[Condition]`: (amendment 1) the standard conditions grid used by the
  acceptance test's main scenario — every speed crossed with *every*
  engine config, so overlapping-speed multi-config coverage is the
  default, not something each test has to remember to set up. The
  degenerate-data scenario (amendment 1b) explicitly filters this down to
  one config instead of using a separately-constructed grid, so it's
  obviously "the same grid minus the thing that makes it identifiable",
  not a different setup that could hide other differences.

### 8. `fit/pipeline.py` — orchestration

- `fit_twin(samples, hull, engine_names, priors=DEFAULT_PRIORS) ->
  FittedTwin` (`FittedTwin` = `spec: VesselSpec` (`provisional=True`) +
  `fit_report: FitReport` bundling both components' `FitResult`s and the
  holdout `ValidationReport`). One call: segment → split → fit calm →
  fit added resistance → validate on holdout.

### 9. `fit/cli.py` — thin CLI

- `python3 -m fit.cli --telemetry PATH --hull-length-wl-m ... --out
  PATH.yaml` (real-data entry point, Phase 1's eventual consumer) plus a
  `--synthetic-demo` flag that runs the full pipeline against
  `fit/synthetic.py`'s generator and prints the validation report — a
  runnable demonstration of the whole thing today, no real data needed.
  Orchestration logic itself lives in `fit/pipeline.py`'s `fit_twin()`;
  this module is just argument parsing + printing (amendment 4).

## Tests

- `tests/test_fit_segments.py`: synthetic sample streams with known
  manoeuvring/tank-transfer junk at known positions → extraction rejects
  exactly those, keeps the clean steady-state runs.
- `tests/test_fit_priors.py`: every `DEFAULT_PRIORS` entry has a non-empty
  `source`; `Prior` fields are sane (std > 0).
- `tests/test_fit_calm_resistance.py`, `test_fit_added_resistance.py`:
  fit against small hand-built synthetic segment sets with a known ground
  truth, assert recovered predictions (not raw parameters — see below)
  match ground truth within tolerance.
- `tests/test_fit_validate.py`: holdout split is disjoint and
  reproducible given a seed; `validate_fit` on a perfect (noiseless) fit
  reports ~zero error; on a deliberately-wrong spec reports a large one
  (sanity-checks the metric isn't vacuous).
- **`tests/test_fit_acceptance.py`** — the core test ROADMAP names
  explicitly, two scenarios:
  - *Main scenario*: `default_synthetic_conditions()` (both engine
    configs, overlapping speeds) with realistic input+output noise +
    injected junk, run the full `fit_twin` pipeline, assert (1) the
    extracted segments exclude the known junk indices, (2) the fitted
    twin's **predicted** `fuel_kg_per_h` matches the ground-truth twin's
    predictions within a stated tolerance across a held-out grid of
    speeds/sea-states (testing predictive agreement, not raw parameter
    closeness — nonlinear curve fits can recover a functionally-equivalent
    curve via a different but correlated parameter combination; asserting
    on predictions is the meaningful, robust check, and it's what
    `validate_fit`'s holdout report is already built to answer), (3) the
    holdout error band is consistent with the injected noise level (not
    badly overfit, not badly underfit).
  - *Degenerate scenario* (amendment 1b): the same generator, conditions
    grid filtered to a single `active_engines` value. Asserts
    `FitResult.engine_configs_present` reports exactly one config (the
    diagnostic is working), and that the SFOC parameters'
    `prior_shift_sigma` stays small (graceful degradation — the fit
    didn't wander into an overfit-but-meaningless solution just because
    the data couldn't identify it) rather than asserting a specific
    prediction-accuracy tolerance (which the degeneracy makes
    meaningless to promise).

## Docs

- `CLAUDE.md`: gotcha entry noting the `fit/` package exists, depends on
  `core/` one-way (same as `ingest/`), and that its priors are
  provisional pending naval-arch review — link the acceptance test as the
  thing that proves the tooling works today, independent of that review.
- `ROADMAP.md`: mark 0.6 done once green, noting what's still pending
  (naval-arch review of the model forms + prior values — a follow-up,
  not a blocker) same pattern as 0.5's "verified end-to-end" note.
- `pyproject.toml`: new `fit` extras group, scipy pinned, comment
  explaining why it's separate from `ingest`.

## Verification

- `pytest -m ""` (full suite) green, `ruff check .` clean — same bar as
  every prior ticket.
- Run `python3 -m fit.cli --synthetic-demo` manually and read the printed
  validation report — confirms the CLI path works end-to-end, not just
  the library functions in isolation.

## Implementation notes (found while building, not anticipated in the plan)

Four real issues surfaced empirically while validating the acceptance
test against a deliberately off-prior ground truth (see that test's
docstring for why prior-equals-truth would have made "recovery" vacuous)
— all fixed before landing, all worth recording since they're exactly
the kind of thing that would silently reappear if the fitting math is
touched later without re-running the acceptance test against an
off-prior truth:

1. **Proportional, not fixed-absolute, data-residual weighting.** The
   first working version weighted every segment's `fuel_kg_per_h`
   residual by one fixed absolute std. Fuel rate spans roughly an order
   of magnitude across the speed range, so a fixed absolute weight
   silently overweights high-speed (high-fuel-rate) segments and
   underweights low-speed ones relative to their actual noise level —
   this alone was the single biggest driver of poor recovery in early
   testing (max relative prediction error dropped from ~53% to under 1%
   on one trial after switching to `fuel_noise_std_fraction * seg.mean_fuel_kg_per_h`,
   floored at a small absolute minimum). `fit/calm_resistance.py`/
   `fit/added_resistance.py`'s `DEFAULT_FUEL_NOISE_STD_FRACTION`.
2. **The synthetic speed grid must reach past the steepening onset.**
   `fit/synthetic.py`'s conditions grid originally stopped at 16kn;
   the shipped hull's steepening onset (`hull_speed_froude=0.4`) is
   right around there, so `steepening_coefficient`/`steepening_exponent`
   were barely constrained by the data. Extended to 18kn
   (`DEFAULT_SPEEDS_KN`).
3. **`scipy.optimize.least_squares` returns `numpy.float64`,** which
   flowed unconverted into `core.vessel_spec` dataclasses and broke
   `yaml.safe_dump` in `fit/cli.py`'s `--out` path. Both `_candidate_spec`
   functions now cast to plain `float` explicitly.
4. **Predictive agreement, not raw parameter recovery, is what the
   acceptance test asserts** — already the plan's stated intent, but
   validating against an off-prior ground truth showed *why* concretely:
   individual parameters (especially the 7 jointly-fit calm/SFOC ones)
   can land 10-40% off their true values while the fitted function still
   predicts within ~1-4% — multiple correlated parameter combinations
   fit the same observable surface almost equally well. Confirms the
   plan's original reasoning was right, not just a hedge.

Also: `SteadyStateSegment` gained `t_start_h`/`t_end_h` fields (not in
the original design) — needed to precisely test that no segment overlaps
a manoeuvring-junk block, and generally useful segment metadata beyond
just `duration_h`. `extract_steady_state_segments` gained a `max_gap_s`
tolerance (data dropouts shouldn't silently bridge into one segment) —
also what gives `fit/synthetic.py`'s condition-by-condition blocks an
unambiguous boundary even when two adjacent blocks happen to share the
same speed/engine config and differ only in sea state (which the
compatibility check deliberately doesn't examine on its own).
