"""Shared `FitResult` shape, returned by both `fit/calm_resistance.py` and
`fit/added_resistance.py` (ticket 0.6)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FitResult:
    params: dict[str, float]
    # (fitted - prior.mean) / prior.std per parameter -- how many prior
    # standard deviations the data moved each one. Large shifts on real
    # data are worth a naval architect's attention; small shifts under
    # deliberately degenerate (e.g. single-engine-config) data are the
    # signal that regularisation kept the fit graceful rather than
    # overfit-but-meaningless -- see fit/calm_resistance.py.
    prior_shift_sigma: dict[str, float]
    # In-sample (training-set) RMSE only -- fit/validate.py's holdout
    # metrics are the ones that matter for reported fit quality.
    residual_rmse: float
    # Which active_engines values appeared in the segments this fit
    # actually used -- the diagnostic for whether the fit was in the
    # power/SFOC-identifiable regime (needs >= 2 distinct values at
    # overlapping speeds) or not.
    engine_configs_present: frozenset[int]
