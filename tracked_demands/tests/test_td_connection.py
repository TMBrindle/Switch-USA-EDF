"""Grid connection of a tracked demand (§108 (f), Tom's decision D4): cost (zone network upgrade $/kW, multiplier or
own $/kW), bill share, optional endogenous size, optional headroom draw with its charge, carried through a chain, and
the counterfactual check."""
from __future__ import annotations

import shutil
import sys

import pandas as pd
import pytest

from td_toy import REPO, add_onsite_gen, cost, csv, read, set_td, td_case, td_chain



def crf(ir, n):
    return ir / (1 - (1 + ir) ** -n)


def interest(run):
    return float(pd.read_csv(run / "inputs" / "financials.csv")["interest_rate"].iat[0])


def zone_cost(per_kw=200.0, zone="North"):
    return lambda inp: csv(inp, "tracked_demand_zone_upgrade_costs.csv",
                           [{"LOAD_ZONE": zone, "td_zone_upgrade_cost_per_kw": per_kw}])


def chain(*fns):
    def f(inp):
        for fn in fns:
            fn(inp)
    return f


def conn(run, p=2020):
    c = read(run, "tracked_demand_connection_costs.csv")
    return c[c["period"] == p].iloc[0]


def ic_files(inp):
    for f in ("ic_zones.csv", "ic_tranches.csv", "ic_uprates.csv", "ic_params.csv", "ic_weights.csv"):
        shutil.copy(REPO / "interconnection_headroom/tests/switch_toy" / f, inp / f)


# ------------------------------------------------------------------------------------------------ cost
@pytest.mark.parametrize("cols,per_kw,source", [
    ({}, 200.0, "zone"),
    ({"td_connection_cost_multiplier": 1.5}, 300.0, "zone_x_multiplier"),
    ({"td_connection_cost_per_kw": 500.0}, 500.0, "own"),
])
def test_connection_cost(tmp_path, cols, per_kw, source):
    run = td_case(tmp_path, f"c{per_kw:.0f}", onsite=False, storage=False,
                  after=chain(zone_cost(), lambda i: set_td(i, **cols)) if cols else zone_cost())
    c = conn(run)
    expect = 2.0 * 1000 * per_kw * crf(interest(run), 40)
    assert c["cost_source"] == source and c["cost_per_kw_overnight"] == pytest.approx(per_kw)
    assert c["annual_cost_usd"] == pytest.approx(expect, rel=1e-9)
    ci = read(run, "costs_itemized.csv")
    row = ci[(ci["Component"] == "TDConnectionCost") & (ci["PERIOD"] == 2020)]
    assert float(row["AnnualCost_Real"].iat[0]) == pytest.approx(expect, rel=1e-6)


def test_cost_share_is_reporting_only(tmp_path):
    full = td_case(tmp_path, "s100", onsite=False, storage=False, after=zone_cost())
    quarter = td_case(tmp_path, "s25", onsite=False, storage=False,
                      after=chain(zone_cost(), lambda i: set_td(i, td_connection_cost_share_demand=0.25)))
    assert cost(quarter) == pytest.approx(cost(full), rel=1e-12)
    c = conn(quarter)
    assert c["paid_by_demand_usd"] == pytest.approx(0.25 * c["annual_cost_usd"])
    assert c["paid_by_other_customers_usd"] == pytest.approx(0.75 * c["annual_cost_usd"])


# ------------------------------------------------------------------------------------------------ endogenous size
@pytest.mark.parametrize("per_kw,expect_conn", [(1e5, 0.0), (0.0, None)])
def test_endogenous_connection(tmp_path, per_kw, expect_conn):
    """A dear connection is sized to 0 and a linked generator serves the load; a free one is used."""
    def after(inp):
        add_onsite_gen(inp)
        set_td(inp, td_connection_endogenous=1, td_connection_cost_per_kw=per_kw)
    run = td_case(tmp_path, f"endo{per_kw:.0f}", onsite=False, storage=False, after=after)
    c = conn(run)
    d = read(run, "tracked_demand_dispatch.csv")
    assert c["endogenous"] == 1
    if expect_conn is not None:
        assert c["connection_mw"] == pytest.approx(expect_conn, abs=1e-6)
        assert d["TDGridDraw_MW"].max() < 1e-6
    else:
        assert d["TDGridDraw_MW"].max() <= c["connection_mw"] + 1e-6


# ------------------------------------------------------------------------------------------------ headroom draw
def headroom_case(tmp_path, name, mw=0.5, draw=True, extra=()):
    def after(inp):
        ic_files(inp)
        set_td(inp, td_default_min_power_mw=mw, td_default_max_power_mw=mw, td_grid_interconnect_mw=mw,
               td_energy_requirement_mwh_per_year=0.0, td_connection_uses_headroom=int(draw))
    return td_case(tmp_path, name, onsite=False, storage=False, before=("interconnection_headroom",),
                   after=after, extra=extra)


def test_headroom_draw_and_charge(tmp_path):
    without = headroom_case(tmp_path, "nodraw", draw=False)
    with_draw = headroom_case(tmp_path, "draw")
    hw = read(with_draw, "ic_headroom.csv").query("load_zone == 'North'").set_index("period")
    hn = read(without, "ic_headroom.csv").query("load_zone == 'North'").set_index("period")
    # the draw (0.5 MW) is met by more headroom bought (or freed) in North
    more = (hw["headroom_bought_mw"] + hw["freed_headroom_mw"]) - (hn["headroom_bought_mw"] + hn["freed_headroom_mw"])
    assert (more >= 0.5 - 1e-6).all()
    c = read(with_draw, "tracked_demand_connection_costs.csv").set_index("period")
    assert (c["headroom_draw_mw"] - 0.5).abs().max() < 1e-9
    v = hw["headroom_value_usd_per_kw_yr"]
    assert (c["headroom_charge_usd"] - v * 1000 * 0.5).abs().max() < 1e-6
    assert (c["headroom_charge_usd"] > 0).all()


def test_counterfactual_check_matches_on_one_linear_piece(tmp_path):
    """The attributed charge (shadow price x MW) equals the objective difference against the no-draw solve while
    the draw stays on one linear piece of the headroom curve (0.1 MW here)."""
    sys.path.insert(0, str(REPO / "tracked_demands" / "scripts"))
    import headroom_counterfactual as hc
    run = td_case(tmp_path, "cf", onsite=False, storage=False, before=("interconnection_headroom",), solve=False,
                  after=lambda inp: (ic_files(inp), set_td(
                      inp, td_default_min_power_mw=0.1, td_default_max_power_mw=0.1, td_grid_interconnect_mw=0.1,
                      td_energy_requirement_mwh_per_year=0.0, td_connection_uses_headroom=1)))
    res = hc.main([str(run), "--outputs-dir", "out_cf", "--", "--solver", "appsi_highs", "--suffixes", "dual",
                   "--inputs-dir", "inputs"]).iloc[0]
    assert res["objective_difference"] > 0
    assert res["attributed_charge_npv"] == pytest.approx(res["objective_difference"], rel=1e-4)


def test_headroom_charge_over_a_chain(tmp_path):
    """Stage 2020 connects (new draw); stage 2030 carries the connection and its draw and charges it at 2030's
    headroom value; the summary splits the connect stage from later stages."""
    def after(inp):
        ic_files(inp)
        set_td(inp, td_default_min_power_mw=0.5, td_default_max_power_mw=0.5, td_grid_interconnect_mw=0.5,
               td_energy_requirement_mwh_per_year=0.0, td_connection_uses_headroom=1)
        zone_cost()(inp)
        # Switch's storage module (the chain helper's prepare_next_stage needs it) must come before
        # interconnection_headroom, whose generator weights read STORAGE_GENS
        lines = (inp / "modules.txt").read_text().splitlines()
        i = lines.index("mods.interconnection_headroom")
        (inp / "modules.txt").write_text(
            "\n".join(lines[:i] + ["switch_model.generators.extensions.storage"] + lines[i:]) + "\n")
    run, stages, outs = td_chain(tmp_path, "hchain", modules=("interconnection_headroom", "tracked_demands"),
                                 onsite=False, storage=False, after=after)
    ex = pd.read_csv(run / "in/2030/case/tracked_demand_connection_existing.chained.case.csv").iloc[0]
    assert ex["td_connection_existing_mw"] == pytest.approx(0.5) and ex["td_headroom_draw_existing_mw"] == pytest.approx(0.5)
    c2 = pd.read_csv(outs[1] / "tracked_demand_connection_costs.csv").iloc[0]
    assert c2["new_connection_mw"] == pytest.approx(0.0, abs=1e-9) and c2["headroom_draw_mw"] == pytest.approx(0.0)
    assert c2["carried_headroom_draw_mw"] == pytest.approx(0.5)
    led = pd.read_csv(outs[1] / "tracked_demand_headroom_ledger.csv")
    assert list(led["stage"].astype(str)) == ["2020", "2030"]
    s = pd.read_csv(outs[1] / "tracked_demand_headroom_summary.csv").iloc[0]
    assert s["headroom_charge_connect_stage_usd"] > 0
    assert s["headroom_charge_total_usd"] == pytest.approx(
        s["headroom_charge_connect_stage_usd"] + s["headroom_charge_later_stages_usd"])



def test_headroom_weight_inert_without_draw(tmp_path):
    """td_headroom_weight is used only when the draw is on: with td_connection_uses_headroom = 0 any weight gives the
    same solve and no draw (the case writer writes the spec's weight, default 1, either way)."""
    def run(w):
        return td_case(tmp_path, f"w{w}", onsite=False, storage=False, before=("interconnection_headroom",),
                       after=lambda inp: (ic_files(inp), set_td(
                           inp, td_default_min_power_mw=0.5, td_default_max_power_mw=0.5, td_grid_interconnect_mw=0.5,
                           td_energy_requirement_mwh_per_year=0.0, td_connection_uses_headroom=0,
                           td_headroom_weight=w)))
    runs = [run(1), run(7)]
    assert cost(runs[1]) == pytest.approx(cost(runs[0]), rel=1e-12)
    for r in runs:
        c = read(r, "tracked_demand_connection_costs.csv")
        assert (c["headroom_draw_mw"] == 0).all() and (c["headroom_charge_usd"] == 0).all()
