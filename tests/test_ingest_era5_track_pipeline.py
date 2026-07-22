"""End-to-end ERA5 annotator pipeline test -- cdsapi.Client.retrieve is
monkeypatched to write a fabricated response matching the REAL CDS
response shape confirmed live during this ticket's manual verification
(2026-07-14, see docs/plans/ticket-B7.md's "Live ERA5 verification
result"): a zip archive containing two separate per-stream NetCDF files
(wind/`oper` and wave), at *different* native resolutions (wave is
coarser). No real network/credentials in any automated test, matching
ticket 0.5's own precedent.
"""

from __future__ import annotations

import logging
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

import ingest.fetch_era5_track as era5_track
from core.geography import RealGeography
from core.track import TrackPoint
from ingest.track_io import read_track_csv, write_track_csv


def _oper_dataset(lats, lons, times, *, u10_value=4.0, v10_value=-1.5):
    shape = (len(times), len(lats), len(lons))
    return xr.Dataset(
        {
            "u10": (("valid_time", "latitude", "longitude"), np.full(shape, u10_value)),
            "v10": (("valid_time", "latitude", "longitude"), np.full(shape, v10_value)),
        },
        coords={
            "valid_time": np.array(times, dtype="datetime64[ns]"),
            "latitude": lats,
            "longitude": lons,
        },
    )


def _wave_dataset(
    lats, lons, times, *, hs_value=1.2, pp1d_value=7.0, mwp_value=5.5, mwd_value=210.0
):
    shape = (len(times), len(lats), len(lons))
    return xr.Dataset(
        {
            "swh": (("valid_time", "latitude", "longitude"), np.full(shape, hs_value)),
            "mwd": (("valid_time", "latitude", "longitude"), np.full(shape, mwd_value)),
            "pp1d": (("valid_time", "latitude", "longitude"), np.full(shape, pp1d_value)),
            "mwp": (("valid_time", "latitude", "longitude"), np.full(shape, mwp_value)),
        },
        coords={
            "valid_time": np.array(times, dtype="datetime64[ns]"),
            "latitude": lats,
            "longitude": lons,
        },
    )


def _fabricate_era5_zip(dest, *, wind_lats, wind_lons, wave_lats, wave_lons, times, **wave_kwargs):
    """Matches the real CDS response shape found live: a zip archive with
    `data_stream-oper_stepType-instant.nc` (wind) and `data_stream-wave_
    stepType-instant.nc` (wave), independently gridded -- wave is commonly
    coarser than wind in the real product."""
    dest = Path(dest)
    oper = _oper_dataset(wind_lats, wind_lons, times)
    wave = _wave_dataset(wave_lats, wave_lons, times, **wave_kwargs)
    oper_path = dest.parent / "_oper_tmp.nc"
    wave_path = dest.parent / "_wave_tmp.nc"
    oper.to_netcdf(oper_path)
    wave.to_netcdf(wave_path)
    with zipfile.ZipFile(dest, "w") as zf:
        zf.write(oper_path, "data_stream-oper_stepType-instant.nc")
        zf.write(wave_path, "data_stream-wave_stepType-instant.nc")
    oper_path.unlink()
    wave_path.unlink()


def _fabricate_era5_netcdf(dest, *, lats, lons, times, hs_value=1.2, pp1d_value=7.0, mwp_value=5.5):
    """A single, non-zip, combined-stream response -- covers
    _open_era5_response's fallback branch, in case some request shape
    ever returns a raw file instead of a zip."""
    shape = (len(times), len(lats), len(lons))
    ds = xr.Dataset(
        {
            "swh": (("time", "latitude", "longitude"), np.full(shape, hs_value)),
            "mwd": (("time", "latitude", "longitude"), np.full(shape, 210.0)),
            "pp1d": (("time", "latitude", "longitude"), np.full(shape, pp1d_value)),
            "mwp": (("time", "latitude", "longitude"), np.full(shape, mwp_value)),
            "u10": (("time", "latitude", "longitude"), np.full(shape, 4.0)),
            "v10": (("time", "latitude", "longitude"), np.full(shape, -1.5)),
        },
        coords={
            "time": np.array(times, dtype="datetime64[ns]"),
            "latitude": lats,
            "longitude": lons,
        },
    )
    ds.to_netcdf(dest)


class _FakeCdsClient:
    calls: list[dict] = []

    def __init__(self):
        pass

    def retrieve(self, dataset, request, target):
        _FakeCdsClient.calls.append(request)
        _fabricate_era5_zip(
            target,
            wind_lats=np.array([40.0, 41.0, 42.0, 43.0]),
            wind_lons=np.array([6.0, 7.0, 8.0, 9.0]),
            wave_lats=np.array([40.0, 41.0, 42.0, 43.0]),
            wave_lons=np.array([6.0, 7.0, 8.0, 9.0]),
            times=["2026-01-01T00:00:00", "2026-01-01T03:00:00", "2026-01-01T06:00:00"],
        )


@pytest.fixture
def fake_cdsapi(monkeypatch):
    import cdsapi

    _FakeCdsClient.calls = []
    monkeypatch.setattr(cdsapi, "Client", _FakeCdsClient)
    return _FakeCdsClient


def test_annotate_track_end_to_end_matches_known_analytic_field(tmp_path, fake_cdsapi):
    track = [
        TrackPoint(t_epoch_s=1767225600.0, lat_deg=41.0, lon_deg=7.0),
        TrackPoint(t_epoch_s=1767225600.0 + 3600.0, lat_deg=41.5, lon_deg=7.5),
    ]
    track_csv = tmp_path / "track.csv"
    write_track_csv(track_csv, track)
    out_csv = tmp_path / "annotated.csv"

    sys.argv = ["fetch_era5_track", str(track_csv), "--out", str(out_csv)]
    era5_track.main()

    annotated = read_track_csv(out_csv)
    assert len(annotated) == 2
    for p in annotated:
        assert p.hs_m == pytest.approx(1.2, abs=1e-6)
        assert p.period_peak_s == pytest.approx(7.0, abs=1e-6)
        assert p.period_mean_s == pytest.approx(5.5, abs=1e-6)
        assert p.wind_u_ms == pytest.approx(4.0, abs=1e-6)
        assert p.wind_v_ms == pytest.approx(-1.5, abs=1e-6)

    # exactly one monolithic CDS request for the whole track, not per-point.
    assert len(fake_cdsapi.calls) == 1


def test_large_span_logs_a_warning(tmp_path, fake_cdsapi, caplog):
    t0 = 1767225600.0
    track = [
        TrackPoint(t_epoch_s=t0, lat_deg=41.0, lon_deg=7.0),
        TrackPoint(t_epoch_s=t0 + 40 * 86400.0, lat_deg=41.5, lon_deg=7.5),  # 40 days
    ]
    track_csv = tmp_path / "track.csv"
    write_track_csv(track_csv, track)
    out_csv = tmp_path / "annotated.csv"

    sys.argv = ["fetch_era5_track", str(track_csv), "--out", str(out_csv)]
    with caplog.at_level(logging.WARNING, logger="ingest.fetch_era5_track"):
        era5_track.main()

    assert any("40" in r.getMessage() or "days" in r.getMessage() for r in caplog.records)


def test_short_span_does_not_log_a_warning(tmp_path, fake_cdsapi, caplog):
    t0 = 1767225600.0
    track = [
        TrackPoint(t_epoch_s=t0, lat_deg=41.0, lon_deg=7.0),
        TrackPoint(t_epoch_s=t0 + 3600.0, lat_deg=41.5, lon_deg=7.5),
    ]
    track_csv = tmp_path / "track.csv"
    write_track_csv(track_csv, track)
    out_csv = tmp_path / "annotated.csv"

    sys.argv = ["fetch_era5_track", str(track_csv), "--out", str(out_csv)]
    with caplog.at_level(logging.WARNING, logger="ingest.fetch_era5_track"):
        era5_track.main()

    # Scoped to this module's own logger, not caplog.records overall --
    # ticket W1's coastal-fill mechanism (ingest.grib_common) can
    # legitimately log its own, unrelated WARNING for this fixture's real
    # western-Med geography (a masked cell filled from a real, if
    # longer-range, neighbour); this test only asserts the large-span
    # warning this module itself owns doesn't fire for a short span.
    assert [r for r in caplog.records if r.name == "ingest.fetch_era5_track"] == []


def test_build_grid_from_netcdf_handles_the_raw_non_zip_fallback(tmp_path):
    lats = np.array([40.0, 41.0, 42.0])
    lons = np.array([6.0, 7.0, 8.0])
    nc = tmp_path / "era5.nc"
    _fabricate_era5_netcdf(nc, lats=lats, lons=lons, times=["2026-01-01T00:00:00"])
    geo = RealGeography()
    field = era5_track.build_grid_from_netcdf(nc, geo, reference_epoch_s=1767225600.0)
    sample = field.sample(41.0, 7.0, 0.0)
    assert sample.wind_u_ms == pytest.approx(4.0, abs=1e-6)


# --- regression tests for the two real defects found during live
# verification (2026-07-14): a real CDS response is a zip of two
# per-stream files at different native resolutions, not one raw NetCDF. --


def test_open_era5_response_extracts_a_zip_with_two_streams(tmp_path):
    dest = tmp_path / "era5.nc"  # note: .nc extension, but really a zip
    _fabricate_era5_zip(
        dest,
        wind_lats=np.array([40.0, 41.0]),
        wind_lons=np.array([6.0, 7.0]),
        wave_lats=np.array([40.0, 41.0]),
        wave_lons=np.array([6.0, 7.0]),
        times=["2026-01-01T00:00:00"],
    )
    datasets = era5_track._open_era5_response(dest)
    assert len(datasets) == 2
    all_vars = set()
    for ds in datasets:
        all_vars |= set(ds.data_vars)
    assert all_vars == {"u10", "v10", "swh", "mwd", "pp1d", "mwp"}


def test_build_grid_from_netcdf_broadcasts_a_coarser_single_point_wave_stream(tmp_path):
    """The exact live-found scenario: wind comes back on a real 3x3 grid,
    wave degenerates to a single point (its native cell is wider than the
    request bbox) -- must not silently produce NaN (as bare `.interp()`
    did, confirmed live) and must not raise."""
    dest = tmp_path / "era5.nc"
    _fabricate_era5_zip(
        dest,
        wind_lats=np.array([40.0, 41.0, 42.0]),
        wind_lons=np.array([6.0, 7.0, 8.0]),
        wave_lats=np.array([41.0]),
        wave_lons=np.array([7.0]),
        times=["2026-01-01T00:00:00"],
        hs_value=0.75,
        mwd_value=200.0,
    )
    geo = RealGeography()
    field = era5_track.build_grid_from_netcdf(dest, geo, reference_epoch_s=1767225600.0)
    # every wind grid point gets the wave stream's one value, broadcast --
    # not NaN, and not just the exact wave source coordinate.
    for lat, lon in ((40.0, 6.0), (41.0, 7.0), (42.0, 8.0)):
        sample = field.sample(lat, lon, 0.0)
        assert sample.hs_m == pytest.approx(0.75, abs=1e-6)
        assert not np.isnan(sample.wind_u_ms)


def test_merge_era5_streams_downsamples_onto_the_finer_grid():
    times = ["2026-01-01T00:00:00"]
    oper = era5_track.normalise_and_sort_dataset(
        _oper_dataset(np.array([40.0, 41.0, 42.0]), np.array([6.0, 7.0, 8.0]), times)
    )
    wave = era5_track.normalise_and_sort_dataset(
        _wave_dataset(np.array([41.0]), np.array([7.0]), times, hs_value=2.5)
    )
    merged = era5_track._merge_era5_streams([oper, wave])
    assert merged.sizes["latitude"] == 3
    assert merged.sizes["longitude"] == 3
    assert set(merged.data_vars) == {"u10", "v10", "swh", "mwd", "pp1d", "mwp"}
    assert float(merged["swh"].isel(valid_time=0, latitude=0, longitude=0)) == pytest.approx(2.5)
