# Ticket B7 — Historical-fit readiness

**Review status: approved with 2 required amendments + 3 minor flags,
incorporated below (amendment/flag markers inline).** Judgment calls
flagged in the original plan are all signed off as written: `core/track.py`
placement in `core/` (the `WeatherSample` precedent), `margin_deg=0.25`,
the provisional per-source noise table values (keeping the "provisional,
pending real calibration" honesty in the docstring), and the 0→1→2→3→4
implementation order.

## Context

Phase 0's `fit/` (ticket 0.6) has a working, tested nonlinear-least-squares
twin-fit pipeline, proven only against synthetic telemetry generated from a
known-ground-truth `VesselSpec`. `core/` (routing/lattice/optimiser) is
feature-frozen until ticket 1.5 and must not be touched for anything
routing-shaped. B7 (enables B6, "get real historical data into the fit
pipeline") is scoped by ROADMAP.md as four parts:

1. **R1-lite for the data layer** — bbox as a parameter end-to-end in
   ingest/geography loading, track-driven (compute a covering bbox from a
   historical track, fetch ERA5/GSHHG/GEBCO for it). Routing/lattice
   untouched.
2. **ERA5 track annotator** — `(t, lat, lon)` rows → hs/period/wave-dir/wind
   appended from reanalysis via the CDS API (registered key, not anonymous
   like NOMADS).
3. **Import layer** — canonical telemetry schema + per-source adapters
   (monitoring CSV, e-logbook, noon reports), a low-frequency
   pre-aggregated-daily-row path, per-source STW-vs-SOG handling, and a
   per-source units/timezone audit.
4. **Passage-level holdout** in `fit/validate.py` — segment-level holdout
   flatters autocorrelated historical data; validate across vessels where
   possible.

**Validation ladder (July 2026):** bridge-simulator data (Plymouth
University contact) is a distinct middle rung between our synthetic loop
and real vessel data — its environment settings are exact ground truth (no
ERA5 needed, so it exercises the import layer without part 2), and its
independent vessel-dynamics model tests our fit against physics we didn't
write. The simulator-format adapter is a **named follow-up**, not built
here (format unknown until Plymouth's data-brief response arrives) — but
its `Protocol` slot is designed now so it's a drop-in later. **Large-vessel
data is fine**: the twin's forms are size-agnostic, but big ships never
reach hull speed, so large-vessel data validates everything except the
near-hull-speed steepening regime yachts actually use — not a blocker, just
a coverage gap this ticket's design should not assume away.

**Correcting the ROADMAP row's framing (found during research, stated
plainly rather than repeated as fact):** the row's phrase "per-source
STW-vs-SOG handling via the existing input-noise terms" overstates what
exists. The only literal input-noise term today is `fit/synthetic.py`'s
single `stw_noise_std_ms` — sensor noise on an already-STW-labelled
*synthetic* channel, not a per-source or SOG-aware mechanism. There is no
`sog_ms` field anywhere in `fit/telemetry.py`, and the one real STW/SOG
*distinction* in the codebase (`core.units.resolve_ground_speed_ms`,
principle A4: "fuel/motion/wear always take STW; only duration/ETA take
SOG+current") is a routing current-triangle concept, unrelated to
import-time measurement uncertainty. Part 3 has to design a per-source
noise/bias mechanism from scratch. Likewise, the row's parenthetical
"(enables B6; the fit pipeline itself is already passage-agnostic — `fit/`
never touches routing)" is true only about code *dependencies* (`fit/`
genuinely never imports `core.optimiser`/`core.legs`) — it says nothing
about the holdout split being statistically passage-aware. `fit/
validate.py`'s `holdout_split` is confirmed (read the actual source,
below) to be a flat `rng.permutation(len(segments))` with zero grouping —
Part 4 is a real design lift, not the small thing a skim of the row might
suggest.

**Feature-freeze guardrail, stated explicitly as a design constraint, not
just an absence:** nothing in this plan touches `core/lattice.py`,
`core/optimiser.py`, or `core/corridors.py`'s `PORTS`/`DEFAULT_ORIGIN`/
`DEFAULT_DESTINATION`. The regression suite covering routing must stay
green throughout. A trivial `git diff --stat` check confirming those files
are untouched is part of this ticket's own Verification section, the same
way ticket B1 stated "zero changes to `core/` source files" for its own
out-of-scope surface.

## Design

### 1. Implementation order, and a foundational shared type ahead of Part 3

**Proposed order: a small foundational step 0, then 1 → 2 → 3 → 4** —
matching the ROADMAP's own numbering for the four named parts, but with
one small step 0 inserted first to resolve a real chicken-and-egg problem:
Part 1 (bbox from a track) and Part 2 (ERA5 annotation of track rows) both
need *some* `(t, lat, lon)` row shape to operate on, before Part 3's full
canonical import schema (which itself depends on Parts 1/2's output being
one of its input formats, and separately needs the fitting pipeline's
segment schema to gain vessel/passage identity for Part 4) is anywhere
near designed.

Rather than block Parts 1/2 on Part 3, or have each invent a throwaway ad
hoc tuple, this plan carves out **one new, deliberately minimal module,
`core/track.py`**, as a step-0 foundation:

```python
@dataclass(frozen=True)
class TrackPoint:
    t_epoch_s: float
    lat_deg: float
    lon_deg: float
    hs_m: float | None = None
    period_peak_s: float | None = None
    period_mean_s: float | None = None   # amendment 2 -- mirrors WeatherSample's peak/mean pair
    wave_from_deg: float | None = None
    wind_u_ms: float | None = None
    wind_v_ms: float | None = None

def covering_bbox(points: list[TrackPoint], margin_deg: float = 0.25) -> tuple[float, float, float, float]: ...
def covering_time_range_s(points: list[TrackPoint]) -> tuple[float, float]: ...
```

**Minor flag — antimeridian guard:** `covering_bbox` raises `ValueError`
when the track's raw longitude spread (`max(lon) - min(lon)`) exceeds
180° — the cheap, reliable signal that the track likely crosses the
antimeridian (±180°) rather than genuinely spanning a huge bbox (no real
vessel passage bbox should be that wide under this simple lon0/lon1
convention). Documented as a known limitation, not fixed: proper
antimeridian handling needs unwrapped/circular longitude arithmetic
throughout the bbox/ingest chain, which is irrelevant for the Med/UK
operating area this ticket targets and genuinely new work for a real
future ocean-crossing ticket (`ROADMAP.md`'s R3 — Ocean passages — already
flags "proper spherical geometry... beyond ~500nm" as that ticket's job).
Tested with a fabricated track straddling ±180°.

**Placement judgment call (flagging for explicit sign-off):** this lives in
`core/` — not `ingest/` or `fit/` — because `core/` is the only package
both `ingest/` and `fit/` already depend on one-way; `ingest/` and `fit/`
never depend on each other (CLAUDE.md's package-layout convention).
`core.weather.WeatherSample` is the direct, already-shipped precedent for
this exact pattern: a plain data contract living in `core/`, produced by
ingest-adjacent code, consumed by `fit/`, zero I/O, numpy-only. `TrackPoint`/
`covering_bbox` are pure computation — consistent with `core/`'s
numpy+PyYAML-only, zero-I/O boundary — and this is not routing/lattice
code, so it does not touch anything under the feature freeze.
`margin_deg=0.25` (~15nm at mid-latitudes) is a genuine judgment call, not
derived from anything in the codebase — flagging for reviewer sanity-check.

**Layering consequence, stated explicitly:** Part 2's ERA5 annotator
(`ingest/`) and Part 3's import adapters (`fit/`) **never import each
other**. Their schema dependency is a **file-format contract, not a code
dependency** — Part 2 writes `TrackPoint`-shaped rows (env fields
populated) to a CSV using shared column-name constants defined in
`core/track.py`; Part 3 gets a dedicated adapter that reads that exact CSV
format back in. This is a small, deliberate duplication (a few lines of
CSV-reading logic in both `ingest/track_io.py` and `fit/`'s
annotated-track adapter) in exchange for preserving the one-way layering
invariant — boring over clever, matching this repo's stated bias.

**Order and rationale, concretely:**

0. `core/track.py` — `TrackPoint`, `covering_bbox`, `covering_time_range_s`,
   shared CSV column-name constants. Nothing depends on anything else;
   unblocks both 1 and 2 immediately.
1. **Part 1** (`ingest/`, bbox-parametric data layer) — depends only on
   step 0. Fixes the real `RealGeography._check_in_bounds` gap along the
   way (independent of bbox-as-CLI-parameter but naturally bundled since
   both are "make geography validation bbox-correct").
2. **Part 2** (`ingest/`, ERA5 annotator) — depends on step 0 (row shape)
   and, per the ROADMAP's own text, structurally depends on Part 1's
   bbox-from-track machinery (needs a covering bbox+time span before
   issuing one CDS grid request for the whole track). Doing Part 1 first
   makes this literal reuse, not a parallel reimplementation.
3. **Part 3** (`fit/`, import layer) — depends on step 0's CSV contract
   (to build the "annotated-track" adapter, the first concrete consumer of
   Parts 1+2's output) and needs nothing else from Parts 1/2 to *begin* —
   the other three adapters (monitoring CSV, e-logbook, noon report) can
   be built in parallel with, or even before, Parts 1/2, since they don't
   depend on bbox/ERA5 machinery at all. Ordered after 1/2 here mainly so
   the annotated-track adapter has a real upstream format to target, not
   because of a hard dependency.
4. **Part 4** (`fit/validate.py` + a small `fit/pipeline.py` wiring change,
   see §5) — depends on Part 3 introducing `vessel_id`/`passage_id` fields
   into the segment schema. Genuinely cannot be built standalone; must
   come last.

This ordering lets each part ship its own acceptance test independently
and incrementally rather than needing all four parts finished before
anything is testable.

### 2. Part 1 — R1-lite bbox-parametric data layer

**Files touched:**
- `core/geography.py` — fix `_check_in_bounds`.
- `ingest/fetch_gshhg.py`, `ingest/fetch_gebco.py` — `--bbox` CLI flag.
- `ingest/fetch_grib_ecmwf.py`, `ingest/fetch_grib_nomads.py` — `--bbox`
  flag plus geography-path passthrough args.
- New: `ingest/track_io.py` (`read_track_csv`/`write_track_csv`, operating
  on `core.track.TrackPoint`), `ingest/track_bbox.py` (thin CLI wrapper
  around `core.track.covering_bbox`).
- New: `data/historical/README.md` documenting the new data-directory
  convention.

**The real gap fix (`core/geography.py`), verified against the actual
source this pass:** `_check_in_bounds` is currently a *module-level*
function (`core/geography.py:188-194`) hard-importing `OPERATING_AREA_BBOX`,
called from `RealGeography.is_land` (line 337) and `.depth_m` (line 358).
`RealGeography.__init__` (lines 305-327) already computes `self._lat0,
self._dlat, self._lon0, self._dlon, self._nlat, self._nlon` straight from
whatever `bathymetry_path` was loaded — a `RealGeography` instance pointed
at a *different* bbox's `.npz` files would still validate against the
wrong (western-Med) bounds via the hard-imported constant. Fix: convert it
to a bound method, `RealGeography._check_in_bounds(self, lat_deg,
lon_deg)`, deriving bounds from those already-stored instance attributes
instead of the module constant. Update both call sites (`is_land`,
`depth_m`). Keep the `OutOfOperatingAreaError` class name (still accurate
in spirit) but generalize its docstring/message to not hardcode "western
Med"/`OPERATING_AREA_BBOX` wording. **This does not touch
`core/lattice.py`**, which independently and separately hard-imports
`OPERATING_AREA_BBOX` for lattice-clipping and stays exactly as-is per the
freeze.

**Ingest scripts, gotchas found in research to fix while parameterizing:**
1. `fetch_gshhg.py`'s `clip_to_bbox`/`fetch_gebco.py`'s `fetch_subset` are
   already bbox-parametric internally — only each `main()`/argparse
   hard-wires `OPERATING_AREA_BBOX`. Add `--bbox lon_min lat_min lon_max
   lat_max` (4 floats, default `None` → falls back to `OPERATING_AREA_BBOX`
   for backward-compatible default invocation).
2. `fetch_gshhg.py`'s output JSON currently bakes `list(OPERATING_AREA_BBOX)`
   into `bbox_lon_lat` (line ~134) regardless of what bbox was actually
   used — fix to record the resolved bbox actually passed.
3. **Naming-collision guard (required, not optional):** every script's
   `--out` defaults to a fixed `*_western_med` filename. When `--bbox` is
   explicitly passed and differs from `OPERATING_AREA_BBOX`, `--out` (and,
   for `fetch_gshhg.py`, `--bathymetry`) becomes **required** — the script
   errors out with a clear message rather than silently defaulting, so a
   second bbox run can never clobber the committed western-Med files.
4. **Ordering gotcha, documented not code-enforced:** GEBCO ingest must run
   before GSHHG ingest for any new bbox (GSHHG rasterizes onto whatever
   bathymetry `.npz` its `--bathymetry` arg points to) — call this out in
   both scripts' `--help` text and in `data/historical/README.md`.
5. `fetch_grib_ecmwf.py`/`fetch_grib_nomads.py` get a `--bbox` flag plus
   the same geography-path kwargs `api/config.py`'s `_geography_kwargs()`
   already knows how to pass to `RealGeography.__init__` — for a
   track-driven bbox, both fetchers must land-mask against the *matching
   newly-ingested* geography files, not the western-Med defaults (they
   currently construct `RealGeography()` with all defaults, unconditionally,
   purely for land-masking). Document the resulting ordering requirement:
   geography ingest (GEBCO → GSHHG) before weather ingest, for any new
   bbox.
6. **Large-bbox size guard (flag, don't solve):** `fetch_gebco.py` already
   avoids GEBCO's ~7GB full-grid size via lazy HTTP range reads
   (`fsspec`+`h5netcdf`), verified only at the western-Med corridor's size
   (~80s). Add a simple pre-fetch `WARNING` log estimating bbox area in
   deg² when it exceeds a small hardcoded threshold (e.g. 5x the
   western-Med bbox's ~11 deg²) — a warning, not a hard block; a genuinely
   large ocean-crossing track's full size/time budget is out of scope for
   this ticket.

**Data directory convention:** `data/historical/<track-id>/geography/*.npz`
and `data/historical/<track-id>/weather/*.npz` — closer in spirit to
`data/geography/`'s "static fact" treatment than `data/weather/`'s "stale
within hours" treatment (a historical passage's weather is a fixed
historical fact once fetched, not a live forecast), but under its own
subtree so it can never collide by name with the committed western-Med
files. `data/historical/README.md` documents the convention and the
GEBCO→GSHHG→weather ordering. No actual `<track-id>/` subdirectory is
created or committed in this ticket — there's no real track yet (B6's
job); only the convention + tooling.

**Explicitly out of scope here (restated from the R1-lite framing):**
`core/lattice.py`, `core/optimiser.py`, `core/corridors.py`'s
`PORTS`/`DEFAULT_ORIGIN`/`DEFAULT_DESTINATION`, `api/weather_field.py`'s
hard-coded bbox use, WPI port entries, arbitrary-endpoint routing, hand-
drawn-corridor demotion — all R1's job, a separate later ticket.

### 3. Part 2 — ERA5 track annotator

**Files touched:** new `ingest/fetch_era5_track.py`; `pyproject.toml`'s
`ingest` extras gains `cdsapi`.

**Structurally different from `fetch_grib_ecmwf.py` — confirmed via
research, zero reusable HTTP-fetch code:** ERA5/CDS is a registered-key,
async submit-then-poll-then-download API (`cdsapi.Client().retrieve(
dataset, request_dict)`), not `fetch_grib_ecmwf.py`'s anonymous synchronous
`.index`-sidecar Range-GET. **Operator setup required, flagged explicitly**
(an analogue to ticket 0.5's `eccodes` system-dependency precedent, but an
auth-setup caveat rather than a build-toolchain one): register a free CDS
account, accept the ERA5 dataset's license, generate an API key, write it
to `~/.cdsapirc`. This cannot be automated or verified in CI.

**Design — the strongest reuse win found in research, zero new
interpolation code:**
1. Load the track via `ingest/track_io.read_track_csv` → `list[TrackPoint]`.
2. `core.track.covering_bbox`/`covering_time_range_s` over the whole track
   — Part 1's bbox-from-track machinery, reused literally. **Minor flag —
   large-span warning:** if `covering_time_range_s`'s span exceeds ~30
   days, log a `WARNING` before issuing the request (noon-report-derived
   tracks can span a whole season) — flag, don't solve, matching Part 1's
   GEBCO large-bbox-size guard's style (a log line, not a hard block; the
   real cost/latency budget of a season-long single CDS request is
   unverified and out of scope here).
3. One CDS request for that bbox+time span: significant height of combined
   wind waves and swell (`swh`), mean wave direction (`mwd`), **peak wave
   period (`pp1d`) — amendment 2: the load-bearing period field, since
   everything downstream (`TrackPoint.period_peak_s`,
   `TelemetrySample.period_peak_s`, the added-resistance STAWAVE-class
   component) is peak, not mean** — plus mean wave period (`mwp`, carried
   alongside per `core.weather.WeatherSample`'s existing `period_peak_s`/
   `period_mean_s` pair, additive/unused by fitting physics beyond peak)
   and 10m u/v wind components. Mirrors `fetch_grib_ecmwf.py`'s existing
   `WAVE_PARAMS = {"hs_m": "swh", "dir_deg": "mwd", "period_peak_s":
   "pp1d", "period_mean_s": "mwp"}` naming exactly — the *request*
   variable names (CDS's ERA5 single-levels dataset uses long names like
   `peak_wave_period`/`mean_wave_period` in the request dict, decoding to
   short names close to `pp1d`/`mwp` in the response) still need live
   confirmation per the conventions caveat below; downloaded to a temp
   NetCDF.
4. Convert the CDS response into an in-memory `core.weather.
   GriddedWeatherField` — either via a temporary npz matching the existing
   `lat0/dlat/lon0/dlon/nlat/nlon` schema (reusing `from_npz`) or directly
   via `GriddedWeatherField.__init__`. This is the one genuinely new piece
   of grid-shaping code in Part 2.
5. Loop track rows, call the **existing, completely unchanged**
   `GriddedWeatherField.sample(lat_deg, lon_deg, t_h) -> WeatherSample`
   (`core/weather.py`, real bilinear-in-space + linear-in-time
   interpolation) once per row. `ingest/verify_grib_consistency.py` is the
   closest structural precedent for this loop-and-sample pattern (it
   currently diffs two fields for a fixed check-point list; the annotator
   does an append-to-row instead of a diff).
6. Write annotated `TrackPoint`s (hs/period/wave-dir/wind fields now
   populated) via `ingest/track_io.write_track_csv`.

**Conventions — explicit "assumption by analogy, not yet verified"
statement, following ticket 0.5's own precedent:** 0-360 longitude
handling and wave-direction from/to-convention should **not** be assumed
identical to the already-confirmed ECMWF-open-data values just because
both are loosely "ECMWF" — ERA5/CDS reanalysis is a different
product/pipeline. Ship the normalisation logic unit-tested against
synthetic fixtures now; flag the real live-conventions check as a pending
manual step, closed out later exactly like the eccodes-on-Ubuntu gap was
closed out in the 2026-07-13 Hetzner deploy. Concrete follow-up: once a
real CDS key exists, cross-check an ERA5-derived sample against a
NOMADS/ECMWF value at a real overlapping point/time, extending
`verify_grib_consistency.py`'s pattern rather than duplicating it.

**Wind has nowhere to land downstream yet, flagged not silently
dropped — verified this pass:** `core.weather.WeatherSample` (used
elsewhere, e.g. `fit/validate.py`'s `_predict_fuel_kg_per_h`) already
carries `wind_u_ms`/`wind_v_ms`, but `fit/telemetry.py`'s `TelemetrySample`
(confirmed by reading the actual dataclass: `t_h, stw_ms, heading_deg,
active_engines, fuel_kg_per_h, hs_m, period_peak_s, wave_from_deg`) has no
wind field at all. Part 3 (below) adds optional `wind_u_ms`/`wind_v_ms` to
`TelemetrySample` as an additive-only, forward-compatible passthrough —
recorded, not yet consumed by any fitting physics (no new physics, per the
scope constraint).

**`cdsapi` dependency placement:** joins the existing `ingest` extras group
(same precedent as `cfgrib` — the group's own stated boundary is
"one-time data preprocessing... never imported at runtime, only by
scripts under `ingest/`"), with a comment explaining the registered-key
setup requirement.

### 4. Part 3 — import layer

**Files touched:** new `fit/import_schema.py`, `fit/import_adapters.py`,
`fit/import_pipeline.py` (new this revision — amendment 1, see 4c-4e);
small additive changes to `fit/telemetry.py`, `fit/segments.py`,
`fit/calm_resistance.py`, `fit/added_resistance.py`.

**4a. `fit/import_schema.py` — the canonical row type.** A new,
deliberately-superset schema — **not** a reuse of `capture/telemetry.py`'s
`TelemetrySample` (confirmed by reading both: `capture/telemetry.py`'s is
PGN-provenance-shaped for live NMEA sensor-tier degradation; conflating
the two would wrongly couple historical-import source heterogeneity to
that model):

```python
@dataclass(frozen=True)
class CanonicalImportRow:
    t_epoch_s: float
    lat_deg: float
    lon_deg: float
    vessel_id: str
    passage_id: str
    source: str   # "monitoring_csv" | "e_logbook" | "noon_report" | "bridge_sim" | "annotated_track"
    stw_kn: float | None = None
    sog_kn: float | None = None
    heading_deg: float | None = None
    active_engines: int | None = None
    fuel_kg_per_h: float | None = None
    hs_m: float | None = None
    period_peak_s: float | None = None
    period_mean_s: float | None = None   # amendment 2, additive -- mirrors TrackPoint
    wave_from_deg: float | None = None
    wind_u_ms: float | None = None
    wind_v_ms: float | None = None
    is_low_frequency: bool = False
```

**Amendment 1 — `fuel_noise_std_fraction` (the per-row override) is
DROPPED from this dataclass, present in the original plan draft.**
Reviewer finding: it was speculative, had no working mechanism to reach
the residual weighting anyway (see 4d below — that mechanism is now
source-level, not row-level), and per-source granularity is all real B6
data will realistically support. Per-source noise now flows exclusively
through `SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION[source]` (below) — one
knob per source, not a per-row escape hatch.

Environmental field names intentionally mirror `core.track.TrackPoint`'s,
so the annotated-track adapter (below) is a near-mechanical field copy.

**Open design decision, flagged for explicit sign-off:**
`SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION: dict[str, float]` — a provisional
per-source noise table (proposing `monitoring_csv=0.03` matching `fit/`'s
existing global `DEFAULT_FUEL_NOISE_STD_FRACTION`, `e_logbook=0.08`,
`noon_report=0.15`, `bridge_sim=0.03`). Per CLAUDE.md's "no invented
numbers" principle, every entry's rationale must be stated in the module
docstring as "order-of-magnitude, provisional, pending real per-source
calibration once B6 has real data" — the same honesty `fit/priors.py`'s
`source` fields already model. This is genuinely new design work
(correcting the ROADMAP's overstated "via the existing input-noise terms"
framing), not a retrieval of an existing number.

**STW-vs-SOG handling, concrete default (resolved here as a judgment
call):** B7 does not fetch any current field (only ERA5 wave/wind data is
added; no current reanalysis is in scope) — reverse-correcting SOG toward
STW via `core.units`'s current-triangle math run backward is **not
implementable in this ticket** for lack of an input. Concrete default:
rows with `stw_kn is None and sog_kn is not None` are carried through with
`sog_kn` standing in for speed (a derived `speed_is_sog: bool` computed at
conversion time, not stored on `CanonicalImportRow` itself), with an
additional `SOG_FALLBACK_NOISE_MULTIPLIER = 2.0` (provisional, flagged)
applied on top of the per-source fraction. Reverse-triangle correction is
named explicitly in the module docstring as a **follow-up**, blocked on a
current-data source this ticket doesn't add.

**4b. Adapter Protocol (`fit/import_adapters.py`)** — matching this
repo's established `Protocol` convention (`capture/gateway.py`'s
`GatewayReader`, `ingest/grib_common.py`'s `_LandChecker`):

```python
class ImportAdapter(Protocol):
    def parse(self, path: Path, *, vessel_id: str, passage_id: str) -> list[CanonicalImportRow]: ...
```

Four concrete adapters implemented now: `MonitoringCsvAdapter`,
`ELogbookAdapter`, `NoonReportAdapter` (each against fabricated,
documented-assumed formats — no real files exist yet, that's B6's job) and
`AnnotatedTrackCsvAdapter` (reads the CSV format Part 2 writes, via
`core/track.py`'s shared column names — the first real, in-ticket
consumer of Parts 1+2's output). Each adapter owns its own unit/timezone
normalisation at parse time (kn vs m/s, L/h-with-a-documented-fuel-density
vs kg/h, local `zoneinfo` tz vs UTC epoch) — the "per-source units/
timezone audit" happens exactly once, inside `parse()`, matching B1's own
"normalise at the ingest boundary" precedent.

A **named follow-up slot**, not built: `BridgeSimulatorAdapter` — format
unknown until Plymouth's data-brief response. The `Protocol` is designed
now so it's a drop-in once the format is known; no invented fields.

**4c. AMENDMENT 1 — the high-frequency path was losing identity and
source metadata, breaking Parts 3 and 4 as originally drafted.** Original
draft: `canonical_rows_to_telemetry_samples` converted `CanonicalImportRow`
→ `TelemetrySample` (which has no vessel/passage/source fields at all —
by design, it's `fit/`'s minimal fitting-input shape), then the unchanged
`extract_steady_state_segments` produced segments with `vessel_id=None,
passage_id=None, fuel_noise_multiplier=1.0` **always** — regardless of
what source or vessel the data actually came from. Two real consequences
the reviewer caught: (a) `passage_holdout_split` (§5) can never work on
high-frequency imported data, since every segment's id is `None` — Part 4
as originally drafted only actually worked for the daily-aggregate path;
(b) `SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION` could never reach the
residual weighting on the high-frequency path — the original "unless
overridden per-source" phrase in 4d (old) had no real mechanism behind it.

**Fix — new `fit/import_pipeline.py`, orchestration runs per `(vessel_id,
passage_id, source)` batch, segments get stamped post-extraction:**

```python
def _source_fuel_noise_multiplier(source: str, *, uses_sog_fallback: bool = False) -> float:
    """SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION[source] expressed relative
    to fit's own global default (DEFAULT_FUEL_NOISE_STD_FRACTION=0.03) --
    what SteadyStateSegment.fuel_noise_multiplier actually multiplies
    against in fit_calm_resistance/fit_added_resistance's residual
    weighting. Always relative to the *default* fraction, not whatever a
    caller happens to pass fit_twin at call time -- a documented
    simplification. SOG_FALLBACK_NOISE_MULTIPLIER stacks on top when the
    batch's speed channel is SOG standing in for STW."""
    fraction = SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION.get(source, DEFAULT_FUEL_NOISE_STD_FRACTION)
    multiplier = fraction / DEFAULT_FUEL_NOISE_STD_FRACTION
    return multiplier * (SOG_FALLBACK_NOISE_MULTIPLIER if uses_sog_fallback else 1.0)

def stamp_segment_provenance(
    segments: list[SteadyStateSegment], *, vessel_id: str, passage_id: str,
    source: str, uses_sog_fallback: bool = False,
) -> list[SteadyStateSegment]:
    """Post-extraction identity/noise stamping via dataclasses.replace --
    extract_steady_state_segments itself stays completely untouched (it
    has no concept of provenance, by design, and shouldn't grow one)."""
    multiplier = _source_fuel_noise_multiplier(source, uses_sog_fallback=uses_sog_fallback)
    return [
        dataclasses.replace(seg, vessel_id=vessel_id, passage_id=passage_id, fuel_noise_multiplier=multiplier)
        for seg in segments
    ]

def rows_to_segments(rows: list[CanonicalImportRow], *, low_freq_noise_multiplier: float = 5.0) -> list[SteadyStateSegment]:
    """Top-level orchestration, the actual answer to 'how does a batch of
    imported rows become fit-ready segments with correct provenance.'
    Splits low- vs high-frequency rows; groups high-frequency rows by
    (vessel_id, passage_id, source) -- normally one group per adapter
    parse() call, since an adapter tags every row it produces with the
    same vessel_id/passage_id/source -- runs the unchanged
    canonical_rows_to_telemetry_samples + extract_steady_state_segments
    per group, then stamps; low-frequency rows go through
    daily_rows_to_segments per-row (already source-tagged per row)."""
    ...
```

**4d. Low-frequency (daily-aggregate) entry path**, also fixed by the same
mechanism instead of a hardcoded flat multiplier — reuses
`CanonicalImportRow` with `is_low_frequency=True` (daily mean stw/sog,
total fuel burned over hours run that day) rather than a separate
dataclass:

```python
def daily_rows_to_segments(rows: list[CanonicalImportRow], *, low_freq_noise_multiplier: float = 5.0) -> list[SteadyStateSegment]
```

— constructs one `SteadyStateSegment` per daily row directly, **bypassing
`extract_steady_state_segments` entirely** (per the ROADMAP's own
wording), with `n_samples=1`. Since each row already carries its own
`vessel_id`/`passage_id`/`source` (the daily path never round-trips
through the identity-stripping `TelemetrySample` shape), it sets
`vessel_id=row.vessel_id`, `passage_id=row.passage_id`, and
`fuel_noise_multiplier = _source_fuel_noise_multiplier(row.source,
uses_sog_fallback=...) * low_freq_noise_multiplier` — the same per-source
lookup used by the high-frequency path, with the low-frequency
multiplier stacked on top (not a separate, disconnected hardcoded value
as originally drafted).

**Second review pass, required fix:** the shipped first draft of this
function silently defaulted `mean_heading_deg`/`active_engines` to
`0.0`/`1` when a row lacked them (a plain noon report typically has
neither). Caught in review: `fit/added_resistance.py` consumes
`mean_heading_deg` for the relative wave angle, and `active_engines` is
central to the calm/SFOC fit's identifiability (ticket 0.6 finding #1)
— fabricating either would silently corrupt both fits, exactly what "no
invented numbers" forbids. Fixed by adding both to the same
required-fields `ValueError` check the sea-state fields already use (no
default, no fallback) — a daily row missing heading/engine-count now
needs a richer source or manual annotation, the same honest failure mode
as missing sea state. Same-pass **minor fix**: `rows_to_segments`'s
`uses_sog_fallback` check for a high-frequency batch changed from
`all(r.stw_kn is None for r in group_rows)` to `any(...)` — a mixed group
(some rows have real STW, some fall back to SOG) now gets the wider
SOG-fallback noise band too, the conservative choice for a batch with
partial STW dropout rather than letting the SOG-derived rows ride on
their STW-having neighbours' unwidened weighting.

**4e. Threading noise through to the existing fit math — the one place
this ticket touches core fitting math, additive-only, no new physics —
verified against the actual source this pass:** `SteadyStateSegment`
(`fit/segments.py:39-50`, currently `t_start_h, t_end_h, mean_stw_ms,
mean_heading_deg, active_engines, mean_fuel_kg_per_h, mean_hs_m,
mean_period_peak_s, mean_wave_from_deg, duration_h, n_samples`) gains three
new fields, all defaulted so existing ticket 0.6 tests/synthetic generator
are unaffected:

```python
vessel_id: str | None = None
passage_id: str | None = None
fuel_noise_multiplier: float = 1.0
```

`fit/calm_resistance.py:176` and `fit/added_resistance.py:110`'s residual-
weight line — confirmed identical in both files: `max(fuel_noise_floor_
kg_per_h, fuel_noise_std_fraction * seg.mean_fuel_kg_per_h)` — becomes
`max(fuel_noise_floor_kg_per_h, fuel_noise_std_fraction *
seg.mean_fuel_kg_per_h * seg.fuel_noise_multiplier)`. Every segment that
never passes through `rows_to_segments`/`stamp_segment_provenance` (i.e.
every existing ticket-0.6 caller, synthetic or otherwise) keeps
`fuel_noise_multiplier=1.0` by construction — this is the exact mechanism
the ROADMAP's overstated "existing input-noise terms" phrase should have
described, and it now genuinely reaches the residual weighting on *both*
the high- and low-frequency import paths, not just the low-frequency one.

**4f. Normal-frequency path — bridging into the unchanged pipeline
(unchanged from original draft, now correctly followed by stamping in
`rows_to_segments` rather than being the end of the story):** new
`canonical_rows_to_telemetry_samples(rows: list[CanonicalImportRow]) ->
list[TelemetrySample]` converts high-frequency `CanonicalImportRow`s
(`is_low_frequency=False`) into `fit/telemetry.py`'s existing
`TelemetrySample` shape (`t_h` relative to the first row, `stw_ms` from
`stw_kn` or the SOG-fallback, unchanged otherwise) so `extract_steady_
state_segments`/`fit_twin` run completely unmodified downstream — this
function's docstring now says explicitly that it deliberately drops
identity, and that `rows_to_segments` is what restores it afterward, so
nobody reads this function in isolation and assumes it's the whole
pipeline. `TelemetrySample` gains two new optional fields, additive-only:
`wind_u_ms: float | None = None`, `wind_v_ms: float | None = None` —
explicitly **not** wired into any `core.twin`/`fit_calm_resistance`/
`fit_added_resistance` physics in this ticket; recorded for a future
ticket to consume.

**4g. Code location:** following ticket 0.6's own precedent (`fit`'s own
scipy extras group despite `ingest` already having it — "different
concern," explanatory comment), `fit/import_schema.py`/
`import_adapters.py`/`import_pipeline.py` live inside `fit/`, preserving
its one-way dependency on `core/` only. `zoneinfo` is stdlib (py311+,
matches `requires-python>=3.11`) — no new dependency for timezone
handling; `csv` is stdlib, already used in `fit/cli.py`. No speculative
new parsing dependency (e.g. `openpyxl`) added until a specific known real
format structurally needs it.

**4h. Ticket 0.6 findings, restated as live design constraints for
Part 3, not background:** (1) real e-logbook/noon-report data is very
plausibly single-`active_engines`-config for long stretches —
`FitResult.engine_configs_present`/`.prior_shift_sigma` need to be
surfaced prominently in whatever report/CLI Part 3 adds (`fit/cli.py`
gains a summary print of these two fields when run against imported data,
not just the synthetic demo path). (2) real cruising logs may never reach
past the ~16-17kn steepening onset — not fixable by import-layer design,
but worth a diagnostic: a per-source/per-import-run log line reporting the
max observed `stw_kn`, so this coverage gap is visible without re-deriving
it from raw segments.

### 5. Part 4 — passage-level holdout

**Files touched:** `fit/validate.py` (new function); `fit/pipeline.py`
(small wiring change, found necessary during review — see below).

```python
def passage_holdout_split(
    segments: list[SteadyStateSegment],
    *,
    group_by: Literal["passage_id", "vessel_id"] = "passage_id",
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    rng: np.random.Generator | None = None,
) -> tuple[list[SteadyStateSegment], list[SteadyStateSegment]]:
```

Groups segments by the chosen id field, permutes over **group ids** (not
row indices), assigns whole groups atomically to train/holdout — no
group's segments ever split across both sides, which is what actually
prevents within-passage/within-vessel autocorrelation from leaking across
the split. **Data-quality gate:** raises `ValueError` if the chosen id
field is `None` on some segments and populated on others (mixed
provenance is a real data problem, not something to silently degrade
through).

**Minor flag — degenerate-group guards, both hard errors, not silent
fallbacks:** (1) raises `ValueError` if fewer than 2 distinct groups exist
for the chosen `group_by` — a holdout split needs at least one group on
each side. (2) raises `ValueError` if `holdout_fraction` rounds the
holdout side down to **zero** groups — deliberately using `int(n_groups *
holdout_fraction)` truncation (not `round()`, and *not* the existing flat
`holdout_split`'s `max(1, round(...))` forcing pattern), so the reviewer's
stated example is the literal regression test: 3 passages at
`holdout_fraction=0.25` → `int(3 * 0.25) = int(0.75) = 0` → raises,
rather than silently forcing a holdout of 1 group and misrepresenting
what fraction was actually achieved. The error message states the actual
group count and suggests either a larger `holdout_fraction` or more
`group_by` groups.

**The existing `holdout_split` (`fit/validate.py:35-47`, confirmed via
direct read — a flat `rng.permutation(len(segments))`, zero grouping)
stays, unchanged** — it remains correct for ticket 0.6's synthetic
single-passage acceptance test, where passage grouping is moot. Module
docstring updated to state plainly which to use when.

**Wiring gap found during review, not in the original research — a real
addition to the plan:** `fit/pipeline.py`'s `fit_twin` calls `holdout_split`
**internally and unconditionally**. Without a change here,
`passage_holdout_split` would exist as a library function nobody's main
orchestration entry point ever calls. First fix attempt: give `fit_twin`
a `holdout_group_by` parameter selecting `passage_holdout_split` instead.

**Second, deeper gap found during actual implementation (not caught by
the review's own reading, only by wiring it end-to-end and testing it):**
`fit_twin(samples: list[TelemetrySample], ...)` always derives its own
segments via `extract_steady_state_segments(samples, ...)` — and
`TelemetrySample` has no vessel/passage/source concept at all, by design
(§4a). So `fit_twin`'s own internally-extracted segments **always** have
`vessel_id=passage_id=None`, regardless of `holdout_group_by` — the
parameter would raise `passage_holdout_split`'s own "needs at least 2
distinct groups" error on every real call, since there'd only ever be one
group (`None`). A `TelemetrySample`-based entry point structurally cannot
carry the identity `passage_holdout_split` needs; identity only exists
once `fit/import_pipeline.py`'s `rows_to_segments` has stamped it onto
already-extracted `SteadyStateSegment`s.

**Actual fix:** `fit/pipeline.py`'s post-extraction logic (fit calm
resistance → fit added resistance → validate, previously `fit_twin`'s own
back half) is factored out into a new function, **`fit_twin_from_segments
(segments: list[SteadyStateSegment], base_spec, *, ..., holdout_group_by=
None, rng=None) -> FittedTwin`**. `fit_twin` becomes a thin wrapper:
extract segments from `samples` (unchanged), then call `fit_twin_from_
segments`. **Real B7 historical-import data calls `fit_twin_from_segments`
directly with `rows_to_segments`'s output — never `fit_twin`** — since
there's no raw `TelemetrySample` stream to re-extract from (`rows_to_
segments` already ran `extract_steady_state_segments` itself, per
`(vessel_id, passage_id, source)` batch, before stamping). `fit_twin`'s
own signature, default behaviour, and every existing call site
(`fit/cli.py`, `tests/test_fit_acceptance.py`) are completely unchanged —
this is purely additive. Verified end-to-end: adapter → `rows_to_segments`
→ `fit_twin_from_segments(holdout_group_by="passage_id")` →
`ValidationReport.n_groups_holdout` populated, group counts correct.

**`ValidationReport` gains two optional fields** (additive, backward
compatible): `n_groups_train: int | None = None`, `n_groups_holdout: int
| None = None` — populated only via the `passage_holdout_split` path,
`None` from the flat path. An error band built from 2 holdout passages
must not look identical to one built from 20; surfacing group counts
alongside the band makes that visible rather than implicit (CLAUDE.md's
"no invented numbers" principle).

## Tests

Per ticket 0.6's own acceptance-test spirit — synthetic/fabricated data
with a **known ground truth**, asserting predictive agreement (not raw
parameter recovery) with a stated tolerance, since no real historical data
exists yet (B6's job).

**Part 1:**
- `tests/test_core_geography_bbox.py` — construct a second `RealGeography`
  from a fabricated tiny synthetic `.npz` pair at a bbox disjoint from
  `OPERATING_AREA_BBOX`; assert a point inside the synthetic bbox but
  outside `OPERATING_AREA_BBOX` does **not** raise `OutOfOperatingAreaError`,
  and a point inside `OPERATING_AREA_BBOX` but outside the synthetic bbox
  **does** — the direct regression test for the found gap.
- `tests/test_ingest_bbox_param.py` — run `clip_to_bbox`/`fetch_subset`
  with a bbox differing from `OPERATING_AREA_BBOX`; assert the output's
  recorded bbox field matches what was actually passed, not the constant.
  Assert `--bbox` set without an explicit differing `--out` errors clearly
  rather than silently writing to the western-Med default path.
- `tests/test_core_track.py` — a small fabricated track (~20 points, known
  hand-computed bbox), asserting `covering_bbox` both contains every point
  plus margin and doesn't wildly overshoot. **Minor flag 3:** a fabricated
  track straddling ±180° longitude asserts `covering_bbox` raises
  `ValueError` rather than returning a silently-wrong bbox.
- `tests/test_ingest_track_io.py` — CSV round-trip fidelity for
  `TrackPoint`s with and without env fields populated (incl.
  `period_mean_s`, amendment 2).

**Part 2 (all CI-run, zero real network/credentials — following ticket
0.5's own precedent):**
- `tests/test_ingest_era5_track_normalise.py` — pure normalisation-logic
  unit tests (longitude convention, direction convention) against
  synthetic fixture arrays with known expected output.
- `tests/test_ingest_era5_track_pipeline.py` — monkeypatch
  `cdsapi.Client.retrieve` to return a small fabricated NetCDF/array with a
  **known analytic** hs/period/wave-dir/wind field; run the full annotator
  pipeline (network call mocked only) against a ~10-point fabricated track;
  assert annotated values match the known analytic field within
  interpolation tolerance — exercises "reuse `GriddedWeatherField.sample`,
  zero new interpolation code" end-to-end. Asserts both `period_peak_s`
  (from `pp1d`) and `period_mean_s` (from `mwp`) land correctly
  (amendment 2). **Minor flag 2:** a fabricated track spanning >30 days
  asserts a `WARNING` is logged before the (mocked) CDS request fires.
- Explicit statement: no automated test calls the real CDS API or requires
  `~/.cdsapirc`. **Manual step, documented not automated:** once a real
  CDS account+key exists, run the annotator against a real short track
  overlapping a NOMADS/ECMWF archive window and cross-check one sample,
  extending `verify_grib_consistency.py`'s pattern — flagged pending, same
  as ticket 0.5's live-GRIB verification gap.

**Part 3:**
- `tests/test_fit_import_schema.py` — `CanonicalImportRow` defaults;
  `SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION` has one entry per implemented
  adapter source; SOG-fallback multiplier applies only when `stw_kn is
  None`.
- `tests/test_fit_import_adapters.py` — one fabricated sample file per
  implemented adapter (monitoring CSV, e-logbook, noon report, annotated-
  track); assert correct unit/timezone normalisation into UTC-epoch/kn/
  kg/h.
- `tests/test_fit_import_pipeline.py` — **amendment 1's regression tests,
  the load-bearing new coverage this revision adds:**
  - ~10 fabricated daily-aggregate rows with known ground-truth mean
    stw/fuel; `daily_rows_to_segments` → unmodified `fit_calm_resistance`;
    assert it runs end-to-end, and that a low-frequency source's stamped
    `fuel_noise_multiplier` (source fraction × `low_freq_noise_multiplier`)
    vs `1.0` on the same underlying noisy data produces a measurably
    smaller `prior_shift_sigma` (evidence the multiplier is load-bearing).
  - **The amendment-1 regression test named explicitly in review:**
    fabricate a high-frequency e-logbook-shaped import (`source=
    "e_logbook"`, `SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION["e_logbook"]=
    0.08`, materially noisier than the `0.03` default) through
    `ELogbookAdapter.parse()` → `rows_to_segments`; assert (a) the
    resulting segments carry non-`None` `vessel_id`/`passage_id` matching
    what was passed to `parse()`, so `passage_holdout_split` can actually
    consume them; (b) `fuel_noise_multiplier ≈ 0.08 / 0.03` on every
    resulting segment, not `1.0`; (c) fitting through these segments with
    `fit_calm_resistance` produces a measurably different (wider,
    appropriately less confident) result than the same underlying data
    stamped as `monitoring_csv` (`0.03`, multiplier `1.0`) — proving the
    per-source weighting actually reaches the residual, not just that the
    field is populated.
  - `stamp_segment_provenance`/`_source_fuel_noise_multiplier` unit tests:
    known source → known multiplier; `uses_sog_fallback=True` stacks
    `SOG_FALLBACK_NOISE_MULTIPLIER` correctly; an unknown source falls back
    to `DEFAULT_FUEL_NOISE_STD_FRACTION` (multiplier `1.0`).
  - **Review-fix regression tests (second pass):** `daily_rows_to_segments`
    raises `ValueError` naming `heading_deg`/`active_engines` specifically
    when either is missing, mirroring the existing sea-state-missing
    assertions — the direct regression test for the required fix (no more
    silent `0.0`/`1` defaults). A mixed-STW/SOG high-frequency batch
    (`rows_to_segments`) gets the same widened `SOG_FALLBACK_NOISE_
    MULTIPLIER`-inclusive multiplier as an all-SOG batch, strictly greater
    than an all-STW batch's — the regression test for the `any()`-not-
    `all()` minor fix.
- `tests/test_fit_import_acceptance.py` — complementary to the amendment-1
  regression test above (that one proves provenance/noise flows through;
  this one proves predictive fidelity): generate synthetic high-frequency
  telemetry via the **unchanged** `fit.synthetic.
  generate_synthetic_telemetry` against a known ground-truth `VesselSpec`;
  serialize it through a fabricated monitoring-CSV file (realistic
  per-source noise/unit conversions) and back through `MonitoringCsvAdapter
  .parse()` + `canonical_rows_to_telemetry_samples`; run the unmodified
  `fit_twin`; assert predictive agreement within a stated tolerance (0.6's
  `MAX_RELATIVE_PREDICTION_ERROR`, or an explicitly-justified slightly
  wider one accounting for round-trip conversion noise) — proves the
  import layer doesn't silently corrupt data en route to the unchanged
  fitting math.

**Part 4:**
- `tests/test_fit_validate_passage_holdout.py` — fabricate segments across
  6 synthetic passages, each with its own fixed fuel-residual bias
  (simulating real autocorrelation, e.g. a day's flowmeter miscalibration
  or fouling state); run both `holdout_split` and `passage_holdout_split`
  across many rng seeds on the same data. **Empirical finding, found
  while building this test, not theorised in advance (correcting the
  original plan's assumption):** with the real, low-degrees-of-freedom
  parametric calm-resistance fit, the two methods' *mean* RMSE across
  seeds isn't reliably ordered either way — the physical curve is too
  smooth/global to locally "memorise" one passage's bias just because
  same-passage points leak into training. What *is* large, consistent,
  and mechanistically sound: the flat split's RMSE is nearly identical
  every run regardless of which passage lands in holdout (an individual-
  level sample always blends a bit of every passage), while the
  passage-grouped split's RMSE swings widely run to run (std ~3x larger
  in the actual test run — 34 vs 12 kg/h on the fabricated data). Test
  asserts `std(grouped) > 1.5 * std(flat)` and
  `max(grouped) > max(flat)` — the real content of "flatters": a
  flat-split error band looks falsely stable regardless of which unseen
  passage a deployment actually faces; a passage-grouped band honestly
  exposes that the answer depends heavily on which passage you get.
- A mechanics-only companion test: no passage ever splits across
  train/holdout; reproducible given a fixed seed; `ValueError` on mixed
  `None`/populated id data.
- A `group_by="vessel_id"` variant on a fabricated 2-vessel set.
- **Minor flag 1, tested explicitly:** `ValueError` on fewer than 2 groups
  (a single-passage fabricated set); `ValueError` on the reviewer's stated
  degenerate case — 3 passages, `holdout_fraction=0.25` (`int(3*0.25)=0`)
  — while confirming the same 3-passage set at a fraction that truncates
  to ≥1 (e.g. `0.4` → `int(3*0.4)=1`) succeeds normally.
- `tests/test_fit_pipeline.py` (new file): full adapter → `rows_to_
  segments` → `fit_twin_from_segments(..., holdout_group_by="passage_id")`
  end-to-end, confirming `ValidationReport.n_groups_holdout` is populated
  and group counts are correct — the regression guard for both the
  wiring-gap fix *and* the deeper segments-vs-samples gap found during
  implementation (see §5's "second, deeper gap" note). Also confirms
  `fit_twin(samples, base_spec)` (existing call shape, no new args) is
  byte-for-byte unaffected, and that `fit_twin(..., holdout_group_by=
  "passage_id")` — the *old*, samples-based entry point — correctly
  raises (its own internally-extracted segments have no identity to
  group by), documenting why real B7 callers must use `fit_twin_from_
  segments` instead.

## Scope cuts (explicit)

- No changes to `core/lattice.py`, `core/optimiser.py`, or
  `core/corridors.py`'s `PORTS`/`DEFAULT_ORIGIN`/`DEFAULT_DESTINATION`, or
  any routing/lattice/search code — feature freeze until ticket 1.5.
- No changes to `api/weather_field.py`'s hard-coded bbox use.
- No full R1 region-pack refactor — arbitrary endpoints, WPI port entries,
  hand-drawn-corridor demotion are R1's job, a separate later ticket.
- No actual Plymouth bridge-simulator adapter implementation — only the
  `ImportAdapter` Protocol + a named follow-up slot; format unknown until
  their data-brief response.
- No real credentialed CDS API calls inside the automated test suite — all
  CI tests are synthetic/mocked; live verification is a documented manual
  step needing a one-time CDS account+key setup.
- No reverse current-triangle STW correction for SOG-only sources — no
  current data source is added in this ticket; SOG-only rows get a wider
  noise multiplier instead, flagged as a real follow-up once a current
  field exists.
- No wiring of the new `wind_u_ms`/`wind_v_ms` fields into any
  `core.twin`/fitting physics — additive passthrough fields only, no new
  physics.
- No change to `fit/`'s core regression math beyond accepting the new
  per-segment `fuel_noise_multiplier` in the existing residual-weight
  expression.
- No committing/checking in any real historical vessel data — that's
  ticket B6's job; this ticket ships tooling and a data-directory
  convention, not data.
- No change to `capture/telemetry.py`/`capture/` at all — B7's import
  layer is a deliberately separate schema from B1/B5's live-capture
  schema, not a reuse.

## Docs

- `ROADMAP.md`: mark B7's four parts done as each lands (or as one
  combined note once all four are green — reviewer's call), explicitly
  noting the corrected STW/SOG framing and the deferred simulator adapter
  as a named, not-yet-built follow-up.
- `CLAUDE.md`: new gotcha/convention entries for (1) `RealGeography.
  _check_in_bounds` now deriving bounds from the loaded grid, not the
  module-level `OPERATING_AREA_BBOX` constant — and that `core/lattice.py`
  deliberately still uses the constant directly, unrelated and untouched;
  (2) `core/track.py` as a `WeatherSample`-precedent shared cross-package
  data contract, and the file-format-not-code-dependency resolution
  between `ingest/`'s Part 2 and `fit/`'s Part 3; (3) the CDS/ERA5
  registered-key setup requirement (account, license acceptance,
  `~/.cdsapirc`); (4) `fit/import_pipeline.py`'s per-`(vessel_id,
  passage_id, source)`-batch orchestration + post-extraction
  `stamp_segment_provenance` (`dataclasses.replace`) as the actual answer
  to the ROADMAP's overstated "existing input-noise terms" wording —
  amendment 1, found in plan review: the original draft's
  `canonical_rows_to_telemetry_samples` → unchanged
  `extract_steady_state_segments` path silently produced
  `vessel_id=None, passage_id=None, fuel_noise_multiplier=1.0` on every
  high-frequency segment regardless of source, breaking both Part 3's
  per-source weighting and Part 4's holdout grouping on exactly the data
  path most real B6 imports will use; (5) Part 4's empirical
  autocorrelation finding once the test above confirms it, and the
  `fit_twin`/`holdout_group_by` wiring gap found during plan review
  (mirroring how ticket 0.6's own empirical findings got recorded).
- New `data/historical/README.md` — the bbox/geography/weather-ingest
  ordering convention for a track-driven region.
- New short operator runbook (`docs/historical-import-runbook.md`,
  mirroring `deploy/README.md`'s precedent): compute bbox from a track,
  ingest geography then weather for it, register a CDS key, run the
  annotator, run an adapter, run `fit_twin` against the result.
- `pyproject.toml`: `ingest` extras gains `cdsapi`, with a comment
  distinguishing its registered-key auth caveat from `cfgrib`'s system-
  library caveat.

## Verification

- `pytest -m ""` (full suite) green, `ruff check .` clean — same bar as
  every prior ticket; the existing 0.6 acceptance test must stay green
  unmodified throughout (new `SteadyStateSegment`/`TelemetrySample` fields
  are additive-only with defaults, and `fit_twin`'s new parameter defaults
  to today's exact behaviour, so this is a real, checkable regression
  guard, not just an assumption).
- **CDS account+key setup is already done on the implementation machine**
  (`~/.cdsapirc` exists, valid key, ERA5 single-levels licence accepted —
  confirmed by direct instruction, not assumed). Automated tests still
  must not touch the real CDS API (mocked per Part 2's test design), but
  the live verification step — one real annotator run against a real
  short track/point overlapping a NOMADS/ECMWF archive window,
  cross-checking longitude/direction convention — **is run for real at
  the end of this ticket**, not deferred, with the result recorded below
  the same way ticket 0.5 recorded its first real run in CLAUDE.md.
  Once Plymouth's bridge-simulator data-brief response arrives (separate,
  later, not blocking this ticket's completion), implement
  `BridgeSimulatorAdapter` as a fast-follow against the now-known format.
- `git diff --stat` confirming no diff touches `core/lattice.py`,
  `core/optimiser.py`, or `core/corridors.py` — the feature-freeze
  guardrail, checked mechanically before calling this ticket done.

### Live ERA5 verification result (2026-07-14, real run against real CDS)

`~/.cdsapirc` was already set up (registered key, ERA5 single-levels
licence accepted). Ran `python3 -m ingest.fetch_era5_track` for real
against a one-point track: `(42.5°N, 8.5°E)` at `2026-07-08T00:00Z` —
chosen to exactly match a real, already-committed ECMWF *open-data*
forecast sample this repo has from ticket 0.5's own first real run
(`data/weather/ecmwf_western_med.npz`, cycle `20260707_12z`, +12h),
giving a genuine cross-source overlap point to check convention against,
per the plan's design.

**Two real defects found and fixed, neither guessable in advance —
exactly the kind of gap this manual step exists to catch (ticket 0.5's
own precedent):**

1. **A combined wind+wave CDS request returns a zip archive, not a raw
   NetCDF** — containing two *separate* per-stream files
   (`data_stream-oper_stepType-instant.nc` for wind,
   `data_stream-wave_stepType-instant.nc` for wave), regardless of the
   `target` filename given to `cdsapi`. `_open_era5_response` now
   detects and extracts this (falls back to opening a raw file directly,
   in case a different request shape ever returns one).
2. **ERA5's wave stream (WAM model) is natively on a coarser grid than
   the 0.25° wind/atmospheric stream** — confirmed live: for this
   request, wind came back on a 3×3 (0.25°) grid, wave on a single point
   (its native cell, ~0.5°, was wider than the whole tiny request bbox).
   The same real GFS-wind/WW3-wave mismatch `fetch_grib_nomads.py`
   already resamples via `.interp()` — `_merge_era5_streams`/
   `_resample_onto_grid` do the same, with a broadcast fallback for an
   axis with only one source point (linear interpolation is
   mathematically undefined there — confirmed live, `.interp()` alone
   returned all-NaN; verified `.interp(method="nearest")` doesn't fix it
   either, since scipy's nearest-neighbour interpolator still respects
   the source's bounding box by default).

**Real result after both fixes, full CLI run
(`python3 -m ingest.fetch_era5_track`), no errors:**

| Field | ECMWF open-data forecast (0.25°, +12h) | ERA5 reanalysis (real run) |
|---|---|---|
| `hs_m` | 0.591 | 0.338 |
| `period_peak_s` | 3.21 | 2.77 |
| `period_mean_s` | 2.81 | 2.75 |
| `wave_from_deg` | 214.8° | 226.1° |
| `wind_u_ms`, `wind_v_ms` | 3.76, 6.81 | 3.71, 3.39 |

**Conventions confirmed, not just assumed:** wave-direction disagreement
is **11.4°** — in the same range as ticket 0.5's own confirmed cross-
source agreement (16° mean, WW3 vs ECMWF open-data) and nowhere near the
~180° a from/to-convention mismatch would produce. `ERA5_MWD_IS_TO_
CONVENTION` stays `False`, now confirmed live rather than assumed by
analogy. Longitude convention (0-360 native) held correctly (no bbox/
sign errors). Hs/wind magnitude differences are consistent with ordinary
forecast-vs-reanalysis disagreement for a genuinely light, variable sea
state (same order of magnitude, no unit/scaling bug — e.g. no factor-of-
10, no radians-vs-degrees mix-up). `CDS_VARIABLES`' guessed short names
(`swh`/`mwd`/`pp1d`/`mwp`/`u10`/`v10`) and the `valid_time` coordinate
fallback in `_time_coord_name` were both confirmed exactly correct
against the real response, with zero further changes needed.

### Critical files for implementation
- `core/geography.py` (bound-method fix)
- `core/track.py` (new)
- `ingest/fetch_gshhg.py`, `ingest/fetch_gebco.py`, `ingest/fetch_grib_ecmwf.py`, `ingest/fetch_grib_nomads.py` (bbox params)
- `ingest/track_io.py`, `ingest/track_bbox.py` (new)
- `ingest/fetch_era5_track.py` (new)
- `fit/import_schema.py`, `fit/import_adapters.py`, `fit/import_pipeline.py` (new)
- `fit/telemetry.py`, `fit/segments.py` (additive fields)
- `fit/calm_resistance.py`, `fit/added_resistance.py` (noise-multiplier wiring)
- `fit/validate.py` (`passage_holdout_split`)
- `fit/pipeline.py` (`fit_twin_from_segments`, the segments-first entry point real B7 imports use)
- `fit/pipeline.py` (`holdout_group_by` wiring)
- `pyproject.toml` (`cdsapi` in `ingest` extras)
