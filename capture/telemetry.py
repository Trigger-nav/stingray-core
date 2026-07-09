"""Canonical telemetry schema (ticket B1/B5). Sensor tiers follow
CLAUDE.md's graceful-degradation principle ("every feature must define
behaviour at each sensor tier: full integration / NMEA + engine fuel rate
/ NMEA + manual fuel") -- `sensor_tier` on every sample makes degradation
explicit and queryable, not inferred from which fields happen to be
`None`.

`motion` is optional and tier-flagged (B5: "IMU: optional early add, not
committed -- the ingestion/telemetry schema should accept a motion
source if present, but nothing in MVP scope depends on it").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SensorTier = Literal["full_integration", "nmea_engine_fuel_rate", "nmea_manual_fuel"]


@dataclass(frozen=True)
class MotionSample:
    """Optional IMU reading -- absent entirely until an IMU is actually
    fitted (B5: "no IMU -> comfort model stays priors-only until Phase 2").
    """

    heave_m: float | None = None
    roll_deg: float | None = None
    pitch_deg: float | None = None
    accel_vertical_ms2: float | None = None


@dataclass(frozen=True)
class TelemetrySample:
    timestamp: float  # unix epoch seconds
    sensor_tier: SensorTier
    lat_deg: float | None = None
    lon_deg: float | None = None
    sog_kn: float | None = None
    cog_deg: float | None = None
    heading_deg: float | None = None
    stw_kn: float | None = None  # speed through water, where available
    engine_fuel_rate_l_per_h: float | None = None
    engine_rpm: float | None = None
    motion: MotionSample | None = None
    source_pgn: int | None = None  # provenance: which PGN this sample came from
