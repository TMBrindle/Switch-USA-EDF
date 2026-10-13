"""§134: build guards. PowerGenome provenance (V32-04) and the cluster-cache marker. Fixture folders only."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import build_guard as bg  # noqa: E402

PIN = "c4c433cfefd0ae11f5b3f47ae05e3b9d7c90a691"


def _info(commit, present=True):
    return lambda fn=None: {"package": "x", "root": "/x", "commit": commit, "dirty": False, "is_repo_submodule": False,
                            "cost_multiplier_fn": fn, "cost_multiplier_file_present": present}


def test_powergenome_commit_check(monkeypatch, tmp_path):
    s = {"cost_multiplier_fn": "AEO_2026_regional_cost_corrections.csv"}
    monkeypatch.setattr(bg, "powergenome_info", _info("5ecb45427bba7ba3adba468ffa5e0ce3d35f56de"))
    bg._PG_CHECKED.clear()
    assert bg.check_powergenome(s, {})["commit"].startswith("5ecb454")          # no expectation: record only
    bg._PG_CHECKED.clear()
    with pytest.raises(ValueError, match="PYTHONPATH=<worktree>/PowerGenome"):
        bg.check_powergenome(s, {"powergenome_commit": PIN})
    monkeypatch.setattr(bg, "powergenome_info", _info(PIN, present=False))
    bg._PG_CHECKED.clear()
    with pytest.raises(ValueError, match="no data/cost_multipliers"):
        bg.check_powergenome(s, {"powergenome_commit": PIN})
    monkeypatch.setattr(bg, "powergenome_info", _info(PIN))
    bg._PG_CHECKED.clear()
    lines = []
    bg.write_provenance(tmp_path, s, {"powergenome_commit": PIN[:7]}, lines.append)
    assert json.loads((tmp_path / bg.PROVENANCE).read_text())["commit"] == PIN and PIN in lines[0]


def test_v32_settings_ask_for_the_pin():
    import yaml
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0["powergenome_commit"] == PIN


def _s0(pv=True, off=True, d=None):
    return {"pv_profile_repair": {"mode": "neighbour_fill" if pv else "off", "profiles_dir": "pg_data/profiles_pvfix",
                                  "output_sha256": "ab" * 32},
            "offshore_filter_fix": {"mode": "split_groups" if off else "off", "resource_groups_dir": str(d or "x")}}


def test_cluster_cache_marker(monkeypatch, tmp_path):
    cache = tmp_path / "cluster_assignments"
    monkeypatch.setattr(bg, "cache_dir", lambda s: cache)
    split = tmp_path / "split"
    split.mkdir()
    (split / "offshore_split_manifest.json").write_text('{"groups": []}')
    on = _s0(d=split)
    bg.check_cluster_cache({}, on)                                  # new folder: marker written
    assert json.loads((cache / bg.MARKER).read_text())["pv_profile_repair"]["output_sha256"] == "ab" * 32
    (cache / "38aa2729417c51b9").mkdir()                            # the build's own entries
    bg.check_cluster_cache({}, on)                                  # same data: fine
    with pytest.raises(ValueError, match="made by a v3.2 build"):   # flags off on a v3.2 cache
        bg.check_cluster_cache({}, _s0(pv=False, off=False))
    (split / "offshore_split_manifest.json").write_text('{"groups": [1]}')
    with pytest.raises(ValueError, match="made for other data"):    # the split changed
        bg.check_cluster_cache({}, on)
    old = tmp_path / "old" / "cluster_assignments"
    (old / "7f3e").mkdir(parents=True)                               # a v3.1 cache: entries, no marker
    monkeypatch.setattr(bg, "cache_dir", lambda s: old)
    with pytest.raises(ValueError, match="1 entries made without the v3.2 data"):
        bg.check_cluster_cache({}, on)
    bg.check_cluster_cache({}, _s0(pv=False, off=False))            # flags off on a v3.1 cache: fine
