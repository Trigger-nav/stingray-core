# Region pack generation runbook (ticket R1)

How to generate a complete new region pack, end to end, using the same
bbox-parametric ingest scripts B7 Part 1 built. Mirrors
`docs/historical-import-runbook.md`'s precedent (copy-paste sequence,
every step says what it's for) and reuses its GEBCO→GSHHG→weather
ordering directly — the two pieces that runbook doesn't cover (no-go/TSS,
named ports) are new here. `data/region_packs/uk_sw.yaml` (this ticket's
own real second pack, Plymouth–Falmouth) is a worked example of every
step below — see its own header comment for the exact commands run and
two real findings from generating it.

`<pack>` below is any short pack id (used as a directory/filename
fragment, and as `RegionPack.pack_id` — must be unique across your
deployment's `region_packs.yaml`).

## 1. Choose a bbox

Pick two real endpoints (a chart, port coordinates, or a real passage you
care about) and a covering bbox with margin — the same shape
`core.track.covering_bbox` uses for a historical track (B7 Part 1), just
picked by hand here since there's no track to derive it from
automatically. `lon_min lat_min lon_max lat_max`, matching every ingest
script's `--bbox` convention.

## 2. Geography — GEBCO then GSHHG (reuse verbatim)

Ordering matters: GEBCO first (GSHHG rasterises its land mask onto
whatever bathymetry grid GEBCO wrote).

```
mkdir -p data/region_packs/<pack>

python3 -m ingest.fetch_gebco --bbox LON_MIN LAT_MIN LON_MAX LAT_MAX \
  --out data/region_packs/<pack>/bathymetry_<pack>.npz

python3 -m ingest.fetch_gshhg --bbox LON_MIN LAT_MIN LON_MAX LAT_MAX \
  --out data/region_packs/<pack>/coastline_<pack>.json \
  --bathymetry data/region_packs/<pack>/bathymetry_<pack>.npz
```

Both scripts refuse to run with a bbox differing from
`core.geography.OPERATING_AREA_BBOX` unless `--out` (and, for
`fetch_gshhg.py`, `--bathymetry`) is also given explicitly — the guard
against silently overwriting the committed western-Med files.

## 3. No-go / TSS zones — a manual research step, not a script

**There is no ingest script for this** — `data/geography/nogo_western_med.json`/
`tss_western_med.json` were hand-authored with cited real sources
(marineregions.org MRGIDs) during ticket 0.8, not fetched. For a new
pack:

- If you know of real no-go zones or IMO-adopted TSS lanes for this
  region, cite your source in each zone's `gazetteer_source`/
  `gazetteer_name` fields, and be honest about precision — set
  `precise_boundary_verified: false` with a `caveat` string if you're
  working from an approximate/secondary source rather than the primary
  chart or IMO publication (exactly ticket 0.8's own placeholder-TSS
  precedent).
- Otherwise, ship an honest empty file:

```json
{"source": "none -- no zones researched for this pack yet", "note": "...", "zones": []}
```

An empty `zones` list needs zero code changes to `core.geography._load_nogo_polygons`
— confirmed directly, this is a fully supported, non-degraded state, not
a placeholder that breaks anything.

## 4. Weather (reuse verbatim)

Same `--bbox`-parametric fetchers B7 Part 1 built, landmasking against
*this pack's own* just-ingested geography (not the Med defaults):

```
python3 -m ingest.fetch_grib_nomads --bbox LON_MIN LAT_MIN LON_MAX LAT_MAX \
  --out data/region_packs/<pack>/weather_<pack>.npz \
  --coastline-path data/region_packs/<pack>/coastline_<pack>.json \
  --bathymetry-path data/region_packs/<pack>/bathymetry_<pack>.npz \
  --nogo-path data/region_packs/<pack>/nogo_<pack>.json \
  --tss-path data/region_packs/<pack>/tss_<pack>.json
```

(`fetch_grib_ecmwf.py` takes the same flags, for the 3-hourly ECMWF
source — see `deploy/README.md`'s cadence notes.)

**Not committed to the repo** — a region pack's weather npz goes stale
within hours, same as `data/weather/*.npz` (see `.gitignore`); regenerate
it locally (or let cloud-role cron do it, §7 below) rather than expecting
a committed copy to still be current.

## 5. Named ports (WPI)

```
python3 -m ingest.fetch_wpi_ports --bbox LON_MIN LAT_MIN LON_MAX LAT_MAX \
  --out data/region_packs/<pack>/ports_<pack>.json
```

Real, live NGA World Port Index data (`msi.nga.mil`, no authentication) —
verified against the real endpoint during this ticket. There's no
server-side bbox filter (confirmed by inspecting the live site's own
query-builder UI), so this fetches the whole ~3,000-port database once
(~6MB) and filters to your bbox client-side; a bbox with zero WPI ports
inside it is valid (a pack can rely on arbitrary endpoints/favourites
alone), just double-check the bbox is right if that surprises you.

**A raw named-port coordinate is a facility location, not automatically
a validated open-water routing endpoint** — found empirically generating
the UK South-West pack: WPI's raw "plymouth" pin sat on rasterized land
(the breakwater) at real GSHHG resolution, and "falmouth_harbour"'s pin,
while navigable, read 0.0m GEBCO depth (an inshore shoal). Before using
any WPI port as a pack's `default_origin`/`default_destination`, verify
it's actually navigable (`Geography.is_navigable`) and deep enough
(`Geography.depth_m` >= your vessel spec's draft + UKC) — nudge to a
nearby harbour-approach point if not, the same way a real passage would
route to the fairway, not the quay (`data/region_packs/uk_sw.yaml`'s own
header has the exact adjustment made and why).

## 6. Currents (optional, CMEMS — ticket C1)

`current_u_ms`/`current_v_ms` default to zero (exactly ticket B2's
original behaviour) unless a pack opts in — worth doing whenever the
region has current/tidal-stream speeds that are a real fraction of your
vessel's cruising speed (the UK South-West pack's whole reason for
existing), not worth the recurring fetch cost otherwise (this repo's own
Med pack stays off — `docs/plans/ticket-C1.md` §5 has the reasoning).

**One-time setup**: a free Copernicus Marine Service account
(https://data.marine.copernicus.eu), then set
`COPERNICUSMARINE_SERVICE_USERNAME`/`COPERNICUSMARINE_SERVICE_PASSWORD`
(env vars, this repo's preferred form — `deploy/.env.example`) or run
`copernicusmarine login` once per machine. Same shape as the CDS/
`~/.cdsapirc` setup ticket B7's ERA5 annotator needs — cannot be
automated or tested in CI.

**Find the real dataset id for your region** — do not guess or reuse the
UK South-West pack's id for a different area. `copernicusmarine.describe(dataset_id=...)`
is unauthenticated and confirms the real variable names/coverage before
you commit to an id; `docs/plans/ticket-C1.md` §2 has the live-verified
process (inspecting the product's own STAC metadata,
`admp_retired_date`, etc.) that found `cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i`
for the UK pack.

```
python3 -m ingest.fetch_currents_cmems --bbox LON_MIN LAT_MIN LON_MAX LAT_MAX \
  --dataset-id <real dataset id, confirmed via describe()> \
  --out data/region_packs/<pack>/currents_<pack>.npz \
  --coastline-path data/region_packs/<pack>/coastline_<pack>.json \
  --bathymetry-path data/region_packs/<pack>/bathymetry_<pack>.npz \
  --nogo-path data/region_packs/<pack>/nogo_<pack>.json \
  --tss-path data/region_packs/<pack>/tss_<pack>.json

python3 -m ingest.merge_currents \
  --weather-npz data/region_packs/<pack>/weather_<pack>.npz \
  --currents-npz data/region_packs/<pack>/currents_<pack>.npz \
  --pack-id <pack>
```

Ordering matters: step 4's weather fetch must already have written
`weather_<pack>.npz` before this runs — `merge_currents.py` rewrites that
file in place, current fields only, everything else untouched. Set
`currents_dataset_id` in the pack's manifest (step 7) to have
`ingest/fetch_all_packs.py`'s cron orchestration run both of these
automatically every cycle — a currents-step failure there is isolated
from the wind/wave fetch (logged at `WARNING`, doesn't block that pack's
weather from staying fresh).

## 7. Assemble the manifest

Write `data/region_packs/<pack>.yaml` (see `data/region_packs/uk_sw.yaml`
or `data/region_packs/med.yaml` for the exact schema — `RegionPack.from_yaml`,
`core/regionpack.py`), pointing at every file from steps 2-6, with
`default_origin`/`default_destination` set to a verified-navigable pair
(step 5's caveat). Lattice search knobs (`lane_turn_rate_nm`,
`min_navigable_edge_fraction`, `min_refinement_step_nm`) default to the
Med's own tuned values if omitted — there's no principled reason to
assume those are right for a different coastline's degradation profile,
but no invented region-specific number either; start from the defaults
and treat any resulting search infeasibility as a real finding to
investigate, not something to silently tune away. `currents_dataset_id`
(step 6) defaults to unset — omit it entirely for a pack that doesn't
model currents.

Add the new manifest's path to your deployment's `data/region_packs.yaml`
(or the committed example, if this pack should ship by default) so
`Settings.region_packs_path`-configured instances pick it up.

## 8. Validate

Run `optimise()` end-to-end against the new pack with a real
origin/destination inside it and confirm a feasible plan — the concrete
template:

```python
from core.geography import RealGeography
from core.optimiser import PlanRequest, optimise
from core.regionpack import RegionPack
from core.vessel_spec import VesselSpec
from core.weather import GriddedWeatherField

pack = RegionPack.from_yaml("data/region_packs/<pack>.yaml")
geography = RealGeography.from_pack(pack)  # raises loudly on a bbox/data mismatch
weather = GriddedWeatherField.from_npz(pack.weather_npz_path)
vessel = VesselSpec.from_yaml("data/vessel_specs/mys_50m_default.yaml")

request = PlanRequest(
    weather=weather, geography=geography, vessel=vessel,
    pace=50, comfort=50,
    origin=pack.default_origin, destination=pack.default_destination,
    region_pack=pack,
)
result = optimise(request)
assert result.candidates and not result.missed_window
```

This is exactly `tests/test_uk_sw_pack_acceptance.py`'s own shape — the
reusable validation template for *every* future pack, not a one-off
proof specific to the UK South-West pack.

Once weather is fetched, add the pack to your cloud role's cron (or
`ingest.fetch_all_packs --packs-manifest data/region_packs.yaml`,
`deploy/README.md`'s "Multi-pack deployments" note) so it stays current
without a manual re-fetch every time.
