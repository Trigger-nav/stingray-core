"""Ticket R1: RegionPack manifest, the corridor-name registry (review
amendment 1), the no-I/O MED_PACK default (amendment 2), and
RealGeography.from_pack's bbox consistency check (amendment 3)."""

import dataclasses
import json

import numpy as np
import pytest

from core.geography import RealGeography
from core.regionpack import MED_PACK, RegionPack


def test_med_yaml_matches_med_pack_constant():
    """The drift guard required by amendment 2: data/region_packs/med.yaml
    (the API/deployment-layer form) and MED_PACK (core/'s zero-I/O
    default) must never diverge in value."""
    loaded = RegionPack.from_yaml("data/region_packs/med.yaml")
    assert loaded == MED_PACK


def test_med_pack_legacy_corridors_resolve_to_real_corridor_functions():
    from core.corridors import PORTS, corridor_east, corridor_west
    from core.regionpack import resolve_corridor

    names = [entry[2] for entry in MED_PACK.legacy_corridors]
    assert names == ["corridor_west", "corridor_east"]
    assert resolve_corridor("corridor_west") is corridor_west
    assert resolve_corridor("corridor_east") is corridor_east
    for origin, destination, _ in MED_PACK.legacy_corridors:
        assert origin == PORTS["antibes"]
        assert destination == PORTS["portocervo"]


def test_from_dict_raises_clear_error_on_unknown_corridor_name():
    raw = {
        "pack_id": "bogus",
        "name": "Bogus",
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "ref_lat_deg": 0.0,
        "coastline_path": "a",
        "bathymetry_path": "b",
        "nogo_path": "c",
        "tss_path": "d",
        "weather_npz_path": "e",
        "legacy_corridors": [
            {"origin": [0.0, 0.0], "destination": [1.0, 1.0], "corridor": "not_a_real_corridor"}
        ],
    }
    with pytest.raises(ValueError, match="not_a_real_corridor"):
        RegionPack.from_dict(raw)


@pytest.fixture
def synthetic_pack_and_files(tmp_path):
    """A tiny real (not SyntheticGeography) grid at a known bbox, plus a
    RegionPack manifest pointing at it -- for from_pack's consistency
    check."""
    coastline_path = tmp_path / "coastline.json"
    coastline_path.write_text(
        json.dumps({"source": "synthetic", "bbox_lon_lat": [], "polygons": []})
    )
    nogo_path = tmp_path / "nogo.json"
    nogo_path.write_text(json.dumps({"zones": []}))
    tss_path = tmp_path / "tss.json"
    tss_path.write_text(json.dumps({"zones": []}))

    bathymetry_path = tmp_path / "bathymetry.npz"
    nlat, nlon = 10, 10
    lat0, dlat, lon0, dlon = 19.0, 0.1, 19.0, 0.1
    np.savez_compressed(
        bathymetry_path,
        lat0=lat0,
        dlat=dlat,
        lon0=lon0,
        dlon=dlon,
        nlat=nlat,
        nlon=nlon,
        elevation_m=np.full((nlat, nlon), -100.0),
    )
    # Grid covers lat 18.95-19.95, lon 18.95-19.95 (half-cell-extended, per
    # RealGeography._check_in_bounds/_check_pack_bounds' own logic).
    correct_bbox = (18.95, 18.95, 19.95, 19.95)
    pack = RegionPack(
        pack_id="synthetic",
        name="Synthetic test pack",
        bbox=correct_bbox,
        ref_lat_deg=19.0,
        coastline_path=str(coastline_path),
        bathymetry_path=str(bathymetry_path),
        nogo_path=str(nogo_path),
        tss_path=str(tss_path),
        weather_npz_path="unused.npz",
    )
    return pack


def test_from_pack_succeeds_when_bbox_matches_loaded_grid(synthetic_pack_and_files):
    geo = RealGeography.from_pack(synthetic_pack_and_files)
    assert geo.depth_m(19.5, 19.5) == pytest.approx(100.0)


def test_from_pack_raises_loudly_on_bbox_mismatch(synthetic_pack_and_files):
    """Amendment 3's actual regression test: a pack whose bbox field
    doesn't match what its data files actually cover must fail loudly at
    load time, not as silent downstream infeasibility."""
    mismatched = dataclasses.replace(synthetic_pack_and_files, bbox=(0.0, 0.0, 1.0, 1.0))
    with pytest.raises(ValueError, match="does not match the loaded grid"):
        RealGeography.from_pack(mismatched)


def test_from_pack_on_real_med_pack_does_not_raise():
    RealGeography.from_pack(MED_PACK)
