# Stingray Savings-Verification Methodology

**v0.1 draft · July 2026 · Ticket 0.7 deliverable.**
Status: for review by the naval-architecture consultant (alongside ticket 0.6's model forms) and by early management-company counterparties before first pilot. This document, once agreed, is versioned and locked per vessel at onboarding — see §8.

---

## 1. Purpose and scope of claims

Stingray's commercial claim is verified fuel (and hence CO₂) savings versus what would have happened without it. This document defines exactly how that number is produced, so that a management company's technical superintendent, an owner's representative, or an auditor can reproduce it from logged data.

Claims covered: per-passage fuel savings (kg and litres); per-vessel periodic savings (monthly/annual aggregate); CO₂ equivalents for SEEMP continuous-improvement evidence. Explicitly **not** claimed in fuel terms: schedule value, comfort, or wear benefits — these are reported as their own metrics, never monetised into the savings figure.

## 2. The counterfactual problem

Savings = (fuel that would have been burned) − (fuel actually burned). The first term is unobservable — the industry's perennial source of performance-claim disputes. Four candidate baseline definitions, assessed honestly:

| Baseline | Description | Strength | Weakness |
|---|---|---|---|
| B1. Model counterfactual | The "do-nothing" plan (direct feasible route, vessel's customary service speed, standard config) evaluated by the vessel's fitted twin under the **weather actually encountered** | Computable every passage; same-vessel, same-weather comparison | Depends on our own model |
| B2. Historical normalisation | Vessel's pre-Stingray fuel-per-mile record, weather-normalised | Model-independent; uses the vessel's own history | Needs ≥30 sea-days of pre-install data; normalisation itself needs a model |
| B3. Declared intent | Captain's stated pre-advice plan, captured at planning time | Reflects real behaviour | Gameable, unverifiable, burdens the bridge |
| B4. Crossover trials | Alternate passages with/without advice | Gold standard | Costs real fuel and schedule; impractical at fleet scale |

**Method: B1 is the primary, continuous measure — with the twin's error measured and corrected on every passage (§4). B2 validates B1 per-vessel during the first season. B4 is used sparingly during the Phase 1 pilot to validate the whole construction. B3 is not used.**

This mirrors accepted M&V practice (IPMVP option-C/D structure; ISO 15016-style weather normalisation), which reviewers can map to familiar ground.

## 3. Measurement definitions

- **Actual fuel** (`F_actual`): passage total, berth-to-berth, propulsion + hotel during passage. Measured per the vessel's sensor tier: **Tier A** — dedicated flowmeters (metered); **Tier B** — engine-reported fuel rate (PGN 127489/J1939) reconciled against tank levels per passage; **Tier C** — manual tank soundings, passage totals only. Every published savings figure carries its tier label.
- **Passage boundary:** departure berth/anchorage release to arrival berth/anchorage set. Anchorage hotel consumption outside passages is excluded from passage claims (reported separately).
- **Encountered weather:** the corrected weather field as logged along the actual track, plus the same field sampled along the counterfactual track at the counterfactual's timings. Provenance (model cycle, corrections applied) logged per §8.
- **Attribution rule:** a passage counts as *advised* only if a Stingray plan was generated before departure and the vessel substantially followed it (track within corridor tolerance, speed within 1 kn of plan profile for ≥80% of passage time). Followed and not-followed passages are both logged; savings are claimed **only on followed passages**, but the denominator disclosure (§8) always shows both counts, so selective adherence can't manufacture a rate.

## 4. The savings estimator

For each advised, followed passage:

```
S_raw = F_twin(baseline plan, encountered weather) − F_actual
```

Two corrections make this defensible:

**4.1 Same-passage bias correction.** The twin's error on this passage is directly observable:

```
e = F_actual − F_twin(actual plan as sailed, encountered weather)
```

`e` measures how wrong the twin was *today, for this vessel, in this weather*. The corrected estimate is:

```
S = F_twin(baseline) − F_twin(actual) − 0   …applied as: S = [F_twin(baseline) + e] − F_actual
  = F_twin(baseline) − F_twin(actual)
```

i.e. the claim reduces to a **difference of two twin evaluations under identical weather**, with the twin's same-day bias cancelling in the difference. This is the core defensibility argument: we never claim the twin's absolute accuracy, only its *relative* accuracy between two plans for the same hull in the same sea state — where correlated model errors (fouling state, displacement, hotel load error) subtract out.

**4.2 Uncertainty propagation.** The twin carries per-prediction confidence intervals (ticket 0.6's validation machinery). The savings CI combines both evaluations' uncertainties **less their estimated correlation** (high, same conditions), plus measurement uncertainty by tier. Reporting rule:

- **Operational reporting** (fleet dashboard): central estimate ± 90% CI.
- **Contractual/marketing claims:** the **lower bound (P10)** of the aggregated distribution. We claim what we're 90% sure of, not the midpoint.
- Passages whose CI includes zero are included in aggregates (their uncertainty widens the total CI) — they are never dropped.

## 5. Baseline plan definition (locked per vessel)

The B1 counterfactual is fixed at onboarding and versioned: direct feasible route between the passage endpoints (shortest constraint-legal path — computed by the same routing engine with optimisation objectives disabled), at the vessel's documented customary service speed and standard engine configuration, with the same departure time. Provisional note: the current codebase flags this construction `baseline_provisional=True`; this document, once countersigned, is what removes that flag. Changes to a vessel's baseline definition require re-agreement and apply prospectively only.

## 6. Validation programme (Phase 1 pilot)

1. **Historical cross-check (B2):** for each pilot vessel with ≥30 pre-install sea-days, weather-normalised historical fuel-per-mile vs twin-predicted for the same passages; agreement within the twin's stated CI validates the twin as a baseline instrument.
2. **Crossover passages (B4):** a small number (target ≥4 per pilot vessel) of matched passages sailed conventionally, interleaved with advised passages; measured difference vs estimator's claim.
3. **Adherence sensitivity:** estimator recomputed under stricter/looser adherence thresholds to show claims aren't threshold-artefacts.
Published pilot result: estimator vs crossover agreement, with CIs. This is the evidence pack behind the marketing number.

## 7. Aggregation and degradation

Periodic savings = Σ passage savings (followed passages), with CI aggregated assuming passage-level independence of residual errors (conservative given same-vessel correlation is removed per §4.1). **Hull/engine degradation:** the twin's slow drift states (fouling, engine wear) are part of the model, so both plan evaluations use the vessel's *current* condition — savings from Stingray-prompted maintenance (e.g. fouling detection → cleaning) are reported as a separate line item, attributed to the intervention, never folded into passage-optimisation savings.

## 8. Integrity rules (anti-gaming, auditability)

1. **Pre-registration:** baseline definition, adherence thresholds, and tier are locked per vessel before the first claimed passage; method version recorded on every claim.
2. **Full denominator:** every advised passage appears in the log — followed, not-followed, and infeasible-window passages. No cherry-picking by construction.
3. **Immutable logging:** raw telemetry, weather provenance, plan artefacts, and both twin evaluations retained per passage; claims are recomputable by a third party from the log.
4. **Method changes** (new twin version, revised estimator) apply prospectively; retrospective restatement only with counterparty agreement and dual reporting during transition.
5. **Tier honesty:** Tier C (manual fuel) passages are reported with their wider CIs and never silently mixed into Tier A/B aggregates.

## 9. Compliance mapping

CO₂ = fuel(kg) × 3.15. Passage debriefs and periodic aggregates are formatted to slot into SEEMP Part II continuous-improvement records (measure → monitor → evaluate loop evidence). For 5,000GT+ vessels, the same measured fuel totals reconcile with IMO DCS submissions — one source of truth. Index exports (SEA Index, YETI) use measured totals, not modelled ones.

## 10. Known limitations (stated, not hidden)

- The estimator is model-relative (§4): its validity rests on the twin's *relative* accuracy between nearby plans, validated per §6 — not on absolute prediction accuracy.
- Single-passage claims are noisy; the product's honest unit of account is the periodic aggregate with CI.
- Weather luck dominates individual passages; only the baseline's identical-weather construction makes per-passage numbers meaningful at all.
- Savings from ETA-window relaxation (customer chooses to arrive later) are behavioural, not algorithmic; they are reported as a separate "schedule flexibility" line so the algorithmic claim stays clean.

---

*v0.1 open questions for consultant/counterparty review: (a) is customary service speed the right baseline speed, or should it be the vessel's documented historical average by passage type? (b) adherence thresholds (corridor tolerance, 1 kn / 80%) — validate against pilot behaviour; (c) whether Tier B tank-reconciliation error bounds need per-vessel calibration passages.*
