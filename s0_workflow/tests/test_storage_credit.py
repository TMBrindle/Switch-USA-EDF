"""v3.2 flag (b): the battery storage credit with the OBBBA phase-down, through the credit writer (CHANGES §100;
s0_workflow/production.apply_storage_credit, s0_workflow/tax_credits.apply_investment_terms,
pg_to_switch.gen_build_costs_file). Hand-built settings and cost tables: fixtures, not results."""
import ast
import copy
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s0_workflow import production as s0prod, tax_credits as tc  # noqa: E402
from test_s0_production import _case_settings  # noqa: E402

STAGES = (2028, 2030, 2035, 2040, 2045)
BATT = "Utility-Scale Battery Storage_Lithium Ion_Moderate"


def _axis(name):
    sm = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"]
    return copy.deepcopy(sm["tax_credits"][name])


def _stage_settings(mode=None, axis="no_wind_solar", basis=None, share_modifiers=False):
    cs = {"c": {}}
    shared = None
    for y in STAGES:
        s = s0prod.deep_merge(_case_settings(year=y), _axis(axis))
        if mode:
            s["s0_production"]["storage_credit"] = {**s["s0_production"].get("storage_credit", {}), "mode": mode}
            if basis:
                s["s0_production"]["storage_credit"]["phase_basis"] = basis
        if share_modifiers:                      # the years of a case can share settings objects
            shared = shared or s["atb_modifiers"]
            s["atb_modifiers"] = shared
        cs["c"][y] = s
    s0prod.apply_settings(cs)
    return cs["c"]


def _writer():
    tree = ast.parse((REPO / "pg_to_switch.py").read_text())
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "gen_build_costs_file"]
    g = {"pd": pd, "Path": Path}
    exec(compile(ast.Module(fn, []), "pg_to_switch", "exec"), g)
    return g["gen_build_costs_file"]


def _gens():
    rows = [{"Resource": "p1_batt_existing", "technology": BATT, "build_year": 2020}]
    for y in STAGES:
        rows += [{"Resource": "p1_batt", "technology": BATT, "build_year": y},
                 {"Resource": "p1_pv", "technology": "UtilityPV_Class1_Moderate", "build_year": y}]
    g = pd.DataFrame(rows)
    return g.assign(capex_mw=1_000_000.0, capex_mwh=250_000.0, regional_cost_multiplier=1.1,
                    Fixed_OM_Cost_per_MWyr_mean=10_000.0, Fixed_OM_Cost_per_MWhyr=1_000.0)


def test_default_is_v31_flat():
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0["storage_credit"]["mode"] == "flat"
    for y, s in _stage_settings().items():
        assert s["atb_modifiers"]["batteries"]["capex_mw"] == ["mul", 0.7], y        # x0.7 in every period
        assert s["atb_modifiers"]["batteries"]["capex_mwh"] == ["mul", 0.7], y
        assert not tc.investment_terms_for(s)


@pytest.mark.parametrize("share", [False, True])
def test_obbba_moves_the_credit_to_the_writer(share):
    for y, s in _stage_settings("obbba", share_modifiers=share).items():
        assert s["atb_modifiers"]["batteries"]["capex_mw"] == ["mul", 1.0], y
        assert s["atb_modifiers"]["batteries"]["capex_mwh"] == ["mul", 1.0], y
        t = tc.investment_terms_for(s)["storage_itc_48e"]
        assert t["itc_rate"] == pytest.approx(0.30) and t["phase_down"] == {2034: 0.75, 2035: 0.5, 2036: 0.0}
        assert not tc.terms_for(s)                     # not a production (per-MWh) term


def test_obbba_without_a_storage_credit_on_the_axis():
    for axis in ("no_wind_solar_storage", "repealed"):
        for y, s in _stage_settings("obbba", axis=axis).items():
            assert s["atb_modifiers"]["batteries"]["capex_mw"] == ["mul", 1.0]
            assert not tc.investment_terms_for(s), (axis, y)


@pytest.mark.parametrize("basis,expect", [
    ("period_label", {2028: 0.70, 2030: 0.70, 2035: 0.85, 2040: 1.0, 2045: 1.0}),
    # span_mean: 2035 covers 2031-35 (1, 1, 1, 0.75, 0.5: phase 0.85); 2040 covers 2036-40 (all 0)
    ("span_mean", {2028: 0.70, 2030: 0.70, 2035: 1 - 0.3 * 0.85, 2040: 1.0, 2045: 1.0}),
])
def test_battery_capex_by_stage(tmp_path, basis, expect):
    ssd = _stage_settings("obbba", basis=basis)
    _writer()(_gens(), tmp_path, ssd)
    bc = pd.read_csv(tmp_path / "gen_build_costs.csv", na_values=".").set_index(["GENERATION_PROJECT", "BUILD_YEAR"])
    for y, m in expect.items():
        assert bc.at[("p1_batt", y), "gen_overnight_cost"] == pytest.approx(1_100_000 * m), (basis, y)
        assert bc.at[("p1_batt", y), "gen_storage_energy_overnight_cost"] == pytest.approx(275_000 * m), (basis, y)
        assert bc.at[("p1_pv", y), "gen_overnight_cost"] == pytest.approx(1_100_000)       # other techs untouched
    assert bc.at[("p1_batt_existing", 2020), "gen_overnight_cost"] == pytest.approx(1_100_000)  # not a new build
    rep = pd.read_csv(tmp_path / "investment_credit_report.csv").set_index("PERIOD")
    assert rep.capex_multiplier.to_dict() == pytest.approx(expect)
    assert (rep.phase_basis == basis).all() and (rep.itc_rate == 0.3).all()


def test_flat_writes_build_costs_as_v31(tmp_path):
    """flat (the default): the writer sees no investment term, so gen_build_costs.csv is PowerGenome's capex (the
    x0.7 is already inside it, from atb_modifiers) and no report file is written."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _writer()(_gens(), a, _stage_settings())
    _writer()(_gens(), b)                              # the v3.1 call: no settings
    assert (a / "gen_build_costs.csv").read_text() == (b / "gen_build_costs.csv").read_text()
    assert not (a / "investment_credit_report.csv").exists()


def test_flat_and_obbba_agree_until_2033():
    """Same battery capex as v3.1 in the 2028 and 2030 stages: PowerGenome's x0.7 there equals the writer's
    1 - 0.3 x 1."""
    flat, ob = _stage_settings(), _stage_settings("obbba")
    for y in (2028, 2030):
        m_flat = flat[y]["atb_modifiers"]["batteries"]["capex_mw"][1]
        t = tc.investment_terms_for(ob[y])["storage_itc_48e"]
        m_ob = ob[y]["atb_modifiers"]["batteries"]["capex_mw"][1] * (1 - t["itc_rate"] * tc.period_phase(t, y))
        assert m_ob == pytest.approx(m_flat)


def test_investment_term_validation():
    with pytest.raises(ValueError, match="itc_rate"):
        tc.investment_terms_for({"tax_credit_terms": {"x": {"kind": "investment", "techs": ["a"]}}})
    with pytest.raises(ValueError, match="phase_basis"):
        tc.investment_terms_for({"tax_credit_terms": {"x": {"kind": "investment", "techs": ["a"], "itc_rate": 0.3,
                                                             "phase_basis": "mid"}}})
    with pytest.raises(ValueError, match="storage_credit.mode"):
        s0prod.storage_credit_settings({"storage_credit": {"mode": "phase"}})


def test_credit_writer_hook_keeps_s0_tax_credits_as_v31(tmp_path):
    """The §90 credit writer brought into v3.2 adds nothing for S0's settings (no production terms): its
    gen_tax_credits.csv is what the s0-v3.1 writer produces, under both storage settings."""
    import subprocess

    def fn(src):
        tree = ast.parse(src)
        f = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "gen_tax_credits_file"]
        g = {"pd": pd, "Path": Path}
        exec(compile(ast.Module(f, []), "pg_to_switch", "exec"), g)
        return g["gen_tax_credits_file"]

    old = subprocess.run(["git", "show", "s0-v3.1:pg_to_switch.py"], cwd=REPO, capture_output=True, text=True)
    if old.returncode:
        pytest.skip("tag s0-v3.1 not fetched")
    gens = pd.DataFrame([{"Resource": f"p1_{k}", "technology": t_, "model_year": y}
                         for y in STAGES for k, t_ in (("nuc", "Nuclear_Nuclear - Large_Moderate"),
                                                         ("pv", "UtilityPV_Class1_Moderate"), ("batt", BATT))]).assign(new_build=True)
    for mode in (None, "obbba"):
        ssd = _stage_settings(mode)
        a, b = tmp_path / f"new_{mode}", tmp_path / f"old_{mode}"
        a.mkdir()
        b.mkdir()
        pd.DataFrame({"GENERATION_PROJECT": gens.Resource.unique(), "gen_max_age": 30}).to_csv(a / "gen_info.csv",
                                                                                                index=False)
        fn((REPO / "pg_to_switch.py").read_text())(gens.copy(), ssd, a)
        fn(old.stdout)(gens.copy(), ssd, b)
        assert (a / "gen_tax_credits.csv").read_text() == (b / "gen_tax_credits.csv").read_text()
        assert sorted(x.name for x in a.iterdir()) == ["gen_info.csv", "gen_tax_credits.csv"]
