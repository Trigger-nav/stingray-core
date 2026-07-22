"""Ticket R1's actual acceptance proof: a real second region pack (UK
South-West / Channel Approaches), generated via docs/region-pack-runbook.md
using real GEBCO/GSHHG geography and real NOMADS weather (ingested live
during this ticket, 2026-07-14 -- see data/region_packs/uk_sw.yaml's own
header for the exact commands and two real endpoint-adjustment findings),
producing a feasible `optimise()` plan on a passage the Med pack's own
`OPERATING_AREA_BBOX` could never serve (Plymouth/Falmouth, entirely
outside 6.7-10.15 lon / 40.75-44.0 lat).

Geography/ports are committed (static, like the Med's own data); the
weather npz is gitignored (goes stale within hours, same as
`data/weather/*.npz`) -- this test **skips**, not fails, when it's
missing, since regenerating it needs a real network fetch this suite
doesn't want to depend on. Run docs/region-pack-runbook.md's weather
step (`ingest.fetch_grib_nomads --bbox ... --out
data/region_packs/uk_sw/weather_uk_sw.npz ...`) to produce it locally.
"""

from __future__ import annotations

import os

import pytest

from core.geography import OPERATING_AREA_BBOX, RealGeography
from core.optimiser import PlanRequest, optimise
from core.regionpack import RegionPack
from core.units import distance_m, m_to_nm
from core.vessel_spec import VesselSpec
from core.weather import GriddedWeatherField

PACK_YAML = "data/region_packs/uk_sw.yaml"


@pytest.fixture
def uk_sw_pack():
    pack = RegionPack.from_yaml(PACK_YAML)
    if not os.path.exists(pack.weather_npz_path):
        pytest.skip(
            f"{pack.weather_npz_path} not present (gitignored, goes stale) -- "
            "run docs/region-pack-runbook.md's weather step to regenerate"
        )
    return pack


def test_uk_sw_bbox_is_disjoint_from_the_med_pack():
    """The load-bearing premise: this genuinely is a passage the Med
    pack's own bbox could never serve, not just a different point inside
    the same operating area."""
    pack = RegionPack.from_yaml(PACK_YAML)
    lon_min, lat_min, lon_max, lat_max = pack.bbox
    med_lon_min, med_lat_min, med_lon_max, med_lat_max = OPERATING_AREA_BBOX
    assert lon_max < med_lon_min or lon_min > med_lon_max
    assert pack.pack_id != "med"


def test_uk_sw_pack_from_pack_passes_bbox_consistency_check(uk_sw_pack):
    RealGeography.from_pack(uk_sw_pack)  # raises on mismatch -- amendment 3


def test_uk_sw_pack_produces_a_feasible_plan_end_to_end(uk_sw_pack):
    """The ticket's real acceptance criterion: a genuine `optimise()`
    call against real ingested geography/weather, not a mocked stand-in,
    producing a feasible plan (at least one candidate, no missed-window
    diagnostic)."""
    geography = RealGeography.from_pack(uk_sw_pack)
    weather = GriddedWeatherField.from_npz(uk_sw_pack.weather_npz_path)
    vessel = VesselSpec.from_yaml("data/vessel_specs/mys_50m_default.yaml")

    request = PlanRequest(
        weather=weather,
        geography=geography,
        vessel=vessel,
        pace=50,
        comfort=50,
        origin=uk_sw_pack.default_origin,
        destination=uk_sw_pack.default_destination,
        region_pack=uk_sw_pack,
    )
    result = optimise(request)

    assert len(result.candidates) >= 1
    assert not result.missed_window
    for c in result.candidates:
        assert c.duration_h > 0
        assert c.distance_nm > 0
        # No Corsica-like distinguishing region exists for this pack --
        # _route_signature's R1 fix must return None, not a mislabelled
        # "W" (the exact bug this ticket found and fixed).
        assert c.side is None
    assert result.baseline.duration_h > 0


def test_uk_sw_pack_detour_ratio_stays_reasonable(uk_sw_pack):
    """Ticket L1: a regression guard against the dog-leg
    `docs/plans/ticket-C1.md`'s own diagnostic found (detour ratio 1.398
    on this exact origin/destination pair) reappearing -- L1's derived
    lane spacing (`core/lattice.py`'s `CROSS_TRACK_STEP_FRACTION`) brought
    the real measured ratio down to ~1.10 (`docs/plans/ticket-L1.md`'s own
    acceptance run). 1.30 is deliberately generous, not the tight
    measured value -- weather (hence current, hence which candidate speed/
    route the search settles on) varies with whatever the locally
    regenerated `weather_uk_sw.npz` happens to hold when this test runs
    (gitignored, goes stale, per this file's own module docstring), so a
    tight assertion would be flaky by design; this only guards against the
    lattice-geometry regression actually found, not against ordinary
    weather-driven variation."""
    geography = RealGeography.from_pack(uk_sw_pack)
    weather = GriddedWeatherField.from_npz(uk_sw_pack.weather_npz_path)
    vessel = VesselSpec.from_yaml("data/vessel_specs/mys_50m_default.yaml")

    request = PlanRequest(
        weather=weather,
        geography=geography,
        vessel=vessel,
        pace=50,
        comfort=50,
        origin=uk_sw_pack.default_origin,
        destination=uk_sw_pack.default_destination,
        region_pack=uk_sw_pack,
    )
    result = optimise(request)

    straight_nm = m_to_nm(
        distance_m(
            uk_sw_pack.default_origin, uk_sw_pack.default_destination, uk_sw_pack.ref_lat_deg
        )
    )
    assert result.candidates
    for c in result.candidates:
        detour_ratio = c.distance_nm / straight_nm
        assert detour_ratio < 1.30, (
            f"candidate {c.corridor_name!r} detour_ratio={detour_ratio:.4f} "
            f"(distance_nm={c.distance_nm:.2f}, straight_nm={straight_nm:.2f})"
        )
