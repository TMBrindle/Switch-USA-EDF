"""Projection methods (§102): additive vs ratio rule, mass-balance repair, the traced-total identity, Colorado San Juan
split, district mixing, metadata and sensitivity outputs."""
import numpy as np
import pandas as pd
import pytest
from conftest import PKG, needs_raw

from gasnet import balance, load_config, network, reconcile, tracing

OUT = PKG / "outputs"


def test_move_rules():
    # 2024 bundle 100 Bcf (MMcf units), Table 64 2025 = 50 Bcf, year = 80 Bcf
    assert network._move(100e3, 50, 80, "ratio") == (160e3, None)
    assert network._move(100e3, 50, 80, "additive") == (130e3, None)
    assert network._move(100e3, 50, 0, "additive") == (50e3, None)
    assert network._move(10e3, 50, 0, "additive") == (0.0, "additive_floored_at_0")
    assert network._move(100e3, 0, 30, "ratio") == (100e3, "zero_in_table64_held")
    assert network._move(100e3, 0, 30, "additive") == (130e3, None)
    assert network._move(100e3, None, 30, "additive") == (100e3, "not_in_table64_held")


def test_traced_totals_equal_supply_even_when_overdrawn():
    # B ships more than it holds: the identity sum_h L_h s_hb = supply_b still holds (why the AEO production check
    # cannot discriminate methods); overdraw is what shows the inconsistency
    sup = pd.DataFrame({"hub": ["A", "B"], "basin": ["a", "b"], "value": [10.0, 2.0]})
    fl = pd.DataFrame({"src": ["A", "B"], "dst": ["B", "C"], "value": [4.0, 9.0]})
    t = tracing.trace(sup, fl, ["A", "B", "C"], ["a", "b"])
    bt = reconcile.basin_totals({"supply": sup, "flows": fl}, t).set_index("basin")
    np.testing.assert_allclose(bt.traced_mmcf, bt.supply_mmcf)
    assert bt.overdraw_mmcf.iloc[0] == pytest.approx(-3.0)          # B: 2 + 4 - 9


class _Inp:
    """Minimal stand-in for network.Inputs: 2 regions, flat AEO growth."""
    def __init__(self):
        self.cfg = {"aeo_index_base_year": 2025}
        self.region = {"PA": "R1", "OH": "R1", "KY": "R2", "TN": "R2"}

    def aeo_series(self, table, code):
        return pd.Series({2025: 1.0, 2030: 1.0})


def test_balance_removes_overdraw_and_keeps_interregional():
    nodes = ["PA", "OH", "KY", "TN"]
    base_sup = pd.DataFrame({"hub": ["PA"], "basin": ["a"], "value": [10.0], "kind": "production"})
    base_fl = pd.DataFrame({"src": ["PA", "OH", "KY"], "dst": ["OH", "KY", "TN"], "value": [8.0, 6.0, 3.0],
                            "kind": "interstate"})
    base = {"supply": base_sup, "flows": base_fl}
    # projected: PA doubles; interregional OH->KY tripled (fixed), intra arcs not grown enough -> OH overdrawn
    sup = base_sup.assign(value=[20.0])
    fl = base_fl.assign(value=[9.0, 18.0, 4.0])
    net = {"year": 2030, "supply": sup, "flows": fl}
    L = balance.local_disposition(sup, fl, nodes)
    assert L["OH"] < 0
    new, info = balance.balance(_Inp(), base, net, nodes)
    L2 = balance.local_disposition(new["supply"], new["flows"], nodes)
    assert (L2 >= -1e-6).all() and info["overdraw_after_mmcf"] == pytest.approx(0)
    f = new["flows"].set_index(["src", "dst"]).value
    assert f[("OH", "KY")] == pytest.approx(18.0)                   # interregional bundle untouched
    # PA (20 supply) keeps its floor of 2 and ships 18; the fixed 18 out of OH leaves OH's floor (2) unmet -> slack
    assert f[("PA", "OH")] == pytest.approx(18.0)
    assert info["slack_hubs"] == {"OH": pytest.approx(2.0)}


@needs_raw
def test_colorado_san_juan_split():
    cfg = load_config()
    inp = network.Inputs(cfg)
    co = network.hsm_san_juan(inp.raw / "nems", "CO")
    assert co["san_juan"] / (co["san_juan"] + co["other"]) == pytest.approx(0.275, abs=0.005)
    for st in ("UT", "AZ"):
        assert network.hsm_san_juan(inp.raw / "nems", st)["san_juan"] == 0
    # validation of the HSM method on New Mexico against the reserves report (NM West 506 / 3,585 = 14.1%)
    nm = network.hsm_san_juan(inp.raw / "nems", "NM")
    assert nm["san_juan"] / (nm["san_juan"] + nm["other"]) == pytest.approx(506 / 3585, abs=0.015)
    sh = network._area_shares(inp).set_index("area").share_of_state
    assert sh["CO_SJ"] + sh["CO_OTHER"] == pytest.approx(1.0)


@needs_raw
def test_district_mixing():
    from gasnet import limitations
    inp = network.Inputs(load_config())
    m = limitations.district_mixing(inp, network.base_network(inp)["supply"]).set_index("area")
    assert m.loc["TX_D4", "non_shale_pct"] == pytest.approx(100 * (1154 - 679) / 1154)
    assert set(m[m.moves_between_basins].index) == {"TX_D1", "TX_D2", "TX_D4"}
    assert set(m[m.non_shale_pct > 30].index) >= {"TX_D3", "TX_D4", "TX_D7B", "TX_D8A", "TX_D10"}


def test_committed_sensitivity_and_metadata():
    import json
    s = pd.read_csv(OUT / "zone_basin_shares_sensitivity_ratio.csv")
    np.testing.assert_allclose(s.groupby(["zone", "year"]).share.sum().values, 1.0, atol=1e-5)
    assert set(s.method) == {"trace_aeo_ratio_balanced"}
    z = pd.read_csv(OUT / "zone_basin_shares.csv")
    assert set(z[z.year > 2024].method) == {"trace_aeo_additive_balanced"}
    meta = json.load(open(OUT / "zone_basin_shares.meta.json"))
    ids = [k["id"] for k in meta["known_limitations"]]
    assert "district_to_basin_labelling" in ids and "colorado_san_juan_model_based" in ids
    r = pd.read_csv(OUT / "projection_reconciliation.csv")
    assert (r[r.method.str.endswith("_balanced")].overdraw_after_mmcf > -1).all()
