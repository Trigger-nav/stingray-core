"""ingest/fetch_all_packs.py (ticket R1) -- cron wrapper looping the
existing (unmodified) fetch_grib_nomads/fetch_grib_ecmwf CLIs over every
configured pack. subprocess.run is mocked throughout -- this tests the
orchestration (which args get built per pack), not the fetchers
themselves (already covered by their own test suites)."""

from __future__ import annotations

from unittest.mock import patch

import yaml

from core.regionpack import MED_PACK
from ingest.fetch_all_packs import fetch_one, load_packs


def test_load_packs_reads_manifest_and_resolves_each_pack(tmp_path):
    manifest = tmp_path / "region_packs.yaml"
    manifest.write_text(yaml.dump({"packs": ["data/region_packs/med.yaml"]}))
    packs = load_packs(str(manifest))
    assert len(packs) == 1
    assert packs[0].pack_id == "med"


def test_fetch_one_builds_the_right_bbox_and_paths():
    with patch("ingest.fetch_all_packs.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        rc = fetch_one("ingest.fetch_grib_nomads", MED_PACK)
        assert rc == 0
        (cmd,), _ = mock_run.call_args
        assert "ingest.fetch_grib_nomads" in cmd
        bbox_idx = cmd.index("--bbox")
        assert cmd[bbox_idx + 1 : bbox_idx + 5] == [str(v) for v in MED_PACK.bbox]
        out_idx = cmd.index("--out")
        assert cmd[out_idx + 1] == MED_PACK.weather_npz_path


def test_fetch_one_returns_nonzero_on_failure_without_raising():
    with patch("ingest.fetch_all_packs.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        rc = fetch_one("ingest.fetch_grib_nomads", MED_PACK)
        assert rc == 1
