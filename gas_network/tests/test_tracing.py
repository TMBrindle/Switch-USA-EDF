import numpy as np
import pandas as pd
import pytest

from gasnet import texas, tracing


def _sh(t, hub):
    return t[t.hub == hub].set_index("basin").share


def test_chain_known_answer():
    # A produces 10 of basin a and ships 6 to B; B produces 4 of b and ships 5 to C; C produces nothing.
    sup = pd.DataFrame({"hub": ["A", "B"], "basin": ["a", "b"], "value": [10.0, 4.0]})
    fl = pd.DataFrame({"src": ["A", "B"], "dst": ["B", "C"], "value": [6.0, 5.0]})
    t = tracing.trace(sup, fl, ["A", "B", "C"], ["a", "b"])
    assert _sh(t, "A").to_dict() == {"a": 1.0, "b": 0.0}
    np.testing.assert_allclose(_sh(t, "B").values, [0.6, 0.4])     # 6 of a + 4 of b
    np.testing.assert_allclose(_sh(t, "C").values, [0.6, 0.4])     # carries B's mix
    assert t[t.hub == "B"].throughput.iloc[0] == 10.0


def test_loop_known_answer():
    # two nodes trading both ways: A: 9 of a, receives 3 from B; B: 3 of b, receives 6 from A.
    # s_A = (9 e_a + 3 s_B) / 12, s_B = (3 e_b + 6 s_A) / 9  ->  s_A[a] = 9/10, s_B[a] = 3/5
    sup = pd.DataFrame({"hub": ["A", "B"], "basin": ["a", "b"], "value": [9.0, 3.0]})
    fl = pd.DataFrame({"src": ["A", "B"], "dst": ["B", "A"], "value": [6.0, 3.0]})
    t = tracing.trace(sup, fl, ["A", "B"], ["a", "b"])
    np.testing.assert_allclose(_sh(t, "A").values, [0.9, 0.1])
    np.testing.assert_allclose(_sh(t, "B").values, [0.6, 0.4])


def test_shares_sum_to_one_and_dead_node_nan():
    rng = np.random.default_rng(1)
    nodes = list("ABCDEFG")
    sup = pd.DataFrame({"hub": nodes[:4], "basin": ["x", "y", "z", "x"], "value": rng.uniform(1, 9, 4)})
    pairs = [(a, b) for a in nodes[:6] for b in nodes[:6] if a != b]
    fl = pd.DataFrame(pairs, columns=["src", "dst"]).assign(value=rng.uniform(0, 3, len(pairs)))
    t = tracing.trace(sup, fl, nodes, ["x", "y", "z"])
    s = t.groupby("hub").share.sum(min_count=1)
    np.testing.assert_allclose(s[nodes[:6]].values, 1.0)
    assert np.isnan(s["G"])                                          # no supply, no receipts


def test_rejects_unknown_node_and_negative():
    sup = pd.DataFrame({"hub": ["A"], "basin": ["a"], "value": [1.0]})
    with pytest.raises(ValueError):
        tracing.trace(sup, pd.DataFrame({"src": ["A"], "dst": ["Z"], "value": [1.0]}), ["A"], ["a"])
    with pytest.raises(ValueError):
        tracing.trace(sup, pd.DataFrame({"src": ["A"], "dst": ["A"], "value": [-1.0]}), ["A"], ["a"])


TX = {"capacity_mmcfd": {"TX_E->TX_N": 1, "TX_E->TX_W": 1, "TX_N->TX_E": 10, "TX_N->TX_W": 1,
                         "TX_W->TX_E": 10, "TX_W->TX_N": 10},
      "arc_cost": {a: 1.0 for a in ["TX_E->TX_N", "TX_E->TX_W", "TX_N->TX_E", "TX_N->TX_W", "TX_W->TX_E", "TX_W->TX_N"]}}


def test_texas_route_direct_and_scaled():
    sup = {"TX_W": 100.0, "TX_N": 20.0, "TX_E": 30.0}
    disp = {"TX_W": 10.0, "TX_N": 20.0, "TX_E": 90.0}       # total 120 vs supply 150 -> scale 1.25
    fl, info = texas.route(sup, disp, TX, days=10)
    assert info["disposition_scale"] == pytest.approx(1.25)
    f = {(r.src, r.dst): r.value for r in fl.itertuples()}
    # scaled demand W 12.5, N 25, E 112.5: W ships 87.5 (5 to N, 82.5 to E), all direct
    assert f[("TX_W", "TX_E")] == pytest.approx(82.5)
    assert f[("TX_W", "TX_N")] == pytest.approx(5.0)
    assert info["capacity_slack_mmcf"] == pytest.approx(0.0)


def test_texas_route_capacity_slack_reported():
    sup = {"TX_W": 300.0, "TX_N": 0.0, "TX_E": 0.0}
    disp = {"TX_W": 0.0, "TX_N": 0.0, "TX_E": 300.0}
    fl, info = texas.route(sup, disp, TX, days=10)          # W->E 100 + W->N->E 100 within capacity; 100 slack
    assert info["capacity_slack_mmcf"] == pytest.approx(100.0)
    assert fl.value.sum() == pytest.approx(400.0)            # 200 direct incl. slack + 100 two-hop counted twice


def test_assign_interstate_ok_split():
    tx = {"border_hub": {"LA": "TX_E", "NM": "TX_W"}, "ok_receipts_split": {"TX_N": 1, "TX_W": 3},
          "ok_deliveries_split": {"TX_W": 1}}
    f = pd.DataFrame({"src": ["OK", "TX", "NM", "TX"], "dst": ["TX", "OK", "TX", "LA"], "value": [8.0, 5.0, 2.0, 7.0]})
    out = texas.assign_interstate(f, tx).set_index(["src", "dst"]).value.to_dict()
    assert out == {("NM", "TX_W"): 2.0, ("OK", "TX_N"): 2.0, ("OK", "TX_W"): 6.0, ("TX_E", "LA"): 7.0,
                   ("TX_W", "OK"): 5.0}
