"""Infeasible day-weights LP: the elastic diagnosis, and offshore / degenerate clusters outside the selection
(CHANGES §139). Synthetic records are test fixtures only (never results)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))
from s0_workflow import day_selection as ds  # noqa: E402
from s0_workflow.scripts import diagnose_day_weights as dw  # noqa: E402
from test_s0_production import _case_settings, _record  # noqa: E402

pytest.importorskip("sklearn")
DIAG_FILES = (ds.LP_DUMP, ds.SLACK_CSV, ds.RANGE_CSV, ds.CLUSTER_CSV)


def _ts(**kw):
    ts = ds.ts_settings(_case_settings())
    ts.update(n_days=12, n_top_load_days=2, n_low_net_load_days=2, kmeans_n_init=3, **kw)
    return ts


def _with_offshore(R, gens, H):
    """The four offshore groups as the v3.2 build offers them, floating/pref-site-1 (the fourth entry) degenerate:
    all zero in one zone, near zero in another, constant in a third."""
    rng = np.random.default_rng(7)
    R = R.copy()
    R["off_fixed_0"] = np.clip(0.45 + 0.2 * rng.standard_normal(H), 0, 1)
    R["off_float_0"] = np.clip(0.5 + 0.2 * rng.standard_normal(H), 0, 1)
    R["off_float_1_zero"] = 0.0
    R["off_float_1_tiny"] = np.abs(rng.normal(0, 1e-9, H))
    R["off_float_1_const"] = 0.37
    extra = pd.DataFrame({"Resource": ["off_fixed_0", "off_float_0", "off_float_1_zero", "off_float_1_tiny",
                                       "off_float_1_const"],
                          "technology": ["OffShoreWind_Class3_Moderate_fixed_0", "OffShoreWind_Class12_Moderate_floating_0",
                                         "OffShoreWind_Class12_Moderate_floating_1",
                                         "OffShoreWind_Class12_Moderate_floating_1", "Offshore Wind Turbine"],
                          "region": ["p1", "p60", "p2", "p61", "p1"]})
    return R, pd.concat([gens, extra], ignore_index=True)


def test_offshore_and_degenerate_clusters_do_not_enter_the_selection(tmp_path):
    """Offshore wind (any group, the new floating_1 included, however degenerate its profile) is in neither the
    targets nor the tails, so the days and weights are those of the record without it. A feasible run writes none of
    the diagnosis files."""
    L, R, gens = _record()
    ts = _ts()
    _, rep, w = ds.select_days(R, L, gens, ts, tmp_path / "a")
    R2, gens2 = _with_offshore(R, gens, len(R))
    assert {ds.kind(t) for t in gens2.technology[-5:]} == {"offwind"}
    res2, rep2, w2 = ds.select_days(R2, L, gens2, ts, tmp_path / "b")
    assert rep2.equals(rep) and np.array_equal(w2, w)
    assert pd.read_csv(tmp_path / "a/fi_target_errors.csv").equals(pd.read_csv(tmp_path / "b/fi_target_errors.csv"))
    assert pd.read_csv(tmp_path / "a/fi_tail_shares.csv").equals(pd.read_csv(tmp_path / "b/fi_tail_shares.csv"))
    assert not any((tmp_path / d / f).exists() for d in ("a", "b") for f in DIAG_FILES)
    # the selected profiles carry the offshore columns through (an all-zero one as 1.0, PowerGenome's rule)
    assert (res2["resource_profiles"]["off_float_1_zero"] == 1.0).all()
    assert np.array_equal(res2["resource_profiles"]["off_fixed_0"].values,
                          R2["off_fixed_0"].values.reshape(-1, 24)[[int(x[1:]) - 1 for x in rep2.slot]].reshape(-1))


def test_infeasible_day_weights_diagnosed(tmp_path):
    """w_min x days above the year: only the w_min family can absorb it. The error names the
    diagnosis, the slack table says which families make it feasible and by how much, and the script reproduces it."""
    L, R, gens = _record()
    R2, gens2 = _with_offshore(R, gens, len(R))
    d = tmp_path / "x"
    with pytest.raises(RuntimeError, match="infeasible even with tolerances x2.25") as e:
        ds.select_days(R2, L, gens2, _ts(w_min=40.0), d)
    assert "elastic diagnosis" in str(e.value) and "only target: infeasible" in str(e.value)
    assert all((d / f).exists() for f in DIAG_FILES)
    tab = pd.read_csv(d / ds.SLACK_CSV)
    feas = tab.groupby("run").feasible.first()
    # weights summing to more than the year would push every load target out of tolerance, so only w_min can absorb it
    assert feas["only w_min"] and feas["all"]
    assert not (feas["only target"] or feas["only tail"] or feas["only weight_sum"])
    lp = dw.load(d / ds.LP_DUMP)
    excess = len(lp["slots"]) * 40.0 - lp["total"]
    for run in ("only w_min", "all"):
        wm = tab[(tab.run == run) & (tab.family == "w_min")]
        assert wm.slack.sum() >= excess - 1e-6 and set(wm.constraint) <= set(lp["slots"])
        assert set(tab[tab.run == run].family) == {"w_min"}
    # the script on the dump gives the same table; a what-if (w_min 1) needs no slack
    dw.main([str(d / ds.LP_DUMP), "--out", str(tmp_path / "again.csv")])
    pd.testing.assert_frame_equal(pd.read_csv(tmp_path / "again.csv"), tab, check_dtype=False, atol=1e-6)
    ok = dw.main([str(d / ds.LP_DUMP), "--w-min", "1"])
    assert (ok.constraint == "(no slack needed)").all() and ok.feasible.all()
    # the cluster stats: offshore listed but not in the targets; the degenerate floating_1 clusters flagged
    cs = pd.read_csv(d / ds.CLUSTER_CSV).set_index("Resource")
    assert not cs.loc[["off_float_1_zero", "off_float_1_tiny", "off_float_1_const"], "in targets"].any()
    assert cs.loc[["off_float_1_zero", "off_float_1_tiny", "off_float_1_const"], "degenerate"].all()
    assert not cs.loc[["w1", "w2", "s1", "s2", "off_fixed_0", "off_float_0"], "degenerate"].any()
    assert cs.loc[["w1", "w2", "s1", "s2"], "in targets"].all()


def test_unreachable_target_named():
    """A target outside what the candidate days can reach: the target family carries the slack, by the exact gap."""
    A = np.array([[1.0, 2.0, 3.0]]) / 365.0
    b, t = np.array([4.0]), np.array([0.1])
    tails = [("tail", np.array([0.1, 0.2, 0.3]), 0.0, 0.0, 100.0)]
    tab = ds.slack_table(A, b, t, ["solar CF X"], tails, 365.0, 1.0, 1.0, ["p1", "p2", "p3"])
    best_mean = (1 + 2 + 3 * 363) / 365
    s = tab[(tab.run == "all") & (tab.family == "target")]
    assert len(s) == 1 and s.side.iloc[0] == "below" and s.slack.iloc[0] == pytest.approx(4 - best_mean - 0.1)
    assert not tab[tab.run == "only tail"].feasible.iloc[0] and not tab[tab.run == "only w_min"].feasible.iloc[0]
    rng = ds.target_range(A, b, t, ["solar CF X"], 365.0, 1.0)
    assert not rng["reachable alone"].iloc[0]
    assert "unreachable from the candidates whatever the weights: solar CF X" in ds.summarise(tab, rng)


def test_diagnosis_only_on_the_failure_path():
    """The weights LP itself is untouched: the diagnosis is called only after the last relaxation step fails."""
    src = (REPO / "s0_workflow/day_selection.py").read_text(encoding="utf-8")
    body = src[src.index("def select_days("):src.index("def elastic_lp(")]
    i = body.index("if res.status != 0:\n        tl =")
    assert body.index("for attempt in range(steps + 1):") < i < body.index("diagnose_infeasible(", i)
    assert body.count("diagnose_infeasible(") == 1
