"""Chronological multi-day blocks in the fleet-independent day selector (sample: NxL; CHANGES §56).

Synthetic records are test fixtures only (never results)."""
import ast
import json
import logging
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))
from s0_workflow import day_selection as ds  # noqa: E402
from s0_workflow import prm  # noqa: E402
from s0_workflow import production as s0prod  # noqa: E402
from test_s0_production import _case_settings, _record  # noqa: E402

pytest.importorskip("sklearn")


def _ts(sample="4x3", **kw):
    s = _case_settings()
    s["s0_production"]["time_sampling"]["sample"] = sample
    ts = ds.ts_settings(s)
    ts.update(kmeans_n_init=3, block_pool=8, n_top_load_days=2, n_low_net_load_days=2, **kw)
    return ts


def _ts_tp_pg_kmeans():
    src = (REPO / "conversion_functions.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "ts_tp_pg_kmeans")
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "dates_in_year" for t in n.targets))]
    g = {"pd": pd, "math": math, "List": list}
    exec(compile(ast.Module(nodes, []), "conversion_functions", "exec"), g)
    return g["ts_tp_pg_kmeans"]


def test_sample_setting():
    assert ds.parse_sample(24) == (24, 1) and ds.parse_sample("4x3") == (4, 3) and ds.parse_sample("6X2") == (6, 2)
    for bad in ("4x", "x3", "0x3", "four"):
        with pytest.raises(ValueError):
            ds.parse_sample(bad)
    # absent: the day mode, untouched (no block keys)
    ts = ds.ts_settings(_case_settings())
    assert ts["n_days"] == 24 and "block_days" not in ts
    ts = _ts("4x3")
    assert (ts["n_days"], ts["block_days"]) == (4, 3)
    assert (_ts(24)["n_days"], _ts(24)["block_days"]) == (24, 1)


def test_blocks_meet_targets_and_tails(tmp_path):
    L, R, gens = _record(days=365)
    ts = _ts()
    res, rep, w = ds.select_days(R, L, gens, ts, tmp_path / "d")
    assert list(rep.n_days) == [3, 3, 3, 3, 1] and rep.slot.iloc[:4].str.match(r"^p\d+x3$").all()
    starts = list(rep.start_day.iloc[:4])
    peak = int(rep.start_day.iloc[4])
    assert starts == sorted(starts) and all(b - a >= 3 for a, b in zip(starts, starts[1:]))
    assert not any(s <= peak < s + 3 for s in starts)                       # no block holds the peak day
    # weights: occurrences a year; blocks x 3 days + the peak day = 365 days, each block at least w_min times
    assert sum(x * 3 for x in w[:4]) + w[4] == pytest.approx(365.0) and min(w[:4]) >= ts["w_min"] - 1e-9
    assert res["ClusterWeights"] == w and len(res["load_profiles"]) == 13 * 24
    hrs = np.concatenate([np.arange(s * 24, (s + 3) * 24) for s in starts] + [np.arange(peak * 24, peak * 24 + 24)])
    assert np.allclose(res["load_profiles"].values, L.values[hrs]) and (res["resource_profiles"].gas == 1.0).all()
    err = pd.read_csv(tmp_path / "d/fi_target_errors.csv")
    info = json.load(open(tmp_path / "d/fi_info.json"))
    assert info["relax_factor"] == 1.0 and not info["relaxed"] and info["sample"] == "4x3"
    assert err.within.all() and (err["error / base tolerance"] <= 1 + 1e-9).all()
    tails = pd.read_csv(tmp_path / "d/fi_tail_shares.csv")
    lo, hi = tails["band %"].str.split("-", expand=True).astype(float).T.values
    assert ((tails["sample share %"] >= lo - 1e-6) & (tails["sample share %"] <= hi + 1e-6)).all()
    sel = pd.read_csv(tmp_path / "d/fi_days_selected.csv")
    assert sel.weight_days_per_yr.sum() == pytest.approx(365.0) and sel.role.iloc[-1].startswith("peak")
    # fleet-independent and deterministic
    res2, rep2, w2 = ds.select_days(R, L, gens, ts, tmp_path / "d2")
    assert rep2.equals(rep) and np.allclose(w2, w)


def test_blocks_relax_only_when_infeasible(tmp_path, caplog):
    L, R, gens = _record(days=365)
    tight = {"load_rel": 1e-5, "wind_cf": 1e-5, "solar_cf": 1e-5}
    with caplog.at_level(logging.WARNING):
        ds.select_days(R, L, gens, _ts(tolerances=tight), tmp_path / "t")
    info = json.load(open(tmp_path / "t/fi_info.json"))
    assert info["relaxed"] and info["relax_factor"] > 1 and info["relax_factor"] >= info["min_relax_lower_bound"] - 1e-9
    # the factor is the first relaxation step at which some set is feasible
    assert info["relax_factor"] / 1.5 < info["min_relax_lower_bound"] or info["relax_factor"] == pytest.approx(1.5)
    assert "relaxed" in caplog.text
    err = pd.read_csv(tmp_path / "t/fi_target_errors.csv")
    assert err.within.all() and (err["tolerance"] / err["base tolerance"]).round(9).eq(round(info["relax_factor"], 9)).all()
    with pytest.raises(RuntimeError, match="no feasible selection"):
        ds.select_days(R, L, gens, _ts(tolerances=tight, block_relax_max_steps=0), tmp_path / "t0")


def test_block_timeseries_ids_and_stress_days(tmp_path):
    ts_tp_pg_kmeans = _ts_tp_pg_kmeans()
    # single-day slots: the same tables as PowerGenome's builder
    rep1 = pd.DataFrame({"slot": ["p11", "p201", "p2000"]})
    a = ts_tp_pg_kmeans(rep1.slot, [100.0, 150.0, 115.0], 1, 2035, 2031)
    b = ds.ts_tp_blocks(rep1, [100.0, 150.0, 115.0], 2035, 2031)
    for x, y in zip(a, b):
        pd.testing.assert_frame_equal(x.reset_index(drop=True), y[x.columns].reset_index(drop=True), check_dtype=False)
    # blocks + peak day + stress days appended by the PRM (zero weight, own ids)
    rng = np.random.default_rng(3)
    H = 7 * 365 * 24
    lc = pd.DataFrame({"p120": 100 + rng.normal(0, 1, H), "p65": 90 + rng.normal(0, 1, H)})
    var = pd.DataFrame({"r_wind": rng.uniform(0, 1, H), "r_pv": rng.uniform(0, 1, H)})
    gens = pd.DataFrame({"Resource": ["r_wind", "r_pv"], "technology": ["LandbasedWind_Class3", "UtilityPV_Class1"],
                         "region": ["p120", "p65"]})
    rep = pd.DataFrame({"slot": ["p100x3", "p400x3", "p250"], "start_day": [99, 399, 249], "n_days": [3, 3, 1]})
    hrs = np.concatenate([np.arange(99 * 24, 102 * 24), np.arange(399 * 24, 402 * 24), np.arange(249 * 24, 250 * 24)])
    res = {"load_profiles": lc.iloc[hrs], "resource_profiles": var.iloc[hrs], "ClusterWeights": [60.0, 61.0, 0.5]}
    s = {"model_year": 2035, "s0_production": {"enabled": True, "prm": {"design": "regional"}}}
    out, rep2, w2, n = prm.add_stress_days(res, rep, [60.0, 61.0, 0.5], lc, var, gens, s, tmp_path)
    assert n >= 1 and w2[-n:] == [0.0] * n and len(out["load_profiles"]) == (7 + n) * 24
    assert ds.sample_is_blocks(rep2)
    rep2.loc[len(rep2) - 1, ["slot", "start_day", "n_days"]] = ["p101", np.nan, np.nan]   # inside a block
    t, p = ds.ts_tp_blocks(rep2, w2, 2035, 2031)
    t, p = prm.rename_stress_rows(t, p, n)
    assert list(t.ts_num_tps) == [72, 72, 24] + [24] * n and len(p) == (7 + n) * 24
    assert t.timeseries.is_unique and p.timepoint_id.is_unique and p.timepoint_id.astype("int64").is_unique
    assert list(t.ts_scale_to_period.iloc[:3]) == [300.0, 305.0, 2.5] and (t.ts_scale_to_period.iloc[-n:] == 0).all()
    blk = p[p.timeseries == "2035_p100x3"].timepoint_id
    assert len(blk) == 72 and blk.iloc[0] == "20351041000" and blk.iloc[-1] == "20351041223"   # weather year 1, Apr 10-12
    assert t.timeseries.iloc[-1] == "2035_p101_prm" and p.timepoint_id.iloc[-1] == "920351041123"


def test_case_pair_and_axis():
    ax = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"]
    assert ax["time_sample"] == {"days24": None, "blocks4x3": {"s0_production": {"time_sampling": {"sample": "4x3"}}}}
    assert ax["s0_production"]["on_single"] == {"s0_production": {"enabled": True, "foresight": {"mode": "single"}}}
    si = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv")
    assert (si.loc[~si.case_id.isin(["s4x1_S0prod_2035_fi4x3"]), "time_sample"] == "days24").all()
    a = si[si.case_id == "s4x1_S0prod_2035_fi24"].iloc[0]
    b = si[si.case_id == "s4x1_S0prod_2035_fi4x3"].iloc[0]
    assert [c for c in si.columns if a[c] != b[c]] == ["case_id", "time_sample"]
    assert a.year == 2035 and a.time_series == "s4x1" and a.s0_production == "on_single" and a.prm_design == "legacy"
    tx = si[si.case_id == "s4x1_S0prod_2035_txreeds"].iloc[0]
    assert [c for c in si.columns if a[c] != tx[c]] == ["case_id", "s0_production"]
    # merged settings: the fleet-independent selector in both, blocks only in fi4x3; today's (legacy) reserve
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    for val, expect in (("days24", (24, None)), ("blocks4x3", (4, 3))):
        s = s0prod.deep_merge({"s0_production": s0}, ax["s0_production"]["on_single"])
        s = s0prod.deep_merge(s, ax["prm_design"]["legacy"])
        if ax["time_sample"][val]:
            s = s0prod.deep_merge(s, ax["time_sample"][val])
        ts = ds.ts_settings(s)
        assert ts["method"] == "fleet_independent" and (ts["n_days"], ts.get("block_days")) == expect
        assert prm.year_prm(s) is None


def test_hooked_into_pg_to_switch():
    src = (REPO / "pg_to_switch.py").read_text(encoding="utf-8")
    i = src.index("s0days.sample_is_blocks(representative_point)")
    assert src.index("s0prm.add_stress_days(") < i < src.index("s0days.ts_tp_blocks(", i) < src.index(
        "s0prm.rename_stress_rows(", i)


def test_ids_beyond_int32_and_the_windows_guard():
    """Timepoint ids are wider than int32 (they already were: PowerGenome's multi-weather-year ids are 11 digits, e.g.
    the committed fedpol cases), so they must never go through the platform C long (`astype(int)`), which is 32-bit
    on Windows. conftest.py's autouse guard makes `astype(int)` raise there on every platform."""
    rep = pd.DataFrame({"slot": ["p2550x3", "p101"], "start_day": [2549, np.nan], "n_days": [3, np.nan]})
    t, p = ds.ts_tp_blocks(rep, [100.0, 0.0], 2035, 2031)
    t, p = prm.rename_stress_rows(t, p, 1)
    ids = p.timepoint_id.astype("int64")
    assert ids.max() >= 2 ** 31 and ids.is_unique and p.timepoint_id.iloc[-1].startswith("9")
    assert int(p.timepoint_id.iloc[-1]) == 920351041123 and ids.max() < 2 ** 63
    with pytest.raises(OverflowError, match="C long"):
        p.timepoint_id.astype(int)
    with pytest.raises(OverflowError, match="C long"):
        p[["timepoint_id"]].astype({"timepoint_id": int})
    assert pd.Series(["2147483647"]).astype(int).iloc[0] == 2 ** 31 - 1          # within int32: allowed
