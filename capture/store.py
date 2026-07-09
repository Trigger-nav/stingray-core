"""Local telemetry store (ticket B1/B5 design 7-8): SQLite, append-only,
the natural single-PC-scale equivalent of the "local store" ticket 1.3
later builds out fully -- boring, zero-ceremony, matches this project's
existing "boring tech" bias.

Concurrent access (plan review addition #1): the same file is written by
`capture/service.py` (continuous, one OS process) and read by the planner
process's `GET /v1/telemetry/status` handler (`api/routes.py`, design 8) --
two independent OS processes. Every connection this module opens sets
`PRAGMA journal_mode=WAL` (readers don't block the writer, and vice versa)
and `PRAGMA busy_timeout=5000` (a writer momentarily contending with WAL's
checkpoint, or two near-simultaneous opens, retries for up to 5s instead of
raising `database is locked` immediately) -- via one shared helper
(`connect`), not duplicated per call site.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass

from capture.telemetry import MotionSample, SensorTier, TelemetrySample

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    timestamp REAL NOT NULL,
    source_pgn INTEGER NOT NULL,
    sensor_tier TEXT NOT NULL,
    lat_deg REAL,
    lon_deg REAL,
    sog_kn REAL,
    cog_deg REAL,
    heading_deg REAL,
    stw_kn REAL,
    engine_fuel_rate_l_per_h REAL,
    engine_rpm REAL,
    motion_heave_m REAL,
    motion_roll_deg REAL,
    motion_pitch_deg REAL,
    motion_accel_vertical_ms2 REAL,
    PRIMARY KEY (timestamp, source_pgn)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Shared connection-opening helper -- every caller (the continuous
    writer in `capture/service.py`, and `query_status`'s one-off reads
    below) goes through this, so the WAL/busy_timeout pragmas are never
    forgotten at a call site."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def append(conn: sqlite3.Connection, sample: TelemetrySample) -> None:
    """Takes an already-open connection (not a path) -- `capture/
    service.py` holds one connection open for its whole run rather than
    reconnecting per sample; `query_status` below is the only caller that
    opens a short-lived connection per call, since it's infrequent
    (one poll per health check)."""
    motion = sample.motion
    conn.execute(
        """
        INSERT OR REPLACE INTO telemetry (
            timestamp, source_pgn, sensor_tier, lat_deg, lon_deg, sog_kn,
            cog_deg, heading_deg, stw_kn, engine_fuel_rate_l_per_h,
            engine_rpm, motion_heave_m, motion_roll_deg, motion_pitch_deg,
            motion_accel_vertical_ms2
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sample.timestamp,
            sample.source_pgn or 0,
            sample.sensor_tier,
            sample.lat_deg,
            sample.lon_deg,
            sample.sog_kn,
            sample.cog_deg,
            sample.heading_deg,
            sample.stw_kn,
            sample.engine_fuel_rate_l_per_h,
            sample.engine_rpm,
            motion.heave_m if motion else None,
            motion.roll_deg if motion else None,
            motion.pitch_deg if motion else None,
            motion.accel_vertical_ms2 if motion else None,
        ),
    )
    conn.commit()


def read_all(conn: sqlite3.Connection) -> list[TelemetrySample]:
    """Test/debug convenience -- not used on the hot write path."""
    rows = conn.execute(
        """
        SELECT timestamp, source_pgn, sensor_tier, lat_deg, lon_deg, sog_kn,
               cog_deg, heading_deg, stw_kn, engine_fuel_rate_l_per_h,
               engine_rpm, motion_heave_m, motion_roll_deg, motion_pitch_deg,
               motion_accel_vertical_ms2
        FROM telemetry ORDER BY timestamp
        """
    ).fetchall()
    samples = []
    for row in rows:
        has_motion = any(v is not None for v in row[11:15])
        motion = (
            MotionSample(
                heave_m=row[11], roll_deg=row[12], pitch_deg=row[13], accel_vertical_ms2=row[14]
            )
            if has_motion
            else None
        )
        samples.append(
            TelemetrySample(
                timestamp=row[0],
                source_pgn=row[1],
                sensor_tier=row[2],
                lat_deg=row[3],
                lon_deg=row[4],
                sog_kn=row[5],
                cog_deg=row[6],
                heading_deg=row[7],
                stw_kn=row[8],
                engine_fuel_rate_l_per_h=row[9],
                engine_rpm=row[10],
                motion=motion,
            )
        )
    return samples


@dataclass(frozen=True)
class TelemetryStatus:
    last_sample_at: float | None
    sensor_tier: SensorTier | None
    sample_count: int
    gap_seconds: float | None


def query_status(db_path: str) -> TelemetryStatus:
    """Read-only summary for `GET /v1/telemetry/status` (api/routes.py) --
    opens a short-lived connection since this is called infrequently (one
    poll per health check), not the hot write path `append` serves."""
    if not os.path.exists(db_path):
        return TelemetryStatus(
            last_sample_at=None, sensor_tier=None, sample_count=0, gap_seconds=None
        )
    conn = connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
        if count == 0:
            return TelemetryStatus(
                last_sample_at=None, sensor_tier=None, sample_count=0, gap_seconds=None
            )
        row = conn.execute(
            "SELECT timestamp, sensor_tier FROM telemetry ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        last_sample_at, sensor_tier = row
        return TelemetryStatus(
            last_sample_at=last_sample_at,
            sensor_tier=sensor_tier,
            sample_count=count,
            gap_seconds=time.time() - last_sample_at,
        )
    finally:
        conn.close()
