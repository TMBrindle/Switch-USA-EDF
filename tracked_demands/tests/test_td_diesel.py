"""Backup diesel for tracked demands (CHANGES §120): an existing fleet linked to the demand, emergency running on
stress days within a permit cap on full-load-equivalent hours (stress hours at real duration, grouped by weather
year), the stress grid limit (option B) and its PRM requirement, the visible shortfall, non-emergency running for
tier 4 engines, potential to emit at permit hours, and a chain.

Toy: DC1 in North, 2 MW flat (minimum 2 MW), 2 MW connection; stress days of 2 timepoints x 12 h; the fleet is
2.6 MW (1.3 x the connection) built in 2015."""
from __future__ import annotations

import os
import subprocess
import sys

import pandas as pd
import pytest

from td_toy import REPO, add_stress_timeseries, csv, read, set_td, td_case, td_chain

GEN = "N-DC1-diesel"
MW = 2.6
HR = 10.2
CO2 = 0.07315          # DistillateFuelOil in the toy's fuels.csv


def add_diesel(inp, mw=MW, fuel_price=None, links=True, **cols):
    """The fleet: predetermined (2015, zero capex), DistillateFuelOil, linked to DC1, a tracked_demand_diesel.csv row."""
    gi = pd.read_csv(inp / "gen_info.csv", na_values=".")
    row = gi[gi["GENERATION_PROJECT"] == "N-NG_GT"].iloc[0].copy()
    row.update({"GENERATION_PROJECT": GEN, "gen_tech": "TD_diesel", "gen_energy_source": "DistillateFuelOil",
                "gen_full_load_heat_rate": HR, "gen_variable_om": 0.0, "gen_capacity_limit_mw": float("nan"),
                "gen_max_age": 40, "gen_forced_outage_rate": 0.0, "gen_scheduled_outage_rate": 0.0,
                "gen_unit_size": float("nan"), "gen_connect_cost_per_mw": 0.0})
    pd.concat([gi, pd.DataFrame([row])]).to_csv(inp / "gen_info.csv", index=False, na_rep=".")
    for f, add in (("gen_build_predetermined.csv", {"build_gen_predetermined": mw}),
                   ("gen_build_costs.csv", {"gen_overnight_cost": 0.0, "gen_fixed_om": 0.0})):
        df = pd.read_csv(inp / f)
        pd.concat([df, pd.DataFrame([{"GENERATION_PROJECT": GEN, "build_year": 2015, **add}])]).to_csv(
            inp / f, index=False)
    if fuel_price is not None:          # the toy prices distillate in its regional market All_DistOil
        sc = pd.read_csv(inp / "fuel_supply_curves.csv")
        sc.loc[sc.iloc[:, 0] == "All_DistOil", "unit_cost"] = fuel_price
        sc.to_csv(inp / "fuel_supply_curves.csv", index=False, na_rep=".")
    if links:
        f = inp / "tracked_demand_onsite_gens.csv"
        old = pd.read_csv(f) if f.exists() else pd.DataFrame()
        pd.concat([old, pd.DataFrame([{"TRACKED_DEMAND": "DC1", "GENERATION_PROJECT": GEN,
                                       "td_onsite_prime_mover": "recip", "td_onsite_gtc_class": "none",
                                       "td_onsite_unit_mw": 2.5, "td_onsite_rated_mw": mw}])]).to_csv(
            f, index=False, na_rep=".")
    csv(inp, "tracked_demand_diesel.csv", [{"TRACKED_DEMAND": "DC1", "GENERATION_PROJECT": GEN,
                                            "td_diesel_forced_outage_rate": 0.0, **cols}])


def stress_days(inp, years=(2007,), load_scale=1.0):
    """Stress days 2020_p1_prm, 2020_p2_prm, ... (2 x 12 h each, zone loads x load_scale), with their weather years.
    At load_scale 1 the system serves them easily, so only the TD's own stress rules decide the fleet's running."""
    names = [f"2020_p{i + 1}_prm" for i in range(len(years))]
    for nm in names:
        add_stress_timeseries(inp, name=nm, load_scale=load_scale)
    csv(inp, "tracked_demand_stress_years.csv",
        [{"TIMESERIES": nm, "td_stress_weather_year": y} for nm, y in zip(names, years)])


def case(tmp_path, name, years=(2007,), td=None, diesel=None, solve=True, extra_after=None, load_scale=1.0, **kw):
    def after(inp):
        stress_days(inp, years, load_scale)
        add_diesel(inp, **(diesel or {}))
        if td:
            set_td(inp, **td)
        if extra_after:
            extra_after(inp)
    return td_case(tmp_path, name, onsite=False, storage=False, after=after, solve=solve, **kw)


def run_switch(run, extra=()):
    """Solve and return the completed process (stdout kept, for the warnings)."""
    return subprocess.run(["switch", "solve", "--solver", "appsi_highs", "--suffixes", "dual", "--inputs-dir", "inputs",
                           "--outputs-dir", "outputs", *extra], cwd=run, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(run)})


def stress_rows(run):
    d = read(run, "tracked_demand_dispatch.csv")
    tp = pd.read_csv(run / "inputs" / "timepoints.csv")
    stress = set(tp.loc[tp["timeseries"].str.endswith("_prm"), "timepoint_id"])
    return d[d["timepoint"].isin(stress)], d[~d["timepoint"].isin(stress)]


def diesel_row(run, period=2020, year=None):
    df = read(run, "tracked_demand_diesel.csv")
    df = df[df["period"] == period]
    if year is not None:
        df = df[df["weather_year"].astype(str) == str(year)]
    return df.iloc[0]


# ------------------------------------------------------------------------------------------------ C1, C5
def test_emergency_only_and_idle_without_a_grid_limit(tmp_path):
    """s = 1 (D1b as is): the grid serves the stress days, the fleet costs money there (C5), so it stays off."""
    run = case(tmp_path, "idle")
    s, n = stress_rows(run)
    assert s["TDDiesel_MW"].abs().max() < 1e-9 and n["TDDiesel_MW"].abs().max() < 1e-9
    assert s["TDStressShortfall_MW"].abs().max() < 1e-9
    r = diesel_row(run)
    assert r["diesel_mw"] == pytest.approx(MW) and r["permit_flh"] == 36 and r["test_flh"] == 12
    assert r["total_flh"] == pytest.approx(12) and r["flh_remaining"] == pytest.approx(24)


def test_runs_when_the_stress_balance_binds(tmp_path):
    """Stress days at 1.5 x zone load: even at s = 1 the fleet runs where it relieves the stress-day energy balance
    (C5 prices it; it beats building capacity), and its cap still holds."""
    run = case(tmp_path, "binds", load_scale=1.5)
    s, _ = stress_rows(run)
    assert s["TDDiesel_MW"].max() > 0.1
    r = diesel_row(run)
    assert r["total_flh"] <= 36 + 1e-6 and r["stress_mwh"] > 0


# ------------------------------------------------------------------------------------------------ C2 at real hours
def test_grid_limit_runs_the_fleet_at_real_hours(tmp_path):
    run = case(tmp_path, "limit", td={"td_stress_grid_limit_share": 0.5})
    s, n = stress_rows(run)
    assert (s["TDGridDraw_MW"] <= 1.0 + 1e-6).all()
    assert s["TDDiesel_MW"].values == pytest.approx([1.0, 1.0])
    assert n["TDDiesel_MW"].abs().max() < 1e-9                                    # C1
    r = diesel_row(run, year=2007)
    assert r["stress_mwh"] == pytest.approx(24.0)                                  # 2 x 12 h x 1 MW (tp_weight is 0)
    assert r["stress_flh"] == pytest.approx(24.0 / MW)
    assert r["flh_remaining"] == pytest.approx(36 - 12 - 24.0 / MW)
    assert r["fuel_mmbtu_stress"] == pytest.approx(24.0 * HR) and r["co2_t_stress"] == pytest.approx(24.0 * HR * CO2)
    assert r["nox_t"] == pytest.approx((24.0 + 12 * MW) * 6.4 / 1000)              # tier 2 limit, stress + test
    assert r["shortfall_mwh"] == pytest.approx(0.0, abs=1e-6)
    e = read(run, "tracked_demand_onsite_emissions.csv")
    assert e.loc[e["period"] == 2020, "tco2_emergency_per_yr"].iat[0] == pytest.approx(24.0 * HR * CO2)


# ------------------------------------------------------------------------------------------------ C6 and the check
def test_tight_permit_shows_shortfall(tmp_path):
    """Permit 15 h, test 12 h: 3 h x 2.6 MW = 7.8 MWh for 24 MWh of need; the rest is shortfall, flagged before the
    solve and after it; strict mode stops the run instead."""
    run = case(tmp_path, "tight", td={"td_stress_grid_limit_share": 0.5}, diesel={"td_diesel_permit_flh": 15},
               solve=False)
    r = run_switch(run)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "needs 24 MWh beyond its stress grid limit" in r.stdout and "at most 8 MWh" in r.stdout
    row = diesel_row(run)
    assert row["stress_mwh"] == pytest.approx(3 * MW) and row["flh_remaining"] == pytest.approx(0.0, abs=1e-6)
    assert row["shortfall_mwh"] == pytest.approx(24.0 - 3 * MW)
    assert row["cap_dual_usd_per_mwh"] > 0
    summ = read(run, "tracked_demand_stress_summary.csv").iloc[0]
    assert summ["shortfall_mwh_total"] == pytest.approx(24.0 - 3 * MW) and str(summ["binding_weather_year"]) == "2007"
    strict = case(tmp_path, "strict", td={"td_stress_grid_limit_share": 0.5, "td_stress_strict": 1},
                  diesel={"td_diesel_permit_flh": 15}, solve=False)
    r = run_switch(strict)
    assert r.returncode != 0 and "td_stress_strict" in r.stdout + r.stderr


def test_shortfall_penalty_from_the_case_lost_load_cost(tmp_path):
    """The penalty is tracked_demand_params.csv's, else the case's unserved-load cost, else 10,000 $/MWh."""
    def penalty(run):
        ci = read(run, "costs_itemized.csv")
        return float(ci[(ci["Component"] == "TDStressShortfallCost") & (ci["PERIOD"] == 2020)]["AnnualCost_Real"].iat[0])
    base = case(tmp_path, "pen", td={"td_stress_grid_limit_share": 0.5}, diesel={"td_diesel_permit_flh": 15})
    own = case(tmp_path, "pen2", td={"td_stress_grid_limit_share": 0.5}, diesel={"td_diesel_permit_flh": 15},
               extra_after=lambda inp: csv(inp, "tracked_demand_params.csv", [{"td_stress_shortfall_penalty": 5000}]))
    short = 24.0 - 3 * MW
    assert penalty(base) == pytest.approx(short * 10000, rel=1e-6)
    assert penalty(own) == pytest.approx(short * 5000, rel=1e-6)


# ------------------------------------------------------------------------------------------------ weather-year groups
@pytest.mark.parametrize("years,short", [((2007, 2009), 0.0), ((2007, 2007), 48.0 - 12 * MW)])
def test_weather_year_groups(tmp_path, years, short):
    """Permit 24 h, test 12 h: 12 h x 2.6 MW = 31.2 MWh a year. Two stress days (24 MWh each) in different weather
    years each fit; in the same year they share it."""
    run = case(tmp_path, f"wy{years[1]}", years=years, td={"td_stress_grid_limit_share": 0.5},
               diesel={"td_diesel_permit_flh": 24})
    d = read(run, "tracked_demand_diesel.csv")
    d = d[d["period"] == 2020]
    assert sorted(d["weather_year"].astype(str)) == sorted({str(y) for y in years})
    assert d["shortfall_mwh"].sum() == pytest.approx(short, abs=1e-6)


def test_all_stress_days_one_year(tmp_path):
    run = case(tmp_path, "all", years=(2007, 2009), td={"td_stress_grid_limit_share": 0.5, "td_stress_year_basis": "all"},
               diesel={"td_diesel_permit_flh": 24})
    d = read(run, "tracked_demand_diesel.csv")
    assert set(d["weather_year"]) == {"all"}
    assert d.loc[d["period"] == 2020, "shortfall_mwh"].sum() == pytest.approx(48.0 - 12 * MW)


# ------------------------------------------------------------------------------------------------ non-emergency
def test_non_emergency_running_tier_4(tmp_path):
    """Cheap fuel: a tier 4 fleet allowed 5 h runs them in normal hours (counted at tp_weight_in_year; it would run
    ~12.6 h here unlimited), never exports, and its tonnes go through Switch's accounting."""
    run = case(tmp_path, "ne", diesel={"td_diesel_tier": 4, "td_diesel_non_emergency_flh": 5, "fuel_price": 0.01})
    r = diesel_row(run)
    assert r["non_emergency_flh"] == pytest.approx(5.0, rel=1e-6)
    assert r["total_flh"] <= 36 + 1e-6
    assert r["co2_t_non_emergency"] == pytest.approx(r["non_emergency_mwh"] * HR * CO2, rel=1e-6)
    d = read(run, "tracked_demand_dispatch.csv")
    assert d["TDGridExport_MW"].abs().max() < 1e-9                                 # C3
    e = read(run, "tracked_demand_onsite_emissions.csv")
    assert e.loc[e["period"] == 2020, "tco2_per_yr"].iat[0] == pytest.approx(r["co2_t_non_emergency"], rel=1e-6)


@pytest.mark.parametrize("cols,match", [
    ({"td_diesel_non_emergency_flh": 20}, "tier 2 and not permitted"),
    ({"td_diesel_non_emergency_flh": 30, "td_diesel_tier": 4}, "exceed the permit"),
])
def test_non_emergency_rules(tmp_path, cols, match):
    run = case(tmp_path, "ner", diesel=cols, solve=False)
    r = run_switch(run)
    assert r.returncode != 0 and match in r.stdout + r.stderr


# ------------------------------------------------------------------------------------------------ stress modes
@pytest.mark.parametrize("td,diesel_mw", [
    ({"td_stress_mode": "min", "td_default_min_power_mw": 1.0}, 0.0),     # min load 1 MW fits the 1 MW grid limit
    ({"td_stress_curtailable_share": 0.25, "td_default_min_power_mw": 0.0}, 0.5),   # 1.5 MW to serve, 1 MW grid
])
def test_stress_modes_set_the_need(tmp_path, td, diesel_mw):
    run = case(tmp_path, f"mode{diesel_mw}", td={"td_stress_grid_limit_share": 0.5, **td})
    s, _ = stress_rows(run)
    assert s["TDDiesel_MW"].values == pytest.approx([diesel_mw] * 2, abs=1e-6)


# ------------------------------------------------------------------------------------------------ PRM (option B)
@pytest.mark.parametrize("cols,req", [({"td_stress_grid_limit_share": 1.0}, 5.0),
                                      ({"td_stress_grid_limit_share": 0.5}, 2.5)])
def test_prm_requirement_uses_the_grid_limit(tmp_path, cols, req):
    sys.path.insert(0, str(REPO / "tracked_demands" / "tests"))
    from test_td_stress_prm import prm_case, zone_hours
    without = prm_case(tmp_path, "without", td=False)
    with_td = prm_case(tmp_path, f"with{req}", td_cols=cols)
    a, b = zone_hours(without), zone_hours(with_td)
    assert ((b["served_load_mw"] - a["served_load_mw"]) - req).abs().max() < 1e-6


# ------------------------------------------------------------------------------------------------ cap coverage
@pytest.mark.parametrize("pte_hours,covered", [(".", 0), (8766, 1)])
def test_potential_to_emit_at_permit_hours(tmp_path, pte_hours, covered):
    """A 1,000 t/yr site threshold: the fleet at its 36 permit hours (70 t) is below it; at 8766 h (17,000 t) above."""
    def after(inp):
        f = inp / "tracked_demand_onsite_gens.csv"
        g = pd.read_csv(f)
        g["td_onsite_pte_hours"] = pte_hours
        g.to_csv(f, index=False)
        from td_toy import carbon_cap
        carbon_cap(inp)
        csv(inp, "tracked_demand_cap_coverage.csv", [{"CO2_PROGRAM": "RGGI", "td_cap_threshold_tco2": 1000,
                                                      "td_cap_threshold_basis": "site"}])
    run = case(tmp_path, f"pte{covered}", extra_after=after, before=("carbon_policies_regional",))
    e = read(run, "tracked_demand_onsite_emissions.csv")
    assert int(e.loc[e["period"] == 2020, "covered_by_cap"].iat[0]) == covered


# ------------------------------------------------------------------------------------------------ light formulation
def s0_operating_modules(light):
    """The S0 dispatch module and simple fuel costs (the toy's regional markets priced at their first tier), with the
    stress day light or not."""
    def edit(inp):
        mods = inp.parent / "mods"
        for mod in ("generators_core_dispatch", "fuel_costs_simple"):
            (mods / f"{mod}.py").write_text((REPO / f"switch/study_modules/{mod}.py").read_text())
        rep = {"switch_model.generators.core.dispatch": "mods.generators_core_dispatch",
               "switch_model.energy_sources.fuel_costs.markets": "mods.fuel_costs_simple"}
        lines = [rep.get(ln.strip(), ln) for ln in (inp / "modules.txt").read_text().splitlines()]
        (inp / "modules.txt").write_text("\n".join(lines) + "\n")
        sc = pd.read_csv(inp / "fuel_supply_curves.csv", na_values=".")
        first = sc.sort_values("tier").groupby(["regional_fuel_market", "period"], as_index=False).first()
        zr = pd.read_csv(inp / "zone_to_regional_fuel_market.csv")
        rf = pd.read_csv(inp / "regional_fuel_markets.csv")
        fc = (zr.merge(rf, on="regional_fuel_market").merge(first, on="regional_fuel_market")
              [["load_zone", "fuel", "period", "unit_cost"]].rename(columns={"unit_cost": "fuel_cost"}))
        old = pd.read_csv(inp / "fuel_cost.csv")
        pd.concat([old, fc]).drop_duplicates(["load_zone", "fuel", "period"]).to_csv(inp / "fuel_cost.csv", index=False)
        for f in ("fuel_supply_curves.csv", "regional_fuel_markets.csv", "zone_to_regional_fuel_market.csv",
                  "zone_fuel_cost_diff.csv"):
            (inp / f).unlink(missing_ok=True)
        if light:
            csv(inp, "stress_light_timeseries.csv", [{"TIMESERIES": "2020_p1_prm"}])
    return edit


def test_light_stress_days_give_the_same_diesel_outputs(tmp_path):
    """Under the light formulation (no fuel use or emissions on stress days) the module's stress fuel and CO2 are the
    same as under full: they come from dispatch x real hours, not Switch's fuel accounting."""
    full = case(tmp_path, "full", td={"td_stress_grid_limit_share": 0.5}, extra_after=s0_operating_modules(False))
    lt = case(tmp_path, "light", td={"td_stress_grid_limit_share": 0.5}, extra_after=s0_operating_modules(True))
    a, b = diesel_row(full), diesel_row(lt)
    for c in ("stress_mwh", "fuel_mmbtu_stress", "co2_t_stress", "shortfall_mwh"):
        assert b[c] == pytest.approx(a[c], abs=1e-6), c


# ------------------------------------------------------------------------------------------------ chain
def test_chain_carries_the_fleet(tmp_path):
    """Stage 2020 then 2030: the fleet is existing capacity in both stages (carried by prepare_next_stage), and the
    permit cap holds in each."""
    def after(inp):
        stress_days(inp, (2007,))
        add_stress_timeseries(inp, period=2030, n=1, load_scale=1.0, name="2030_p1_prm")   # the toy has one 2030 tp
        sy = pd.read_csv(inp / "tracked_demand_stress_years.csv")
        pd.concat([sy, pd.DataFrame([{"TIMESERIES": "2030_p1_prm", "td_stress_weather_year": 2008}])]).to_csv(
            inp / "tracked_demand_stress_years.csv", index=False)
        add_diesel(inp)
        set_td(inp, td_stress_grid_limit_share=0.5)
    run, stages, outs = td_chain(tmp_path, "dchain", onsite=False, storage=False, after=after)
    pred = pd.read_csv(run / "in/2030/case/gen_build_predetermined.chained.case.csv")
    assert (pred["GENERATION_PROJECT"] == GEN).any()
    for out, p, need in zip(outs, (2020, 2030), (24.0, 12.0)):
        d = pd.read_csv(out / "tracked_demand_diesel.csv")
        r = d[d["period"] == p].iloc[0]
        assert r["diesel_mw"] == pytest.approx(MW) and r["stress_mwh"] == pytest.approx(need)
        assert r["total_flh"] <= 36 + 1e-6
