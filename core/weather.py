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
from pathlib import Path
from typing import Protocol

import numpy as np

from core.gridding import bilinear, bilinear_masked, grid_fracs
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
# Bilinear/grid-fraction math lives in core/gridding.py, shared with
# core/geography.py's RealGeography bathymetry lookup (ticket 0.3).
# ---------------------------------------------------------------------------


class GriddedWeatherField:
    """Bilinear-in-space, linear-in-time interpolation over a regular
    lat/lon/time grid. Directions interpolate via vector components.

    Land-masking (B2) is wave-and-current, not wind, per ticket 0.5/C1:
    `hs_m`/periods/wave direction, and (since C1) `current_u_ms`/
    `current_v_ms`, use `bilinear_masked` (renormalises over whichever
    corners aren't NaN, only fully-missing when *all four* are — see
    `core/gridding.py`), so a point near shore (an anchorage approach,
    say) still gets a real wave/current estimate from its valid
    neighbours instead of reading as missing just because one stencil
    corner is land. Wind is never land-masked at ingest in the first
    place: an over-land GFS/IFS wind value is a real model output, not a
    hardcoded-calm artefact the way the demo's wave field was, so there's
    no equivalent bug to guard against and plain `bilinear` is used for
    it. Current is ocean-only data (unlike wind) with no equivalent
    over-land meaning, so it follows wave's treatment, not wind's — found
    during ticket C1 while tracing what happens once current stops being
    a uniform zero: plain `bilinear` propagates any single NaN stencil
    corner to a fully-missing result, and a real ocean-current product
    masks land far more aggressively near shore than GFS/IFS wind does —
    exactly where routing endpoints tend to live. Verified freeze-
    compatible: a uniform-zero current array has no NaNs anywhere, so
    `bilinear_masked` and `bilinear` are numerically identical on it.

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
        cycle: str | None = None,
        fetched: str | None = None,
        source: str | None = None,
        current_cycle: str | None = None,
        current_fetched: str | None = None,
        current_source: str | None = None,
        wave_filled_cells: int | None = None,
        current_filled_cells: int | None = None,
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
        # Provenance (ticket 0.5): which model cycle, when this field was
        # fetched, and from where -- None for in-memory/test-built fields
        # that were never loaded from an ingest npz. Ticket C1: a
        # separate current_cycle/current_fetched/current_source triple,
        # since currents are fetched from a different source (CMEMS) at a
        # different cadence than wind/wave -- all None (not "modelled but
        # happens to read zero") for a pack that never enables currents
        # (RegionPack.currents_dataset_id unset) or hasn't been merged yet.
        self.cycle = cycle
        self.fetched = fetched
        self.source = source
        self.current_cycle = current_cycle
        self.current_fetched = current_fetched
        self.current_source = current_source
        # Ticket W1: how many wave/current cells this npz's own ingest
        # filled from a nearby real ocean cell (coastal-fill, see
        # ingest.grib_common.coastal_fill_mask/apply_coastal_fill) --
        # None (not 0) for an npz written before this ticket, or a field
        # group a pack never fetches (Med currents) -- the same "not
        # modelled, not indistinguishable from modelled-and-zero" signal
        # current_source already established.
        self.wave_filled_cells = wave_filled_cells
        self.current_filled_cells = current_filled_cells

    @classmethod
    def from_npz(cls, path: str | Path) -> GriddedWeatherField:
        """Load a grid written by `ingest/fetch_grib_nomads.py` or
        `ingest/fetch_grib_ecmwf.py` -- mirrors
        `core.geography.RealGeography.__init__`'s `np.load(...)` +
        field-unpacking pattern. No default path constant here (unlike
        geography's static, committed data): which source/cycle to load is
        a caller decision, not a library default, since forecasts are
        time-varying and per-run rather than committed repo content."""
        grid = np.load(path, allow_pickle=False)
        return cls(
            lat0_deg=float(grid["lat0"]),
            dlat_deg=float(grid["dlat"]),
            lon0_deg=float(grid["lon0"]),
            dlon_deg=float(grid["dlon"]),
            hours=list(grid["hours"]),
            hs_m=grid["hs_m"],
            period_peak_s=grid["period_peak_s"],
            period_mean_s=grid["period_mean_s"],
            wave_from_deg=grid["wave_from_deg"],
            wind_u_ms=grid["wind_u_ms"],
            wind_v_ms=grid["wind_v_ms"],
            current_u_ms=grid["current_u_ms"],
            current_v_ms=grid["current_v_ms"],
            cycle=str(grid["cycle"]) if "cycle" in grid else None,
            fetched=str(grid["fetched"]) if "fetched" in grid else None,
            source=str(grid["source"]) if "source" in grid else None,
            # Ticket C1: same defensive `in grid` pattern as the three
            # fields above -- an npz written before this ticket (or by an
            # older fetch_grib_*.py mid-upgrade) has none of these keys
            # at all, and must load as "not modelled" (None), not KeyError.
            current_cycle=str(grid["current_cycle"]) if "current_cycle" in grid else None,
            current_fetched=str(grid["current_fetched"]) if "current_fetched" in grid else None,
            current_source=str(grid["current_source"]) if "current_source" in grid else None,
            # Ticket W1: same defensive `in grid` pattern -- an npz
            # written before this ticket has neither key.
            wave_filled_cells=(
                int(grid["wave_filled_cells"]) if "wave_filled_cells" in grid else None
            ),
            current_filled_cells=(
                int(grid["current_filled_cells"]) if "current_filled_cells" in grid else None
            ),
        )

    def _grid_fracs(self, lat_deg: float, lon_deg: float) -> tuple[float, float]:
        return grid_fracs(
            lat_deg, lon_deg, self._lat0, self._dlat, self._lon0, self._dlon, self._nlat, self._nlon
        )

    def sample(self, lat_deg: float, lon_deg: float, t_h: float) -> WeatherSample:
        fy, fx = self._grid_fracs(lat_deg, lon_deg)
        step = (self._hours[1] - self._hours[0]) if len(self._hours) > 1 else 1.0
        ti = (t_h - self._hours[0]) / step
        ti = max(0.0, min(len(self._hours) - 1.001, ti))
        t0 = int(math.floor(ti))
        wt = ti - t0
        t1 = min(t0 + 1, len(self._hours) - 1)

        def at(arr3d: np.ndarray, *, masked: bool = False) -> float:
            fn = bilinear_masked if masked else bilinear
            v0 = fn(arr3d[t0], fy, fx)
            v1 = fn(arr3d[t1], fy, fx)
            return float(v0 * (1 - wt) + v1 * wt)

        hs = at(self._hs, masked=True)
        peak = at(self._period_peak, masked=True)
        mean = at(self._period_mean, masked=True)
        wind_u = at(self._wind_u)
        wind_v = at(self._wind_v)
        current_u = at(self._current_u, masked=True)
        current_v = at(self._current_v, masked=True)
        wdu, wdv = at(self._wave_dir_u, masked=True), at(self._wave_dir_v, masked=True)
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
