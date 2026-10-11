"""Case-build writer for tracked-demand inputs (§108 (g)): on the toy, with stress days, a tax-credit table and a
carbon programme, then a solve of the written case."""
from __future__ import annotations

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
    assert cov["td_cap_threshold_mw"] == 25 and cov["td_cap_limited_export_exemption"] == 1
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
