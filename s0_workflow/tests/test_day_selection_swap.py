"""The S0 v3.2 2028 failure, reproduced on a synthetic record, and the fix: swapping k-means cluster representatives
when the medoids give no feasible weights (CHANGES §240). Synthetic records are test fixtures only (never results).

The real case (fi_elastic_slack.csv from the VM): with the PV repair on, the medoids' weights LP is infeasible at
x2.25 on one regional onshore-wind target (FRCC, below), although that target is reachable alone. The repair changes
the solar shapes that feed k-means, so the candidate days change, and the 49 targets no longer fit together. Here:
eleven transregs (35 targets for 15 weighted days); the "unrepaired" record has solar zeroed on 8% of days in three
western regions (missing data), the "repaired" record has it filled. The unrepaired record passes on its medoids;
the repaired one fails on one regional wind target (below) unless representatives are swapped."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))
from s0_workflow import day_selection as ds  # noqa: E402
from test_s0_production import _case_settings  # noqa: E402

pytest.importorskip("sklearn")
ZONES = {"CAISO": "p9", "ERCOT": "p60", "FRCC": "p101", "ISONE": "p129", "MISO": "p37", "NYISO": "p127",
         "NorthernGrid": "p1", "PJM": "p80", "SERTP": "p88", "SPP": "p35", "WestConnect": "p23"}


def _record(seed=4, days=365, repaired=True):
    rng = np.random.default_rng(seed)
    H = days * 24
    h = np.arange(H)
    L, R, rows = {}, {}, []
    for i, z in enumerate(ZONES.values()):
        L[z] = 1000 + 300 * np.sin(2 * np.pi * h / H + i) + 200 * np.sin(2 * np.pi * h / 24) + rng.normal(0, 40, H)
        day_w = np.repeat(rng.normal(0, 0.15, days), 24)
        R[f"w{z}"] = np.clip(0.35 + 0.1 * np.sin(2 * np.pi * h / H + 2 * i) + day_w + 0.1 * rng.standard_normal(H),
                             0, 1)
        cloud = np.repeat(np.clip(1 - np.abs(rng.normal(0, 0.3, days)), 0, 1), 24)
        R[f"s{z}"] = (np.clip(np.sin(2 * np.pi * (h % 24 - 6) / 24), 0, None)
                      * (0.8 + 0.2 * np.sin(2 * np.pi * h / H + i)) * cloud)
        rows += [(f"w{z}", "LandbasedWind_Class3_Moderate", z), (f"s{z}", "UtilityPV_Class1_Moderate", z)]
    L, R = pd.DataFrame(L), pd.DataFrame(R)
    if not repaired:
        miss = np.repeat(np.random.default_rng(100 + seed).random(days) < 0.08, 24)
        for z in ("p9", "p1", "p23"):
            R.loc[miss, f"s{z}"] = 0.0
    return L, R, pd.DataFrame(rows, columns=["Resource", "technology", "region"])


def _ts(**kw):
    ts = ds.ts_settings(_case_settings())
    ts.update(n_days=16, kmeans_n_init=3, **kw)
    return ts


def test_unrepaired_passes_on_its_medoids(tmp_path):
    L, R, g = _record(repaired=False)
    _, rep, w = ds.select_days(R, L, g, _ts(), tmp_path / "u")
    info = json.load(open(tmp_path / "u/fi_info.json"))
    assert "candidate_swaps" not in info and not (tmp_path / "u" / ds.SWAPS_CSV).exists()
    assert not (tmp_path / "u" / ds.SLACK_CSV).exists()
    assert "cluster member (swapped for the medoid)" not in set(pd.read_csv(tmp_path / "u/fi_days_selected.csv").role)


def test_repaired_medoids_fail_on_one_wind_target(tmp_path):
    """Without the swap (swap_max 0) the build stops as on the VM: one regional wind target, below, reachable alone."""
    L, R, g = _record()
    with pytest.raises(RuntimeError, match="no swap of cluster representatives helps"):
        ds.select_days(R, L, g, _ts(swap_max=0), tmp_path / "f")
    tab = pd.read_csv(tmp_path / "f" / ds.SLACK_CSV)
    a = tab[tab.run == "all"]
    assert len(a) == 1 and a.family.iloc[0] == "target" and a.constraint.iloc[0].startswith("wind CF ")
    assert a.constraint.iloc[0] != "wind CF national" and a.side.iloc[0] == "below"
    assert not tab[tab.run == "only tail"].feasible.iloc[0] and not tab[tab.run == "only w_min"].feasible.iloc[0]
    assert pd.read_csv(tmp_path / "f" / ds.RANGE_CSV)["reachable alone"].all()        # a joint conflict


def test_repaired_feasible_by_swapping_representatives(tmp_path):
    """The fix: the same targets, tails, w_min and tolerance schedule; only medoids are replaced by other members of
    their own clusters. The medoids' diagnosis is still written."""
    L, R, g = _record()
    d = tmp_path / "r"
    res, rep, w = ds.select_days(R, L, g, _ts(), d)
    info = json.load(open(d / "fi_info.json"))
    sw = pd.read_csv(d / ds.SWAPS_CSV)
    assert 1 <= len(sw) <= ds.SWAP_MAX and info["candidate_swaps"] == [f"p{a}->p{b}" for a, b in zip(sw["from day"],
                                                                                                     sw["to day"])]
    assert info["relax_factor"] in (1.0, 1.5, 2.25) and (sw["tolerance factor"] == info["relax_factor"]).all()
    assert (d / ds.SLACK_CSV).exists()
    err = pd.read_csv(d / "fi_target_errors.csv")
    assert err.within.all() and len(err) == 2 * len(ZONES) + len(ZONES) + 3        # every target kept
    base = {n: v for n, v in zip(err.target, err.tolerance / info["relax_factor"])}
    assert base["wind CF SPP"] == pytest.approx(0.010) and base["solar CF CAISO"] == pytest.approx(0.005)  # unchanged
    tails = pd.read_csv(d / "fi_tail_shares.csv")
    lo, hi = tails["band %"].str.split("-", expand=True).astype(float).T.values
    assert ((tails["sample share %"] >= lo - 1e-6) & (tails["sample share %"] <= hi + 1e-6)).all()
    assert sum(w) == pytest.approx(365.0) and min(w[:-1]) >= 1.0 - 1e-9
    sel = pd.read_csv(d / "fi_days_selected.csv")
    swapped = sel[sel.role == "cluster member (swapped for the medoid)"]
    assert set(swapped.slot) <= {f"p{x}" for x in sw["to day"]} and len(swapped) >= 1
    # the tail days and the peak day are those the medoid selection had
    fixed = sel[sel.role.isin(["top load", "low net load", "peak load (PG convention)"])].slot.tolist()
    assert len(fixed) == 3 + 3 + 1 and not set(fixed) & {f"p{x}" for x in sw["from day"]}
    # deterministic
    _, rep2, w2 = ds.select_days(R, L, g, _ts(), tmp_path / "r2")
    assert rep2.equals(rep) and np.allclose(w2, w)


def test_swaps_stay_in_their_clusters(tmp_path):
    """Each swap replaces a day by another member of the same k-means cluster (checked on the clustering itself)."""
    from sklearn.cluster import KMeans
    L, R, g = _record()
    ts = _ts()
    P = ds._prepare(R, L, g, ts, None, True)
    feats = [(x - x.mean()) / (x.std() + 1e-9) for x in (P["day"](v) for n, v in P["S"].items() if "national" not in n)]
    feat = np.hstack(feats)
    K = ts["n_days"] - ts["n_top_load_days"] - ts["n_low_net_load_days"]
    km = KMeans(n_clusters=K, n_init=ts["kmeans_n_init"], random_state=ts["seed"]).fit(feat)
    d = tmp_path / "c"
    ds.select_days(R, L, g, ts, d)
    sw = pd.read_csv(d / ds.SWAPS_CSV)
    for a, b, k in zip(sw["from day"], sw["to day"], sw["cluster"]):
        assert km.labels_[int(a) - 1] == km.labels_[int(b) - 1] == int(k)
