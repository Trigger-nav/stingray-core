"""Telemetry schema tests (ticket B1/B5): tier-flagging and the optional,
tier-flagged motion/IMU field (B5: "nothing in MVP scope depends on it").
"""

from __future__ import annotations

from capture.telemetry import MotionSample, TelemetrySample


def test_sample_without_motion_is_valid_and_motion_is_none():
    sample = TelemetrySample(timestamp=0.0, sensor_tier="nmea_manual_fuel")
    assert sample.motion is None
    assert sample.sensor_tier == "nmea_manual_fuel"


def test_sample_with_motion_carries_the_full_field():
    motion = MotionSample(heave_m=0.3, roll_deg=2.1, pitch_deg=-1.0, accel_vertical_ms2=9.9)
    sample = TelemetrySample(timestamp=0.0, sensor_tier="full_integration", motion=motion)
    assert sample.motion is motion
    assert sample.motion.roll_deg == 2.1


def test_sensor_tier_is_independent_of_which_fields_are_populated():
    # a nmea_manual_fuel-tier sample can still carry position/COG-SOG
    # fields -- the tier describes the *installation*, not this one
    # sample's own field population.
    sample = TelemetrySample(
        timestamp=0.0, sensor_tier="nmea_manual_fuel", lat_deg=42.0, lon_deg=8.0
    )
    assert sample.sensor_tier == "nmea_manual_fuel"
    assert sample.lat_deg == 42.0
