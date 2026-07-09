"""HTTP Basic Auth tests (ticket B1 design 9) -- missing/wrong credentials
must be rejected on every endpoint, uniformly."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app

CORRECT = ("u", "p")
WRONG_PASSWORD = ("u", "wrong")
WRONG_USER = ("someone_else", "p")


@pytest.fixture
def client():
    settings = Settings(
        role="cloud",
        weather_npz_path="data/weather/ecmwf_western_med.npz",
        pool_size=1,
        auth_user="u",
        auth_password="p",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


ENDPOINTS = [
    ("GET", "/v1/health"),
    ("GET", "/v1/vessel"),
    ("GET", "/v1/plans/some-id"),
    ("GET", "/v1/weather/latest.npz"),
    ("GET", "/v1/telemetry/status"),
]


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_missing_credentials_are_rejected(client, method, path):
    r = client.request(method, path)
    assert r.status_code == 401


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_wrong_password_is_rejected(client, method, path):
    r = client.request(method, path, auth=WRONG_PASSWORD)
    assert r.status_code == 401


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_wrong_username_is_rejected(client, method, path):
    r = client.request(method, path, auth=WRONG_USER)
    assert r.status_code == 401


def test_post_plans_without_auth_is_rejected(client):
    r = client.post("/v1/plans", json={"pace": 50, "comfort": 50})
    assert r.status_code == 401


def test_correct_credentials_are_accepted(client):
    r = client.get("/v1/health", auth=CORRECT)
    assert r.status_code == 200
