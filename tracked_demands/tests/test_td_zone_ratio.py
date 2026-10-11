"""Tracked load in gen_zone_ratio (§108 (e)): the load side counts each tracked demand's zone withdrawal (grid draw -
export + its linked on-site generators), consistent with the generation side, which counts those generators."""
from __future__ import annotations

import pandas as pd
import pytest

from td_toy import add_onsite_gen, csv, read, soft_cap, td_case

TOL = 1e-6


def ratio_file(**cols):
    return lambda inp: csv(inp, "gen_zone_load_ratio.csv",
                           [{"LOAD_ZONE": "North", "PERIOD": p, **cols} for p in (2020, 2030)])


def chain(*fns):
    def f(inp):
        for fn in fns:
            fn(inp)
    return f


def linked(inp):
    add_onsite_gen(inp)
    soft_cap(inp)


def weights(run):
    tp = pd.read_csv(run / "inputs" / "timepoints.csv")
    ts = pd.read_csv(run / "inputs" / "timeseries.csv").set_index("TIMESERIES")
    per = pd.read_csv(run / "inputs" / "periods.csv").set_index("INVESTMENT_PERIOD")
    yrs = (per["period_end"] - per["period_start"] + 1)
    w = tp["timeseries"].map(ts["ts_duration_of_tp"] * ts["ts_scale_to_period"]) / tp["timeseries"].map(
        ts["ts_period"]).map(yrs)
    return pd.Series(w.values, index=tp["timepoint_id"]), tp.set_index("timepoint_id")["timeseries"].map(ts["ts_period"])


def summary(run, p=2020):
    s = read(run, "gen_zone_ratio_summary.csv")
    return s[(s["TYPE"] == "zone") & (s["LOAD_ZONE"] == "North") & (s["PERIOD"] == p)].iloc[0]


def test_load_side_counts_the_tracked_withdrawal(tmp_path):
    base = td_case(tmp_path, "base", load_module=False, before=("gen_zone_ratio",), after=ratio_file())
    run = td_case(tmp_path, "td", before=("gen_zone_ratio",), onsite=False, storage=False,
                  after=chain(linked, ratio_file()))
    d = read(run, "tracked_demand_dispatch.csv").set_index("timepoint")
    w, per = weights(run)
    withdrawal = d["TDGridDraw_MW"] - d["TDGridExport_MW"] + d["TDOnsiteGen_MW"]
    mwh = (withdrawal * w.reindex(d.index))[per.reindex(d.index) == 2020].sum()
    assert d["TDOnsiteGen_MW"].min() > 0.5           # the linked generator serves part of it
    added = summary(run)["actual_annual_load_twh"] - summary(base)["actual_annual_load_twh"]
    assert added == pytest.approx(mwh / 1e6, abs=1e-4)                # summary is rounded to 1e-4 TWh


def test_max_annual_ratio_holds_with_tracked_load(tmp_path):
    run = td_case(tmp_path, "max", before=("gen_zone_ratio",), onsite=False, storage=False,
                  after=chain(linked, ratio_file(max_annual_ratio=0.9)))
    s = summary(run)
    assert s["actual_annual_ratio"] <= 0.9 + 1e-3


def test_max_peak_share_uses_tracked_load(tmp_path):
    """Every hour, North's generation (linked generator included) <= 0.9 x (demand + tracked withdrawal); it binds,
    and the generation it allows exceeds 0.9 x demand alone, so the tracked load is what makes room for it."""
    run = td_case(tmp_path, "peak", before=("gen_zone_ratio",), onsite=False, storage=False,
                  after=chain(linked, ratio_file(max_peak_share=0.9)))
    disp = read(run, "dispatch.csv")
    gen = disp[disp["gen_load_zone"] == "North"].groupby("timestamp")["DispatchGen_MW"].sum()
    d = read(run, "tracked_demand_dispatch.csv").set_index("timestamp")
    loads = pd.read_csv(run / "inputs" / "loads.csv").query("LOAD_ZONE == 'North'").set_index("TIMEPOINT")
    tp = pd.read_csv(run / "inputs" / "timepoints.csv").set_index("timestamp")["timepoint_id"]
    demand = tp.reindex(d.index).map(loads["zone_demand_mw"])
    withdrawal = d["TDGridDraw_MW"] - d["TDGridExport_MW"] + d["TDOnsiteGen_MW"]
    gen = gen.reindex(d.index)
    assert (0.9 * (demand + withdrawal) - gen >= -1e-5).all()
    assert (0.9 * (demand + withdrawal) - gen).min() < 1e-4
    assert (gen > 0.9 * demand + 1e-3).any()
