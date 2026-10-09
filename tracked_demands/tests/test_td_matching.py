"""Hourly clean-energy matching on the toy data centre: on (hourly blocks), annual, off; zero-weight stress hours.

Tests marked xfail(strict=True) record gaps found in the step-1 audit (CHANGES §94). Each passes, and so fails the
suite, once the gap is fixed, so the marker has to be removed with the fix.
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


# ------------------------------------------------------------------------------------------------ known gaps (§94)
@pytest.mark.xfail(strict=True, reason="§94 gap 3: on-site clean output that charges the battery is credited when "
                                       "generated and again (at the grid fraction) when discharged")
def test_hourly_clean_credit_not_above_consumption(runs):
    d = read(runs["hourly"], "tracked_demand_cfe_detail.csv")
    assert (d["clean_MWh"] <= d["total_MWh"] * (1 + 1e-6)).all()


@pytest.mark.xfail(strict=True, reason="§94 gap 1: a flexible tracked load drops to its minimum on zero-weight "
                                       "stress days (free: no weight), so stress days don't see it")
def test_tracked_load_present_on_stress_days(tmp_path):
    run = td_case(tmp_path, "stressflex", matching=None, stress=True, min_mw=0.0, onsite=False, storage=False)
    d = read(run, "tracked_demand_dispatch.csv").set_index("timepoint")
    assert d.loc[[8, 9], "TDDispatch_MW"].min() > 0.5


@pytest.mark.xfail(strict=True, reason="§94 gap 5: grid_clean_fraction_computed.csv is keyed by timestamp, "
                                       "grid_clean_fraction.csv by timepoint id, so a second pass can't read it")
def test_computed_clean_fraction_keys_are_timepoints(runs):
    comp = read(runs["hourly"], "grid_clean_fraction_computed.csv")
    tps = pd.read_csv(runs["hourly"] / "inputs" / "timepoints.csv")
    assert set(comp["TIMEPOINT"]) <= set(tps["timepoint_id"])


@pytest.mark.xfail(strict=True, reason="§94 gap 5: stress-hour CO2 intensity is computed from weighted emissions, "
                                       "so it reads 0 on zero-weight hours")
def test_stress_hour_co2_intensity_not_zero(tmp_path):
    run = td_case(tmp_path, "stressco2", matching=None, stress=True, onsite=False, storage=False)
    g = read(run, "grid_co2_intensity_computed.csv")
    stress_ts = pd.read_csv(run / "inputs" / "timepoints.csv").query("timeseries == '2020_stress'")["timestamp"]
    assert g[g["TIMEPOINT"].isin(stress_ts) & (g["LOAD_ZONE"] == "North")]["co2_tonne_per_mwh"].min() > 0
