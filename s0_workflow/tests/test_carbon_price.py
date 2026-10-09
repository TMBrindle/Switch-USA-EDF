"""Carbon allowance clearing price with CCR tiers (CHANGES §88; switch/study_modules/carbon_policies_regional.py).
Hand-built numbers and the 3zone_toy example: fixtures, not results."""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _module():
    pytest.importorskip("switch_model")
    spec = importlib.util.spec_from_file_location("cpr", REPO / "switch/study_modules/carbon_policies_regional.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tiers(p1, p2, rent1=None, pool=10e6, t1=23.0, t2=46.0):
    return [{"trigger": t1, "pool": pool, "purchases": p1, "rent": rent1},
            {"trigger": t2, "pool": pool, "purchases": p2, "rent": None}]


def test_price_rule_cases():
    f = _module().ccr_clearing_price
    # no tier used: the cap dual (scarcity or the floor); residuals below the relative threshold don't count
    assert f(17.5, _tiers(0.0, 0.0)) == (17.5, "cap_dual")
    assert f(17.5, _tiers(40.0, 0.0)) == (17.5, "cap_dual")                 # 40 t of a 10 Mt pool: a residual
    # tier 1 partly used, with a barrier residual on tier 2 (crossover off): tier 1's trigger, not tier 2's (the old
    # rule took the highest tier with purchases > 0.001 t and reported 46)
    assert f(23.2, _tiers(3e6, 50.0)) == (23.0, "ccr_tier1_trigger")
    # tier 1 exhausted, tier 2 partly used: tier 1 + its rent = tier 2's trigger
    assert f(46.0, _tiers(10e6, 4e6, rent1=23.0)) == (46.0, "ccr_tier1_trigger_plus_rent")
    # both exhausted: the price is above tier 2's trigger (the old rule reported 46, understating it)
    assert f(61.0, _tiers(10e6, 10e6, rent1=38.0)) == (61.0, "ccr_tier1_trigger_plus_rent")
    # tier 1 exhausted but no reduced cost: the cap dual; nothing at all: None
    assert f(61.0, _tiers(10e6, 10e6)) == (61.0, "cap_dual")
    assert f(None, _tiers(10e6, 10e6)) == (None, "cap_dual")
    # "exhausted" is relative too: 1 t short of a 10 Mt pool is exhausted
    assert f(50.0, _tiers(10e6 - 1, 2e6, rent1=27.0))[1] == "ccr_tier1_trigger_plus_rent"


def _toy(tmp_path, name, cap, tiers):
    """3zone_toy with one hard-cap program over all zones (carbon_policies_regional) and CCR tiers [(pool, trigger)];
    write_dual_costs loaded so dual_costs.csv is written."""
    from toyutil import solve, toy_inputs

    def edit(inp):
        zones = list(pd.read_csv(inp / "load_zones.csv").LOAD_ZONE)
        periods = list(pd.read_csv(inp / "periods.csv").INVESTMENT_PERIOD)
        pd.DataFrame([{"CO2_PROGRAM": "ETS 1", "PERIOD": p, "LOAD_ZONE": z,
                       "carbon_cap_tco2_per_yr": cap[p] / len(zones), "carbon_cost_dollar_per_tco2": ".",
                       "carbon_floor_price_dollar_per_tco2": 0.0} for p in periods for z in zones]).to_csv(
            inp / "carbon_policies_regional.csv", index=False)
        if tiers:
            pd.DataFrame([{"CO2_PROGRAM": "ETS 1", "PERIOD": p, "ccr_tier": k + 1, "ccr_pool_tco2_per_yr": pool[p],
                           "ccr_price_dollar_per_tco2": trig} for p in periods
                          for k, (pool, trig) in enumerate(tiers)]).to_csv(inp / "carbon_policies_ccr.csv", index=False)
    run = toy_inputs(tmp_path, name, ["carbon_policies_regional", "write_dual_costs"], edit)
    return solve(run)


def test_toy_prices_from_duals(tmp_path):
    big = {2020: 1e12, 2030: 1e12}
    out = _toy(tmp_path, "free", big, None)
    e = pd.read_csv(out / "carbon_program_clearing_prices.csv").set_index("PERIOD").emissions_tco2_per_yr
    assert (e > 0).all()

    def run(name, cap_share, tiers):
        cap = {p: cap_share * e[p] for p in e.index}
        res = pd.read_csv(_toy(tmp_path, name, cap, [({p: s * e[p] for p in e.index}, trig) for s, trig in tiers])
                          / "carbon_program_clearing_prices.csv").set_index("PERIOD")
        return res, pd.read_csv(tmp_path / name / "outputs/dual_costs.csv")

    # both tiers exhausted (cap 50%, pools 5% each at $1 and $2): the price is above tier 2's trigger, equals the
    # cap-dual price, and the cap's dual is in dual_costs.csv
    r, dc = run("both", 0.5, [(0.05, 1.0), (0.05, 2.0)])
    for p in e.index:
        x = r.loc[p]
        assert x.ccr_tier1_purchases_tco2 == pytest.approx(x.ccr_tier1_pool_tco2, abs=1)   # output rounds to t
        assert x.ccr_tier2_purchases_tco2 == pytest.approx(x.ccr_tier2_pool_tco2, abs=1)
        assert x.price_source == "ccr_tier1_trigger_plus_rent"
        assert x.clearing_price_dollar_per_tco2 > 2.0 + 1e-3                        # the old rule reported 2.0
        assert x.clearing_price_dollar_per_tco2 == pytest.approx(x.cap_dual_price_dollar_per_tco2, rel=1e-4)
        assert x.ccr_tier1_rent_dollar_per_tco2 == pytest.approx(x.clearing_price_dollar_per_tco2 - 1.0, rel=1e-4)
    cap_rows = dc[dc.constraint == "Enforce_Regional_Carbon_Cap"]
    assert len(cap_rows) == len(e) and (cap_rows.dual != 0).all()
    # tier 1 partly used (cap 90%, a $0.01 tier 1 pool of 50%): tier 1's trigger, equal to the cap dual
    r, dc = run("partial", 0.9, [(0.5, 0.01), (0.5, 1000.0)])
    for p in e.index:
        x = r.loc[p]
        assert 0 < x.ccr_tier1_purchases_tco2 < x.ccr_tier1_pool_tco2 and x.ccr_tier2_purchases_tco2 == 0
        assert x.price_source == "ccr_tier1_trigger" and x.clearing_price_dollar_per_tco2 == pytest.approx(0.01)
        assert x.cap_dual_price_dollar_per_tco2 == pytest.approx(0.01, rel=1e-4)
    # no CCR drawn (cap 90% and tier 1 dearer than abating): the cap dual; the cap row is in dual_costs.csv
    r, dc = run("nodraw", 0.9, [(0.05, 1e4), (0.05, 2e4)])
    assert (r.price_source == "cap_dual").all() and (r.ccr_tier1_purchases_tco2 == 0).all()
    assert ((r.clearing_price_dollar_per_tco2 > 0) & (r.clearing_price_dollar_per_tco2 < 1e4)).all()
    assert len(dc[dc.constraint == "Enforce_Regional_Carbon_Cap"]) == len(e)
