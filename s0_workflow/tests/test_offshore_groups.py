"""Offshore wind duplicates (CHANGES §120; s0_workflow/offshore_groups.py, scripts/split_offshore_groups.py,
production.apply_offshore_filter_fix / write_offshore_check). The before state is read from the committed s4x1 fedpol
inputs; the split runs on a hand-built resource-group folder (fixture, not results)."""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import offshore_groups as og, production as s0prod  # noqa: E402

FEDPOL = REPO / "switch/in/foresight/s4x1_fedpol_current"


def test_v31_offers_each_zone_resource_three_times():
    if not (FEDPOL / "gen_info.csv").exists():
        pytest.skip("committed fedpol inputs not present")
    gi = pd.read_csv(FEDPOL / "gen_info.csv", na_values=".")
    by = og.mw_by_zone(gi)
    p = by.pivot_table(index="gen_load_zone", columns="gen_tech", values="mw", aggfunc="sum")
    assert list(p.columns) == ["OffShoreWind_Class12_Moderate_floating_0", "OffShoreWind_Class3_Moderate_fixed_0",
                               "OffShoreWind_Class3_Moderate_fixed_1"]
    assert (p.nunique(axis=1) == 1).all()                         # the same limit three times in every zone
    assert len(p) == 36 and p.iloc[:, 0].sum() == pytest.approx(2985509.0, abs=1)
    assert p.loc["p1"].iloc[0] == pytest.approx(68339.0, abs=1)


def _groups(tmp_path):
    g = tmp_path / "resource_groups"
    (g / "cpas").mkdir(parents=True)
    (g / "cpas" / "osw_group.json").write_text(json.dumps(
        {"technology": "offshorewind", "metadata": "offshorewind_lcoe_ReEDS.csv", "profiles": "osw_profiles.parquet"}))
    meta = pd.DataFrame({"CPA_ID": range(8), "ipm_region": ["p1"] * 5 + ["p8"] * 3,
                         "mw": [100.0, 200, 300, 400, 50, 10, 20, 30], "lcoe": [80, 90, 120, 130, 70, 60, 100, 110],
                         "Depth_m": [30, 45, 200, 800, 55, 20, 300, 25], "pref": [1, 0, 0, 0, 1, 0, 0, 1]})
    meta.to_csv(g / "cpas" / "offshorewind_lcoe_ReEDS.csv", index=False)
    (g / "cpas" / "solar_group.json").write_text(json.dumps({"technology": "utilitypv", "metadata": "solar.csv"}))
    (g / "cpas" / "solar.csv").write_text("CPA_ID,mw\n1,5\n")
    return g, meta


def test_split_folder_by_turbine_and_pref_site(tmp_path):
    g, meta = _groups(tmp_path)
    out = tmp_path / "oswfix"
    man = og.split_folder(g, out, "cpas/osw_group.json", depth_col="depth_m", pref_col="PREF", log=lambda m: None)
    kinds = {(x["turbine_type"], x["pref_site"]): x for x in man["groups"]}
    assert set(kinds) == {("fixed", 1), ("fixed", 0), ("floating", 0)}
    assert kinds[("fixed", 1)]["cpas"] == 3 and kinds[("floating", 0)]["mw_all"] == 300 + 400 + 20
    for x in man["groups"]:
        j = json.loads((out / x["json"]).read_text())
        assert j["technology"] == "offshorewind" and j["profiles"] == "osw_profiles.parquet"   # profiles shared
        assert j["turbine_type"] == x["turbine_type"] and j["pref_site"] == x["pref_site"]
        assert len(pd.read_csv(out / "cpas" / j["metadata"])) == x["cpas"]
    assert not (out / "cpas" / "osw_group.json").exists()                     # the all-CPA group is gone
    assert (out / "cpas" / "solar_group.json").read_text() == (g / "cpas" / "solar_group.json").read_text()
    tr = pd.read_csv(out / "offshore_true_resource_by_zone.csv").groupby("zone").mw.sum()
    assert tr.to_dict() == {"p1": 100 + 200 + 300 + 50, "p8": 10 + 20 + 30}       # LCOE <= 125 (CPA 3 at 130 out)
    assert man["true_resource_mw"] == 710 and og.check_folder(out)["method"] == "split_groups"
    assert (g / "cpas" / "osw_group.json").exists()                          # the source is untouched


def test_build_flag_and_case_check(tmp_path):
    g, _ = _groups(tmp_path)
    out = tmp_path / "oswfix"
    og.split_folder(g, out, "cpas/osw_group.json", depth_col="depth_m", pref_col="pref", log=lambda m: None)
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0["offshore_filter_fix"]["mode"] == "off"                       # v3.1 until approved
    s = {"RESOURCE_GROUPS": str(g)}
    s0prod.apply_offshore_filter_fix(s, {"offshore_filter_fix": {"mode": "off"}})
    assert s["RESOURCE_GROUPS"] == str(g)
    with pytest.raises(ValueError, match="is off but RESOURCE_GROUPS"):
        s0prod.apply_offshore_filter_fix({"RESOURCE_GROUPS": str(out)}, {"offshore_filter_fix": {"mode": "off"}})
    on = {"offshore_filter_fix": {"mode": "split_groups", "resource_groups_dir": str(out)}}
    s0prod.apply_offshore_filter_fix(s, on)
    assert s["RESOURCE_GROUPS"] == str(out.resolve())
    # a case written from the split groups: limits per zone equal the true resource
    case = tmp_path / "case"
    case.mkdir()
    gi = pd.DataFrame({"GENERATION_PROJECT": ["a", "b", "c", "d", "e"],
                       "gen_tech": ["OffShoreWind_Class3_Moderate_fixed_1", "OffShoreWind_Class3_Moderate_fixed_0",
                                    "OffShoreWind_Class12_Moderate_floating_0", "OffShoreWind_Class3_Moderate_fixed_1",
                                    "OffShoreWind_Class3_Moderate_fixed_0"],
                       "gen_load_zone": ["p1", "p1", "p1", "p8", "p8"], "gen_capacity_limit_mw": [150, 200, 300, 30, 30]})
    gi.to_csv(case / "gen_info.csv", index=False)
    logs = []
    s0prod.write_offshore_check(case, on, logs.append)
    t = pd.read_csv(case / og.BY_ZONE).set_index("zone")
    assert t.loc["p1", "ratio"] == pytest.approx(1.0) and t.ok.all() and "equal the true resource" in logs[0]
    gi.loc[gi.gen_load_zone == "p8", "gen_capacity_limit_mw"] = 60.0         # the v3.1 duplication: x2 in p8
    gi.to_csv(case / "gen_info.csv", index=False)
    with pytest.raises(ValueError, match="p8 x2.00"):
        s0prod.write_offshore_check(case, on, logs.append)
    s0prod.write_offshore_check(case, {}, logs.append)                       # off: report only
    assert "offshore (v3.1)" in logs[-1]


def test_cli(tmp_path):
    from s0_workflow.scripts import split_offshore_groups as cli
    g, _ = _groups(tmp_path)
    assert cli.main(["--groups-dir", str(g), "--out-dir", str(tmp_path / "o"), "--json", "cpas/osw_group.json",
                     "--depth-col", "Depth_m", "--pref-col", "pref"]) == 0
    with pytest.raises(ValueError, match="no pref_site column"):
        cli.main(["--groups-dir", str(g), "--out-dir", str(tmp_path / "o2"), "--json", "cpas/osw_group.json",
                  "--depth-col", "Depth_m", "--pref-col", "preferred"])
