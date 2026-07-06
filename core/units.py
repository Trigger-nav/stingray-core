"""SI conversions and geometry primitives.

Internal convention (B1): metres, m/s, kg fuel. Marine units (knots, nm,
litres) only cross at API boundaries via the `_kn`/`_nm`/`_l` suffixed
functions below. Passage duration is the one deliberate exception: hours
(`_h`), not seconds, since that's how ETA windows and passage plans are
naturally expressed and isn't one of the units B1 calls out as a source of
silent bugs.

Distance/bearing here use a local equirectangular (flat-earth) approximation
anchored at a reference latitude — valid over the few-hundred-nm corridor
scale this operates at (matches the demo's approach). Real great-circle
geodesy isn't needed until routing operates over much larger areas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

KN_TO_MS = 0.514444
NM_TO_M = 1852.0
EARTH_NM_PER_DEG_LAT = 60.0


def kn_to_ms(speed_kn: float) -> float:
    return speed_kn * KN_TO_MS


def ms_to_kn(speed_ms: float) -> float:
    return speed_ms / KN_TO_MS


def nm_to_m(distance_nm: float) -> float:
    return distance_nm * NM_TO_M


def m_to_nm(distance_m: float) -> float:
    return distance_m / NM_TO_M


def normalize_lon_deg(lon_deg: float) -> float:
    """Wrap longitude to [-180, 180)."""
    return ((lon_deg + 180.0) % 360.0) - 180.0


def normalize_bearing_deg(bearing_deg: float) -> float:
    """Wrap a bearing/direction to [0, 360)."""
    return bearing_deg % 360.0


def components_from_direction(speed: float, from_deg: float) -> tuple[float, float]:
    """Convert a met/ocean "coming from" direction + speed to (u, v) vector
    components (u=east, v=north) of the flow's direction of travel."""
    to_rad = math.radians(from_deg + 180.0)
    u = speed * math.sin(to_rad)
    v = speed * math.cos(to_rad)
    return u, v


def direction_from_components(u: float, v: float) -> tuple[float, float]:
    """Inverse of components_from_direction: returns (speed, from_deg)."""
    speed = math.hypot(u, v)
    to_deg = math.degrees(math.atan2(u, v))
    from_deg = normalize_bearing_deg(to_deg + 180.0)
    return speed, from_deg


@dataclass(frozen=True)
class LatLon:
    lat_deg: float
    lon_deg: float


def distance_m(a: LatLon, b: LatLon, ref_lat_deg: float) -> float:
    klon = math.cos(math.radians(ref_lat_deg))
    dy_nm = (b.lat_deg - a.lat_deg) * EARTH_NM_PER_DEG_LAT
    dx_nm = (b.lon_deg - a.lon_deg) * EARTH_NM_PER_DEG_LAT * klon
    return nm_to_m(math.hypot(dx_nm, dy_nm))


def bearing_deg(a: LatLon, b: LatLon, ref_lat_deg: float) -> float:
    klon = math.cos(math.radians(ref_lat_deg))
    dy = b.lat_deg - a.lat_deg
    dx = (b.lon_deg - a.lon_deg) * klon
    return normalize_bearing_deg(math.degrees(math.atan2(dx, dy)))


def interpolate_point(a: LatLon, b: LatLon, t: float) -> LatLon:
    return LatLon(a.lat_deg + (b.lat_deg - a.lat_deg) * t, a.lon_deg + (b.lon_deg - a.lon_deg) * t)


def resolve_ground_speed_ms(
    stw_ms: float, track_bearing_deg: float, current_u_ms: float, current_v_ms: float
) -> float:
    """Speed over ground along a fixed track, given speed through water and
    a surface current, assuming the vessel steers to correct for cross-track
    set (the standard current-triangle correction). STW/SOG distinction
    (A4): fuel/motion/wear always take `stw_ms`; only duration/ETA take this.
    """
    brg = math.radians(track_bearing_deg)
    track_u, track_v = math.sin(brg), math.cos(brg)
    cross_u, cross_v = math.cos(brg), -math.sin(brg)
    along_current = current_u_ms * track_u + current_v_ms * track_v
    cross_current = current_u_ms * cross_u + current_v_ms * cross_v
    cross_track_water = -cross_current
    remainder = stw_ms**2 - cross_track_water**2
    if remainder < 0:
        raise ValueError("current exceeds vessel speed through water; cannot hold track")
    along_track_water = math.sqrt(remainder)
    return along_track_water + along_current


def resolve_course_to_steer_deg(
    stw_ms: float, track_bearing_deg: float, current_u_ms: float, current_v_ms: float
) -> float:
    """Course to steer: the heading the vessel must point (not the track
    course) to make good `track_bearing_deg` over the ground given a
    surface current — companion to `resolve_ground_speed_ms` (same
    current-triangle decomposition, but returns the steered heading rather
    than the resulting speed). Equals `track_bearing_deg` exactly when
    current is zero."""
    brg = math.radians(track_bearing_deg)
    track_u, track_v = math.sin(brg), math.cos(brg)
    cross_u, cross_v = math.cos(brg), -math.sin(brg)
    cross_current = current_u_ms * cross_u + current_v_ms * cross_v
    cross_track_water = -cross_current
    remainder = stw_ms**2 - cross_track_water**2
    if remainder < 0:
        raise ValueError("current exceeds vessel speed through water; cannot hold track")
    along_track_water = math.sqrt(remainder)
    water_u = along_track_water * track_u + cross_track_water * cross_u
    water_v = along_track_water * track_v + cross_track_water * cross_v
    return normalize_bearing_deg(math.degrees(math.atan2(water_u, water_v)))
