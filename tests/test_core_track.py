import pytest

from core.track import TrackPoint, covering_bbox, covering_time_range_s


def _fabricated_track(n=20):
    return [
        TrackPoint(t_epoch_s=float(i * 600), lat_deg=41.0 + i * 0.05, lon_deg=8.0 + i * 0.03)
        for i in range(n)
    ]


def test_covering_bbox_contains_every_point_plus_margin():
    points = _fabricated_track()
    lon_min, lat_min, lon_max, lat_max = covering_bbox(points, margin_deg=0.25)
    for p in points:
        assert lon_min <= p.lon_deg <= lon_max
        assert lat_min <= p.lat_deg <= lat_max
    # doesn't wildly overshoot: raw point spread + a bit more than the margin
    raw_lon_min = min(p.lon_deg for p in points)
    raw_lon_max = max(p.lon_deg for p in points)
    assert lon_min == pytest.approx(raw_lon_min - 0.25)
    assert lon_max == pytest.approx(raw_lon_max + 0.25)


def test_covering_time_range_s():
    points = _fabricated_track()
    t_min, t_max = covering_time_range_s(points)
    assert t_min == 0.0
    assert t_max == 19 * 600.0


def test_covering_bbox_empty_track_raises():
    with pytest.raises(ValueError):
        covering_bbox([])


def test_covering_bbox_raises_on_antimeridian_crossing_track():
    # minor flag 3: a track straddling +/-180 longitude must raise rather
    # than silently producing a wrong (near-360-degree-wide) bbox.
    points = [
        TrackPoint(t_epoch_s=0.0, lat_deg=41.0, lon_deg=-179.5),
        TrackPoint(t_epoch_s=3600.0, lat_deg=41.0, lon_deg=179.5),
    ]
    with pytest.raises(ValueError, match="antimeridian"):
        covering_bbox(points)


def test_covering_bbox_does_not_false_positive_on_a_wide_but_non_crossing_track():
    # a genuinely wide (but not antimeridian-crossing) track must not raise.
    points = [
        TrackPoint(t_epoch_s=0.0, lat_deg=41.0, lon_deg=-60.0),
        TrackPoint(t_epoch_s=3600.0, lat_deg=41.0, lon_deg=60.0),
    ]
    bbox = covering_bbox(points, margin_deg=0.0)
    assert bbox == (-60.0, 41.0, 60.0, 41.0)
