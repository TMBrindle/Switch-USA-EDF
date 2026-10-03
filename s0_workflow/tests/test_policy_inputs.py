"""State policy buyout output (item 3) and the pinned ReEDS state-policy inputs (item 4)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
PIN = REPO / "pg/extra_inputs/reeds_state_policies"


def rps_module():
    spec = importlib.util.spec_from_file_location("rps_regional", REPO / "switch/study_modules/rps_regional.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_buyouts_by_state_year():
    rows = pd.DataFrame({"RPS_PROGRAM": ["ESR_NY_rps", "ESR_NY_ces", "ESR_MD_rps_solar", "OTHER"],
                         "PERIOD": [2030, 2030, 2035, 2030], "rps_acp_per_mwh": [100.0] * 4,
                         "target_mwh_per_yr": [10.0, 20.0, 5.0, 1.0], "shortfall_mwh_per_yr": [1.0, 2.0, 0.5, 0.0],
                         "shortfall_cost_per_yr": [100.0, 200.0, 50.0, 0.0]})
    out = rps_module().buyouts_by_state_year(rows, {2030: (2029, 2030), 2035: (2031, 2035)})
    ny = out[out.state == "NY"]
    assert list(ny.year) == [2029, 2030] and (ny.buyout_mwh_per_yr == 3.0).all() and (ny.buyout_cost_per_yr == 300.0).all()
    assert (ny.programs == 2).all()
    md = out[out.state == "MD"]
    assert list(md.year) == list(range(2031, 2036)) and (md.buyout_cost_per_yr == 50.0).all()
    assert set(out.state) == {"NY", "MD", "OTHER"}
    assert list(out.columns) == ["state", "year", "PERIOD", "period_start", "period_end", "programs",
                                 "target_mwh_per_yr", "buyout_mwh_per_yr", "buyout_cost_per_yr"]
    assert rps_module().buyouts_by_state_year(rows.iloc[0:0], {}).empty


def test_reeds_inputs_pinned():
    rel = yaml.safe_load(open(PIN / "REEDS_RELEASE.yml"))
    assert rel["release"] == "2026.09.21" and rel["commit"] == "8a1572331da89b15ad3c0f74db448911f715266c"
    assert rel["repository"] == "https://github.com/ReEDS-Model/ReEDS"
    for f in rel["files"]:
        p = PIN / f
        assert p.exists() and p.stat().st_size > 0, f
        assert "git-lfs" not in p.read_text()[:200], f
    src = (REPO / "make_emission_policies.py").read_text()
    assert "refs/heads/main" not in src and "reeds_input(" in src
    # every ReEDS file the script reads goes through reeds_input() and is pinned here
    used = set(__import__("re").findall(r'reeds_input\(f?"([^"]+)"\)', src))
    used = {u.replace("{prog}", p) for u in used for p in ("rps", "ces")} if any("{prog}" in u for u in used) else used
    assert used <= set(rel["files"]) | {"state_policies/rps_fraction.csv", "state_policies/ces_fraction.csv"}


def test_reeds_policy_diff_report(tmp_path):
    out = tmp_path / "diff.csv"
    r = subprocess.run([sys.executable, str(REPO / "s0_workflow/scripts/compare_reeds_state_policies.py"),
                        "--out", str(out)], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr
    d = pd.read_csv(out, comment="#")
    committed = pd.read_csv(REPO / "s0_workflow/data/reeds_state_policy_diff_2026.09.21.csv", comment="#")
    pd.testing.assert_frame_equal(d, committed)                                   # the committed report is current
    assert not d.program.isin(["ESR_NY_rps", "ESR_NY_ces"]).any()                 # NY targets unchanged
    nc = d[(d.program == "ESR_NC_ces") & (d.year == 2035)].iloc[0]
    assert nc.target_current == pytest.approx(0.5738, abs=1e-4) and nc.target_pinned == pytest.approx(0.3915, abs=1e-4)
    assert set(d[d.status == "dropped in release"].program.str.replace("UREC_Limit_", "")) == {"ESR_AZ_rps"}


def builder():
    spec = importlib.util.spec_from_file_location(
        "build_reeds_state_policies", REPO / "s0_workflow/scripts/build_reeds_state_policies.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_pinned_policy_files_are_current():
    """The committed S0 policy files are what the builder writes from the pinned release."""
    r = subprocess.run([sys.executable, str(REPO / "s0_workflow/scripts/build_reeds_state_policies.py"), "--check"],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr


def test_pinned_policy_files_content():
    b = builder()
    doc = yaml.safe_load(open(PIN / "s0_state_policies_2026.09.21.yml"))
    assert doc["release"] == "2026.09.21" and doc["commit"].startswith("8a1572331da8")
    assert doc["emission_policies_fn"] == "rggi_carbon/emission_policies_reeds_2026.09.21.csv"
    new = pd.read_csv(REPO / "pg/extra_inputs" / doc["emission_policies_fn"])
    cur = pd.read_csv(REPO / "pg/extra_inputs/rggi_carbon/emission_policies_current.csv")
    assert list(new.columns) == list(cur.columns)
    # region -> state from the shapefile agrees with hierarchy.csv
    st = b.region_states().set_index("region")["st"]
    h = pd.read_csv(REPO / "hierarchy.csv").set_index("ba")["st"].str.upper()
    assert (st == h.reindex(st.index)).all()
    # targets: the compare script's pinned values, for states in the model
    spec = importlib.util.spec_from_file_location("cmp", REPO / "s0_workflow/scripts/compare_reeds_state_policies.py")
    cmp_ = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cmp_)
    pin = cmp_.pinned_targets()
    pin = pin[pin.st.isin(set(st)) & (pin.target > 0)]
    long = new.assign(st=new.region.map(st)).copy().melt(id_vars=["year", "region", "st"],
                                                    value_vars=[c for c in new.columns if "ESR_" in c])
    long = long[long.variable.str.replace("UREC_Limit_", "").str.split("_").str[1] == long.st]
    got = long[long.value > 0].groupby(["year", "variable"]).value.agg(["min", "max"])
    assert (got["min"] == got["max"]).all()
    exp = pin.set_index(["year", "program"]).target.sort_index()
    pd.testing.assert_series_equal(got["max"].rename_axis(["year", "program"]).sort_index(), exp, check_names=False)
    nc = new[(new.year == 2035) & (new.region.map(st) == "NC")].ESR_NC_ces
    assert nc.iloc[0] == pytest.approx(0.3915, abs=1e-4)
    ny = ["ESR_NY_rps", "ESR_NY_ces"]
    k = ["year", "region"]
    m = cur[k + ny].merge(new[k + ny], on=k, suffixes=("_c", "_n"))
    # NY unchanged (the current file holds NY targets rounded to 6 digits in p127 and p128)
    for c in ny:
        assert m[c + "_n"].to_numpy() == pytest.approx(m[c + "_c"].to_numpy(), abs=1e-6)
    # carbon columns as in the current file (RGGI states and cap unchanged)
    co2 = [c for c in cur.columns if c.startswith("CO_2_")]
    m = cur[k + co2].merge(new[k + co2], on=k, suffixes=("_c", "_n"))
    assert len(m) == len(new)
    for c in co2:
        assert m[c + "_n"].to_numpy(float) == pytest.approx(m[c + "_c"].to_numpy(float), rel=1e-12), c
    # ESR eligibility of the release equals the current regional_resource_tags.yml (no change in this
    # release: only targets differ), and the tag list equals resource_tags.yml's ESR_ entries
    rrt = yaml.safe_load(open(REPO / "pg/settings/regional_resource_tags.yml"))["regional_tag_values"]
    cur_el = {r: {p: v for p, v in (d or {}).items() if p.startswith("ESR_")} for r, d in rrt.items()}
    assert {r: d for r, d in cur_el.items() if d} == doc["regional_tag_values"]
    tags = yaml.safe_load(open(REPO / "pg/settings/resource_tags.yml"))["model_tag_names"]
    assert sorted(t for t in tags if t.startswith("ESR_")) == doc["esr_tags"]


def test_offshore_mandates_unchanged_in_release():
    """The builder doesn't rebuild offshore wind mandates: the release's values equal the all_cases
    MinCapReq in scenario_management.yml for every model year."""
    o = pd.read_csv(PIN / "state_policies/offshore_req_default.csv").set_index("st")
    sm = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]
    n = 0
    for y, v in sm.items():
        req = ((v or {}).get("all_cases") or {}).get("MinCapReq")
        if not req:
            continue
        cur = {k.split("_")[1]: d["min_mw"] for k, d in req.items()}
        assert cur == {s: o.at[s, str(y)] for s in o.index if o.at[s, str(y)] > 0}, y
        n += 1
    assert n >= 9
