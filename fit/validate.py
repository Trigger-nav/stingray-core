"""Holdout validation (ticket 0.6, ROADMAP scope item d) -- fit quality
reported as error bands from held-out data, not in-sample point
estimates. `fit/calm_resistance.py`/`fit/added_resistance.py`'s
`FitResult.residual_rmse` is training-set only and answers "did the
optimiser converge"; this module answers "how good is this fit,
honestly" -- the number that actually matters.

**Two split functions, use the right one (ticket B7 Part 4):**
`holdout_split` is a flat `rng.permutation` over individual segments --
correct for a single synthetic/single-passage stream (ticket 0.6's
acceptance test), where there's nothing to group by. `passage_holdout_split`
groups segments by passage/vessel identity *before* permuting, so no
group's segments ever split across train/holdout -- required for any real
multi-passage or multi-vessel historical data, since a flat segment-level
split lets within-passage autocorrelation (e.g. one day's flowmeter
miscalibration, or one passage's particular sea state) leak across the
split and flatters the reported error band. Use `holdout_split` only when
there's genuinely one passage/vessel in play.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from core.twin import VesselTwin
from core.vessel_spec import VesselSpec
from core.weather import WeatherSample
from fit.segments import SteadyStateSegment

DEFAULT_HOLDOUT_FRACTION = 0.2
# Normal-approximation 90%-coverage half-width (1.645 sigma) -- chosen
# over empirical percentiles for stability with small holdout sets, which
# is the common case this early (few sea-days of telemetry).
NINETY_PCT_Z = 1.645


@dataclass(frozen=True)
class ValidationReport:
    n_holdout: int
    rmse_kg_per_h: float
    mean_bias_kg_per_h: float
    error_band_kg_per_h: float  # +/- half-width, 90% coverage (normal approx)
    # ticket B7 Part 4, additive -- populated only via passage_holdout_split
    # (below); None from the flat holdout_split path. An error band built
    # from 2 holdout passages must not look identical to one built from 20
    # -- surfacing group counts alongside the band makes that visible
    # rather than implicit (CLAUDE.md's "no invented numbers" principle).
    n_groups_train: int | None = None
    n_groups_holdout: int | None = None


def holdout_split(
    segments: list[SteadyStateSegment],
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    rng: np.random.Generator | None = None,
) -> tuple[list[SteadyStateSegment], list[SteadyStateSegment]]:
    if rng is None:
        rng = np.random.default_rng()
    n_holdout = max(1, round(len(segments) * holdout_fraction)) if segments else 0
    indices = rng.permutation(len(segments))
    holdout_idx = set(indices[:n_holdout].tolist())
    train = [s for i, s in enumerate(segments) if i not in holdout_idx]
    holdout = [s for i, s in enumerate(segments) if i in holdout_idx]
    return train, holdout


def passage_holdout_split(
    segments: list[SteadyStateSegment],
    *,
    group_by: Literal["passage_id", "vessel_id"] = "passage_id",
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    rng: np.random.Generator | None = None,
) -> tuple[list[SteadyStateSegment], list[SteadyStateSegment]]:
    """Groups `segments` by `group_by`, permutes over **whole groups**
    (never individual segments), assigns entire groups atomically to
    train/holdout -- the mechanism that actually prevents within-passage/
    within-vessel autocorrelation from leaking across the split (see
    module docstring).

    Two hard-error guards, both deliberate (not silent fallbacks):
    - `ValueError` if `group_by` is `None` on some segments and populated
      on others -- mixed provenance is a real data problem, not something
      to silently degrade through.
    - `ValueError` if fewer than 2 distinct groups exist, or if
      `holdout_fraction` truncates the holdout side down to **zero**
      groups. Deliberately uses `int(n_groups * holdout_fraction)`
      truncation, *not* `holdout_split`'s `max(1, round(...))` forcing
      pattern -- silently forcing a holdout of 1 group would misrepresent
      what fraction was actually achieved. Concretely: 3 groups at
      `holdout_fraction=0.25` gives `int(3 * 0.25) = 0` and raises, rather
      than quietly holding out 1 anyway.
    """
    ids = [getattr(s, group_by) for s in segments]
    n_none = sum(1 for i in ids if i is None)
    if 0 < n_none < len(ids):
        raise ValueError(
            f"passage_holdout_split: mixed None/populated {group_by!r} values "
            f"({n_none}/{len(ids)} segments have no {group_by}) -- mixed "
            "provenance is a real data problem, not something to silently "
            "degrade through"
        )
    unique_ids = list(dict.fromkeys(ids))
    if len(unique_ids) < 2:
        raise ValueError(
            f"passage_holdout_split needs at least 2 distinct {group_by!r} "
            f"groups, got {len(unique_ids)}"
        )
    n_holdout_groups = int(len(unique_ids) * holdout_fraction)
    if n_holdout_groups == 0:
        raise ValueError(
            f"holdout_fraction={holdout_fraction} rounds down to 0 holdout "
            f"groups out of {len(unique_ids)} {group_by!r} groups -- increase "
            f"holdout_fraction or provide more {group_by!r} groups"
        )
    if rng is None:
        rng = np.random.default_rng()
    perm = rng.permutation(len(unique_ids))
    holdout_ids = {unique_ids[i] for i in perm[:n_holdout_groups]}
    train = [s for s in segments if getattr(s, group_by) not in holdout_ids]
    holdout = [s for s in segments if getattr(s, group_by) in holdout_ids]
    return train, holdout


def _predict_fuel_kg_per_h(spec: VesselSpec, seg: SteadyStateSegment) -> float:
    weather = WeatherSample(
        hs_m=seg.mean_hs_m,
        period_peak_s=seg.mean_period_peak_s,
        period_mean_s=seg.mean_period_peak_s,
        wave_from_deg=seg.mean_wave_from_deg,
        wind_u_ms=0.0,
        wind_v_ms=0.0,
        current_u_ms=0.0,
        current_v_ms=0.0,
    )
    result = VesselTwin(spec).fuel_rate(
        v_ms=seg.mean_stw_ms,
        weather=weather,
        heading_deg=seg.mean_heading_deg,
        active_engines=seg.active_engines,
    )
    return result.fuel_kg_per_h


def validate_fit(
    fitted_spec: VesselSpec,
    holdout_segments: list[SteadyStateSegment],
    *,
    n_groups_train: int | None = None,
    n_groups_holdout: int | None = None,
) -> ValidationReport:
    """`n_groups_train`/`n_groups_holdout` are caller-supplied (ticket B7
    Part 4) -- this function doesn't infer them itself, since it has no
    opinion on `group_by`; a caller using `passage_holdout_split` passes
    the group counts it already computed, a caller using flat
    `holdout_split` leaves both `None` (unchanged default)."""
    if not holdout_segments:
        raise ValueError("no holdout segments to validate against")
    residuals = np.array(
        [
            _predict_fuel_kg_per_h(fitted_spec, seg) - seg.mean_fuel_kg_per_h
            for seg in holdout_segments
        ]
    )
    rmse = float(np.sqrt(np.mean(residuals**2)))
    bias = float(np.mean(residuals))
    std = float(np.std(residuals))
    return ValidationReport(
        n_holdout=len(holdout_segments),
        rmse_kg_per_h=rmse,
        mean_bias_kg_per_h=bias,
        error_band_kg_per_h=NINETY_PCT_Z * std,
        n_groups_train=n_groups_train,
        n_groups_holdout=n_groups_holdout,
    )
