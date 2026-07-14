"""Ticket R1: per-vessel saved favourites (api/favourites.py's SQLite
store + api/routes.py's GET/POST/DELETE /v1/favourites), through the real
FastAPI app.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app

AUTH = ("u", "p")


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        role="vessel",
        weather_npz_path="data/weather/ecmwf_western_med.npz",
        pool_size=1,
        auth_user="u",
        auth_password="p",
        favourites_db_path=str(tmp_path / "favourites.sqlite3"),
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_list_favourites_empty_for_a_new_vessel(client):
    r = client.get("/v1/favourites", params={"vessel_id": "v1"}, auth=AUTH)
    assert r.status_code == 200
    assert r.json() == []


def test_create_and_list_favourite_round_trips(client):
    body = {"name": "Antibes", "lat_deg": 43.55, "lon_deg": 7.17, "pack_id": "med"}
    created = client.post("/v1/favourites", params={"vessel_id": "v1"}, json=body, auth=AUTH)
    assert created.status_code == 201
    fav = created.json()
    assert fav["name"] == "Antibes"
    assert fav["vessel_id"] == "v1"
    assert fav["is_anchorage"] is False

    listed = client.get("/v1/favourites", params={"vessel_id": "v1"}, auth=AUTH)
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == fav["id"]


def test_favourites_are_scoped_per_vessel(client):
    body = {"name": "Antibes", "lat_deg": 43.55, "lon_deg": 7.17}
    client.post("/v1/favourites", params={"vessel_id": "v1"}, json=body, auth=AUTH)
    r = client.get("/v1/favourites", params={"vessel_id": "v2"}, auth=AUTH)
    assert r.json() == []


def test_create_favourite_with_unknown_pack_id_returns_422(client):
    body = {"name": "Nowhere", "lat_deg": 0.0, "lon_deg": 0.0, "pack_id": "nonexistent"}
    r = client.post("/v1/favourites", params={"vessel_id": "v1"}, json=body, auth=AUTH)
    assert r.status_code == 422


def test_delete_favourite_removes_it(client):
    body = {"name": "Antibes", "lat_deg": 43.55, "lon_deg": 7.17}
    created = client.post("/v1/favourites", params={"vessel_id": "v1"}, json=body, auth=AUTH)
    fav_id = created.json()["id"]

    deleted = client.delete(f"/v1/favourites/{fav_id}", params={"vessel_id": "v1"}, auth=AUTH)
    assert deleted.status_code == 204

    listed = client.get("/v1/favourites", params={"vessel_id": "v1"}, auth=AUTH)
    assert listed.json() == []


def test_delete_favourite_scoped_to_vessel_returns_404_for_another_vessel(client):
    body = {"name": "Antibes", "lat_deg": 43.55, "lon_deg": 7.17}
    created = client.post("/v1/favourites", params={"vessel_id": "v1"}, json=body, auth=AUTH)
    fav_id = created.json()["id"]

    r = client.delete(f"/v1/favourites/{fav_id}", params={"vessel_id": "v2"}, auth=AUTH)
    assert r.status_code == 404

    # still there for the real owner
    listed = client.get("/v1/favourites", params={"vessel_id": "v1"}, auth=AUTH)
    assert len(listed.json()) == 1


def test_delete_unknown_favourite_returns_404(client):
    r = client.delete("/v1/favourites/does-not-exist", params={"vessel_id": "v1"}, auth=AUTH)
    assert r.status_code == 404
