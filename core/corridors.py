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
    """**Resolved, ticket 0.8 (2026-07-08) — feasible under RealGeography
    again.** Prior finding (ticket 0.4 review): the D->D2 segment below
    (41.29,9.08 -> 41.26,9.40) passes close to the real Bonifacio Strait /
    Iles Lavezzi archipelago's scattered granite islets, and under
    `core.geography.RealGeography` this corridor was infeasible via
    `core.optimiser._dp_route` at *every* speed/engine-config combination
    tried, including with the lateral-offset allowance widened to +-5 lanes
    and the turn-rate to +-3. That exhaustive check was against the
    synthetic *placeholder* no-go box `RealGeography` used before ticket
    0.8 (`lat 41.29-41.36, lon 9.21-9.31` — see the old `NOGO` list in
    `core/geography.py`'s history), which happened to cover the exact
    lateral-offset water the DP needed around the islets. Ticket 0.8's
    real, cited no-go geometry (`data/geography/nogo_western_med.json`,
    marineregions.org MRGID 3457 — the tighter, precisely-sourced Lavezzi
    *archipelago* box, `lat 41.3328-41.3514, lon 9.2476-9.2636`) no longer
    blocks that water, and this corridor is feasible again (verified: 1
    engine/10kn reaches ~193nm, clean track). It's a true fast-path
    fallback for this passage once more, not a permanently-broken one —
    the open lattice search (`core/lattice.py`, ticket 0.4) remains the
    primary/more-thorough search and still backs `optimiser._baseline_route`
    (see that function's docstring), but this corridor is no longer
    something callers must avoid relying on under RealGeography.
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
