"""Weather field schema (B2) and two backing implementations.

`WeatherSample` fixes the conventions CORE_PORTING_NOTES.md calls out:
peak/mean period (the demo had none — A2), wave direction normalised to
"coming from", wind carried natively as u/v components (interpolation-
friendly), and a surface current vector (zero for now — A4/B2, real values
arrive with the ingest pipeline).

`SyntheticWeatherField` ports the demo's three closed-form scenarios
(mistral/calm/easterly) for regression fixtures. `GriddedWeatherField` is
the interpolation engine a real gridded product (ticket 0.5) will sit
behind: bilinear in space, linear in time, directions interpolated via
vector components (never raw degrees), and land cells (NaN in the source
arrays) propagate as missing rather than bleeding in artificial calm — the
"most dangerous silent bug in the demo" per B2.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from core.units import components_from_direction, direction_from_components, kn_to_ms


@dataclass(frozen=True)
class WeatherSample:
    hs_m: float
    period_peak_s: float
    period_mean_s: float
    wave_from_deg: float
    wind_u_ms: float
    wind_v_ms: float
    current_u_ms: float
    current_v_ms: float
    wind_sea_hs_m: float | None = None
    swell_hs_m: float | None = None

    @property
    def is_missing(self) -> bool:
        return math.isnan(self.hs_m)


class WeatherField(Protocol):
    def sample(self, lat_deg: float, lon_deg: float, t_h: float) -> WeatherSample: ...


# ---------------------------------------------------------------------------
# Synthetic scenarios (ported from the demo's SCENARIOS), for regression fixtures.
# ---------------------------------------------------------------------------


def _gauss(x: float, c: float, w: float) -> float:
    d = (x - c) / w
    return math.exp(-d * d)


def _synthetic_period(hs_m: float, base_s: float = 4.0, scale: float = 3.5) -> tuple[float, float]:
    """Placeholder Tp/Tm filler for the synthetic scenarios only — not a
    real spectral model. Real period comes from WW3/ECMWF wave GRIBs (0.5)."""
    peak = base_s + scale * math.sqrt(max(hs_m, 0.0))
    return peak, peak * 0.8


def _mistral(lat: float, lon: float, t: float):
    decay = 0.45 + 0.55 / (1 + math.exp((t - 20) / 5))
    west = _gauss(lon, 7.9, 1.5) * _gauss(lat, 41.9, 1.6)
    lig = 0.45 * _gauss(lon, 8.6, 1.6) * _gauss(lat, 43.2, 0.9)
    boni = 0.85 * _gauss(lon, 9.05, 0.45) * _gauss(lat, 41.32, 0.22)
    lee = 0.30 if (lon > 9.15 and 41.4 < lat < 43.05) else 1.0
    hs = min(4.2, (0.5 + 3.4 * max(west, lig, boni) * decay) * lee)
    wind_kn = (8 + 30 * max(west, boni) * decay) * (0.5 if lee < 1 else 1)
    return hs, 315.0, wind_kn, 315.0


def _calm(lat: float, lon: float, t: float):
    return 0.5, 200.0, 6.0, 220.0


def _easterly(lat: float, lon: float, t: float):
    build = min(1.0, 0.4 + t / 24)
    east = _gauss(lon, 9.8, 1.2) * _gauss(lat, 42.2, 1.6)
    lee = 0.35 if (lon < 8.9 and 41.5 < lat < 43.0) else 1.0
    hs = min(3.2, (0.5 + 2.6 * east * build) * lee)
    wind_kn = (7 + 22 * east * build) * (0.6 if lee < 1 else 1)
    return hs, 95.0, wind_kn, 95.0


_SCENARIOS = {"mistral": _mistral, "calm": _calm, "easterly": _easterly}


class SyntheticWeatherField:
    """Closed-form demo scenarios, period-extended (A2/B2). No land-masking
    needed — used only for optimiser regression fixtures, not real routing."""

    def __init__(self, scenario: str) -> None:
        if scenario not in _SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}; choose from {sorted(_SCENARIOS)}")
        self._fn = _SCENARIOS[scenario]

    def sample(self, lat_deg: float, lon_deg: float, t_h: float) -> WeatherSample:
        hs, wave_from_deg, wind_kn, wind_from_deg = self._fn(lat_deg, lon_deg, t_h)
        peak, mean = _synthetic_period(hs)
        wind_u, wind_v = components_from_direction(kn_to_ms(wind_kn), wind_from_deg)
        return WeatherSample(
            hs_m=hs,
            period_peak_s=peak,
            period_mean_s=mean,
            wave_from_deg=wave_from_deg,
            wind_u_ms=wind_u,
            wind_v_ms=wind_v,
            current_u_ms=0.0,
            current_v_ms=0.0,
        )


# ---------------------------------------------------------------------------
# Gridded interpolation engine (backing implementation for ticket 0.5).
# ---------------------------------------------------------------------------


def _bilinear(grid_2d: np.ndarray, fy: float, fx: float) -> float:
    y0, x0 = int(math.floor(fy)), int(math.floor(fx))
    wy, wx = fy - y0, fx - x0
    y1 = min(y0 + 1, grid_2d.shape[0] - 1)
    x1 = min(x0 + 1, grid_2d.shape[1] - 1)
    v00, v10 = grid_2d[y0, x0], grid_2d[y1, x0]
    v01, v11 = grid_2d[y0, x1], grid_2d[y1, x1]
    return v00 * (1 - wy) * (1 - wx) + v10 * wy * (1 - wx) + v01 * (1 - wy) * wx + v11 * wy * wx


class GriddedWeatherField:
    """Bilinear-in-space, linear-in-time interpolation over a regular
    lat/lon/time grid. Directions interpolate via vector components. Any
    NaN in the source arrays (land, per B2) propagates to a missing sample
    rather than being read as calm.

    All arrays are shaped (n_hours, n_lat, n_lon).
    """

    def __init__(
        self,
        *,
        lat0_deg: float,
        dlat_deg: float,
        lon0_deg: float,
        dlon_deg: float,
        hours: list[float],
        hs_m: np.ndarray,
        period_peak_s: np.ndarray,
        period_mean_s: np.ndarray,
        wave_from_deg: np.ndarray,
        wind_u_ms: np.ndarray,
        wind_v_ms: np.ndarray,
        current_u_ms: np.ndarray,
        current_v_ms: np.ndarray,
    ) -> None:
        self._lat0, self._dlat = lat0_deg, dlat_deg
        self._lon0, self._dlon = lon0_deg, dlon_deg
        self._hours = np.asarray(hours, dtype=float)
        self._hs = np.asarray(hs_m, dtype=float)
        self._period_peak = np.asarray(period_peak_s, dtype=float)
        self._period_mean = np.asarray(period_mean_s, dtype=float)
        self._wind_u = np.asarray(wind_u_ms, dtype=float)
        self._wind_v = np.asarray(wind_v_ms, dtype=float)
        self._current_u = np.asarray(current_u_ms, dtype=float)
        self._current_v = np.asarray(current_v_ms, dtype=float)
        rad = np.radians(np.asarray(wave_from_deg, dtype=float) + 180.0)
        self._wave_dir_u = np.sin(rad)
        self._wave_dir_v = np.cos(rad)
        _, self._nlat, self._nlon = self._hs.shape

    def _grid_fracs(self, lat_deg: float, lon_deg: float) -> tuple[float, float]:
        fy = (lat_deg - self._lat0) / self._dlat
        fx = (lon_deg - self._lon0) / self._dlon
        fy = max(0.0, min(self._nlat - 1.001, fy))
        fx = max(0.0, min(self._nlon - 1.001, fx))
        return fy, fx

    def sample(self, lat_deg: float, lon_deg: float, t_h: float) -> WeatherSample:
        fy, fx = self._grid_fracs(lat_deg, lon_deg)
        step = (self._hours[1] - self._hours[0]) if len(self._hours) > 1 else 1.0
        ti = (t_h - self._hours[0]) / step
        ti = max(0.0, min(len(self._hours) - 1.001, ti))
        t0 = int(math.floor(ti))
        wt = ti - t0
        t1 = min(t0 + 1, len(self._hours) - 1)

        def at(arr3d: np.ndarray) -> float:
            v0 = _bilinear(arr3d[t0], fy, fx)
            v1 = _bilinear(arr3d[t1], fy, fx)
            return float(v0 * (1 - wt) + v1 * wt)

        hs = at(self._hs)
        peak = at(self._period_peak)
        mean = at(self._period_mean)
        wind_u = at(self._wind_u)
        wind_v = at(self._wind_v)
        current_u = at(self._current_u)
        current_v = at(self._current_v)
        wdu, wdv = at(self._wave_dir_u), at(self._wave_dir_v)
        wave_from_deg = (
            float("nan")
            if (math.isnan(wdu) or math.isnan(wdv))
            else direction_from_components(wdu, wdv)[1]
        )

        return WeatherSample(
            hs_m=hs,
            period_peak_s=peak,
            period_mean_s=mean,
            wave_from_deg=wave_from_deg,
            wind_u_ms=wind_u,
            wind_v_ms=wind_v,
            current_u_ms=current_u,
            current_v_ms=current_v,
        )
