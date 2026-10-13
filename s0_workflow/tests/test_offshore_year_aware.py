"""§241: the offshore check requires only the groups a stage offers. The S0 v3.2 build at 4bdbcad stopped in 2028
("groups with resource but no offshore technology in the case: [('floating', 0), ('floating', 1)]"): the 2028-29
atb_new_gen in scenario_management.yml has no floating offshore (Class12; no ATB prices before 2030), so 2028 has only
fixed_0 and fixed_1, as in v3.1 and F3. Synthetic gen_info / true-resource tables are test fixtures only."""
import copy
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))
from s0_workflow import offshore_groups as og  # noqa: E402
from s0_workflow import production as s0prod  # noqa: E402
from test_s_set_on_v32 import FILES  # noqa: E402

CASES = ["S0prod_A", "BILL_central", "BILL_central_txonly", "S1", "S4", "S2", "S5", "S3"]
YEARS = (2028, 2030, 2035, 2040, 2045)
FIXED = {("fixed", 0), ("fixed", 1)}
ALL4 = FIXED | {("floating", 0), ("floating", 1)}


def _stage_settings(case, year):
    """atb_new_gen and renewables_clusters as a case's stage gets them: resources.yml, then the year's all_cases
    (scenario_management.yml), then the case's axis values (chain_reuse._merged), then the fourth entry (§137)."""
    from s0_workflow import chain_reuse as cr
    res = yaml.safe_load(open(REPO / "pg/settings/resources.yml"))
    sm = yaml.safe_load(open(REPO / FILES[1]))["settings_management"]
    s = {k: copy.deepcopy(res[k]) for k in ("atb_new_gen", "renewables_clusters")}
    for k in s:
        if k in ((sm.get(year) or {}).get("all_cases") or {}):
            s[k] = copy.deepcopy(sm[year]["all_cases"][k])
    si = pd.read_csv(REPO / FILES[0])
    row = si[(si.case_id == case) & (si.year == year)].iloc[0]
    ax = cr._merged(sm["all_years"], sm.get(year, {}), row, [c for c in si.columns if c not in ("case_id", "year")],
                    yaml.safe_load(open(REPO / FILES[2]))["s0_production"], year)
    for k in s:
        if k in ax:
            s[k] = ax[k]
    og.add_floating_pref_entries(s)
    return s


def test_offered_groups_by_case_and_year():
    """Every v3.2 case: fixed only before 2030, all four groups from 2030 (the case axes don't change it)."""
    for case in CASES:
        for year in YEARS:
            assert og.offered_groups(_stage_settings(case, year)) == (FIXED if year < 2030 else ALL4), (case, year)
    assert og.atb_turbine_type("Class3") == "fixed" and og.atb_turbine_type("Class12") == "floating"
    assert og.atb_turbine_type("Class 8") == "floating" and og.atb_turbine_type("Lithium Ion") is None
    # no offshore in atb_new_gen: nothing offered, whatever the cluster entries
    assert og.offered_groups({"atb_new_gen": [["UtilityPV", "Class1", "Moderate", 1]],
                              "renewables_clusters": _stage_settings("S0prod_A", 2030)["renewables_clusters"]}) == set()


@pytest.fixture
def split(tmp_path):
    """A split folder's true resource (meta dir) for zones p1 (all four groups) and p8 (fixed only)."""
    d = tmp_path / "rg_v32"
    d.mkdir()
    og.meta_dir(d).mkdir()
    pd.DataFrame({"zone": ["p1", "p1", "p1", "p1", "p8", "p8"],
                  "turbine_type": ["fixed", "fixed", "floating", "floating", "fixed", "fixed"],
                  "pref_site": [0, 1, 0, 1, 0, 1], "mw": [200.0, 150.0, 300.0, 40.0, 30.0, 20.0]}
                 ).to_csv(og.meta_dir(d) / og.TRUE_RES, index=False)
    return {"offshore_filter_fix": {"mode": "split_groups", "resource_groups_dir": str(d)}}


def _case(tmp_path, name, rows):
    """gen_info.csv with the given (gen_tech, zone, MW) offshore rows, an existing offshore plant and a gas plant."""
    c = tmp_path / name
    c.mkdir()
    rows = rows + [("Offshore Wind Turbine", "p1", 30.0), ("NaturalGas_Combustion Turbine (F-Frame)_Moderate", "p8", 100)]
    pd.DataFrame({"GENERATION_PROJECT": [f"g{i}" for i in range(len(rows))], "gen_tech": [r[0] for r in rows],
                  "gen_load_zone": [r[1] for r in rows], "gen_capacity_limit_mw": [r[2] for r in rows]}
                 ).to_csv(c / "gen_info.csv", index=False)
    return c


FX0, FX1 = "OffShoreWind_Class3_Moderate_fixed_0", "OffShoreWind_Class3_Moderate_fixed_1"
FL0, FL1 = "OffShoreWind_Class12_Moderate_floating_0", "OffShoreWind_Class12_Moderate_floating_1"
STAGE_2028 = [(FX0, "p1", 200.0), (FX1, "p1", 150.0), (FX0, "p8", 30.0), (FX1, "p8", 20.0)]
STAGE_2030 = STAGE_2028 + [(FL0, "p1", 300.0), (FL1, "p1", 40.0)]


def test_2028_stage_fixed_only_passes(tmp_path, split):
    """The VM's failure: a 2028 stage (no floating offered) passes, and the limits are still compared."""
    case = _case(tmp_path, "c2028", STAGE_2028)
    logs = []
    s0prod.write_offshore_check(case, split, logs.append, {2028: _stage_settings("S0prod_A", 2028)})
    t = pd.read_csv(case / og.BY_ZONE).set_index("zone")
    assert t.ok.all() and t.loc["p1", "true_mw"] == 350.0                        # fixed only, floating not counted
    assert "groups [('fixed', 0), ('fixed', 1)]" in logs[-1]
    assert "not offered this stage (atb_new_gen): [('floating', 0), ('floating', 1)], 0.3 GW" in logs[-1]
    # without the stage's settings (the §137 rule), the same case stops as on the VM
    with pytest.raises(ValueError, match=r"no offshore technology in the case: \[\('floating', 0\), \('floating', 1\)\]"):
        s0prod.write_offshore_check(case, split, logs.append)
    # limits are still compared: fixed_1 offered twice in p8
    bad = _case(tmp_path, "c2028_dup", STAGE_2028 + [(FX1, "p8", 20.0)])
    with pytest.raises(ValueError, match="p8 x1.40"):
        s0prod.write_offshore_check(bad, split, logs.append, {2028: _stage_settings("S0prod_A", 2028)})
    # a floating technology in a stage that offers none is a build error too
    odd = _case(tmp_path, "c2028_fl", STAGE_2028 + [(FL0, "p1", 300.0)])
    with pytest.raises(ValueError, match="groups its settings don't offer"):
        s0prod.write_offshore_check(odd, split, logs.append, {2028: _stage_settings("S0prod_A", 2028)})


def test_2030_stage_requires_all_four(tmp_path, split):
    s30 = {2030: _stage_settings("S0prod_A", 2030)}
    logs = []
    s0prod.write_offshore_check(_case(tmp_path, "c2030", STAGE_2030), split, logs.append, s30)
    assert "groups [('fixed', 0), ('fixed', 1), ('floating', 0), ('floating', 1)]" in logs[-1]
    assert "not offered" not in logs[-1]
    with pytest.raises(ValueError, match=r"offered in \[2030\].*\[\('floating', 1\)\]"):
        s0prod.write_offshore_check(_case(tmp_path, "c2030_3", STAGE_2030[:-1]), split, logs.append, s30)
    with pytest.raises(ValueError, match=r"\[\('floating', 0\), \('floating', 1\)\]"):
        s0prod.write_offshore_check(_case(tmp_path, "c2030_fixed", STAGE_2028), split, logs.append, s30)
    # a case folder spanning 2028 and 2030 (multi-period): the union, so floating is required
    with pytest.raises(ValueError, match="floating"):
        s0prod.write_offshore_check(_case(tmp_path, "c_multi", STAGE_2028), split, logs.append,
                                    {2028: _stage_settings("S0prod_A", 2028), **s30})


def test_no_new_offshore_and_zones_outside_the_case(tmp_path, split):
    """A stage with no new offshore technology at all now stops if its settings offer a group with resource (it used to
    return before the check); groups in zones the case doesn't have are not required."""
    logs = []
    empty = _case(tmp_path, "c_none", [])
    with pytest.raises(ValueError, match="no offshore technology"):
        s0prod.write_offshore_check(empty, split, logs.append, {2028: _stage_settings("S0prod_A", 2028)})
    none_offered = {2028: {"atb_new_gen": [], "renewables_clusters": []}}
    s0prod.write_offshore_check(empty, split, logs.append, none_offered)
    assert "no new offshore technologies in this stage" in logs[-1]
    p8 = _case(tmp_path, "c_p8", [(FX0, "p8", 30.0), (FX1, "p8", 20.0)])
    gi = pd.read_csv(p8 / "gen_info.csv")
    gi = gi[gi.gen_load_zone != "p1"]
    gi.to_csv(p8 / "gen_info.csv", index=False)                              # a case without p1
    s0prod.write_offshore_check(p8, split, logs.append, {2030: _stage_settings("S0prod_A", 2030)})
    assert "limits equal the true resource in 1 zones" in logs[-1]


def test_flags_off_unchanged(tmp_path):
    """Split off (v3.1, F1-F3): the report only, as before, with or without the stage's settings."""
    case = _case(tmp_path, "off", [(FX0, "p1", 200.0), (FX1, "p1", 200.0), (FL0, "p1", 200.0)])
    a, b = [], []
    s0prod.write_offshore_check(case, {}, a.append)
    t1 = (case / og.BY_ZONE).read_bytes()
    s0prod.write_offshore_check(case, {}, b.append, {2028: _stage_settings("S0prod_A", 2028)})
    assert a == b and (case / og.BY_ZONE).read_bytes() == t1 and "offshore (v3.1)" in a[0] and "3.00" in a[0]
    none = _case(tmp_path, "off_none", [])
    c = []
    s0prod.write_offshore_check(none, {}, c.append, {2028: {}})
    assert c == [] and not (none / og.BY_ZONE).exists()


def test_credit_report_reads_an_empty_investment_report(tmp_path):
    """Sweep (§241): a stage whose investment credit matched no project writes an investment_credit_report.csv with no
    rows; the credit report treats it as no credit in that stage instead of failing on it."""
    from s0_workflow import credit_spend as cs
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    pd.DataFrame().to_csv(tmp_path / "in/investment_credit_report.csv", index=False)
    assert cs._read(tmp_path / "in/investment_credit_report.csv") is None and cs._read(tmp_path / "nope.csv") is None
    st = cs.storage_itc([{"inputs": tmp_path / "in", "outputs": tmp_path / "out"}], "S0")
    assert st.empty and list(st.columns)[:3] == ["case", "period", "category"]
