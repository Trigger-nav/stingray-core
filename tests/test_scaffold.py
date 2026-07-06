import importlib


def test_core_packages_import():
    for name in ["core", "core.twin", "core.optimiser", "core.weather", "ingest"]:
        importlib.import_module(name)
