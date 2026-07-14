"""Orchestration (ticket 0.6): telemetry -> segments -> train/holdout
split -> fit calm resistance + SFOC -> fit added resistance (calm/SFOC
held fixed) -> validate on holdout. One call, `fit_twin`, ties the whole
pipeline together; `fit/cli.py` is a thin wrapper around it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from core.vessel_spec import VesselSpec
from fit.added_resistance import fit_added_resistance
from fit.calm_resistance import (
    DEFAULT_FUEL_NOISE_FLOOR_KG_PER_H,
    DEFAULT_FUEL_NOISE_STD_FRACTION,
    DEFAULT_HS_THRESHOLD_M,
    fit_calm_resistance,
)
from fit.priors import (
    DEFAULT_ADDED_RESISTANCE_PRIORS,
    DEFAULT_CALM_RESISTANCE_PRIORS,
    DEFAULT_SFOC_PRIORS,
    AddedResistancePriors,
    CalmResistancePriors,
    SfocPriors,
)
from fit.result import FitResult
from fit.segments import (
    DEFAULT_FUEL_JUMP_TOL_FRACTION,
    DEFAULT_HEADING_TOL_DEG,
    DEFAULT_MAX_GAP_S,
    DEFAULT_MIN_DURATION_S,
    DEFAULT_SPEED_TOL_KN,
    SteadyStateSegment,
    extract_steady_state_segments,
)
from fit.telemetry import TelemetrySample
from fit.validate import (
    DEFAULT_HOLDOUT_FRACTION,
    ValidationReport,
    holdout_split,
    passage_holdout_split,
    validate_fit,
)


@dataclass(frozen=True)
class FitReport:
    calm_resistance_result: FitResult
    added_resistance_result: FitResult
    validation: ValidationReport


@dataclass(frozen=True)
class FittedTwin:
    spec: VesselSpec
    fit_report: FitReport


def fit_twin(
    samples: list[TelemetrySample],
    base_spec: VesselSpec,
    *,
    calm_prior: CalmResistancePriors = DEFAULT_CALM_RESISTANCE_PRIORS,
    sfoc_prior: SfocPriors = DEFAULT_SFOC_PRIORS,
    added_prior: AddedResistancePriors = DEFAULT_ADDED_RESISTANCE_PRIORS,
    hs_threshold_m: float = DEFAULT_HS_THRESHOLD_M,
    fuel_noise_std_fraction: float = DEFAULT_FUEL_NOISE_STD_FRACTION,
    fuel_noise_floor_kg_per_h: float = DEFAULT_FUEL_NOISE_FLOOR_KG_PER_H,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    min_duration_s: float = DEFAULT_MIN_DURATION_S,
    speed_tol_kn: float = DEFAULT_SPEED_TOL_KN,
    heading_tol_deg: float = DEFAULT_HEADING_TOL_DEG,
    fuel_jump_tol_fraction: float = DEFAULT_FUEL_JUMP_TOL_FRACTION,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
    holdout_group_by: Literal["passage_id", "vessel_id"] | None = None,
    rng: np.random.Generator | None = None,
) -> FittedTwin:
    """`base_spec` supplies everything this ticket doesn't fit (hull
    particulars, engine names/MCR, hotel load, comfort, wear policy) --
    only `calm_resistance`, engine SFOC fields, and `added_resistance`
    are replaced in the returned spec.

    Derives `SteadyStateSegment`s itself via `extract_steady_state_
    segments`, which has no provenance concept -- every segment this path
    produces has `vessel_id=passage_id=None`. **`holdout_group_by` is
    therefore only useful here if `samples` somehow already correspond to
    exactly one passage/vessel** (it isn't, generally -- real per-source
    identity is a `SteadyStateSegment`-level concept, stamped by `fit/
    import_pipeline.py`'s `rows_to_segments`, never carried by
    `TelemetrySample`). **For real historical-import data with genuine
    multi-passage/multi-vessel provenance (ticket B7 Parts 3/4), call
    `fit_twin_from_segments` directly with `rows_to_segments`'s output
    instead of this function** -- found during plan review: without a
    segments-first entry point, `holdout_group_by`/`passage_holdout_split`
    would be unreachable by any real B7 import, since this function's own
    internal extraction always discards identity."""
    segments = extract_steady_state_segments(
        samples,
        min_duration_s=min_duration_s,
        speed_tol_kn=speed_tol_kn,
        heading_tol_deg=heading_tol_deg,
        fuel_jump_tol_fraction=fuel_jump_tol_fraction,
        max_gap_s=max_gap_s,
    )
    if not segments:
        raise ValueError("no steady-state segments extracted from telemetry")
    return fit_twin_from_segments(
        segments,
        base_spec,
        calm_prior=calm_prior,
        sfoc_prior=sfoc_prior,
        added_prior=added_prior,
        hs_threshold_m=hs_threshold_m,
        fuel_noise_std_fraction=fuel_noise_std_fraction,
        fuel_noise_floor_kg_per_h=fuel_noise_floor_kg_per_h,
        holdout_fraction=holdout_fraction,
        holdout_group_by=holdout_group_by,
        rng=rng,
    )


def fit_twin_from_segments(
    segments: list[SteadyStateSegment],
    base_spec: VesselSpec,
    *,
    calm_prior: CalmResistancePriors = DEFAULT_CALM_RESISTANCE_PRIORS,
    sfoc_prior: SfocPriors = DEFAULT_SFOC_PRIORS,
    added_prior: AddedResistancePriors = DEFAULT_ADDED_RESISTANCE_PRIORS,
    hs_threshold_m: float = DEFAULT_HS_THRESHOLD_M,
    fuel_noise_std_fraction: float = DEFAULT_FUEL_NOISE_STD_FRACTION,
    fuel_noise_floor_kg_per_h: float = DEFAULT_FUEL_NOISE_FLOOR_KG_PER_H,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    holdout_group_by: Literal["passage_id", "vessel_id"] | None = None,
    rng: np.random.Generator | None = None,
) -> FittedTwin:
    """The segments-first entry point (ticket B7, added during plan
    review): `fit_twin` above is `extract_steady_state_segments` +
    this function, for the ticket-0.6 raw-telemetry-stream case. Real
    historical-import data (`fit/import_pipeline.py`'s `rows_to_segments`,
    which stamps `vessel_id`/`passage_id`/`fuel_noise_multiplier` post-
    extraction) already *is* a `list[SteadyStateSegment]` with real
    provenance -- it calls this function directly, never `fit_twin`,
    since there's no raw `TelemetrySample` stream to re-extract from
    (`rows_to_segments` already ran `extract_steady_state_segments`
    itself, per `(vessel_id, passage_id, source)` batch).

    `holdout_group_by`: `None` (default) preserves the flat `fit.
    validate.holdout_split`, unchanged -- correct when `segments` really
    is one undifferentiated stream (e.g. `fit_twin`'s call path above, or
    a single-passage import). Set to `"passage_id"`/`"vessel_id"` to use
    `fit.validate.passage_holdout_split` instead, required for real
    multi-passage/multi-vessel data (see that function's docstring for
    why the flat split flatters autocorrelated data)."""
    if not segments:
        raise ValueError("no segments to fit")

    n_groups_train = n_groups_holdout = None
    if holdout_group_by is None:
        train, holdout = holdout_split(segments, holdout_fraction, rng)
    else:
        train, holdout = passage_holdout_split(
            segments, group_by=holdout_group_by, holdout_fraction=holdout_fraction, rng=rng
        )
        n_groups_train = len({getattr(s, holdout_group_by) for s in train})
        n_groups_holdout = len({getattr(s, holdout_group_by) for s in holdout})
    if not train:
        raise ValueError(
            f"only {len(segments)} segment(s) -- not enough to both fit and hold out; "
            "need more telemetry"
        )

    calm_fit = fit_calm_resistance(
        train,
        base_spec,
        calm_prior=calm_prior,
        sfoc_prior=sfoc_prior,
        hs_threshold_m=hs_threshold_m,
        fuel_noise_std_fraction=fuel_noise_std_fraction,
        fuel_noise_floor_kg_per_h=fuel_noise_floor_kg_per_h,
    )
    spec_after_calm = replace(
        base_spec,
        calm_resistance=calm_fit.calm_resistance,
        engines=tuple(
            replace(
                e,
                sfoc_base_g_per_kwh=calm_fit.sfoc_base_g_per_kwh,
                sfoc_min_load_fraction=calm_fit.sfoc_min_load_fraction,
                sfoc_curvature=calm_fit.sfoc_curvature,
            )
            for e in base_spec.engines
        ),
    )

    added_fit = fit_added_resistance(
        train,
        spec_after_calm,
        prior=added_prior,
        fuel_noise_std_fraction=fuel_noise_std_fraction,
        fuel_noise_floor_kg_per_h=fuel_noise_floor_kg_per_h,
    )
    fitted_spec = replace(
        spec_after_calm, added_resistance=added_fit.added_resistance, provisional=True
    )

    validation = validate_fit(
        fitted_spec,
        holdout if holdout else train,
        n_groups_train=n_groups_train,
        n_groups_holdout=n_groups_holdout,
    )

    fit_report = FitReport(
        calm_resistance_result=calm_fit.result,
        added_resistance_result=added_fit.result,
        validation=validation,
    )
    return FittedTwin(spec=fitted_spec, fit_report=fit_report)
