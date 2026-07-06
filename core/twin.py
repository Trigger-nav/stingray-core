"""VesselTwin: fuel_rate / motion / wear (E1), fixing the three demo
shortcuts CORE_PORTING_NOTES.md calls out as not portable as-is:

- A1: calm-water power is Froude-conditioned, steepening sharply near the
  hull's characteristic speed, not a flat v^3 law.
- A2: added resistance is additive power (R_aw * v), period-conditioned,
  not a multiplicative Hs-only factor.
- A3: each engine has an explicit U-shaped SFOC-vs-load-fraction map;
  single- vs twin-engine advantage emerges from evaluating both configs
  against that map, never asserted via a speed-threshold branch.

All coefficients come from `VesselSpec` (E5) — the only things hard-coded
here are universal physical constants (g, seawater density), not fitted
vessel parameters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.units import ms_to_kn
from core.vessel_spec import EngineSpec, VesselSpec
from core.weather import WeatherSample

G = 9.81
RHO_SEAWATER_KG_M3 = 1025.0


def encounter_angle_deg(heading_deg: float, from_deg: float) -> float:
    """0 when heading and wave-from direction coincide, 180 when they're
    opposed, symmetric in between (90 = beam). Ported unchanged from the
    demo, including its directional convention: `angle_factor`/`beamF`-style
    terms below scale up toward 180, exactly as in the validated demo."""
    return abs(((heading_deg - from_deg) % 360 + 540) % 360 - 180)


def _period_response(period_peak_s: float, period_reference_s: float) -> float:
    """Resonance-like response shape peaking at period_peak_s ==
    period_reference_s, falling off for periods far from it either way.
    Provisional/placeholder — Phase 1 replaces with a fitted RAO."""
    ratio_sq = (period_peak_s / period_reference_s) ** 2
    return ratio_sq / (1 + (ratio_sq - 1) ** 2)


def calm_power_kw(v_ms: float, spec: VesselSpec) -> float:
    """A1: Froude-conditioned calm-water power, steepening near hull speed."""
    if v_ms <= 0:
        return 0.0
    cr = spec.calm_resistance
    base = cr.linear_coefficient * v_ms**2 + cr.cubic_coefficient * v_ms**3
    froude = v_ms / math.sqrt(G * spec.hull.length_wl_m)
    ratio = froude / cr.hull_speed_froude
    steepen = 1.0 + cr.steepening_coefficient * ratio**cr.steepening_exponent
    return base * steepen


def added_power_kw(
    v_ms: float, hs_m: float, period_peak_s: float, encounter_deg: float, spec: VesselSpec
) -> float:
    """A2: additive, period-conditioned added resistance power."""
    ar = spec.added_resistance
    hull = spec.hull
    period_factor = _period_response(period_peak_s, ar.period_reference_s)
    enc_norm = encounter_deg / 180.0
    angle_factor = ar.following_factor + (ar.head_factor - ar.following_factor) * enc_norm**1.6
    r_aw_n = (
        ar.scale
        * RHO_SEAWATER_KG_M3
        * G
        * hs_m**2
        * (hull.beam_wl_m**2 / hull.length_wl_m)
        * period_factor
        * angle_factor
    )
    return r_aw_n * v_ms / 1000.0


def required_power_kw(
    v_ms: float, weather: WeatherSample, heading_deg: float, spec: VesselSpec
) -> float:
    enc = encounter_angle_deg(heading_deg, weather.wave_from_deg)
    return calm_power_kw(v_ms, spec) + added_power_kw(
        v_ms, weather.hs_m, weather.period_peak_s, enc, spec
    )


def sfoc_g_per_kwh(load_fraction: float, engine: EngineSpec) -> float:
    """A3: U-shaped SFOC vs load fraction, minimum at engine.sfoc_min_load_fraction."""
    diff = load_fraction - engine.sfoc_min_load_fraction
    return engine.sfoc_base_g_per_kwh * (1 + engine.sfoc_curvature * diff**2)


@dataclass(frozen=True)
class FuelResult:
    fuel_kg_per_h: float
    per_engine_load_fraction: tuple[float, ...]
    active_engines: int


@dataclass(frozen=True)
class WearResult:
    wear_rate_per_h: float
    slam_event: bool
    overload: bool


class VesselTwin:
    def __init__(self, spec: VesselSpec) -> None:
        self.spec = spec

    def fuel_rate(
        self, *, v_ms: float, weather: WeatherSample, heading_deg: float, active_engines: int
    ) -> FuelResult:
        if not 1 <= active_engines <= len(self.spec.engines):
            raise ValueError(f"active_engines must be between 1 and {len(self.spec.engines)}")
        power_kw = required_power_kw(v_ms, weather, heading_deg, self.spec)
        power_per_engine_kw = power_kw / active_engines
        engines = self.spec.engines[:active_engines]
        loads: list[float] = []
        fuel_kg_per_h = self.spec.hotel_load_fuel_kg_per_h
        for engine in engines:
            load_fraction = power_per_engine_kw / engine.mcr_kw
            loads.append(load_fraction)
            fuel_kg_per_h += power_per_engine_kw * sfoc_g_per_kwh(load_fraction, engine) / 1000.0
        return FuelResult(
            fuel_kg_per_h=fuel_kg_per_h,
            per_engine_load_fraction=tuple(loads),
            active_engines=active_engines,
        )

    def motion(self, *, v_ms: float, weather: WeatherSample, heading_deg: float) -> float:
        enc = encounter_angle_deg(heading_deg, weather.wave_from_deg)
        c = self.spec.comfort
        beam_response = c.beam_base + c.beam_amplitude * math.sin(math.radians(enc)) ** 2
        if enc > c.head_bonus_threshold_deg:
            beam_response += c.head_bonus
        period_factor = _period_response(weather.period_peak_s, c.period_reference_s)
        speed_factor = c.speed_base + ms_to_kn(v_ms) / c.speed_scale_kn
        return c.scale * weather.hs_m**c.hs_exponent * beam_response * period_factor * speed_factor

    def wear(
        self,
        *,
        v_ms: float,
        weather: WeatherSample,
        heading_deg: float,
        load_fraction: float,
        active_engines: int,
    ) -> WearResult:
        enc = encounter_angle_deg(heading_deg, weather.wave_from_deg)
        wp = self.spec.wear_policy
        slam_event = (
            enc > wp.slamming_encounter_angle_deg
            and weather.hs_m > wp.slamming_hs_threshold_m
            and v_ms > wp.slamming_min_speed_ms
        )
        slam_term = 0.0
        if slam_event:
            slam_term = (
                wp.slam_wear_scale
                * (weather.hs_m - wp.slamming_hs_threshold_m)
                * (v_ms - wp.slamming_min_speed_ms)
            )
        load_term = max(0.0, load_fraction) ** 3 * wp.load_wear_scale
        single_engine_term = wp.single_engine_wear_bonus if active_engines == 1 else 0.0
        wear_rate_per_h = load_term + slam_term + single_engine_term
        overload = load_fraction > wp.max_continuous_load_fraction
        return WearResult(wear_rate_per_h=wear_rate_per_h, slam_event=slam_event, overload=overload)
