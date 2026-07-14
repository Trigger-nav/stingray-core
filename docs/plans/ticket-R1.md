# Ticket R1 — Region packs (parameterise the region)

**Review status: approved with 3 required amendments + 3 minor flags,
incorporated below (amendment/flag markers inline).** All eight judgment
calls from the original plan are signed off as written, including SQLite
for favourites. Implementation follows this revised version.

## Context

Phase 0 + the B1/B2 bridge are done; a real optimiser core runs on real
Med geography and real forecasts, and B7 (2026-07-14) built the *data-layer*
half of region-independence: `ingest/fetch_gshhg.py`, `fetch_gebco.py`,
`fetch_grib_ecmwf.py`, `fetch_grib_nomads.py` all take `--bbox` today, with
a clobber guard, and `RealGeography._check_in_bounds` already derives its
bounds from whatever grid the instance actually loaded rather than the
module-level `OPERATING_AREA_BBOX` constant. B7's own scope note said
explicitly: *"No full R1 region-pack refactor — arbitrary endpoints, WPI
port entries, hand-drawn-corridor demotion are R1's job, a separate later
ticket."* This is that ticket.

R1's ROADMAP row (`ROADMAP.md:76`) frames it as "refactoring, not new
science," and states the engineering rule this ticket exists to enforce:
**`OPERATING_AREA_BBOX` and `PORTS` become per-region-pack data — new code
must not deepen them as global constants.**

**A major finding, ahead of any design work: arbitrary-endpoint routing is
already built.** `CORE_PORTING_NOTES.md`'s item B6 (dated July 2026, i.e.
already landed) states routing endpoints are arbitrary navigable points,
not a port enum — and the code backs this up. `core/optimiser.py`'s
`PlanRequest` already takes `origin`/`destination` as plain `LatLon`,
`origin_is_anchorage`/`destination_is_anchorage` flags, and
`_validate_endpoint()` already checks navigability plus, for anchorages, a
plausible depth band (`ANCHORAGE_MIN_DEPTH_M`/`MAX_DEPTH_M`). `core/legs.py`'s
`DEPTH_EXEMPT_RADIUS_NM` pilotage exemption is already endpoint-declared —
verified directly, not assumed: its docstring confirms B6's
`_validate_endpoint` already owns endpoint depth plausibility, and the
radius mechanism takes `exempt_points` at call time, generalizing cleanly.
`api/schemas.py`'s `PlanRequestIn` mirrors this exactly — `origin`/
`destination`/`origin_is_anchorage`/`destination_is_anchorage` are already
optional pydantic fields (`None` → core's own Med defaults). So **R1's
"arbitrary endpoints" scope item is not new engineering** — the real gap
is that today there is exactly one implicit geography/weather/vessel
context a `PlanRequest` can validate and route against (the Med), with no
concept of *which region* an origin/destination pair belongs to, and
several genuinely region-shaped constants still hard-imported underneath
it. That gap — the region-pack concept itself — is R1's actual work.

**Feature-freeze framing, restated as this ticket's own explicit
discipline (not the bridge-phase freeze verbatim, which is about new
optimiser *features*):** R1 touches `core/lattice.py` and
`core/corridors.py`, both freeze-protected files. The freeze's purpose is
no new optimiser features before ticket 1.5; R1 is geometry
*generalisation*, not a feature — the replacement discipline is **zero
behaviour change on the existing Med region**, checked mechanically:
`pytest -m ""` (full suite, including the Bonifacio regression tests, the
charter-window test, and the ~370s fine-resolution navigability sweep)
must pass **unmodified**. Any test needing an edit to keep passing is a
correctness red flag for this ticket, surfaced for review — not silently
fixed.

## Design

### 0. The region-pack manifest — the backbone of everything else

New `core/regionpack.py`, `RegionPack` — a frozen dataclass, following
`core/vessel_spec.py`'s `VesselSpec.from_yaml` precedent (the existing
example of a config-loading dataclass living in `core/` despite `core/`'s
general zero-I/O-side-effects bias; a `from_yaml` classmethod is
established as acceptable there already):

```python
@dataclass(frozen=True)
class RegionPack:
    pack_id: str
    name: str
    bbox: tuple[float, float, float, float]   # lon_min, lat_min, lon_max, lat_max — OPERATING_AREA_BBOX's own convention
    ref_lat_deg: float
    coastline_path: str
    bathymetry_path: str
    nogo_path: str
    tss_path: str
    weather_npz_path: str
    ports: dict[str, LatLon] = field(default_factory=dict)
    default_origin: LatLon | None = None
    default_destination: LatLon | None = None
    # Med-tuned lattice search knobs (see §1) -- pack-overridable, Med-value defaults.
    lane_turn_rate_nm: float = 15.0
    min_navigable_edge_fraction: float = 0.75
    min_refinement_step_nm: float = 0.5
    # Empty for every pack except "med" -- see §3. Names, not Callables --
    # see REQUIRED AMENDMENT 1 below for why.
    legacy_corridors: tuple[tuple[LatLon, LatLon, str], ...] = ()

    @classmethod
    def from_yaml(cls, path: str | Path) -> RegionPack: ...
```

**REQUIRED AMENDMENT 1 (review) — `legacy_corridors` can't hold `Callable`s
if `RegionPack` is also YAML-serialisable.** The original draft's
`tuple[LatLon, LatLon, Callable]` can't round-trip through
`data/region_packs/med.yaml` — YAML has no function literal. Fix: a new
registry in `core/corridors.py`,

```python
CORRIDOR_REGISTRY: dict[str, Callable[[], Corridor]] = {
    "corridor_west": corridor_west,
    "corridor_east": corridor_east,
}
```

`legacy_corridors` stores `(origin, destination, corridor_name: str)`
tuples; `RegionPack.from_yaml` resolves each name through
`CORRIDOR_REGISTRY`, raising a clear `ValueError` naming the unknown
corridor and the pack file it came from on a lookup miss (mirrors
`api/state.py`'s own "turn a raw lookup failure into an actionable
message" pattern, applied here at pack-load time rather than
job-submission time). `optimise()`'s corridor-DP loop (§3) resolves the
name to the function once per pack load (in `from_yaml`, not per-request)
— the resolved `Callable` lives on the in-memory `RegionPack` object
after loading; only the on-disk/YAML form is name-based. Test: a
fabricated pack YAML with a bogus corridor name raises `ValueError`
mentioning that name; `med.yaml`'s two real names resolve to the actual
`corridor_west`/`corridor_east` functions.

`RealGeography` gains one new classmethod, additive, existing constructor
untouched:

```python
@classmethod
def from_pack(cls, pack: RegionPack) -> RealGeography:
    geo = cls(pack.coastline_path, pack.bathymetry_path, pack.nogo_path, pack.tss_path)
    geo._check_pack_bounds(pack.bbox)
    return geo
```

**REQUIRED AMENDMENT 3 (review) — `from_pack` must catch a
manifest/data mismatch loudly, not let it fail as silent infeasibility.**
A `RegionPack` whose `bbox` field doesn't match the region its
`bathymetry_path`/`coastline_path` files actually cover (e.g. a copy-paste
error building a new pack's YAML) is a real, plausible authoring mistake
— B7 already flagged that GEBCO/GSHHG's `lat0_deg`/`lon0_deg` are sample
points, not the covered area's edge (`RealGeography._check_in_bounds`'s
own docstring). Without a check, the symptom would be confusing at
runtime: `core/lattice.py`'s lattice-clipping trusts `pack.bbox` while
`RealGeography`'s own point queries trust the loaded grid — a mismatch
between the two doesn't raise anywhere, it just silently prunes the
lattice to the wrong area, producing an opaque "no feasible route found"
with no hint why. Fix: a new `RealGeography._check_pack_bounds(self,
pack_bbox)`, called once at `from_pack` construction time (not on every
point query — this is a one-time manifest sanity check, unlike
`_check_in_bounds`'s per-query bounds check), computing this instance's
own derived bounds (same edge/half-grid-step logic `_check_in_bounds`
already uses) and asserting they match `pack_bbox` within half a grid
step in each direction, raising a clear, actionable error (which pack,
which files, what was expected vs. loaded) on mismatch. Test: construct a
`RegionPack` whose `bbox` is deliberately offset from a real loaded
bathymetry grid's actual coverage; assert `from_pack` raises with a
message naming the mismatch, not a downstream infeasibility.

The **Med pack is the existing committed data, unchanged**, wired up as
`data/region_packs/med.yaml`:  `bbox=OPERATING_AREA_BBOX`,
`ref_lat_deg=42.3`, the four already-committed `data/geography/*_western_med.*`
paths, `ports=PORTS` (the literal existing dict — not duplicated data),
`default_origin=PORTS["antibes"]`, `default_destination=PORTS["portocervo"]`,
`lane_turn_rate_nm=15.0` etc. (the existing tuned defaults), and
`legacy_corridors` populated with `(antibes, portocervo, "corridor_west")`
and `(antibes, portocervo, "corridor_east")`. This file is the literal,
checked-in expression of "OPERATING_AREA_BBOX and PORTS become per-region-pack
data" — the Med's own numbers move from Python constants into pack data
without changing value.

**REQUIRED AMENDMENT 2 (review) — `med.yaml` is the API/deployment-layer
form only; `core/` must not default to loading it.** See §1 below for the
full fix (`PlanRequest.region_pack: RegionPack | None = None` +
`MED_PACK`, a pure-Python constant with zero file I/O) — flagged here
because it directly affects how "the Med pack" is expressed: there are
now **two** representations of the same data, `MED_PACK` (a `RegionPack`
literal built from the existing module constants, used as `core/`'s
own zero-I/O default) and `data/region_packs/med.yaml` (loaded via
`from_yaml`, used by the API/deployment layer) — kept from drifting apart
by a test asserting they're equal (§1).

**`core/corridors.py`'s `OPERATING_AREA_BBOX`-adjacent module constants —
`REF_LAT_DEG`, `PORTS`, `DEFAULT_ORIGIN`/`DEFAULT_DESTINATION` in
`core/optimiser.py` — are kept exactly as-is, not deleted.** They remain
the zero-config fallback used when no `RegionPack` is supplied at all
(preserves every existing test and caller that constructs a bare
`PlanRequest()`/calls `build_lattice()` positionally). New code is
required to route through a `RegionPack` instead of importing these
directly — enforced by review/convention (matching how B6 already states
`PORTS`/`OPERATING_AREA_BBOX` "must not be deepened" today), not by
deleting the constants themselves. This is the same additive-parameterisation
pattern B7 used for `RealGeography._check_in_bounds` and every `fit/`
schema field: old defaults stay byte-identical, new capability is layered
on top.

### 1. `core/lattice.py` / `core/corridors.py` / `core/legs.py` — threading pack data through

**REQUIRED AMENDMENT 2 (review) — `PlanRequest.region_pack`'s default
must not do file I/O.** The original draft implied `PlanRequest`
defaults to "the Med pack," which — if that meant loading `med.yaml` —
would give `core/` an import- or construction-time file dependency
resolved relative to the process's current working directory. That's
exactly the shape of bug the `install.sh`/`data/` deploy finding (2026-07-13,
`CLAUDE.md`'s deploy-findings gotcha) already burned this project on once:
workers, tests, and the frozen PyInstaller binary all construct
`PlanRequest` from different working directories, and a relative-path
default buried inside a dataclass default would fail invisibly in at
least one of them. Fix: `PlanRequest.region_pack: RegionPack | None =
None`. `optimise()` resolves `request.region_pack or MED_PACK` at call
time, where `MED_PACK` is a plain Python module-level constant —

```python
# core/optimiser.py, or core/regionpack.py if that avoids a circular import
MED_PACK = RegionPack(
    pack_id="med", name="Western Mediterranean",
    bbox=OPERATING_AREA_BBOX, ref_lat_deg=REF_LAT_DEG,
    coastline_path=DEFAULT_COASTLINE_PATH, bathymetry_path=DEFAULT_BATHYMETRY_PATH,
    nogo_path=DEFAULT_NOGO_PATH, tss_path=DEFAULT_TSS_PATH,
    weather_npz_path="data/weather/ecmwf_western_med.npz",
    ports=PORTS, default_origin=PORTS["antibes"], default_destination=PORTS["portocervo"],
    lane_turn_rate_nm=LANE_TURN_RATE_NM,
    min_navigable_edge_fraction=DEFAULT_MIN_NAVIGABLE_EDGE_FRACTION,
    min_refinement_step_nm=DEFAULT_MIN_REFINEMENT_STEP_NM,
    legacy_corridors=((PORTS["antibes"], PORTS["portocervo"], "corridor_west"),
                       (PORTS["antibes"], PORTS["portocervo"], "corridor_east")),
)
```

— built entirely from constants already imported at module load time, no
`open()`/`from_yaml` call anywhere in the construction path. `RealGeography`
itself is *not* constructed here (that still happens once, in
`AppState`/`_worker_init`, unchanged) — `MED_PACK` only carries the *paths*
and tuning numbers a `PlanRequest` needs to resolve pack-shaped defaults
before a `Geography` instance exists. `data/region_packs/med.yaml` remains
real and is what the API/deployment layer's `Settings.region_packs_path`
loads (§4) — it is not read by anything in `core/`. **Regression test,
directly guarding the one real risk this split creates (the two
representations drifting apart): `RegionPack.from_yaml("data/region_packs/med.yaml")
== MED_PACK`.** Since `corridor_west`/`corridor_east` resolve to the same
function objects either way (amendment 1's registry), and every other
field is a plain value, dataclass equality is exact and meaningful here —
this test fails the moment anyone edits one representation without the
other.

**`REF_LAT_DEG` (duplicated today between `core/corridors.py:14` and
`core/lattice.py:33`, imported again by `core/legs.py:15`) is
pack-supplied data, not physics — but it does *not* become a
`Geography.ref_lat_deg` protocol property.** I checked this directly:
`RealGeography.__init__` already derives its own `lat_min`/`lat_max` from
the loaded grid (`_check_in_bounds`'s own bound-derivation logic), so a
bbox-centroid-derived `ref_lat_deg` is trivially computable from an
instance — and `SyntheticGeography` already *accepts* `ref_lat_deg` as a
constructor arg (`core/geography.py:248`) but only stores the derived
cosine, not the value itself. A protocol property was the first idea I
considered, and I'm rejecting it: `build_lattice(..., geography=None)`
already treats `geography` as optional (the adaptive-refinement pass is
skippable), and several call sites needing `ref_lat_deg` — `core/corridors.py`'s
module-level `offset_point`, `core/lattice.py`'s `_offset_point` — do
pure lat/lon-offset math with no `Geography` instance in scope at all.
Requiring every caller to thread a `Geography` instance through just to
read one float, when `core/units.py`'s `distance_m`/`bearing_deg`
primitives *already* take `ref_lat_deg` as a plain explicit (non-defaulted)
parameter, would be inconsistent with the codebase's own existing
convention. **Decision: `ref_lat_deg` becomes an explicit parameter
threaded from the active `RegionPack`, defaulting to today's `42.3`
everywhere it's already a parameter, newly added as a parameter (default
`REF_LAT_DEG`) everywhere it's currently a bare import.** Concretely:
`core/legs.py`'s functions gain `ref_lat_deg: float = REF_LAT_DEG` params
instead of importing the constant; `optimise()` reads `request.region_pack.ref_lat_deg`
and passes it down to every one of `build_lattice`, the corridor
functions, and `core/legs.py`'s navigability/depth calls it already makes.

**`core/lattice.py`'s `OPERATING_AREA_BBOX` hard import (`_within_bbox_with_margin`,
the only use in the file) gets the same treatment**: `build_lattice(...,
bbox: tuple[float, float, float, float] = OPERATING_AREA_BBOX)`, threaded
to `_within_bbox_with_margin`. `optimise()` passes `request.region_pack.bbox`.
Existing callers passing no `bbox` arg get identical Med clipping.

**The three Med-tuned lattice knobs — `LANE_TURN_RATE_NM`,
`DEFAULT_MIN_NAVIGABLE_EDGE_FRACTION`, `DEFAULT_MIN_REFINEMENT_STEP_NM` —
are explicitly Bonifacio-tuned search heuristics, not vessel physics, and
become pack-overridable-with-Med-default data, not pack-independent
constants left alone.** This is stated as a direct judgment call per the
ticket's own instruction to make this call explicitly. Justification,
from the code's own documentation: `LANE_TURN_RATE_NM`'s comment says
outright it is "not a vessel kinematic limit — the vessel steers each leg
at its own heading regardless" and records the exact empirical tuning
story (10nm found too tight, 15nm gives real margin) against Bonifacio's
scattered islet field specifically; `DEFAULT_MIN_NAVIGABLE_EDGE_FRACTION`'s
own comment cites "Bonifacio's real worst coarse-resolution stage sits at
exactly 0.700" as the number that shaped the 0.75 threshold. Neither has
any claim to being right for a different coastline's degradation
profile — a scattered-islet strait and, say, a UK ria/estuary coastline
plausibly need different values, and there's no principled reason to
assume 15nm/0.75/0.5nm are correct anywhere but the Med. Good news,
confirmed directly against `build_lattice`'s actual signature: **`min_navigable_edge_fraction`,
`max_refinement_passes`, and `min_refinement_step_nm` are already exposed
as overridable keyword parameters** — this part of R1 is smaller than the
ROADMAP row might suggest. Only `LANE_TURN_RATE_NM` itself needs a new
parameter (it's currently read as a bare module constant inside
`_turn_range`). Concretely: `build_lattice(..., lane_turn_rate_nm: float =
LANE_TURN_RATE_NM)`, threaded to `_turn_range`; `optimise()` passes
`request.region_pack.lane_turn_rate_nm` (`= 15.0` for "med", unchanged
behaviour) and `.min_navigable_edge_fraction`/`.min_refinement_step_nm`
the same way. **For the UK acceptance pack (§6), ship the same Med-tuned
defaults as the pack's own starting values** — there is no principled UK
number to substitute without empirical tuning against real UK coastline
data, and inventing one would violate "no invented numbers." The
acceptance criterion is a feasible plan, not a re-tuned search; if the
default values turn out to make the UK pack's search infeasible or absurd,
that's a real finding to report during implementation, not something to
paper over with a guessed constant.

**`core/optimiser.py`'s three `m_to_nm(distance_m(..., REF_LAT_DEG))`
call sites (lines 380, 595, 697) are a genuine, required fix** — unlike
the corridor-DP gating (below), these unconditionally use the Med
constant regardless of which region a request targets, and would silently
produce wrong-scale costs for a non-Med pack. Threaded from
`request.region_pack.ref_lat_deg`.

**`core/isochrone.py` and `core/units.py` need no changes** — confirmed
directly: `isochrone.py`'s only relevant import is the `Geography`
protocol itself, zero hardcoded Med constants; **[implementation
correction: this claim held for constants but missed parameter
propagation — `isochrone.py`'s `_best_feasible_duration_h` calls
`evaluate_leg`, which gained `ref_lat_deg` in this ticket's threading
pass, so isochrone.py did change after all (it now threads
`lattice.ref_lat_deg` through — purely mechanical, no logic change)]**; `units.py`'s `distance_m`/
`bearing_deg` already require `ref_lat_deg` as a plain parameter with no
default, i.e. already pack-agnostic primitives. This narrows the surface
area of the "threading pass" considerably — it's `lattice.py`,
`corridors.py`, `legs.py`, and `optimiser.py`'s own three call sites, not
a codebase-wide sweep.

### 2. `_route_signature`'s Corsica-fallback bug (found during this planning pass, real, previously undocumented)

`core/optimiser.py`'s `_route_signature(track)` labels a candidate route
"W" or "E" of Corsica for logging/diagnostics. Its fallback path (used
when no track point falls inside `CORSICA_LAT_BAND`) compares the track's
mean longitude against `CORSICA_REF_LON = 9.0` — for **any** passage
entirely west of 9°E (the UK/Channel pack, by construction, always is),
this fallback always returns "W", regardless of the route's actual shape.
This is a labelling/diagnostics bug, not a search-correctness bug —
`_distinguishing_region()` (the function that actually gates
`_side_diversity_filter`'s search-affecting behaviour) already, correctly,
returns `None` for a passage nowhere near Corsica, and
`_side_diversity_filter` already degrades gracefully to "unconstrained"
in that case (both confirmed directly, both pre-existing, both from
ticket 0.8's amendment 3). **Fix: `_route_signature` takes the
already-computed `distinguishing_region` result as a parameter instead of
hardcoding `CORSICA_REF_LON`, and returns a neutral label (e.g. `None`/`"—"`)
when there is no distinguishing region for this pack/passage, instead of
claiming a directional label that isn't geographically meaningful.**

**`CORSICA_LAT_BAND`/`CORSICA_REF_LON` themselves are left as Med-specific
module constants, not generalised into pack data, in this ticket —
explicit scope cut, flagged for sign-off.** They're real, working,
non-trivial classification logic tied to one specific geographic feature
(the Corsica land barrier splitting west/east routing options). A fuller
generalisation (`RegionPack.distinguishing_region: tuple[...] | None`)
is a clean design and a natural next step, but the UK acceptance
criterion doesn't need it — `_distinguishing_region` already returns
`None` gracefully for any pack without a matching feature, `_side_diversity_filter`
already handles that correctly, and touching this logic further would
widen the diff inside `core/optimiser.py`'s single highest-regression-risk
area (the Bonifacio side-diversity mechanism, ticket 0.8's hardest-won
fix) for no acceptance-relevant benefit. Recommend leaving it exactly as
today's code, with a one-line comment noting it's Med-specific and a
named future generalisation, not a silent gap.

### 3. Hand-drawn corridors demoted to Med-only legacy

`optimise()`'s current corridor-DP gate (`if request.origin ==
DEFAULT_ORIGIN and request.destination == DEFAULT_DESTINATION: for
corridor_fn in (corridor_west, corridor_east): ...`) is, I confirmed
directly, **already safe for non-Med regions as written** — the equality
check can never match a UK pack's origin/destination, since those aren't
`DEFAULT_ORIGIN`/`DEFAULT_DESTINATION`. No code change is strictly
required to prevent a non-Med pack from hitting this path.

Replacing it anyway, for the reason the ROADMAP row states explicitly
("nothing outside the Med pack references corridors" as a designed
property, not an accident of non-matching defaults): `RegionPack.legacy_corridors`
(§0) replaces the hardcoded tuple + equality check —
`optimise()` loops `for origin, destination, corridor_fn in
request.region_pack.legacy_corridors: if request.origin == origin and
request.destination == destination: ...`. For "med" this is
value-identical to today (same two corridors, same endpoint pairing,
same fallback behaviour for any other origin/destination pair within the
Med pack); every other pack ships `legacy_corridors=()`, so the loop body
never executes — structurally, not incidentally, corridor-free.

### 4. API layer — `PlanRequestIn`, `AppState`, multi-pack worker state

**Schema change (`api/schemas.py`):** `PlanRequestIn` gains
`pack_id: str = "med"`. Default preserves exact current behaviour for
every existing caller (the hosted demo's fixed passage keeps working
unmodified, and becomes — per the ticket's own framing — just one saved
favourite within the "med" pack, not a change to how it's requested
today). This crosses the CI parity test between `api/schemas.py` and
`core/optimiser.py`'s `PlanRequest` (`api/convert.py`'s conversion layer)
— `PlanRequest` itself needs a `region_pack: RegionPack` field (not
`pack_id: str` — the core layer works with the resolved pack object, not
a string key; string-to-object resolution is an API-layer concern, see
below), so the parity test's field-mapping needs a new explicit entry
covering `pack_id` (API) → `region_pack` (core), resolved not copied
verbatim — flagging this as the one parity-test change that isn't a
straight 1:1 field rename, worth a reviewer's eye.

**Settings/config (`api/config.py`) — the real design decision here,
flagged for sign-off.** Today's `Settings` is single-pack: one
`coastline_path`/`bathymetry_path`/`nogo_path`/`tss_path`/`weather_npz_path`,
one flat set of `STINGRAY_*` env vars, matching the project's stated
"boring tech, not pydantic-settings" bias. Expressing N packs as
individually-named flat env vars doesn't scale (there's no clean
`STINGRAY_PACK2_BATHYMETRY_PATH`-style pattern that stays boring past 2
packs). **Proposed: a new `Settings.region_packs_path: str | None = None`
(`STINGRAY_REGION_PACKS_PATH` env var) pointing at a YAML file
listing all configured packs for that deployment — `data/region_packs.yaml`,
loaded via `RegionPack.from_yaml`-per-entry, same YAML-config-file
precedent `VesselSpec` already established.** When unset (the default —
every currently-deployed vessel/cloud instance), `Settings` synthesizes a
single implicit "med" pack from today's existing flat
`coastline_path`/etc. fields exactly as before — **zero migration forced
on any existing deployment**; a bridge PC or the pilot cloud VM with its
current `.env` keeps working identically the moment this ships, with no
new file to create. New multi-pack deployments (the pilot's eventual
Med+UK setup) opt in by setting `STINGRAY_REGION_PACKS_PATH`. This is a
genuine architecture choice with a real alternative (e.g., N sets of
numbered flat env vars) — recommending the YAML-manifest path as
consistent with existing precedent and because a packs list is naturally
tabular/nested data, not flat key-values, but flagging for explicit
sign-off since it's the largest new piece of ticket-level design in the
API layer.

**`AppState`/`_worker_init`/`run_plan_job` (`api/state.py`) — confirmed by
direct read that these are genuinely, deeply single-pack today**:
`_worker_geography`/`_worker_weather`/`_worker_vessel` are bare
module-level globals, one value each, set once by `_worker_init`;
`AppState.__init__` builds exactly one `RealGeography`/`WeatherField`/
`VesselSpec`. **Redesign: these become `dict[str, Geography]`/
`dict[str, WeatherField]` keyed by `pack_id`, loaded by looping over every
configured pack at `_worker_init` time** (mechanically the same
single-pack construction repeated N times, not new logic per pack).
`PlanJobPayload` gains `pack_id: str = "med"`; `run_plan_job` looks up
`_worker_geography[payload.pack_id]` (a clear, actionable `KeyError` →
wrapped message for an unconfigured pack_id, mirroring the existing
`_load_weather`'s `FileNotFoundError`-to-actionable-message pattern) instead
of reading the bare global. `_validate_weather_sane`'s sanity-check points
(currently hardcoded `DEFAULT_ORIGIN`/`DEFAULT_DESTINATION`) become
`pack.default_origin`/`.default_destination`, looped once per configured
pack.

**MINOR FLAG (review) — the dict-keyed redesign must cover all three
`_load_weather` call sites, not just `_worker_init`.** Confirmed directly:
`_load_weather` is called from (1) `AppState.__init__` (main-process
copy, used for `/v1/health` provenance + the synchronous fail-fast
validation pass), (2) `_worker_init` (per worker process), and (3) the
hot-swap mtime-watcher reload path (`AppState`'s background task that
re-`_load_weather`s when the npz file's mtime changes, keeping cron's
periodic re-fetch visible without a service restart). All three become
per-pack: `AppState.weather: dict[str, WeatherField]`, the watcher loops
its mtime-check over every configured pack's npz path independently (one
pack's cron-refreshed file changing must not block or wait on another
pack's), and `_worker_init` as already described. Missing any one of the
three would leave a real, easy-to-miss staleness bug — e.g. the hot-swap
watcher silently only ever refreshing the Med pack's weather while a UK
pack's npz goes stale forever with no error, since nothing about that
failure mode is loud (health endpoint would still report "healthy," just
with one pack silently unrefreshed). Test: fabricate two packs' worth of
npz files, touch one's mtime, assert only that pack's in-memory
`WeatherField` changes, the other's doesn't.

**Vessel-role weather sync (`api/weather_sync.py`) becomes per-pack too**:
today's single conditional-HTTP pull (opportunistic, ETag/If-Modified-Since
against `GET /v1/weather/latest.npz`) becomes one independent pull per
pack the vessel's own `Settings.region_packs_path` configures it to care
about — a UK-only bridge PC's packs manifest lists only the UK pack, so it
only ever issues one pull, not one per pack the *cloud* role happens to
serve. Each pack's pull keeps its own independent conditional-HTTP
state (separate ETag/Last-Modified tracking) so one pack's fetch failure
or staleness doesn't block another's.

**Tradeoff flagged for sign-off: one shared `ProcessPoolExecutor` with
dict-keyed globals (every worker loads every configured pack) vs. a
dedicated executor per pack.** Recommending the shared-pool design for
v1: it's the smaller change (today's single `ExecutorHolder`/pool-sizing
logic is untouched), and per the ticket's own framing ("even if v1 ships
with exactly two packs configured"), a worker holding two packs' worth of
geography+weather data is cheap in absolute terms (the existing docstring
in `state.py` already notes bathymetry/weather grids are ~1MB scale). The
real cost of this choice is that a deployment's `region_packs.yaml` is
also implicitly the mechanism for *scoping* which packs a given box
loads — a UK-only bridge PC lists only the UK pack in its own
`region_packs.yaml`, not "all packs the fleet uses" — worth stating
explicitly in the deploy docs rather than left implicit, but not a code
problem.

### 5. `api/weather_field.py` — hard-coded bbox (explicitly scoped out of B7, in scope now)

Confirmed directly: `build_weather_field` unconditionally uses
`OPERATING_AREA_BBOX` regardless of which pack the request's weather
belongs to. Fix, same additive pattern as everywhere else in this ticket:
`build_weather_field(weather, valid_time_h, bbox: tuple[float, float,
float, float] = OPERATING_AREA_BBOX)`. The route handler resolves the
request's pack (from a new `?pack=` query param, defaulting to `"med"` —
matching `PlanRequestIn`'s own default) and passes that pack's `bbox` and
matching `WeatherField` instance through.

**MINOR FLAG (review) — `compute_weather_field_etag` must incorporate
`pack_id`, or a UK request can 304 against a cached Med grid.** Confirmed
directly: the ETag key today is `f"{cycle}|{fetched}|{valid_time_h}|{FIELD_GRID_NLAT}x{FIELD_GRID_NLON}"`
— nothing pack-specific. Two packs' weather fields can easily share the
same `(cycle, fetched, valid_time_h)` triple (e.g. both fetched from the
same cron run, same hour requested), in which case a browser that already
cached the Med response for that key would get served a `304 Not
Modified` for a `?pack=uk_sw` request carrying the same `If-None-Match` —
silently serving stale/wrong-region data with no error anywhere. Fix:
`compute_weather_field_etag(weather, valid_time_h, pack_id)`, `pack_id`
folded into the hashed key. **Test**: build two fields for two distinct
(fabricated) packs sharing an identical `(cycle, fetched, valid_time_h)`,
assert their ETags differ.

### 6. WPI ports + per-vessel favourites

**WPI ingest — genuinely new, greenfield work, no existing script to
extend.** New `ingest/fetch_wpi_ports.py`, following this repo's
established `ingest/fetch_*.py` CLI conventions (argparse, `--bbox` to
filter to a pack's region, writes JSON matching `RegionPack.ports`'
`dict[str, LatLon]` schema). NGA's World Port Index is a real, public
dataset — **the actual download URL/format needs verifying at
implementation time, not guessed now** (this plan does not invent one,
per the project's own no-invented-numbers principle and the standing rule
against guessing URLs). Flag for implementation: confirm current
publication format (historically a shapefile/CSV export) before writing
the parser.

**Per-vessel saved favourites — open design question, explicitly flagged,
no existing precedent fits cleanly.** Two real options:
1. **SQLite**, matching `capture/`'s existing embedded-SQLite precedent
   (`data/telemetry/telemetry.sqlite3`) — a new, separate small
   `favourites.sqlite3` (not a shared table inside the telemetry DB;
   different writer, different lifecycle, avoids coupling two unrelated
   concerns into one schema/file). Handles concurrent read/write from a
   bridge UI cleanly, matches how this codebase already solves "small
   structured per-vessel state, one axis simpler than a full document
   store."
2. **A per-vessel YAML/JSON file** — simpler to inspect/edit by hand,
   but needs its own concurrent-write handling if the future bridge app
   (ticket 2.2) ever writes from multiple contexts, which SQLite gets for
   free.

**Signed off directly by Jack: option 1 (SQLite), a separate
`favourites.sqlite3`.** New `api/favourites.py`: `GET/POST/DELETE
/v1/favourites` scoped by `vessel_id` (mirrors `api/config.py`'s existing
`telemetry_db_path` pattern — a new `favourites_db_path` setting, same
shape). Schema: `(id, vessel_id, name, lat_deg, lon_deg, is_anchorage,
pack_id, created_at)`.

**MINOR FLAG (review) — `POST /v1/favourites` is a state-writing endpoint
behind the single shared Basic Auth credential (`api/config.py`'s
`auth_user`/`auth_password`), named explicitly as inheriting ticket 1.4's
multi-tenant auth debt, not something this ticket fixes.** Every existing
endpoint this credential guards is either read-only or a compute job
scoped by request payload, not per-account persisted state — favourites
are the first thing in this API that *persists* something tied to an
identity (`vessel_id`) without any actual per-vessel authentication, only
one shared password for the whole deployment. Acceptable for v1 (a single
pilot yacht or a small fleet sharing one deployment, matching everything
else in this phase's threat model), but worth naming plainly so it isn't
mistaken for real multi-tenant isolation — the same category of gap
ticket 1.4 (not yet scoped in detail) already exists to close.

### 7. `STINGRAY_ROLE` / deploy / cron — multi-pack weather serving

Confirmed against `api/config.py`'s docstring and `CLAUDE.md`'s own
gotcha: `cloud` role runs `ingest.fetch_grib_*` on cron for one bbox,
serving `GET /v1/weather/latest.npz`; `vessel` role never imports
`ingest.*`, pulling that npz opportunistically instead
(`api/weather_sync.py`) — this split exists specifically to keep
`cfgrib`'s `eccodes` system dependency out of the Windows/macOS
PyInstaller build, and must be preserved, not replaced.

**Multi-pack cloud role:** cron needs to fetch weather for N bboxes, one
per configured pack, not one. Recommending a thin wrapper script/module
(`ingest/fetch_all_packs.py` or a `deploy/fetch_all_packs.sh` looping over
`region_packs.yaml`) over N raw independent crontab lines — a packs
manifest that's also the single source of truth for *which* bboxes cron
fetches keeps crontab and the packs list from silently drifting apart;
each raw `ingest.fetch_grib_*` invocation underneath is unchanged (still
one fetch, one bbox, one output path — just looped, not duplicated
logic). `GET /v1/weather/latest.npz` gains a `?pack=` query param
(default `"med"`, matching everywhere else in this ticket); `api/weather_sync.py`
(vessel role) is configured with the specific pack(s) *that vessel*
cares about — a UK-only bridge PC pulls only the UK pack's npz, not every
pack the cloud role happens to serve, avoiding wasted bandwidth for
irrelevant regions. **v1 ships with exactly two packs configured
(Med + UK), per the ticket's own acceptance framing** — this design
supports more without further changes, but the actual `region_packs.yaml`
committed in this ticket lists only those two.

### 8. Pack-generation runbook (Part 4)

New `docs/region-pack-runbook.md`, extending — not duplicating —
`docs/historical-import-runbook.md`'s existing GEBCO→GSHHG→weather
ordering (already correct and already bbox-parametric per B7 Part 1),
with the two pieces B7 explicitly didn't cover:

1. **Geography** (reuse verbatim): `ingest.fetch_gebco --bbox ... --out
   .../bathymetry_<pack>.npz`, then `ingest.fetch_gshhg --bbox ... --out
   .../coastline_<pack>.json --bathymetry .../bathymetry_<pack>.npz`
   (ordering enforced already — GSHHG rasterizes onto whatever bathymetry
   its `--bathymetry` arg points to).
2. **No-go / TSS zones — a manual research step, not a script.** Confirmed
   directly: unlike geography/weather, there is **no ingest script for
   nogo/TSS data at all** — `data/geography/nogo_western_med.json`/
   `tss_western_med.json` were hand-authored with cited real sources
   (marineregions.org MRGIDs) during ticket 0.8, not fetched. The runbook
   documents this honestly as a research task: identify real no-go zones
   / TSS lanes for the new region if any are known (citing sources, and
   marking `precise_boundary_verified: false` with a caveat exactly as
   ticket 0.8's own placeholder TSS file already does when precise lane
   geometry isn't available), **or ship `{"zones": []}`** — confirmed
   directly that an empty zones list needs zero code changes to
   `_load_nogo_polygons`. For the UK acceptance pack specifically, an
   empty (or minimally-researched) file is expected and acceptable —
   the acceptance criterion is a feasible plan on real geography/weather,
   not a fully chart-verified TSS survey of the Channel.
3. **Weather** (reuse verbatim): the same `--bbox`-parametric
   `ingest.fetch_grib_nomads`/`fetch_grib_ecmwf` invocations B7 Part 1
   built, writing to the pack's own weather npz path.
4. **WPI ports** (new, §6): `ingest.fetch_wpi_ports --bbox ... --out
   .../ports_<pack>.json`.
5. **Assemble the manifest**: write `data/region_packs/<pack>.yaml`
   pointing at all of the above, add it to the deployment's
   `region_packs.yaml` pack list.
6. **Validate**: run `optimise()` end-to-end against the new pack with a
   real origin/destination inside it and assert a feasible plan — stated
   as the runbook's own final step, i.e. this ticket's UK acceptance test
   (§ Acceptance) becomes the reusable template for validating *every*
   future pack, not a one-off proof.

## Implementation order

1. **`core/regionpack.py`** (`RegionPack`, `from_yaml`) + `data/region_packs/med.yaml`
   encoding today's exact Med values. No other file changed yet — this
   step is purely additive and independently testable (load the Med pack,
   assert every field matches today's existing constants).
2. **`core/geography.py`**: `RealGeography.from_pack` classmethod.
3. **`core/lattice.py` / `core/corridors.py` / `core/legs.py` / `core/optimiser.py`
   parameter-threading pass** (§1–§3): add `bbox`/`ref_lat_deg`/
   `lane_turn_rate_nm`/etc. as explicit defaulted parameters; wire
   `optimise()` to resolve `region_pack = request.region_pack or MED_PACK`
   (no I/O — every existing bare `PlanRequest(...)` construction in tests
   keeps working unmodified, resolving to `MED_PACK`); fix
   `_route_signature`'s Corsica fallback; replace corridor-DP gating with
   `RegionPack.legacy_corridors` (registry-resolved). **Full `pytest -m ""`
   run immediately after this step, before touching `api/` at all** — this
   is where the "zero behaviour change" guarantee is actually proven or
   disproven, in isolation from any API-layer risk.
4. **`api/` layer** (§4–§5): `PlanRequestIn.pack_id`, `api/convert.py`
   parity-test update, `Settings.region_packs_path` +
   `data/region_packs.yaml`, `AppState`/`_worker_init`/`run_plan_job`
   dict-keyed redesign, `api/weather_field.py` bbox fix.
5. **WPI ingest + favourites** (§6) — independent of steps 1–4 beyond
   needing `RegionPack.ports`' schema to target; can run in parallel with
   step 4 if convenient.
6. **Deploy/cron multi-pack wiring** (§7) — depends on step 4's
   `region_packs.yaml` shape existing.
7. **UK pack generation + acceptance run** (§8, Acceptance below) —
   depends on everything above; the actual proof of the ticket.
8. **Runbook doc** (§8) — written alongside step 7, since writing it is
   how step 7 gets executed.

## Acceptance criteria, per part

- **§0–§3 (core geometry generalisation):** `pytest -m ""` green,
  **unmodified** — no test file edited. `git diff --stat` shows
  `core/lattice.py`/`core/corridors.py`/`core/optimiser.py`/`core/legs.py`
  changed but every new parameter has a default reproducing today's exact
  Med values. New tests, all required by the review amendments: (1)
  `RegionPack.from_yaml("data/region_packs/med.yaml") == MED_PACK` (the
  drift guard, amendment 2); (2) an unknown corridor name in a fabricated
  pack YAML raises `ValueError` naming it (amendment 1); (3)
  `RealGeography.from_pack` raises on a deliberately bbox-mismatched pack
  (amendment 3).
- **§4 (API layer):** the CI parity test passes with the `pack_id` ↔
  `region_pack` mapping added explicitly (not a silent gap); a request
  with no `pack_id` behaves identically to today (hosted demo keeps
  working unmodified); a request for an unconfigured `pack_id` returns a
  clear 4xx, not a worker crash.
- **§5 (`api/weather_field.py`):** `GET /v1/weather/field?pack=med`
  matches today's unparametrized output exactly; a `?pack=uk_sw` request
  against the UK pack's data returns a grid over the UK bbox, not the Med
  one.
- **§6 (WPI + favourites):** at least one real named UK port ingested via
  WPI and resolvable as a `RegionPack.ports` entry; a favourite can be
  saved/listed/deleted per `vessel_id` and round-trips through the
  chosen persistence layer.
- **§7 (deploy/cron):** `region_packs.yaml` with two entries drives two
  independent cron-fetched npz files; `GET /v1/weather/latest.npz?pack=uk_sw`
  serves the UK one.
- **§8 / overall (the ticket's actual point):** a real UK South-West /
  Channel pack (a real endpoint pair such as Plymouth–Falmouth, exact
  coordinates and bbox margin decided at implementation time against real
  chart positions, not invented here) is generated via the runbook using
  real GEBCO/GSHHG data (via B7's `--bbox` flags) and real weather
  (NOMADS/ECMWF), and `optimise()` runs end-to-end against it producing a
  **feasible plan** on a passage the Med pack's own geography/bbox could
  never serve (a point outside `OPERATING_AREA_BBOX` entirely). This is
  the load-bearing proof that region-pack parameterisation actually works,
  not just that it doesn't break the Med.

## Scope cuts (explicit)

- **R2** (cloud-side pack pipeline/sync across ~6 packs, chart-folio sync
  model) — out. This ticket ships tooling to *generate* one pack by hand
  and configure a deployment with a short fixed list; no sync protocol,
  no automatic pack discovery/download.
- **R3** (spherical/great-circle geometry, ocean-scale passages, currents
  as a real field, tropical-cyclone polygons) — out entirely. R1's
  equirectangular-plus-single-`ref_lat_deg` approximation is explicitly
  a regional, not oceanic, approximation; nothing here changes that math,
  only which region's `ref_lat_deg` it's anchored to.
- **R4** (two-level global planner, coarse sea graph, canal/strait
  scheduling) — out entirely.
- **`CORSICA_LAT_BAND`/`CORSICA_REF_LON` generalisation** (§2) — left
  Med-specific; `_distinguishing_region` already degrades gracefully for
  any pack without an equivalent feature. Flagged as a deliberate,
  reviewable scope cut, not an oversight.
- **Full Bonifacio-lattice-tuning re-derivation for the UK pack** — the UK
  pack ships with the Med's own tuned lattice-search defaults (§1); no
  UK-specific empirical re-tuning is performed in this ticket beyond
  whatever the acceptance run itself reveals is necessary to reach a
  feasible plan.
- **`BridgeSimulatorAdapter`-style unknowns** don't apply here (that's a
  B7/fit-layer concern) — noted only to avoid confusion with B7's own
  named follow-ups.
- **No real second-pack coverage beyond UK South-West/Channel** — the
  ticket proves the mechanism with one real second pack; a fuller
  multi-region rollout (Caribbean, US East Coast, etc.) is R2's job.
- **No WPI-sourced port data folded into the Med pack** — `PORTS` stays
  exactly `{"antibes", "portocervo"}` as today; WPI ingest is exercised
  against the new UK pack only, where there's no existing hand-curated
  port dict to disturb.

## Judgment calls flagged for sign-off (collected)

1. `ref_lat_deg` as an explicit pack-supplied parameter threaded through
   function signatures, **not** a `Geography.ref_lat_deg` protocol
   property (§1).
2. `LANE_TURN_RATE_NM`/`DEFAULT_MIN_NAVIGABLE_EDGE_FRACTION`/
   `DEFAULT_MIN_REFINEMENT_STEP_NM` classified as Med-tuned search
   heuristics → pack-overridable data, not left as global physics
   constants (§1).
3. `_route_signature`'s Corsica-fallback bug fix scope: fix the labelling
   bug, but leave `CORSICA_LAT_BAND`/`CORSICA_REF_LON` themselves
   Med-specific rather than generalising the whole distinguishing-region
   mechanism into pack data (§2).
4. Corridor-DP gating replaced with `RegionPack.legacy_corridors` even
   though the existing equality check is already provably safe for
   non-Med packs — done for the stated "designed property, not an
   accident" reason, not because of a bug (§3).
5. Multi-pack `Settings` config expressed as a new `region_packs.yaml`
   manifest file (opt-in via one new env var), not N sets of numbered
   flat env vars — the largest new architectural surface in the API layer
   (§4).
6. Shared `ProcessPoolExecutor` with dict-keyed per-pack globals, not a
   dedicated executor pool per pack (§4).
7. Per-vessel favourites persistence: a new, separate SQLite file (not
   reusing `capture/telemetry.sqlite3`'s schema, not a YAML file) — **signed
   off directly by Jack** (§6).
8. Exact UK acceptance endpoint pair, bbox margin, and how much (if any)
   real no-go/TSS research to do for the Channel pack — left to
   implementation time against real chart positions rather than fixed
   here (§8, Acceptance).

**Review outcome: all eight approved as recommended** (Jack, review
round). Three required amendments (corridor registry for YAML
serialisability, no-I/O `MED_PACK` default, `from_pack` bbox consistency
check) and three minor flags (all-three-call-sites dict-keyed weather +
per-pack vessel sync, pack-aware ETag, favourites auth-debt note)
incorporated inline above (§0, §1, §4, §5, §6).

## Verification

- `pytest -m ""` (full suite, all `@pytest.mark.slow` included) green
  after step 3, **before** any `api/` change — isolates the
  zero-behaviour-change proof from API-layer risk.
- `pytest -m ""` green again after every subsequent step.
- `ruff check .` clean throughout.
- `git diff --stat` reviewed at the end of step 3 specifically: every
  touched `core/` file should show new *optional, defaulted* parameters
  only — no removed constants, no changed defaults, no deleted function
  signatures.
- The UK acceptance run (§ Acceptance) is the ticket's real proof and
  must be a genuine `optimise()` call against real ingested data, not a
  mocked/synthetic stand-in — matching this project's own standing bias
  toward real verification runs (B7's live CDS run, the Hetzner deploy's
  live checks) over assumption.

## Real UK South-West acceptance run result (2026-07-14)

Implementation is done; this records what the real run actually found,
matching ticket B7's "Live ERA5 verification result" precedent —
everything below happened against real, live data, not a synthetic
stand-in.

**WPI endpoint, verified live (not guessed):** `GET https://msi.nga.mil/api/publications/world-port-index?output=json`
returns the whole ~2,951-port database (~6.3MB) — confirmed by fetching
the live site's own React bundle and finding its query-builder's real API
path (`publications/world-port-index`), then a live GET. No server-side
bbox/lat-lon filter exists (only `regionName`/`countryName`/`portName`/
`harborSize`) — `ingest/fetch_wpi_ports.py` fetches the full database
once and filters client-side, same shape as every other `ingest/fetch_*.py`
script's `--bbox`. Port coordinates are sexagesimal strings
(`"51°30'00\"N"`) — `_parse_dms` converts them, verified against a known
value (Avonmouth, 51.5, -2.7167) during this run.

**Bbox used:** `(-5.5, 49.8, -3.5, 50.8)` — Plymouth/Falmouth and the
immediate Channel approaches, chosen from real WPI-confirmed port
positions, entirely outside `OPERATING_AREA_BBOX` (6.7–10.15 lon,
40.75–44.0 lat). 12 real named ports found inside it (Plymouth, Falmouth
Harbour, Fowey, Dartmouth, etc.) via a live fetch.

**Real ingest, all four steps, live:**
- GEBCO: `ingest.fetch_gebco --bbox -5.5 49.8 -3.5 50.8` → a 240x480
  bathymetry grid, ~172KB, one live CEDA range-read fetch.
- GSHHG: `ingest.fetch_gshhg` (same bbox, `--bathymetry` pointed at the
  above) → 27 real coastline polygons (748 points), rasterised land mask
  (46,703/115,200 cells land — a real, complex, multi-peninsula/estuary
  coastline, not a simple box).
- No-go/TSS: confirmed directly, as expected from B7 Part 1's own
  finding — no ingest script exists for either. Shipped as honest empty
  `{"zones": []}` files (`data/region_packs/uk_sw/nogo_uk_sw.json`/
  `tss_uk_sw.json`), each with a `note` naming this as unresearched, not
  silently absent.
- Weather: `ingest.fetch_grib_nomads` (same bbox + this pack's own just-
  ingested geography paths) → a real GFS+WW3 cycle (`20260714 06z`,
  48h horizon), landmasked correctly against the UK coastline just
  fetched, not the Med's.

**Two real endpoint findings, both fixed by adjustment rather than
invented data, both documented in `data/region_packs/uk_sw.yaml`'s own
header:**
1. WPI's raw `"plymouth"` pin (50.36667, -4.15) sits on **rasterized
   land** (the breakwater/harbour-front) at this bbox's real GSHHG
   resolution — `_validate_endpoint`'s navigability check rejects it
   unconditionally. Not a bug in `_validate_endpoint` or the ingest —
   a real property of using a facility's raw coordinate as an open-water
   routing endpoint. Fixed by nudging `default_origin` ~0.02° south into
   Plymouth Sound (verified navigable, depth ≈7.25m) — the harbour
   approach a real passage would actually route to.
2. WPI's raw `"falmouth_harbour"` pin (50.15, -5.06667) **is** navigable
   but reads **0.0m GEBCO depth** — a real inshore/tidal shoal at this
   resolution, below the shipped vessel spec's ~4.5m draft+UKC minimum,
   which made the *destination* leg infeasible even for the full
   candidate speed grid (not just the fixed-speed baseline) on the first
   run. Fixed the same way: `default_destination` nudged ~0.02° into the
   Fal estuary approach (verified navigable, depth ≈16.7m).

Neither `ports.plymouth`/`ports.falmouth_harbour` themselves were
touched — real WPI data, left exactly as published; only the pack's own
`default_origin`/`default_destination` differ from the raw pins.
`docs/region-pack-runbook.md` §5 generalises this into standing guidance
for every future pack.

**Lattice-knob finding: none needed.** The UK pack ships the Med's own
tuned `lane_turn_rate_nm=15.0`/`min_navigable_edge_fraction=0.75`/
`min_refinement_step_nm=0.5` unchanged (§1's "no invented UK-specific
number" decision) — `Lattice.refinement_diagnostics` came back empty
(no stage failed to reach the navigability threshold even after
refinement), and once the two endpoint findings above were fixed, the
search succeeded on the very first re-run. This coastline's real
complexity (real islets/headlands/estuaries in the ~37nm straight-line
span) didn't require any different tuning than Bonifacio's — a genuine
empirical result, not assumed in advance.

**Real `optimise()` result** (`tests/test_uk_sw_pack_acceptance.py`,
runs in the fast suite, ~0.5s — small bbox, small lattice):
- 1 candidate: 13.57kn, 3.79h, 51.4nm, ~1007kg fuel, `side=None` —
  confirming the `_route_signature` fix (§2) works correctly for a
  region with no Corsica-like distinguishing feature: no mislabelled
  `"W"`, and the opposite-side secondary search correctly skips itself
  rather than searching for a "side" that isn't a meaningful concept
  here.
- Baseline (fixed 14kn/2-engine reference): 4.91h, 68.7nm — a genuinely
  different, longer route than the optimised candidate, real routing
  behaviour, not a coincidence of a trivial search space.
- Zero diagnostics, `missed_window=False` — a clean feasible result, not
  an edge-case pass.

This is the ticket's actual proof: a real second region pack, generated
entirely through this ticket's own tooling and runbook, producing a real
feasible plan on a passage the Med pack's own geography could never have
served.

### Critical files for implementation

- `core/regionpack.py` (new — `RegionPack`, `from_yaml`, `MED_PACK`)
- `core/corridors.py` (`CORRIDOR_REGISTRY`, amendment 1)
- `data/region_packs/med.yaml`, `data/region_packs.yaml` (new)
- `core/geography.py` (`RealGeography.from_pack` + `_check_pack_bounds`, amendment 3)
- `core/lattice.py`, `core/corridors.py`, `core/legs.py`, `core/optimiser.py`
  (parameter threading, `_route_signature` fix, corridor-gate replacement,
  `region_pack: RegionPack | None = None` on `PlanRequest`, amendment 2)
- `api/schemas.py`, `api/convert.py` (`pack_id` field + parity test)
- `api/config.py` (`region_packs_path`)
- `api/state.py` (dict-keyed worker globals)
- `api/weather_field.py` (bbox param)
- `ingest/fetch_wpi_ports.py` (new)
- `api/favourites.py` (new)
- `deploy/` cron wrapper, `api/weather_sync.py` (multi-pack pull)
- `docs/region-pack-runbook.md` (new)
