# Ticket 0.5 — GRIB pipeline in production: NOMADS + ECMWF open data

## Context

Tickets 0.1–0.4 (done, committed) built the production optimiser core against
real geography but synthetic weather (`SyntheticWeatherField`'s three demo
scenarios). `core/weather.py` already has the consumer side ready and waiting:
`GriddedWeatherField` (bilinear-in-space, linear-in-time, land-as-NaN per B2)
was built in an earlier ticket explicitly as "the interpolation engine a real
gridded product (ticket 0.5) will sit behind." This ticket builds that real
product: ingest scripts that fetch real GFS/WW3 (NOAA NOMADS) and IFS (ECMWF
open data) GRIB2 forecasts, normalise them to `core/weather.py`'s schema, and
write a loadable grid file — the same shape as tickets 0.3/0.4's
`ingest/fetch_gshhg.py` → `RealGeography` / `ingest/fetch_gebco.py` →
`RealGeography` pattern.

**Two constraints discovered during scoping, both confirmed live (not
assumed):**

1. **This sandbox has no Homebrew at all** (checked `/opt/homebrew`,
   `/usr/local/bin`, `PATH` — genuinely absent), so `cfgrib`/`eccodes` can't be
   installed or exercised here. Network egress to NOMADS and ECMWF works fine
   — confirmed by actually downloading real GRIB2 files and probing ECMWF's
   directory listings during scoping (see below). **Decision (user, asked
   directly): write the full pipeline now; unit-test only the pure
   normalisation logic against synthetic fixtures.** The live fetch +
   cfgrib variable-name mapping is flagged as unverified-by-me in code
   comments and CLAUDE.md — needs one real run on a machine with eccodes
   installed before being trusted in production.
2. **Decision (user): wire both NOMADS and ECMWF now** (not NOMADS-first,
   ECMWF-as-follow-up), and **scheduling is out of scope for this ticket**
   (script + CLI only — architecture is edge-first, so a live cron belongs
   with the edge device in Phase 1+, not this bench milestone).

**Verified live during scoping** (real HTTP requests, not guessed):
- NOMADS' `filter_gfs_0p25_1hr.pl` and `filter_gfswave.pl` grib-filter
  endpoints support server-side bbox subsetting — confirmed by downloading
  real, tiny (0.7–7KB), bbox-cropped GRIB2 files for `OPERATING_AREA_BBOX`.
  Both GFS wind (`UGRD`/`VGRD` @ 10m) and WW3 wave
  (`HTSGW`/`PERPW`/`DIRPW`) are available **hourly** through at least 48h —
  confirmed f000 through f048 all exist for both products on today's 00z
  cycle.
- ECMWF open data has **no server-side bbox subsetting**, but ships a
  `.index` sidecar (newline-delimited JSON, `{"param": ..., "_offset":
  ..., "_length": ...}`) next to every whole-globe GRIB2 file — confirmed
  by fetching real index files. This makes an HTTP Range request for a
  single parameter cheap (~700–900KB) even though the full file is huge
  (confirmed: the atmospheric `oper` file is 132MB for one step, bundling
  many unrelated fields). Confirmed real params in the `wave` stream:
  `swh`/`mwd`/`pp1d`/`mwp` map directly to `hs_m`/`wave_from_deg`/
  `period_peak_s`/`period_mean_s` — no separate wind-sea/swell split in
  this stream (fine — deferred, see Scope cuts below). Confirmed cadence:
  3-hourly (0,3,6,...,48h). Confirmed wind params in `oper`: `10u`/`10v`.
- **Direction convention (CLAUDE.md's flagged gotcha):** ECMWF's `mwd` is
  WMO-standard "mean wave direction", documented convention is direction
  **from** which waves are coming (same convention as wind) — usable
  as-is. NOAA's own GRIB2 parameter tables for WW3's `DIRPW`/`WVDIR`/
  `SWDIR` (checked live) give units ("degree true") but **no explicit
  from/to statement** — WMO No.702 governs it and every WW3 product
  description found describes it the same way as wind (from-convention),
  but this is **not empirically verified against a real decoded file**
  (blocked on constraint 1). Implemented as a single named, tested,
  currently-`False` toggle (`WW3_DIRECTION_IS_TO_CONVENTION`) rather than
  baked into the parsing — flip one constant once someone verifies it for
  real, not a code change.

## Scope cuts (explicit, not silent)

- **No wind-sea/swell partitioning.** WW3 offers `SWELL`/`WVHGT` partition
  fields; `WeatherSample.wind_sea_hs_m`/`swell_hs_m` exist as optional
  fields for this. Not wired up: `core/twin.py`'s `added_power_kw` only
  consumes combined `hs_m`/`period_peak_s` today, so the partition split
  has no consumer yet. Noted in the ingest docstrings as a follow-up, not
  dropped silently.
- **No scheduling/cron** (explicit user decision above).
- **Currents stay zero** — A4's existing v1 boundary condition, unchanged;
  `current_u_ms`/`current_v_ms` are written as zero arrays by both sources,
  matching `GriddedWeatherField`'s existing required (but so-far always
  synthetic-zero) fields.
- **Single region** — `OPERATING_AREA_BBOX` (western Med), matching
  tickets 0.3/0.4's scope boundary and ROADMAP's "Beyond Phase 2" region-pack
  note (global coverage is explicitly staged, later work).

## Amendments (review, before implementation)

1. **Land-masking + partially-NaN sampling.** Only wave fields (`hs_m`,
   `period_peak_s`, `period_mean_s`, wave direction) get land-masked to NaN
   at ingest — wind is left unmasked (a GFS/IFS over-land wind value is a
   real model output, not a hardcoded-calm artefact the way the demo's
   wave field was; masking it would introduce the exact problem below for
   no corresponding B2 win). But masking wave alone still isn't enough on
   its own: `core/gridding.py`'s `bilinear()` propagates *any* NaN corner
   to a fully-missing result, and anchorage endpoints (B6) are frequently
   close enough to shore that their 4-corner stencil includes a land
   corner most of the time — under the current behaviour, wave sampling
   at exactly the points anchorage routing depends on would read as
   universally missing. New `bilinear_masked()` in `core/gridding.py`
   renormalises weights over whichever corners aren't NaN, only
   propagating to missing when *all four* are land — used by
   `GriddedWeatherField.sample()` for the wave-derived fields specifically.
   `tests/test_weather.py`'s existing
   `test_gridded_land_adjacent_point_is_missing_not_calm` asserted the
   *old* (wrong, for this purpose) behaviour for a 3-valid/1-land stencil —
   it gets rewritten to assert a real blended value there, with a new,
   separate fixture (all four corners land) covering genuine full-missing.
2. **Real fixtures + guarded parsing tests.** The four small real GRIB2
   files downloaded live during scoping (confirmed valid, see Context) are
   committed under `tests/fixtures/grib/`: `gfs_wind_sample.grib2` (702B,
   NOMADS filter, UGRD/VGRD),`ww3_wave_sample.grib2` (7KB, NOMADS filter,
   HTSGW/PERPW/DIRPW), `ecmwf_wind_sample.grib2` (744KB, single Range-
   fetched `10u` message), `ecmwf_wave_sample.grib2` (843KB, single
   Range-fetched `swh` message). `tests/test_grib_parsing.py` (new) opens
   each with `xr.open_dataset(path, engine="cfgrib")` behind
   `pytest.importorskip("cfgrib")` — skipped (not failed) on any machine
   without eccodes, including this sandbox and current CI, but real
   verification the moment it runs somewhere that has it. Variable-name
   assertions are written permissively (checked against a short list of
   plausible cfgrib-mapped names) since I can't confirm cfgrib's exact
   GFS-table naming without eccodes here — noted inline as the one thing
   to tighten once someone runs this for real.
3. **npz provenance + atomic writes.** Both ingest scripts' npz schema
   gains three explicit fields: `cycle` (e.g. `"20260707_00z"`), `fetched`
   (ISO timestamp), `source` (descriptive string) — machine-readable
   staleness/provenance, not just a single free-text blob. Both scripts
   write via a `.tmp` sibling file (file-object form of
   `np.savez_compressed`, not the path form, to avoid numpy's automatic
   `.npz`-suffix handling fighting the temp name) then `os.replace()` into
   place — a crash mid-write can never leave a corrupt/partial npz at the
   path `GriddedWeatherField.from_npz` will next load.
4. **Cross-source consistency check.** New `ingest/verify_grib_consistency.py`:
   loads both providers' npz outputs, samples the same real points/times,
   and reports wind/wave differences — rough agreement is the closest
   thing to an ex-post correctness check available without eccodes-
   verified ground truth, and it doubles as an empirical check of the
   WW3 direction-convention assumption (`WW3_DIRECTION_IS_TO_CONVENTION`):
   agreement close to 0° supports it; disagreement close to 180° means
   it's wrong. Documented as step 1 of the "first real run" checklist in
   `CLAUDE.md`, alongside actually running both fetch scripts.

## Design

### 1. `ingest/grib_common.py` (new) — pure, unit-tested normalisation

Shared by both provider scripts so B2's "normalise at the ingest boundary
and test it" has exactly one implementation to test, not two:

- `normalise_longitude_deg(lon_deg) -> float`: wraps any longitude into
  [-180, 180) — GFS/WW3/IFS grids are natively 0–360.
- `direction_to_from_convention_deg(direction_deg, *, source_is_to_convention: bool) -> float`:
  identity when the source already reports "from" (the default for both
  our sources per the research above); flips (+180 mod 360) otherwise.
  This is the one place a from/to bug would live — fully unit tested with
  both branches, independent of any real GRIB parsing.
- `mask_land_as_missing(values: np.ndarray, lats, lons, geography) -> np.ndarray`:
  per-cell `geography.is_land_precise(lat, lon)` (reusing
  `core.geography.RealGeography.is_land_precise` — the GSHHG polygon
  ray-cast, already validated in ticket 0.3/0.4; ingest is a one-time cost,
  not the hot path, so the precise check is preferable to the raster here)
  → NaN. Applied to every field (wave *and* wind — a coastal cell's "wind"
  from an atmospheric model is equally not what a vessel offshore
  experiences), fixing B2's "most dangerous silent bug in the demo".
- `latest_available_cycle_utc(now_utc: datetime, delay_h: float = 5.0) -> tuple[str, str]`:
  rounds down to the latest `00/06/12/18z` cycle that should be fully
  published by `now_utc`. Takes `now_utc` as a parameter (not
  `datetime.utcnow()` internally) specifically so it's unit-testable
  without wall-clock dependence.

### 2. `ingest/fetch_grib_nomads.py` (new)

- CLI mirrors `fetch_gebco.py`/`fetch_gshhg.py`: `argparse`, `--out`,
  `--cycle-date`/`--cycle-hour` (default: `latest_available_cycle_utc`),
  `--horizon-h` (default: `core.optimiser.DEFAULT_HORIZON_H`, i.e. stays in
  sync with what the optimiser actually needs rather than a second magic
  number).
- For each hourly step 0..horizon_h: `filter_gfs_0p25_1hr.pl` (10u/10v) and
  `filter_gfswave.pl` (HTSGW/PERPW/DIRPW), both bbox-subsetted to
  `OPERATING_AREA_BBOX` server-side — confirmed real, tiny downloads (no
  GEBCO-style lazy-range-read trick needed, the filter service already
  does the cropping).
- Each step's tiny GRIB2 response parsed via `xr.open_dataset(path,
  engine="cfgrib")` (flagged unverified-by-me, see Context).
- Normalises via `ingest/grib_common.py`; writes
  `data/weather/nomads_western_med.npz` — schema below.

### 3. `ingest/fetch_grib_ecmwf.py` (new)

- Same CLI shape. Step cadence fixed at 3h (ECMWF open data's actual
  cadence, confirmed live) up to `--horizon-h`.
- For each step: fetch `{...}-{step}h-oper-fc.index` and
  `{...}-{step}h-wave-fc.index`, find `10u`/`10v`/`swh`/`mwd`/`pp1d`/`mwp`
  entries, issue one HTTP Range request per param via
  `urllib.request.Request(url, headers={"Range": f"bytes={offset}-{offset+length-1}"})`
  (stdlib only, matching `fetch_gshhg.py`'s `urlretrieve` — no new
  dependency for this part). Each param's single-message response is
  written to its own temp file and opened individually with cfgrib (avoids
  cfgrib's multi-hypercube errors when concatenating heterogeneous
  messages).
- Normalises (direction is a no-op here — `mwd` confirmed from-convention)
  and writes `data/weather/ecmwf_western_med.npz`.

### 4. `core/weather.py` — loader

- `GriddedWeatherField.from_npz(path: str | Path) -> GriddedWeatherField`
  classmethod, mirroring `RealGeography.__init__`'s `np.load(...)` +
  field-unpacking pattern exactly. No hardcoded default path constant in
  `core/` (unlike geography's static, committed data) — which source file
  to load is a caller decision, not a library default, since weather data
  is time-varying and per-run (see below).

### 5. `pyproject.toml`

- Add `cfgrib>=0.9` to the `ingest` extra, with a comment (matching
  CLAUDE.md's existing convention note) that it requires the system
  `eccodes` C library (`brew install eccodes` / `apt-get install
  libeccodes-dev`) — not installable via pip alone.

### 6. `.gitignore`

- Add `data/weather/*.npz`. Unlike geography's static committed data,
  fetched forecasts go stale within hours — these are regenerable,
  per-run artefacts, not repo content. (Confirmed geography npz *is*
  committed today via `git log`; deliberately not following that
  precedent here since the reason for committing doesn't apply.)

### 7. Tests

- `tests/test_grib_common.py` (new): `normalise_longitude_deg` (200°→-160°,
  edge cases at ±180/0/360), `direction_to_from_convention_deg` (both
  branches), `mask_land_as_missing` against a tiny stub `Geography`-like
  object (not real `RealGeography` — keep it fast and independent of the
  committed data files), `latest_available_cycle_utc` against a handful of
  injected `now_utc` values spanning cycle boundaries.
- `tests/test_weather.py` additions: `GriddedWeatherField.from_npz`
  round-trip — write a tiny synthetic npz via `np.savez_compressed`
  matching the schema to `tmp_path`, load it back, assert `.sample()`
  matches. No test performs real network I/O or real cfgrib parsing,
  matching the existing precedent (`fetch_gshhg.py`/`fetch_gebco.py` have
  no test coverage of their own network/parsing logic either — only their
  *outputs'* consumers are tested).

### 8. Docs

- `CLAUDE.md`: extend the existing GRIB-conventions gotcha with the
  concrete finding above (ECMWF `mwd` confirmed-from; WW3 `DIRPW` assumed-
  from-but-unverified; the `WW3_DIRECTION_IS_TO_CONVENTION` toggle; the
  eccodes-in-this-sandbox gap).
- `ROADMAP.md`: leave 0.5 as in-progress with a short note on the
  eccodes-verification gap, so it's tracked rather than silently assumed
  done once code lands.

## Verification

- `pytest`/`ruff` green — same bar as every prior ticket. This covers all
  normalisation logic, the npz round-trip, and cycle-selection math.
- What this **cannot** verify (explicitly, per the constraint above): that
  `cfgrib` actually opens a real downloaded GRIB2 file and produces the
  variable names/shapes the ingest scripts assume, and that the WW3
  direction-convention assumption is correct. Both scripts are structured
  so that's a single real run (`python3 -m ingest.fetch_grib_nomads` /
  `fetch_grib_ecmwf`) away from being confirmed once eccodes is available
  — flagged, not hidden.
