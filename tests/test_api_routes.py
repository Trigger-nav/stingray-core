"""FastAPI TestClient contract tests across the full endpoint table
(ticket B1 design 4). Short custom routes throughout for speed -- see
tests/test_api_jobs.py's module docstring for why.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app

AUTH = ("u", "p")


def _settings(role: str = "vessel", **overrides) -> Settings:
    defaults = dict(
        role=role,
        weather_npz_path="data/weather/ecmwf_western_med.npz",
        pool_size=1,
        auth_user="u",
        auth_password="p",
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture
def client():
    app = create_app(_settings())
    with TestClient(app) as c:
        yield c


@pytest.fixture
def cloud_client():
    app = create_app(_settings(role="cloud"))
    with TestClient(app) as c:
        yield c


def _submit_short_route(client, **overrides):
    body = {
        "pace": 50,
        "comfort": 50,
        "origin": {"lat_deg": 43.0, "lon_deg": 7.9},
        "destination": {"lat_deg": 42.8, "lon_deg": 8.1},
        "speeds_kn": [12.0],
    }
    body.update(overrides)
    return client.post("/v1/plans", json=body, auth=AUTH)


def _poll_until_finished(client, job_id, timeout_s=15.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = client.get(f"/v1/plans/{job_id}", auth=AUTH)
        if r.json()["status"] in ("done", "failed"):
            return r
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish within {timeout_s}s")


def test_health_reports_role_and_schema_version(client):
    r = client.get("/v1/health", auth=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["role"] == "vessel"
    assert body["schema_version"]


def test_vessel_returns_the_loaded_default_spec(client):
    r = client.get("/v1/vessel", auth=AUTH)
    assert r.status_code == 200
    assert r.json()["name"]
    assert "hull" in r.json()


def test_submit_plan_returns_202_with_job_descriptor(client):
    r = _submit_short_route(client)
    assert r.status_code == 202
    body = r.json()
    assert body["job_id"]
    assert body["status"] in ("queued", "running")


def test_submit_plan_invalid_origin_returns_422(client):
    r = _submit_short_route(client, origin={"lat_deg": 42.3, "lon_deg": 9.0})
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_request"
    assert "not navigable" in r.json()["message"]


def test_poll_unknown_job_returns_404(client):
    r = client.get("/v1/plans/does-not-exist", auth=AUTH)
    assert r.status_code == 404


def test_poll_reaches_done_with_a_full_plan_result(client):
    submitted = _submit_short_route(client)
    job_id = submitted.json()["job_id"]
    r = _poll_until_finished(client, job_id)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["result"] is not None
    assert body["result"]["candidates"]
    assert body["error"] is None


def test_cloud_role_serves_weather_npz(cloud_client):
    r = cloud_client.get("/v1/weather/latest.npz", auth=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert len(r.content) > 0
    assert "last-modified" in {k.lower() for k in r.headers}


def test_vessel_role_does_not_serve_weather_npz(client):
    r = client.get("/v1/weather/latest.npz", auth=AUTH)
    assert r.status_code == 404


def test_submit_plan_returns_429_when_queue_is_full():
    app = create_app(_settings(max_queue_depth=1))
    with TestClient(app) as client:
        first = _submit_short_route(client)
        assert first.status_code == 202
        second = _submit_short_route(client)
        assert second.status_code == 429
        assert second.json()["code"] == "queue_full"
        assert second.headers.get("retry-after")


def test_weather_field_returns_a_downsampled_grid(client):
    r = client.get("/v1/weather/field?h=3", auth=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["valid_time_h"] == 3.0
    assert len(body["hs_m"]) == body["nlat"]
    assert len(body["hs_m"][0]) == body["nlon"]
    assert "etag" in {k.lower() for k in r.headers}


def test_weather_field_conditional_get_returns_304(client):
    first = client.get("/v1/weather/field?h=3", auth=AUTH)
    etag = first.headers["etag"]
    second = client.get("/v1/weather/field?h=3", headers={"If-None-Match": etag}, auth=AUTH)
    assert second.status_code == 304
    assert second.content == b""


def test_weather_field_quantizes_h_to_the_nearest_hour(client):
    a = client.get("/v1/weather/field?h=3.0", auth=AUTH)
    b = client.get("/v1/weather/field?h=3.4", auth=AUTH)
    assert a.json()["valid_time_h"] == b.json()["valid_time_h"] == 3.0
    assert a.headers["etag"] == b.headers["etag"]
