"""Thin wrapper around the `nmea2000` PyPI package (canboat-PGN-database-
backed decoder, ticket B1/B5) -- isolated behind this one module so the
third-party dependency is swappable later without touching
`capture/gateway.py` (transport-layer, real bytes) or
`capture/telemetry.py` (canonical schema).

**Flagged, not hidden** (matches ticket 0.5's cfgrib-verification
precedent): PGN coverage/correctness was verified here against the
library's own encode/decode round-trip for a couple of representative
PGNs (127488 Engine Parameters Rapid Update, 129026 COG & SOG Rapid
Update) during implementation -- not against a real, idiosyncratic vessel
NMEA 2000 installation. Real-vessel PGN variability is ROADMAP.md ticket
1.3's "first real encounter with PGN variability" job, not this ticket's.
"""

from __future__ import annotations

from dataclasses import dataclass

from nmea2000 import NMEA2000Decoder

from capture.gateway import RawFrame, recompose_can_id

_decoder = NMEA2000Decoder()


@dataclass(frozen=True)
class DecodedField:
    field_id: str
    value: object
    unit: str | None


@dataclass(frozen=True)
class DecodedPgn:
    pgn: int
    name: str
    fields: tuple[DecodedField, ...]


def decode_frame(frame: RawFrame) -> DecodedPgn | None:
    """Returns `None` for a PGN the `nmea2000` library doesn't recognise
    (an unsupported/proprietary PGN, or -- for fast-packet PGNs spanning
    multiple CAN frames -- a single frame that isn't the complete
    reassembled message; fast-packet reassembly across frames isn't
    handled by this thin wrapper, another real-vessel-only gap flagged
    for ticket 1.3, not silently pretended-away here)."""
    can_id = recompose_can_id(frame.priority, frame.pgn, frame.source_address)
    line = f"stingray {can_id:08X} [{len(frame.data)}] " + " ".join(
        f"{b:02x}" for b in frame.data
    )
    message = _decoder.decode(line)
    if message is None:
        return None
    return DecodedPgn(
        pgn=message.PGN,
        name=message.id,
        fields=tuple(
            DecodedField(field_id=f.id, value=f.value, unit=f.unit_of_measurement)
            for f in message.fields
        ),
    )
