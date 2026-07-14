"""Canonical historical-import row type (ticket B7 Part 3). A new,
deliberately-superset schema -- **not** a reuse of `capture/telemetry.py`'s
`TelemetrySample`. That schema is PGN-provenance-shaped for live NMEA
sensor-tier degradation (ticket B1/B5); conflating the two would wrongly
couple historical-import source heterogeneity (CSV export vendor, e-log
format, noon-report text) to that model. `CanonicalImportRow` is what
every `ImportAdapter` (`fit/import_adapters.py`) produces; `fit/
import_pipeline.py` is what turns rows into fit-ready `SteadyStateSegment`s.

Environmental field names intentionally mirror `core.track.TrackPoint`'s,
so `AnnotatedTrackCsvAdapter` (the first real consumer of ticket B7 Parts
1+2's output) is a near-mechanical field copy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalImportRow:
    t_epoch_s: float
    lat_deg: float
    lon_deg: float
    vessel_id: str
    passage_id: str
    source: str  # "monitoring_csv" | "e_logbook" | "noon_report" | "bridge_sim" | "annotated_track"
    stw_kn: float | None = None
    sog_kn: float | None = None
    heading_deg: float | None = None
    active_engines: int | None = None
    fuel_kg_per_h: float | None = None
    hs_m: float | None = None
    period_peak_s: float | None = None
    period_mean_s: float | None = None  # amendment 2, additive -- mirrors TrackPoint
    wave_from_deg: float | None = None
    wind_u_ms: float | None = None
    wind_v_ms: float | None = None
    is_low_frequency: bool = False


# Amendment 1 (ticket B7 plan review): the original draft also carried a
# per-row `fuel_noise_std_fraction` override on CanonicalImportRow itself.
# Dropped -- it was speculative, had no working mechanism to reach the
# residual weighting (fixed properly in fit/import_pipeline.py, which
# stamps SteadyStateSegment.fuel_noise_multiplier post-extraction instead),
# and per-source granularity is all real B6 data will realistically
# support. Every entry here is order-of-magnitude, provisional, pending
# real per-source calibration once B6 has real data -- the same honesty
# fit/priors.py's `source` fields already model (design principle #4, "no
# invented numbers"). monitoring_csv matches fit/calm_resistance.py's
# existing global DEFAULT_FUEL_NOISE_STD_FRACTION (flowmeter-tier, the
# best case); e_logbook and noon_report are coarser, hand-estimated fuel
# figures and get wider bands; bridge_sim (Plymouth's simulator, once its
# adapter exists) is treated as flowmeter-tier since its "fuel" comes
# straight from the simulator's own model, not a real sensor's noise.
SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION: dict[str, float] = {
    "monitoring_csv": 0.03,
    "e_logbook": 0.08,
    "noon_report": 0.15,
    "bridge_sim": 0.03,
    "annotated_track": 0.03,
}

# STW-vs-SOG handling (ticket B7 Part 3): B7 adds no current-reanalysis
# source, so reverse-correcting SOG toward STW via core.units's current-
# triangle math run backward isn't implementable here -- rows with no STW
# carry SOG as a stand-in instead, at a wider noise band. Provisional,
# same honesty as the table above; reverse-triangle correction is a real
# follow-up once a current data source exists.
SOG_FALLBACK_NOISE_MULTIPLIER = 2.0
