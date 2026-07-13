"""`api.state._load_weather` (2026-07-13 Hetzner deploy fix #1) -- a
fresh box with no weather npz used to crash-loop on a bare
`FileNotFoundError` traceback; this wraps `GriddedWeatherField.from_npz`
so the failure is actionable in `journalctl` instead.
"""

from __future__ import annotations

import numpy as np
import pytest

from api.state import _load_weather
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
