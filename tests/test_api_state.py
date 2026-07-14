"""`api.state._load_weather` (2026-07-13 Hetzner deploy fix #1) -- a
fresh box with no weather npz used to crash-loop on a bare
`FileNotFoundError` traceback; this wraps `GriddedWeatherField.from_npz`
so the failure is actionable in `journalctl` instead.
"""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest
import yaml

from api.config import Settings
from api.state import AppState, _load_weather
from core.weather import GriddedWeatherField


def test_load_weather_raises_actionable_runtime_error_when_missing(tmp_path):
    missing_path = tmp_path / "does_not_exist.npz"

    with pytest.raises(RuntimeError) as excinfo:
        _load_weather(str(missing_path))

    message = str(excinfo.value)
    assert str(missing_path) in message
    assert "ingest.fetch_grib_nomads" in message or "ingest.fetch_grib_ecmwf" in message
    assert isinstance(excinfo.value.__cause__, FileNotFoundError)


def test_load_weather_loads_a_real_npz(tmp_path):
    nlat, nlon = 2, 2
    grid = np.full((1, nlat, nlon), 1.5)
    zeros = np.zeros((1, nlat, nlon))

    npz_path = tmp_path / "test_western_med.npz"
    with open(npz_path, "wb") as f:
        np.savez_compressed(
            f,
            lat0=41.0,
            dlat=1.0,
            lon0=8.0,
            dlon=1.0,
            hours=np.array([0.0]),
            hs_m=grid,
            period_peak_s=grid,
            period_mean_s=grid,
            wave_from_deg=grid,
            wind_u_ms=grid,
            wind_v_ms=grid,
            current_u_ms=zeros,
            current_v_ms=zeros,
        )

    field = _load_weather(str(npz_path))

    assert isinstance(field, GriddedWeatherField)
    s = field.sample(41.5, 8.5, 0.0)
    assert not s.is_missing
    assert s.hs_m == pytest.approx(1.5)


def _write_tiny_weather_npz(path, hs_m_value: float) -> None:
    nlat, nlon = 2, 2
    grid = np.full((1, nlat, nlon), hs_m_value)
    zeros = np.zeros((1, nlat, nlon))
    with open(path, "wb") as f:
        np.savez_compressed(
            f,
            lat0=19.0,
            dlat=1.0,
            lon0=19.0,
            dlon=1.0,
            hours=np.array([0.0]),
            hs_m=grid,
            period_peak_s=grid,
            period_mean_s=grid,
            wave_from_deg=grid,
            wind_u_ms=zeros,
            wind_v_ms=zeros,
            current_u_ms=zeros,
            current_v_ms=zeros,
        )


def _write_tiny_pack(tmp_path, pack_id: str, lat0: float, lon0: float, hs_m_value: float) -> str:
    """A minimal from_pack-loadable RegionPack (land-free 2x2 grid), for
    hot-swap tests that don't need real coastline/no-go fidelity."""
    coastline_path = tmp_path / f"coastline_{pack_id}.json"
    coastline_path.write_text(
        json.dumps({"source": "synthetic", "bbox_lon_lat": [], "polygons": []})
    )
    nogo_path = tmp_path / f"nogo_{pack_id}.json"
    nogo_path.write_text(json.dumps({"zones": []}))
    tss_path = tmp_path / f"tss_{pack_id}.json"
    tss_path.write_text(json.dumps({"zones": []}))

    bathymetry_path = tmp_path / f"bathymetry_{pack_id}.npz"
    nlat, nlon = 4, 4
    np.savez_compressed(
        bathymetry_path,
        lat0=lat0,
        dlat=1.0,
        lon0=lon0,
        dlon=1.0,
        nlat=nlat,
        nlon=nlon,
        elevation_m=np.full((nlat, nlon), -100.0),
    )

    weather_path = tmp_path / f"weather_{pack_id}.npz"
    _write_tiny_weather_npz(weather_path, hs_m_value)

    pack_yaml = tmp_path / f"pack_{pack_id}.yaml"
    pack_yaml.write_text(
        yaml.dump(
            {
                "pack_id": pack_id,
                "name": f"Tiny pack {pack_id}",
                "bbox": [lon0 - 0.5, lat0 - 0.5, lon0 + 3.5, lat0 + 3.5],
                "ref_lat_deg": lat0,
                "coastline_path": str(coastline_path),
                "bathymetry_path": str(bathymetry_path),
                "nogo_path": str(nogo_path),
                "tss_path": str(tss_path),
                "weather_npz_path": str(weather_path),
                "default_origin": [lat0 + 1, lon0 + 1],
                "default_destination": [lat0 + 2, lon0 + 2],
            }
        )
    )
    return str(pack_yaml), str(weather_path)


def test_hot_swap_only_refreshes_the_pack_whose_weather_file_actually_changed(tmp_path):
    """Minor flag (ticket R1 review): the hot-swap watcher must cover
    every configured pack, not just one -- fabricate two packs, touch
    only one's npz, and confirm only that pack's in-memory WeatherField
    changes."""

    async def run() -> None:
        pack_a_yaml, weather_a_path = _write_tiny_pack(tmp_path, "a", 19.0, 19.0, hs_m_value=1.0)
        pack_b_yaml, weather_b_path = _write_tiny_pack(tmp_path, "b", 40.0, 40.0, hs_m_value=2.0)
        packs_manifest = tmp_path / "region_packs.yaml"
        packs_manifest.write_text(yaml.dump({"packs": [pack_a_yaml, pack_b_yaml]}))

        config = Settings(pool_size=1, region_packs_path=str(packs_manifest))
        state = AppState(config)
        try:
            a_before = state.weather["a"].sample(20.0, 20.0, 0.0).hs_m
            b_before = state.weather["b"].sample(41.0, 41.0, 0.0).hs_m
            assert a_before == pytest.approx(1.0)
            assert b_before == pytest.approx(2.0)

            # Only pack "a"'s weather file changes.
            _write_tiny_weather_npz(weather_a_path, hs_m_value=9.0)
            state._last_weather_mtimes["a"] = -1.0  # force "changed" regardless of real mtime

            await state._check_and_swap()

            a_after = state.weather["a"].sample(20.0, 20.0, 0.0).hs_m
            b_after = state.weather["b"].sample(41.0, 41.0, 0.0).hs_m
            assert a_after == pytest.approx(9.0)
            assert b_after == pytest.approx(2.0)  # untouched pack's value is unchanged
        finally:
            state.shutdown()

    asyncio.run(run())
