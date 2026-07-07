"""Open lattice around the rhumb line between two ports (ticket 0.4) —
replaces the two hand-drawn, name-fixed corridors (`core/corridors.py`,
still used as the fallback/fast path) with a single lattice wide enough
that routing around Corsica (Bonifacio strait vs east-about) *emerges*
from the search against real geography, rather than being told there are
exactly two named options.

Geometry matches `core/corridors.py`'s approach exactly (straight lat/lon
interpolation + perpendicular offset in an equirectangular approximation
anchored at `REF_LAT_DEG`) — appropriate at this ~200nm passage scale, and
consistent with the rest of the codebase.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.geography import OPERATING_AREA_BBOX
from core.units import EARTH_NM_PER_DEG_LAT, LatLon, distance_m, interpolate_point, m_to_nm

REF_LAT_DEG = 42.3

DEFAULT_ALONG_TRACK_STEP_NM = 6.0
DEFAULT_CROSS_TRACK_STEP_NM = 5.0
DEFAULT_CROSS_TRACK_HALF_WIDTH_NM = 80.0
BBOX_MARGIN_NM = 3.0


@dataclass(frozen=True)
class Lattice:
    origin: LatLon
    destination: LatLon
    stage_centres: tuple[LatLon, ...]
    # per-stage symmetric lane range [-max_lane_per_stage[i], +max_lane_per_stage[i]] —
    # the ports themselves sit near OPERATING_AREA_BBOX's corners (little room
    # before the bbox edge), so width must taper near the endpoints and can
    # only open up mid-passage, same shape core/corridors.py's Corridor
    # objects already hand-tuned per corridor; here it falls out of the bbox
    # geometry automatically instead.
    max_lane_per_stage: tuple[int, ...]
    cross_track_step_nm: float

    @property
    def n_stages(self) -> int:
        return len(self.stage_centres)

    def point(self, stage: int, lane: int) -> LatLon:
        return _offset_point(self.stage_centres, stage, lane, self.cross_track_step_nm)


def _offset_point(
    centres: tuple[LatLon, ...], i: int, k: int, step_nm: float, ref_lat_deg: float = REF_LAT_DEG
) -> LatLon:
    """Perpendicular offset by `k` lanes of `step_nm` each — same
    construction as `core/corridors.py`'s `offset_point`, generalised to
    arbitrary lane spacing rather than a fixed corridor's `offset_nm * k`."""
    pts = centres
    a = pts[max(0, i - 1)]
    b = pts[min(len(pts) - 1, i + 1)]
    klon = math.cos(math.radians(ref_lat_deg))
    dy = b.lat_deg - a.lat_deg
    dx = (b.lon_deg - a.lon_deg) * klon
    length = math.hypot(dx, dy) or 1e-9
    px, py = -dy / length, dx / length
    offset_nm = step_nm * k
    return LatLon(
        pts[i].lat_deg + (py * offset_nm) / EARTH_NM_PER_DEG_LAT,
        pts[i].lon_deg + (px * offset_nm) / (EARTH_NM_PER_DEG_LAT * klon),
    )


def _within_bbox_with_margin(lat_deg: float, lon_deg: float, margin_deg: float) -> bool:
    lon_min, lat_min, lon_max, lat_max = OPERATING_AREA_BBOX
    return (
        lat_min + margin_deg <= lat_deg <= lat_max - margin_deg
        and lon_min + margin_deg <= lon_deg <= lon_max - margin_deg
    )


def build_lattice(
    origin: LatLon,
    destination: LatLon,
    *,
    along_track_step_nm: float = DEFAULT_ALONG_TRACK_STEP_NM,
    cross_track_step_nm: float = DEFAULT_CROSS_TRACK_STEP_NM,
    cross_track_half_width_nm: float = DEFAULT_CROSS_TRACK_HALF_WIDTH_NM,
) -> Lattice:
    """Build the open lattice, clipping the requested half-width down to
    whatever actually stays inside `OPERATING_AREA_BBOX` (with margin) at
    every stage — the concrete fix for the 0.3 "operating-area bounds"
    follow-up: the lattice itself never asks `RealGeography` for a point
    outside the area real data covers, so `OutOfOperatingAreaError` is a
    pure defensive backstop, not something normal operation should trip.
    """
    total_nm = m_to_nm(distance_m(origin, destination, REF_LAT_DEG))
    n_stages = max(2, round(total_nm / along_track_step_nm) + 1)
    stage_centres = tuple(
        interpolate_point(origin, destination, i / (n_stages - 1)) for i in range(n_stages)
    )

    requested_max_lane = max(1, round(cross_track_half_width_nm / cross_track_step_nm))
    margin_deg = BBOX_MARGIN_NM / EARTH_NM_PER_DEG_LAT

    max_lane_per_stage = []
    for i in range(n_stages):
        stage_max = 0
        for lane in range(1, requested_max_lane + 1):
            p_pos = _offset_point(stage_centres, i, lane, cross_track_step_nm)
            p_neg = _offset_point(stage_centres, i, -lane, cross_track_step_nm)
            if not (
                _within_bbox_with_margin(p_pos.lat_deg, p_pos.lon_deg, margin_deg)
                and _within_bbox_with_margin(p_neg.lat_deg, p_neg.lon_deg, margin_deg)
            ):
                break
            stage_max = lane
        max_lane_per_stage.append(stage_max)

    return Lattice(
        origin=origin,
        destination=destination,
        stage_centres=stage_centres,
        max_lane_per_stage=tuple(max_lane_per_stage),
        cross_track_step_nm=cross_track_step_nm,
    )
