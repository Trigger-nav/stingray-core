"""Ticket R1: multi-pack API-layer plumbing -- a second, fabricated
"b" pack alongside the real "med" one (loaded via `region_packs_path`),
exercising `pack_id` resolution end-to-end through the real FastAPI app.
Pack "b"'s geography/weather content don't need to be scientifically
meaningful (a tiny synthetic land-free grid, the real committed Med npz
reused verbatim as its weather file) -- this suite tests plumbing
(routing/ETag/404s), not physics; the real UK pack's physics-meaningful
end-to-end run is docs/plans/ticket-R1.md's separate acceptance test.
"""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app

AUTH = ("u", "p")


def _write_synthetic_pack_b(tmp_path):
    coastline_path = tmp_path / "coastline_b.json"
    coastline_path.write_text(
        json.dumps({"source": "synthetic", "bbox_lon_lat": [], "polygons": []})
    )
    nogo_path = tmp_path / "nogo_b.json"
    nogo_path.write_text(json.dumps({"zones": []}))
    tss_path = tmp_path / "tss_b.json"
    tss_path.write_text(json.dumps({"zones": []}))

    bathymetry_path = tmp_path / "bathymetry_b.npz"
    nlat, nlon = 10, 10
    np.savez_compressed(
        bathymetry_path,
        lat0=19.0,
        dlat=0.1,
        lon0=19.0,
        dlon=0.1,
        nlat=nlat,
        nlon=nlon,
        elevation_m=np.full((nlat, nlon), -100.0),
    )

    weather_b_path = tmp_path / "weather_b.npz"
    shutil.copy("data/weather/ecmwf_western_med.npz", weather_b_path)

    pack_b_yaml = tmp_path / "pack_b.yaml"
    pack_b_yaml.write_text(
        yaml.dump(
            {
                "pack_id": "b",
                "name": "Pack B (synthetic, test-only)",
                "bbox": [18.95, 18.95, 19.95, 19.95],
                "ref_lat_deg": 19.0,
                "coastline_path": str(coastline_path),
                "bathymetry_path": str(bathymetry_path),
                "nogo_path": str(nogo_path),
                "tss_path": str(tss_path),
                "weather_npz_path": str(weather_b_path),
                "default_origin": [19.5, 19.3],
                "default_destination": [19.3, 19.5],
            }
        )
    )

    packs_manifest = tmp_path / "region_packs.yaml"
    packs_manifest.write_text(
        yaml.dump({"packs": ["data/region_packs/med.yaml", str(pack_b_yaml)]})
    )
    return str(packs_manifest)


@pytest.fixture
def two_pack_settings(tmp_path):
    packs_path = _write_synthetic_pack_b(tmp_path)
    return Settings(
        role="vessel",
        pool_size=1,
        auth_user="u",
        auth_password="p",
        region_packs_path=packs_path,
    )


@pytest.fixture
def two_pack_client(two_pack_settings):
    app = create_app(two_pack_settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def two_pack_cloud_client(tmp_path):
    packs_path = _write_synthetic_pack_b(tmp_path)
    settings = Settings(
        role="cloud",
        pool_size=1,
        auth_user="u",
        auth_password="p",
        region_packs_path=packs_path,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_submit_plan_against_a_non_med_pack_by_default_endpoints(two_pack_client):
    r = two_pack_client.post(
        "/v1/plans", json={"pace": 50, "comfort": 50, "pack_id": "b"}, auth=AUTH
    )
    assert r.status_code == 202


def test_submit_plan_with_unknown_pack_id_returns_422(two_pack_client):
    r = two_pack_client.post(
        "/v1/plans", json={"pace": 50, "comfort": 50, "pack_id": "nonexistent"}, auth=AUTH
    )
    assert r.status_code == 422


def test_submit_plan_omitted_pack_id_still_defaults_to_med(two_pack_client):
    r = two_pack_client.post(
        "/v1/plans",
        json={
            "pace": 50,
            "comfort": 50,
            "origin": {"lat_deg": 43.0, "lon_deg": 7.9},
            "destination": {"lat_deg": 42.8, "lon_deg": 8.1},
            "speeds_kn": [12.0],
        },
        auth=AUTH,
    )
    assert r.status_code == 202


def test_weather_field_etag_differs_between_packs_at_the_same_hour(two_pack_client):
    med = two_pack_client.get("/v1/weather/field?h=3&pack=med", auth=AUTH)
    b = two_pack_client.get("/v1/weather/field?h=3&pack=b", auth=AUTH)
    assert med.status_code == 200
    assert b.status_code == 200
    assert med.headers["ETag"] != b.headers["ETag"]


def test_weather_field_etag_from_one_pack_does_not_304_another_pack(two_pack_client):
    med = two_pack_client.get("/v1/weather/field?h=3&pack=med", auth=AUTH)
    med_etag = med.headers["ETag"]
    b = two_pack_client.get(
        "/v1/weather/field?h=3&pack=b", headers={"If-None-Match": med_etag}, auth=AUTH
    )
    assert b.status_code == 200


def test_weather_field_unknown_pack_returns_422(two_pack_client):
    r = two_pack_client.get("/v1/weather/field?pack=nonexistent", auth=AUTH)
    assert r.status_code == 422


def test_weather_field_default_pack_is_med(two_pack_client):
    default = two_pack_client.get("/v1/weather/field?h=3", auth=AUTH)
    med = two_pack_client.get("/v1/weather/field?h=3&pack=med", auth=AUTH)
    assert default.json() == med.json()


def test_latest_npz_serves_the_requested_pack(two_pack_cloud_client):
    med = two_pack_cloud_client.get("/v1/weather/latest.npz?pack=med", auth=AUTH)
    b = two_pack_cloud_client.get("/v1/weather/latest.npz?pack=b", auth=AUTH)
    assert med.status_code == 200
    assert b.status_code == 200


def test_latest_npz_unknown_pack_returns_422(two_pack_cloud_client):
    r = two_pack_cloud_client.get("/v1/weather/latest.npz?pack=nonexistent", auth=AUTH)
    assert r.status_code == 422
