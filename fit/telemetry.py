"""Telemetry data contract (ticket 0.6). The flowmeter-tier observable
(TECHNICAL_ARCHITECTURE.md's sensor-tier matrix) -- the richest signal,
and the one `fit/synthetic.py` generates for the acceptance test.
NMEA+manual-fuel tiers are a Phase 1 concern once real sensor data
exists; this schema doesn't try to anticipate their shape.

Deliberately has no "is this junk" field: real telemetry never arrives
pre-labelled as manoeuvring/transient/tank-transfer-artefact -- that's
exactly what `fit/segments.py` has to figure out from the shape of the
data itself.

`wind_u_ms`/`wind_v_ms` (ticket B7 Part 2/3, additive, both optional):
`core.weather.WeatherSample` already carries wind, but this schema didn't
-- `ingest/fetch_era5_track.py`'s ERA5 annotator now can produce it, and
`fit/import_pipeline.py`'s `canonical_rows_to_telemetry_samples` carries
it through. Recorded only -- **not** wired into any `core.twin`/
`fit_calm_resistance`/`fit_added_resistance` physics in this ticket, no
new physics; a future ticket's job.
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
    wind_u_ms: float | None = None
    wind_v_ms: float | None = None
