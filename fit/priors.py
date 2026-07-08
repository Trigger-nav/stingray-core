"""Per-parameter priors (ticket 0.6, ROADMAP scope item c) -- fitting-time
inputs only, never read by `core/twin.py` or `core/optimiser.py` (see
docs/plans/ticket-0.6.md's "key design decision"). Every prior is
provisional and carries a real `source` describing the published method
or typical engineering data it's informed by -- never a bare number
presented as more precise than it is (design principle #4, "no invented
numbers").

Where the honest answer is "this is order-of-magnitude, not a rigorous
regression" (calm resistance below), the source says exactly that. These
are all meant to be reviewed and superseded by the naval-arch consult
(ROADMAP's 0.6 row) -- this module exists so the fitting pipeline has
*something* physically reasonable to regularise against today, not to
pre-empt that review.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Prior:
    mean: float
    std: float
    source: str


@dataclass(frozen=True)
class CalmResistancePriors:
    """Priors for `core.vessel_spec.CalmResistanceCurve`'s free-fit fields.
    `hull_speed_froude` is deliberately not fit (see
    `fit/calm_resistance.py`'s identifiability discussion) -- its prior
    mean is used directly as the fixed value, not as a regularisation
    target."""

    linear_coefficient: Prior
    cubic_coefficient: Prior
    steepening_coefficient: Prior
    steepening_exponent: Prior
    hull_speed_froude: Prior


@dataclass(frozen=True)
class AddedResistancePriors:
    scale: Prior
    period_reference_s: Prior
    head_factor: Prior
    following_factor: Prior


@dataclass(frozen=True)
class SfocPriors:
    """One shared prior set applied to every engine (see
    `fit/calm_resistance.py`'s docstring for why sister engines are fit
    jointly, not per-engine, by default)."""

    sfoc_base_g_per_kwh: Prior
    sfoc_min_load_fraction: Prior
    sfoc_curvature: Prior


_CALM_SOURCE = (
    "Order-of-magnitude, not a rigorous Holtrop-Mennen regression -- "
    "core.vessel_spec.HullParticulars (length/beam/block-coefficient "
    "only) lacks the draft/midship-coefficient/LCB/wetted-surface inputs "
    "true Holtrop & Mennen (1982) needs. Shape (steepening onset near "
    "Fn~0.4, exponent ~4) is informed by published round-bilge "
    "displacement-hull resistance curves and typical 45-50m motoryacht "
    "sea-trial power curves. Flagged for naval-arch review: either "
    "accept this simplified parametric form, or extend HullParticulars "
    "for a fuller Holtrop-Mennen fit."
)

_ADDED_SOURCE = (
    "STAWAVE-1-class semi-empirical form (ITTC Recommended Procedures "
    "7.5-04-01-01.2, added resistance in waves). scale/period_reference "
    "and the head/following asymmetry are informed by typical head-sea "
    "speed-loss curves published for comparable displacement hulls, not "
    "vessel-specific data. Provisional pending naval-arch review."
)

_SFOC_SOURCE = (
    "Typical medium-speed marine diesel BSFOC curve shape (published "
    "performance curves for ~1000-1500kW propulsion diesels, e.g. "
    "MTU/Caterpillar/MAN marine engine data sheets): U-shaped, minimum "
    "near 70-85% MCR. Provisional pending the actual installed engine's "
    "certified fuel map from the manufacturer."
)

DEFAULT_CALM_RESISTANCE_PRIORS = CalmResistancePriors(
    linear_coefficient=Prior(mean=5.0, std=1.5, source=_CALM_SOURCE),
    cubic_coefficient=Prior(mean=1.0, std=0.4, source=_CALM_SOURCE),
    steepening_coefficient=Prior(mean=1.5, std=0.6, source=_CALM_SOURCE),
    steepening_exponent=Prior(mean=4.0, std=1.0, source=_CALM_SOURCE),
    hull_speed_froude=Prior(mean=0.40, std=0.05, source=_CALM_SOURCE),
)

DEFAULT_ADDED_RESISTANCE_PRIORS = AddedResistancePriors(
    scale=Prior(mean=0.4, std=0.15, source=_ADDED_SOURCE),
    period_reference_s=Prior(mean=7.5, std=2.0, source=_ADDED_SOURCE),
    head_factor=Prior(mean=1.0, std=0.3, source=_ADDED_SOURCE),
    following_factor=Prior(mean=0.25, std=0.15, source=_ADDED_SOURCE),
)

DEFAULT_SFOC_PRIORS = SfocPriors(
    sfoc_base_g_per_kwh=Prior(mean=195.0, std=15.0, source=_SFOC_SOURCE),
    sfoc_min_load_fraction=Prior(mean=0.75, std=0.1, source=_SFOC_SOURCE),
    sfoc_curvature=Prior(mean=2.0, std=0.75, source=_SFOC_SOURCE),
)
