"""Telemetry data contract (ticket 0.6). The flowmeter-tier observable
(TECHNICAL_ARCHITECTURE.md's sensor-tier matrix) -- the richest signal,
and the one `fit/synthetic.py` generates for the acceptance test.
NMEA+manual-fuel tiers are a Phase 1 concern once real sensor data
exists; this schema doesn't try to anticipate their shape.

Deliberately has no "is this junk" field: real telemetry never arrives
pre-labelled as manoeuvring/transient/tank-transfer-artefact -- that's
exactly what `fit/segments.py` has to figure out from the shape of the
data itself.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TelemetrySample:
    t_h: float
    stw_ms: float
    heading_deg: float
    active_engines: int
    fuel_kg_per_h: float
    hs_m: float
    period_peak_s: float
    wave_from_deg: float
