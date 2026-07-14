# Historical-fit import runbook (ticket B7)

The end-to-end flow from a historical track + performance log to a fitted
twin. Mirrors `deploy/README.md`'s precedent: a copy-paste sequence, every
step says what it's for. No real historical data exists in this repo yet
(that's ticket B6's job) — this is the tooling that's ready for it.

## 1. Compute the bbox from a historical track

You need a `core.track.TrackPoint` CSV first (`ingest/track_io.py`'s
format: `t_epoch_s,lat_deg,lon_deg,hs_m,period_peak_s,period_mean_s,
wave_from_deg,wind_u_ms,wind_v_ms`, env columns blank until step 3
annotates them) — from a monitoring system's GPS log, an e-logbook
export, or wherever position/time data for the passage lives.

```
python3 -m ingest.track_bbox my_track.csv
```

Prints the covering bbox (`core.track.covering_bbox`, `margin_deg=0.25`
default) and time span — use the printed `--bbox ...` args directly in
the next step. **Minor flag:** raises if the track crosses the
antimeridian (±180°) — not supported, irrelevant for Med/UK routes.

## 2. Ingest geography + weather for that bbox

Ordering matters — see `data/historical/README.md`. `<id>` is any short
name for this track/region (a folder name, not parsed for meaning):

```
mkdir -p data/historical/<id>/geography data/historical/<id>/weather

python3 -m ingest.fetch_gebco --bbox <LON_MIN> <LAT_MIN> <LON_MAX> <LAT_MAX> \
  --out data/historical/<id>/geography/bathymetry_<id>.npz

python3 -m ingest.fetch_gshhg --bbox <LON_MIN> <LAT_MIN> <LON_MAX> <LAT_MAX> \
  --out data/historical/<id>/geography/coastline_<id>.json \
  --bathymetry data/historical/<id>/geography/bathymetry_<id>.npz
```

Both scripts refuse to run with a differing `--bbox` unless `--out` (and
`--bathymetry`, for `fetch_gshhg.py`) is also given explicitly — the
guard against silently overwriting the committed western-Med files.

## 3. Annotate the track with ERA5 reanalysis

**One-time setup, per machine, not automatable:** register a free account
at https://cds.climate.copernicus.eu, accept the ERA5 single-levels
dataset's licence, and write your API key to `~/.cdsapirc`. Real CDS
credentials are required here — this step cannot be mocked or skipped.

```
python3 -m ingest.fetch_era5_track my_track.csv \
  --out data/historical/<id>/weather/era5_<id>.csv \
  --coastline-path data/historical/<id>/geography/coastline_<id>.json \
  --bathymetry-path data/historical/<id>/geography/bathymetry_<id>.npz
```

One CDS request for the whole track (not per-point); logs a `WARNING`
first if the track spans more than ~30 days. Output is an annotated
`TrackPoint` CSV with `hs_m`/`period_peak_s`/`period_mean_s`/
`wave_from_deg`/`wind_u_ms`/`wind_v_ms` now populated.

**Verified live (2026-07-14)** — see `docs/plans/ticket-B7.md`'s "Live
ERA5 verification result" for the real numbers and the two real defects
a live run found and fixed (a real response is a zip of two per-stream
files at different resolutions, not one raw NetCDF).

## 4. Parse your performance log with an adapter

Pick the adapter matching your source format (`fit/import_adapters.py`):
`MonitoringCsvAdapter` (flowmeter-tier, richest signal), `ELogbookAdapter`
(SOG-only, litres/hour), `NoonReportAdapter` (daily-aggregate, always
low-frequency), or `AnnotatedTrackCsvAdapter` (reads step 3's own output
directly, if that's your whole source — environment only, no
performance). Every adapter's docstring documents its exact assumed CSV
format (fabricated for this ticket — B6 will confirm the real shapes).

```python
from fit.import_adapters import MonitoringCsvAdapter

rows = MonitoringCsvAdapter().parse(
    "my_monitoring_export.csv", vessel_id="<vessel>", passage_id="<passage>"
)
```

A source with no logged sea state (most e-logbook/noon-report exports)
needs its rows' positions/times cross-referenced against step 3's
annotated track separately — this ticket doesn't solve automatic
row-joining across sources by `passage_id`.

## 5. Build fit-ready segments and fit

```python
from core.vessel_spec import VesselSpec
from fit.import_pipeline import rows_to_segments
from fit.pipeline import fit_twin_from_segments

base_spec = VesselSpec.from_yaml("data/vessel_specs/mys_50m_default.yaml")
segments = rows_to_segments(rows)  # groups by (vessel_id, passage_id, source),
                                    # stamps identity + per-source noise weighting

fitted = fit_twin_from_segments(
    segments, base_spec, holdout_group_by="passage_id"  # once you have >=2 passages
)
print(fitted.fit_report.validation)
```

**Always call `fit_twin_from_segments`, never `fit_twin`, for imported
data** — `fit_twin` takes raw `TelemetrySample`s and always re-extracts
segments itself, which discards the identity `rows_to_segments` already
stamped; `passage_holdout_split`/`holdout_group_by` has nothing to group
by through that path. `holdout_group_by="passage_id"` requires at least 2
distinct passages (or `"vessel_id"` for at least 2 vessels) — omit it for
a single-passage import, which falls back to the flat `holdout_split`
(correct when there's nothing to group by).

## Repeat for more passages

Steps 1–4 per passage/source, concatenating all resulting
`CanonicalImportRow`s (from as many adapters/sources as you have) before
step 5's single `rows_to_segments` call — that's what lets
`passage_holdout_split` do its job honestly, per
`docs/plans/ticket-B7.md`'s empirical finding on why a flat split
flatters autocorrelated historical data.
