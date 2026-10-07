"""S0 production settings and case steps (items 1-4, 6, 7). Small hand-built frames are fixtures, not
results. Run from the repo root: SWITCH_SRC=/opt/switch-src pytest -q s0_workflow/tests"""
import ast
import collections
import copy
import logging
import shlex
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import coal_cf, day_selection, production as s0prod  # noqa: E402


def s0_yaml():
    return yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]


def axis():
    sm = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))
    return sm["settings_management"]["all_years"]["s0_production"]


# ----------------------------------------------------------------------------------- settings (7)
def test_settings_file_axis_and_rows():
    s0 = s0_yaml()
    assert s0["enabled"] is False                                   # inert unless a case turns it on
    ax = axis()
    assert set(ax) == {"off", "on", "on_windows", "on_pgdays", "on_pgdays_new", "on_single"}
    assert ax["on"]["s0_production"] == {"enabled": True}
    assert ax["on_windows"]["s0_production"]["foresight"]["mode"] == "windows"
    assert ax["on_pgdays"]["s0_production"]["time_sampling"]["method"] == "powergenome"
    # the regression case keeps the legacy settings; its _new twin takes the new defaults
    leg = ax["on_pgdays"]["s0_production"]
    assert leg["gas_capex"] == {"mode": "atb_moderate"} and leg["gas_turbine_cap"] == {"form": "legacy"}
    assert leg["rps_acp"] == {"mode": "programs"} and leg["state_policies"] == {"release": "legacy"}
    assert leg["new_build_rule"] == {"enabled": False}
    assert ax["on_pgdays_new"]["s0_production"] == {"enabled": True, "time_sampling": {"method": "powergenome"},
                                                    "foresight": {"mode": "single"}}
    si = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv")
    old = si[~si.case_id.str.contains("S0prod|S0_tx|BILL") & (si.s_set == "none")]   # §75 S-set rows are S0
    assert (old.s0_production == "off").all() and (old.build_rate == "off").all()   # existing rows unchanged
    a = si[si.case_id == "S0prod_A"]
    assert sorted(a.year) == [2028, 2030, 2035, 2040, 2045] and (a.s0_production == "on").all()
    assert (si[si.case_id == "S0prod_B"].s0_production == "on_windows").all()
    reg = si[si.case_id == "s4x1_S0prod_2035"].iloc[0]
    base = si[si.case_id == "s4x1_S0unc_2035_icon"].iloc[0]
    diff = [c for c in si.columns if reg[c] != base[c]]
    assert set(diff) == {"case_id", "build_rate", "s0_production", "retirements_pre2030", "forced_tx"}
    assert reg.s0_production == "on_pgdays" and reg.retirements_pre2030 == "legacy" and reg.forced_tx == "legacy"
    new = si[si.case_id == "s4x1_S0prod_2035_new"].iloc[0]
    # §66: _new carries the final S0 configuration (fleet-independent days, regional reserve, S0_tx transmission)
    assert [c for c in si.columns if new[c] != reg[c]] == ["case_id", "s0_production", "retirements_pre2030", "forced_tx",
                                                           "prm_design", "tx_bill"]
    assert new.s0_production == "on_single" and new.retirements_pre2030 == "block_all"
    assert new.forced_tx == "reeds_certain_plus_A" and new.prm_design == "regional" and new.tx_bill == "s0_tx"
    for c in ("S0prod_A", "S0prod_B"):
        r = si[si.case_id == c]
        assert (r.forced_tx == "reeds_certain_plus_A").all() and (r.tx_bill == "s0_tx").all()
        assert (r.prm_design == "regional").all() and (r.time_sample == "days24").all()
    md = yaml.safe_load(open(REPO / "pg/settings/model_definition.yml"))
    spans = {int(k): v for k, v in s0["period_spans"].items()}
    for y, (first, last) in spans.items():
        assert md["model_first_planning_year"][md["model_year"].index(y)] == first and last == y
    # everything the README's hand steps did is on
    assert s0["settings"]["build_rate"]["level"] == "central" and s0["settings"]["build_rate"]["regional_groups"] == [
        "wind_onshore", "solar"]
    assert s0["settings"]["interconnection_headroom"]["scenario"] == "atts_s0"
    # new defaults: central premium paired with the central turbine allowance; flat $100 buyouts
    assert s0["gas_capex"]["mode"] == "premium" and s0["gas_capex"]["path"] == "central"
    assert s0["gas_turbine_cap"] == {"form": "allowance", "path": "central"}
    assert s0["rps_acp"]["mode"] == "flat" and s0["rps_acp"]["price"] == 100.0
    assert s0["rps_acp"]["programs"] == {"ESR_NY_rps": 45.39}                       # legacy option kept
    assert s0["state_policies"] == {"release": "2026.09.21"}                         # pinned ReEDS release
    # no new nuclear before the 2035 stage (separate from the nuclear growth cap)
    assert s0["new_build_rule"] == {"enabled": True, "no_new_build_before": 2035, "technologies": ["nuclear"]}
    assert s0["coal_cf_caps"]["enabled"] and s0["wind_loss"]["enabled"]
    pp = s0["gas_capex"]["premium_paths"]
    assert pp["central"]["cc"] == {2031: 0.37, 2032: 0.28, 2033: 0.18, 2034: 0.09, 2035: 0.0}
    assert pp["central"]["ct"] == {2031: 0.45, 2032: 0.34, 2033: 0.23, 2034: 0.11, 2035: 0.0}
    assert pp["low"]["cc"] == {2033: 0.5, 2034: 0.4, 2035: 0.3, 2036: 0.2, 2037: 0.1, 2038: 0.0}
    assert pp["low"]["ct"] == {2033: 0.45, 2034: 0.36, 2035: 0.27, 2036: 0.18, 2037: 0.09, 2038: 0.0}
    assert pp["high"]["cc"] == {2029: 0.37, 2030: 0.25, 2031: 0.12, 2032: 0.0}
    assert pp["high"]["ct"] == {2029: 0.45, 2030: 0.30, 2031: 0.15, 2032: 0.0}
    assert s0["wind_loss"]["factor"] == pytest.approx((1 - 0.134) / (1 - 0.017))
    assert s0["extra_modules"] == ["study_modules.gen_amortization_period"]          # item 6, case only
    assert "gen_amortization_period" not in open(REPO / "switch/modules.txt").read()
    assert s0["time_sampling"]["method"] == "fleet_independent" and s0["retirement_rule"]["enabled"]


def _case_settings(year=2035, axis_value="on", first=None):
    res = yaml.safe_load(open(REPO / "pg/settings/resources.yml"))
    s = {"s0_production": copy.deepcopy(s0_yaml()), "atb_modifiers": copy.deepcopy(res["atb_modifiers"]),
         "build_rate": {"enabled": False, "level": "low"}, "interconnection_headroom": {"enabled": False},
         "model_first_planning_year": first or {2028: 2026, 2030: 2029, 2035: 2031, 2040: 2036, 2045: 2041}[year],
         "case_id": "c"}
    s = s0prod.deep_merge(s, axis()[axis_value])
    if axis_value == "on_pgdays":            # the regression row: its prm_design / tx_bill columns set those keys (§81)
        s = s0prod.deep_merge(s, regression_columns())
    return s


def regression_columns() -> dict:
    """The settings the regression row's other S0 columns add to on_pgdays (§81: prm_design legacy, tx_bill legacy)."""
    sm = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"]
    return s0prod.deep_merge(copy.deepcopy(sm["prm_design"]["legacy"]), sm["tx_bill"]["legacy"])


def test_apply_settings_merges_and_sets_atb_moderate():
    cs = {"c": {2035: _case_settings()}, "legacy": {2035: {"atb_modifiers": {"ngcc": {
        "technology": "NaturalGas", "capex_mw": 2061000}}}}}
    s0prod.apply_settings(cs)
    s = cs["c"][2035]
    assert s["build_rate"] == {"enabled": True, "level": "central", "groups": ["wind_onshore", "solar", "storage"],
                               "regional": True, "regional_groups": ["wind_onshore", "solar"],
                               "gas_turbine_cap": {"enabled": True, "form": "cumulative_additions",
                                                   "allowance_path": "central"}}
    assert s["interconnection_headroom"] == {"enabled": True, "scenario": "atts_s0"}
    assert s["atb_modifiers"]["ngcc"]["capex_mw"] == ["mul", 1.0] and s["atb_modifiers"]["ngct"]["capex_mw"] == ["mul", 1.0]
    assert s["atb_modifiers"]["batteries"]["capex_mw"] == ["mul", 0.7]                  # untouched
    assert cs["legacy"][2035]["atb_modifiers"]["ngcc"]["capex_mw"] == 2061000           # other cases untouched
    with pytest.raises(ValueError, match="period_spans"):
        s0prod.apply_settings({"c": {2035: _case_settings(first=2030)}})
    assert s0prod.foresight_mode(cs["c"][2035]) == "myopic"
    assert s0prod.foresight_mode(cs["legacy"][2035]) == "default"
    assert s0prod.foresight_mode(_case_settings(axis_value="on_windows")) == "windows"
    assert "--include-module study_modules.gen_amortization_period" in s0prod.scenario_options(s)
    assert "--include-module study_modules.retirement_rules" in s0prod.scenario_options(s)
    assert "--include-module study_modules.build_rules" in s0prod.scenario_options(s)
    for ax in ("on_windows", "on_pgdays_new"):
        assert "study_modules.build_rules" in s0prod.scenario_options(_case_settings(axis_value=ax))
    assert "build_rules" not in s0prod.scenario_options(_case_settings(axis_value="on_pgdays"))   # legacy
    assert s0prod.scenario_options({}) == ""


# ---------------------------------------------------------------------------------- case steps (2-4)
def _case(folder: Path):
    gi = pd.DataFrame({
        "GENERATION_PROJECT": ["p1_coal", "p2_coal", "agg_coal", "p1_cc_new", "p1_ct_new", "p1_wind", "p1_wind_old",
                               "p1_osw", "p1_pv", "p1_gas_old"],
        "gen_tech": ["Conventional Steam Coal", "Conventional Steam Coal", "Conventional Steam Coal",
                     "NaturalGas_1-on-1 Combined Cycle (H-Frame)_Moderate",
                     "NaturalGas_Combustion Turbine (F-Frame)_Moderate", "LandbasedWind_Class3_Moderate_1",
                     "Onshore Wind Turbine", "OffShoreWind_Class12_Moderate_1", "UtilityPV_Class1_Moderate_1",
                     "Natural Gas Fired Combined Cycle"],
        "gen_load_zone": ["p107", "p10", "AGG", "p1", "p1", "p1", "p1", "p1", "p1", "p1"],
        "gen_energy_source": ["coal"] * 3 + ["naturalgas"] * 2 + ["wind"] * 3 + ["sun", "naturalgas"],
        "gen_forced_outage_rate": [0.1] * 10, "gen_can_retire_early": [0] * 10})
    gi.to_csv(folder / "gen_info.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["p1_coal", "p2_coal", "agg_coal", "p1_wind_old", "p1_gas_old"],
                  "build_year": [1980, 1985, 1990, 2010, 2005],
                  "build_gen_predetermined": [500, 50, 100, 10, 300]}).to_csv(folder / "gen_build_predetermined.csv",
                                                                               index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["p1_cc_new"] * 3 + ["p1_ct_new"] * 3 + ["p1_gas_old"],
                  "build_year": [2028, 2030, 2035] * 2 + [2005],
                  "gen_overnight_cost": [1000.0] * 6 + [0.0], "gen_fixed_om": [1.0] * 7}).to_csv(
        folder / "gen_build_costs.csv", index=False)
    rows = [{"GENERATION_PROJECT": g, "timepoint": t, "gen_max_capacity_factor": 0.5}
            for g in ("p1_wind", "p1_wind_old", "p1_osw", "p1_pv") for t in (1, 2)]
    pd.DataFrame(rows).to_csv(folder / "variable_capacity_factors.csv", index=False)
    pd.DataFrame({"RPS_PROGRAM": ["ESR_NY_rps", "ESR_NY_ces", "ESR_MA_rps"], "LOAD_ZONE": ["p127"] * 3,
                  "PERIOD": [2035] * 3, "rps_share": [0.7, 0.84, 0.5]}).to_csv(folder / "rps_requirements.csv", index=False)
    pd.DataFrame([{"ic_retirement_reuse_share": 0.1}]).to_csv(folder / "ic_params.csv", index=False)
    pd.DataFrame({"INVESTMENT_PERIOD": [2028, 2030, 2035], "period_start": [2026, 2029, 2031],
                  "period_end": [2028, 2030, 2035]}).to_csv(folder / "periods.csv", index=False)


def test_case_steps(tmp_path):
    """The regression case's legacy settings (on_pgdays): ATB-only gas capex, NY RPS buyout only."""
    _case(tmp_path)
    s = _case_settings(axis_value="on_pgdays")
    s["_zone_map"] = {"p101": "AGG", "p103": "AGG"}
    s0prod.apply_settings({"c": {2035: s}})
    lines = s0prod.write_case_inputs(tmp_path, {2028: s, 2030: s, 2035: s})
    log = (tmp_path / "s0_production_log.txt").read_text()
    assert "wind loss" in log and "coal CF caps" in log and "rps acp" in log and "periods: 2028 = 2026-2028" in log
    # coal: p107 from the table, p10 (50 MW of history) at the fallback, the aggregate AGG weighted
    gi = pd.read_csv(tmp_path / "gen_info.csv", na_values=".").set_index("GENERATION_PROJECT")
    t = pd.read_csv(REPO / "s0_workflow/data/coal_cf_caps_eia923_2021_2024.csv", comment="#").set_index("ba")
    assert gi.at["p1_coal", "gen_max_annual_availability"] == pytest.approx(min(1, t.at["p107", "cap_cf"] / 0.9), abs=1e-6)
    assert gi.at["p2_coal", "gen_max_annual_availability"] == pytest.approx(0.65 / 0.9, abs=1e-6)
    agg = np.average(t.loc[["p101", "p103"], "cap_cf"], weights=t.loc[["p101", "p103"], "hist_mw"])
    assert gi.at["agg_coal", "gen_max_annual_availability"] == pytest.approx(min(1, agg / 0.9), abs=1e-6)
    assert np.isnan(gi.at["p1_pv", "gen_max_annual_availability"])
    # wind loss on onshore and offshore, new and existing; solar unchanged
    v = pd.read_csv(tmp_path / "variable_capacity_factors.csv").groupby("GENERATION_PROJECT").gen_max_capacity_factor.max()
    f = (1 - 0.134) / (1 - 0.017)
    assert v["p1_wind"] == pytest.approx(0.5 * f) and v["p1_wind_old"] == pytest.approx(0.5 * f)
    assert v["p1_osw"] == pytest.approx(0.5 * f) and v["p1_pv"] == 0.5
    # NY buyout on ESR_NY_rps only
    r = pd.read_csv(tmp_path / "rps_requirements.csv", dtype=str).set_index("RPS_PROGRAM")["rps_acp_per_mwh"]
    assert r["ESR_NY_rps"] == "45.39" and r["ESR_NY_ces"] == "." and r["ESR_MA_rps"] == "."
    assert pd.read_csv(tmp_path / "ic_params.csv").ic_slack_cost_per_mw.iat[0] == 5e7
    # gas capex: atb_moderate leaves the case's (ATB) costs alone
    assert (pd.read_csv(tmp_path / "gen_build_costs.csv").gen_overnight_cost.iloc[:6] == 1000).all()
    # retirement rule
    assert gi.at["p1_coal", "gen_can_retire_early"] == 1 and gi.at["p1_gas_old", "gen_can_retire_early"] == 1
    assert gi.at["p1_cc_new", "gen_can_retire_early"] == 0
    assert (tmp_path / "retirement_rules.csv").exists()
    assert len(lines) >= 6


def test_new_defaults_premium_buyouts_and_cap(tmp_path):
    _case(tmp_path)
    s = _case_settings(axis_value="on")
    s["build_rate"] = {"gas_turbine_cap": {"enabled": True, "form": "cumulative_in_service"}}
    s0prod.apply_settings({"c": {2035: s}})
    assert s["atb_modifiers"]["ngcc"]["capex_mw"] == ["mul", 1.0]                 # ATB Moderate basis
    assert s["build_rate"]["gas_turbine_cap"]["form"] == "cumulative_additions"
    assert s["build_rate"]["gas_turbine_cap"]["allowance_path"] == "central"
    # the coal spec needs PowerGenome's unit table (recorded by the hook during the real build): without it the
    # case build stops; the coal steps are tested in test_coal_spec.py
    with pytest.raises(RuntimeError, match="unit hook"):
        s0prod.write_case_inputs(tmp_path, {2028: s, 2030: s, 2035: s})
    _case(tmp_path)
    s["s0_production"]["coal_spec"]["enabled"] = False
    s["s0_production"]["lifetime_backstop"] = {"enabled": False}  # also needs the unit table (test_final_s0.py)
    s["s0_production"]["tx_policy"] = {"mode": "legacy"}  # national cap: needs the transmission files (test_tx_policy.py)
    s["s0_production"]["fuel_prices"] = {"mode": "hist5"}  # STEO -> AEO: needs fuel_cost.csv (test_fuel_prices.py)
    s["s0_production"]["prm"] = {"design": "legacy"}      # regional reserve: needs stress days (test_prm.py)
    s0prod.write_case_inputs(tmp_path, {2028: s, 2030: s, 2035: s})
    bc = pd.read_csv(tmp_path / "gen_build_costs.csv").set_index(["GENERATION_PROJECT", "build_year"])["gen_overnight_cost"]
    # periods 2028 = 2026-28, 2030 = 2029-30, 2035 = 2031-35; premium = mean over the in-service years
    assert bc[("p1_cc_new", 2028)] == pytest.approx(1000 * 1.37)
    assert bc[("p1_cc_new", 2030)] == pytest.approx(1000 * 1.37)
    assert bc[("p1_cc_new", 2035)] == pytest.approx(1000 * (1 + (0.37 + 0.28 + 0.18 + 0.09 + 0) / 5))
    assert bc[("p1_ct_new", 2035)] == pytest.approx(1000 * (1 + (0.45 + 0.34 + 0.23 + 0.11 + 0) / 5))
    assert bc[("p1_gas_old", 2005)] == 0.0                                       # predetermined untouched
    r = pd.read_csv(tmp_path / "rps_requirements.csv", dtype=str).set_index("RPS_PROGRAM")["rps_acp_per_mwh"]
    assert (r == "100.0").all()                                                  # NY RPS and CES included
    assert "flat $100/MWh on 3 state programs" in (tmp_path / "s0_production_log.txt").read_text()
    assert (tmp_path / "build_rules.csv").exists()                              # new-build rule on


def test_gas_premium_paths():
    pp = s0_yaml()["gas_capex"]["premium_paths"]
    f = s0prod.premium_fraction
    assert [f(pp["central"]["cc"], y) for y in (2025, 2031, 2032, 2033, 2034, 2035, 2045)] == [
        0.37, 0.37, 0.28, 0.18, 0.09, 0.0, 0.0]
    assert [f(pp["low"]["cc"], y) for y in (2033, 2034, 2035, 2036, 2037, 2038)] == [0.5, 0.4, 0.3, 0.2, 0.1, 0.0]
    assert [f(pp["low"]["ct"], y) for y in (2033, 2034, 2037, 2038)] == [0.45, 0.36, 0.09, 0.0]
    assert [f(pp["high"]["cc"], y) for y in (2029, 2030, 2031, 2032)] == [0.37, 0.25, 0.12, 0.0]
    assert [f(pp["high"]["ct"], y) for y in (2029, 2030, 2031, 2032)] == [0.45, 0.30, 0.15, 0.0]
    assert s0prod.premium_for_period(pp["central"]["cc"], 2031, 2035, "period_label") == 0.0
    assert s0prod.gas_capex_class("NaturalGas_CCCCSAvgCF_Moderate") is None       # CCS excluded
    assert s0prod.gas_capex_class("NaturalGas_Combustion Turbine (F-Frame)_Moderate") == "ct"
    # ATB-only and the GridLab override stay available
    for mode, capex in (("atb_moderate", ["mul", 1.0]), ("gridlab", 2061000)):
        cs = {"c": {2035: _case_settings()}}
        cs["c"][2035]["s0_production"]["gas_capex"]["mode"] = mode
        s0prod.apply_settings(cs)
        assert cs["c"][2035]["atb_modifiers"]["ngcc"]["capex_mw"] == capex


def _policy_settings():
    """The state-policy settings of a case as PowerGenome loads them from pg/settings."""
    rrt = yaml.safe_load(open(REPO / "pg/settings/regional_resource_tags.yml"))["regional_tag_values"]
    tags = yaml.safe_load(open(REPO / "pg/settings/resource_tags.yml"))["model_tag_names"]
    cols = yaml.safe_load(open(REPO / "pg/settings/model_definition.yml"))["generator_columns"]
    return {"emission_policies_fn": s0prod.CURRENT_POLICIES_FN, "regional_tag_values": rrt,
            "model_tag_names": tags, "generator_columns": cols}


def test_apply_state_policies():
    """New defaults take the policy files built from the pinned ReEDS release; legacy and other cases keep
    the current ones."""
    shared = _policy_settings()
    new = {**_case_settings(axis_value="on_pgdays_new"), **shared}
    leg = {**_case_settings(axis_value="on_pgdays"), **shared}
    other = dict(shared)
    snap = copy.deepcopy(shared)
    cs = {"new": {2035: new}, "leg": {2035: leg}, "other": {2035: other}}
    s0prod.apply_state_policies(cs)
    assert new["emission_policies_fn"] == "rggi_carbon/emission_policies_reeds_2026.09.21.csv"
    assert (REPO / "pg/extra_inputs" / new["emission_policies_fn"]).exists()
    assert leg == {**_case_settings(axis_value="on_pgdays"), **snap} and other == snap
    assert shared == snap                                         # nested settings shared by cases untouched
    doc = s0prod.state_policy_doc("2026.09.21")
    for r, progs in doc["regional_tag_values"].items():
        assert {p: v for p, v in new["regional_tag_values"][r].items() if p.startswith("ESR_")} == progs
    # non-ESR tags (offshore mandates, growth caps) are kept
    keep = {(r, p) for r, d in snap["regional_tag_values"].items() for p in (d or {}) if not p.startswith("ESR_")}
    assert keep and all(p in new["regional_tag_values"][r] for r, p in keep)
    assert sorted(t for t in new["model_tag_names"] if t.startswith("ESR_")) == doc["esr_tags"]
    # S0prod_A / S0prod_B (on, on_windows) take the release too
    for ax in ("on", "on_windows"):
        s = {**_case_settings(axis_value=ax), **_policy_settings()}
        s0prod.apply_state_policies({"c": {2035: s}})
        assert s["emission_policies_fn"].endswith("reeds_2026.09.21.csv")
    # a case on another policy file (e.g. the decarb policies) is not silently replaced
    with pytest.raises(ValueError, match="emission_policies_decarb"):
        s0prod.apply_state_policies({"c": {2035: {**_case_settings(axis_value="on"), **_policy_settings(),
                                                  "emission_policies_fn": "rggi_carbon/emission_policies_decarb.csv"}}})
    with pytest.raises(FileNotFoundError, match="build_reeds_state_policies"):
        s0prod.state_policy_doc("1999.01.01")
    src = (REPO / "pg_to_switch.py").read_text()
    assert src.index("s0prod.apply_state_policies(case_settings)") < src.index("_any_scope = any(")


def test_legacy_settings_build_as_before(tmp_path):
    """The regression case (on_pgdays) produces the same case files as the code and settings before the
    Oct 2026 changes (commit e03736e)."""
    import importlib.util
    import subprocess
    old_dir = tmp_path / "old_code"
    old_dir.mkdir()
    for f, dst in (("s0_workflow/production.py", "production_old.py"), ("pg/settings/s0_production.yml", "s0_old.yml"),
                   ("pg/settings/scenario_management.yml", "sm_old.yml")):
        (old_dir / dst).write_text(subprocess.run(["git", "show", f"e03736e:{f}"], cwd=REPO, capture_output=True,
                                                  text=True, check=True).stdout)
    spec = importlib.util.spec_from_file_location("production_old", old_dir / "production_old.py")
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    old.REPO = REPO                       # the old module resolves data paths from its own location
    res = yaml.safe_load(open(REPO / "pg/settings/resources.yml"))

    def settings(s0, ax):
        s = {"s0_production": copy.deepcopy(s0), "atb_modifiers": copy.deepcopy(res["atb_modifiers"]),
             "model_first_planning_year": 2031, "case_id": "c", **_policy_settings()}
        return s0prod.deep_merge(s, ax)
    old_s = settings(yaml.safe_load(open(old_dir / "s0_old.yml"))["s0_production"],
                     yaml.safe_load(open(old_dir / "sm_old.yml"))["settings_management"]["all_years"]["s0_production"]["on_pgdays"])
    new_s = settings(s0_yaml(), s0prod.deep_merge(axis()["on_pgdays"], regression_columns()))
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    _case(tmp_path / "old")
    _case(tmp_path / "new")
    old.apply_settings({"c": {2035: old_s}})
    before = copy.deepcopy(new_s)
    s0prod.apply_state_policies({"c": {2035: new_s}})
    assert new_s == before                                       # legacy: current state-policy files
    s0prod.apply_settings({"c": {2035: new_s}})
    assert old_s["atb_modifiers"] == new_s["atb_modifiers"]
    assert old_s.get("build_rate") == new_s.get("build_rate")            # no gas-turbine cap override
    assert "forced_tx_table" not in new_s and "forced_tx_expansion_limit" not in new_s   # transmission as before
    old.write_case_inputs(tmp_path / "old", {2035: old_s})
    s0prod.write_case_inputs(tmp_path / "new", {2035: new_s})
    for f in sorted(p.name for p in (tmp_path / "old").glob("*.csv")):
        assert (tmp_path / "old" / f).read_text() == (tmp_path / "new" / f).read_text(), f
    # and no new files: no build_rules.csv (no new-build rule), so the same set of case files
    assert sorted(p.name for p in (tmp_path / "new").glob("*.csv")) == \
        sorted(p.name for p in (tmp_path / "old").glob("*.csv"))
    assert "build_rules" not in s0prod.scenario_options(new_s)
    # planning reserve: legacy design (per-zone planning_reserves + the extreme-day script), as before
    assert "prm_regional" not in s0prod.scenario_options(new_s) and "exclude-module" not in s0prod.scenario_options(new_s)
    from s0_workflow import prm as s0prm
    assert s0prm.year_prm(new_s) is None and "model_adjustment_scripts" not in new_s


def test_coal_table_and_method():
    t = pd.read_csv(REPO / "s0_workflow/data/coal_cf_caps_eia923_2021_2024.csv", comment="#")
    assert list(t.columns) == ["ba", "hist_mw", "n_units", "cap_cf", "cap_cf_nameplate"]
    assert t.cap_cf.between(0, 1).all() and t.ba.str.match(r"^p\d+$").all()
    assert 100_000 < t.hist_mw.sum() < 200_000
    # method on a tiny synthetic set
    g = lambda caps: pd.DataFrame({"Plant Code": [1, 1, 2], "Generator ID": ["A", "B", "C"],
                                   "Energy Source 1": ["BIT", "BIT", "NG"], "Status": ["OP"] * 3,
                                   "Planned Retirement Year": [np.nan, 2030, np.nan], "Operating Year": [1980, 1990, 2000],
                                   "Winter Capacity (MW)": caps, "Nameplate Capacity (MW)": caps,
                                   "State": ["OH"] * 3, "County": ["Adams"] * 3})
    gens = {2023: g([100.0, 50.0, 10.0]), 2024: g([100.0, 50.0, 10.0])}
    gen = pd.DataFrame({"plant": [1, 1, 1], "gen": ["A", "A", "B"], "year": [2023, 2024, 2024],
                        "mwh": [0.5 * 100 * 8760, 0.7 * 100 * 8784, 1.0], "months": [12, 11, 12]})
    c2z = pd.DataFrame({"FIPS": ["39001"], "ba": ["p9"], "county_name": ["adams"], "state": ["OH"]})
    zc, units = coal_cf.zone_caps(gens, gens[2024], None, gen, c2z, (2023, 2024), 2035)
    assert list(units["Generator ID"]) == ["A"]                       # B retires before 2035; C is gas
    assert zc.set_index("ba").at["p9", "cap_cf"] == pytest.approx(0.5)   # 2024 has 11 months: skipped


# ------------------------------------------------------------------------------- scenario lines (6, 8)
def _scenario_files():
    src = (REPO / "pg_to_switch.py").read_text()
    tree = ast.parse(src)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("scenario_files", "model_folder_names")]

    class IC:
        @staticmethod
        def ic_settings(s):
            return s.get("interconnection_headroom") if (s.get("interconnection_headroom") or {}).get("enabled") else None
    g = {"collections": collections, "shlex": shlex, "ic_case": IC, "s0prod": s0prod, "Path": Path,
         "logger": logging.getLogger(), "short_fn": lambda f, t=None: f, "__file__": str(REPO / "pg_to_switch.py")}
    exec(compile(ast.Module(fns, []), "pg_to_switch", "exec"), g)
    return g["scenario_files"]


def test_scenario_lines_modes(tmp_path):
    sf = _scenario_files()
    years = [2028, 2030, 2035, 2040, 2045]
    for case, ax, mode in (("A", "on", "myopic"), ("B", "on_windows", "windows")):
        s = _case_settings(2035, ax)
        s0prod.apply_settings({case: {2035: s}})
        cs = {case: {y: s for y in years}}
        stages = s0prod.plan_stages(years, mode)
        sf(tmp_path, cs, False, {case: stages})
        lines = (tmp_path / f"scenarios_{case}.txt").read_text().splitlines()
        assert len(lines) == len(stages)
        for i, (st, ln) in enumerate(zip(stages, lines)):
            # parse the line rather than match text: pg_to_switch shlex-quotes the folders, and on
            # Windows str(Path) has backslashes, so the quoted form differs from "{tmp_path}/..."
            args = shlex.split(ln)
            assert Path(args[args.index("--inputs-dir") + 1]) == tmp_path / st["name"] / case
            assert "--include-module study_modules.gen_amortization_period" in ln
            assert "--include-module study_modules.retirement_rules" in ln
            assert ("study_modules.prepare_next_stage" in ln) == (st["next"] is not None)
            if i:
                al = ln.split("--input-aliases ")[1].split(" ")
                assert f"gen_build_predetermined.csv=gen_build_predetermined.chained.{case}.csv" in al
                assert f"ic_zones.csv=ic_zones.chained.{case}.csv" in al
                assert f"build_rate_prev_build.csv=build_rate_prev_build.chained.{case}.csv" in al
                assert ln.count("--input-aliases") == 1
            else:
                assert "--input-aliases" not in ln
    # legacy cases: unchanged lines (no S0 modules)
    sf(tmp_path, {"L": {2035: {}}}, False, {"L": [dict(st, legacy=True) for st in s0prod.plan_stages([2035], "single")]})
    ln = (tmp_path / "scenarios_L_foresight.txt").read_text()
    assert "--scenario-name L_foresight" in ln and "gen_amortization_period" not in ln


# ----------------------------------------------------------------------------- day selection (1)
def _record(days=120, seed=1):
    rng = np.random.default_rng(seed)
    H = days * 24
    h = np.arange(H)
    season = np.sin(2 * np.pi * h / H)
    zones = ["p1", "p2", "p60", "p61"]          # NorthernGrid, PJM-ish: two transregs
    L = pd.DataFrame({z: 1000 + 300 * season + 200 * np.sin(2 * np.pi * h / 24) + rng.normal(0, 40, H) for z in zones})
    sol = np.clip(np.sin(2 * np.pi * (h % 24 - 6) / 24), 0, None) * (0.8 + 0.2 * season)
    R = pd.DataFrame({"w1": np.clip(0.4 + 0.2 * rng.standard_normal(H), 0, 1),
                      "w2": np.clip(0.35 - 0.1 * season + 0.15 * rng.standard_normal(H), 0, 1),
                      "s1": sol, "s2": sol * 0.9, "gas": np.ones(H)})
    gens = pd.DataFrame({"Resource": ["w1", "w2", "s1", "s2", "gas"],
                         "technology": ["LandbasedWind_Class3_Moderate", "Onshore Wind Turbine", "UtilityPV_Class1_Moderate",
                                        "Solar Photovoltaic", "Natural Gas Fired Combined Cycle"],
                         "region": ["p1", "p60", "p2", "p61", "p1"]})
    return L, R, gens


def test_day_selection_targets_and_tails(tmp_path):
    try:
        import sklearn  # noqa: F401  (day_selection.select_days uses sklearn's KMeans)
    except ImportError:
        pytest.skip("scikit-learn is not installed in this env; the fleet-independent day selector needs it. "
                    "Run this test in an env with both scikit-learn and pytest.")
    L, R, gens = _record()
    s = _case_settings()
    ts = day_selection.ts_settings(s)
    assert ts is not None and ts["wind_factor"] == pytest.approx((1 - 0.134) / (1 - 0.017))
    ts.update(n_days=12, n_top_load_days=2, n_low_net_load_days=2, kmeans_n_init=5)
    res, rep, w = day_selection.select_days(R, L, gens, ts, tmp_path / "diag")
    assert len(rep) == 13 and sum(w) == pytest.approx(365.0) and min(w[:-1]) >= 1.0 - 1e-9
    assert rep.slot.str.match(r"^p\d+$").all() and len(res["load_profiles"]) == 13 * 24
    err = pd.read_csv(tmp_path / "diag/fi_target_errors.csv")
    assert err.within.all() and {"load national", "wind CF national", "solar CF national"} <= set(err.target)
    info = yaml.safe_load(open(tmp_path / "diag/fi_info.json"))
    tails = pd.read_csv(tmp_path / "diag/fi_tail_shares.csv")
    lo, hi = tails["band %"].str.split("-", expand=True).astype(float).T.values
    assert ((tails["sample share %"] >= lo - 1e-6) & (tails["sample share %"] <= hi + 1e-6)).all()
    assert info["fleets"] == ["stylised"]
    # the selection uses no fleet: the same record gives the same days whatever was built before
    res2, rep2, w2 = day_selection.select_days(R, L, gens, ts, tmp_path / "diag2")
    assert list(rep2.slot) == list(rep.slot) and np.allclose(w2, w)
    # the PowerGenome method is the default outside S0 production and for on_pgdays
    assert day_selection.ts_settings({}) is None
    assert day_selection.ts_settings(_case_settings(axis_value="on_pgdays")) is None
    with pytest.raises(ValueError, match="whole days"):
        day_selection.select_days(R.iloc[:-1], L.iloc[:-1], gens, ts, tmp_path / "bad")


def test_day_selection_hooked_into_pg_to_switch():
    src = (REPO / "pg_to_switch.py").read_text()
    i = src.index("def operational_files(")
    body = src[i:src.index("\ndef ", i + 10)]
    assert "s0days.ts_settings(year_settings)" in body and "s0days.select_days(" in body
    assert body.index("for model_year, year_settings in scen_settings_dict.items()") < body.index("s0days.select_days(")


# ------------------------------------------------------------------------------ regression tooling (9)
def test_compare_script(tmp_path):
    import subprocess
    for name, scale in (("new", 1.0), ("old", 1.004)):
        d = tmp_path / name
        d.mkdir()
        pd.DataFrame({"generation_project": ["c1", "g1", "w1"], "gen_tech": ["Conventional Steam Coal",
                      "NaturalGas_1-on-1 Combined Cycle (H-Frame)_Moderate", "LandbasedWind_Class3_Moderate_1"],
                      "gen_energy_source": ["coal", "naturalgas", "wind"], "period": [2035] * 3,
                      "Energy_GWh_typical_yr": [441000.0 * scale, 1000.0, 2000.0],
                      "DispatchEmissions_tCO2_per_typical_yr": [1.2e9 * scale, 2.6e7, 0.0],
                      "GenCapacity_MW": [77900.0, 5000.0, 9000.0]}).to_csv(d / "dispatch_gen_annual_summary.csv", index=False)
        pd.DataFrame({"GEN_BLD_YRS_1": ["g1", "w1", "c1"], "GEN_BLD_YRS_2": [2035, 2035, 1980],
                      "BuildGen": [5000.0, 9000.0 * scale, 77900.0]}).to_csv(d / "BuildGen.csv", index=False)
        pd.DataFrame({"a": ["x", "y"], "v": [1.0, 2.0 * scale]}).to_csv(d / "t.csv", index=False)
    r = subprocess.run([sys.executable, str(REPO / "s0_workflow/scripts/compare_s0_runs.py"), "outputs",
                        str(tmp_path / "new"), str(tmp_path / "old")], capture_output=True, text=True)
    assert r.returncode == 0 and "OVERALL PASS" in r.stdout and "new_cc_gw" in r.stdout
    r = subprocess.run([sys.executable, str(REPO / "s0_workflow/scripts/compare_s0_runs.py"), "inputs",
                        str(tmp_path / "new"), str(tmp_path / "old")], capture_output=True, text=True)
    assert "numeric differences" in r.stdout


# ----------------------------------------------------------------------------------- §81 one column per setting
def test_committed_rows_set_each_setting_in_one_place(tmp_path):
    """Every committed scenario_inputs row resolves with no setting set by two columns (the case build refuses one:
    the regression row did, with prm.design / tx_policy.mode in on_pgdays and in its prm_design / tx_bill columns).
    The checker finds a same-key and a block-vs-key conflict."""
    from s0_workflow import chain_reuse as cr
    si, sm = REPO / "pg/extra_inputs/scenario_inputs.csv", REPO / "pg/settings/scenario_management.yml"
    found = cr.setting_conflicts(si, sm)
    assert found.empty, found.head(10).to_string()
    rows = pd.DataFrame([{"case_id": "x", "year": 2035, "a": "v", "b": "w", "c": "u"}])
    rows.to_csv(tmp_path / "si.csv", index=False)
    (tmp_path / "sm.yml").write_text(yaml.safe_dump({"settings_management": {"all_years": {
        "a": {"v": {"s0_production": {"prm": {"design": "legacy"}}}},
        "b": {"w": {"s0_production": {"prm": {"design": "regional", "reserve_rows": "compact"}}}},
        "c": {"u": {"s0_production": {"tx_policy": {"mode": "legacy"}}}}},
        2035: {"c": {"u": {"s0_production": {"tx_policy": {"cap_tw_mi_per_yr": 1.0}}}}}}}))   # same column: fine
    f = cr.setting_conflicts(tmp_path / "si.csv", tmp_path / "sm.yml")
    assert list(f.path) == ["s0_production.prm.design"] and f["columns"].iat[0] == "a, b"
    (tmp_path / "sm.yml").write_text(yaml.safe_dump({"settings_management": {"all_years": {
        "a": {"v": {"s0_production": {"prm": "legacy"}}}, "b": {"w": {"s0_production": {"prm": {"design": "x"}}}}}}}))
    assert list(cr.setting_conflicts(tmp_path / "si.csv", tmp_path / "sm.yml").path) == ["s0_production.prm"]


def test_regression_row_resolves_as_before_the_one_column_fix():
    """§81: the regression row (s4x1_S0prod_2035) resolves to exactly the settings it had at 9e2045b (before prm.design
    and tx_policy.mode moved out of on_pgdays into its prm_design / tx_bill columns, both legacy)."""
    import subprocess
    import tempfile
    from s0_workflow import chain_reuse as cr
    old = {}
    with tempfile.TemporaryDirectory() as d:
        for f in ("pg/extra_inputs/scenario_inputs.csv", "pg/settings/scenario_management.yml",
                  "pg/settings/s0_production.yml"):
            p = Path(d) / Path(f).name
            p.write_text(subprocess.run(["git", "show", f"9e2045b:{f}"], cwd=REPO, capture_output=True, text=True,
                                        check=True).stdout)
            old[Path(f).name] = p

        def resolve(si_path, sm_path, s0_path):
            si = pd.read_csv(si_path)
            sm = yaml.safe_load(open(sm_path))["settings_management"]
            base = yaml.safe_load(open(s0_path))["s0_production"]
            row = si[si.case_id == "s4x1_S0prod_2035"].iloc[0]
            cols = [c for c in si.columns if c not in ("case_id", "year")]
            return cr._at_year(cr._merged(sm["all_years"], sm.get(2035, {}), row, cols, base, 2035), 2035)
        before = resolve(old["scenario_inputs.csv"], old["scenario_management.yml"], old["s0_production.yml"])
    now = resolve(REPO / "pg/extra_inputs/scenario_inputs.csv", REPO / "pg/settings/scenario_management.yml",
                  REPO / "pg/settings/s0_production.yml")
    assert cr._diff_paths(now, before) == []
    assert now["s0_production"]["prm"]["design"] == "legacy" and now["s0_production"]["tx_policy"]["mode"] == "legacy"
    on_pgdays = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))[
        "settings_management"]["all_years"]["s0_production"]["on_pgdays"]["s0_production"]
    assert "design" not in (on_pgdays.get("prm") or {}) and "mode" not in (on_pgdays.get("tx_policy") or {})
