"""Stress-day load modes and the tracked demands' planning-reserve requirement (§108 (d), Tom's decision D1).

Stress days are the zero-weight timepoints. On them a TD's load is its maximum (default), a stated profile or its
minimum, and may be cut by up to its curtailable share. In prm_regional its requirement is
min(grid connection, peak load) x (1 - curtailable share) in every stress hour, and the same MW enters the region peak
(the import-cap basis). On-site plant gets no reserve credit.
"""
from __future__ import annotations

import sys

import pandas as pd
import pytest

from td_toy import REPO, add_onsite_gen, csv, read, set_td, td_case, write_td_inputs

TOL = 1e-5
STRESS = [8, 9]   # td_toy.add_stress_timeseries ids


def stress_dispatch(run):
    d = read(run, "tracked_demand_dispatch.csv").set_index("timepoint")["TDDispatch_MW"]
    return d.loc[STRESS]


def flex(**cols):
    return lambda inp: set_td(inp, **cols)


# ------------------------------------------------------------------------------------------------ modes
def test_max_is_the_default(tmp_path):
    run = td_case(tmp_path, "max", stress=True, min_mw=0.0, onsite=False, storage=False)
    assert (stress_dispatch(run) - 2.0).abs().max() < TOL


def test_min_mode(tmp_path):
    run = td_case(tmp_path, "min", stress=True, min_mw=0.5, onsite=False, storage=False,
                  after=flex(td_stress_mode="min"))
    assert (stress_dispatch(run) - 0.5).abs().max() < TOL


def test_profile_mode(tmp_path):
    def prof(inp):
        set_td(inp, td_stress_mode="profile")
        csv(inp, "tracked_demand_stress_profile.csv",
            [{"TRACKED_DEMAND": "DC1", "TIMEPOINT": 8, "td_stress_load_mw": 1.2},
             {"TRACKED_DEMAND": "DC1", "TIMEPOINT": 9, "td_stress_load_mw": 0.7}])
    run = td_case(tmp_path, "prof", stress=True, min_mw=0.0, onsite=False, storage=False, after=prof)
    d = stress_dispatch(run)
    assert d.loc[8] == pytest.approx(1.2, abs=TOL) and d.loc[9] == pytest.approx(0.7, abs=TOL)


def test_profile_needs_every_stress_hour(tmp_path):
    from td_toy import solve

    def prof(inp):
        set_td(inp, td_stress_mode="profile")
        csv(inp, "tracked_demand_stress_profile.csv",
            [{"TRACKED_DEMAND": "DC1", "TIMEPOINT": 8, "td_stress_load_mw": 1.2}])
    run = td_case(tmp_path, "profbad", stress=True, min_mw=0.0, onsite=False, storage=False, after=prof,
                  solve=False)
    with pytest.raises(AssertionError, match="no row"):
        solve(run)


@pytest.mark.parametrize("mode,low,high", [("max", 1.5, 2.0), ("min", 0.375, 0.5)])
def test_curtailable_share_combines_with_any_mode(tmp_path, mode, low, high):
    """Share 0.25: the stress load can be cut to 75% of the mode's load (free on a zero-weight day, so it is)."""
    run = td_case(tmp_path, f"curt_{mode}", stress=True, min_mw=0.5, onsite=False, storage=False,
                  after=flex(td_stress_mode=mode, td_stress_curtailable_share=0.25))
    d = stress_dispatch(run)
    assert (d >= low - TOL).all() and (d <= high + TOL).all()


def test_weighted_hours_unchanged(tmp_path):
    """Stress modes touch only zero-weight timepoints: without stress days the plan is the same as before (§107)."""
    run = td_case(tmp_path, "nostress", matching="hourly", after=flex(td_stress_mode="min",
                                                                      td_stress_curtailable_share=0.5))
    base = td_case(tmp_path, "nostress0", matching="hourly")
    assert read(run, "tracked_demand_annual.csv").equals(read(base, "tracked_demand_annual.csv"))


# ------------------------------------------------------------------------------------------------ PRM requirement
def prm_case(tmp_path, name, td=True, td_cols=None, linked=False, credit_none=True, siting=None, td_first=True):
    """s0_workflow's prm_regional toy with a tracked demand in Central (loaded before prm_regional)."""
    sys.path.insert(0, str(REPO / "s0_workflow" / "tests"))
    from test_prm import BACKUP, PEN, mini_case

    def extra(inp, tps):
        if not td:
            return
        write_td_inputs(inp, matching=None, onsite=False, storage=False, clean_fraction=None, min_mw=0.0)
        csv(inp, "tracked_demand_candidate_zones.csv", [{"TRACKED_DEMAND": "DC1", "LOAD_ZONE": "Central"}])
        # stress mode min (minimum 0): the stress-day energy doesn't matter here, only the requirement
        set_td(inp, **{"td_default_max_power_mw": 8.0, "td_grid_interconnect_mw": 5.0,
                       "td_energy_requirement_mwh_per_year": 0.0, "td_stress_mode": "min", **(td_cols or {})})
        if siting:
            set_td(inp, td_location_fixed=0)
            csv(inp, "tracked_demand_candidate_zones.csv",
                [{"TRACKED_DEMAND": "DC1", "LOAD_ZONE": z} for z in siting])
            csv(inp, "tracked_demand_siting_fixed.csv",
                [{"TRACKED_DEMAND": "DC1", "LOAD_ZONE": z, "td_fixed_allocation": v} for z, v in siting.items()])
        if linked:
            add_onsite_gen(inp, name="C-DC1-gas", zone="Central")
            if credit_none:
                c = pd.read_csv(inp / "prm_gen_credit.csv")
                c = pd.concat([c, pd.DataFrame([{"GENERATION_PROJECT": "C-DC1-gas", "prm_credit": "none",
                                                 "prm_class": "onsite"}])])
                c.to_csv(inp / "prm_gen_credit.csv", index=False)
        mods = inp.parent / "mods"
        (mods / "tracked_demands.py").write_text((REPO / "switch/study_modules/tracked_demands.py").read_text())
        lines = (inp / "modules.txt").read_text().splitlines()
        i = lines.index("mods.prm_regional") + (0 if td_first else 1)
        (inp / "modules.txt").write_text("\n".join(lines[:i] + ["mods.tracked_demands"] + lines[i:]) + "\n")

    gens = BACKUP + [("N-th", "North", "thermal", 30), ("C-wind", "Central", "wind", 4)]
    loads = {"North": ([50, 50], [8, 8, 8, 8]), "Central": ([50, 50], [10, 12, 10, 9]), "South": ([1, 1], [1, 1, 1, 1])}
    per = [{"PRM_REGION": r, "PERIOD": 2020, "prm_margin": 0.15, "prm_import_share": 0.3} for r in ("R1", "R2")]
    return mini_case(tmp_path, name, gens, loads, [("N-C", "North", "Central", 20, 0.95),
                                                   ("C-S", "Central", "South", 0, 1.0)],
                     {"North": "R1", "Central": "R2"}, per, {}, PEN, cf={"C-wind": 0.5}, extra=extra)


def zone_hours(out, zone="Central"):
    z = pd.read_csv(out / "prm_zone_hours.csv")
    return z[z["LOAD_ZONE"] == zone].sort_values("TIMEPOINT").reset_index(drop=True)


@pytest.mark.parametrize("cols,req", [
    ({}, 5.0),                                                 # min(connection 5, peak 8)
    ({"td_stress_curtailable_share": 0.2}, 4.0),               # x (1 - 0.2)
    ({"td_grid_interconnect_mw": 12.0}, 8.0),                  # capped at peak load 8
])
def test_requirement_and_region_peak(tmp_path, cols, req):
    without = prm_case(tmp_path, "without", td=False)
    with_td = prm_case(tmp_path, "with", td_cols=cols)
    a, b = zone_hours(without), zone_hours(with_td)
    assert ((b["served_load_mw"] - a["served_load_mw"]) - req).abs().max() < 1e-6
    assert ((b["requirement_mw"] - a["requirement_mw"]) - 1.15 * req).abs().max() < 1e-6
    ra, rb = pd.read_csv(without / "prm_region_hours.csv"), pd.read_csv(with_td / "prm_region_hours.csv")
    ra, rb = ra[ra["PRM_REGION"] == "R2"], rb[rb["PRM_REGION"] == "R2"]
    # import cap = share x region peak; the peak rises by the requirement
    assert (rb["import_cap_mw"].values - ra["import_cap_mw"].values - 0.3 * req).__abs__().max() < 1e-6


def test_onsite_plant_gets_no_reserve_credit(tmp_path):
    out = prm_case(tmp_path, "linked", linked=True)
    cc = pd.read_csv(out / "prm_zone_hours.csv")
    assert len(cc)
    with pytest.raises(AssertionError, match="no reserve"):
        prm_case(tmp_path, "linked_bad", linked=True, credit_none=False)


def test_max_mode_beyond_the_connection_is_an_error(tmp_path):
    from td_toy import solve
    run = td_case(tmp_path, "toobig", stress=True, min_mw=0.0, onsite=False, storage=False,
                  after=flex(td_grid_interconnect_mw=1.0), solve=False)
    with pytest.raises(AssertionError, match="on stress days"):
        solve(run)


def test_flexible_td_with_fixed_siting_splits_requirement(tmp_path):
    """Siting fixed half North (R1), half Central (R2): each zone's served load and each region's peak take half."""
    without = prm_case(tmp_path, "without", td=False)
    with_td = prm_case(tmp_path, "split", siting={"North": 0.5, "Central": 0.5})
    for zone, region in (("North", "R1"), ("Central", "R2")):
        a, b = zone_hours(without, zone), zone_hours(with_td, zone)
        assert ((b["served_load_mw"] - a["served_load_mw"]) - 2.5).abs().max() < 1e-6
        ra = pd.read_csv(without / "prm_region_hours.csv").query("PRM_REGION == @region")
        rb = pd.read_csv(with_td / "prm_region_hours.csv").query("PRM_REGION == @region")
        assert (rb["import_cap_mw"].values - ra["import_cap_mw"].values - 0.3 * 2.5).__abs__().max() < 1e-6


def test_tracked_demands_must_come_before_prm_regional(tmp_path):
    with pytest.raises(AssertionError, match="loaded before prm_regional"):
        prm_case(tmp_path, "order", td_first=False)
