"""§138: the v3.2 build at c0759bf failed on a cold cluster cache with the split on: production set
s["RESOURCE_GROUPS"] = str(...), and PowerGenome calls .as_posix() on it when it writes a new cache entry
(nrelatb.py). PowerGenome's own settings carry these as Paths. This runs apply_settings on a v3.2 case with a real
split folder, a real repaired PV folder and an empty (cold) cache, and checks that every path setting is still a Path
that answers .as_posix(), as PowerGenome's cache writer needs. A flags-off case leaves the settings as they were."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s0_workflow import build_guard as bg, offshore_groups as og, production as s0prod  # noqa: E402
from test_s0_production import _case_settings  # noqa: E402

pytest.importorskip("pyarrow")
PATH_KEYS = ("RESOURCE_GROUPS", "RESOURCE_GROUP_PROFILES", "input_folder")


def _fixtures(tmp_path):
    import test_offshore_groups as to
    import test_pv_profile_repair as tp
    g, _, mapping = to._rg_layout(tmp_path / "shared")
    split = tmp_path / "pg_data" / "resource_groups_v32"
    og.split_by_type_files(g, split, mapping, log=lambda m: None)
    src, sites, _ = tp._profiles(tmp_path, dead_capacity=0.0)
    pv = tmp_path / "profiles_pvfix"
    tp._repair(src, pv, sites)
    return g, split, src, pv


def _pg_cache_writer(s):
    """What PowerGenome does with these settings when it writes a new cluster-cache entry: .as_posix() on them."""
    return [s[k].as_posix() for k in PATH_KEYS]


def test_cold_cache_split_on_keeps_paths(tmp_path, monkeypatch):
    g, split, src, pv = _fixtures(tmp_path)
    cache = tmp_path / "extra_inputs" / "cluster_assignments"                 # cold: does not exist yet
    monkeypatch.setattr(bg, "cache_dir", lambda s: cache)
    s = _case_settings(2028)
    s["s0_production"]["offshore_filter_fix"] = {"mode": "split_groups", "resource_groups_dir": str(split)}
    s["s0_production"]["pv_profile_repair"] = {"mode": "neighbour_fill", "profiles_dir": str(pv), "verify": "manifest"}
    s.update({"RESOURCE_GROUPS": g, "RESOURCE_GROUP_PROFILES": src, "input_folder": tmp_path / "extra_inputs",
              "renewables_clusters": [{"region": "p1", "technology": "offshorewind", "turbine_type": "floating",
                                       "pref_site": 0}]})
    s0prod._PV_REPAIR_CHECKED.clear()
    s0prod.apply_settings({"c": {2028: s}})
    for k in PATH_KEYS:
        assert isinstance(s[k], Path), (k, type(s[k]))                        # the bug: a str here
    assert s["RESOURCE_GROUPS"] == split.resolve() and s["RESOURCE_GROUP_PROFILES"] == pv.resolve()
    assert _pg_cache_writer(s)                                                # no AttributeError
    assert (cache / bg.MARKER).exists()                                       # the cold cache got its marker
    assert any(e.get("pref_site") == 1 for e in s["renewables_clusters"])     # the fourth entry


def test_flags_off_leaves_path_settings_as_they_were(tmp_path):
    g = tmp_path / "rg"
    g.mkdir()
    s = _case_settings(2028)
    s["s0_production"]["offshore_filter_fix"] = {"mode": "off"}
    s["s0_production"]["pv_profile_repair"] = {"mode": "off"}
    before = {"RESOURCE_GROUPS": g, "RESOURCE_GROUP_PROFILES": tmp_path / "profiles", "input_folder": tmp_path}
    s.update(before)
    s0prod.apply_settings({"c": {2028: s}})
    for k, v in before.items():                    # same value and type (apply_settings deep-copies when it merges)
        assert s[k] == v and type(s[k]) is type(v), k
