"""Pure normalisation-logic unit tests for ingest/fetch_era5_track.py --
no real network/credentials, matching ticket 0.5's own precedent.
"""

from datetime import UTC, datetime

import pytest

from ingest.fetch_era5_track import (
    CDS_DATASET,
    CDS_VARIABLES,
    _build_cds_request,
    _find_var,
    _time_coord_name,
)


def test_build_cds_request_covers_the_whole_span_and_bbox():
    t_min = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    t_max = datetime(2026, 1, 2, 12, tzinfo=UTC).timestamp()
    req = _build_cds_request((6.0, 40.0, 10.0, 44.0), t_min, t_max)

    assert req["area"] == [44.0, 6.0, 40.0, 10.0]  # North, West, South, East
    assert set(req["year"]) == {"2026"}
    assert set(req["month"]) == {"01"}
    assert set(req["day"]) == {"01", "02"}
    assert len(req["time"]) == 24
    assert set(req["variable"]) == set(CDS_VARIABLES.values())
    assert req["format"] == "netcdf"


def test_build_cds_request_spans_a_month_boundary():
    t_min = datetime(2026, 1, 31, tzinfo=UTC).timestamp()
    t_max = datetime(2026, 2, 1, tzinfo=UTC).timestamp()
    req = _build_cds_request((0.0, 0.0, 1.0, 1.0), t_min, t_max)
    assert set(req["month"]) == {"01", "02"}
    assert set(req["day"]) == {"31", "01"}


def test_cds_dataset_name_is_the_era5_single_levels_reanalysis_product():
    assert CDS_DATASET == "reanalysis-era5-single-levels"


class _FakeDataset:
    def __init__(self, data_vars, coords):
        self.data_vars = data_vars
        self.coords = coords

    def __getitem__(self, key):
        return self.data_vars[key]


def test_find_var_raises_clear_error_when_missing():
    ds = _FakeDataset(data_vars={"swh": object()}, coords={})
    with pytest.raises(KeyError, match="pp1d"):
        _find_var(ds, "pp1d")


def test_find_var_returns_the_variable_when_present():
    sentinel = object()
    ds = _FakeDataset(data_vars={"swh": sentinel}, coords={})
    assert _find_var(ds, "swh") is sentinel


def test_time_coord_name_prefers_time_over_valid_time():
    ds = _FakeDataset(data_vars={}, coords={"time": None, "valid_time": None})
    assert _time_coord_name(ds) == "time"


def test_time_coord_name_falls_back_to_valid_time():
    ds = _FakeDataset(data_vars={}, coords={"valid_time": None})
    assert _time_coord_name(ds) == "valid_time"


def test_time_coord_name_raises_when_neither_present():
    ds = _FakeDataset(data_vars={}, coords={})
    with pytest.raises(KeyError):
        _time_coord_name(ds)
