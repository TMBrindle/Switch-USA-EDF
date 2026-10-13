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
    (g / "cpas" / "osw_profiles.parquet").write_bytes(b"profiles stand-in")   # §134: references must resolve
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
    assert s0["offshore_filter_fix"]["mode"] == "split_groups"              # v3.2 ON (§129)
    assert s0["offshore_filter_fix"]["resource_groups_dir"] == "pg_data/resource_groups/ReEDS-cpas-patched-offshore-split"
    assert s0prod.offshore_fix_settings({})["mode"] == "off"               # the code default: v3.1
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


# ---- §134: the ReEDS-cpas-patched layout (four JSONs sharing one metadata file; per-type membership files) ----------

def _rg_layout(tmp_path, overlap=False, missing=False):
    """Hand-built group folder: four offshore JSONs pointing at one patched metadata file (zones p1, p2; CPA 6 moved from
    p1 to p2 by the patch), four per-type membership files (unpatched zones), and an unrelated solar group."""
    g = tmp_path / "rg"
    g.mkdir(parents=True)
    meta = pd.DataFrame({"CPA_ID": range(1, 9), "ipm_region": ["p1", "p1", "p2", "p2", "p1", "p2", "p2", "p1"],
                         "mw": [100.0, 200, 300, 400, 500, 600, 700, 800],
                         "lcoe": [80.0, 90, 130, 70, 60, 50, 140, 100]})
    meta.to_csv(g / "offshorewind_lcoe_ReEDS.csv", index=False)
    types = {("fixed", "true"): [1, 2], ("fixed", "false"): [3, 4], ("floating", "false"): [5, 6, 7],
             ("floating", "true"): [8]}
    if overlap:
        types[("floating", "true")] = [8, 1]
    if missing:
        types[("floating", "true")] = []
    for (tt, ps), ids in types.items():
        t = meta[meta.CPA_ID.isin(ids)].copy()
        t.loc[t.CPA_ID == 6, "ipm_region"] = "p1"                  # unpatched zone in the per-type file
        t.to_csv(g / f"offshorewind_lcoe_ReEDS_{tt}_{ps}.csv", index=False)
        (g / f"offshorewind_{tt}_{ps}.json").write_text(json.dumps({
            "technology": "offshorewind", "turbine_type": tt, "pref_site": ps == "true",
            "metadata": "offshorewind_lcoe_ReEDS.csv", "profiles": "offshore_profiles.parquet",
            "site_map": "offshore_site_map.csv"}))
    (g / "offshore_profiles.parquet").write_bytes(b"profiles stand-in")
    (g / "offshore_site_map.csv").write_text("CPA_ID,Site\n1,1\n")
    (g / "solar.json").write_text(json.dumps({"technology": "utilitypv", "metadata": "solar_meta.csv"}))
    (g / "solar_meta.csv").write_text("CPA_ID\n1\n")
    mapping = {f"offshorewind_{tt}_{ps}.json": f"offshorewind_lcoe_ReEDS_{tt}_{ps}.csv" for tt, ps in types}
    return g, meta, mapping


def test_type_files_split_keeps_patched_rows_and_resolves(tmp_path):
    g, meta, mapping = _rg_layout(tmp_path)
    out = tmp_path / "split"
    man = og.split_by_type_files(g, out, mapping, log=lambda m: None)
    assert man["method"] == "type_files" and man["partition"]["exact"] and man["broken_references"] == []
    for k in mapping:
        j = json.loads((out / k).read_text())
        part = pd.read_csv(out / j["metadata"])
        ids = set(pd.read_csv(g / mapping[k]).CPA_ID)
        assert set(part.CPA_ID) == ids and j["turbine_type"] in ("fixed", "floating")
    flt = pd.read_csv(out / "offshorewind_lcoe_ReEDS__offshorewind_floating_false.csv").set_index("CPA_ID")
    assert flt.at[6, "ipm_region"] == "p2"                                  # the patched zone, not the type file's
    grp = {g_["json"]: g_ for g_ in man["groups"]}
    assert grp["offshorewind_floating_false.json"]["zone_differs_from_shared"] == 1
    tr = pd.read_csv(out / "offshore_true_resource_by_zone.csv")
    assert tr.mw.sum() == pytest.approx(meta[meta.lcoe <= 125].mw.sum())      # the types add up to the whole
    assert og.check_references(out) == [] and og.check_folder(out)["method"] == "type_files"
    assert json.loads((g / "offshorewind_fixed_true.json").read_text())["metadata"] == "offshorewind_lcoe_ReEDS.csv"


def test_type_files_split_refuses_bad_partitions_and_unmapped_jsons(tmp_path):
    g, _, mapping = _rg_layout(tmp_path, overlap=True)
    with pytest.raises(ValueError, match="overlap"):
        og.split_by_type_files(g, tmp_path / "o1", mapping, log=lambda m: None)
    g2, _, mapping2 = _rg_layout(tmp_path / "b", missing=True)
    with pytest.raises(ValueError, match="in no membership file"):
        og.split_by_type_files(g2, tmp_path / "o2", mapping2, log=lambda m: None)
    m3 = dict(list(mapping2.items())[:3])
    with pytest.raises(ValueError, match="map every one"):
        og.split_by_type_files(g2, tmp_path / "o3", m3, log=lambda m: None)


def test_broken_reference_fails_the_folder_check(tmp_path):
    """The older one-JSON split on this layout: the other three JSONs keep pointing at the shared file, which the split
    leaves out of the copy. The reference check catches it (the manifest check alone did not)."""
    g, _, mapping = _rg_layout(tmp_path)
    out = tmp_path / "split"
    og.split_by_type_files(g, out, mapping, log=lambda m: None)
    (out / "offshorewind_lcoe_ReEDS__offshorewind_fixed_true.csv").unlink()
    bad = og.check_references(out)
    assert bad == [{"json": "offshorewind_fixed_true.json",
                    "missing": "offshorewind_lcoe_ReEDS__offshorewind_fixed_true.csv"}]
    with pytest.raises(ValueError, match="reference missing files"):
        og.check_folder(out)


def test_case_check_uses_the_requested_groups(tmp_path):
    """The case offers 3 of the 4 groups (fixed/1, fixed/0, floating/0): the check compares with those, and a group
    offered three times still fails."""
    tr = pd.DataFrame({"zone": ["p1", "p1", "p1", "p1"], "turbine_type": ["fixed", "fixed", "floating", "floating"],
                       "pref_site": [1, 0, 0, 1], "mw": [10.0, 20.0, 30.0, 40.0]})
    case = tmp_path / "c"
    case.mkdir()
    pd.DataFrame({"gen_load_zone": ["p1"] * 3, "gen_capacity_limit_mw": [10.0, 20.0, 30.0],
                  "gen_tech": ["OffShoreWind_Class3_Moderate_fixed_1", "OffShoreWind_Class3_Moderate_fixed_0",
                               "OffShoreWind_Class12_Moderate_floating_0"]}).to_csv(case / "gen_info.csv", index=False)
    t = og.check_case(case, tr)
    assert t.ok.all() and t.true_mw.iat[0] == 60.0
    pd.DataFrame({"gen_load_zone": ["p1"] * 3, "gen_capacity_limit_mw": [100.0] * 3,
                  "gen_tech": ["OffShoreWind_Class3_Moderate_fixed_1", "OffShoreWind_Class3_Moderate_fixed_0",
                               "OffShoreWind_Class12_Moderate_floating_0"]}).to_csv(case / "gen_info.csv", index=False)
    assert not og.check_case(case, tr).ok.any()


def test_cli_survey_and_map(tmp_path, capsys):
    from s0_workflow.scripts import split_offshore_groups as cli
    g, _, mapping = _rg_layout(tmp_path)
    assert cli.main(["--survey", "--groups-dir", str(g)]) == 0
    out = capsys.readouterr().out
    assert "offshorewind_floating_true.json" in out and "solar.json" not in out and "1 rows" in out
    args = ["--groups-dir", str(g), "--out-dir", str(tmp_path / "s")]
    for k, v in mapping.items():
        args += ["--map", f"{k}={v}"]
    assert cli.main(args) == 0 and og.check_references(tmp_path / "s") == []
