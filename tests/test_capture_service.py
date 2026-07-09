"""capture/service.py's PGN-to-TelemetrySample mapping (ticket B1/B5).

Regression coverage for a real bug found empirically while writing these
tests (not theorised in advance): the `nmea2000` library reports PGN
129026's `cog` field in **radians** and `sog` in **m/s** -- the first
mapping written here passed them straight through as `cog_deg`/`sog_kn`
unconverted. Caught by cross-checking `unit_of_measurement` on a real
decoded field, not assumed from the field names alone.
"""

from __future__ import annotations

import math

import pytest

from capture.pgn import DecodedField, DecodedPgn
from capture.service import (
    _COG_SOG_PGN,
    _ENGINE_DYNAMIC_PGN,
    _ENGINE_RAPID_PGN,
    _POSITION_PGN,
    sample_from_decoded_pgn,
)


def test_position_pgn_maps_lat_lon_directly():
    decoded = DecodedPgn(
        pgn=_POSITION_PGN,
        name="positionRapidUpdate",
        fields=(
            DecodedField("latitude", 42.5, "deg"),
            DecodedField("longitude", 8.1, "deg"),
        ),
    )
    sample = sample_from_decoded_pgn(decoded, timestamp=100.0, sensor_tier="nmea_manual_fuel")
    assert sample.lat_deg == 42.5
    assert sample.lon_deg == 8.1


def test_cog_sog_pgn_converts_radians_to_degrees_and_ms_to_knots():
    decoded = DecodedPgn(
        pgn=_COG_SOG_PGN,
        name="cogSogRapidUpdate",
        fields=(
            DecodedField("cog", math.pi, "rad"),  # 180 degrees
            DecodedField("sog", 1.0, "m/s"),  # ~1.94384 kn
        ),
    )
    sample = sample_from_decoded_pgn(decoded, timestamp=100.0, sensor_tier="nmea_manual_fuel")
    assert sample.cog_deg == pytest.approx(180.0)
    assert sample.sog_kn == pytest.approx(1.94384, abs=1e-4)


def test_cog_sog_pgn_handles_missing_fields_gracefully():
    decoded = DecodedPgn(pgn=_COG_SOG_PGN, name="cogSogRapidUpdate", fields=())
    sample = sample_from_decoded_pgn(decoded, timestamp=100.0, sensor_tier="nmea_manual_fuel")
    assert sample.cog_deg is None
    assert sample.sog_kn is None


def test_engine_rapid_pgn_maps_rpm_directly():
    decoded = DecodedPgn(
        pgn=_ENGINE_RAPID_PGN,
        name="engineParametersRapidUpdate",
        fields=(DecodedField("speed", 1800.0, "rpm"),),
    )
    sample = sample_from_decoded_pgn(decoded, timestamp=100.0, sensor_tier="nmea_manual_fuel")
    assert sample.engine_rpm == 1800.0


def test_engine_dynamic_pgn_maps_fuel_rate_directly():
    # fuelRate's own unit is already L/h (checked against a real decoded
    # field during implementation) -- no conversion needed here, unlike
    # cog/sog above.
    decoded = DecodedPgn(
        pgn=_ENGINE_DYNAMIC_PGN,
        name="engineParametersDynamic",
        fields=(DecodedField("fuelRate", 12.5, "L/h"),),
    )
    sample = sample_from_decoded_pgn(decoded, timestamp=100.0, sensor_tier="nmea_manual_fuel")
    assert sample.engine_fuel_rate_l_per_h == 12.5


def test_unmapped_pgn_returns_none():
    decoded = DecodedPgn(pgn=999999, name="unknown", fields=())
    assert sample_from_decoded_pgn(decoded, timestamp=100.0, sensor_tier="nmea_manual_fuel") is None
