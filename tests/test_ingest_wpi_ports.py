"""ingest/fetch_wpi_ports.py's pure functions (ticket R1) -- no real
network call in CI, matching ticket B7's ERA5-mocking precedent. The real
endpoint (`msi.nga.mil/api/publications/world-port-index`) was verified
live during this ticket's planning (real DMS formats, real "ENGLAND SW
COAST" region data including Plymouth/Falmouth) -- see
docs/plans/ticket-R1.md's acceptance-run notes; this file only covers the
parsing/filtering logic, fabricated fixtures throughout.
"""

from __future__ import annotations

import pytest

from ingest.fetch_wpi_ports import _parse_dms, ports_within_bbox

# Real records, verified against a live fetch during this ticket
# (Avonmouth/Plymouth/Falmouth) -- not invented coordinates.
_AVONMOUTH = {
    "portNumber": 35060,
    "portName": "Avonmouth",
    "latitude": "51°30'00\"N",
    "longitude": "2°43'00\"W",
}
_PLYMOUTH = {
    "portNumber": 35310,
    "portName": "Plymouth",
    "latitude": "50°22'00\"N",
    "longitude": "4°09'00\"W",
}
_FALMOUTH = {
    "portNumber": 35340,
    "portName": "Falmouth Harbour",
    "latitude": "50°09'00\"N",
    "longitude": "5°04'00\"W",
}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("51°30'00\"N", 51.5),
        ("2°43'00\"W", -2.716666666666667),
        ("0°00'00\"N", 0.0),
        ("30°20'00.5\"N", 30 + 20 / 60 + 0.5 / 3600),
        ("125°45'12.34\"E", 125 + 45 / 60 + 12.34 / 3600),
    ],
)
def test_parse_dms(value, expected):
    assert _parse_dms(value) == pytest.approx(expected)


def test_parse_dms_raises_on_unrecognised_format():
    with pytest.raises(ValueError, match="unrecognised"):
        _parse_dms("not a coordinate")


def test_ports_within_bbox_filters_correctly():
    ports = [_AVONMOUTH, _PLYMOUTH, _FALMOUTH]
    # Plymouth-Falmouth bbox -- excludes Avonmouth (Bristol Channel, north).
    bbox = (-5.5, 49.8, -3.5, 50.8)
    result = ports_within_bbox(ports, bbox)
    assert "plymouth" in result
    assert "falmouth_harbour" in result
    assert "avonmouth" not in result
    assert result["plymouth"] == pytest.approx((50 + 22 / 60, -(4 + 9 / 60)))


def test_ports_within_bbox_name_normalisation():
    ports = [_FALMOUTH]
    result = ports_within_bbox(ports, (-6.0, 49.0, -4.0, 51.0))
    assert list(result.keys()) == ["falmouth_harbour"]


def test_ports_within_bbox_disambiguates_name_collisions():
    duplicate = dict(_PLYMOUTH, portNumber=99999)
    result = ports_within_bbox([_PLYMOUTH, duplicate], (-5.5, 49.8, -3.5, 50.8))
    assert "plymouth" in result
    assert "plymouth_99999" in result


def test_ports_within_bbox_skips_unparseable_entries_without_raising():
    bad = dict(_PLYMOUTH, latitude="garbage")
    result = ports_within_bbox([bad, _FALMOUTH], (-5.5, 49.8, -3.5, 50.8))
    assert "plymouth" not in result
    assert "falmouth_harbour" in result


def test_ports_within_bbox_empty_result_for_a_bbox_with_no_ports():
    result = ports_within_bbox([_AVONMOUTH], (-5.5, 49.8, -3.5, 50.8))
    assert result == {}
