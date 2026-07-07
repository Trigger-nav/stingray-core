"""Corridor definitions, ported ~1:1 from the demo's corridorWest()/
corridorEast()/offsetPoint() (per section D — this structure is validated
and carries over; ticket 0.4 replaces the corridor-bounded lattice with an
open time-expanded graph search, at which point this becomes the fast/
fallback path per the roadmap)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.units import EARTH_NM_PER_DEG_LAT, LatLon, interpolate_point

REF_LAT_DEG = 42.3

PORTS: dict[str, LatLon] = {
    "antibes": LatLon(43.55, 7.17),
    "portocervo": LatLon(41.13, 9.55),
}


@dataclass(frozen=True)
class Corridor:
    name: str
    side: str
    points: tuple[LatLon, ...]
    max_offset_steps: tuple[int, ...]
    offset_nm: float


def _seg(a: LatLon, b: LatLon, n: int) -> list[LatLon]:
    return [interpolate_point(a, b, i / n) for i in range(n + 1)]


def corridor_west() -> Corridor:
    """KNOWN ISSUE (found during ticket 0.4 review, unresolved — ticket 0.8's
    job): the D->D2 segment below (41.29,9.08 -> 41.26,9.40) cuts straight
    across the real Bonifacio Strait / Iles Lavezzi archipelago — dozens of
    scattered granite islets between Corsica and Sardinia, plus the marine
    reserve and (in reality) a TSS. Against `core.geography.RealGeography`
    this corridor is infeasible via `core.optimiser._dp_route` at *every*
    speed/engine-config combination (verified exhaustively, including with
    the lateral-offset allowance widened to +-5 lanes and the turn-rate to
    +-3 — a two-waypoint straight segment doesn't thread a scattered reef
    field no matter how much lateral room the DP is given).

    Ticket 0.4 worked around this by backing `optimiser._baseline_route`
    with the open lattice search instead of this corridor (see that
    function's docstring). This corridor's D/D2/D3 waypoints themselves are
    NOT fixed — doing so properly needs the real TSS lane geometry and
    chart-derived no-go data (ticket 0.8: "Routing safety constraints: min
    depth, TSS, no-go polygons from chart data"), not a guess from raw
    GSHHG coastline polygons. Until then, do not rely on this corridor
    being feasible under RealGeography; SyntheticGeography (the demo's
    hand-drawn, coarser polygons) does not exhibit this problem, which is
    why the optimiser regression/constraint tests that use `_dp_route`
    directly still pass against it.
    """
    a = PORTS["antibes"]
    b = LatLon(42.50, 8.25)
    c = LatLon(41.55, 8.60)
    d = LatLon(41.29, 9.08)
    d2 = LatLon(41.26, 9.40)
    d3 = LatLon(41.19, 9.55)
    e = PORTS["portocervo"]
    pts = (
        _seg(a, b, 5)
        + _seg(b, c, 3)[1:]
        + _seg(c, d, 2)[1:]
        + _seg(d, d2, 2)[1:]
        + _seg(d2, d3, 1)[1:]
        + _seg(d3, e, 1)[1:]
    )
    n = len(pts)
    max_offset = tuple(
        1 if (p.lat_deg < 41.6 or i < 2 or i > n - 3) else 3 for i, p in enumerate(pts)
    )
    return Corridor(
        name="West via Bonifacio",
        side="W",
        points=tuple(pts),
        max_offset_steps=max_offset,
        offset_nm=5.0,
    )


def corridor_east() -> Corridor:
    a = PORTS["antibes"]
    b = LatLon(43.10, 9.42)
    c = LatLon(42.30, 9.80)
    d = LatLon(41.55, 9.80)
    e = PORTS["portocervo"]
    pts = _seg(a, b, 5) + _seg(b, c, 3)[1:] + _seg(c, d, 3)[1:] + _seg(d, e, 2)[1:]
    n = len(pts)
    max_offset = tuple(1 if (i < 2 or i > n - 3) else 2 for i in range(n))
    return Corridor(
        name="East of Corsica",
        side="E",
        points=tuple(pts),
        max_offset_steps=max_offset,
        offset_nm=5.0,
    )


def offset_point(corridor: Corridor, i: int, k: int, ref_lat_deg: float = REF_LAT_DEG) -> LatLon:
    pts = corridor.points
    a = pts[max(0, i - 1)]
    b = pts[min(len(pts) - 1, i + 1)]
    klon = math.cos(math.radians(ref_lat_deg))
    dy = b.lat_deg - a.lat_deg
    dx = (b.lon_deg - a.lon_deg) * klon
    length = math.hypot(dx, dy) or 1e-9
    px, py = -dy / length, dx / length
    offset_nm = corridor.offset_nm * k
    return LatLon(
        pts[i].lat_deg + (py * offset_nm) / EARTH_NM_PER_DEG_LAT,
        pts[i].lon_deg + (px * offset_nm) / (EARTH_NM_PER_DEG_LAT * klon),
    )
