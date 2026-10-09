"""The old tracked-demands suite (tracked_demands/scripts/legacy/run_td_tests.ps1, groups A-F), ported to the toy.

The legacy runner solved a full 2035 case (s4x1_caelp_parclust_zoned) on a Windows machine and checked only that each
case solved and met its CFE target. Here each case keeps its purpose and runs on the 3-zone toy with HiGHS, with an
assertion on the feature it exercises. Plus the module features the legacy runner didn't cover: dispatch bounds
(stage 6), predetermined on-site builds, MIP build sizes, the soft grid cap, annual grid caps (9B).
"""
from __future__ import annotations

import pandas as pd
import pytest

import td_toy
from td_toy import csv, read, cost, td_case, toyutil

TOL = 1e-5


def caps(**mw):
    return lambda inp: csv(inp, "tracked_demand_onsite_build_caps.csv",
                           [{"TRACKED_DEMAND": "DC1", "td_onsite_tech": k, "td_onsite_build_mw_cap": v}
                            for k, v in mw.items()])


def chain(*fns):
    def f(inp):
        for fn in fns:
            fn(inp)
    return f


def built(run):
    return read(run, "tracked_demand_onsite_build.csv").set_index("tech")["built_MW"]


def electrolyser(inp, max_mw=3.0, share=0.5, zone="North"):
    csv(inp, "tracked_demands.csv", [{"TRACKED_DEMAND": "EL1", "td_type": "electrolyzer",
                                      "td_energy_requirement_mwh_per_year": share * max_mw * 8760,
                                      "td_default_min_power_mw": 0, "td_default_max_power_mw": max_mw}])
    csv(inp, "tracked_demand_candidate_zones.csv", [{"TRACKED_DEMAND": "EL1", "LOAD_ZONE": zone}])
    for f in ("tracked_demand_storage.csv",):
        (inp / f).unlink(missing_ok=True)


# ------------------------------------------------------------------------------------------------ A: DC CFE variants
def test_A1_dc_no_cfe_adds_load(tmp_path):
    base = toyutil.toy_run(tmp_path, "base")
    run = td_case(tmp_path, "A1", matching=None)
    ann = read(run, "tracked_demand_annual.csv")
    assert (ann["annual_dispatch_MWh"] >= ann["energy_requirement_MWh"] - 1e-3).all()
    assert cost(run) > toyutil.total_cost(base / "outputs")
    assert not (run / "outputs" / "tracked_demand_cfe_detail.csv").exists()


def test_A2_dc_cfe_99pct_hourly_met(tmp_path):
    run = td_case(tmp_path, "A2", matching="hourly", target=0.99, penalty=1e4)
    d = read(run, "tracked_demand_cfe_detail.csv")
    assert d["shortfall_MWh"].max() < 1e-3
    assert (d["cfe_achieved_fraction"] >= 0.99 - TOL).all()


def test_A3_dc_cfe_advisory_no_investment(tmp_path):
    """Zero penalty: the targets change nothing (on-site solar is economic in the toy on its own, so compare with no
    targets rather than with zero builds)."""
    none = td_case(tmp_path, "A3none", matching=None)
    run = td_case(tmp_path, "A3", matching="hourly", target=0.9, penalty=0.0)
    assert abs(cost(run) - cost(none)) <= 1e-7 * cost(none)
    assert (built(run) - built(none)).abs().max() < 1e-4
    assert read(run, "tracked_demand_cfe_detail.csv")["shortfall_MWh"].max() > 1.0


# ------------------------------------------------------------------------------------------------ B: supply options
def test_B1_recs_only(tmp_path):
    def recs(inp):
        t = pd.read_csv(inp / "tracked_demand_cfe_targets.csv")
        csv(inp, "tracked_demand_rec_supply.csv",
            t[["TRACKED_DEMAND", "PERIOD", "time_block"]].assign(td_rec_supply_mwh=1e6, td_rec_cost_per_mwh=5.0))
    run = td_case(tmp_path, "B1", matching="hourly", storage=False,
                  after=chain(recs, caps(solar_onsite=0, gas_recip=0)))
    d = read(run, "tracked_demand_cfe_detail.csv")
    assert d["shortfall_MWh"].max() < 1e-3 and d["rec_MWh"].sum() > 0
    assert built(run).sum() < 1e-6


def test_B2_solar_and_storage_only(tmp_path):
    run = td_case(tmp_path, "B2", matching="hourly", after=caps(gas_recip=0))
    b = built(run)
    assert b["gas_recip"] < 1e-6 and b["solar_onsite"] > 0.1
    assert read(run, "tracked_demand_storage_build.csv")["built_power_MW"].sum() > 0.1
    assert read(run, "tracked_demand_cfe_detail.csv")["shortfall_MWh"].max() < 1e-3


def test_B3_firm_clean_only(tmp_path):
    def geo(inp):
        t = pd.read_csv(inp / "tracked_demand_onsite_techs.csv", na_values=".")
        t = pd.concat([t, pd.DataFrame([{"td_onsite_tech": "geo_onsite", "td_onsite_tech_capital_cost": 250000.0,
                                         "td_onsite_tech_is_clean": 1}])])
        csv(inp, "tracked_demand_onsite_techs.csv", t)
    run = td_case(tmp_path, "B3", matching="hourly", storage=False,
                  after=chain(geo, caps(solar_onsite=0, gas_recip=0)))
    b = built(run)
    assert b["geo_onsite"] > 0.1 and b["solar_onsite"] < 1e-6
    assert read(run, "tracked_demand_cfe_detail.csv")["shortfall_MWh"].max() < 1e-3


# ------------------------------------------------------------------------------------------------ C: grid limits, siting
def test_C3_grid_clean_cap_forces_onsite(tmp_path):
    free = td_case(tmp_path, "C3free", matching="hourly")

    def gcap(inp):
        t = pd.read_csv(inp / "tracked_demand_cfe_targets.csv")
        csv(inp, "tracked_demand_grid_clean_caps.csv",
            t[["TRACKED_DEMAND", "PERIOD", "time_block"]].assign(td_grid_clean_max_mwh=0.0))
    capped = td_case(tmp_path, "C3", matching="hourly", after=gcap)
    assert built(capped)["solar_onsite"] > built(free)["solar_onsite"] + 0.1
    assert cost(capped) > cost(free)


def test_C4_location_flexible_siting(tmp_path):
    def flex(inp):
        csv(inp, "tracked_demands.csv", [{"TRACKED_DEMAND": "DC1", "td_type": "data_center",
                                          "td_energy_requirement_mwh_per_year": 0.9 * 2 * 8760,
                                          "td_default_min_power_mw": 2, "td_default_max_power_mw": 2,
                                          "td_location_fixed": 0}])
        csv(inp, "tracked_demand_candidate_zones.csv",
            [{"TRACKED_DEMAND": "DC1", "LOAD_ZONE": z} for z in ("North", "Central", "South")])
    run = td_case(tmp_path, "C4", onsite=False, storage=False, after=flex)
    al = read(run, "tracked_demand_zone_allocation.csv")
    assert set(al["zone"]) == {"North", "Central", "South"}
    assert (al.groupby("period")["allocation_fraction"].sum() - 1).abs().max() < TOL


def test_soft_grid_cap(tmp_path):
    run = td_case(tmp_path, "soft", matching=None, after=lambda inp: csv(
        inp, "tracked_demand_grid_soft_cap.csv",
        [{"TRACKED_DEMAND": "DC1", "td_grid_soft_cap_mw": 1.0, "td_grid_soft_cap_penalty": 1e6}]))
    assert read(run, "tracked_demand_dispatch.csv")["TDGridDraw_MW"].max() <= 1.0 + TOL
    assert (run / "outputs" / "tracked_demand_grid_cap_summary.csv").exists()
    assert built(run).sum() > 0.5


def test_annual_grid_cap_9B(tmp_path):
    cap_mwh = 0.5 * 2 * 8760
    run = td_case(tmp_path, "gridcap", matching=None, after=lambda inp: csv(
        inp, "tracked_demand_grid_caps.csv",
        [{"TRACKED_DEMAND": "DC1", "PERIOD": p, "td_annual_grid_max_mwh": cap_mwh} for p in (2020, 2030)]))
    ann = read(run, "tracked_demand_annual.csv")
    assert (ann["annual_grid_draw_MWh"] <= cap_mwh + 1e-3).all()


# ------------------------------------------------------------------------------------------------ D: electrolyser
def test_D1_electrolyser_basic(tmp_path):
    run = td_case(tmp_path, "D1", onsite=False, storage=False, after=electrolyser)
    ann = read(run, "tracked_demand_annual.csv")
    assert (ann["annual_dispatch_MWh"] >= ann["energy_requirement_MWh"] - 1e-3).all()


def test_D2_electrolyser_h2_storage(tmp_path):
    avg_kg_hr = 0.5 * 3.0 / 0.05     # MW / (MWh per kg)
    run = td_case(tmp_path, "D2", onsite=False, storage=False, after=chain(electrolyser, lambda inp: csv(
        inp, "tracked_demand_h2_storage.csv",
        [{"TRACKED_DEMAND": "EL1", "td_h2_storage_cost_per_kg_yr": 1.0,
          "td_h2_min_delivery_kg_per_hr": 0.9 * avg_kg_hr}])))
    h2 = read(run, "tracked_demand_h2_dispatch.csv")
    assert (h2["delivery_kg_hr"] >= 0.9 * avg_kg_hr - 1e-4).all()
    assert (run / "outputs" / "tracked_demand_h2_build.csv").exists()


def test_D3_flex_event(tmp_path):
    run = td_case(tmp_path, "D3", onsite=False, storage=False, after=chain(electrolyser, lambda inp: csv(
        inp, "tracked_demand_flex_events.csv",
        [{"TRACKED_DEMAND": "EL1", "TIMEPOINT": 2, "td_flex_max_grid_draw_mw": 0.0,
          "td_flex_noncompliance_penalty": 1e5}])))
    fx = read(run, "tracked_demand_flex_detail.csv")
    assert fx["grid_draw_MW"].max() < 1e-6


# ------------------------------------------------------------------------------------------------ E: 45V
def test_E1_electrolyser_45v_assessment(tmp_path):
    def el_cfe(inp):
        electrolyser(inp)
        t = pd.read_csv(inp / "tracked_demand_cfe_targets.csv").assign(TRACKED_DEMAND="EL1")
        t.to_csv(inp / "tracked_demand_cfe_targets.csv", index=False)
        b = pd.read_csv(inp / "tracked_demand_time_blocks.csv").assign(TRACKED_DEMAND="EL1")
        b.to_csv(inp / "tracked_demand_time_blocks.csv", index=False)
        csv(inp, "tracked_demand_h2_storage.csv", [{"TRACKED_DEMAND": "EL1", "td_h2_storage_cost_per_kg_yr": 1.0}])
    run = td_case(tmp_path, "E1", matching="hourly", target=0.9, storage=False, after=el_cfe)
    a = read(run, "tracked_demand_45v_assessment.csv")
    assert set(a["period"]) == {2020, 2030} and (a["total_h2_kg"] > 0).all()
    assert read(run, "tracked_demand_cfe_detail.csv")["shortfall_MWh"].max() < 1e-3


# ------------------------------------------------------------------------------------------------ F: multi-TD
def test_F1_two_tracked_demands(tmp_path):
    def multi(inp):
        td = pd.read_csv(inp / "tracked_demands.csv")
        el = pd.DataFrame([{"TRACKED_DEMAND": "EL1", "td_type": "electrolyzer",
                            "td_energy_requirement_mwh_per_year": 0.5 * 3 * 8760,
                            "td_default_min_power_mw": 0, "td_default_max_power_mw": 3,
                            "td_grid_interconnect_mw": 3}])
        csv(inp, "tracked_demands.csv", pd.concat([td, el]))
        csv(inp, "tracked_demand_candidate_zones.csv",
            [{"TRACKED_DEMAND": t, "LOAD_ZONE": "North"} for t in ("DC1", "EL1")])
    run = td_case(tmp_path, "F1", matching="hourly", after=multi)
    ann = read(run, "tracked_demand_annual.csv")
    assert set(ann["tracked_demand"]) == {"DC1", "EL1"}
    assert set(read(run, "tracked_demand_cfe_detail.csv")["tracked_demand"]) == {"DC1"}


# ------------------------------------------------------------------------------------------------ not in the legacy runner
def test_dispatch_bounds_stage6(tmp_path):
    run = td_case(tmp_path, "bounds", min_mw=0.0, onsite=False, storage=False, after=lambda inp: csv(
        inp, "tracked_demand_dispatch_bounds.csv",
        [{"TRACKED_DEMAND": "DC1", "TIMEPOINT": 4, "td_min_power_mw": 0.0, "td_max_power_mw": 0.5}]))
    d = read(run, "tracked_demand_dispatch.csv").set_index("timepoint")
    assert d.loc[4, "TDDispatch_MW"] <= 0.5 + TOL


def test_predetermined_onsite(tmp_path):
    run = td_case(tmp_path, "predet", matching=None, storage=False, after=lambda inp: csv(
        inp, "tracked_demand_onsite_predetermined.csv",
        [{"TRACKED_DEMAND": "DC1", "td_onsite_tech": "solar_onsite", "td_onsite_predetermined_mw": 1.0}]))
    assert abs(built(run)["solar_onsite"] - 1.0) < TOL


def test_mip_build_size(tmp_path):
    def mip(inp):
        t = pd.read_csv(inp / "tracked_demand_onsite_techs.csv", na_values=".")
        t.loc[t["td_onsite_tech"] == "gas_recip", "td_onsite_tech_min_build_mw"] = 1.5
        t.loc[t["td_onsite_tech"] == "gas_recip", "td_onsite_tech_build_increment_mw"] = 0.5
        csv(inp, "tracked_demand_onsite_techs.csv", t)
        caps(solar_onsite=0)(inp)
        csv(inp, "tracked_demand_grid_soft_cap.csv",
            [{"TRACKED_DEMAND": "DC1", "td_grid_soft_cap_mw": 1.0, "td_grid_soft_cap_penalty": 1e6}])
    run = td_case(tmp_path, "mip", matching=None, storage=False, after=mip, duals=False)
    assert abs(built(run)["gas_recip"] - 1.5) < TOL
