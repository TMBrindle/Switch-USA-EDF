"""§246: each zone's load and each hydro budget to the full record (cf_fidelity.load_hydro). Hand-built profiles and
sample: fixtures, not results."""
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import cf_fidelity as cf  # noqa: E402

GENS = pd.DataFrame({"Resource": ["hyd_a", "hyd_b", "pv", "gas"], "HYDRO": [1, 1, 0, 0],
                     "technology": ["Conventional Hydroelectric", "Conventional Hydroelectric",
                                    "UtilityPV_Class1_Moderate", "NaturalGas_CCAvg"]})


def _case():
    rng = np.random.default_rng(5)
    D = 80
    H = 24 * D
    season = np.repeat(np.sin(np.linspace(0, 2 * np.pi, D)), 24)
    load = pd.DataFrame({"p1": 1000 + 300 * season + rng.normal(0, 30, H), "p2": 500 - 100 * season + rng.normal(0, 20, H)})
    var = pd.DataFrame({"hyd_a": np.clip(0.5 + 0.3 * season + rng.normal(0, 0.05, H), 0, 1),
                        "hyd_b": np.clip(1.2 + 0.7 * season, 0, 2.0),           # hydro_factor 2: above 1
                        "pv": rng.uniform(0, 1, H), "gas": np.ones(H)})
    picks = [5, 20, 33, 47, 70, 71]                               # 5 sample days + 1 stress day (the last)
    rows = np.concatenate([np.arange(d * 24, d * 24 + 24) for d in picks])
    ts = pd.DataFrame({"timeseries": [f"2030_p{i}" for i in range(5)] + ["2030_p5_prm"],
                       "ts_scale_to_period": [60.0, 80.0, 70.0, 90.0, 65.0, 0.0], "ts_duration_of_tp": [1.0] * 6,
                       "ts_num_tps": [24] * 6})
    tp = pd.DataFrame({"timeseries": np.repeat(ts.timeseries.values, 24), "timepoint_id": range(len(rows))})
    return load, var, load.iloc[rows].reset_index(drop=True), var.iloc[rows].reset_index(drop=True), ts, tp


CFG = {**cf.DEFAULTS, "mode": "rescale", "load_hydro": "rescale"}


def test_load_and_hydro_match_the_full_record():
    load, var, sl, sv, ts, tp = _case()
    w = cf.row_weights(ts, tp)
    on = w > 0
    wm = lambda x: float((np.asarray(x)[on] * w[on]).sum() / w[on].sum())
    assert abs(wm(sl.p1) / load.p1.mean() - 1) > 0.005                          # the sample drifts before
    nl, nv, rep = cf.rescale_load_hydro(load, sl, var, sv, GENS, w, CFG, 2030)
    for z in ("p1", "p2"):
        assert wm(nl[z]) == pytest.approx(load[z].mean(), rel=1e-12)
        # uniform per zone: every sampled hour (the peak day included) times the same factor
        r = (nl[z].to_numpy()[on] / sl[z].to_numpy()[on])
        assert np.allclose(r, r[0]) and r[0] == pytest.approx(rep.set_index("name").scale[z])
    for h in ("hyd_a", "hyd_b"):
        assert wm(nv[h]) == pytest.approx(var[h].mean(), rel=1e-9)
    assert nv.hyd_b.max() <= var.hyd_b.max() + 1e-12 and nv.hyd_b.max() > 1.0      # cap: its own maximum, not 1
    # stress-day rows and everything else untouched
    assert np.array_equal(nl.to_numpy()[~on], sl.to_numpy()[~on]) and np.array_equal(nv.to_numpy()[~on], sv.to_numpy()[~on])
    assert nv.pv.equals(sv.pv) and nv.gas.equals(sv.gas)
    assert set(rep.kind) == {"load", "hydro"} and (rep.residual.abs() < 1e-6).all()
    # the hydro budget Switch reads (hydro_avg_flow_mw: the mean per timeseries) now sums to the full record
    per_ts = pd.Series(nv.hyd_a.to_numpy()).groupby(tp.timeseries.to_numpy()).mean()
    wts = ts.set_index("timeseries").ts_scale_to_period
    assert (per_ts * wts).sum() / wts.sum() == pytest.approx(var.hyd_a.mean(), rel=1e-9)


def test_settings_v32_on_code_default_off():
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0["time_sampling"]["cf_fidelity"]["load_hydro"] == "rescale"               # v3.2 ON (§246)
    assert "load_hydro" not in cf.DEFAULTS                                             # code default: off (v3.1)
    on = {"s0_production": {**s0, "enabled": True}}
    assert cf.load_hydro_settings(on) is not None
    for c in ({"mode": "rescale"}, {"mode": "off", "load_hydro": "rescale"}):          # absent = off; needs rescale
        s = {"s0_production": {**s0, "enabled": True, "time_sampling": {**s0["time_sampling"], "cf_fidelity": c}}}
        assert cf.load_hydro_settings(s) is None
    with pytest.raises(ValueError, match="load_hydro"):
        cf.load_hydro_settings({"s0_production": {"enabled": True, "time_sampling": {
            "cf_fidelity": {"mode": "rescale", "load_hydro": "zones"}}}})
    sm = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"]
    assert sm["s0_production"]["on_pgdays"]["s0_production"]["time_sampling"]["cf_fidelity"] == {"mode": "off"}


def test_wind_solar_rescale_unchanged_since_f3():
    """The cap became a parameter (default 1.0): the wind / solar rescale computes exactly as F3's code (0c9d9f7)."""
    r = subprocess.run(["git", "show", "0c9d9f7:s0_workflow/cf_fidelity.py"], cwd=REPO, capture_output=True, text=True)
    if r.returncode:
        pytest.skip("0c9d9f7 not available")
    ns = {}
    exec(compile(r.stdout.replace("from s0_workflow.day_selection import kind", "kind = None"), "old", "exec"), ns)
    rng = np.random.default_rng(9)
    for _ in range(25):
        x = rng.uniform(0, 1, 200) * rng.choice([0.5, 1.0, 1.5])
        w = rng.choice([0.0, 10.0, 25.0, 60.0], 200)
        t = float(rng.uniform(0.05, 0.8))
        a, b = ns["_rescale_column"](x, w, t, 5), cf._rescale_column(x, w, t, 5)
        assert np.array_equal(a[0], b[0]) and [a[1], a[2], a[3]] == [b[1], b[2], b[3]] or np.isnan(a[1]) and np.isnan(b[1])


def test_wired_before_hydro_and_loads():
    src = (REPO / "pg_to_switch.py").read_text()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "operational_files")
    body = ast.unparse(fn)
    i = body.index("s0_cf_fidelity.rescale_load_hydro(")
    assert body.index("rename_stress_rows(") < i < body.index("hydro_timeseries_pg_kmeans(") < body.index(
        "load_pg_kmeans(period_lc_sampled")
    assert i < body.index("hydro_system_tables(") and "load_hydro_fidelity.csv" in body
    assert "s0_cf_fidelity.load_hydro_settings(year_settings) if cluster_time else None" in body
