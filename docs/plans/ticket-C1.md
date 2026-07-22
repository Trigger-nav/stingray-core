# Ticket C1 — Real surface currents in the weather field

**Review status: approved with 2 required amendments + 2 minor flags,
incorporated below (amendment/flag markers inline).** All six judgment
calls from the original plan are signed off as recommended. Implementation
follows this revised version, in the plan's own order (core fixes + full
`pytest -m ""` first, in isolation, before any ingest work).

## Context

ROADMAP R3 names "currents become a real field (Gulf Stream > weather on
this corridor — A4 graduates from zeros)" as part of the ocean-scale
work. This ticket pulls that one line forward, standalone, at *regional*
scale — motivated directly by the UK South-West pack (ticket R1): CTV/
passage routing in the Channel is genuinely tide-dominated (multi-knot
streams in constricted water, routinely a bigger speed-over-ground factor
than wind/wave at the vessel's typical cruising speeds), and R1 shipped
that pack with the current field still hardwired to zero. This is **not**
R3 — no spherical geometry, no ocean-crossing currents, no Gulf Stream;
purely "make the existing regional current plumbing carry real numbers."

**The plumbing already exists, confirmed by direct code reading, not
assumed:**
- `core/weather.py`'s `WeatherSample` has carried `current_u_ms`/
  `current_v_ms` since ticket B2 — currently always `0.0` from
  `SyntheticWeatherField` and always zero-filled arrays from every real
  ingest fetcher.
- `GriddedWeatherField`'s constructor/`.from_npz()`/`.sample()` already
  store, load, and bilinearly interpolate `current_u_ms`/`current_v_ms`
  exactly like wind — the npz schema already has both fields (currently
  always `np.zeros(...)`, `ingest/fetch_grib_nomads.py`/
  `fetch_grib_ecmwf.py` both write them that way today).
- `core/units.py`'s `resolve_ground_speed_ms`/`resolve_course_to_steer_deg`
  already implement the full current-triangle correction, and
  `core/legs.py`'s `leg_navigation` already calls both, unconditionally,
  every leg, today — with a current of exactly `(0, 0)` this is a no-op
  (returns `track_bearing_deg`/`stw_ms` unchanged), which is exactly why
  it's never been exercised against a nonzero value in production.
- A4's own documented discipline (`core/optimiser.py` module docstring)
  is already the right one for currents: "fuel/motion/wear always take
  STW; only duration/ETA take" ground speed — `leg_navigation` already
  respects this (current only ever touches `ground_speed_ms`/`cts_deg`/
  `duration_h`, never the twin's `fuel_rate`/`motion`/`wear` calls, which
  all still take `stw_ms`).

**So this is genuinely an ingest-layer ticket** — but two real,
previously-undiscoverable-because-never-exercised defects turned up while
verifying the "ideally nothing changes in core/" premise directly against
the code (§1 below), not theorised in advance. Both are small, additive,
and freeze-compatible (verified: with current held at exactly zero, both
changes are no-ops) — but they are real, required fixes, not optional
polish, found by asking "what happens when `current_u_ms`/`current_v_ms`
stop being exactly zero" and tracing it through.

## Design

### 1. Two required core/ fixes, found by tracing "what happens when current is real"

**1a. `resolve_ground_speed_ms`/`resolve_course_to_steer_deg` raise
`ValueError` when cross-current exceeds STW — currently unreachable,
about to become reachable, and nothing catches it.** Read directly
(`core/units.py`): both functions compute `remainder = stw_ms**2 -
cross_track_water**2` and raise `ValueError("current exceeds vessel
speed through water; cannot hold track")` if `remainder < 0` — physically
correct (a vessel genuinely cannot hold a track when the cross-track
current component alone exceeds its speed through water), but with
current always `(0, 0)` today, `cross_track_water` is always `0` and
`remainder` is always `stw_ms**2 >= 0`, so this branch has **never fired
in production**. Real UK Channel tidal streams routinely reach 2-4+ kn in
constricted water; the candidate speed grid's floor
(`MIN_CANDIDATE_SPEED_KN = 6.0`) is well within range of a real stream
strong enough to trip this. Traced the call chain directly: `core/legs.py`'s
`leg_navigation` calls both with **no try/except**, so the exception
propagates uncaught through `evaluate_leg` → every one of `_lattice_search`/
`_dp_route`/`_best_feasible_duration_h` → `optimise()` itself. `api/jobs.py`'s
`_classify_error` happens to map any `ValueError` to `invalid_request`
(422) rather than crashing as a 500 — so the *symptom* is "soft" at the
API layer, but the *behaviour* is wrong: one physically-infeasible
(candidate speed, leg, current) combination fails the **entire plan
request**, when the correct behaviour is to prune just that combination
and let the search try other speeds/routes/times, exactly like
`navigable`/`depth_ok`/`slam_event`/`overload` already do.

**Required fix**: `LegResult` (`core/legs.py`) gains a new field,
`current_exceeds_stw: bool`, following the exact existing one-flag-per-
hard-constraint-reason pattern. `leg_navigation`/`evaluate_leg` catch the
`ValueError` from both current-triangle calls and set this flag instead
of letting it propagate (leg becomes infeasible at this speed, not the
whole request). All three existing hard-constraint check call sites gain
the new condition, mechanically:
- `core/isochrone.py:68` (`_best_feasible_duration_h`):
  `if not (leg.navigable and leg.depth_ok) or leg.slam_event or leg.overload:`
  → add `or leg.current_exceeds_stw`.
- `core/optimiser.py:512` (`_lattice_search`): same addition.
- `core/optimiser.py:703/705` (`_dp_route`, two separate `if`s today):
  same addition to the first.

**Verified freeze-compatible**: with current held at `(0, 0)`,
`cross_track_water` is always `0`, `remainder` is always `>= 0`, the
`ValueError` path is never entered, `current_exceeds_stw` is always
`False` — byte-identical behaviour to today for the Med (and for any
pack with currents disabled, §3). This is the one genuinely required
`core/legs.py`/`core/optimiser.py`/`core/isochrone.py` change; everything
else in this ticket is ingest/api.

**1b. `GriddedWeatherField.sample()` uses unmasked `bilinear` for
current, not `bilinear_masked` — real ocean-current products are
land-masked far more aggressively near shore than wind is, and this
matters specifically where routing endpoints live.** Read directly:
`core/gridding.py`'s plain `bilinear` propagates **any single NaN
corner** to a fully-missing result; `bilinear_masked` renormalises over
whichever corners aren't NaN, only returning NaN when all four are — the
exact mechanism `hs_m`/periods/wave-direction already use, specifically
because "a query point just offshore — an anchorage approach, say — sits
in a stencil with at least one land (NaN) corner far more often than
not" (`bilinear_masked`'s own docstring). Wind is deliberately *not*
masked this way because an over-land atmospheric value is a real model
output, not an artefact — but an ocean **current** model has no
equivalent over-land value at all (CMEMS's shelf-seas products are
ocean-only grids; a land cell is genuinely no-data, not "calm"). Today
this has never mattered because current is a uniform `0.0` field with no
NaNs anywhere. Once real CMEMS data is loaded, any lattice point near a
harbour/headland/anchorage — i.e. routinely near a declared origin/
destination, exactly where `DEPTH_EXEMPT_RADIUS_NM`'s pilotage exemption
already anticipates nearshore water being awkward — risks a single
masked stencil corner silently NaN-ing the whole sampled current,
propagating to `duration_h = inf` for that leg with no diagnostic
explaining why (confirmed via the same reasoning as `leg_navigation`'s
`ground_speed_ms > 0 else float("inf")` fallback: `NaN > 0` is `False` in
Python, so a NaN current silently degrades to "infeasible leg," not a
crash — a second, quieter failure mode alongside 1a's crash).

**Required fix**: switch `current_u`/`current_v` sampling in
`GriddedWeatherField.sample()` from `bilinear` to `bilinear_masked`,
matching wave's precedent exactly. **Verified freeze-compatible**: a
uniform-zero current array has no NaNs anywhere, so `bilinear_masked`
and `bilinear` are numerically identical on it — zero behaviour change
for the Med/any zero-current pack.

**Judgment call, flagged, not fixed here: the A* heuristic's
zero-current admissibility assumption becomes false once currents are
real, and this ticket does not fix it.** `core/optimiser.py`'s
`_heuristic_cost_eur` docstring already states its lower bound is
admissible "given zero current in the current weather model (A4's v1
boundary condition)" — a following current can make the true
achievable ground speed exceed the heuristic's max-STW assumption,
which can make the heuristic *overestimate* remaining cost, which
breaks A*'s admissibility guarantee. Concretely: `_lattice_search` tries
the heuristic-guided run first; if it **finds** the destination (just
not necessarily via the cheapest path), there is no fallback to the
exhaustive `use_heuristic=False` run — that fallback only fires when the
heuristic-guided run finds *nothing*. So a strongly favourable tide
could, in principle, cause the search to return a real, feasible,
*non-optimal* candidate silently, with no error or diagnostic. This is a
soft optimality-quality risk, not a crash and not an infeasibility —
qualitatively different from 1a/1b, which are both hard bugs. **Recommend
deferring**: real UK tidal streams (a few knots) are a smaller fraction
of typical cruising speed (10-16kn) than would be needed to make this a
large practical effect, and fixing it properly means querying the
current field *during heuristic evaluation* (which the heuristic
deliberately avoids today, for speed/simplicity) — a real design change
this ticket's ingest-layer scope shouldn't absorb. Documented as a named
follow-up (CLAUDE.md gotcha + a code comment on `_heuristic_cost_eur`
itself), not silently ignored. Flagged explicitly for sign-off: accept
as a known limitation for v1, or pull the heuristic fix into this
ticket's scope.

### 2. Real CMEMS interface, verified live during this planning pass (not guessed)

Following the WPI/CDS precedent exactly — real endpoints/IDs found via
live lookups, not invented:

**Package**: `copernicusmarine` (PyPI, v2.4.1 at time of writing,
Python 3.10-3.14, pure Python — xarray/zarr/requests-based, **no system
C-library dependency** the way `cfgrib`/eccodes is. Confirmed via PyPI's
own listing.) Joins the `ingest` extras group; unlike `cfgrib`, needs no
new "system dependency" caveat comment, just the registered-account one
below.

**Auth, same shape as the CDS precedent**: a free Copernicus Marine
Service account (register at data.marine.copernicus.eu), then either
`copernicusmarine.login()` (stores credentials at `$HOME/.copernicusmarine`,
analogous to `~/.cdsapirc`) or environment variables
`COPERNICUSMARINE_SERVICE_USERNAME`/`COPERNICUSMARINE_SERVICE_PASSWORD`
(confirmed as a first-class supported auth path, precedence: explicit
params > env vars > credentials file). **Recommend env vars** for this
repo's deployment shape — matches `Settings.from_env()`'s own
env-var-first convention better than a dotfile, and slots cleanly into
`deploy/.env.example`/systemd `EnvironmentFile` the same way every other
credential in this repo already does. **Operator action needed once the
plan is approved, exactly like the CDS precedent**: register the free
account; this cannot be automated or tested in CI.

**Python API** (`copernicusmarine.subset(...)`, confirmed via the real
toolbox docs): `dataset_id`, `variables`, `minimum_longitude`/
`maximum_longitude`/`minimum_latitude`/`maximum_latitude`,
`start_datetime`/`end_datetime`, `output_filename`/`output_directory` —
downloads a NetCDF subset. Structurally a closer cousin to
`ingest/fetch_era5_track.py`'s `cdsapi` pattern (a Python client library
doing the subsetting server-side) than to NOMADS/ECMWF's raw HTTP Range
requests.

**Real dataset IDs, confirmed live against CMEMS's own metadata catalogue
API** (`data-be-prd.marine.copernicus.eu/api/metadata/<product_id>`, a
real, working ISO-19115/CSW endpoint — found the same way the WPI
endpoint was found last ticket: inspecting what the real service actually
serves rather than trusting third-party summaries alone):
- **UK pack (tidal signal required)**: product
  `NWSHELF_ANALYSISFORECAST_PHY_004_013` ("Atlantic — European North
  West Shelf — Ocean Physics Analysis and Forecast," a coupled
  hydrodynamic-wave model **with tides**, 1.5km resolution, "updated
  daily"). Dataset id **`cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i`** —
  hourly, 2D (surface), instantaneous. (A `PT15M-i` quarter-hourly
  surface-current variant also exists — rejected as unnecessary
  precision for leg-costed routing at this codebase's existing hourly-ish
  weather cadence, the same "sub-hour would be fake precision" reasoning
  `api/weather_field.py`'s `quantize_hour` already applies.)
- **Med pack**: product `MEDSEA_ANALYSISFORECAST_PHY_006_013`. Dataset id
  **`cmems_mod_med_phy-cur_anfc_4.2km-2D_PT1H-m`** — hourly, 2D surface,
  4.2km. (A real de-tided variant also exists in this product family,
  confirming the standard non-detided product does carry a tidal
  component where physically present — consistent with wanting *total*
  current, not a decomposed piece, for ship routing.)
- **Variables**: CMEMS's standard NEMO-model short names, **expected**
  `uo`/`vo` (confirmed as the real CF long names "eastward/northward sea
  water velocity" for this product family via the live product page;
  the exact short variable names should be confirmed with a real
  `copernicusmarine.describe(dataset_id=...)` call at implementation
  time, before writing the parser — flagged explicitly rather than
  hard-asserted, matching this ticket's own "verify, don't guess"
  instruction applied to itself).
- Both product pages state "updated daily" (NWS: "12:00 UTC") — a
  **structurally different freshness model from NOMADS/ECMWF**, not a
  smaller version of the same one (§3).

**MINOR FLAG 2 (review) — resolved, checked live during this revision.**
A live catalogue fetch during review showed `NWSHELF_ANALYSISFORECAST_PHY_004_013`'s
cached temporal extent ending 2026-05-17, raising a real "is this product
still live, or has it been superseded" question. Re-checked directly
against the dataset's own real-time STAC record (not the cached CSW
summary), fetched fresh during this revision (2026-07-20):
`http://stac.marine.copernicus.eu/metadata/NWSHELF_ANALYSISFORECAST_PHY_004_013/cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i_202511/dataset.stac.json`
reports `start_datetime: 2023-09-29T01:00:00Z`, `end_datetime:
2026-07-27T00:00:00Z` (exactly ~7 days ahead of the fetch date — the
signature of a live, rolling analysis-forecast product whose "end" is
the current forecast horizon, not a fixed retirement point), and,
decisively, an explicit `"admp_retired_date": null` field. **Conclusion:
not superseded** — the reviewer's 2026-05-17 observation was a stale
cached snapshot of a rolling field, not a real retirement signal; no
successor dataset id search was needed. Real variable names also
confirmed directly from this same live STAC record (not inferred): `uo`
("Eastward Current Velocity in the Water Column", CF standard name
`eastward_sea_water_velocity`, unit `m s-1`) and `vo` (northward
equivalent) — both present, exactly as expected, closing out §2's "flag
for confirmation via `describe()`" note for the NWS dataset specifically.
Re-verify the same way for the Med dataset (`cmems_mod_med_phy-cur_anfc_4.2km-2D_PT1H-m`)
if/when it's ever enabled (§5) — not re-checked here since Med currents
are out of scope for this ticket.

**Variable names, closed out during implementation via the real
`copernicusmarine.describe()` Python call (not just the raw STAC JSON
above) — exactly the check this plan itself called for**: `describe()`
is unauthenticated (no credentials needed for metadata-only queries,
confirmed live) and returns `uo` (`eastward_sea_water_velocity`, `m
s-1`) and `vo` (`northward_sea_water_velocity`, `m s-1`) for
`cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i` — matching the STAC record
exactly, now confirmed through the actual tool `fetch_currents_cmems.py`
uses, not just its metadata sidecar.

### 3. Fetcher, provenance schema, and the merge design

**New `ingest/fetch_currents_cmems.py`**, following the established
`fetch_*.py` shape (`--bbox`, clobber guard, `write_npz_atomic`):

```
python3 -m ingest.fetch_currents_cmems --bbox LON_MIN LAT_MIN LON_MAX LAT_MAX \
  --dataset-id cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i \
  --horizon-h 48 \
  --out data/region_packs/<pack>/currents_<pack>.npz
```

Writes a **currents-only** intermediate npz — deliberately not merged
into the wind/wave npz by this script itself, keeping the established
one-script-one-source convention every other `ingest/fetch_*.py` script
already follows (GEBCO, GSHHG, ERA5, NOMADS, ECMWF, WPI — each owns
exactly one product).

**Judgment call: the intermediate npz's time axis is absolute UTC
timestamps, not cycle-relative hours — deliberately different from every
other ingest npz's schema, for a real reason.** Every existing npz's
`hours` array is relative to *that file's own* cycle start (confirmed:
`fetch_grib_nomads.py` writes `cycle=f"{cycle_date}_{cycle_hour}z"`,
`fetched=now_utc.isoformat()`, and `hours` as `range(0, horizon_h+1,
STEP_H)` relative to that cycle). CMEMS's daily-update cadence means its
own "cycle start" essentially never coincides with GFS/ECMWF's 6-/12-
hourly cycle starts — merging two cycle-relative axes without first
converting to a shared absolute frame is a real ambiguity, not a detail
to wave away. `fetch_currents_cmems.py`'s own npz therefore stores
`times` (absolute UTC epoch floats) instead of `hours`, sidestepping the
ambiguity entirely; nothing downstream ever sees this schema directly
except the merge step below.

**New `ingest/merge_currents.py`** — reads the *target* wind/wave npz's
own `cycle` field (format `"{cycle_date}_{cycle_hour}z"`, confirmed
parseable back to an absolute datetime) to establish that file's
absolute-time reference, converts the currents-only npz's `times` into
that file's own relative-hours convention, resamples current onto the
wind/wave npz's exact lat/lon grid (`xarray`'s `.interp()`, the same
WW3-onto-GFS / B7 ERA5-wave-onto-wind resampling pattern already used
twice in this codebase — no new interpolation approach), and rewrites
the wind/wave npz **in place** (all existing fields copied through
unchanged, `current_u_ms`/`current_v_ms` replaced, via
`write_npz_atomic` — the file `GriddedWeatherField.from_npz` actually
loads never has a moment where it's partially written).

```
python3 -m ingest.merge_currents \
  --weather-npz data/region_packs/<pack>/weather_<pack>.npz \
  --currents-npz data/region_packs/<pack>/currents_<pack>.npz
```

**REQUIRED AMENDMENT 2 (review) — explicit time-coverage policy, so a
currents dataset that doesn't fully span the weather npz's hours range
fails loudly instead of silently NaN-ing legs.** The original draft's
`.interp()`-based resampling has an unstated failure mode identical in
shape to §1b's spatial one, but on the time axis: `xarray`'s `.interp()`
returns NaN for any query point outside the source data's covered range
(e.g. the weather npz's horizon genuinely outrunning the currents
product's own forecast horizon, or the two fetches landing on slightly
different available windows for entirely mundane reasons — different
publication cadences, a currents fetch that ran a few hours later than
the wind/wave one). An unhandled out-of-range NaN current propagates
exactly like the spatial case: `duration_h = inf` for the affected legs,
silently, with nothing explaining why a passage suddenly looks
infeasible late in its own planning horizon. Fixed with an explicit
policy, checked once per merge (not per query point) against the
weather npz's full hours range vs. the currents-only npz's own time
coverage:
- **Gap of ≤6h at either end**: hold-nearest (clamp the query time to
  the nearest edge of the currents data's real coverage, rather than
  extrapolating or interpolating past it) — logged at `WARNING` (naming
  the pack, the gap size, which end), and recorded as a provenance note
  (`current_source` gains a `" (held-nearest Nh at start/end)"` suffix,
  or an equivalent explicit marker — visible through `/v1/health`, not
  silently absorbed). 6h chosen as a real, small fraction of a
  semidiurnal tidal cycle (~6.2h) — short enough that holding the
  nearest real sample is a defensible approximation of "the tide hasn't
  moved far," not an invented tolerance; **flagged as a concrete number
  worth a second look at implementation time** once real coverage-gap
  sizes between the two sources are observed in practice, rather than
  assumed correct now.
- **Gap beyond 6h**: hard error — `merge_currents.py` refuses to write a
  merged npz at all rather than silently shipping a partially-covered
  one; the pack's currents step is then exactly what amendment 1's
  failure isolation catches (wind/wave still publishes, currents stays
  at its previous value, `WARNING` logged).

Fixture tests for both branches: a fabricated currents-only npz whose
coverage falls 3h short of the weather npz's horizon (hold-nearest,
assert the clamped value matches the nearest real sample, assert the
`WARNING` log and provenance marker); one falling 10h short (assert
`merge_currents.py` raises, assert no output file is written/the
existing one is untouched).

**`fetch_all_packs.py` orchestration**: a pack's `RegionPack` gains a new
optional field, `currents_dataset_id: str | None = None` (additive,
default `None` — see §5 for why this, not a bare boolean). When set,
`fetch_all_packs.py`'s per-pack loop runs `fetch_currents_cmems` (using
the pack's own bbox + this dataset id) then `merge_currents`, *after*
the existing wind/wave fetch for that pack — ordering matters (merge
needs the wind/wave npz to already exist as its target). When unset
(every pack today, including uk_sw until this ticket enables it),
behaviour is **exactly** today's: no currents step runs, the wind/wave
fetcher's own zero-filled arrays are never touched.

**REQUIRED AMENDMENT 1 (review) — a currents-step failure must not take
down that pack's wind/wave fetch or hot-swap.** The original draft
implicitly chained currents onto the wind/wave fetch as one per-pack
unit; a real CMEMS outage, quota rejection, or the amendment-2 hard-error
case below would then fail the *whole* pack's cron run, leaving even the
wind/wave data stale — a strictly worse outcome than "this pack's
currents just don't update this cycle," since wind/wave is the load-bearing
data every pack needs regardless of whether currents are modelled at all.
Fixed: `fetch_all_packs.py`'s per-pack loop wraps the
`fetch_currents_cmems`+`merge_currents` pair in its own failure boundary,
independent of the wind/wave fetch's own (pre-existing, unchanged)
success/failure handling — a currents-step exception is caught, logged at
`WARNING` (naming the pack, which step failed — fetch or merge — and the
error), and the loop moves on to hot-swap-publishing whatever wind/wave
data *did* fetch successfully, exactly as it would if currents were
disabled for that pack entirely. The npz's `current_u_ms`/`current_v_ms`
stay at whatever they were before this cycle (a previous successful
merge's real values, or zeros if this is the first cycle / currents were
just enabled) — never silently reset, never blocking the pack that
actually needs to stay fresh. Test: a mocked currents-fetch failure
(patching `fetch_currents_cmems`/`merge_currents` to raise) still
completes the wind/wave path for that pack — the wind/wave npz is
written and valid, and a second, independent pack in the same run is
unaffected.

**Publication-schedule/self-heal, flagged as a real open question, not
assumed**: NOMADS/ECMWF's `latest_available_cycle_utc`/
`fetch_with_cycle_fallback` mechanism is built around a *discrete cycle
grid* (00/06/12/18z, or 00/12z) with a 404-on-not-yet-published failure
mode — confirmed live during the 2026-07-13 Hetzner deploy findings.
CMEMS's "updated daily" analysis-forecast products don't obviously have
the same discrete-cycle shape; `copernicusmarine.subset()`'s real
behaviour when a requested time range isn't published yet (a hard error,
a partial/NaN-filled response, or something else) **was not verified
during this planning pass** and shouldn't be assumed — verify with a
real live call before designing any retry/fallback logic, at
implementation time. Do not force-fit `fetch_with_cycle_fallback`'s
exact shape onto this without first confirming CMEMS actually fails the
same way.

### 4. Convention normalisation

- **Direction convention: CMEMS current u/v components are physical
  eastward/northward velocity vectors (a "to" convention by
  construction — u/v components don't have a from/to ambiguity the way a
  single direction-in-degrees field does), unlike WW3's wave direction,
  which is confirmed **from**-convention (ticket 0.5). Worth a explicit,
  named callout precisely because it's the kind of silent-sign-error trap
  ticket 0.5's gotcha already warns about generically — `core/units.py`'s
  `resolve_ground_speed_ms`/`resolve_course_to_steer_deg` already expect
  u/v as "the vector the water is moving *toward*" (confirmed by reading
  the current-triangle math itself: `along_current`/`cross_current` are
  added directly as a drift vector, not negated) — so CMEMS's raw u/v
  needs **no sign flip**, but this must be verified against a real
  fetched sample (§6) rather than assumed correct by inspection alone,
  and stated explicitly in `ingest/fetch_currents_cmems.py`'s docstring
  so a future reader doesn't have to re-derive it.
- **Units**: CMEMS ships m/s natively (confirmed: CF standard variable is
  "sea water velocity" in m/s) — no unit conversion needed, unlike WPI's
  knots-in-CSV.
- **Longitude convention**: verify at implementation time whether the
  subset API's `minimum_longitude`/`maximum_longitude` params expect
  -180..180 or 0..360 — `ingest/grib_common.py`'s
  `normalise_longitude_deg` is reusable if a wrap is needed, but don't
  assume one is.
- **Land masking**: `fetch_currents_cmems.py` reuses
  `ingest/grib_common.py`'s existing `mask_land_as_missing` (this
  codebase's own GSHHG-derived coastline) rather than trusting CMEMS's
  native ocean-grid mask alone — **judgment call, flagged**: this is
  arguably redundant (CMEMS's shelf-seas model is already ocean-only) but
  keeps every source in this repo's weather pipeline consistent with
  *this repo's own* coastline definition rather than each source's own,
  slightly different one, matching wave's existing treatment exactly.
  Recommend keeping it for consistency; flagging since it's a real
  double-masking, not a necessity.

### 5. Per-pack enablement and `/v1/health` provenance

**`RegionPack` gains `currents_dataset_id: str | None = None`** (§3) —
additive, defaulting to "not modelled," matching every other R1-era
`RegionPack` field's own additive-default pattern.

**uk_sw: ON** — `data/region_packs/uk_sw.yaml` sets
`currents_dataset_id: cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i`. This
is the ticket's whole point.

**med: OFF for this ticket, a deliberate, justified recommendation, not
an oversight — flagged for sign-off.** Real Med surface currents exist
(a few cm/s to ~0.3-0.5kn in the open western Med generally, stronger
locally in boundary currents/straits) but are small relative to this
fleet's typical 10-16kn cruising speeds — nowhere near the
current-exceeds-STW regime UK tidal streams create, so the
routing-relevant benefit is genuinely marginal. Three reasons to defer
rather than enable: (1) it adds a recurring CMEMS fetch/quota cost for a
pack where it won't materially change plan shape; (2) enabling it is a
**one-line config change** (`currents_dataset_id` in `med.yaml`) once
this ticket's mechanism is proven live on uk_sw — nothing about this
ticket's design blocks flipping it on later; (3) it keeps this ticket's
"zero behaviour change on the Med" story maximally simple: nothing about
what the Med pack actually serves changes, prospectively or otherwise,
during this ticket — the safest reading of the freeze discipline R1
established. **The committed Med regression-test npz
(`data/weather/ecmwf_western_med.npz`) is never regenerated or
re-fetched by this ticket regardless of this decision** — that file's
content is what `pytest -m ""` actually reads, and this ticket does not
touch it either way.

**Provenance surfaced through `/v1/health`, matching how weather
cycle/fetched already is — a pack with no current source gets an
explicit "not modelled" signal, not a silent zero.** `GriddedWeatherField`
gains three new optional constructor params / npz round-trip fields,
separate from the existing `cycle`/`fetched`/`source` triple (which
describes the wind/wave source): `current_cycle: str | None = None`,
`current_fetched: str | None = None`, `current_source: str | None =
None` — all `None` for a zero-current pack (uk_sw's own committed test
fixtures, med, or any future pack that never enables currents), populated
by `merge_currents.py` from the currents-only npz's own provenance when
it runs. `api/schemas.py`'s `HealthOut` gains matching optional fields
(`currents_source`/`currents_cycle`/`currents_fetched`, all `None` when
absent); `api/routes.py`'s `health()` reads them off the active pack's
`GriddedWeatherField` the same way it already reads `weather_source`/
`weather_cycle`/`weather_fetched`. A pack with currents disabled reports
`currents_source: null` explicitly — visibly "none, not modelled," not
indistinguishable from "modelled but happens to read zero."

**MINOR FLAG (review) — `GriddedWeatherField.from_npz` must tolerate an
npz written before this ticket, which has none of the three new keys at
all.** Confirmed by reading `.from_npz` directly: it currently does
`str(grid["cycle"]) if "cycle" in grid else None` for the *existing*
three provenance fields — the same defensive `in grid` check must cover
`current_cycle`/`current_fetched`/`current_source` too, not just assume
every npz was written by a post-this-ticket fetcher. Real, not
theoretical: a cloud instance running a newer `core/`/`api/` against an
npz cron last wrote with the *old* `fetch_grib_nomads.py`/
`fetch_grib_ecmwf.py` (before a deploy picks up this ticket's changes to
those or before the next cron cycle re-fetches), or a vessel-role
instance mid-upgrade pulling an npz from a cloud instance one version
behind it, both hit this. Test: load a fixture npz built with exactly
today's (pre-ticket) key set — assert `from_npz` succeeds and the three
new fields read back as `None`, not a `KeyError`.

### 6. UK acceptance run (the ticket's proof)

**Real, live, end-to-end — matching the R1/B7 precedent, not a mocked
stand-in.** Two parts:

1. **A real fetch+merge+`optimise()` run** on the uk_sw pack (same
   Plymouth/Falmouth-class endpoints R1 already verified navigable),
   producing a plan where the tide **measurably moves the answer** — the
   concrete test: run the same passage at two departure times roughly
   half a tidal cycle apart (~6h) and confirm a real difference in
   duration/fuel/track attributable to fair vs. foul tide, not noise.
   Record the actual numbers in this plan file once run, the same way
   R1's own UK acceptance section did.
2. **Cross-check against a real, independently published reference** —
   not this codebase's own output validating itself. Candidate real
   sources to pin down at implementation time (not fixed here, matching
   R1's own "exact acceptance endpoint pair... left to implementation
   time" precedent): UKHO EasyTide's public tidal predictions (some
   stations publish tidal stream/current data alongside height), a
   charted tidal diamond near the Plymouth/Falmouth approach, or a
   published Admiralty tidal stream atlas figure for a known position
   and time. Whichever is used, cite the real source and record the
   comparison numerically in this plan file, matching B7's "Live ERA5
   verification result" section precedent exactly (source, sampled
   value, reference value, agreement).

**Live UK acceptance run result (2026-07-22, real CMEMS fetch, real
`optimise()` calls) — the ticket's own "run step 6" resumption, credentials
finally registered.**

*Part 1 — real fetch+merge+`optimise()`, tide measurably moves the
answer.* `ingest/fetch_grib_nomads.py` re-fetched wind/wave for the uk_sw
bbox (fresh GFS+WW3 cycle `20260721_06z`). `ingest/fetch_currents_cmems.py`
fetched real `cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i` data — **one
real gap found along the way**: the CLI's `main()` hardcodes `start=now`,
which left the currents fetch starting ~11h after the weather npz's own
cycle-06z hour-0 (NOMADS's own publication delay means the weather cycle
is always somewhat older than "now" at fetch time) —
`ingest/merge_currents.py`'s 6h hold-nearest tolerance correctly refused
to merge across that gap (`CurrentsCoverageError`, working exactly as
designed, §3's amendment). Not a code bug to fix in this ticket (the
existing hard-error is the right behaviour) — worked around for this run
by calling `fetch_currents_cmems.fetch_currents()` directly with `start`
anchored to the weather npz's own cycle start instead of the CLI's
implicit "now" (a real operational consideration for whatever eventually
schedules this fetch relative to the wind/wave one — worth a follow-up
note in `ingest/fetch_all_packs.py`'s own docstring, not fixed here, out
of scope for a resumed one-off ticket-closing run). Real sampled currents
at the pack's own origin (50.35°N, 4.15°W) over the first 24h: u ranges
-0.29 to +0.13 m/s, sign-flipping roughly every ~6h — a real, visible
semidiurnal signal, not noise.

`optimise()` (Plymouth→Falmouth, `speeds_kn=(10.0,)`, `pace=50`,
`comfort=50`) at three departure times:

| `departure_t0_h` | `duration_h` | `fuel_kg` | `distance_nm` | `score_eur` |
|---|---|---|---|---|
| 0 | 4.2720 | 565.68 | 42.269 | 3774.60 |
| 6 | 4.6785 | 619.29 | 48.088 | 4133.10 |
| 12 | 4.2609 | 563.91 | 42.269 | 3764.52 |

t0=6h is **9.5% slower and 9.5% more fuel** than t0=0h — a real,
substantial foul-tide penalty, not noise (also visibly a *different*
route: `distance_nm` 48.09 vs 42.27nm, i.e. the search chose a different
track under the t0=6h current field, not just a slower transit of the
same one). t0=12h returns to within 0.3% of t0=0h's numbers — exactly
what a ~12.4h M2 semidiurnal period predicts (t0=12h is one full cycle
minus ~0.4h, close enough to the same tidal phase). This is the ticket's
central proof: current genuinely, measurably moves the plan.

*Part 2 — external cross-check: real, but qualitative, not the
precise numeric match originally hoped for.* Checked, in order: **UKHO
EasyTide** (free, live) publishes tide *height* predictions only for this
area, no stream/current data. **Admiralty Tidal Stream Atlas NP221**
(Plymouth Harbour and Approaches) and **NP250** (English Channel) —
the real, authoritative source for exactly this — are paywalled paper/PDF
products, no free numeric access found. **POLPRED** (National
Oceanography Centre) requires an account/portal, not a simple free
numeric lookup. **The CMEMS product's own official Quality Information
Document** (`CMEMS-NWS-QUID-004-013`, v3.0, 28 May 2025, fetched and read
directly, 77 pages) was checked for a validation-grade number — it
explicitly states current-speed validation is only performed against 3
HF radar sites (German Bight, Skagerrak, Norwegian Trench/Vestlandet),
none in the Channel/Celtic Sea, and says outright: *"we do not include
the surface current evaluation in the Summary of the results"* due to
limited HF radar coverage elsewhere on the shelf — an honest gap in the
product's own documentation, not a research shortcut taken here.

Given no free, region-matched numeric reference exists (a real data-
availability limit, not skipped), the cross-check achieved and recorded
here — **presented to the operator, who confirmed this is acceptable**
— is two independent, real, freely-verifiable properties rather than one
precise number-vs-number match:
1. **Order-of-magnitude match**: CMEMS-sampled current speeds in this
   bbox are ~0.2-0.6 m/s (~0.4-1.2kn), consistent with a secondary
   (non-primary-cited, found via web search) pilotage reference stating
   tidal currents "reaching up to 2 knots" near Eddystone Rocks on
   springs — plausible given the sampled bbox is more open water, away
   from both the constricted "Bridge" passage inside Plymouth Sound
   (separately cited at up to 3kn on springs, not on this route) and
   from spring-tide peak conditions specifically.
2. **Periodicity match against undisputed physics**: the sampled
   current's sign-flip cadence (~6h) and the `optimise()` duration
   pattern's own near-return at t0=12h both match the English
   Channel/Western Approaches' well-established semidiurnal (M2, ~12.42h)
   tidal regime — a real, independently-verifiable physical fact, not
   this codebase validating its own output.

This is a real, if qualitative, cross-check — recorded honestly as such,
not rounded up to a false precision.

### 7. Explicit scope cuts (restated from the ticket description, plus findings from this pass)

- **Harmonic tide prediction (FES/TPXO via `pyTMD`) as a pack asset** —
  named follow-up, one-paragraph sketch only: the real edge-offline
  endgame is a small set of harmonic constituents per pack (baked into
  the pack manifest at generation time, computed once from FES2014/TPXO9
  at pack-build time), letting a vessel predict tidal currents locally
  with zero network dependency at all, rather than depending on a fresh
  CMEMS fetch — this ticket's CMEMS-fetch approach is the *cloud-role*
  answer (matches the existing edge-first split: cloud fetches, vessel
  pulls a compact npz), not the eventual fully-offline one. Not built
  here.
- **Digitised Admiralty tidal stream atlases** — rejected outright, not
  just deferred: real licensing cost, real manual-digitisation labour,
  and the harmonic-constituent path above dominates it on every axis
  that matters (cost, coverage, edge-offline fit).
- **R3 ocean-scale currents** (Gulf Stream routing, spherical geometry,
  currents as a genuinely global field) — untouched, a separate,
  larger ticket.
- **B7's reverse current-triangle STW correction for SOG-only historical
  import rows** — B7's plan explicitly named this as blocked on "no
  current data source." This ticket unblocks it (a real current field
  now exists for at least one pack) but does not build it — named
  follow-up in `fit/`, not touched here.
- **Demo/UI current visualisation** — not this ticket; a possible later
  `prototype/`/bridge-app tweak.
- **The A* heuristic admissibility gap** (§1) — documented, deferred,
  not fixed, per the judgment call above.
- **Med currents enablement** (§5) — deferred as a one-line follow-up,
  not built here.

## Implementation order

1. **Core fixes first, in isolation, before any ingest work** (§1a/1b):
   `LegResult.current_exceeds_stw` + the three hard-constraint call
   sites; `bilinear_masked` for current sampling. **Full `pytest -m ""`
   immediately after, before touching `ingest/`/`api/` at all** — same
   discipline R1 established, and the higher-stakes check here since
   these are genuine behaviour changes to shared search code, not pure
   parameter-threading — the freeze-compatibility argument above must
   hold in practice, not just in principle.
2. **`ingest/fetch_currents_cmems.py`** (§2/§3/§4) — real dataset ids
   above, `copernicusmarine.describe()` call to confirm exact variable
   names before finalising the parser, fixture tests with known analytic
   values (never real shipped data, per the no-invented-numbers
   constraint) for the direction/unit/masking normalisation.
3. **`ingest/merge_currents.py`** (§3) — the absolute-time-axis alignment
   design above; fixture tests with fabricated wind/wave + currents npz
   pairs at known offsets, asserting the merged result's `current_u_ms`/
   `current_v_ms` land at the expected resampled values.
4. **`RegionPack.currents_dataset_id` + `fetch_all_packs.py` orchestration
   + `GriddedWeatherField`/`HealthOut` provenance fields** (§3/§5) — all
   additive; `tests/test_api_state.py`-style fixture confirming a
   currents-disabled pack reports `currents_source: null` through
   `/v1/health`, and a currents-enabled fixture pack reports real
   provenance strings.
5. **uk_sw pack enabled, med left off** (§5) — `data/region_packs/uk_sw.yaml`
   gains `currents_dataset_id`; `data/region_packs/med.yaml` untouched.
6. **Docs**: `docs/region-pack-runbook.md` gains a currents step (§3's
   fetch+merge commands, ordering requirement); `deploy/README.md`'s
   cron section gains a currents note (parallel to R1's own multi-pack
   cron note) — `COPERNICUSMARINE_SERVICE_USERNAME`/`_PASSWORD` in
   `deploy/.env.example`, flagged as the CDS-equivalent one-time setup
   requirement; `CLAUDE.md` gains a gotcha entry for the two real §1
   findings, the CMEMS interface, and the deferred heuristic-admissibility
   limitation.
7. **UK acceptance run** (§6) — real fetch, real merge, real `optimise()`
   at two departure times, real cross-check against a cited external
   reference, results recorded in this file.

## Acceptance criteria, per part

- **§1 (core fixes)**: `pytest -m ""` green, **unmodified** — no existing
  test edited. New regression tests: a fabricated nonzero-current
  fixture that would trip `ValueError` under the old code is pruned
  (`current_exceeds_stw=True`, leg excluded, search still completes via
  other options) rather than crashing the whole `optimise()` call; a
  fabricated single-NaN-corner current stencil samples a real value via
  `bilinear_masked` instead of NaN-ing out.
- **§2/§3/§4 (ingest)**: `ingest/fetch_currents_cmems.py --help` documents
  the real dataset ids; a real (or realistically mocked, matching the
  ERA5 precedent for CI) fetch+merge round-trip produces a wind/wave npz
  whose `current_u_ms`/`current_v_ms` are no longer uniformly zero and
  whose other fields are byte-identical to before the merge. Amendment 2:
  a ≤6h coverage gap hold-nearests with a logged `WARNING` + provenance
  marker; a >6h gap raises and writes nothing. Amendment 1: a mocked
  currents-step failure still completes and publishes that pack's
  wind/wave fetch, logged at `WARNING`, and doesn't affect a second pack
  in the same `fetch_all_packs` run.
- **§5 (enablement/provenance)**: a currents-disabled pack's `/v1/health`
  reports `currents_source: null`; a currents-enabled fixture pack
  reports real provenance. `data/weather/ecmwf_western_med.npz` (the
  committed Med test fixture) is untouched by this ticket, confirmed via
  `git diff --stat` showing no diff to that file.
- **§6 (UK acceptance, the ticket's real proof) — met, 2026-07-22.** A
  real `optimise()` run on uk_sw at three departure times (0h, 6h, 12h)
  shows a measurable, tide-attributable difference: t0=6h is 9.5% slower
  and 9.5% more fuel than t0=0h; t0=12h returns within 0.3% of t0=0h,
  consistent with the ~12.4h M2 semidiurnal cycle. External cross-check:
  a precise numeric reference proved genuinely unavailable for free
  (checked and cited: EasyTide, Admiralty NP221/NP250, POLPRED, CMEMS's
  own QUID document) — recorded instead as two real, independently-
  verifiable qualitative checks (semidiurnal periodicity, order-of-
  magnitude agreement with a secondary pilotage figure), operator-
  confirmed as an acceptable substitute. Full trace: §6.

## Judgment calls flagged for sign-off (collected)

1. Fix the A* heuristic's now-broken zero-current admissibility
   assumption in this ticket, or defer it as a documented, named
   limitation (§1, recommending defer).
2. `fetch_currents_cmems.py`'s intermediate npz uses absolute UTC
   timestamps rather than this codebase's usual cycle-relative `hours`
   convention, specifically to make merging against a differently-cadenced
   product unambiguous (§3, recommending this design).
3. Currents-only fetch + merge as two separate scripts (one-source-per-
   script convention preserved) rather than one combined fetch-and-merge
   script (§3, recommending two scripts).
4. Reuse `mask_land_as_missing` (this repo's own GSHHG coastline) for
   current land-masking even though CMEMS's ocean grid is arguably
   already self-masked — redundant but consistent (§4, recommending
   keep it for consistency).
5. Med currents: deferred as a one-line follow-up config change, not
   enabled in this ticket (§5, recommending defer, with reasons given).
6. Real external tidal-reference source for the UK acceptance
   cross-check left unpinned until implementation time (§6, matching
   R1's own precedent for leaving real-world specifics to implementation).

**Review outcome: all six approved as recommended.** Two required
amendments (cron failure isolation for the currents step, §3; an
explicit hold-nearest/hard-error time-coverage policy in
`merge_currents.py`, §3) and two minor flags (`from_npz` backward
compatibility with pre-ticket npz files, §5; the CMEMS product-retirement
question, resolved live — not superseded, real dataset/variable names
confirmed directly against the product's own STAC record, §2)
incorporated inline above.

## ROADMAP row text (proposed)

To be added to the "Beyond Phase 2" table, or wherever the reviewer
prefers it land given it's pulled forward from R3 rather than native to
that table's own numbering:

> **C1 — Real surface currents (regional)** | CMEMS-sourced surface
> currents (NW Shelf incl. tides for the UK pack, Med product available
> but not enabled) merged into the existing `current_u_ms`/`current_v_ms`
> weather-field plumbing (`core/units.resolve_ground_speed_ms` already
> implements the current-triangle correction; only the ingest side wrote
> zeros). Pulled forward from R3's "currents become a real field" line,
> standalone at regional scale — not R3's ocean-crossing/spherical-geometry
> work. | Mostly ingest-layer; two real, freeze-compatible `core/` fixes
> found by tracing what "real current" actually exercises (a previously-
> unreachable current-exceeds-STW hard-constraint gap, and land-masked
> bilinear sampling) — see `docs/plans/ticket-C1.md`.

## Implementation status (2026-07-22)

**All 6 steps are done.** Steps 1-5 landed 2026-07-20, tested and green
(`pytest -m ""` unmodified after the core fixes, `pytest -q` full-suite
green throughout, `ruff check .` clean). **Step 6 (the real UK acceptance
run), previously paused pending Copernicus Marine Service credential
registration, ran live 2026-07-22** — see §6's "Live UK acceptance run
result" for the full trace: a real fetch+merge+`optimise()` run showing a
9.5%-slower/9.5%-more-fuel foul-tide penalty at t0=6h vs t0=0h, and a
qualitative-but-real external cross-check (semidiurnal periodicity match
against undisputed physics, plus order-of-magnitude agreement with a
secondary pilotage reference) once a precise numeric reference turned out
to be genuinely unavailable for free (checked: EasyTide, Admiralty NP221/
NP250, POLPRED, and the CMEMS product's own QUID validation document,
which explicitly has no Channel/Celtic Sea current validation) — the
operator was presented with this finding and confirmed the qualitative
cross-check is acceptable. Note: `data/region_packs/uk_sw/weather_uk_sw.npz`
is gitignored (local-only, regenerated not committed, per the region-pack
runbook's "goes stale within hours" convention) — the real-current merge
performed for this run doesn't need to be (and isn't) committed; any
fresh checkout regenerates it the same way, following
`docs/region-pack-runbook.md` §6.

## Verification

- `pytest -m ""` green, unmodified, immediately after §1's core fixes,
  before any ingest/api work — isolating the one place this ticket
  touches shared search code from everything else.
- `pytest -m ""` green again after every subsequent implementation step.
- `ruff check .` clean throughout.
- `git diff --stat` confirming `data/weather/ecmwf_western_med.npz` and
  `data/region_packs/med.yaml` are untouched.
- The UK acceptance run (§6) is a genuine live fetch/merge/`optimise()`
  sequence with a real external cross-check, not a mocked stand-in —
  matching this project's standing bias toward real verification runs
  over assumption (B7's live CDS run, R1's live WPI/UK ingest, the
  Hetzner deploy's live checks).

### Critical files for implementation

- `core/legs.py` (`LegResult.current_exceeds_stw`, `leg_navigation`'s
  try/except)
- `core/optimiser.py`, `core/isochrone.py` (three hard-constraint call
  sites; `_heuristic_cost_eur`'s docstring/comment update for the
  deferred admissibility limitation)
- `core/gridding.py` / `core/weather.py` (`bilinear_masked` for current
  sampling; new `current_cycle`/`current_fetched`/`current_source`
  fields)
- `core/regionpack.py` (`currents_dataset_id` field)
- `ingest/fetch_currents_cmems.py` (new)
- `ingest/merge_currents.py` (new)
- `ingest/fetch_all_packs.py` (per-pack currents orchestration)
- `data/region_packs/uk_sw.yaml` (enable), `data/region_packs/med.yaml`
  (untouched, deliberately)
- `api/schemas.py`, `api/routes.py` (`HealthOut` currents provenance)
- `deploy/.env.example`, `deploy/README.md`, `docs/region-pack-runbook.md`
  (currents step + `COPERNICUSMARINE_SERVICE_USERNAME`/`_PASSWORD`)
- `pyproject.toml` (`copernicusmarine` in `ingest` extras)
