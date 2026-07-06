"""Mission weights (B4/B5): two per-passage sliders — Pace and Comfort —
mapped to a common €-equivalent scoring unit, plus the fixed per-vessel wear
weight (from `VesselSpec.wear_policy`, B5) combined in at the optimiser.

Coefficients here are the same shape as the validated demo's slider->weight
mapping (fuel ~=EUR1/L, time up to ~=EUR25/min, comfort scaled x1.2),
renamed Pace/Comfort and with wear removed (B5) — pricing/ops input may
revise the constants, not twin fitting, so they're versioned separately
from `VesselSpec`.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.vessel_spec import WearPolicy

WEIGHTS_MODEL_VERSION = "0.2.0"

DIESEL_PRICE_EUR_PER_L = 1.0
DIESEL_DENSITY_KG_PER_L = 0.85
TIME_VALUE_EUR_PER_MIN_MAX = 25.0
# The demo's score line scored comfort as `W.comfort * L.comfort * 30` — the
# slider->weight mapping (comf/100*1.2) and the x30 index-point conversion
# are folded together here into one €-per-index-point constant, so the
# optimiser's score formula can just do `weight * comfort_index` uniformly
# (same treatment B5 gives the wear weight, see VesselSpec.WearPolicy).
COMFORT_EUR_PER_INDEX_POINT_MAX = 1.2 * 30


@dataclass(frozen=True)
class MissionWeights:
    fuel_eur_per_kg: float
    time_eur_per_min: float
    comfort_eur_per_index_point: float


@dataclass(frozen=True)
class Weights:
    fuel_eur_per_kg: float
    time_eur_per_min: float
    comfort_eur_per_index_point: float
    wear_eur_per_index_point: float


def weights_from_mission(pace: float, comfort: float) -> MissionWeights:
    """pace: 0 (economy) .. 100 (schedule). comfort: 0 (crew transit) .. 100
    (owner aboard). No wear parameter (B5) — wear is vessel policy, not a
    passage input."""
    if not 0 <= pace <= 100:
        raise ValueError(f"pace must be in [0, 100], got {pace}")
    if not 0 <= comfort <= 100:
        raise ValueError(f"comfort must be in [0, 100], got {comfort}")

    fuel_eur_per_l = (100 - pace) / 100 * 1.0 + 0.15
    fuel_eur_per_kg = fuel_eur_per_l / DIESEL_DENSITY_KG_PER_L
    time_eur_per_min = pace / 100 * TIME_VALUE_EUR_PER_MIN_MAX + 0.5
    comfort_eur_per_index_point = comfort / 100 * COMFORT_EUR_PER_INDEX_POINT_MAX
    return MissionWeights(
        fuel_eur_per_kg=fuel_eur_per_kg,
        time_eur_per_min=time_eur_per_min,
        comfort_eur_per_index_point=comfort_eur_per_index_point,
    )


def combine_weights(mission: MissionWeights, wear_policy: WearPolicy) -> Weights:
    return Weights(
        fuel_eur_per_kg=mission.fuel_eur_per_kg,
        time_eur_per_min=mission.time_eur_per_min,
        comfort_eur_per_index_point=mission.comfort_eur_per_index_point,
        wear_eur_per_index_point=wear_policy.weight_eur_equivalent,
    )


@dataclass(frozen=True)
class MissionPreset:
    pace: float
    comfort: float


MISSION_PRESETS: dict[str, MissionPreset] = {
    "owner_aboard": MissionPreset(pace=25, comfort=90),
    "repositioning": MissionPreset(pace=20, comfort=10),
    "charter_turnaround": MissionPreset(pace=85, comfort=40),
    "delivery": MissionPreset(pace=45, comfort=15),
}
