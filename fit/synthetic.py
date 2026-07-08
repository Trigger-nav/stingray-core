"""Synthetic telemetry generator (ticket 0.6) -- backs the acceptance
test in `tests/test_fit_acceptance.py`: generate telemetry from a twin
with **known** parameters, realistic noise, and injected junk, then
check the pipeline recovers the knowns and rejects the junk. No real
data needed.

Each `Condition` becomes one contiguous block of samples in the returned
stream, separated from its neighbours by a deliberate time gap (beyond
`fit.segments.DEFAULT_MAX_GAP_S`) so blocks never accidentally merge --
including two adjacent blocks that happen to share the same speed/engine
config and differ only in sea state, which `extract_steady_state_segments`
deliberately doesn't check on its own (see that module's docstring).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.twin import VesselTwin
from core.units import kn_to_ms
from core.vessel_spec import VesselSpec
from core.weather import WeatherSample
from fit.segments import DEFAULT_MAX_GAP_S
from fit.telemetry import TelemetrySample

# Reaches to 18kn deliberately -- the shipped default spec's calm-power
# steepening onset (hull_speed_froude=0.4) lands around 16-17kn for this
# hull, so a grid stopping at 16kn barely probes the steepening region at
# all, leaving `steepening_coefficient`/`steepening_exponent` poorly
# constrained (found empirically while validating this module: parameter
# recovery was poor with speeds only up to 16kn, and improved sharply
# once the grid reached past the steepening onset).
DEFAULT_SPEEDS_KN = (6.0, 8.0, 10.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0)
DEFAULT_HS_LEVELS_M = (0.0, 0.3, 1.0, 2.0)
DEFAULT_HEADING_DEG = 180.0
DEFAULT_WAVE_FROM_DEG = 0.0
DEFAULT_PERIOD_PEAK_S = 7.0

SAMPLE_INTERVAL_H = 1.0 / 60.0  # 1-minute samples
SAMPLES_PER_CONDITION = 15  # 15 min/condition, comfortably > default min_duration_s (5 min)
# Strictly greater than fit.segments.DEFAULT_MAX_GAP_S, converted to hours,
# so every condition block is unambiguously separated from its neighbours.
GAP_H = (DEFAULT_MAX_GAP_S * 2) / 3600.0

DEFAULT_FUEL_NOISE_STD_FRACTION = 0.03
DEFAULT_STW_NOISE_STD_MS = 0.05
DEFAULT_HS_NOISE_STD_M = 0.1
DEFAULT_JUNK_FRACTION = 0.15


@dataclass(frozen=True)
class JunkBlock:
    t_start_h: float
    t_end_h: float
    kind: str  # "manoeuvre" | "tank_transfer"


@dataclass(frozen=True)
class Condition:
    speed_kn: float
    heading_deg: float
    hs_m: float
    period_peak_s: float
    wave_from_deg: float
    active_engines: int


def default_synthetic_conditions(
    engine_configs: tuple[int, ...] = (1, 2),
    speeds_kn: tuple[float, ...] = DEFAULT_SPEEDS_KN,
    hs_levels_m: tuple[float, ...] = DEFAULT_HS_LEVELS_M,
    heading_deg: float = DEFAULT_HEADING_DEG,
    wave_from_deg: float = DEFAULT_WAVE_FROM_DEG,
    period_peak_s: float = DEFAULT_PERIOD_PEAK_S,
) -> list[Condition]:
    """Cross product of every speed x every engine config x every sea
    state -- overlapping-speed multi-config coverage *by default*, the
    power/SFOC identifiability fix (`fit/calm_resistance.py`'s
    docstring). A caller has to deliberately narrow `engine_configs` (as
    the acceptance test's degenerate scenario does) to lose that
    coverage, not build a grid without it by omission."""
    return [
        Condition(
            speed_kn=v,
            heading_deg=heading_deg,
            hs_m=hs,
            period_peak_s=period_peak_s,
            wave_from_deg=wave_from_deg,
            active_engines=n,
        )
        for n in engine_configs
        for v in speeds_kn
        for hs in hs_levels_m
    ]


def _true_fuel_kg_per_h(twin: VesselTwin, condition: Condition) -> float:
    weather = WeatherSample(
        hs_m=condition.hs_m,
        period_peak_s=condition.period_peak_s,
        period_mean_s=condition.period_peak_s,
        wave_from_deg=condition.wave_from_deg,
        wind_u_ms=0.0,
        wind_v_ms=0.0,
        current_u_ms=0.0,
        current_v_ms=0.0,
    )
    return twin.fuel_rate(
        v_ms=kn_to_ms(condition.speed_kn),
        weather=weather,
        heading_deg=condition.heading_deg,
        active_engines=condition.active_engines,
    ).fuel_kg_per_h


def generate_synthetic_telemetry(
    ground_truth_spec: VesselSpec,
    conditions: list[Condition],
    *,
    fuel_noise_std_fraction: float = DEFAULT_FUEL_NOISE_STD_FRACTION,
    stw_noise_std_ms: float = DEFAULT_STW_NOISE_STD_MS,
    hs_noise_std_m: float = DEFAULT_HS_NOISE_STD_M,
    junk_fraction: float = DEFAULT_JUNK_FRACTION,
    samples_per_condition: int = SAMPLES_PER_CONDITION,
    sample_interval_h: float = SAMPLE_INTERVAL_H,
    rng: np.random.Generator | None = None,
) -> tuple[list[TelemetrySample], list[JunkBlock]]:
    """Generates one contiguous, gap-separated block of samples per
    condition (order shuffled, so two conditions with the same speed/
    engine config aren't suspiciously always adjacent), computing the
    **true** `fuel_kg_per_h` via the real `core.twin.VesselTwin` against
    `ground_truth_spec` and recording noised inputs (`stw_ms`, `hs_m`)
    *and* output (`fuel_kg_per_h`) -- an errors-in-variables setup, not
    output-noise-only.

    A `junk_fraction` of blocks are corrupted as either:
    - a **manoeuvring transient**: speed/heading pushed outside
      `extract_steady_state_segments`'s tolerance on *every* sample in the
      block, so no sub-run of it should ever survive extraction intact.
    - a **tank-transfer artefact**: exactly *one* interior sample's
      `fuel_kg_per_h` is spiked/dropped, uncorrelated with any power
      change. Extraction correctly excludes just that one sample and
      keeps the genuinely-clean data before/after it as (typically two)
      separate legitimate segments -- that's correct behaviour, not a
      leak, and tests should check for it accordingly (see
      `tests/test_fit_acceptance.py`).

    Returns `(samples, junk_blocks)` -- `JunkBlock` carries the corrupted
    span and which kind it was, for a test to check extraction's actual
    behaviour against each kind's specific expectation.
    """
    if rng is None:
        rng = np.random.default_rng()
    twin = VesselTwin(ground_truth_spec)

    order = rng.permutation(len(conditions))
    samples: list[TelemetrySample] = []
    junk_blocks: list[JunkBlock] = []

    t = 0.0
    for idx in order:
        condition = conditions[int(idx)]
        is_junk = rng.random() < junk_fraction
        junk_kind = rng.choice(["manoeuvre", "tank_transfer"]) if is_junk else None
        true_fuel = _true_fuel_kg_per_h(twin, condition)

        block_start = t
        for i in range(samples_per_condition):
            stw_ms = kn_to_ms(condition.speed_kn) + rng.normal(0.0, stw_noise_std_ms)
            hs_m = max(0.0, condition.hs_m + rng.normal(0.0, hs_noise_std_m))
            heading_deg = condition.heading_deg
            fuel_kg_per_h = true_fuel * (1.0 + rng.normal(0.0, fuel_noise_std_fraction))

            if junk_kind == "manoeuvre":
                stw_ms += rng.uniform(-3.0, 3.0)
                heading_deg += rng.uniform(-60.0, 60.0)
            if junk_kind == "tank_transfer" and i == samples_per_condition // 2:
                fuel_kg_per_h *= float(rng.choice([0.4, 1.8]))

            samples.append(
                TelemetrySample(
                    t_h=t,
                    stw_ms=stw_ms,
                    heading_deg=heading_deg % 360.0,
                    active_engines=condition.active_engines,
                    fuel_kg_per_h=fuel_kg_per_h,
                    hs_m=hs_m,
                    period_peak_s=condition.period_peak_s,
                    wave_from_deg=condition.wave_from_deg,
                )
            )
            t += sample_interval_h

        if is_junk:
            junk_blocks.append(JunkBlock(t_start_h=block_start, t_end_h=t, kind=junk_kind))
        t += GAP_H

    return samples, junk_blocks
