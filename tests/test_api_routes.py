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


def _poll_until_finished(client, job_id, timeout_s=150.0):
    # 150s, not 15s: a real optimise() job costs ~19s cold on a fast dev
    # machine and several times that on a 2-core CI runner (see
    # tests/test_api_jobs.py's own note and CLAUDE.md's B1 profiling
    # gotcha). Still bounded so a genuine hang fails rather than stalls.
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


def test_health_currents_provenance_is_null_when_not_modelled(client):
    # ticket C1: the default (Med, single-pack) test client's committed
    # weather npz has no currents provenance -- must read null, not be
    # missing/error, and must not be confused with "modelled but zero".
    r = client.get("/v1/health", auth=AUTH)
    body = r.json()
    assert body["currents_source"] is None
    assert body["currents_cycle"] is None
    assert body["currents_fetched"] is None


def test_health_coastal_fill_counts_are_null_for_a_pre_w1_npz(client):
    # ticket W1: the committed Med test npz predates coastal fill --
    # must read null (not modelled/not present), not 0 or a KeyError.
    r = client.get("/v1/health", auth=AUTH)
    body = r.json()
    assert body["wave_filled_cells"] is None
    assert body["current_filled_cells"] is None


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


def test_submit_plan_distill_false_round_trips_to_undistilled_result(client):
    """Ticket S1's required amendment: `distill: false` in the request
    body must reach `optimise()` as a real `PlanRequest(..., distill=False)`
    -- proven end to end through the real API surface, not just the
    schema-parity field-name mirror. Deliberately does *not* use this
    module's short-route convention (`_submit_short_route`'s
    (43.0,7.9)->(42.8,8.1) pair) -- verified directly against
    `core.optimiser.optimise()` that it already collapses to a minimal
    3-waypoint track regardless of `distill`, so it can't discriminate
    on/off. The default Antibes<->Porto Cervo passage (this API's own
    default origin/destination when omitted) has real removable kinks
    (15 waypoints undistilled vs 6 distilled, at `speeds_kn=[12.0]` --
    measured directly), so it's the pair actually exercised here; a single
    fixed speed keeps the corridor-DP grid small enough to stay fast."""
    body = {"pace": 50, "comfort": 50, "speeds_kn": [12.0], "distill": False}
    submitted = client.post("/v1/plans", json=body, auth=AUTH)
    assert submitted.status_code == 202
    r = _poll_until_finished(client, submitted.json()["job_id"], timeout_s=300.0)
    result = r.json()["result"]
    assert result is not None
    api_wp_counts = sorted(len(c["track"]) for c in result["candidates"])

    from core.geography import RealGeography
    from core.optimiser import DEFAULT_DESTINATION, DEFAULT_ORIGIN, PlanRequest, optimise
    from core.vessel_spec import VesselSpec
    from core.weather import GriddedWeatherField

    vessel = VesselSpec.from_yaml("data/vessel_specs/mys_50m_default.yaml")
    geo = RealGeography()
    wx = GriddedWeatherField.from_npz("data/weather/ecmwf_western_med.npz")
    common = dict(
        weather=wx,
        geography=geo,
        vessel=vessel,
        pace=50,
        comfort=50,
        origin=DEFAULT_ORIGIN,
        destination=DEFAULT_DESTINATION,
        speeds_kn=(12.0,),
    )
    direct_undistilled = optimise(PlanRequest(**common, distill=False))
    direct_distilled = optimise(PlanRequest(**common, distill=True))

    # The real round-trip: the API's distill=False result matches a direct,
    # genuinely-undistilled optimise() call...
    assert api_wp_counts == sorted(len(c.track) for c in direct_undistilled.candidates)
    # ...and, the discriminating check, differs from a distilled one --
    # proving distill=False actually suppressed distillation rather than
    # both paths coincidentally producing the same (already-minimal) track.
    assert api_wp_counts != sorted(len(c.track) for c in direct_distilled.candidates)
