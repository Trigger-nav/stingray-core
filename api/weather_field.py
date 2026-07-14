"""Downsampled weather-grid snapshot builder (ticket B2 amendment) --
`GET /v1/weather/field` in `api/routes.py`. Built entirely on top of
`core.weather.WeatherField.sample()`, the same interpolation every other
consumer (the optimiser, `evaluate_leg`) uses -- no `core/` changes, no
new code path there. Deliberately a display-only downsample, not a mirror
of the full-resolution grid.
"""

from __future__ import annotations

import hashlib
import math

from api.schemas import WeatherFieldOut
from core.geography import OPERATING_AREA_BBOX
from core.units import direction_from_components, ms_to_kn
from core.weather import WeatherField

# Fixed downsample resolution -- a canvas heatmap has no use for the
# source grid's full resolution (typically 0.25 deg per ticket 0.5), and
# a fixed, small grid keeps every request cheap (~1000 .sample() calls,
# each an O(1) bilinear lookup) regardless of the source data's own
# resolution. Chosen to roughly match OPERATING_AREA_BBOX's aspect ratio
# (~3.45 deg lon x 3.25 deg lat) rather than an arbitrary square.
FIELD_GRID_NLAT = 30
FIELD_GRID_NLON = 32


def quantize_hour(h: float) -> float:
    """Rounds to the nearest whole hour -- source weather data is
    hourly-to-3-hourly resolution (ticket 0.5), so serving sub-hour
    precision from a scrub slider would be fake precision beyond what the
    data actually carries, and quantizing is what makes the ETag below
    cache-friendly across a continuous scrub gesture (many nearby scrub
    positions collapse onto the same served hour, and browsers/caches
    treat a repeated ETag as "unchanged" via a 304)."""
    return round(h)


def compute_weather_field_etag(weather: WeatherField, valid_time_h: float, pack_id: str) -> str:
    """Depends on (weather cycle/fetch provenance, quantized hour,
    downsample resolution, `pack_id`) -- never recomputed unless one of
    those actually changes, so a scrub gesture revisiting the same hour,
    or a second browser tab, gets a cheap 304 instead of re-serializing
    the same ~1000-point grid. `pack_id` (ticket R1): without it, two
    packs' fields can share an identical (cycle, fetched, valid_time_h)
    triple (e.g. both fetched by the same cron run) and a browser that
    already cached one pack's response would get served a 304 for the
    other pack's request -- silently serving stale/wrong-region data with
    no error anywhere."""
    cycle = getattr(weather, "cycle", None) or ""
    fetched = getattr(weather, "fetched", None) or ""
    key = f"{pack_id}|{cycle}|{fetched}|{valid_time_h}|{FIELD_GRID_NLAT}x{FIELD_GRID_NLON}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f'"{digest}"'


def _none_if_nan(x: float) -> float | None:
    return None if math.isnan(x) else x


def build_weather_field(
    weather: WeatherField,
    valid_time_h: float,
    bbox: tuple[float, float, float, float] = OPERATING_AREA_BBOX,
) -> WeatherFieldOut:
    lon_min, lat_min, lon_max, lat_max = bbox
    dlat = (lat_max - lat_min) / (FIELD_GRID_NLAT - 1)
    dlon = (lon_max - lon_min) / (FIELD_GRID_NLON - 1)

    hs_grid: list[list[float | None]] = []
    speed_grid: list[list[float | None]] = []
    dir_grid: list[list[float | None]] = []
    for i in range(FIELD_GRID_NLAT):
        lat = lat_min + i * dlat
        hs_row: list[float | None] = []
        speed_row: list[float | None] = []
        dir_row: list[float | None] = []
        for j in range(FIELD_GRID_NLON):
            lon = lon_min + j * dlon
            sample = weather.sample(lat, lon, valid_time_h)
            hs_row.append(_none_if_nan(sample.hs_m))
            speed_ms, from_deg = direction_from_components(sample.wind_u_ms, sample.wind_v_ms)
            speed_row.append(_none_if_nan(ms_to_kn(speed_ms)))
            dir_row.append(_none_if_nan(from_deg))
        hs_grid.append(hs_row)
        speed_grid.append(speed_row)
        dir_grid.append(dir_row)

    return WeatherFieldOut(
        lat0_deg=lat_min,
        dlat_deg=dlat,
        lon0_deg=lon_min,
        dlon_deg=dlon,
        nlat=FIELD_GRID_NLAT,
        nlon=FIELD_GRID_NLON,
        valid_time_h=valid_time_h,
        hs_m=hs_grid,
        wind_speed_kn=speed_grid,
        wind_from_deg=dir_grid,
    )
