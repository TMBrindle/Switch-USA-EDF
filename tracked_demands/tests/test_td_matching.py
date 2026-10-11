"""Hourly clean-energy matching on the toy data centre: on (hourly blocks), annual, off; zero-weight stress hours.

Tests marked xfail(strict=True) record gaps found in the step-1 audit (CHANGES §107). Each passes, and so fails the
suite, once the gap is fixed, so the marker has to be removed with the fix. Gaps 3 and 5 were fixed in §108 (b),
gap 1 in §108 (d); none is left.
"""
from __future__ import annotations

import pandas as pd
import pytest

from td_toy import read, cost, td_case

TOL = 1e-5


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    t = tmp_path_factory.mktemp("matching")
    return {m: td_case(t, m or "off", matching=m) for m in (None, "annual", "hourly")}


def test_dc_solves_with_hourly_matching_on_and_off(runs):
    off, annual, hourly = runs[None], runs["annual"], runs["hourly"]
    assert not (off / "outputs" / "tracked_demand_cfe_detail.csv").exists()
    for run in (annual, hourly):
        d = read(run, "tracked_demand_cfe_detail.csv")
        assert d["shortfall_MWh"].max() < 1e-3
        assert (d["cfe_achieved_fraction"] >= d["cfe_target"] - TOL).all()
    # hourly matching is the tighter commitment
    assert cost(off) <= cost(annual) * (1 + 1e-9) <= cost(hourly) * (1 + 1e-9)
    assert cost(hourly) > cost(off) * (1 + 1e-6)
    assert len(read(hourly, "tracked_demand_cfe_detail.csv")) == 7   # one block per timepoint
    assert len(read(annual, "tracked_demand_cfe_detail.csv")) == 2   # one block per period


def test_zero_weight_hours_are_outside_cfe_blocks(tmp_path):
    """Stress hours have zero weight, so they add nothing to either side of a block (matching is over annual MWh)."""
    run = td_case(tmp_path, "stress", matching="hourly", stress=True)
    d = read(run, "tracked_demand_cfe_detail.csv").set_index("time_block")
    assert d.loc[["h8", "h9"], "total_MWh"].abs().max() < TOL
    assert d.loc[["h8", "h9"], "clean_MWh"].abs().max() < TOL


# ------------------------------------------------------------------------------------------------ §107 gap 3, fixed in §108
def test_hourly_clean_credit_not_above_consumption(runs):
    d = read(runs["hourly"], "tracked_demand_cfe_detail.csv")
    assert (d["clean_MWh"] <= d["total_MWh"] * (1 + 1e-6)).all()


def test_clean_through_the_battery_counts_once_at_discharge(runs):
    """Night hours (no solar) meet the hourly target through clean discharge; the battery's clean ledger never
    exceeds its level, and discharged clean <= clean charged x round-trip efficiency over each period."""
    run = runs["hourly"]
    d = read(run, "tracked_demand_cfe_detail.csv")
    assert d["shortfall_MWh"].max() < 1e-3
    sto = read(run, "tracked_demand_storage_dispatch.csv")
    led = read(run, "tracked_demand_clean_ledger.csv").merge(sto, on=["tracked_demand", "timepoint"])
    assert (led["clean_level_MWh"] <= led["level_MWh"] + 1e-6).all()
    assert (led["clean_discharge_MW"] <= led["discharge_MW"] + 1e-6).all()
    night = led[led["timepoint"].isin([1, 3, 5])]
    assert night["clean_discharge_MW"].sum() > 0.1




# ------------------------------------------------------------------------------------------------ §107 gap 1, fixed in §108 (d)
def test_tracked_load_present_on_stress_days(tmp_path):
    """Stress mode `max` (the default): a flexible TD (minimum 0) runs at its maximum on zero-weight stress days."""
    run = td_case(tmp_path, "stressflex", matching=None, stress=True, min_mw=0.0, onsite=False, storage=False)
    d = read(run, "tracked_demand_dispatch.csv").set_index("timepoint")
    assert d.loc[[8, 9], "TDDispatch_MW"].min() > 0.5


# ------------------------------------------------------------------------------------------------ §107 gap 5, fixed in §108
def test_computed_clean_fraction_keys_are_timepoints(runs):
    comp = read(runs["hourly"], "grid_clean_fraction_computed.csv")
    tps = pd.read_csv(runs["hourly"] / "inputs" / "timepoints.csv")
    assert set(comp["TIMEPOINT"]) <= set(tps["timepoint_id"])


def test_computed_clean_fraction_feeds_a_second_pass(runs, tmp_path):
    """grid_clean_fraction_computed.csv is a valid grid_clean_fraction.csv for the next pass."""
    first = runs["hourly"]
    comp = read(first, "grid_clean_fraction_computed.csv")

    def use_computed(inp):
        comp[["LOAD_ZONE", "TIMEPOINT", "grid_clean_fraction"]].to_csv(inp / "grid_clean_fraction.csv", index=False)
    second = td_case(tmp_path, "pass2", matching="hourly", after=use_computed)
    assert read(second, "tracked_demand_cfe_detail.csv")["shortfall_MWh"].max() < 1e-3


def test_stress_hour_co2_intensity_not_zero(tmp_path):
    run = td_case(tmp_path, "stressco2", matching=None, stress=True, onsite=False, storage=False)
    g = read(run, "grid_co2_intensity_computed.csv")
    stress = pd.read_csv(run / "inputs" / "timepoints.csv").query("timeseries == '2020_stress'")["timepoint_id"]
    assert g[g["TIMEPOINT"].isin(stress) & (g["LOAD_ZONE"] == "North")]["co2_tonne_per_mwh"].min() > 0
