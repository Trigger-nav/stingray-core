# `data/historical/` — track-driven region data (ticket B7 Part 1)

A track-driven bbox's geography/weather data lives under its own
subdirectory here, one per track/region:

```
data/historical/<track-id>/
  geography/
    bathymetry_<track-id>.npz     # ingest/fetch_gebco.py --bbox ... --out ...
    coastline_<track-id>.json     # ingest/fetch_gshhg.py --bbox ... --out ... --bathymetry ...
  weather/
    era5_<track-id>.csv           # ingest/fetch_era5_track.py's annotated track
```

**Not `data/geography/`/`data/weather/`** — those are the committed
western-Med corridor's own fixed files (`OPERATING_AREA_BBOX`); a
track-driven bbox's data must never collide with them by name or
directory. Everything under `data/historical/<track-id>/` is gitignored
(this README is the one tracked exception) — a historical passage's
weather is a fixed fact once fetched (closer in spirit to
`data/geography/`'s "static" treatment than `data/weather/`'s "stale
within hours" one), but it's still per-track content, not repo content,
same reasoning as `data/weather/*.npz`.

## Ordering (required, not just convention)

1. **GEBCO before GSHHG.** `fetch_gshhg.py` rasterises its land mask onto
   whatever `.npz` its `--bathymetry` arg points to — that file must
   already exist.
2. **Geography before weather.** `fetch_grib_ecmwf.py`/
   `fetch_grib_nomads.py` land-mask against a `RealGeography` built from
   `--coastline-path`/`--bathymetry-path` — point them at this track's own
   files, not the western-Med defaults, or land-masking runs against the
   wrong geography.
3. **Bbox before all of the above.** Compute it from the track first
   (`python3 -m ingest.track_bbox TRACK_CSV`).

See `docs/historical-import-runbook.md` for the full step-by-step flow,
including the ERA5 annotator and the `fit/` import layer that consumes
its output.
