"""Per-source historical-import adapters (ticket B7 Part 3). One
`ImportAdapter` `Protocol` (matching this repo's established convention --
`capture/gateway.py`'s `GatewayReader`, `ingest/grib_common.py`'s
`_LandChecker`), four concrete adapters against fabricated,
documented-assumed formats -- no real files exist yet, that's ticket B6's
job. Each adapter owns its own unit/timezone normalisation at parse time
(the "per-source units/timezone audit"), matching ticket B1's own
"normalise at the ingest boundary" precedent.

**Named follow-up, not built here:** `BridgeSimulatorAdapter` (Plymouth
University's bridge-simulator log format) -- format unknown until their
data-brief response arrives. The `ImportAdapter` `Protocol` below is
designed to be its drop-in target once the format is known; no invented
fields.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from core.track import TRACK_CSV_COLUMNS
from fit.import_schema import CanonicalImportRow

# Approximate marine diesel oil density, kg/L -- a real, commonly-cited
# engineering constant (marine gas oil is typically ~0.82-0.87 kg/L at
# 15C; 0.85 is the standard round figure used absent a specific fuel
# assay), used only to convert ELogbookAdapter's L/h figures to kg/h.
# Provisional like every other conversion constant here -- flag, don't
# pretend to more precision than a real per-vessel fuel density would
# give.
MARINE_DIESEL_KG_PER_L = 0.85


class ImportAdapter(Protocol):
    def parse(self, path: Path, *, vessel_id: str, passage_id: str) -> list[CanonicalImportRow]: ...


def _local_to_epoch_s(date_str: str, time_str: str, tz_name: str) -> float:
    naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    aware = naive.replace(tzinfo=ZoneInfo(tz_name))
    return aware.timestamp()


class MonitoringCsvAdapter:
    """Flowmeter-tier monitoring-system export -- the richest, best-case
    signal (matches `fit/telemetry.py`'s own "flowmeter-tier observable"
    framing). Assumed format, fabricated for this ticket (no real export
    seen yet -- B6's job): one row per sample, already UTC, already STW
    and kg/h -- `timestamp_utc,lat_deg,lon_deg,stw_kn,heading_deg,
    active_engines,fuel_kg_per_h,hs_m,period_peak_s,wave_from_deg`
    (`hs_m`/`period_peak_s`/`wave_from_deg` optional, blank if the
    monitoring system doesn't log sea state -- a row missing any of them
    can still reach the low-frequency path or ERA5 annotation, just not
    `canonical_rows_to_telemetry_samples` directly). `timestamp_utc` is
    ISO 8601 with an explicit UTC offset (`...+00:00` or `...Z`) --
    flowmeter-tier systems are assumed to log UTC directly, unlike
    e-logbook/noon-report sources below."""

    def parse(self, path: Path, *, vessel_id: str, passage_id: str) -> list[CanonicalImportRow]:
        rows = []
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                ts = r["timestamp_utc"].replace("Z", "+00:00")
                rows.append(
                    CanonicalImportRow(
                        t_epoch_s=datetime.fromisoformat(ts).timestamp(),
                        lat_deg=float(r["lat_deg"]),
                        lon_deg=float(r["lon_deg"]),
                        vessel_id=vessel_id,
                        passage_id=passage_id,
                        source="monitoring_csv",
                        stw_kn=float(r["stw_kn"]),
                        heading_deg=float(r["heading_deg"]),
                        active_engines=int(r["active_engines"]),
                        fuel_kg_per_h=float(r["fuel_kg_per_h"]),
                        hs_m=float(r["hs_m"]) if r.get("hs_m") else None,
                        period_peak_s=float(r["period_peak_s"]) if r.get("period_peak_s") else None,
                        wave_from_deg=float(r["wave_from_deg"]) if r.get("wave_from_deg") else None,
                        is_low_frequency=False,
                    )
                )
        return rows


class ELogbookAdapter:
    """Electronic logbook export. Assumed format (fabricated, B6 will
    confirm the real shape): `local_datetime,timezone,lat_deg,lon_deg,
    sog_kn,heading_deg,active_engines,fuel_l_per_h` -- `local_datetime`
    (`YYYY-MM-DD HH:MM:SS`, naive) + an IANA `timezone` column (e-logbooks
    are commonly filled in against ship's local time, not UTC), **SOG
    only** (GPS-derived, no through-water sensor -- the common case for
    this source, hence no `stw_kn` here at all), and fuel in **L/h**
    (converted to kg/h via `MARINE_DIESEL_KG_PER_L`, not a per-vessel
    measured density -- flagged, provisional). Optional
    `hs_m,period_peak_s,wave_from_deg` columns (a captain's logged sea-
    state observation, when present -- blank otherwise): without them, a
    row can't reach `fit/import_pipeline.py`'s
    `canonical_rows_to_telemetry_samples` (which requires every field
    `TelemetrySample` needs) except via the low-frequency bypass or after
    separate ERA5 annotation (row-joining across sources by `passage_id`
    is not solved by this ticket, see `AnnotatedTrackCsvAdapter`)."""

    def parse(self, path: Path, *, vessel_id: str, passage_id: str) -> list[CanonicalImportRow]:
        rows = []
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                date_str, time_str = r["local_datetime"].split(" ", 1)
                rows.append(
                    CanonicalImportRow(
                        t_epoch_s=_local_to_epoch_s(date_str, time_str, r["timezone"]),
                        lat_deg=float(r["lat_deg"]),
                        lon_deg=float(r["lon_deg"]),
                        vessel_id=vessel_id,
                        passage_id=passage_id,
                        source="e_logbook",
                        sog_kn=float(r["sog_kn"]),
                        heading_deg=float(r["heading_deg"]),
                        active_engines=int(r["active_engines"]),
                        fuel_kg_per_h=float(r["fuel_l_per_h"]) * MARINE_DIESEL_KG_PER_L,
                        hs_m=float(r["hs_m"]) if r.get("hs_m") else None,
                        period_peak_s=float(r["period_peak_s"]) if r.get("period_peak_s") else None,
                        wave_from_deg=float(r["wave_from_deg"]) if r.get("wave_from_deg") else None,
                        is_low_frequency=False,
                    )
                )
        return rows


class NoonReportAdapter:
    """Classic daily noon report -- one row per day, pre-aggregated (per
    CLAUDE.md's B6 gotcha: "usually no wave/motion data, coarse fuel
    resolution"). Assumed format (fabricated, B6 will confirm): `
    report_date,local_time,timezone,lat_deg,lon_deg,distance_run_nm,
    hours_run,fuel_consumed_mt,active_engines`. `distance_run_nm`/
    `hours_run` give a mean **SOG** (distance made good over ground, not
    through water -- noon reports have no through-water sensor
    concept at all); `fuel_consumed_mt` (metric tons, the traditional
    bunkering unit) converts to kg/h over `hours_run`. Always
    `is_low_frequency=True` -- this is exactly the "bypasses segment
    extraction, wide uncertainty" path (`fit/import_pipeline.py`'s
    `daily_rows_to_segments`)."""

    def parse(self, path: Path, *, vessel_id: str, passage_id: str) -> list[CanonicalImportRow]:
        rows = []
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                hours_run = float(r["hours_run"])
                t_epoch_s = _local_to_epoch_s(r["report_date"], r["local_time"], r["timezone"])
                rows.append(
                    CanonicalImportRow(
                        t_epoch_s=t_epoch_s,
                        lat_deg=float(r["lat_deg"]),
                        lon_deg=float(r["lon_deg"]),
                        vessel_id=vessel_id,
                        passage_id=passage_id,
                        source="noon_report",
                        sog_kn=float(r["distance_run_nm"]) / hours_run,
                        active_engines=int(r["active_engines"]),
                        fuel_kg_per_h=(float(r["fuel_consumed_mt"]) * 1000.0) / hours_run,
                        is_low_frequency=True,
                    )
                )
        return rows


class AnnotatedTrackCsvAdapter:
    """Reads `ingest/fetch_era5_track.py`'s output CSV (`core.track.
    TrackPoint`/`TRACK_CSV_COLUMNS`, ticket B7 Parts 1+2) -- the first
    real, in-ticket consumer of that pipeline's output. A track alone
    carries position + environment, never vessel performance (speed/
    heading/fuel) -- those fields are left `None`; typical real usage
    pairs this adapter's rows with another source's performance-only rows
    sharing the same `passage_id` (row-joining across sources is not
    solved by this ticket -- `fit/import_pipeline.py`'s design doesn't
    require it, since environment-only rows simply contribute no segments
    of their own via `extract_steady_state_segments`, which needs
    `stw_ms`/`fuel_kg_per_h` on every `TelemetrySample`)."""

    def parse(self, path: Path, *, vessel_id: str, passage_id: str) -> list[CanonicalImportRow]:
        rows = []
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                optional = {
                    name: (float(r[name]) if r.get(name) not in ("", None) else None)
                    for name in TRACK_CSV_COLUMNS
                    if name not in ("t_epoch_s", "lat_deg", "lon_deg")
                }
                rows.append(
                    CanonicalImportRow(
                        t_epoch_s=float(r["t_epoch_s"]),
                        lat_deg=float(r["lat_deg"]),
                        lon_deg=float(r["lon_deg"]),
                        vessel_id=vessel_id,
                        passage_id=passage_id,
                        source="annotated_track",
                        is_low_frequency=False,
                        **optional,
                    )
                )
        return rows
