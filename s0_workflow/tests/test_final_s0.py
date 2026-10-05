"""Final S0 configuration (CHANGES §66): defaults and the regression pins, the guaranteed stress-day rule, the unit-level
lifetime backstop, retirement friction, Virginia in RGGI, imports generators in CA/WA zones and existing units' fixed
O&M by period in chains. Hand-built fixtures only (not results)."""
import copy
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))

from s0_workflow import coal_fleet as cf  # noqa: E402
from s0_workflow import coal_spec as cs  # noqa: E402
from s0_workflow import prm  # noqa: E402
from s0_workflow import production as s0prod  # noqa: E402

S0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
AX = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"]


def resolved(*axes):
    """s0_production after the axis values (as settings_management merges them)."""
    s = copy.deepcopy(S0)
    for axis, val in axes:
        over = (AX[axis][val] or {}).get("s0_production") or {}
        s = s0prod.deep_merge(s, over)
    return s


# ------------------------------------------------------------------------------------------------ defaults and pins
def test_defaults_and_regression_pins():
    s = resolved(("s0_production", "on"), ("tx_bill", "s0_tx"), ("retirement_sens", "none"))
    assert s["prm"]["design"] == "regional" and s["prm"]["stress_days"]["rule"] == "cover_plus_interconnect_wind"
    assert s["demand_response"]["enabled"] is False
    assert s["time_sampling"]["method"] == "fleet_independent" and s["time_sampling"]["n_days"] == 24
    assert s["tx_policy"]["mode"] == "national_cap" and s["tx_policy"]["moratorium_first_period"] == 2040
    assert s["tx_policy"]["cap_tw_mi_per_yr"] == {2028: 0.0, 2030: 1.4} and s["forced_tx"] == "reeds_certain_plus_A"
    assert s["rggi"]["mode"] == "3pr" and s["rggi"]["virginia"]["enabled"] is True
    assert s["ca_wa_carbon"] == {"mode": "linked", "path": "central", "import_gens": {"p11": 0.428, "p1": 0.0, "p3": 0.0}}
    assert s["lifetime_backstop"] == {"enabled": True, "coal_years": 65, "gas_years": 55, "floor_year": 2026}
    assert s["retirement_friction"]["fraction"] == 0.5 and s["retirement_friction"]["from_period"] == 2030
    assert s["existing_fixed_om"] == "by_period"
    # the regression case: every new piece off / legacy
    reg = resolved(("s0_production", "on_pgdays"), ("tx_bill", "legacy"), ("retirement_sens", "none"))
    assert reg["tx_policy"]["mode"] == "legacy" and reg["forced_tx"] == "named_projects"
    assert reg["prm"]["design"] == "legacy" and reg["rggi"]["mode"] == "legacy" and reg["ca_wa_carbon"]["mode"] == "legacy"
    assert cf.lifetime_settings(reg) is None and not s0prod.friction_on(reg) and reg["existing_fixed_om"] == "mean"
    assert reg["time_sampling"]["method"] == "powergenome"
    assert "retirement_rules" not in s0prod.scenario_options({"s0_production": dict(reg, retirement_rule={"enabled": False})})
    # comparison rows keep today's transmission (tx_bill legacy sets it)
    assert resolved(("s0_production", "on_pgdays_new"), ("tx_bill", "legacy"))["tx_policy"]["mode"] == "legacy"
    # sensitivities
    assert resolved(("retirement_sens", "life_coal60"))["lifetime_backstop"]["coal_years"] == 60
    assert resolved(("retirement_sens", "life_coal70"))["lifetime_backstop"]["coal_years"] == 70
    assert not s0prod.friction_on(resolved(("retirement_sens", "friction_off")))
    assert cf.lifetime_settings(dict(resolved(("retirement_sens", "lifetime_off")), enabled=True)) is None
    si = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv")
    assert set(si.retirement_sens) == {"none", "life_coal60", "life_coal70", "friction_off"}
    assert set(si[si.retirement_sens != "none"].case_id) == {"s4x1_S0_tx_2035_life60", "s4x1_S0_tx_2035_life70",
                                                            "s4x1_S0_tx_2035_nofriction"}


# ------------------------------------------------------------------------------------------------ stress days
def _region_data(n_regions, rng):
    cal = prm.day_dates(7 * 365, 2007)
    H = 7 * 365 * 24
    load, wind, planted = {}, {}, {}
    for i in range(n_regions):
        r = f"R{i}"
        x = 100 + rng.normal(0, 1, H)
        s = int(cal[(cal.year == 2008 + i % 5) & (cal.month == 7) & (cal.dom == 1 + i)].day.iloc[0])
        w = int(cal[(cal.year == 2009 + i % 4) & (cal.month == 1) & (cal.dom == 2 + i)].day.iloc[0])
        x[s * 24:(s + 1) * 24] += 60
        x[w * 24:(w + 1) * 24] += 50
        # a high-load (top 1%) but not peak day with the lowest wind among the top-load days
        c = int(cal[(cal.year == 2012) & (cal.month == 8) & (cal.dom == 1 + i)].day.iloc[0])
        x[c * 24:(c + 1) * 24] += 30
        wcf = 0.4 + 0.05 * rng.random(H)
        wcf[c * 24:(c + 1) * 24] = 0.05
        wcf[1000 * 24:1001 * 24] = 0.0                       # calmer, but not a top-load day: not chosen
        load[r], wind[r], planted[r] = x, wcf, (s, w, c)
    return cal, load, wind, planted


def test_guaranteed_stress_days_rule():
    rng = np.random.default_rng(3)
    cal, load, wind, planted = _region_data(6, rng)
    sd = dict(prm.DEFAULTS["stress_days"], rule="guaranteed", top_load_share=0.01)
    days, cov, tol = prm.select_stress_days(load, {r: v.copy() for r, v in load.items()}, sd, wind)
    want = {d for v in planted.values() for d in v}
    assert set(days.day) == want and len(days) == 18 > sd["max_days"]   # every region's three days, no cap
    assert tol == 0.0 and (cov.covered_by == cov.worst_date).all() and len(cov) == 18
    c = cov.set_index(["PRM_REGION", "need"])
    for r, (s, w, k) in planted.items():
        assert c.at[(r, "summer_peak_load"), "worst_date"] == prm.days_label(cal, s)
        assert c.at[(r, "winter_peak_load"), "worst_date"] == prm.days_label(cal, w)
        assert c.at[(r, "low_wind_top_load"), "worst_date"] == prm.days_label(cal, k)
        assert c.at[(r, "low_wind_top_load"), "wind_cf"] == pytest.approx(0.05)
        assert c.at[(r, "low_wind_top_load"), "top_load_days"] == 26          # ceil(1% of 2,555)
    # a day that is two needs' worst day counts once
    load2 = {k: v.copy() for k, v in load.items()}
    s0 = planted["R0"][0]
    load2["R1"][s0 * 24:(s0 + 1) * 24] += 200
    d2, _, _ = prm.select_stress_days(load2, load2, sd, wind)
    assert len(d2) == 17 and "R0 summer_peak_load; R1 summer_peak_load" in set(d2.covers)
    # the greedy rule is still there (S0 before §66) and needs no wind
    d3, _, _ = prm.select_stress_days(load, load, dict(sd, rule="greedy"))
    assert len(d3) <= sd["max_days"]
    with pytest.raises(ValueError, match="wind"):
        prm.select_stress_days(load, load, sd)


def test_region_wind_national_fallback():
    L = pd.DataFrame({"z1": np.ones(48), "z2": np.ones(48)})
    R = pd.DataFrame({"w1": np.r_[np.full(24, 0.2), np.full(24, 0.4)], "s1": np.full(48, 0.3)})
    gens = pd.DataFrame({"Resource": ["w1", "s1"], "technology": ["LandbasedWind_Class1", "UtilityPV_Class1"],
                         "region": ["z1", "z2"]})
    wind = {}
    prm.region_series(L, R, gens, {"z1": "A", "z2": "B"}, wind=wind)
    assert list(wind["A"][:2]) == [0.2, 0.2] and list(wind["B"][24:26]) == [0.4, 0.4]
    assert wind.get(("_national", "B")) is True and ("_national", "A") not in wind


# ------------------------------------------------------------------------------------------------ lifetime backstop
def _units():
    return pd.DataFrame({
        "plant_id_eia": [1, 2, 3, 4, 5, 6, 7, 8],
        "generator_id": ["1", "1", "1", "1", "1", "1", "1", "1"],
        "technology_description": [cs.CSC, cs.CSC, "Natural Gas Fired Combined Cycle", cf.GAS_STEAM,
                                   "Natural Gas Fired Combustion Turbine", "Onshore Wind Turbine",
                                   cf.HOLD_TECH + " 9|1", "Natural Gas Internal Combustion Engine"],
        "operating_date": pd.to_datetime(["1970-06-01", "1960-01-01", "1990-05-01", "1950-01-01", "1980-01-01",
                                          "1960-01-01", "1950-01-01", None]),
        "retirement_year": [1970 + 500, 2028, 1990 + 500, 2040, 1980 + 500, 1960 + 500, 2029, 2500],
        "capacity_mw": [500.0, 300.0, 400.0, 200.0, 100.0, 50.0, 600.0, 10.0],
        "model_region": ["p1"] * 8,
    })


def test_lifetime_backstop_unit_level():
    lt = dict(cf.LIFETIME_DEFAULTS, enabled=True)
    rule = dict(cs.BLOCK_RULE)
    u = _units()
    # block_all: coal 1970 + 65 = 2035; coal 1960 -> 2025, due before 2030: held through the 2030 stage (2031; its
    # planned 2028 is pushed to 2031 by the block anyway); CC 1990 + 55 = 2045; gas steam 1950 -> 2005 -> 2031 (planned
    # 2040); CT 1980 + 55 = 2035; wind, the hold project and the unit without an operating year unchanged
    after, rec = cf.apply_lifetime(cf.push_pre2030(u, rule, pd.Series(["coal", "coal", "natural gas", "natural gas",
                                                                      "natural gas", "", "", "natural gas"])), lt, rule)
    assert list(after.retirement_year) == [2035, 2031, 2045, 2031, 2035, 2460, 2029, 2500]
    assert sorted(rec.plant_id_eia) == [1, 3, 4, 5]
    r = rec.set_index("plant_id_eia")
    assert r.at[1, "from_year"] == 2470 and r.at[1, "to_year"] == 2035 and r.at[1, "lifetime_class"] == "coal"
    assert r.at[4, "due_year"] == 2005 and r.at[4, "to_year"] == 2031 and r.at[4, "lifetime_class"] == "gas"
    by = cf.lifetime_by_stage(rec).set_index(["stage", "lifetime_class"])
    assert by.at[(2030, "gas"), "gw_retired_by_lifetime"] == 0.0             # held through 2030
    assert by.at[(2035, "coal"), "gw_retired_by_lifetime"] == 0.0             # retirement year 2035 runs in 2035
    assert by.at[(2040, "coal"), "gw_retired_by_lifetime"] == 0.5
    assert by.at[(2035, "gas"), "gw_retired_by_lifetime"] == 0.2             # gas steam (planned 2040)
    assert by.at[(2040, "gas"), "gw_retired_by_lifetime"] == 0.3             # + the CT (2035 < 2040)
    assert by.at[(2045, "gas"), "gw_retired_by_lifetime"] == 0.1             # steam's planned 2040 now applies
    assert by.at[(2045, "gas"), "gw_new_in_stage"] == pytest.approx(-0.2)
    # without the block: due before floor_year retires at the first stage (2026), coal 60 years
    after2, _ = cf.apply_lifetime(u, dict(lt, coal_years=60), None)
    assert list(after2.retirement_year)[:5] == [2030, 2026, 2045, 2026, 2035]


def test_lifetime_report(tmp_path):
    lt = dict(cf.LIFETIME_DEFAULTS, enabled=True)
    _, rec = cf.apply_lifetime(_units(), lt, dict(cs.BLOCK_RULE))
    cf._State.lifetime[("lt_case", 2030)] = (rec, lt)
    lines = []
    s0 = {"enabled": True, "lifetime_backstop": {"enabled": True}}
    cf.write_lifetime_report(tmp_path, s0, {2030: {"case_id": "lt_case"}, 2035: {"case_id": "lt_case"}}, lines.append)
    assert (tmp_path / "lifetime_retirements.csv").exists() and "2040: coal 0.50" in lines[0]
    with pytest.raises(RuntimeError, match="unit hook"):
        cf.write_lifetime_report(tmp_path, s0, {2028: {"case_id": "other"}}, lines.append)
    cf.write_lifetime_report(tmp_path, {"enabled": True}, {2028: {"case_id": "other"}}, lines.append)   # off: nothing


# ------------------------------------------------------------------------------------------------ friction
def test_retirement_friction_toy(tmp_path):
    from test_foresight import COAL, suspended, three_periods
    from toyutil import solve, toy_inputs, total_cost

    def with_friction(f, start=2030, fom=None):
        def edit(inp):
            three_periods(inp)
            if fom is not None:
                bc = pd.read_csv(inp / "gen_build_costs.csv")
                bc.loc[bc.GENERATION_PROJECT.isin(COAL), "gen_fixed_om"] = fom
                bc.to_csv(inp / "gen_build_costs.csv", index=False)
            if f:
                pd.DataFrame({"gen_energy_source": ["Coal"], "rf_fraction": [f], "rf_from_period": [start]}).to_csv(
                    inp / "retirement_friction.csv", index=False)
        return edit

    runs = {}
    for name, f, fom in (("f0", 0.0, None), ("f05", 0.5, None), ("f05_dear", 0.5, 4.0e6)):
        run = toy_inputs(tmp_path, name, ["retirement_rules"], with_friction(f, fom=fom))
        solve(run)
        runs[name] = run / "outputs"
    s0, s5, sd = suspended(runs["f0"]), suspended(runs["f05"]), suspended(runs["f05_dear"])
    # fixed O&M 400k/MW-yr: coal retires in 2030 when retiring avoids all of it, not when it avoids only half
    assert s0.get(2030, 0) > 1 and s5.sum() == pytest.approx(0, abs=1e-6)
    assert total_cost(runs["f05"]) >= total_cost(runs["f0"]) - 1e-6
    assert "friction_fraction" not in pd.read_csv(runs["f0"] / "retirement_rules_check.csv").columns  # as before
    # 4M/MW-yr: still retires with the friction, which charges half the fixed O&M on what retired
    assert sd.get(2030, 0) > 1
    chk = pd.read_csv(runs["f05_dear"] / "retirement_rules_check.csv").set_index("period")
    assert chk.at[2030, "friction_fraction"] == 0.5
    assert chk.at[2030, "friction_cost_per_yr"] == pytest.approx(0.5 * 4.0e6 * sd[2030], rel=1e-6)
    assert chk.at[2020, "friction_cost_per_yr"] == 0                       # blocked period: nothing to charge


def test_friction_case_writer(tmp_path):
    pd.DataFrame({"GENERATION_PROJECT": ["coal_old", "gas_old", "gas_new", "wind_old"],
                  "gen_energy_source": ["coal", "naturalgas", "naturalgas", "wind"],
                  "gen_can_retire_early": [1, 1, 0, 0]}).to_csv(tmp_path / "gen_info.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["coal_old", "gas_old", "wind_old"], "build_year": [1990, 2000, 2010],
                  "build_gen_predetermined": [1, 1, 1]}).to_csv(tmp_path / "gen_build_predetermined.csv", index=False)
    log = s0prod.Log(tmp_path)
    s0prod.write_retirement_friction(tmp_path, {"retirement_friction": {"fraction": 0.5}}, log)
    rf = pd.read_csv(tmp_path / "retirement_friction.csv")
    assert list(rf.gen_energy_source) == ["coal", "naturalgas"] and (rf.rf_fraction == 0.5).all()
    assert (rf.rf_from_period == 2030).all() and "avoids 50%" in log.lines[-1]
    (tmp_path / "retirement_friction.csv").unlink()
    s0prod.write_retirement_friction(tmp_path, {"retirement_friction": {"fraction": 0.0}}, log)
    assert not (tmp_path / "retirement_friction.csv").exists()
    with pytest.raises(ValueError, match="fraction"):
        s0prod.friction_settings({"retirement_friction": {"fraction": 1.5}})


# ------------------------------------------------------------------------------------------------ Virginia
def _carbon_case(folder, years=(2028, 2030)):
    zones = ["p98", "p99", "p100", "p118", "p124", "p11"]
    pd.DataFrame({"LOAD_ZONE": zones}).to_csv(folder / "load_zones.csv", index=False)
    rows = []
    for y in years:
        rows += [{"CO2_PROGRAM": "ETS 1", "PERIOD": y, "LOAD_ZONE": "p98", "carbon_cap_tco2_per_yr": 1.0e6,
                  "carbon_cost_dollar_per_tco2": ".", "carbon_floor_price_dollar_per_tco2": 10.0 + y - 2028},
                 {"CO2_PROGRAM": "ETS 2", "PERIOD": y, "LOAD_ZONE": "p11", "carbon_cap_tco2_per_yr": 0.0,
                  "carbon_cost_dollar_per_tco2": 50.0, "carbon_floor_price_dollar_per_tco2": 0.0}]
    pd.DataFrame(rows).to_csv(folder / "carbon_policies_regional.csv", index=False)
    pd.DataFrame({"PERIOD": list(years), "carbon_cap_tco2_per_yr": [1.0e6] * len(years),
                  "carbon_cost_dollar_per_tco2": ["."] * len(years)}).to_csv(folder / "carbon_policies.csv", index=False)
    pd.DataFrame([{"CO2_PROGRAM": "ETS 1", "PERIOD": y, "ccr_tier": t, "ccr_pool_tco2_per_yr": 10656120.0,
                   "ccr_price_dollar_per_tco2": 23.0 * t} for y in years for t in (1, 2)]).to_csv(
        folder / "carbon_policies_ccr.csv", index=False)


def test_va_budget_file():
    b = pd.read_csv(REPO / "s0_workflow/specs/rggi/va_budget.csv").set_index("year").va_budget_short_tons
    cap = pd.read_csv(REPO / "pg/extra_inputs/rggi_carbon/rggicon_3pr.csv", header=None, index_col=0)[1]
    assert b[2026] == 22_960_000 and list(b.index) == list(range(2026, 2038))
    for y in b.index:
        assert b[y] == pytest.approx(22.96e6 * cap[y] / cap[2026], abs=1)
    assert b[2027] / 20_408_889 - 1 == pytest.approx(0.0002, abs=0.0001)        # DEQ's reported 2027 (unverified)
    assert s0prod.va_budget_short_tons(2045) == b[2037]                          # held after 2037


def test_va_in_ets1(tmp_path):
    _carbon_case(tmp_path, (2028, 2030, 2045))
    lines = []
    s0 = {"rggi": {"mode": "3pr", "virginia": {"enabled": True}}}
    s0prod.write_va_rggi(tmp_path, s0, {2028: {}, 2030: {}, 2045: {}}, lines.append)
    c = pd.read_csv(tmp_path / "carbon_policies_regional.csv")
    va = c[c.LOAD_ZONE.isin(["p99", "p100", "p118", "p124"])]
    assert set(va.CO2_PROGRAM) == {"ETS 1"} and len(va) == 12
    b = {y: s0prod.va_budget_short_tons(y) * 0.907185 for y in (2028, 2030, 2045)}
    for y in (2028, 2030, 2045):
        v = va[va.PERIOD == y]
        assert v.carbon_cap_tco2_per_yr.sum() == pytest.approx(b[y])
        assert (v.carbon_floor_price_dollar_per_tco2 == 10.0 + y - 2028).all() and (v.carbon_cost_dollar_per_tco2 == ".").all()
    assert len(c[c.CO2_PROGRAM == "ETS 2"]) == 3                                     # other programs untouched
    cp = pd.read_csv(tmp_path / "carbon_policies.csv").set_index("PERIOD").carbon_cap_tco2_per_yr
    assert cp[2030] == pytest.approx(1.0e6 + b[2030])
    ccr = pd.read_csv(tmp_path / "carbon_policies_ccr.csv")
    assert list(ccr[ccr.PERIOD == 2030].ccr_pool_tco2_per_yr) == pytest.approx([10656120.0 + 0.1 * b[2030]] * 2)
    rep = pd.read_csv(tmp_path / "rggi_va_budget.csv").set_index("PERIOD")
    assert rep.at[2028, "va_budget_short_tons"] == 17861420 and "combined cap" in lines[0]
    # Virginia already in ETS 1 (RGGI10+VA): stop rather than count it twice
    with pytest.raises(ValueError, match="twice"):
        s0prod.write_va_rggi(tmp_path, s0, {2028: {}}, lines.append)
    # off (legacy mode, the regression case; or virginia disabled) and before first_year: nothing
    _carbon_case(tmp_path, (2028,))
    before = (tmp_path / "carbon_policies_regional.csv").read_bytes()
    s0prod.write_va_rggi(tmp_path, {"rggi": {"mode": "legacy", "virginia": {"enabled": True}}}, {2028: {}}, lines.append)
    s0prod.write_va_rggi(tmp_path, {"rggi": {"virginia": {"enabled": True, "first_year": 2030}}}, {2028: {}}, lines.append)
    assert (tmp_path / "carbon_policies_regional.csv").read_bytes() == before


# ------------------------------------------------------------------------------------------------ imports generators
def test_imports_generators_cost(tmp_path):
    pd.DataFrame({"GENERATION_PROJECT": ["p11_imports_1", "p1_imports_1", "p3_imports_1", "p18_imports_1", "p11_cc"],
                  "gen_tech": ["Imports", "Imports", "Imports", "Imports", "NaturalGas_CC"],
                  "gen_load_zone": ["p11", "p1", "p3", "p18", "p11"]}).to_csv(tmp_path / "gen_info.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["p11_imports_1"], "PERIOD": [2030], "gen_fixed_om_by_period": ["."],
                  "gen_variable_om_by_period": [1.5], "gen_storage_energy_fixed_om_by_period": ["."]}).to_csv(
        tmp_path / "gen_om_by_period.csv", index=False)
    lines = []
    s0 = {"ca_wa_carbon": {"mode": "linked", "import_gens": {"p11": 0.428, "p1": 0.0, "p3": 0.0}}}
    s0prod.write_imports_carbon_cost(tmp_path, s0, {2028: {}, 2030: {}}, lines.append)
    om = pd.read_csv(tmp_path / "gen_om_by_period.csv", na_values=["."]).set_index(["GENERATION_PROJECT", "PERIOD"])
    assert set(om.index) == {("p11_imports_1", 2028), ("p11_imports_1", 2030)}       # Canada at 0: no rows
    assert om.at[("p11_imports_1", 2028), "gen_variable_om_by_period"] == pytest.approx(48.6 * 0.428, abs=1e-4)
    assert om.at[("p11_imports_1", 2030), "gen_variable_om_by_period"] == pytest.approx(1.5 + 53.4 * 0.428, abs=1e-4)
    assert "p1 (0 t/MWh): ['p1_imports_1']" in lines[0]
    before = (tmp_path / "gen_om_by_period.csv").read_bytes()
    s0prod.write_imports_carbon_cost(tmp_path, {"ca_wa_carbon": {"mode": "legacy", "import_gens": {"p11": 0.428}}},
                                     {2030: {}}, lines.append)
    assert (tmp_path / "gen_om_by_period.csv").read_bytes() == before


# ------------------------------------------------------------------------------------------------ fixed O&M by period
def test_existing_fom_by_period(tmp_path):
    pd.DataFrame({"GENERATION_PROJECT": ["coal_a", "coal_a", "mixed", "mixed", "cc_new"],
                  "build_year": [1530, 1540, 1990, 2030, 2030],
                  "gen_overnight_cost": [0, 0, 0, 900, 900], "gen_fixed_om": [50000.0, 50000.0, 20000.0, 15000.0, 15000.0]}
                 ).to_csv(tmp_path / "gen_build_costs.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["coal_a", "coal_a", "mixed"], "build_year": [1530, 1540, 1990],
                  "build_gen_predetermined": [100, 200, 50]}).to_csv(tmp_path / "gen_build_predetermined.csv", index=False)
    log = s0prod.Log(tmp_path)
    s0prod.write_existing_fom_by_period(tmp_path, {"existing_fixed_om": "by_period"}, log)
    assert list(pd.read_csv(tmp_path / "existing_fom_by_period.csv").GENERATION_PROJECT) == ["coal_a"]
    (tmp_path / "existing_fom_by_period.csv").unlink()
    s0prod.write_existing_fom_by_period(tmp_path, {"existing_fixed_om": "mean"}, log)
    assert not (tmp_path / "existing_fom_by_period.csv").exists()

    spec = importlib.util.spec_from_file_location("pns", REPO / "switch/study_modules/prepare_next_stage.py")
    pns = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pns)
    carried = pd.DataFrame({"GENERATION_PROJECT": ["coal_a", "coal_a", "coal_b", "cc_new"],
                            "build_year": [1530, 1540, 1550, 2030], "gen_fixed_om": [50000.0, 50000.0, 40000.0, 15000.0]})
    nxt = pd.DataFrame({"GENERATION_PROJECT": ["coal_a", "cc_new"], "build_year": [1530, 2035],
                        "gen_fixed_om": [61000.0, 14000.0]})
    out, n = pns.existing_fom_from_next_stage(carried, nxt, {"coal_a", "coal_b"})
    assert list(out.gen_fixed_om) == [61000.0, 61000.0, 40000.0, 15000.0] and n == 1   # coal_b: no own row, kept


# ------------------------------------------------------------------------------------------------ §68 stress-day rule
def test_cover_plus_interconnect_wind_rule():
    """Greedy cover at 6% plus each interconnection's lowest-wind top-load day, skipped when already in the set."""
    rng = np.random.default_rng(5)
    cal, load, wind, planted = _region_data(4, rng)
    sd = dict(prm.DEFAULTS["stress_days"], rule="cover_plus_interconnect_wind")
    assert sd["cover_plus_tolerance"] == 0.06 and sd["max_days"] == 12
    D = 7 * 365
    ic_load = {"eastern": load["R0"] + load["R1"], "western": load["R2"], "ercot": load["R3"].copy()}
    ic_wind = {"eastern": (wind["R0"] + wind["R1"]) / 2, "western": wind["R2"], "ercot": wind["R3"].copy()}
    # ERCOT: its low-wind top-load day is R3's summer peak (already in the cover set): not added again
    s3 = planted["R3"][0]
    ic_wind["ercot"][s3 * 24:(s3 + 1) * 24] = 0.01
    days, cov, tol = prm.select_stress_days(load, load, sd, None, (ic_load, ic_wind))
    greedy, _, tol_g = prm.greedy_stress_days(load, load, dict(sd, cover_tolerance=0.06))
    assert tol == tol_g == 0.06
    ic = cov[cov.PRM_REGION.str.startswith("interconnection:")].set_index("PRM_REGION")
    assert list(ic.index) == ["interconnection:eastern", "interconnection:western", "interconnection:ercot"]
    assert ic.at["interconnection:ercot", "added"] == "no (already in the set)"
    assert ic.at["interconnection:ercot", "worst_date"] == prm.days_label(cal, s3)
    # the Eastern and Western days: lowest wind among each interconnection's top 1% load days
    for name in ("eastern", "western"):
        dmax = ic_load[name].reshape(D, 24).max(axis=1)
        top = np.argsort(-dmax, kind="stable")[:26]
        d = int(top[np.argmin(ic_wind[name].reshape(D, 24).mean(axis=1)[top])])
        assert ic.at[f"interconnection:{name}", "worst_date"] == prm.days_label(cal, d)
    added = int((ic.added == "yes").sum())
    assert len(days) == len(greedy) + added and days.day.is_unique and days.day.is_monotonic_increasing
    assert len(days) <= sd["max_days"] + 3
    assert "ERCOT low_wind_top_load (interconnection)" in days.set_index("day").at[s3, "covers"]
    with pytest.raises(ValueError, match="interconnections"):
        prm.select_stress_days(load, load, sd)
    with pytest.raises(ValueError, match="must be one of"):
        prm.select_stress_days(load, load, dict(sd, rule="other"))


def test_cover_plus_interconnect_wind_end_to_end(tmp_path):
    """add_stress_days with the S0 default: regions and interconnections from hierarchy.csv (p120 PJM / eastern,
    p65 ERCOT / ercot); ids, weights and the info line."""
    rng = np.random.default_rng(2)
    H = 7 * 365 * 24
    lc = pd.DataFrame({"p120": 100 + rng.normal(0, 1, H), "p65": 90 + rng.normal(0, 1, H)})
    var = pd.DataFrame({"r_wind": rng.uniform(0, 1, H), "r_pv": rng.uniform(0, 1, H)})
    gens = pd.DataFrame({"Resource": ["r_wind", "r_pv"], "technology": ["LandbasedWind_Class3", "UtilityPV_Class1"],
                         "region": ["p120", "p120"]})
    assert prm.zone_interconnects(["p120", "p65", "p11"]) == {"p120": "eastern", "p65": "ercot", "p11": "western"}
    s = {"model_year": 2035, "s0_production": {"enabled": True, "prm": {"design": "regional", "stress_days": {
        "rule": "cover_plus_interconnect_wind"}}}}
    res = {"load_profiles": lc.iloc[:24], "resource_profiles": var.iloc[:24], "ClusterWeights": [365.0]}
    out, rep, w, n = prm.add_stress_days(res, pd.DataFrame({"slot": ["p1"]}), [365.0], lc, var, gens, s, tmp_path)
    cov = pd.read_csv(tmp_path / "stress_coverage.csv")
    ic = cov[cov.PRM_REGION.str.startswith("interconnection:")]
    assert set(ic.PRM_REGION) == {"interconnection:eastern", "interconnection:ercot"}
    assert n == len(pd.read_csv(tmp_path / "stress_days.csv")) <= 2 * 3 + 2 and w[-n:] == [0.0] * n
    info = (tmp_path / "stress_info.txt").read_text()
    assert "rule cover_plus_interconnect_wind" in info and "cover tolerance 0.060" in info
    assert "interconnection low-wind days:" in info
