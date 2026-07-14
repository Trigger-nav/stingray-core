import csv
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from core.track import TrackPoint
from fit.import_adapters import (
    MARINE_DIESEL_KG_PER_L,
    AnnotatedTrackCsvAdapter,
    ELogbookAdapter,
    MonitoringCsvAdapter,
    NoonReportAdapter,
)
from ingest.track_io import write_track_csv

MONITORING_HEADER = [
    "timestamp_utc", "lat_deg", "lon_deg", "stw_kn", "heading_deg",
    "active_engines", "fuel_kg_per_h", "hs_m", "period_peak_s", "wave_from_deg",
]
ELOGBOOK_HEADER = [
    "local_datetime", "timezone", "lat_deg", "lon_deg", "sog_kn",
    "heading_deg", "active_engines", "fuel_l_per_h",
]
NOON_REPORT_HEADER = [
    "report_date", "local_time", "timezone", "lat_deg", "lon_deg",
    "distance_run_nm", "hours_run", "fuel_consumed_mt", "active_engines",
]


def _write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow(row)


def test_monitoring_csv_adapter_utc_timestamp_and_passthrough_units(tmp_path):
    path = tmp_path / "mon.csv"
    _write_csv(
        path,
        MONITORING_HEADER,
        [["2026-01-01T00:00:00Z", "41.0", "8.0", "10.0", "90", "2", "25.0", "1.2", "6.5", "180"]],
    )
    rows = MonitoringCsvAdapter().parse(path, vessel_id="v1", passage_id="p1")
    assert len(rows) == 1
    r = rows[0]
    assert r.t_epoch_s == datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    assert r.stw_kn == pytest.approx(10.0)
    assert r.fuel_kg_per_h == pytest.approx(25.0)  # already kg/h, no conversion
    assert r.source == "monitoring_csv"
    assert r.vessel_id == "v1" and r.passage_id == "p1"
    assert r.is_low_frequency is False


def test_monitoring_csv_adapter_optional_columns_blank_becomes_none(tmp_path):
    path = tmp_path / "mon.csv"
    _write_csv(
        path,
        MONITORING_HEADER,
        [["2026-01-01T00:00:00Z", "41.0", "8.0", "10.0", "90", "2", "25.0", "", "", ""]],
    )
    r = MonitoringCsvAdapter().parse(path, vessel_id="v1", passage_id="p1")[0]
    assert r.hs_m is None
    assert r.period_peak_s is None
    assert r.wave_from_deg is None


def test_elogbook_adapter_converts_local_time_and_liters_to_kg(tmp_path):
    path = tmp_path / "elog.csv"
    _write_csv(
        path,
        ELOGBOOK_HEADER,
        [["2026-01-01 12:00:00", "Europe/Rome", "41.0", "8.0", "9.5", "90", "1", "30.0"]],
    )
    r = ELogbookAdapter().parse(path, vessel_id="v1", passage_id="p1")[0]
    expected_epoch = datetime(2026, 1, 1, 12, 0, 0, tzinfo=ZoneInfo("Europe/Rome")).timestamp()
    assert r.t_epoch_s == pytest.approx(expected_epoch)
    assert r.stw_kn is None  # e-logbook: SOG only, never STW
    assert r.sog_kn == pytest.approx(9.5)
    assert r.fuel_kg_per_h == pytest.approx(30.0 * MARINE_DIESEL_KG_PER_L)
    assert r.source == "e_logbook"


def test_elogbook_adapter_different_timezones_give_different_utc_epochs(tmp_path):
    # the same local wall-clock time in two different timezones must not
    # collapse to the same UTC instant -- the whole point of the per-source
    # timezone audit.
    path_rome = tmp_path / "rome.csv"
    path_utc = tmp_path / "utc.csv"
    row = ["2026-06-01 12:00:00", None, "41.0", "8.0", "9.0", "90", "1", "20.0"]
    _write_csv(path_rome, ELOGBOOK_HEADER, [[*row[:1], "Europe/Rome", *row[2:]]])
    _write_csv(path_utc, ELOGBOOK_HEADER, [[*row[:1], "UTC", *row[2:]]])
    r_rome = ELogbookAdapter().parse(path_rome, vessel_id="v1", passage_id="p1")[0]
    r_utc = ELogbookAdapter().parse(path_utc, vessel_id="v1", passage_id="p1")[0]
    assert r_rome.t_epoch_s != r_utc.t_epoch_s


def test_noon_report_adapter_is_always_low_frequency_and_derives_sog_and_fuel_rate(tmp_path):
    path = tmp_path / "noon.csv"
    _write_csv(
        path,
        NOON_REPORT_HEADER,
        [["2026-01-01", "12:00:00", "UTC", "41.0", "8.0", "220.0", "24.0", "2.5", "2"]],
    )
    r = NoonReportAdapter().parse(path, vessel_id="v1", passage_id="p1")[0]
    assert r.is_low_frequency is True
    assert r.sog_kn == pytest.approx(220.0 / 24.0)
    assert r.fuel_kg_per_h == pytest.approx(2.5 * 1000.0 / 24.0)
    assert r.stw_kn is None
    assert r.heading_deg is None
    assert r.source == "noon_report"


def test_annotated_track_csv_adapter_carries_environment_no_performance(tmp_path):
    path = tmp_path / "track.csv"
    write_track_csv(
        path,
        [
            TrackPoint(
                t_epoch_s=0.0,
                lat_deg=41.0,
                lon_deg=8.0,
                hs_m=1.5,
                period_peak_s=6.0,
                period_mean_s=5.0,
                wave_from_deg=180.0,
                wind_u_ms=1.0,
                wind_v_ms=2.0,
            )
        ],
    )
    r = AnnotatedTrackCsvAdapter().parse(path, vessel_id="v1", passage_id="p1")[0]
    assert r.hs_m == pytest.approx(1.5)
    assert r.period_peak_s == pytest.approx(6.0)
    assert r.period_mean_s == pytest.approx(5.0)
    assert r.wind_u_ms == pytest.approx(1.0)
    assert r.source == "annotated_track"
    assert r.stw_kn is None
    assert r.sog_kn is None
    assert r.fuel_kg_per_h is None
