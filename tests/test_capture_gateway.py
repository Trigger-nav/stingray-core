"""Gateway abstraction tests (ticket B1/B5). `decompose_can_id`/
`_parse_yd_raw_line` are checked against a real, cited example (a Yacht
Devices RAW dump of PGN 129808) -- see capture/gateway.py's module
docstring for the sourcing discipline. The two real transport
implementations are exercised against a real local socket/fake serial
byte source, not a mock of the parsing logic itself.
"""

from __future__ import annotations

import socket
import threading

import pytest

from capture.gateway import (
    _DLE,
    _ETX,
    _N2K_MSG_RECEIVED,
    _STX,
    ActisenseSerialGateway,
    RawFrame,
    ReplayGateway,
    YachtDevicesEthernetGateway,
    _decode_actisense_body,
    _parse_yd_raw_line,
    _read_actisense_frames,
    decompose_can_id,
    recompose_can_id,
)

# A real, cited example: canboat community documentation of a Yacht
# Devices RAW dump, stated to be a PGN 129808 message.
REAL_YD_LINE = "01:36:27.998 R 11FB1000 00 3E 78 6C 21 50 25 21"
REAL_YD_CAN_ID = 0x11FB1000
REAL_YD_PGN = 129808


def test_decompose_can_id_matches_the_real_cited_example():
    priority, pgn, source, destination = decompose_can_id(REAL_YD_CAN_ID)
    assert pgn == REAL_YD_PGN
    assert priority == 4
    assert source == 0x00
    assert destination is None  # PDU2/broadcast format (PF=0xFB >= 240)


def test_recompose_can_id_round_trips_through_decompose():
    for priority, pgn, source in [(2, 127488, 17), (6, 129025, 200), (3, 129808, 5)]:
        can_id = recompose_can_id(priority, pgn, source)
        got_priority, got_pgn, got_source, _ = decompose_can_id(can_id)
        assert (got_priority, got_pgn, got_source) == (priority, pgn, source)


def test_parse_yd_raw_line_matches_the_real_cited_example():
    frame = _parse_yd_raw_line(REAL_YD_LINE)
    assert frame is not None
    assert frame.pgn == REAL_YD_PGN
    assert frame.priority == 4
    assert frame.data == bytes.fromhex("003E786C21502521")


@pytest.mark.parametrize(
    "line",
    ["", "garbage", "01:36:27.998 X 11FB1000 00", "01:36:27.998 R nothex 00"],
)
def test_parse_yd_raw_line_returns_none_for_malformed_input(line):
    assert _parse_yd_raw_line(line) is None


def _serve_yd_raw_lines(lines: list[str]):
    """Real local TCP server yielding canned RAW lines, for exercising
    `YachtDevicesEthernetGateway`'s actual socket-reading code, not a mock
    of it."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        conn, _ = server.accept()
        with conn:
            for line in lines:
                conn.sendall((line + "\n").encode("ascii"))
        server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, thread


def test_yacht_devices_ethernet_gateway_reads_real_frames_over_a_socket():
    port, thread = _serve_yd_raw_lines([REAL_YD_LINE, REAL_YD_LINE])
    gateway = YachtDevicesEthernetGateway(host="127.0.0.1", port=port, timeout_s=5.0)
    frames = list(gateway.raw_frames())
    thread.join(timeout=2)
    assert len(frames) == 2
    assert all(f.pgn == REAL_YD_PGN for f in frames)


def _build_actisense_frame(priority: int, pgn: int, dst: int, src: int, data: bytes) -> bytes:
    payload = (
        bytes([priority])
        + pgn.to_bytes(3, "little")
        + bytes([dst, src])
        + bytes(4)
        + bytes([len(data)])
        + data
    )
    body = bytes([_N2K_MSG_RECEIVED, len(payload)]) + payload
    checksum = (256 - (sum(body) % 256)) % 256
    body_with_checksum = body + bytes([checksum])
    assert sum(body_with_checksum) % 256 == 0
    stuffed = bytearray()
    for b in body_with_checksum:
        if b == _DLE:
            stuffed.append(_DLE)
        stuffed.append(b)
    return bytes([_DLE, _STX]) + bytes(stuffed) + bytes([_DLE, _ETX])


def test_decode_actisense_body_round_trips_a_synthetic_frame():
    data = bytes([1, 2, 3, 4, 5, 6, 7, 8])
    raw = _build_actisense_frame(priority=3, pgn=127488, dst=255, src=17, data=data)
    # strip DLE STX / DLE ETX framing and de-stuff, mirroring what
    # _read_actisense_frames' state machine does before calling
    # _decode_actisense_body -- tested directly here for a tight unit test.
    inner = raw[2:-2]
    destuffed = bytearray()
    i = 0
    while i < len(inner):
        if inner[i] == _DLE:
            i += 1
        destuffed.append(inner[i])
        i += 1
    frame = _decode_actisense_body(bytes(destuffed))
    assert frame is not None
    assert frame.pgn == 127488
    assert frame.priority == 3
    assert frame.source_address == 17
    assert frame.destination_address is None  # 255 -> broadcast
    assert frame.data == data


def test_read_actisense_frames_from_a_fake_byte_source():
    data = bytes([10, 20, 30, 40])
    raw = _build_actisense_frame(priority=2, pgn=129025, dst=255, src=42, data=data)
    it = iter(raw)

    def read(_n: int) -> bytes:
        try:
            return bytes([next(it)])
        except StopIteration:
            return b""

    gen = _read_actisense_frames(read)
    frame = next(gen)
    assert frame.pgn == 129025
    assert frame.source_address == 42
    assert frame.data == data


def test_actisense_serial_gateway_uses_the_shared_frame_reader(monkeypatch):
    # Exercises ActisenseSerialGateway's own raw_frames() wiring (deferred
    # pyserial import + Serial(...).read hookup) without a real serial
    # port, by monkeypatching pyserial's Serial class.
    data = bytes([1, 2, 3])
    raw = _build_actisense_frame(priority=1, pgn=127489, dst=255, src=9, data=data)
    it = iter(raw)

    class _FakeSerial:
        def __init__(self, *_args, **_kwargs):
            pass

        def read(self, n: int) -> bytes:
            try:
                return bytes([next(it)])
            except StopIteration:
                return b""

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    import serial

    monkeypatch.setattr(serial, "Serial", _FakeSerial)
    gateway = ActisenseSerialGateway(device_path="/dev/fake", baudrate=115200)
    frame = next(gateway.raw_frames())
    assert frame.pgn == 127489
    assert frame.data == data


def test_replay_gateway_yields_frames_in_order():
    frames = [
        RawFrame(
            pgn=1, priority=0, source_address=0, destination_address=None, data=b"", timestamp=0.0
        ),
        RawFrame(
            pgn=2, priority=0, source_address=0, destination_address=None, data=b"", timestamp=1.0
        ),
    ]
    gateway = ReplayGateway(frames)
    assert list(gateway.raw_frames()) == frames
