"""On-site fuel plant as ordinary Switch generators linked to a tracked demand, and carbon-cap coverage (§108, step 2a).

Toy: data centre DC1 (2 MW flat, North) with a soft 1 MW grid cap, so a linked gas generator in North supplies the rest.
"""
from __future__ import annotations

import pandas as pd
import pytest

from td_toy import add_onsite_gen, carbon_cap, cost, csv, read, set_td, soft_cap, td_case

TOL = 1e-5
GEN = "N-DC1-gas"


def base(inp, **gen):
    add_onsite_gen(inp, **gen)
    soft_cap(inp)


def coverage(**cols):
    return lambda inp: csv(inp, "tracked_demand_cap_coverage.csv", [{"CO2_PROGRAM": "RGGI", **cols}])


def chain(*fns):
    def f(inp):
        for fn in fns:
            fn(inp)
    return f


def case(tmp_path, name, after, carbon=False, before=(), **kw):
    kw.setdefault("matching", None)
    kw.setdefault("onsite", False)
    kw.setdefault("storage", False)
    before = (("carbon_policies_regional",) if carbon else ()) + tuple(before)
    return td_case(tmp_path, name, after=after, before=before, **kw)


def onsite_emissions(run, period=2020):
    e = read(run, "tracked_demand_onsite_emissions.csv")
    return e[e["period"] == period].iloc[0]


def programme_emissions(run, period=2020):
    c = read(run, "carbon_program_clearing_prices.csv")
    col = [x for x in c.columns if "emission" in x.lower() and "tco2" in x.lower()][0]
    per = [x for x in c.columns if x.lower() in ("period", "pe")][0]
    return float(c.loc[c[per] == period, col].iloc[0])


# ------------------------------------------------------------------------------------------------ balance and fuel
def test_linked_generator_serves_the_load_through_standard_fuel_accounting(tmp_path):
    run = case(tmp_path, "gen", after=base)
    d = read(run, "tracked_demand_dispatch.csv")
    assert d["TDGridDraw_MW"].max() <= 1.0 + TOL
    assert (d["TDDispatch_MW"] - d["TDGridDraw_MW"] + d["TDGridExport_MW"] - d["TDOnsiteGen_MW"]).abs().max() < TOL
    assert d["TDOnsiteGen_MW"].min() > 0.9
    # the generator is an ordinary Switch generator: in dispatch.csv, with fuel use and emissions
    disp = read(run, "dispatch.csv")
    g = disp[disp["generation_project"] == GEN]
    assert len(g) and g["DispatchEmissions_tCO2_per_typical_yr"].sum() > 0
    fuel = read(run, "tracked_demand_onsite_fuel_use.csv")
    assert set(fuel["fuel"]) == {"NaturalGas"}
    hr = 9.0
    merged = fuel.merge(d, on=["tracked_demand", "timepoint"])
    assert (merged["fuel_use_mmbtu_per_h"] - hr * merged["TDOnsiteGen_MW"]).abs().max() < 1e-4


def test_zone_balance_nets_to_grid_draw(tmp_path):
    """The zone sees the TD's grid draw only: a TD that draws 1 MW and runs 1 MW on site costs the system the same
    as a 1 MW load plus the generator's own costs (checked by comparing two equivalent toys)."""
    run = case(tmp_path, "net", after=base)
    disp = read(run, "dispatch.csv")
    d = read(run, "tracked_demand_dispatch.csv")
    zone_gen = disp[disp["gen_load_zone"] == "North"].groupby("timestamp")["DispatchGen_MW"].sum()
    onsite = disp[disp["generation_project"] == GEN].set_index("timestamp")["DispatchGen_MW"]
    assert (onsite.reindex(d["timestamp"]).values - d["TDOnsiteGen_MW"].values).__abs__().max() < TOL
    assert zone_gen.sum() > onsite.sum()


@pytest.mark.parametrize("share", [0, 0.1, "."])
def test_export_limit(tmp_path, share):
    """A cheap, efficient on-site generator exports when allowed; the share limits annual export to share x gross."""
    def after(inp):
        base(inp, heat_rate=5.0, overnight=1.0, variable_om=0.0)
        set_td(inp, td_export_limit_share=share)
    run = case(tmp_path, f"exp{share}", after=after)
    d = read(run, "tracked_demand_dispatch.csv")
    tp = pd.read_csv(run / "inputs" / "timepoints.csv")
    ts = pd.read_csv(run / "inputs" / "timeseries.csv").set_index("TIMESERIES")
    w = tp.set_index("timepoint_id")["timeseries"].map(
        ts["ts_duration_of_tp"] * ts["ts_scale_to_period"] / 10.0)
    exp = (d.set_index("timepoint")["TDGridExport_MW"] * w).sum()
    gen = (d.set_index("timepoint")["TDOnsiteGen_MW"] * w).sum()
    if share == 0:
        assert d["TDGridExport_MW"].abs().max() < TOL
    elif share == 0.1:
        assert exp <= 0.1 * gen + 1e-3 and exp > 0
    else:
        assert exp > 0.1 * gen


def test_linked_gas_is_not_clean_in_matching(tmp_path):
    run = case(tmp_path, "cfe", after=base, matching="hourly", target=0.9, penalty=1000.0, clean_fraction=0.5)
    d = read(run, "tracked_demand_cfe_detail.csv")
    # grid draw is 1 of 2 MW at clean fraction 0.5: at most 25% clean; the gas half earns nothing
    assert (d["cfe_achieved_fraction"] <= 0.25 + 1e-6).all()
    assert d["shortfall_MWh"].min() > 0


# ------------------------------------------------------------------------------------------------ carbon-cap coverage
def test_onsite_counts_by_default(tmp_path):
    run = case(tmp_path, "cov_default", after=chain(base, carbon_cap), carbon=True)
    e = onsite_emissions(run)
    assert e["co2_program"] == "RGGI" and e["covered_by_cap"] == 1
    disp = read(run, "dispatch.csv")
    north = disp[(disp["gen_load_zone"] == "North") & (disp["period"] == 2020)]
    # carbon_program_clearing_prices.csv reports whole tonnes
    assert programme_emissions(run) == pytest.approx(north["DispatchEmissions_tCO2_per_typical_yr"].sum(), abs=1.0)


def test_unit_below_mw_threshold_is_exempt_and_reported(tmp_path):
    run = case(tmp_path, "cov_mw", after=chain(lambda i: base(i, unit_mw=2.0), carbon_cap,
                                               coverage(td_cap_threshold_mw=25)), carbon=True)
    e = onsite_emissions(run)
    assert e["covered_by_cap"] == 0 and e["coverage_reason"].startswith("below_mw_threshold")
    disp = read(run, "dispatch.csv")
    north = disp[(disp["gen_load_zone"] == "North") & (disp["period"] == 2020)]
    gross = north["DispatchEmissions_tCO2_per_typical_yr"].sum()
    assert programme_emissions(run) == pytest.approx(gross - e["tco2_per_yr"], abs=1.0)


def test_exemption_changes_a_binding_cap(tmp_path):
    """With a binding cap, exempting on-site emissions makes the plan cheaper."""
    def tight(inp):
        carbon_cap(inp, cap=15000.0)   # North emits about 25,000 t/yr uncapped
    covered = case(tmp_path, "bind_cov", after=chain(lambda i: base(i, unit_mw=2.0), tight), carbon=True)
    exempt = case(tmp_path, "bind_ex", after=chain(lambda i: base(i, unit_mw=2.0), tight,
                                                   coverage(td_cap_threshold_mw=25)), carbon=True)
    assert cost(exempt) < cost(covered) * (1 - 1e-6)


@pytest.mark.parametrize("basis,covered", [("unit", 0), ("site", 1)])
def test_site_basis_adds_up_units(tmp_path, basis, covered):
    def after(inp):
        base(inp, name="N-DC1-gas", unit_mw=15.0, rated_mw=15.0)
        add_onsite_gen(inp, name="N-DC1-gas2", unit_mw=15.0, rated_mw=15.0)
        carbon_cap(inp)
        coverage(td_cap_threshold_mw=25, td_cap_threshold_basis=basis)(inp)
    run = case(tmp_path, f"site_{basis}", after=after, carbon=True)
    e = read(run, "tracked_demand_onsite_emissions.csv")
    assert set(e["covered_by_cap"]) == {covered}


def test_tco2_threshold_with_actual_check(tmp_path):
    """Applicability is fixed from potential to emit at the declared rated MW; post_solve flags a site whose actual
    emissions reach the threshold."""
    run = case(tmp_path, "tco2", after=chain(lambda i: base(i, rated_mw=0.01), carbon_cap,
                                             coverage(td_cap_threshold_tco2=1000, td_cap_threshold_basis="site")),
               carbon=True)
    e = onsite_emissions(run)
    assert e["covered_by_cap"] == 0 and e["coverage_reason"].startswith("below_tco2_threshold")
    assert e["tco2_per_yr"] > 1000 and "re-run covered" in str(e["threshold_check"])


@pytest.mark.parametrize("share,covered", [(0.1, 0), (".", 1)])
def test_limited_export_exemption(tmp_path, share, covered):
    def after(inp):
        base(inp)
        set_td(inp, td_export_limit_share=share)
        carbon_cap(inp)
        coverage(td_cap_limited_export_exemption=1)(inp)
    run = case(tmp_path, f"lim{share}", after=after, carbon=True)
    assert onsite_emissions(run)["covered_by_cap"] == covered


def test_programme_can_exclude_onsite(tmp_path):
    run = case(tmp_path, "off", after=chain(base, carbon_cap, coverage(td_cap_counts_onsite=0)), carbon=True)
    assert onsite_emissions(run)["coverage_reason"] == "programme_excludes_onsite"


def test_missing_unit_size_is_an_error(tmp_path):
    run = case(tmp_path, "nosize", after=chain(base, carbon_cap, coverage(td_cap_threshold_mw=25)), carbon=True,
               solve=False)
    with pytest.raises(AssertionError, match=r"has no unit MW"):
        from td_toy import solve
        solve(run)


# ------------------------------------------------------------------------------------------------ other modules' settings
def test_gas_turbine_cap_membership(tmp_path):
    def gtc(inp, listed):
        csv(inp, "gas_turbine_cap.csv", [{"PERIOD": p, "gtc_max_mw": 1e6} for p in (2020, 2030)])
        csv(inp, "gas_turbine_cap_gens.csv",
            [{"GENERATION_PROJECT": g, "gtc_class": "ct", "gtc_weight": 1.0} for g in ["N-NG_GT"] + listed])
    from td_toy import solve
    ok = case(tmp_path, "gtc_ok", after=lambda i: (base(i, tech="TD_gas_ct", prime_mover="turbine"), gtc(i, [GEN])),
              before=("build_rate",))
    res = read(ok, "gas_turbine_cap_results.csv")
    built = read(ok, "BuildGen.csv")
    assert len(res) and built[built.iloc[:, 0] == GEN].iloc[:, -1].sum() > 0.5
    bad = case(tmp_path, "gtc_bad", after=lambda i: (base(i, tech="TD_gas_ct", prime_mover="turbine"), gtc(i, [])),
               before=("build_rate",), solve=False)
    with pytest.raises(AssertionError, match=r"on-site turbine N-DC1-gas must be in"):
        solve(bad)
    recip = case(tmp_path, "gtc_recip", after=lambda i: (base(i), gtc(i, [GEN])), before=("build_rate",), solve=False)
    with pytest.raises(AssertionError, match=r"reciprocating engine N-DC1-gas"):
        solve(recip)


def test_onsite_plant_uses_no_headroom(tmp_path):
    from td_toy import solve
    bad = case(tmp_path, "ic_bad", after=base, before=("interconnection_headroom",), solve=False)
    with pytest.raises(AssertionError, match=r"has interconnection"):
        solve(bad)
    ok = case(tmp_path, "ic_ok", after=lambda i: (base(i), csv(i, "ic_weights.csv",
                                                                [{"IC_KEY": "TD_gas_recip", "ic_weight": 0.0}])),
              before=("interconnection_headroom",))
    assert (ok / "outputs" / "tracked_demand_onsite_fuel_use.csv").exists()
