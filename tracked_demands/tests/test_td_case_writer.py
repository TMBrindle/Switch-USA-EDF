"""Case-build writer for tracked-demand inputs (§108 (g)): on the toy, with stress days, a tax-credit table and a
carbon programme, then a solve of the written case."""
from __future__ import annotations

import sys

import pandas as pd
import pytest

from td_toy import REPO, add_stress_timeseries, carbon_cap, toyutil

SPEC = {
    "tracked_demands": [{
        "name": "DC1", "zone": "North", "max_mw": 2.0, "min_mw": 2.0, "annual_energy_share": 0.9,
        "connection_mw": 2.0, "export_limit_share": 0,
        "matching": {"mode": "hourly", "target": 0.9, "penalty": 1000},
        "onsite_techs": [{"name": "solar_onsite", "energy_source": "solar", "capital_cost_per_mw_yr": 90000,
                          "clean": True, "tax_credit_key": "Central_PV"}],
        "storage": {"power_cost_per_mw_yr": 30000, "energy_cost_per_mwh_yr": 20000},
        "onsite_gens": [{"name": "N-DC1-recip", "prime_mover": "recip", "fuel": "gas", "heat_rate": 8.3,
                         "overnight_cost_per_kw": 900, "fixed_om_per_kw_yr": 5, "variable_om": 5, "unit_mw": 2.0,
                         "capacity_limit_mw": 4.0}],
    }],
    "zone_upgrade_cost_per_kw": {"North": 200.0},
    "cap_coverage": {"RGGI": "RGGI"},
}


@pytest.fixture
def case(tmp_path):
    from tracked_demands.case_writer import write_case_inputs

    def edit(inp):
        add_stress_timeseries(inp)
        carbon_cap(inp)
        pd.DataFrame([{"GENERATION_PROJECT": "N-Central_PV-1", "PERIOD": 2020, "gen_ptc_value_per_mwh": 27.5},
                      {"GENERATION_PROJECT": "N-Central_PV-1", "PERIOD": 2030, "gen_ptc_value_per_mwh": 0.0}]).to_csv(
            inp / "gen_tax_credits.csv", index=False)
    run = toyutil.toy_inputs(tmp_path, "w", modules=("carbon_policies_regional", "tracked_demands"), edit=edit)
    out = write_case_inputs(run / "inputs", SPEC, run / "inputs_td")
    return run, out


def test_time_blocks_leave_out_stress_hours(case):
    run, out = case
    tp = pd.read_csv(out / "timepoints.csv")
    stress = set(tp.loc[tp["timeseries"] == "2020_stress", "timepoint_id"])
    blocks = pd.read_csv(out / "tracked_demand_time_blocks.csv")
    assert stress and not (set(blocks["TIMEPOINT"]) & stress)
    assert set(blocks["TIMEPOINT"]) == set(tp["timepoint_id"]) - stress
    t = pd.read_csv(out / "tracked_demand_cfe_targets.csv")
    assert len(t) == len(blocks) and (t["td_cfe_target"] == 0.9).all()


def test_names_as_the_case_uses_them(case):
    run, out = case
    techs = pd.read_csv(out / "tracked_demand_onsite_techs.csv", na_values=".")
    assert techs.set_index("td_onsite_tech").at["solar_onsite", "td_onsite_tech_cf_source"] == "Solar"
    gi = pd.read_csv(out / "gen_info.csv", na_values=".").set_index("GENERATION_PROJECT")
    assert gi.at["N-DC1-recip", "gen_energy_source"] == "NaturalGas"
    assert pd.isna(gi.at["N-DC1-recip", "gen_unit_size"])
    bc = pd.read_csv(out / "gen_build_costs.csv")
    assert set(bc.loc[bc["GENERATION_PROJECT"] == "N-DC1-recip", "build_year"]) == {2020, 2030}


def test_tax_credits_from_the_case(case):
    run, out = case
    sub = pd.read_csv(out / "tracked_demand_onsite_tech_subsidies.csv").set_index("PERIOD")
    assert sub.at[2020, "td_onsite_tech_subsidy_by_period"] == 27.5
    assert sub.at[2030, "td_onsite_tech_subsidy_by_period"] == 0.0


def test_tax_credit_override(tmp_path):
    from tracked_demands.case_writer import write_case_inputs
    run = toyutil.toy_inputs(tmp_path, "o", modules=("tracked_demands",))
    spec = dict(SPEC, cap_coverage={}, tax_credit_values={2020: {"Central_PV": 10.0}, 2030: {"Central_PV": 5.0}})
    out = write_case_inputs(run / "inputs", spec, run / "inputs_td")
    sub = pd.read_csv(out / "tracked_demand_onsite_tech_subsidies.csv").set_index("PERIOD")
    assert sub.at[2020, "td_onsite_tech_subsidy_by_period"] == 10.0
    assert sub.at[2030, "td_onsite_tech_subsidy_by_period"] == 5.0


def test_coverage_rules_and_original_untouched(case):
    run, out = case
    cov = pd.read_csv(out / "tracked_demand_cap_coverage.csv").iloc[0]
    # the limited-export exemption is off unless the case sets it (VM runner, 12 Oct)
    assert cov["td_cap_threshold_mw"] == 25 and cov["td_cap_limited_export_exemption"] == 0
    states = pd.read_csv(out / "tracked_demand_cap_coverage_states.csv").iloc[0]
    assert states["STATE"] == "NY" and states["td_cap_state_threshold_mw"] == 15
    assert not (run / "inputs" / "tracked_demands.csv").exists()
    assert "N-DC1-recip" not in pd.read_csv(run / "inputs" / "gen_info.csv")["GENERATION_PROJECT"].values


def test_written_case_solves(case):
    run, out = case
    toyutil.solve(run, inputs="inputs_td", outputs="outputs_td")
    d = pd.read_csv(run / "outputs_td" / "tracked_demand_cfe_detail.csv")
    assert len(d) and d["shortfall_MWh"].max() < 1e-3
    e = pd.read_csv(run / "outputs_td" / "tracked_demand_onsite_emissions.csv")
    assert set(e["co2_program"].dropna()) == {"RGGI"}
    c = pd.read_csv(run / "outputs_td" / "tracked_demand_connection_costs.csv")
    assert (c["cost_per_kw_overnight"] == 200.0).all()


def test_example_spec_parses():
    import yaml
    spec = yaml.safe_load((REPO / "tracked_demands/specs/dc_1gw_example.yml").read_text())
    assert spec["tracked_demands"][0]["max_mw"] == 1000
    rules = yaml.safe_load((REPO / "tracked_demands/data/cap_coverage_rules.yml").read_text())
    assert {"RGGI", "VA", "CA", "WA"} <= set(rules)


# ------------------------------------------------------------------------------------------------ VM-runner fixes (12 Oct)
def _gen(name, **kw):
    g = {"name": name, "fuel": "gas", "heat_rate": 8.5, "overnight_cost_per_kw": 900, "fixed_om_per_kw_yr": 5,
         "variable_om": 5, "unit_mw": 2.0, "capacity_limit_mw": 4.0}
    g.update(kw)
    return g


def _spec(gens, **extra):
    return {"tracked_demands": [{"name": "DC1", "zone": "North", "max_mw": 2.0, "min_mw": 2.0,
                                 "annual_energy_share": 0.9, "connection_mw": 0.5, "export_limit_share": 0,
                                 "storage": {"power_cost_per_mw_yr": 30000, "energy_cost_per_mwh_yr": 20000},
                                 "onsite_gens": gens}],
            "zone_upgrade_cost_per_kw": {"North": 200.0}, **extra}


def test_build_year_column_as_the_case_names_it(tmp_path):
    """pg_to_switch writes BUILD_YEAR; the writer fills whatever the case's second column is called."""
    from tracked_demands.case_writer import write_case_inputs

    def upper(inp):
        bc = pd.read_csv(inp / "gen_build_costs.csv").rename(columns={"build_year": "BUILD_YEAR"})
        bc.to_csv(inp / "gen_build_costs.csv", index=False)
    run = toyutil.toy_inputs(tmp_path, "by", modules=("tracked_demands",), edit=upper)
    out = write_case_inputs(run / "inputs", _spec([_gen("N-DC1-recip", technology="recip")]), run / "inputs_td")
    bc = pd.read_csv(out / "gen_build_costs.csv")
    assert list(bc.columns[:2]) == ["GENERATION_PROJECT", "BUILD_YEAR"] and "build_year" not in bc.columns
    mine = bc[bc["GENERATION_PROJECT"] == "N-DC1-recip"]
    assert sorted(mine["BUILD_YEAR"]) == [2020, 2030]
    toyutil.solve(run, inputs="inputs_td", outputs="outputs_td")
    built = pd.read_csv(run / "outputs_td" / "BuildGen.csv")
    assert (built.iloc[:, 0] == "N-DC1-recip").any()


def test_uc_from_spec_else_defaults_with_warning(tmp_path, capsys):
    from tracked_demands.case_writer import write_case_inputs
    run = toyutil.toy_inputs(tmp_path, "uc", modules=("tracked_demands",))
    gens = [_gen("N-DC1-recip", technology="recip",
                 uc={"min_load_fraction": 0.4, "min_uptime": 2, "min_downtime": 2, "startup_om": 10,
                     "startup_fuel": 0.5, "ramp_limit_up": 1, "ramp_limit_down": 1}),
            _gen("N-DC1-aero", prime_mover="turbine")]
    out = write_case_inputs(run / "inputs", _spec(gens), run / "inputs_td")
    gi = pd.read_csv(out / "gen_info.csv", na_values=".").set_index("GENERATION_PROJECT")
    assert gi.at["N-DC1-recip", "gen_min_load_fraction"] == 0.4 and gi.at["N-DC1-recip", "gen_min_uptime"] == 2
    # the turbine: technology assumed aero (warned), defaults from onsite_uc_defaults.yml (warned)
    assert gi.at["N-DC1-aero", "gen_min_load_fraction"] == 0.3 and gi.at["N-DC1-aero", "gen_min_uptime"] == 1
    assert pd.isna(gi.at["N-DC1-aero", "gen_startup_om"])
    log = (out / "tracked_demands_writer_log.txt").read_text()
    assert "WARNING: N-DC1-aero: no technology given for a turbine; assumed aero" in log
    assert "WARNING: N-DC1-aero: unit commitment not in the spec" in log and "startup_om unset" in log
    assert "WARNING: N-DC1-recip" not in log
    # nothing copied from the zone's gas plant: the existing N-NG_GT keeps its own values, the new ones don't share them
    links = pd.read_csv(out / "tracked_demand_onsite_gens.csv").set_index("GENERATION_PROJECT")
    assert links.at["N-DC1-aero", "td_onsite_prime_mover"] == "turbine"


def test_limited_export_exemption_only_when_the_case_sets_it(tmp_path):
    from tracked_demands.case_writer import write_case_inputs
    run = toyutil.toy_inputs(tmp_path, "le", modules=("tracked_demands",), edit=lambda i: carbon_cap(i))
    off = write_case_inputs(run / "inputs", _spec([_gen("g1")], cap_coverage={"RGGI": "RGGI"}), run / "a")
    on = write_case_inputs(run / "inputs", _spec([_gen("g1")], cap_coverage={
        "RGGI": {"family": "RGGI", "limited_export_exemption": 1}}), run / "b")
    assert pd.read_csv(off / "tracked_demand_cap_coverage.csv")["td_cap_limited_export_exemption"].iat[0] == 0
    assert pd.read_csv(on / "tracked_demand_cap_coverage.csv")["td_cap_limited_export_exemption"].iat[0] == 1


def test_writer_on_a_chained_stage(tmp_path):
    """The writer on a copy of a chained stage (2030 after 2020): it edits gen_info, gen_build_costs and the other
    files in their chained versions too (BUILD_YEAR as in S0 cases), and the stage's solve, reading the chained
    files through --input-aliases as pg_to_switch writes them, sees every linked generator, its costs, its
    unit-commitment settings and its cap coverage."""
    import os
    import subprocess
    from s0_workflow import production as s0prod
    from tracked_demands.case_writer import write_case_inputs
    from td_toy import subset_td_files

    def edit(inp):
        carbon_cap(inp)
        pd.DataFrame([{"GENERATION_PROJECT": "N-NG_GT", "prm_credit": "capacity", "prm_class": "gas"}]).to_csv(
            inp / "prm_gen_credit.csv", index=False)
    mods = ("carbon_policies_regional", "tracked_demands")
    run = toyutil.toy_inputs(tmp_path, "ch", modules=mods, edit=edit)
    stages = s0prod.plan_stages([2020, 2030], "myopic")
    for st in stages:
        d = run / "in" / st["name"] / "case"
        d.parent.mkdir(parents=True, exist_ok=True)
        toyutil.subset_periods(run / "inputs", d, st["years"])
        subset_td_files(d, st["years"])
        s0prod.write_stage_info(d, st)
        # S0 stage folders name the build-year column BUILD_YEAR (pg_to_switch.gen_build_costs_file)
        bc = pd.read_csv(d / "gen_build_costs.csv").rename(columns={"build_year": "BUILD_YEAR"})
        bc.to_csv(d / "gen_build_costs.csv", index=False)
    (run / "mods/prepare_next_stage.py").write_text(
        (REPO / "switch/study_modules/prepare_next_stage.py").read_text())
    st_extra = ["--include-module", "switch_model.generators.extensions.storage"]
    toyutil.solve(run, "in/2020/case", "out/2020/case",
                  st_extra + ["--include-module", "mods.carbon_policies_regional", "--include-module",
                              "mods.tracked_demands", "--include-module", "mods.prepare_next_stage"])
    stage2 = run / "in/2030/case"
    assert (stage2 / "gen_build_costs.chained.case.csv").exists()

    gens = [_gen("N-DC1-recip", technology="recip", unit_mw=2.0),
            _gen("N-DC1-aero", technology="aero", unit_mw=30.0, rated_mw=30.0, capacity_limit_mw=30.0,
                 uc={"min_load_fraction": 0.25, "min_uptime": 1, "min_downtime": 1})]
    spec = _spec(gens, cap_coverage={"RGGI": "RGGI"})
    out = write_case_inputs(stage2, spec, run / "in/2030/case_td")
    # the chained build costs (what the stage reads) carry the new generators, with BUILD_YEAR
    ch = pd.read_csv(out / "gen_build_costs.chained.case.csv")
    assert list(ch.columns[:2]) == ["GENERATION_PROJECT", "BUILD_YEAR"]
    assert set(ch.loc[ch["GENERATION_PROJECT"].isin(["N-DC1-recip", "N-DC1-aero"]), "BUILD_YEAR"]) == {2030}
    assert set(pd.read_csv(out / "prm_gen_credit.csv")["GENERATION_PROJECT"]) >= {"N-DC1-recip", "N-DC1-aero"}

    # unit commitment: the stage's module list with commitment instead of no_commit
    ml = (out / "modules.txt").read_text().replace("switch_model.generators.core.no_commit",
                                                     "switch_model.generators.core.commit.operate\n"
                                                     "switch_model.generators.core.commit.fuel_use")
    (out / "modules.txt").write_text(ml)
    aliases = ["--input-aliases"] + [f"{f}.csv={f}.chained.case.csv" for f in
                                     ("gen_build_predetermined", "gen_build_costs", "transmission_lines")]
    args = st_extra + ["--include-module", "mods.carbon_policies_regional", "--include-module",
                       "mods.tracked_demands"] + aliases

    # what the solve sees: build the stage's model as switch would and read its parameters
    probe = run / "probe.py"
    probe.write_text(
        "import sys, json\n"
        "from switch_model.utilities import create_model\n"
        "from switch_model.solve import get_module_list\n"
        "args = sys.argv[1:]\n"
        "m = create_model(get_module_list(args), args=args)\n"
        "i = m.load_inputs()\n"
        "out = {g: {'cost': i.gen_overnight_cost[g, 2030], 'fixed_om': i.gen_fixed_om[g, 2030],\n"
        "           'min_load': i.gen_min_load_fraction[g], 'uptime': i.gen_min_uptime[g],\n"
        "           'downtime': i.gen_min_downtime[g], 'zone': i.gen_load_zone[g],\n"
        "           'linked': [td for td, g2 in i.TD_ONSITE_GENS if g2 == g]}\n"
        "       for g in ('N-DC1-recip', 'N-DC1-aero')}\n"
        "print(json.dumps(out))\n")
    r = subprocess.run([sys.executable, str(probe), "--inputs-dir", "in/2030/case_td", *args], cwd=run,
                       capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(run)})
    assert r.returncode == 0, r.stderr[-3000:]
    import json
    seen = json.loads(r.stdout.strip().splitlines()[-1])
    assert seen["N-DC1-recip"]["cost"] == 900000.0 and seen["N-DC1-recip"]["fixed_om"] == 5000.0
    assert seen["N-DC1-recip"]["min_load"] == 0.3 and seen["N-DC1-recip"]["uptime"] == 0     # recip defaults
    assert seen["N-DC1-aero"]["min_load"] == 0.25 and seen["N-DC1-aero"]["uptime"] == 1      # from the spec
    assert seen["N-DC1-aero"]["linked"] == ["DC1"] and seen["N-DC1-recip"]["linked"] == ["DC1"]

    # and the solve: both built in 2030 (0.5 MW connection for a 2 MW load), coverage by unit size
    toyutil.solve(run, "in/2030/case_td", "out/2030/case_td", args)
    o = run / "out/2030/case_td"
    b = pd.read_csv(o / "BuildGen.csv")
    b = b[b.iloc[:, 0].isin(["N-DC1-recip", "N-DC1-aero"])]
    assert set(b.iloc[:, 1]) == {2030} and b.iloc[:, -1].sum() > 1.0
    e = pd.read_csv(o / "tracked_demand_onsite_emissions.csv").set_index("generation_project")
    assert e.at["N-DC1-recip", "covered_by_cap"] == 0          # 2 MW unit < 25 MWe
    assert e.at["N-DC1-aero", "covered_by_cap"] == 1           # 30 MW unit >= 25 MWe; export 0 but no exemption
    assert "below_mw_threshold" in e.at["N-DC1-recip", "coverage_reason"]


# ------------------------------------------------------------------------------------------------ gas-turbine cap
GTC_GENS = [_gen("g_frame", technology="frame_ct"), _gen("g_cc", technology="cc"), _gen("g_aero", technology="aero"),
            _gen("g_titan", technology="industrial_ct"), _gen("g_recip", technology="recip"),
            _gen("g_diesel", technology="diesel", fuel="gas"), _gen("g_fc", technology="fuel_cell")]


def _gtc_case(tmp_path, name, form="cumulative_in_service", case_classes=("combustion_turbine",)):
    def edit(inp):
        pd.DataFrame([{"PERIOD": p, "gtc_max_mw": 1e6} for p in (2020, 2030)]).to_csv(
            inp / "gas_turbine_cap.csv", index=False)
        pd.DataFrame([{"GENERATION_PROJECT": "N-NG_GT", "gtc_class": c, "gtc_weight": 1.0}
                      for c in case_classes]).to_csv(inp / "gas_turbine_cap_gens.csv", index=False)
        pd.DataFrame([{"gtc_form": form, "gtc_retirements_free_room": 0, "gtc_since_year": 2025}]).to_csv(
            inp / "gas_turbine_cap_params.csv", index=False)
    return toyutil.toy_inputs(tmp_path, name, modules=("build_rate", "tracked_demands"), edit=edit)


@pytest.mark.parametrize("coverage,capped", [
    (["combined_cycle", "combustion_turbine"], {"g_frame": 1.0, "g_cc": 1.0}),
    (["combined_cycle", "combustion_turbine", "aeroderivative"],
     {"g_frame": 1.0, "g_cc": 1.0, "g_aero": 1.0, "g_titan": 1.0}),
])
def test_gas_turbine_cap_follows_the_case_coverage(tmp_path, coverage, capped):
    """frame CT -> combustion_turbine, CC -> combined_cycle, aero and industrial CT -> aeroderivative, recip ->
    reciprocating_engine, diesel and fuel cells never; a linked generator joins only if its category is covered."""
    from tracked_demands.case_writer import write_case_inputs
    run = _gtc_case(tmp_path, f"gtc{len(coverage)}")
    out = write_case_inputs(run / "inputs", _spec(GTC_GENS, gas_turbine_cap={"coverage": coverage}),
                            run / "inputs_td")
    g = pd.read_csv(out / "gas_turbine_cap_gens.csv").set_index("GENERATION_PROJECT")
    mine = g.drop("N-NG_GT")
    assert mine["gtc_weight"].to_dict() == capped
    assert mine.at["g_frame", "gtc_class"] == "combustion_turbine" and mine.at["g_cc", "gtc_class"] == "combined_cycle"
    links = pd.read_csv(out / "tracked_demand_onsite_gens.csv").set_index("GENERATION_PROJECT")["td_onsite_gtc_class"]
    assert links.to_dict() == {"g_frame": "combustion_turbine", "g_cc": "combined_cycle", "g_aero": "aeroderivative",
                               "g_titan": "aeroderivative", "g_recip": "reciprocating_engine", "g_diesel": "none",
                               "g_fc": "none"}
    assert list(pd.read_csv(out / "tracked_demand_gtc_coverage.csv")["gtc_class"]) == coverage
    # the module's run-start check accepts the written case
    toyutil.solve(run, inputs="inputs_td", outputs="outputs_td")
    assert (run / "outputs_td" / "gas_turbine_cap_results.csv").exists()


def test_gas_turbine_cap_s0_production_form(tmp_path):
    """An S0 production case (form cumulative_additions, read from the case) takes build_rate.yml's
    allowance_coverage, which includes aeroderivative, and its class weight x nameplate factor."""
    from tracked_demands.case_writer import write_case_inputs
    run = _gtc_case(tmp_path, "gtcprod", form="cumulative_additions")
    out = write_case_inputs(run / "inputs", _spec(GTC_GENS), run / "inputs_td")
    w = pd.read_csv(out / "gas_turbine_cap_gens.csv").set_index("GENERATION_PROJECT")["gtc_weight"].drop("N-NG_GT")
    assert w.to_dict() == pytest.approx({"g_frame": 1.090, "g_cc": 0.65 * 1.049, "g_aero": 1.090, "g_titan": 1.090})


def test_gas_turbine_cap_settings_must_match_the_case(tmp_path):
    from tracked_demands.case_writer import write_case_inputs
    run = _gtc_case(tmp_path, "gtcbad", case_classes=("aeroderivative",))
    with pytest.raises(ValueError, match=r"outside the coverage"):
        write_case_inputs(run / "inputs", _spec(GTC_GENS[:1]), run / "inputs_td")


# ------------------------------------------------------------------------------------------------ gas capex premium
CENTRAL_LOG = ("gas capex: ATB Moderate + central premium (span_mean): CC 2028 +37.0%, CC 2035 +18.4%, "
               "CT 2028 +45.0%, CT 2035 +22.6%")
PREMIUM_GENS = [_gen("g_aero", technology="aero"), _gen("g_titan", technology="industrial_ct"),
                _gen("g_frame", technology="frame_ct"), _gen("g_cc", technology="cc"),
                _gen("g_recip", technology="recip"), _gen("g_fc", technology="fuel_cell"),
                _gen("g_recip_ct", technology="recip", premium_class="ct"),      # a recip premium sensitivity
                _gen("g_recip_10", technology="recip", premium=0.1)]


def _s0_case(tmp_path, name, log_line):
    """The toy with S0's 2028 (2026-28) and 2035 (2031-35) periods and the case build's gas-capex log line."""
    def edit(inp):
        pd.DataFrame({"INVESTMENT_PERIOD": [2028, 2035], "period_start": [2026, 2031],
                      "period_end": [2028, 2035]}).to_csv(inp / "periods.csv", index=False)
        if log_line:
            (inp / "s0_production_log.txt").write_text(f"\n2026-10-12T10:00:00\ncase S0prod_A\n{log_line}\n")
    return toyutil.toy_inputs(tmp_path, name, modules=("tracked_demands",), edit=edit)


def _capex(out):
    bc = pd.read_csv(out / "gen_build_costs.csv")
    bc = bc[bc["GENERATION_PROJECT"].str.startswith("g_")]
    return {(g, int(y)): c / 900e3 for g, y, c in zip(bc["GENERATION_PROJECT"], bc.iloc[:, 1], bc["gen_overnight_cost"])}


def test_gas_capex_premium_central(tmp_path):
    from tracked_demands.case_writer import write_case_inputs
    run = _s0_case(tmp_path, "prem", CENTRAL_LOG)
    out = write_case_inputs(run / "inputs", _spec(PREMIUM_GENS), run / "inputs_td")
    expect = {"g_aero": (0.45, 0.226), "g_titan": (0.45, 0.226), "g_frame": (0.45, 0.226), "g_cc": (0.37, 0.184),
              "g_recip": (0, 0), "g_fc": (0, 0), "g_recip_ct": (0.45, 0.226), "g_recip_10": (0.1, 0.1)}
    got = _capex(out)
    for g, (p28, p35) in expect.items():
        assert got[(g, 2028)] == pytest.approx(1 + p28) and got[(g, 2035)] == pytest.approx(1 + p35), g
    log = (out / "tracked_demands_writer_log.txt").read_text()
    assert "gas capex: mode premium, path central, span_mean (s0_production.yml)" in log
    assert "g_frame: gas capex premium (central CT path, span_mean, premium class ct): 2028 +45.0%, 2035 +22.6%" in log
    assert "g_recip: no gas capex premium (mode premium, premium class none)" in log
    assert "g_recip_10: gas capex premium (spec premium 0.1" in log


def test_gas_capex_atb_moderate_no_premium(tmp_path):
    """A case built with gas_capex atb_moderate (the regression row's on_pgdays): the spec says so; no premium."""
    from tracked_demands.case_writer import write_case_inputs
    run = _s0_case(tmp_path, "atb", "gas capex: atb_moderate (new CC/CT at ATB 2024 Moderate)")
    with pytest.raises(ValueError, match="don't match the case's build"):
        write_case_inputs(run / "inputs", _spec(PREMIUM_GENS[:4]), run / "a")
    out = write_case_inputs(run / "inputs", _spec(PREMIUM_GENS[:4], gas_capex={"mode": "atb_moderate"}), run / "b")
    assert all(v == pytest.approx(1.0) for v in _capex(out).values())
    assert "g_frame: no gas capex premium (mode atb_moderate, premium class ct)" in \
        (out / "tracked_demands_writer_log.txt").read_text()


def test_gas_capex_without_s0_build_or_with_a_mismatch(tmp_path):
    from tracked_demands.case_writer import write_case_inputs
    run = _s0_case(tmp_path, "none", None)          # not an s0_production case: no premium
    out = write_case_inputs(run / "inputs", _spec(PREMIUM_GENS[:1]), run / "a")
    assert all(v == pytest.approx(1.0) for v in _capex(out).values())
    low = _s0_case(tmp_path, "low", CENTRAL_LOG.replace("central", "low"))
    with pytest.raises(ValueError, match="don't match the case's build"):
        write_case_inputs(low / "inputs", _spec(PREMIUM_GENS[:1]), low / "a")
    # the logged premiums are checked too
    bad = _s0_case(tmp_path, "bad", CENTRAL_LOG.replace("22.6", "30.0"))
    with pytest.raises(ValueError, match="differs from the case's"):
        write_case_inputs(bad / "inputs", _spec(PREMIUM_GENS[:1]), bad / "a")
    with pytest.raises(ValueError, match="linked generators only"):
        write_case_inputs(run / "inputs", _spec(PREMIUM_GENS[:1]) | {"tracked_demands": [
            {**_spec(PREMIUM_GENS[:1])["tracked_demands"][0],
             "storage": {"power_cost_per_mw_yr": 1, "energy_cost_per_mwh_yr": 1, "premium_class": "ct"}}]}, run / "b")
