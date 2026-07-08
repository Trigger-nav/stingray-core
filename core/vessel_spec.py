"""VesselSpec: every twin/optimiser constant lives here, loaded from YAML —
nothing numeric is hard-coded in the model classes (E5). All values in the
shipped default spec are synthetic placeholders (fitted parameters with real
priors are ticket 0.6, naval-architecture consultancy); `provisional=True`
marks that status in code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class HullParticulars:
    length_wl_m: float
    beam_wl_m: float
    block_coefficient: float
    draft_m: float


@dataclass(frozen=True)
class CalmResistanceCurve:
    """Calm-water power as a function of Froude number (A1) — steepens
    sharply near `hull_speed_froude`, replacing the demo's flat v^3 law."""

    linear_coefficient: float  # kW per (m/s)^2
    cubic_coefficient: float  # kW per (m/s)^3
    steepening_coefficient: float
    steepening_exponent: float
    hull_speed_froude: float


@dataclass(frozen=True)
class AddedResistanceCoefficients:
    """Additive, period-conditioned added resistance (A2) — a STAWAVE-class
    semi-empirical shape, not the demo's multiplicative Hs-only factor."""

    scale: float
    period_reference_s: float
    head_factor: float
    following_factor: float


@dataclass(frozen=True)
class EngineSpec:
    """Per-engine fuel map: U-shaped SFOC in load fraction (A3)."""

    name: str
    mcr_kw: float
    sfoc_base_g_per_kwh: float
    sfoc_min_load_fraction: float
    sfoc_curvature: float


@dataclass(frozen=True)
class ComfortCoefficients:
    scale: float
    hs_exponent: float
    beam_base: float
    beam_amplitude: float
    head_bonus: float
    head_bonus_threshold_deg: float
    period_reference_s: float
    speed_base: float
    speed_scale_kn: float


@dataclass(frozen=True)
class WearPolicy:
    """Per-vessel machinery-care policy (B5) — replaces the demo's wear
    slider. `weight_eur_equivalent` scores wear under every plan regardless
    of Pace/Comfort; the two thresholds below are hard constraints the
    optimiser prunes on, same mechanism as land/no-go (A5).
    `load_cycling_limit` is accepted but unenforced until ticket 0.4
    introduces per-leg speed (see plan scope note).

    The demo scored wear as `(wear_slider/100) * wear_rate * 18`; with no
    slider anymore, `weight_eur_equivalent` bakes in both the x18 index-
    point conversion and an assumed "moderate care" level (equivalent to
    the demo's wear=50) — i.e. 0.5 * 18 = 9.0 for the shipped default."""

    weight_eur_equivalent: float
    max_continuous_load_fraction: float
    slamming_hs_threshold_m: float
    slamming_min_speed_ms: float
    slamming_encounter_angle_deg: float
    load_wear_scale: float
    slam_wear_scale: float
    single_engine_wear_bonus: float
    load_cycling_limit: float | None = None


@dataclass(frozen=True)
class VesselSpec:
    name: str
    hull: HullParticulars
    calm_resistance: CalmResistanceCurve
    added_resistance: AddedResistanceCoefficients
    engines: tuple[EngineSpec, ...]
    hotel_load_fuel_kg_per_h: float
    fuel_density_kg_per_l: float
    co2_per_kg_fuel: float
    comfort: ComfortCoefficients
    wear_policy: WearPolicy
    # Flat safety margin (ticket 0.8) -- not vessel-class-derived UKC
    # policy, that's a naval-arch question, same provisional status as
    # ticket 0.6's fitted coefficients. core.legs enforces
    # hull.draft_m + min_under_keel_clearance_m as a hard constraint
    # (A5 pattern), except within the pilotage-exemption radius of a
    # declared origin/destination.
    min_under_keel_clearance_m: float
    provisional: bool = field(default=True)

    @classmethod
    def from_yaml(cls, path: str | Path) -> VesselSpec:
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> VesselSpec:
        return cls(
            name=data["name"],
            provisional=data.get("provisional", True),
            hull=HullParticulars(**data["hull"]),
            calm_resistance=CalmResistanceCurve(**data["calm_resistance"]),
            added_resistance=AddedResistanceCoefficients(**data["added_resistance"]),
            engines=tuple(EngineSpec(**e) for e in data["engines"]),
            hotel_load_fuel_kg_per_h=data["hotel_load_fuel_kg_per_h"],
            fuel_density_kg_per_l=data["fuel_density_kg_per_l"],
            co2_per_kg_fuel=data["co2_per_kg_fuel"],
            comfort=ComfortCoefficients(**data["comfort"]),
            wear_policy=WearPolicy(**data["wear_policy"]),
            min_under_keel_clearance_m=data["min_under_keel_clearance_m"],
        )
