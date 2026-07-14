"""Per-vessel saved favourites (ticket R1): SQLite, matching `capture/
store.py`'s existing embedded-SQLite precedent -- a new, separate
`favourites.sqlite3` (not a shared table inside the telemetry DB;
different writer, different lifecycle, avoids coupling two unrelated
concerns into one schema/file). Concurrent-write handling (WAL +
busy_timeout) follows that same precedent's `connect()` shape, since a
future bridge app (ticket 2.2) may write from more than one context.

`POST`/`DELETE /v1/favourites` are the first *state-writing* endpoints in
this API behind the single shared Basic Auth credential
(`api/config.py`'s `auth_user`/`auth_password`) -- every existing
endpoint that credential guards is either read-only or a compute job
scoped by request payload, not per-account persisted state. Acceptable
for v1 (a single pilot yacht or small fleet sharing one deployment,
matching this phase's threat model elsewhere), but this is real
multi-tenant auth debt this ticket inherits rather than solves --
ticket 1.4 (not yet scoped in detail) is where that gets closed.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS favourites (
    id TEXT PRIMARY KEY,
    vessel_id TEXT NOT NULL,
    name TEXT NOT NULL,
    lat_deg REAL NOT NULL,
    lon_deg REAL NOT NULL,
    is_anchorage INTEGER NOT NULL,
    pack_id TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


@dataclass(frozen=True)
class Favourite:
    id: str
    vessel_id: str
    name: str
    lat_deg: float
    lon_deg: float
    is_anchorage: bool
    pack_id: str
    created_at: float


def connect(db_path: str) -> sqlite3.Connection:
    """Same WAL/busy_timeout/shared-schema-execution shape as
    `capture/store.py`'s own `connect()` -- not duplicated by copy-paste
    drift, kept as a small, deliberate parallel since favourites and
    telemetry are genuinely separate concerns (different writer,
    different lifecycle) that shouldn't share a module either."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def add_favourite(
    db_path: str,
    *,
    vessel_id: str,
    name: str,
    lat_deg: float,
    lon_deg: float,
    is_anchorage: bool,
    pack_id: str,
) -> Favourite:
    fav = Favourite(
        id=uuid.uuid4().hex,
        vessel_id=vessel_id,
        name=name,
        lat_deg=lat_deg,
        lon_deg=lon_deg,
        is_anchorage=is_anchorage,
        pack_id=pack_id,
        created_at=time.time(),
    )
    conn = connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO favourites (
                id, vessel_id, name, lat_deg, lon_deg, is_anchorage, pack_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fav.id,
                fav.vessel_id,
                fav.name,
                fav.lat_deg,
                fav.lon_deg,
                int(fav.is_anchorage),
                fav.pack_id,
                fav.created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return fav


def list_favourites(db_path: str, *, vessel_id: str) -> list[Favourite]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, vessel_id, name, lat_deg, lon_deg, is_anchorage, pack_id, created_at
            FROM favourites WHERE vessel_id = ? ORDER BY created_at ASC
            """,
            (vessel_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        Favourite(
            id=r[0],
            vessel_id=r[1],
            name=r[2],
            lat_deg=r[3],
            lon_deg=r[4],
            is_anchorage=bool(r[5]),
            pack_id=r[6],
            created_at=r[7],
        )
        for r in rows
    ]


def delete_favourite(db_path: str, *, vessel_id: str, favourite_id: str) -> bool:
    """Scoped by `vessel_id` too, not just `id` -- a vessel can only ever
    delete its own favourite, never another's by guessing/enumerating an
    id. Returns whether a row was actually deleted (False -> the route
    handler turns this into a 404, not a silent no-op)."""
    conn = connect(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM favourites WHERE id = ? AND vessel_id = ?",
            (favourite_id, vessel_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
