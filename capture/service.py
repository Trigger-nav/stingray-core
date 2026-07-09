"""Logging service daemon loop (ticket B1/B5): gateway -> PGN decode ->
telemetry normalise -> local store. A small, known set of PGN mappings
proves the pipeline end-to-end (position 129025, COG/SOG 129026, engine
rapid update 127488, engine dynamic 127489, which carries fuel rate) --
comprehensive PGN coverage against a real, idiosyncratic vessel
installation is ROADMAP.md ticket 1.3's job ("first real encounter with
PGN variability"), not this ticket's. An unmapped PGN is simply not
logged as telemetry yet, not an error.

Runs as its own independent OS process (design 8) -- see
`capture/__init__.py`'s module docstring for why.
"""

from __future__ import annotations

import argparse
import logging
import math
import sqlite3

from capture.gateway import (
    ActisenseSerialGateway,
    GatewayReader,
    RawFrame,
    YachtDevicesEthernetGateway,
)
from capture.pgn import DecodedPgn, decode_frame
from capture.store import append, connect
from capture.telemetry import SensorTier, TelemetrySample
from core.units import ms_to_kn

logger = logging.getLogger(__name__)

# Deliberately small, known set of PGNs -- see module docstring.
_POSITION_PGN = 129025
_COG_SOG_PGN = 129026
_ENGINE_RAPID_PGN = 127488
_ENGINE_DYNAMIC_PGN = 127489


def _field_value(decoded: DecodedPgn, field_id: str) -> object | None:
    for f in decoded.fields:
        if f.field_id == field_id:
            return f.value
    return None


def sample_from_decoded_pgn(
    decoded: DecodedPgn, timestamp: float, sensor_tier: SensorTier
) -> TelemetrySample | None:
    """Maps one of the small set of known PGNs above to a
    `TelemetrySample` -- `None` for anything else."""
    if decoded.pgn == _POSITION_PGN:
        return TelemetrySample(
            timestamp=timestamp,
            sensor_tier=sensor_tier,
            lat_deg=_field_value(decoded, "latitude"),
            lon_deg=_field_value(decoded, "longitude"),
            source_pgn=decoded.pgn,
        )
    if decoded.pgn == _COG_SOG_PGN:
        # The nmea2000 library reports cog in radians and sog in m/s
        # (found empirically while testing this mapping, not assumed --
        # both are the PGN's real, standard NMEA 2000 raw units) --
        # converted here at the capture/telemetry.py schema boundary,
        # matching core/units.py's B1 convention of converting once, at
        # the boundary, not carrying mixed units downstream.
        cog_rad = _field_value(decoded, "cog")
        sog_ms = _field_value(decoded, "sog")
        return TelemetrySample(
            timestamp=timestamp,
            sensor_tier=sensor_tier,
            cog_deg=math.degrees(cog_rad) if isinstance(cog_rad, (int, float)) else None,
            sog_kn=ms_to_kn(sog_ms) if isinstance(sog_ms, (int, float)) else None,
            source_pgn=decoded.pgn,
        )
    if decoded.pgn == _ENGINE_RAPID_PGN:
        return TelemetrySample(
            timestamp=timestamp,
            sensor_tier=sensor_tier,
            engine_rpm=_field_value(decoded, "speed"),
            source_pgn=decoded.pgn,
        )
    if decoded.pgn == _ENGINE_DYNAMIC_PGN:
        return TelemetrySample(
            timestamp=timestamp,
            sensor_tier=sensor_tier,
            engine_fuel_rate_l_per_h=_field_value(decoded, "fuelRate"),
            source_pgn=decoded.pgn,
        )
    return None


def _handle_frame(frame: RawFrame, conn: sqlite3.Connection, sensor_tier: SensorTier) -> None:
    try:
        decoded = decode_frame(frame)
    except Exception:
        logger.exception("failed to decode frame for PGN %d, skipping", frame.pgn)
        return
    if decoded is None:
        return
    sample = sample_from_decoded_pgn(decoded, frame.timestamp, sensor_tier)
    if sample is None:
        return
    try:
        append(conn, sample)
    except Exception:
        logger.exception("failed to store telemetry sample, skipping")


def run(gateway: GatewayReader, db_path: str, sensor_tier: SensorTier) -> None:
    """The daemon loop -- blocks forever consuming `gateway.raw_frames()`.
    Holds one SQLite connection open for the whole run -- `capture/
    store.py`'s `append` takes an already-open connection, not a path,
    for exactly this reason (reconnecting per sample would be wasteful
    at any real message rate)."""
    conn = connect(db_path)
    try:
        for frame in gateway.raw_frames():
            _handle_frame(frame, conn, sensor_tier)
    finally:
        conn.close()


def _build_gateway(args: argparse.Namespace) -> GatewayReader:
    if args.gateway == "yacht-devices":
        return YachtDevicesEthernetGateway(host=args.host, port=args.port)
    return ActisenseSerialGateway(device_path=args.device, baudrate=args.baudrate)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", choices=["yacht-devices", "actisense"], required=True)
    parser.add_argument("--host", default="127.0.0.1", help="yacht-devices: YDEN-02 host")
    parser.add_argument(
        "--port", type=int, default=1457, help="yacht-devices: RAW data server port"
    )
    parser.add_argument("--device", default="/dev/ttyUSB0", help="actisense: serial device path")
    parser.add_argument("--baudrate", type=int, default=115200, help="actisense: baud rate")
    parser.add_argument("--db-path", default="data/telemetry/telemetry.sqlite3")
    parser.add_argument(
        "--sensor-tier",
        choices=["full_integration", "nmea_engine_fuel_rate", "nmea_manual_fuel"],
        default="nmea_manual_fuel",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    gateway = _build_gateway(args)
    logger.info("stingray capture service starting (gateway=%s)", args.gateway)
    run(gateway, args.db_path, args.sensor_tier)


if __name__ == "__main__":
    main()
