from core.track import TrackPoint
from ingest.track_io import read_track_csv, write_track_csv


def test_round_trip_with_env_fields_populated(tmp_path):
    points = [
        TrackPoint(
            t_epoch_s=0.0,
            lat_deg=41.0,
            lon_deg=8.0,
            hs_m=1.5,
            period_peak_s=6.0,
            period_mean_s=5.0,
            wave_from_deg=200.0,
            wind_u_ms=1.0,
            wind_v_ms=-2.0,
        )
    ]
    path = tmp_path / "track.csv"
    write_track_csv(path, points)
    assert read_track_csv(path) == points


def test_round_trip_without_env_fields_uses_none(tmp_path):
    points = [TrackPoint(t_epoch_s=3600.0, lat_deg=41.1, lon_deg=8.1)]
    path = tmp_path / "track.csv"
    write_track_csv(path, points)
    back = read_track_csv(path)
    assert back == points
    assert back[0].hs_m is None


def test_write_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "track.csv"
    write_track_csv(path, [TrackPoint(t_epoch_s=0.0, lat_deg=41.0, lon_deg=8.0)])
    assert path.exists()
