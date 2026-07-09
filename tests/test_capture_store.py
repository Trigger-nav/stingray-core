"""Local telemetry store tests (ticket B1/B5, plan review addition #1):
basic append/read round-trip, plus a genuine cross-*process* concurrent
read/write test -- exercising the real WAL/busy_timeout behaviour design
addition #1 asked for, not just cross-thread (a subprocess writer is the
same shape `capture/service.py` and the planner process's `GET /v1/
telemetry/status` handler actually have in production, design 8).
"""

from __future__ import annotations

import multiprocessing
import sqlite3
import time

from capture.store import append, connect, query_status, read_all
from capture.telemetry import MotionSample, TelemetrySample


def test_append_and_read_all_round_trip(tmp_path):
    db_path = str(tmp_path / "telemetry.sqlite3")
    conn = connect(db_path)
    sample = TelemetrySample(
        timestamp=1000.0,
        sensor_tier="full_integration",
        lat_deg=42.5,
        lon_deg=8.1,
        sog_kn=10.2,
        engine_rpm=1500.0,
        motion=MotionSample(heave_m=0.1, roll_deg=1.0, pitch_deg=-0.5, accel_vertical_ms2=9.81),
        source_pgn=129025,
    )
    append(conn, sample)
    got = read_all(conn)
    conn.close()
    assert got == [sample]


def test_append_multiple_pgns_at_the_same_timestamp(tmp_path):
    # PRIMARY KEY (timestamp, source_pgn) -- two different PGNs at the
    # same instant are two distinct rows, not a collision.
    db_path = str(tmp_path / "telemetry.sqlite3")
    conn = connect(db_path)
    append(conn, TelemetrySample(timestamp=1.0, sensor_tier="nmea_manual_fuel", source_pgn=129025))
    append(conn, TelemetrySample(timestamp=1.0, sensor_tier="nmea_manual_fuel", source_pgn=129026))
    got = read_all(conn)
    conn.close()
    assert len(got) == 2


def test_query_status_on_missing_file_returns_empty_status(tmp_path):
    status = query_status(str(tmp_path / "does_not_exist.sqlite3"))
    assert status.sample_count == 0
    assert status.last_sample_at is None
    assert status.gap_seconds is None


def test_query_status_reflects_the_most_recent_sample(tmp_path):
    db_path = str(tmp_path / "telemetry.sqlite3")
    conn = connect(db_path)
    append(conn, TelemetrySample(timestamp=100.0, sensor_tier="nmea_manual_fuel", source_pgn=1))
    append(conn, TelemetrySample(timestamp=200.0, sensor_tier="full_integration", source_pgn=2))
    conn.close()

    status = query_status(db_path)
    assert status.sample_count == 2
    assert status.last_sample_at == 200.0
    assert status.sensor_tier == "full_integration"
    assert status.gap_seconds > 0  # 200.0 is long in the past relative to time.time()


def _writer_process(db_path: str, n_rows: int) -> None:
    conn = connect(db_path)
    try:
        for i in range(n_rows):
            append(
                conn,
                TelemetrySample(
                    timestamp=float(i),
                    sensor_tier="nmea_manual_fuel",
                    source_pgn=127488,
                    engine_rpm=float(i),
                ),
            )
    finally:
        conn.close()


def test_concurrent_read_write_across_processes_no_locked_errors(tmp_path):
    db_path = str(tmp_path / "telemetry.sqlite3")
    n_rows = 300
    writer = multiprocessing.Process(target=_writer_process, args=(db_path, n_rows))
    writer.start()

    locked_errors = []
    deadline = time.time() + 20.0
    while (writer.is_alive() or not locked_errors) and time.time() < deadline:
        try:
            query_status(db_path)
        except sqlite3.OperationalError as exc:
            locked_errors.append(str(exc))
        if not writer.is_alive():
            break
        time.sleep(0.005)
    writer.join(timeout=10)

    assert not writer.is_alive(), "writer process did not finish in time"
    assert not locked_errors, f"WAL/busy_timeout did not prevent locked errors: {locked_errors}"

    final_status = query_status(db_path)
    assert final_status.sample_count == n_rows
