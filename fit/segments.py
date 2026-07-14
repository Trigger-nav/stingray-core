"""Steady-state segment extraction (ticket 0.6, ROADMAP scope item b) --
rejects manoeuvring/transients and tank-transfer artefacts before any
telemetry reaches the fitting stage.

Both junk kinds are caught by the same mechanism: a maximal run of
samples is only kept together while every consecutive pair stays within
tolerance on speed, heading, engine config, *and* fuel-rate continuity.
A manoeuvring transient breaks the speed/heading tolerance; a
tank-transfer artefact (a fuel-rate spike/drop with no corresponding
power change) breaks the fuel-rate-continuity tolerance on its own,
independent of speed/heading -- deliberately checked separately, since a
tank-transfer artefact by definition doesn't disturb speed or heading at
all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from fit.telemetry import TelemetrySample

DEFAULT_MIN_DURATION_S = 300.0
DEFAULT_SPEED_TOL_KN = 0.5
DEFAULT_HEADING_TOL_DEG = 5.0
DEFAULT_FUEL_JUMP_TOL_FRACTION = 0.25
# A data dropout/gap shouldn't be silently bridged into one continuous
# segment -- also what gives fit/synthetic.py's condition-by-condition
# blocks an unambiguous boundary even when two adjacent blocks happen to
# share the same speed/heading/engine config (differing only in sea
# state, which this module deliberately doesn't check -- real steady legs
# legitimately drift in Hs without being a new "condition").
DEFAULT_MAX_GAP_S = 120.0

MS_TO_KN = 1.943844


@dataclass(frozen=True)
class SteadyStateSegment:
    t_start_h: float
    t_end_h: float
    mean_stw_ms: float
    mean_heading_deg: float
    active_engines: int
    mean_fuel_kg_per_h: float
    mean_hs_m: float
    mean_period_peak_s: float
    mean_wave_from_deg: float
    duration_h: float
    n_samples: int
    # ticket B7 Parts 3/4, additive -- every existing caller (extract_
    # steady_state_segments below, fit/synthetic.py's generator) leaves
    # these at their defaults, so this doesn't change today's behaviour
    # anywhere. Populated post-extraction by fit/import_pipeline.py's
    # stamp_segment_provenance (`dataclasses.replace`) -- extraction
    # itself has no concept of provenance and shouldn't grow one.
    # fuel_noise_multiplier feeds fit_calm_resistance/fit_added_
    # resistance's residual weighting (`fuel_noise_std_fraction *
    # mean_fuel_kg_per_h * fuel_noise_multiplier`) -- the actual
    # mechanism behind per-source noise handling.
    vessel_id: str | None = None
    passage_id: str | None = None
    fuel_noise_multiplier: float = 1.0


def _circular_diff_deg(a_deg: float, b_deg: float) -> float:
    return abs(((a_deg - b_deg + 180.0) % 360.0) - 180.0)


def _compatible(
    prev: TelemetrySample,
    cur: TelemetrySample,
    *,
    speed_tol_kn,
    heading_tol_deg,
    fuel_jump_tol_fraction,
    max_gap_s,
) -> bool:
    if (cur.t_h - prev.t_h) * 3600.0 > max_gap_s:
        return False
    if cur.active_engines != prev.active_engines:
        return False
    speed_diff_kn = abs(cur.stw_ms - prev.stw_ms) * MS_TO_KN
    if speed_diff_kn > speed_tol_kn:
        return False
    if _circular_diff_deg(cur.heading_deg, prev.heading_deg) > heading_tol_deg:
        return False
    fuel_ref = max(1e-6, (cur.fuel_kg_per_h + prev.fuel_kg_per_h) / 2)
    if abs(cur.fuel_kg_per_h - prev.fuel_kg_per_h) / fuel_ref > fuel_jump_tol_fraction:
        return False
    return True


def _to_segment(run: list[TelemetrySample]) -> SteadyStateSegment:
    n = len(run)
    duration_h = run[-1].t_h - run[0].t_h

    def circular_mean_deg(values: list[float]) -> float:
        rad = [math.radians(v) for v in values]
        y = sum(math.sin(r) for r in rad) / n
        x = sum(math.cos(r) for r in rad) / n
        return math.degrees(math.atan2(y, x)) % 360.0

    return SteadyStateSegment(
        t_start_h=run[0].t_h,
        t_end_h=run[-1].t_h,
        mean_stw_ms=sum(s.stw_ms for s in run) / n,
        mean_heading_deg=circular_mean_deg([s.heading_deg for s in run]),
        active_engines=run[0].active_engines,
        mean_fuel_kg_per_h=sum(s.fuel_kg_per_h for s in run) / n,
        mean_hs_m=sum(s.hs_m for s in run) / n,
        mean_period_peak_s=sum(s.period_peak_s for s in run) / n,
        mean_wave_from_deg=circular_mean_deg([s.wave_from_deg for s in run]),
        duration_h=duration_h,
        n_samples=n,
    )


def extract_steady_state_segments(
    samples: list[TelemetrySample],
    *,
    min_duration_s: float = DEFAULT_MIN_DURATION_S,
    speed_tol_kn: float = DEFAULT_SPEED_TOL_KN,
    heading_tol_deg: float = DEFAULT_HEADING_TOL_DEG,
    fuel_jump_tol_fraction: float = DEFAULT_FUEL_JUMP_TOL_FRACTION,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> list[SteadyStateSegment]:
    """Splits `samples` (assumed time-ordered, roughly regularly sampled)
    into maximal steady-state runs and aggregates each into one fit-ready
    `SteadyStateSegment`, dropping runs shorter than `min_duration_s`."""
    if not samples:
        return []

    min_duration_h = min_duration_s / 3600.0
    segments: list[SteadyStateSegment] = []
    run = [samples[0]]
    for prev, cur in zip(samples, samples[1:]):
        if _compatible(
            prev,
            cur,
            speed_tol_kn=speed_tol_kn,
            heading_tol_deg=heading_tol_deg,
            fuel_jump_tol_fraction=fuel_jump_tol_fraction,
            max_gap_s=max_gap_s,
        ):
            run.append(cur)
        else:
            if run[-1].t_h - run[0].t_h >= min_duration_h:
                segments.append(_to_segment(run))
            run = [cur]
    if run[-1].t_h - run[0].t_h >= min_duration_h:
        segments.append(_to_segment(run))
    return segments
