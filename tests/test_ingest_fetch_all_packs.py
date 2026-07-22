"""ingest/fetch_all_packs.py (ticket R1) -- cron wrapper looping the
existing (unmodified) fetch_grib_nomads/fetch_grib_ecmwf CLIs over every
configured pack. subprocess.run is mocked throughout -- this tests the
orchestration (which args get built per pack), not the fetchers
themselves (already covered by their own test suites).

Ticket C1 additions: run_currents_step's failure-isolation amendment --
a currents-step failure (fetch or merge subprocess, or an unexpected
raised exception) must never block that pack's wind/wave fetch/hot-swap,
nor affect a second, independent pack in the same run.
"""

from __future__ import annotations

import dataclasses
import logging
from unittest.mock import patch

import yaml

from core.regionpack import MED_PACK
from ingest.fetch_all_packs import fetch_one, load_packs, main, run_currents_step


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


# --- Ticket C1: run_currents_step's failure isolation (required amendment) ---

CURRENTS_PACK = dataclasses.replace(
    MED_PACK,
    pack_id="currents-test",
    currents_dataset_id="cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i",
)


def test_run_currents_step_is_a_no_op_when_pack_has_no_currents_dataset_id():
    with patch("ingest.fetch_all_packs.subprocess.run") as mock_run:
        run_currents_step(MED_PACK)  # MED_PACK.currents_dataset_id is None
        mock_run.assert_not_called()


def test_run_currents_step_fetch_failure_is_logged_and_does_not_raise(caplog):
    with patch("ingest.fetch_all_packs.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        with caplog.at_level(logging.WARNING):
            run_currents_step(CURRENTS_PACK)  # must not raise
        assert any(
            "currents-test" in r.message and "currents fetch failed" in r.message
            for r in caplog.records
        )
        # only one subprocess call -- the merge step must not run after a
        # failed fetch (there'd be no valid currents npz to merge from).
        assert mock_run.call_count == 1


def test_run_currents_step_merge_failure_is_logged_and_does_not_raise(caplog):
    with patch("ingest.fetch_all_packs.subprocess.run") as mock_run:
        # fetch succeeds (rc=0), merge fails (rc=1) -- two calls, alternating.
        mock_run.side_effect = [
            type("R", (), {"returncode": 0})(),
            type("R", (), {"returncode": 1})(),
        ]
        with caplog.at_level(logging.WARNING):
            run_currents_step(CURRENTS_PACK)  # must not raise
        assert any(
            "currents-test" in r.message and "currents merge failed" in r.message
            for r in caplog.records
        )
        assert mock_run.call_count == 2


def test_run_currents_step_unexpected_exception_is_logged_and_does_not_raise(caplog):
    with patch("ingest.fetch_all_packs.subprocess.run", side_effect=RuntimeError("boom")):
        with caplog.at_level(logging.WARNING):
            run_currents_step(CURRENTS_PACK)  # must not raise
        assert any(
            "currents-test" in r.message and "unexpected error" in r.message
            for r in caplog.records
        )


def test_main_currents_failure_does_not_block_wind_wave_or_a_second_pack(tmp_path, caplog):
    """The amendment's own explicit test requirement: a mocked
    currents-fetch failure still completes the wind/wave path for that
    pack, and a second, independent pack in the same run is unaffected."""
    pack_a_yaml = tmp_path / "pack_a.yaml"
    pack_a = dataclasses.replace(
        MED_PACK,
        pack_id="a",
        currents_dataset_id="cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i",
    )
    pack_b_yaml = tmp_path / "pack_b.yaml"
    pack_b = dataclasses.replace(MED_PACK, pack_id="b", currents_dataset_id=None)

    def _write_pack_yaml(path, pack):
        path.write_text(
            yaml.dump(
                {
                    "pack_id": pack.pack_id,
                    "name": pack.name,
                    "bbox": list(pack.bbox),
                    "ref_lat_deg": pack.ref_lat_deg,
                    "coastline_path": pack.coastline_path,
                    "bathymetry_path": pack.bathymetry_path,
                    "nogo_path": pack.nogo_path,
                    "tss_path": pack.tss_path,
                    "weather_npz_path": pack.weather_npz_path,
                    "currents_dataset_id": pack.currents_dataset_id,
                }
            )
        )

    _write_pack_yaml(pack_a_yaml, pack_a)
    _write_pack_yaml(pack_b_yaml, pack_b)
    manifest = tmp_path / "region_packs.yaml"
    manifest.write_text(yaml.dump({"packs": [str(pack_a_yaml), str(pack_b_yaml)]}))

    import sys

    call_log = []

    def fake_run(cmd, *a, **kw):
        call_log.append(cmd)
        result = type("R", (), {})()
        # wind/wave fetches (fetch_grib_nomads/ecmwf) always succeed; the
        # currents fetch (fetch_currents_cmems) always fails.
        result.returncode = 1 if "ingest.fetch_currents_cmems" in cmd else 0
        return result

    sys.argv = ["fetch_all_packs", "--packs-manifest", str(manifest)]
    with patch("ingest.fetch_all_packs.subprocess.run", side_effect=fake_run):
        with caplog.at_level(logging.WARNING):
            main()  # must not raise, must not sys.exit(1)

    # both packs' wind/wave fetches ran (2 modules x 2 packs = 4 calls),
    # plus one failed currents-fetch attempt for pack "a" only.
    wind_wave_calls = [c for c in call_log if "ingest.fetch_currents_cmems" not in c]
    assert len(wind_wave_calls) == 4
    currents_calls = [c for c in call_log if "ingest.fetch_currents_cmems" in c]
    assert len(currents_calls) == 1
    assert any("currents fetch failed" in r.message for r in caplog.records)
