"""CORS tests (ticket B2 design 7) -- the demo UI is a browser client on a
different origin than the API. Covers `/v1/health` (a plain endpoint) and
`/v1/weather/field` (the amendment endpoint the user explicitly asked to
extend this file for -- drawWx()'s heatmap/wind-layer data source).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app

ALLOWED_ORIGIN = "http://localhost:8080"
DISALLOWED_ORIGIN = "http://evil.example.com"
AUTH = ("u", "p")


@pytest.fixture
def client():
    settings = Settings(
        role="vessel",
        weather_npz_path="data/weather/ecmwf_western_med.npz",
        pool_size=1,
        auth_user="u",
        auth_password="p",
        cors_origins=(ALLOWED_ORIGIN,),
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def _preflight(client, path: str, origin: str, method: str = "GET"):
    return client.options(
        path,
        headers={"Origin": origin, "Access-Control-Request-Method": method},
    )


@pytest.mark.parametrize("path", ["/v1/health", "/v1/weather/field"])
def test_preflight_from_allowed_origin_is_accepted(client, path):
    r = _preflight(client, path, ALLOWED_ORIGIN)
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
    assert "authorization" in r.headers.get("access-control-allow-headers", "").lower()


@pytest.mark.parametrize("path", ["/v1/health", "/v1/weather/field"])
def test_preflight_from_disallowed_origin_gets_no_allow_header(client, path):
    r = _preflight(client, path, DISALLOWED_ORIGIN)
    # starlette's CORS middleware answers a disallowed-origin preflight
    # with 400 and no Access-Control-Allow-Origin header -- default-deny,
    # not a wildcard.
    assert r.headers.get("access-control-allow-origin") is None


def test_real_request_from_allowed_origin_echoes_the_header(client):
    r = client.get("/v1/health", headers={"Origin": ALLOWED_ORIGIN}, auth=AUTH)
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN


def test_weather_field_real_request_from_allowed_origin_echoes_the_header(client):
    r = client.get("/v1/weather/field", headers={"Origin": ALLOWED_ORIGIN}, auth=AUTH)
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN


def test_no_credentials_mode_advertised():
    # allow_credentials=False deliberately (Basic Auth travels via the
    # Authorization header, not a cookie) -- a preflight response should
    # not advertise Access-Control-Allow-Credentials at all.
    settings = Settings(
        role="vessel",
        weather_npz_path="data/weather/ecmwf_western_med.npz",
        pool_size=1,
        cors_origins=(ALLOWED_ORIGIN,),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        r = _preflight(client, "/v1/health", ALLOWED_ORIGIN)
        assert "access-control-allow-credentials" not in {k.lower() for k in r.headers}


def test_settings_cors_origins_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("STINGRAY_CORS_ORIGINS", raising=False)
    settings = Settings.from_env()
    assert settings.cors_origins == ("http://localhost:8080",)


def test_settings_cors_origins_parses_comma_separated_list(monkeypatch):
    monkeypatch.setenv(
        "STINGRAY_CORS_ORIGINS", "https://trigger-nav.github.io, http://localhost:8080"
    )
    settings = Settings.from_env()
    assert settings.cors_origins == ("https://trigger-nav.github.io", "http://localhost:8080")
