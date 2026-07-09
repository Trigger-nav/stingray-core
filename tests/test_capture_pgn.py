"""PGN decode tests (ticket B1/B5) -- against known-good fixture frames
built via the `nmea2000` library's own encoder (a self-consistent
encode/decode round trip through the real, installed dependency, not a
hand-rolled fixture that could silently drift from the library's actual
behaviour). Real-vessel PGN coverage/variability is ROADMAP.md ticket
1.3's job, not verified here -- see capture/pgn.py's module docstring.
"""

from __future__ import annotations

import math

import pytest
from nmea2000.message import NMEA2000Field, NMEA2000Message
from nmea2000.pgns import encode_pgn_127488, encode_pgn_129026

from capture.gateway import RawFrame
from capture.pgn import decode_frame


def test_decode_frame_engine_parameters_rapid_update():
    message = NMEA2000Message(
        PGN=127488,
        fields=[
            NMEA2000Field(id="instance", value=0),
            NMEA2000Field(id="speed", value=1800.0),
            NMEA2000Field(id="boostPressure", value=0),
            NMEA2000Field(id="tiltTrim", value=5),
            NMEA2000Field(id="reserved_48", value=0),
        ],
    )
    data = encode_pgn_127488(message)
    frame = RawFrame(
        pgn=127488,
        priority=2,
        source_address=17,
        destination_address=None,
        data=data,
        timestamp=0.0,
    )
    decoded = decode_frame(frame)
    assert decoded is not None
    assert decoded.pgn == 127488
    field_values = {f.field_id: f.value for f in decoded.fields}
    assert field_values["speed"] == 1800.0
    assert field_values["tiltTrim"] == 5


def test_decode_frame_cog_sog_rapid_update():
    # cog is encoded in radians (0.0001 resolution, 16-bit unsigned caps
    # it at ~6.55 -- found empirically while writing this test: an
    # in-degrees value like 90.0 overflows the field's real encoding).
    cog_rad = math.pi / 2
    message = NMEA2000Message(
        PGN=129026,
        fields=[
            NMEA2000Field(id="sid", value=0),
            NMEA2000Field(id="cogReference", value=0),
            NMEA2000Field(id="reserved_10", value=0),
            NMEA2000Field(id="cog", value=cog_rad),
            NMEA2000Field(id="sog", value=12.5),
            NMEA2000Field(id="reserved_48", value=0),
        ],
    )
    data = encode_pgn_129026(message)
    frame = RawFrame(
        pgn=129026, priority=2, source_address=1, destination_address=None, data=data, timestamp=0.0
    )
    decoded = decode_frame(frame)
    assert decoded is not None
    field_values = {f.field_id: f.value for f in decoded.fields}
    assert field_values["sog"] == 12.5
    assert field_values["cog"] == pytest.approx(cog_rad, abs=1e-3)


def test_decode_frame_returns_none_for_an_unrecognised_pgn():
    frame = RawFrame(
        pgn=999999,
        priority=0,
        source_address=0,
        destination_address=None,
        data=bytes(8),
        timestamp=0.0,
    )
    assert decode_frame(frame) is None
