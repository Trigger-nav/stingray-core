"""NMEA 2000 gateway abstraction (ticket B1/B5): one `GatewayReader`
protocol, two real transport implementations (Ethernet TCP, serial-over-
USB), and one test double -- matching this project's established
`Synthetic*`/`Real*` pairing convention (e.g. `core.geography`'s
`SyntheticGeography`/`RealGeography`).

Both real implementations decode the *transport* framing only (getting
from raw bytes on the wire to a decomposed CAN frame: PGN + priority +
addresses + 8 data bytes) -- PGN-specific field decoding (turning those
8 bytes into "SOG is 12.3 kn") is `capture/pgn.py`'s job, via the
`nmea2000` PyPI package, not this module's.

Protocol sourcing discipline (CLAUDE.md's "no invented numbers" applies
here too, not just twin coefficients): every framing constant below is
either pulled directly from canboat's own reference implementation
(Apache-2.0, github.com/canboat/canboat -- the community's de facto
reference for this protocol) or cross-verified against a real, cited
example dump. Neither was checked against the primary vendor manuals
themselves in this session -- no PDF-rendering tooling was available in
this sandbox (tried and failed, see docs/plans/ticket-B1.md) -- flagged,
not hidden, same discipline as ticket 0.5's cfgrib gap. A real-hardware
verification pass (one real YDEN-02 and/or Actisense unit) is listed in
the Tests section as a pending manual step.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RawFrame:
    """One decomposed NMEA 2000 / J1939 CAN frame -- transport-layer only,
    not yet PGN-decoded."""

    pgn: int
    priority: int
    source_address: int
    destination_address: int | None  # None = broadcast (PDU2-format PGN)
    data: bytes
    timestamp: float  # unix epoch seconds, receipt time


class GatewayReader(Protocol):
    def raw_frames(self) -> Iterator[RawFrame]:
        """Blocking iterator, one frame at a time -- yields until the
        underlying connection closes or errors."""
        ...


def decompose_can_id(can_id: int) -> tuple[int, int, int, int | None]:
    """Standard SAE J1939 29-bit extended CAN ID decomposition -- NMEA 2000
    rides on J1939's physical/data-link layer, so this is protocol-level,
    not vendor-specific. Returns (priority, pgn, source_address,
    destination_address).

    Verified against a real, cited example (a Yacht Devices RAW dump of
    PGN 129808, referenced in canboat community discussion): CAN ID
    0x11FB1000 decomposes here to priority=4, PDU-format byte 0xFB (>=240,
    so PDU2/broadcast format), pgn=(1<<16)|(0xFB<<8)|0x10 = 129808 --
    exactly matching that dump's own stated PGN, independently confirming
    this decomposition is correct."""
    priority = (can_id >> 26) & 0x7
    data_page = (can_id >> 24) & 0x1
    pdu_format = (can_id >> 16) & 0xFF
    pdu_specific = (can_id >> 8) & 0xFF
    source_address = can_id & 0xFF
    if pdu_format < 240:
        # PDU1 (addressed): the PS byte is a destination address, not part
        # of the PGN itself.
        pgn = (data_page << 16) | (pdu_format << 8)
        destination_address: int | None = pdu_specific
    else:
        # PDU2 (broadcast): the PS byte is a "group extension", part of
        # the PGN; there is no destination.
        pgn = (data_page << 16) | (pdu_format << 8) | pdu_specific
        destination_address = None
    return priority, pgn, source_address, destination_address


def recompose_can_id(priority: int, pgn: int, source_address: int) -> int:
    """Inverse of `decompose_can_id` -- rebuilds a 29-bit CAN ID from
    (priority, pgn, source). Used by `capture/pgn.py` to feed a `RawFrame`
    into the `nmea2000` PyPI package's candump-format decoder input,
    which expects the original CAN ID, not pre-split fields. Broadcast
    (PDU2) destination is implicit in a PDU2-format PGN's own group
    extension byte; a PDU1 (addressed) PGN's destination isn't
    recoverable from `pgn` alone (it was in the original frame's PS byte,
    already folded into `RawFrame.destination_address` instead) -- for
    PDU1 PGNs this reconstructs the *broadcast* form (PS=0), which is
    sufficient for PGN-field decoding (destination doesn't affect how
    the payload bytes are interpreted)."""
    data_page = (pgn >> 16) & 0x1
    pdu_format = (pgn >> 8) & 0xFF
    pdu_specific = pgn & 0xFF if pdu_format >= 240 else 0
    return (
        ((priority & 0x7) << 26)
        | (data_page << 24)
        | (pdu_format << 16)
        | (pdu_specific << 8)
        | (source_address & 0xFF)
    )


def _parse_yd_raw_line(line: str) -> RawFrame | None:
    """Yacht Devices "RAW" text format, one frame per line:
    `HH:MM:SS.mmm R|T <8-hex-digit CAN ID> <data bytes, 2-hex-digit pairs,
    space-separated>`. Community-documented (Yacht Devices' own manual
    describes this in "Appendix E. Format of Messages in RAW Mode" per
    canboat's documentation references) and cross-verified via
    `decompose_can_id`'s docstring."""
    parts = line.split()
    if len(parts) < 3:
        return None
    _timestamp_str, direction, can_id_hex, *data_hex = parts
    if direction not in ("R", "T"):
        return None
    try:
        can_id = int(can_id_hex, 16)
        data = bytes(int(b, 16) for b in data_hex)
    except ValueError:
        return None
    priority, pgn, source_address, destination_address = decompose_can_id(can_id)
    return RawFrame(
        pgn=pgn,
        priority=priority,
        source_address=source_address,
        destination_address=destination_address,
        data=data,
        timestamp=time.time(),
    )


class YachtDevicesEthernetGateway:
    """YDEN-02 RAW-mode TCP data server (B5's preferred gateway -- bus-
    powered, no host drivers, serves any listener on the ship LAN, so
    logging isn't hostage to one PC or USB port). Default port 1457
    matches Yacht Devices' documented default for the RAW data server."""

    def __init__(self, host: str, port: int = 1457, timeout_s: float = 5.0) -> None:
        self._host = host
        self._port = port
        self._timeout_s = timeout_s

    def raw_frames(self) -> Iterator[RawFrame]:
        with socket.create_connection((self._host, self._port), timeout=self._timeout_s) as sock:
            buffer = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    frame = _parse_yd_raw_line(line.decode("ascii", errors="ignore").strip())
                    if frame is not None:
                        yield frame


# Actisense NGT-1/NGX-1 serial framing constants -- pulled directly from
# canboat's reference implementation (actisense-serial/actisense.h),
# github.com/canboat/canboat, Apache-2.0. Frame shape:
#   DLE STX <command> <len> <data...> <checksum> DLE ETX
# with DLE bytes doubled (byte-stuffed) anywhere inside <data...>.
_DLE = 0x10
_STX = 0x02
_ETX = 0x03
_N2K_MSG_RECEIVED = 0x93


def _decode_actisense_body(body: bytes) -> RawFrame | None:
    """`body` is the de-stuffed bytes between `DLE STX` and the trailing
    `<checksum> DLE ETX` (i.e. `<command> <len> <data...> <checksum>`).
    Field layout for a received N2K message (command 0x93) confirmed
    against canboat's own `n2kMessageReceived()`: priority (1 byte), PGN
    (3 bytes, little-endian), destination (1 byte), source (1 byte),
    timestamp (4 bytes, ignored here -- this frame's own receipt time is
    used instead, matching the Ethernet gateway), data length (1 byte),
    data."""
    if len(body) < 3 or (sum(body) & 0xFF) != 0:
        return None  # too short, or checksum mismatch
    command = body[0]
    if command != _N2K_MSG_RECEIVED:
        return None
    payload = body[2:-1]  # strip command, length byte, and trailing checksum
    if len(payload) < 11:
        return None
    priority = payload[0]
    pgn = payload[1] | (payload[2] << 8) | (payload[3] << 16)
    destination = payload[4]
    source = payload[5]
    data_length = payload[10]
    data = payload[11 : 11 + data_length]
    return RawFrame(
        pgn=pgn,
        priority=priority,
        source_address=source,
        destination_address=None if destination == 0xFF else destination,
        data=bytes(data),
        timestamp=time.time(),
    )


def _read_actisense_frames(read: Callable[[int], bytes]) -> Iterator[RawFrame]:
    """`read(n)` returns up to `n` bytes (or `b""` on timeout/no data yet,
    matching `serial.Serial.read`'s contract) -- factored out from
    `ActisenseSerialGateway` so tests can drive the framing state machine
    from a canned byte sequence without a real serial port."""
    buffer = bytearray()
    in_message = False
    escape_next = False
    while True:
        chunk = read(1)
        if not chunk:
            continue
        byte = chunk[0]
        if not in_message:
            if escape_next and byte == _STX:
                in_message = True
                buffer = bytearray()
            escape_next = byte == _DLE
            continue
        if escape_next:
            escape_next = False
            if byte == _DLE:
                buffer.append(_DLE)  # de-stuffed literal DLE
            elif byte == _ETX:
                frame = _decode_actisense_body(bytes(buffer))
                if frame is not None:
                    yield frame
                in_message = False
            else:
                in_message = False  # malformed framing -- resync on next DLE STX
            continue
        if byte == _DLE:
            escape_next = True
        else:
            buffer.append(byte)


class ActisenseSerialGateway:
    """Actisense NGT-1/NGX-1 serial-over-USB gateway (B5's alternative,
    for vessels where LAN access at the backbone isn't practical).
    115200 baud default, matching the documented NGT-1 default (newer
    firmware/NGX-1 may use 230400 -- configurable here)."""

    def __init__(self, device_path: str, baudrate: int = 115200) -> None:
        self._device_path = device_path
        self._baudrate = baudrate

    def raw_frames(self) -> Iterator[RawFrame]:
        import serial  # deferred: only needed when actually opening a port

        with serial.Serial(self._device_path, self._baudrate, timeout=1.0) as port:
            yield from _read_actisense_frames(port.read)


class ReplayGateway:
    """Test double -- replays a fixed sequence of `RawFrame`s. Matches
    this project's established `Synthetic*`/`Real*` pairing convention;
    lets `capture/service.py`'s full pipeline be tested without hardware."""

    def __init__(self, frames: Sequence[RawFrame]) -> None:
        self._frames = list(frames)

    def raw_frames(self) -> Iterator[RawFrame]:
        yield from self._frames
