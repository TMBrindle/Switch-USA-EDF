"""Statutory tax credits levelised over the capital-recovery life (CHANGES §90; s0_workflow/tax_credits.py,
pg_to_switch.gen_tax_credits_file, s0_workflow/credit_tally.py). Hand-built generator tables and toy outputs:
fixtures, not results."""
import ast
import copy
import logging
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import credit_tally, tax_credits as tc  # noqa: E402

SM = REPO / "pg/settings/scenario_management.yml"
V31 = "9d68751"


def _writer():
    tree = ast.parse((REPO / "pg_to_switch.py").read_text())
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "gen_tax_credits_file"]
    g = {"pd": pd, "Path": Path}
    exec(compile(ast.Module(fn, []), "pg_to_switch", "exec"), g)
    return g["gen_tax_credits_file"]


def _axis(name, sm_text=None):
    sm = yaml.safe_load(sm_text) if sm_text else yaml.safe_load(open(SM))
    return copy.deepcopy(sm["settings_management"]["all_years"]["tax_credits"][name])


GENS = pd.DataFrame([
    {"Resource": "p1_landbasedwind_class3_moderate_0", "technology": "LandbasedWind_Class3_Moderate"},
    {"Resource": "p1_utilitypv_class1_moderate_0", "technology": "UtilityPV_Class1_Moderate"},
    {"Resource": "p1_nuclear_large_moderate_0", "technology": "Nuclear_Nuclear - Large_Moderate"},
    {"Resource": "p1_battery_0", "technology": "Utility-Scale Battery Storage_Lithium Ion_Advanced"},
])
GI = pd.DataFrame({"GENERATION_PROJECT": GENS.Resource, "gen_max_age": [500, 500, 500, 500],
                   "gen_amortization_period": [30, 30, 40, 15]})


def _case(tmp_path, name, years, axis_by_year, s0=True, gi=GI):
    out = tmp_path / name
    out.mkdir()
    gi.to_csv(out / "gen_info.csv", index=False)
    gens = pd.concat([GENS.assign(model_year=y, new_build=True) for y in years], ignore_index=True)
    base = {"interest_rate": 0.05, "discount_rate": 0.03, "target_usd_year": 2024}
    if s0:
        base["s0_production"] = {"enabled": True, "extra_modules": [tc.AMORT_MODULE], "foresight": {"mode": "myopic"}}
    ssd = {y: {**copy.deepcopy(base), **copy.deepcopy(axis_by_year[y])} for y in years}
    _writer()(gens, ssd, out)
    return out


def test_annuity_and_level_factor():
    af10, af30 = tc.annuity_factor(0.05, 10), tc.annuity_factor(0.05, 30)
    assert af10 == pytest.approx(7.72173, abs=1e-5) and af30 == pytest.approx(15.37245, abs=1e-5)
    assert tc.level_factor(0.05, 10, 30) == pytest.approx(0.50231, abs=1e-5)
    assert 27.5 * tc.level_factor(0.05, 10, 30) == pytest.approx(13.81, abs=0.01)
    assert tc.level_factor(0.05, 10, 10) == 1.0 and tc.level_factor(0.05, 12, 10) == 1.0     # duration >= life
    assert tc.annuity_factor(0.0, 7) == 7
    assert tc.financial_rates({}) == (0.05, 0.03)


def test_rates_and_lives_are_the_capital_annualisation_fields():
    """r: the same interest_rate setting pg_to_switch writes to financials.csv; life: gen_amortization_period when
    the case loads study_modules.gen_amortization_period (S0: s0_production.extra_modules), else gen_max_age."""
    src = (REPO / "pg_to_switch.py").read_text()
    assert 'for name, default in [("interest_rate", 0.05), ("discount_rate", 0.03)]' in src
    sw = yaml.safe_load(open(REPO / "pg/settings/switch.yml"))
    assert tc.financial_rates(sw) == (sw["interest_rate"], sw["discount_rate"]) == (0.05, 0.03)
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert tc.AMORT_MODULE in s0["extra_modules"]
    assert tc.amortization_on({"s0_production": {**s0, "enabled": True}})
    assert not tc.amortization_on({"s0_production": {**s0, "enabled": False}})          # fedpol: not loaded
    assert tc.AMORT_MODULE not in (REPO / "switch/modules.txt").read_text()
    lf = tc.lives(GI, True).life_years.tolist(), tc.lives(GI, False).life_years.tolist()
    assert lf == ([30, 30, 40, 15], [500, 500, 500, 500])
    res = yaml.safe_load(open(REPO / "pg/settings/resources.yml"))
    assert res["atb_cap_recovery_years"] == 30                   # wind and solar gen_amortization_period
    assert tc.run_mode({"s0_production": {"enabled": True, "foresight": {"mode": "windows"}}}) == "rolling"


def test_full_ira_levelised_s_set(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    out = _case(tmp_path, "s1", [2030, 2035], {2030: _axis("full_ira"), 2035: _axis("full_ira")})
    c = pd.read_csv(out / "gen_tax_credits.csv").set_index(["GENERATION_PROJECT", "PERIOD"]).gen_ptc_value_per_mwh
    lev = 27.5 * tc.level_factor(0.05, 10, 30)
    for y in (2030, 2035):
        assert c[("p1_landbasedwind_class3_moderate_0", y)] == pytest.approx(lev)
        assert c[("p1_utilitypv_class1_moderate_0", y)] == pytest.approx(lev)
        assert c[("p1_nuclear_large_moderate_0", y)] == 15.0                            # placeholder, unscaled
    assert ("p1_battery_0", 2030) not in c.index
    rep = pd.read_csv(out / "credit_levelisation_report.csv")
    w = rep[(rep.tax_credit == "ptc_45y") & rep.technology.str.contains("Wind")].iloc[0]
    assert (w.value_per_mwh, w.duration_years, w.rate, w.life_years, w.life_source) == \
        (27.5, 10, 0.05, 30, "gen_amortization_period")
    assert w.factor == pytest.approx(0.50231, abs=1e-5) and w.levelised_value_per_mwh == pytest.approx(13.81, abs=0.01)
    assert w.run_mode == "myopic" and w.discount_rate == 0.03
    n = rep[rep.tax_credit == "new_nuclear_ptc_placeholder"].iloc[0]
    assert n.prelevelised and n.factor == 1.0 and "PLACEHOLDER" in n.flag
    v = pd.read_csv(out / "gen_tax_credits_by_vintage.csv")                             # the vintage hook
    assert set(v.BUILD_YEAR) == {2030, 2035} and (v[v.tax_credit == "ptc_45y"].statutory_value_per_mwh == 27.5).all()
    t = pd.read_csv(out / "tax_credit_terms.csv")
    assert set(t.tax_credit) == {"ptc_45y", "new_nuclear_ptc_placeholder"}
    assert any("discount rate is 0.0300" in r.message for r in caplog.records)           # r != discount rate: warned


def test_window_phase_down_and_without_amortisation(tmp_path):
    ax = _axis("full_ira")
    ax["tax_credit_terms"]["ptc_45y"].update(build_years=[2026, 2035], phase_down={2033: 0.5})
    out = _case(tmp_path, "w", [2030, 2035, 2040], {y: ax for y in (2030, 2035, 2040)})
    c = pd.read_csv(out / "gen_tax_credits.csv").set_index(["GENERATION_PROJECT", "PERIOD"]).gen_ptc_value_per_mwh
    lev = 27.5 * tc.level_factor(0.05, 10, 30)
    w = "p1_landbasedwind_class3_moderate_0"
    assert c[(w, 2030)] == pytest.approx(lev) and c[(w, 2035)] == pytest.approx(0.5 * lev)
    assert (w, 2040) not in c.index                                                     # outside the window
    # a case without the amortisation module recovers capital over gen_max_age (500 years): the factor follows it
    out = _case(tmp_path, "noam", [2030], {2030: _axis("full_ira")}, s0=False)
    c = pd.read_csv(out / "gen_tax_credits.csv").set_index(["GENERATION_PROJECT", "PERIOD"]).gen_ptc_value_per_mwh
    assert c[(w, 2030)] == pytest.approx(27.5 * tc.level_factor(0.05, 10, 500))
    assert pd.read_csv(out / "credit_levelisation_report.csv").life_source.eq("gen_max_age").any()


def test_conflicts_stop_the_build(tmp_path, caplog):
    ax = _axis("full_ira")
    ax["tax_credit_terms"]["ptc_45y"]["levelised_value_per_mwh"] = 27.5                 # hand-entered, wrong
    with pytest.raises(ValueError, match="conflicts with the computed"):
        _case(tmp_path, "h1", [2030], {2030: ax})
    ax["tax_credit_terms"]["ptc_45y"]["levelised_value_per_mwh"] = round(27.5 * tc.level_factor(0.05, 10, 30), 3)
    _case(tmp_path, "h2", [2030], {2030: ax})                                           # agrees: fine
    both = _axis("full_ira")
    both["tax_credit_values"] = {"LandbasedWind": 27.5}                                 # old-style full-life value
    with pytest.raises(ValueError, match="also credited 27.5 by hand-entered"):
        _case(tmp_path, "h3", [2030], {2030: both})
    usd = _axis("full_ira")
    usd["tax_credit_terms"]["ptc_45y"]["dollar_year"] = 2023
    with pytest.raises(ValueError, match="target_usd_year"):
        _case(tmp_path, "h4", [2030], {2030: usd})
    caplog.set_level(logging.WARNING)
    r = _axis("full_ira")
    r["tax_credit_terms"]["ptc_45y"]["rate"] = 0.07
    _case(tmp_path, "h5", [2030], {2030: r})
    assert any("not the capital annualisation rate" in x.message for x in caplog.records)


def test_s0_unchanged(tmp_path):
    """S0's setting (no_wind_solar) is v3.1's exactly (so S0 and the regression row resolve and build as before):
    the writer gives the same gen_tax_credits.csv and no new files. Its hand-entered $15 is an in-model value (the
    prelevelised placeholder); in full_ira the same placeholder is an explicit prelevelised term."""
    old_text = subprocess.run(["git", "show", f"{V31}:pg/settings/scenario_management.yml"], cwd=REPO,
                              capture_output=True, text=True).stdout
    if not old_text:
        pytest.skip(f"{V31} not in this checkout")
    assert _axis("no_wind_solar") == _axis("no_wind_solar", old_text)
    assert _axis("no_wind_solar_storage") == _axis("no_wind_solar_storage", old_text)
    new = _case(tmp_path, "new", [2028, 2030, 2035], {y: _axis("no_wind_solar") for y in (2028, 2030, 2035)})
    assert sorted(f.name for f in new.iterdir()) == ["gen_info.csv", "gen_tax_credits.csv"]
    assert (pd.read_csv(new / "gen_tax_credits.csv").gen_ptc_value_per_mwh == 15.0).all()
    # a prelevelised term alone writes the same file and no new files either
    pre = {"tax_credit_terms": {"n": {"value_per_mwh": 15, "techs": ["Nuclear"], "prelevelised": True}}}
    p2 = _case(tmp_path, "pre", [2028, 2030, 2035], {y: pre for y in (2028, 2030, 2035)})
    assert (p2 / "gen_tax_credits.csv").read_bytes() == (new / "gen_tax_credits.csv").read_bytes()
    assert sorted(f.name for f in p2.iterdir()) == ["gen_info.csv", "gen_tax_credits.csv"]
    # full_ira_unlev_v3 = v3's full_ira, byte for byte in its output
    a = _case(tmp_path, "unlev", [2035], {2035: _axis("full_ira_unlev_v3")})
    b = _case(tmp_path, "v3full", [2035], {2035: _axis("full_ira", old_text)})
    assert (a / "gen_tax_credits.csv").read_bytes() == (b / "gen_tax_credits.csv").read_bytes()
    assert _axis("full_ira_unlev_v3")["tax_credit_values"] == _axis("full_ira", old_text)["tax_credit_values"]


def test_rows_use_the_right_setting():
    si = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv")
    s0 = si[si.case_id.isin(["S0prod_A", "S0prod_B", "S0_tx", "s4x1_S0prod_2035", "s4x1_S0prod_2035_new"])]
    assert set(s0.tax_credits) == {"no_wind_solar"}
    fed = si[si.case_id.str.startswith("s4x1_fedpol")]
    assert "full_ira" not in set(fed.tax_credits) and "full_ira_unlev_v3" in set(fed.tax_credits)
    s1 = si[si.case_id == "S1"].sort_values("year")
    assert list(s1.tax_credits) == ["no_wind_solar"] + ["full_ira"] * 4                 # S-set: levelised from 2030
    assert "full_ira_unlev_v3" not in set(si[~si.case_id.str.startswith("s4x1_fedpol")].tax_credits)


def test_tally_statutory_profile(tmp_path):
    """One wind project built in 2030 (period 2029-2030) and a predetermined 2027 vintage; energy by period; the
    statutory profile is 10 years of energy x 27.5 from each vintage's first year in service, PV at 3% to 2024."""
    i, o = tmp_path / "in", tmp_path / "out"
    i.mkdir(), o.mkdir()
    pd.DataFrame({"INVESTMENT_PERIOD": [2030, 2035], "period_start": [2029, 2031], "period_end": [2030, 2035]}).to_csv(
        i / "periods.csv", index=False)
    pd.DataFrame({"base_financial_year": [2024], "interest_rate": [0.05], "discount_rate": [0.03]}).to_csv(
        i / "financials.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["w"], "gen_tech": ["LandbasedWind_Class3_Moderate"]}).to_csv(
        i / "gen_info.csv", index=False)
    pd.DataFrame([{"PERIOD": p, "tax_credit": "ptc_45y", "techs": '["LandbasedWind"]', "value_per_mwh": 27.5,
                   "duration_years": 10, "prelevelised": False, "build_year_first": 2025, "build_year_last": None,
                   "phase_down": "{}", "dollar_year": 2024} for p in (2030, 2035)]
                 + [{"PERIOD": 2030, "tax_credit": "nuc", "techs": '["Nuclear"]', "value_per_mwh": 15,
                     "duration_years": None, "prelevelised": True, "build_year_first": None, "build_year_last": None,
                     "phase_down": "{}", "dollar_year": 2024}]).to_csv(i / "tax_credit_terms.csv", index=False)
    pd.DataFrame({"GEN_BLD_YRS_1": ["w", "w"], "GEN_BLD_YRS_2": [2027, 2030], "BuildGen": [100.0, 300.0]}).to_csv(
        o / "BuildGen.csv", index=False)
    pd.DataFrame({"generation_project": ["w", "w"], "period": [2030, 2035], "Energy_GWh_typical_yr": [1000.0, 1200.0],
                  "GenCapacity_MW": [400.0, 400.0]}).to_csv(o / "dispatch_gen_annual_summary.csv", index=False)
    t = credit_tally.tally([{"inputs": i, "outputs": o}])
    m = t[t.build_year == 2030].set_index("year")
    assert list(m.index) == list(range(2029, 2039))                                     # 10 years from 2029
    assert m.at[2029, "energy_mwh"] == pytest.approx(750e3) and m.at[2031, "energy_mwh"] == pytest.approx(900e3)
    assert m.at[2038, "period_energy_from"] == 2035                                     # past the horizon: last period
    assert m.at[2029, "credit_usd"] == pytest.approx(750e3 * 27.5)
    assert m.at[2031, "pv_usd"] == pytest.approx(900e3 * 27.5 / 1.03 ** 7)
    p = t[t.build_year == 2027]                                                          # predetermined vintage
    assert list(p.year) == list(range(2029, 2037)) and not p.model_build.any()           # years before 2029: no energy
    assert set(t.tax_credit) == {"ptc_45y"}                                              # placeholder: no profile
    s = credit_tally.summary(t)
    assert s.credit_usd.sum() == pytest.approx(t.credit_usd.sum())
