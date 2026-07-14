from fit.import_adapters import (
    AnnotatedTrackCsvAdapter,
    ELogbookAdapter,
    MonitoringCsvAdapter,
    NoonReportAdapter,
)
from fit.import_schema import (
    SOG_FALLBACK_NOISE_MULTIPLIER,
    SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION,
    CanonicalImportRow,
)

_IMPLEMENTED_ADAPTER_SOURCES = {"monitoring_csv", "e_logbook", "noon_report", "annotated_track"}


def test_canonical_import_row_defaults():
    row = CanonicalImportRow(
        t_epoch_s=0.0, lat_deg=41.0, lon_deg=8.0, vessel_id="v1", passage_id="p1", source="x"
    )
    assert row.stw_kn is None
    assert row.sog_kn is None
    assert row.is_low_frequency is False
    assert row.period_mean_s is None
    assert not hasattr(row, "fuel_noise_std_fraction")  # amendment 1: dropped


def test_every_implemented_adapter_source_has_a_noise_fraction_entry():
    for source in _IMPLEMENTED_ADAPTER_SOURCES:
        assert source in SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION


def test_noise_fractions_are_positive_and_ordered_by_expected_data_quality():
    # flowmeter-tier (monitoring_csv) should be the tightest band; noon
    # reports (coarsest, hand-estimated) the widest.
    f = SOURCE_DEFAULT_FUEL_NOISE_STD_FRACTION
    assert 0 < f["monitoring_csv"] < f["e_logbook"] < f["noon_report"]


def test_sog_fallback_multiplier_is_greater_than_one():
    assert SOG_FALLBACK_NOISE_MULTIPLIER > 1.0


def test_adapters_implement_the_import_adapter_protocol():
    adapter_classes = (
        MonitoringCsvAdapter,
        ELogbookAdapter,
        NoonReportAdapter,
        AnnotatedTrackCsvAdapter,
    )
    for adapter_cls in adapter_classes:
        adapter = adapter_cls()
        assert callable(adapter.parse)
