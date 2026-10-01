"""Run with: pytest -q  (from the project root)."""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from icsc import cli, estimate, geo, linkage, lbnl, synthetic, tranches

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def cfg():
    return cli.load_cfg(str(ROOT / "config.yaml"))


@pytest.fixture(scope="session")
def built(cfg):
    if not Path(cfg["paths"]["eia860m"]).exists():
        pytest.skip("EIA-860M file missing: run `bash scripts/fetch_data.sh`")
    panel, gens, c2z = cli.build_panel(cfg)
    return panel, gens, c2z


def test_county_matching():
    c2z = geo.load_county2zone(ROOT / "data/reference/county2zone.csv")
    df = pd.DataFrame({"state": ["FL", "IL", "TX", "CT", "LA"],
                       "county": ["Miami Dade", "LaSalle", "DeWitt", "New London", "DeSoto Parish"]})
    out = geo.attach_ba(df, c2z, "state", "county")
    assert out["ba"].notna().all(), out


def test_linkage_cascade(cfg):
    """Own county, then (no Queued Up scope here) single-zone state, then transmission owner."""
    c2z = geo.load_county2zone(ROOT / "data/reference/county2zone.csv")
    assert linkage.norm_qid("Q007 - 061") == linkage.norm_qid("q007-061") == "Q007061"
    assert linkage.norm_qid(1125.0) == "1125"
    f = "TEST.xlsx"
    df = pd.DataFrame({
        "source_file": f, "region": "TEST", "project_id": list("abcdefg"), "qu_id": np.nan, "fips": np.nan,
        "state": ["IL", "IL", "IL", "IL", "IL", "DE", "IL"],
        "county": ["Cook", "Champaign", "Cook", None, None, None, None],
        "owner": ["X", "X", "Y", "X", "Y", None, "Z"],
        "capacity_mw": [100, 300, 50, 10, 10, 10, 10]})
    out = linkage.link(df, c2z, cfg).set_index("project_id")
    zc, zl = geo.attach_ba(df.iloc[:2], c2z)["ba"]
    assert zc != zl  # Cook and Champaign are in different zones, so owner X spans two
    assert out.loc["a", "ba_source"] == "lbnl_county"
    assert out.loc["d", "ba_source"] == "owner_multi" and pd.isna(out.loc["d", "ba"])
    assert out.loc["d", "ba_candidates"] == f"{zl}:0.7500|{zc}:0.2500"
    assert out.loc["d", "ba_cluster"] == zl
    assert out.loc["e", "ba_source"] == "owner_single" and out.loc["e", "ba"] == zc
    assert out.loc["f", "ba_source"] == "state_single_zone"
    assert pd.isna(out.loc["g", "ba"]) and not out.loc["g", "ba_multi"]  # unknown owner: dropped
    panel = pd.DataFrame({"ba": [zc, zl], "year": 2020, "saturation": [0.2, 0.6]})
    s = estimate.attach_saturation(out.reset_index().assign(queue_year=2020), panel).set_index("project_id")
    assert s.loc["d", "saturation"] == pytest.approx(0.75 * 0.6 + 0.25 * 0.2)


def test_categories():
    from icsc.eia import categorise
    t = pd.Series(["Solar Photovoltaic", "Onshore Wind Turbine", "Batteries", "Natural Gas Fired Combined Cycle",
                   "Natural Gas with Compressed Air Storage", "Conventional Steam Coal", "Hydroelectric Pumped Storage"])
    assert categorise(t).tolist() == ["solar", "wind", "storage", "gas", "storage", "other", "storage"]


def test_gas_uses_headroom(cfg, built):
    """Doubling the gas weight must raise saturation wherever gas was added after baseline."""
    from icsc import eia
    panel, gens, c2z = built
    comp = eia.zone_components(gens, c2z, cfg)
    w = dict(cfg["saturation"]["tech_weights"])
    lo = eia.apply_weights(comp, {**w, "gas": 0.0}, 0.0)
    hi = eia.apply_weights(comp, {**w, "gas": 1.0}, 0.0)
    gas_zones = comp["add_gas"] > 0
    assert (hi.loc[gas_zones, "saturation"] > lo.loc[gas_zones, "saturation"]).all()


def test_eia_match_rate(built):
    panel, gens, c2z = built
    m = geo.attach_ba(gens, c2z, "state", "county")
    w = m["mw"].fillna(0)
    assert w[m["ba"].notna()].sum() / w.sum() > 0.98


def test_panel_saturation_nonnegative(built):
    panel, _, _ = built
    assert (panel["saturation"] >= 0).all()
    assert panel["ba"].nunique() == 134


def test_synthetic_recovery(cfg, built, tmp_path):
    panel, _, c2z = built
    hier = geo.load_hierarchy(cfg["paths"]["hierarchy"])
    d = synthetic.make(panel, c2z, hier, tmp_path, n_per_region=800, seed=1)
    # the synthetic costs are generated from a log(1 + $/kW) model, so recover them with log-OLS
    c = dict(cfg, paths=dict(cfg["paths"], lbnl_dir=str(d)),
             estimation=dict(cfg["estimation"], estimator="log_ols", status_term=False))
    projects = lbnl.load_all(c, c2z)
    sample = estimate.attach_saturation(lbnl.estimation_sample(projects, c), panel)
    m = estimate.fit(sample, c)
    p, se = m.result.params, m.result.bse
    assert abs(p["sat"] - synthetic.TRUE["sat"]) < 3 * se["sat"]
    assert abs(p["service_ERIS"] - synthetic.TRUE["ERIS"]) < 0.15
    eff = m.regime_effects()
    true = pd.Series(synthetic.REGIME_EFFECT)
    # regime effects are identified relative to the base regime
    diff = (eff - eff[m.base_regime]) - (true[eff.index] - true[m.base_regime])
    assert diff.abs().max() < 0.2, diff

    # weight search runs and is sorted by fit
    c2 = dict(c, weight_search={"wind": [0.75, 1.0], "storage": [0.5], "gas": [0.0, 1.0], "reuse_share": [0.8]},
              paths=dict(c["paths"], outputs=str(tmp_path / "out")))
    res = cli.cmd_fit_weights(c2, proxies=["transfer"])
    assert len(res) == 4 and res["d_aic"].iloc[0] == 0

    regimes = tranches.zone_regimes(hier, m, c)
    now = cli.start_saturation(panel, built[1], c2z, c, 2026).reset_index()
    zr, tr = tranches.build_reference(m, now, regimes, c, 2026)
    zb, tb = tranches.build_reference(m, now, regimes, c, 2026, regime_override="best")
    assert tr.groupby("ba")["cost_per_kw"].apply(lambda x: (np.diff(x.values) >= -1e-9).all()).all()
    assert (tb["cost_per_kw"] <= tr["cost_per_kw"] + 1e-9).all()
    assert (zr["release_cost_per_kw"] <= tr.groupby("ba")["cost_per_kw"].first().reindex(zr["ba"]).values + 1e-9).all()

    # scenario levers: slope flattens the rise but keeps the first step; uprates scale with H0
    _, flat, _ = tranches.apply_scenario(zr, tr, c, {"slope_multiplier": 0.5})
    first = tr.groupby("ba")["cost_per_kw"].first()
    assert np.allclose(flat.groupby("ba")["cost_per_kw"].first(), first)
    assert (flat["cost_per_kw"] <= tr.sort_values(["ba", "sat_from"])["cost_per_kw"].values + 1e-9).all()
    _, _, up = tranches.apply_scenario(zr, tr, c, {"uprates": ["gets", "reconductor"]})
    assert len(up) == 3 * len(zr)  # plus the new_line backstop
    g = up[up.uprate == "gets"].merge(zr, on="ba")
    assert np.allclose(g["max_mw"], c["uprate_options"]["gets"]["share_of_capacity"] * g["base_capacity_mw"])
    _, _, ref = tranches.apply_scenario(zr, tr, c, {})
    assert set(ref["uprate"]) == set(c["backstop_uprates"]) and len(ref) == len(zr)

    # empirical steps stop at the support edge; zones beyond it get one edge-priced step
    edge = m.sat_support
    inside = tr[~tr["beyond_support"]]
    assert np.allclose(inside.groupby("ba")["sat_to"].max(), edge)
    assert (inside["width"] <= c["tranches"]["step_width"] + 1e-9).all()
    assert not tr["extrapolated"].any()
    out = tr[tr["beyond_support"]]
    assert (out.groupby("ba").size() == 1).all()
    assert np.allclose(out["width"], c["tranches"]["edge_step_width"])


def test_sensitivity_configs_extend_base(cfg):
    for f, key, val in [("wind_050", ("saturation", "tech_weights", "wind"), 0.5),
                        ("boundary_p10", ("saturation", "headroom_proxy"), "boundary+p10"),
                        ("price_active", ("tranches", "reference_status"), "active")]:
        c = cli.load_cfg(str(ROOT / "sensitivities" / f"{f}.yaml"))
        v = c
        for k in key:
            v = v[k]
        assert v == val
        assert c["paths"]["county2zone"] == cfg["paths"]["county2zone"]  # base paths resolve as in config.yaml
        assert c["paths"]["outputs"] != cfg["paths"]["outputs"]
        assert c["estimation"] == cfg["estimation"]


def test_status_by_regime(cfg, built):
    """Regimes with >= status_by_regime_min completed projects get their own completed effect."""
    panel, _, c2z = built
    s = estimate.attach_saturation(lbnl.estimation_sample(lbnl.load_all(cfg, c2z), cfg), panel)
    m = estimate.fit(s, cfg)
    n = s[s["status_n"] == "completed"]["regime"].value_counts()
    own = set(n[n >= cfg["estimation"]["status_by_regime_min"]].index)
    assert set(m.status_regimes["completed"]) == own
    assert {f"status_completed@{r}" for r in own} | {"status_completed@pooled"} <= set(m.columns)
    small = sorted(set(s["regime"]) - own)[0]
    assert m.status_effect(small, "completed") == m.result.params["status_completed@pooled"]
    big = sorted(own)[0]
    assert m.status_effect(big, "completed") == m.result.params[f"status_completed@{big}"]
    # the design reproduces the fitted linear predictor
    d = s.dropna(subset=["saturation", "network_cost_real", "capacity_mw", "queue_year"])
    lp = m.log_pred(d["saturation"].values, d["tech_n"].values, d["service_n"].values, d["capacity_mw"].values,
                    d["queue_year"].values, d["regime"].values, d["status_n"].values)
    sat_ok = d["saturation"].values <= m.sat_support
    assert np.allclose(lp[sat_ok], np.log(m.result.fittedvalues.values[sat_ok]))


def test_zone_regimes_utilities(cfg):
    hier = geo.load_hierarchy(cfg["paths"]["hierarchy"])
    lab = tranches.zone_regime_labels(hier, cfg)
    expect = {"BPA": {"p2", "p5", "p7"}, "PacifiCorp": {"p6", "p8", "p21", "p22", "p25", "p26"},
              "DEC": {"p95", "p97"}, "DEP": {"p98"}}
    for reg, zones in expect.items():
        assert set(lab[lab == reg].index) == zones, reg
    assert lab["p101"] == "FRCC" and lab["p102"] == "FRCC" and lab["p80"] == "PJM"


def test_fit_weights_uses_run_regimes(cfg, built, monkeypatch, tmp_path):
    """fit-weights must fit on the same sample, with the same regime labels, as run."""
    _, _, c2z = built
    run_sample = lbnl.estimation_sample(lbnl.load_all(cfg, c2z), cfg)
    seen = []
    real_fit = estimate.fit
    monkeypatch.setattr(estimate, "fit", lambda s, c: seen.append(s) or real_fit(s, c))
    one = dict(cfg, weight_search={"proxies": ["boundary"], "solar": [1.0], "wind": [0.5], "storage": [0.5],
                                   "gas": [1.0], "other": [1.0], "reuse_share": [0.0]},
               paths=dict(cfg["paths"], outputs=str(tmp_path)))
    cli.cmd_fit_weights(one)
    fw = seen[0]
    assert len(fw) == len(run_sample)
    assert (fw["regime"].values == run_sample["regime"].values).all()
    zone_labels = set(tranches.zone_regime_labels(geo.load_hierarchy(cfg["paths"]["hierarchy"]), cfg))
    assert set(fw["regime"]) <= zone_labels, set(fw["regime"]) - zone_labels


@pytest.mark.skipif(shutil.which("switch") is None, reason="switch_model not installed")
def test_switch_module_on_toy(tmp_path):
    import os
    base = Path(os.environ.get("SWITCH_SRC", ROOT.parent.parent / "switch-src"))
    src = base / "examples" / "3zone_toy"
    if not src.exists():
        pytest.skip("set SWITCH_SRC to a clone of https://github.com/switch-model/switch to run")
    run = tmp_path / "toy"
    shutil.copytree(src, run)
    (run / "ic_mod").mkdir()
    shutil.copy(ROOT.parent / "switch/study_modules/interconnection_headroom.py", run / "ic_mod")
    (run / "ic_mod/__init__.py").touch()
    with open(run / "inputs/modules.txt", "a") as f:
        f.write("\nic_mod.interconnection_headroom\n")
    for f in ("ic_zones.csv", "ic_tranches.csv", "ic_uprates.csv", "ic_params.csv", "ic_weights.csv"):
        shutil.copy(ROOT / "tests/switch_toy" / f, run / "inputs")
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs"], cwd=run, capture_output=True,
                       text=True, env={**__import__("os").environ, "PYTHONPATH": str(run)})
    assert r.returncode == 0, r.stderr[-2000:]
    hr = pd.read_csv(run / "outputs/ic_headroom.csv")
    assert (hr.new_capacity_mw_weighted <= hr.headroom_bought_mw + hr.freed_headroom_mw + 1e-6).all()
    net = pd.read_csv(run / "outputs/ic_network.csv").set_index(["ic_zone", "period"])
    spend = pd.read_csv(run / "outputs/ic_spend.csv")
    zones = pd.read_csv(ROOT / "tests/switch_toy/ic_zones.csv").set_index("IC_ZONE")
    steps = pd.read_csv(ROOT / "tests/switch_toy/ic_tranches.csv")
    # North: wind wants more room than the cheap step gives, so the model buys network capacity
    n = net.loc[("N", 2030)]
    assert n.deliberate_mw_added > 0
    h = zones.loc["N", "ic_base_capacity_mw"] + n.deliberate_mw_added
    w1 = steps.set_index("IC_TRANCHE").loc["N_nu1", "ic_tranche_width"]
    # ...which stretches the cheap step beyond its width at base capacity, and releases headroom
    assert n.headroom_from_curve_mw > w1 * zones.loc["N", "ic_base_capacity_mw"] + 1e-6
    assert n.headroom_from_curve_mw <= sum(
        steps[steps.ic_tranche_zone == "N"].ic_tranche_width) * h + 1e-6
    assert 0 < n.headroom_released_mw <= zones.loc["N", "ic_start_saturation"] * n.deliberate_mw_added + 1e-6
    # spend report: uprate spend = MW x cost; reactive capacity estimate = spend / engineering cost
    up = pd.read_csv(ROOT / "tests/switch_toy/ic_uprates.csv").set_index("IC_UPRATE")
    sp = spend[(spend.ic_zone == "N") & (spend.period == 2030)].set_index("type")
    upr = sp.drop(index="reactive_upgrades")
    assert np.isclose(upr.network_mw_added.sum(), n.deliberate_mw_added)
    assert upr.overnight_cost.sum() <= (up.ic_uprate_max_mw * up.ic_uprate_cost_per_mw).sum() + 1e-6
    k = pd.read_csv(ROOT / "tests/switch_toy/ic_params.csv").ic_reactive_cost_per_mw_network.iat[0]
    assert np.isclose(sp.loc["reactive_upgrades", "network_mw_added"], sp.loc["reactive_upgrades", "overnight_cost"] / k)
    w = pd.read_csv(run / "outputs/ic_gen_weights.csv").set_index("GENERATION_PROJECT")["ic_weight"]
    assert w["C-NG_CC"] == 1.0 and w["N-Wind-1"] == 0.75 and w["N-Central_PV-1"] == 1.0
    # new gas uses headroom: Central's weighted new capacity includes its new NG_CC builds
    bg = pd.read_csv(run / "outputs/BuildGen.csv")
    new_gas_c = bg[(bg.GEN_BLD_YRS_1 == "C-NG_CC") & (bg.GEN_BLD_YRS_2 >= 2020)].BuildGen.sum()
    c30 = hr[(hr.load_zone == "Central") & (hr.period == 2030)].iloc[0]
    assert c30.new_capacity_mw_weighted >= new_gas_c - 1e-6
    assert (hr.headroom_slack_mw == 0).all()          # no ic_slack_cost_per_mw: hard constraint


_FORCE_BUILD_MODULE = '''
from pyomo.environ import Constraint
def define_components(m):
    # test only: a new build far larger than North's headroom curve can admit
    m.Force_IC_Test_Build = Constraint(rule=lambda m: m.BuildGen["N-NG_CC", 2030] >= 200)
'''


def _toy_forced_build(tmp_path, slack_cost):
    import os
    base = Path(os.environ.get("SWITCH_SRC", ROOT.parent.parent / "switch-src"))
    src = base / "examples" / "3zone_toy"
    if not src.exists():
        pytest.skip("set SWITCH_SRC to a clone of https://github.com/switch-model/switch to run")
    run = tmp_path / "toy"
    shutil.copytree(src, run)
    (run / "ic_mod").mkdir()
    shutil.copy(ROOT.parent / "switch/study_modules/interconnection_headroom.py", run / "ic_mod")
    (run / "ic_mod/__init__.py").touch()
    (run / "ic_mod/force_build.py").write_text(_FORCE_BUILD_MODULE)
    with open(run / "inputs/modules.txt", "a") as f:
        f.write("\nic_mod.interconnection_headroom\nic_mod.force_build\n")
    for f in ("ic_zones.csv", "ic_tranches.csv", "ic_uprates.csv", "ic_weights.csv"):
        shutil.copy(ROOT / "tests/switch_toy" / f, run / "inputs")
    params = pd.read_csv(ROOT / "tests/switch_toy/ic_params.csv")
    params["ic_retirement_reuse_share"] = 0.0          # no freed headroom to absorb the forced build
    params["ic_slack_cost_per_mw"] = slack_cost
    params.to_csv(run / "inputs/ic_params.csv", index=False)
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs"], cwd=run, capture_output=True,
                       text=True, env={**os.environ, "PYTHONPATH": str(run)})
    return run, r


@pytest.mark.skipif(shutil.which("switch") is None, reason="switch_model not installed")
def test_headroom_slack_absorbs_forced_build(tmp_path):
    run, r = _toy_forced_build(tmp_path, 1e7)
    assert r.returncode == 0, r.stderr[-2000:]
    hr = pd.read_csv(run / "outputs/ic_headroom.csv").set_index(["load_zone", "period"])
    n = hr.loc[("North", 2030)]
    # 200 MW of new gas (weight 1) against a curve of at most sum(widths) x (H0 + uprates) + release
    assert n.headroom_slack_mw > 0
    assert np.isclose(n.new_capacity_mw_weighted,
                      n.initial_headroom_mw + n.freed_headroom_mw + n.headroom_bought_mw + n.headroom_slack_mw,
                      atol=1e-4)
    assert "diagnostic slack used" in (r.stdout + r.stderr)


@pytest.mark.skipif(shutil.which("switch") is None, reason="switch_model not installed")
def test_headroom_without_slack_is_hard(tmp_path):
    run, r = _toy_forced_build(tmp_path, ".")
    out = (r.stdout + r.stderr).lower()
    assert r.returncode != 0                           # same forced build: infeasible without slack
    assert "constructing component" not in out         # "." loads as "no slack", not a data error
    assert "infeasible" in out or "feasible solution was not found" in out


# ---------------------------------------------------------------------------
# Switch-USA-EDF integration (icsc/switch_case.py, prepare_next_stage.py)
# ---------------------------------------------------------------------------

def _gen_info():
    return pd.DataFrame({
        "GENERATION_PROJECT": ["p1_pv", "p1_wind", "p1_batt", "p1_ccgt", "p1_dr", "p2_pv", "p1_dist"],
        "gen_tech": ["UtilityPV_Class1", "LandbasedWind_Class3", "Battery_4hr", "NaturalGas_CCAvg",
                     "DR_shift", "UtilityPV_Class1", "Distributed_Solar"],
        "gen_energy_source": ["sun", "wind", "storage", "naturalgas", "demand_response", "sun", "sun"],
        "gen_load_zone": ["p1", "p1", "p1", "p1", "p1", "p2", "p1"],
        "gen_connect_cost_per_mw": [200000.0, 300000.0, 0.0, 50000.0, 0.0, 150000.0, 0.0],
    })


def test_switch_case_weights_and_strip():
    from icsc import switch_case as sc
    gi = _gen_info()
    w = sc.weights_by_tech(gi, {"solar": 1.0, "wind": 0.75, "storage": 0.5, "gas": 1.0, "other": 1.0})
    w = w.set_index("ic_key")["ic_weight"]
    assert w["UtilityPV_Class1"] == 1.0 and w["LandbasedWind_Class3"] == 0.75
    assert w["Battery_4hr"] == 0.5 and w["NaturalGas_CCAvg"] == 1.0 and w["DR_shift"] == 0.0
    src = pd.DataFrame({"spur_capex": [50000, 80000, 0, 0, 0, 40000, 0],
                        "tx_capex": [120000, 150000, 0, 0, 0, 60000, 0]})
    diag = sc.strip_network_reinforcement(gi, src)
    assert gi["gen_connect_cost_per_mw"].tolist()[:2] == [80000.0, 150000.0]
    assert (diag["gen_connect_cost_per_mw_before"] - diag["gen_connect_cost_per_mw_after"]).sum() == 330000
    assert (diag["removal_method"] == "tx_capex").all()


def test_switch_case_distributed_weight_zero():
    from icsc import switch_case as sc
    gi = _gen_info()
    gi["gen_is_distributed"] = 0
    gi.loc[len(gi)] = ["p1_dg", "distributed_generation", "sun", "p1", 0.0, 1]   # flag only
    w = sc.weights_by_tech(gi, {"solar": 1.0, "wind": 0.25, "storage": 0.5, "gas": 1.0, "other": 1.0})
    w = w.set_index("ic_key")["ic_weight"]
    assert w["distributed_generation"] == 0.0     # gen_is_distributed == 1
    assert w["Distributed_Solar"] == 0.0          # named as distributed, flag 0
    assert w["UtilityPV_Class1"] == 1.0           # utility solar unchanged
    # flag set on a tech whose name doesn't say distributed
    gi2 = _gen_info().assign(gen_is_distributed=[1, 0, 0, 0, 0, 1, 0])
    w2 = sc.weights_by_tech(gi2, {"solar": 1.0, "wind": 0.25}).set_index("ic_key")["ic_weight"]
    assert w2["UtilityPV_Class1"] == 0.0 and w2["LandbasedWind_Class3"] == 0.25


def test_switch_case_strip_reinforcement_share_for_bundled_costs():
    """ReEDS-CPA costs: tx_capex all zero, interconnect_capex_mw bundles spur + POI + reinforcement."""
    from icsc import switch_case as sc
    gi = _gen_info()
    gi["gen_connect_cost_per_mw"] = [200000.0, 300000.0, 0.0, 0.0, 0.0, 150000.0, 0.0]
    gi.loc[len(gi)] = ["p1_osw", "OffShoreWind_Class3_fixed", "wind", "p1", 1000000.0]
    src = pd.DataFrame({"spur_capex": 0, "tx_capex": 0,
                        "interconnect_capex_mw": [200000.0, 300000.0, 0, 0, 0, 150000.0, 0, 1000000.0]})
    shares = pd.DataFrame({"zone": ["p1", "p1", "_national", "_national", "_national"],
                           "tech": ["upv", "wind-ons", "upv", "wind-ons", "wind-ofs"],
                           "reinforcement_share": [0.8, 0.9, 0.5, 0.6, 0.2]})
    diag = sc.strip_network_reinforcement(gi, src, shares)
    after = gi.set_index("GENERATION_PROJECT")["gen_connect_cost_per_mw"]
    assert after["p1_pv"] == pytest.approx(200000 * 0.2)      # zone share
    assert after["p1_wind"] == pytest.approx(300000 * 0.1)
    assert after["p2_pv"] == pytest.approx(150000 * 0.5)      # no p2 row: national fallback
    assert after["p1_osw"] == pytest.approx(1000000 * 0.8)    # offshore -> wind-ofs national
    assert after["p1_ccgt"] == 0 and after["p1_batt"] == 0    # not wind/solar: untouched
    d = diag.set_index("GENERATION_PROJECT")
    assert d.loc["p1_pv", "reinforcement_share"] == 0.8 and pd.isna(d.loc["p1_ccgt", "reinforcement_share"])
    assert d["removed_per_mw"].sum() == pytest.approx(160000 + 270000 + 75000 + 200000)
    assert (d["removal_method"] == "interconnect_capex_mw x reinforcement_share").all()


def test_reinforcement_share_reference_file():
    from icsc import switch_case as sc
    s = pd.read_csv(sc.REINFORCEMENT_SHARE)
    assert {"upv", "wind-ons", "wind-ofs"} <= set(s.loc[s["zone"] == "_national", "tech"])
    assert s["reinforcement_share"].between(0, 1).all()
    assert s.duplicated(["zone", "tech"]).sum() == 0


def test_switch_case_write_with_zone_map(tmp_path, monkeypatch):
    from icsc import switch_case as sc
    tdir = tmp_path / "tr"
    tdir.mkdir()
    pd.DataFrame({"ba": ["p1", "p2", "p3"], "base_capacity_mw": [1000.0, 800.0, 500.0],
                  "start_saturation": [0.2, 0.4, 0.1], "release_cost_per_kw": [10, 20, 5],
                  "regime": ["ERCOT"] * 3}).to_csv(tdir / "zones_reference.csv", index=False)
    pd.DataFrame({"ba": ["p1", "p1", "p2", "p3"], "tranche": ["nu1", "nu2", "nu1", "nu1"],
                  "width": [0.05, 0.1, 0.05, 0.05], "cost_per_kw": [20, 40, 30, 10],
                  "sat_from": [0.2, 0.25, 0.4, 0.1], "sat_to": [0.25, 0.35, 0.45, 0.15],
                  "extrapolated": [False] * 4, "available_year": [0] * 4}).to_csv(tdir / "tranches_reference.csv", index=False)
    pd.DataFrame({"ba": ["p1", "p3"], "uprate": ["gets", "gets"], "type": ["gets", "gets"],
                  "max_mw": [100.0, 50.0], "cost_per_kw": [20, 20],
                  "available_year": [2028, 2028]}).to_csv(tdir / "uprates_reference.csv", index=False)
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    settings = {"interconnection_headroom": {"enabled": True, "tranches_dir": "tr"},
                "_zone_map": {"p1": "TX", "p2": "TX", "p3": "OK"}}
    gi = _gen_info().assign(gen_load_zone="TX")
    out = tmp_path / "case"
    out.mkdir()
    sc.write_case_inputs(gi, settings, out)
    z = pd.read_csv(out / "ic_zones.csv").set_index("IC_ZONE")
    assert list(z.index) == ["p1", "p2"] and set(z.ic_zone_load_zone) == {"TX"}  # p3 -> OK, not in case
    t = pd.read_csv(out / "ic_tranches.csv")
    assert t["ic_tranche_cost_per_mw"].tolist() == [20000, 40000, 30000]
    u = pd.read_csv(out / "ic_uprates.csv")
    assert u["IC_UPRATE"].tolist() == ["p1_gets"] and u["ic_uprate_available_year"].iat[0] == 2028
    p = pd.read_csv(out / "ic_params.csv")
    assert p["ic_reactive_cost_per_mw_network"].iat[0] > 0
    assert (out / "ic_weights.csv").exists()
    # disabled -> writes nothing
    out2 = tmp_path / "case2"
    out2.mkdir()
    sc.write_case_inputs(gi, {"interconnection_headroom": {"enabled": False}}, out2)
    assert not any(out2.iterdir())


def test_prepare_next_stage_chains_headroom(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pns", ROOT.parent / "switch/study_modules/prepare_next_stage.py")
    pns = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pns)
    inp, nxt, out = tmp_path / "in/2030/c", tmp_path / "in/2035/c", tmp_path / "out/2030/c"
    for d in (inp, nxt, out):
        d.mkdir(parents=True)
    pd.DataFrame({"IC_ZONE": ["p1"], "ic_zone_load_zone": ["p1"], "ic_base_capacity_mw": [1000.0],
                  "ic_start_saturation": [0.2], "ic_release_cost_per_mw": [1e4]}).to_csv(inp / "ic_zones.csv", index=False)
    pd.DataFrame({"IC_TRANCHE": ["p1_nu1", "p1_nu2"], "ic_tranche_zone": ["p1", "p1"],
                  "ic_tranche_width": [0.1, 0.2], "ic_tranche_cost_per_mw": [1e4, 5e4],
                  "ic_tranche_available_year": [0, 0]}).to_csv(inp / "ic_tranches.csv", index=False)
    pd.DataFrame({"IC_UPRATE": ["p1_gets"], "ic_uprate_zone": ["p1"], "ic_uprate_type": ["gets"],
                  "ic_uprate_max_mw": [100.0], "ic_uprate_cost_per_mw": [2e4],
                  "ic_uprate_available_year": [0]}).to_csv(inp / "ic_uprates.csv", index=False)
    # stage 1: built 100 MW of GETs (H = 1100), used all of step 1 (0.1 x 1100 = 110) and 55 of step 2,
    # released 20 MW (<= 0.2 x 100)
    pd.DataFrame({"ic_tranche": ["p1_nu1", "p1_nu2"], "ic_zone": ["p1", "p1"], "period": [2030, 2030],
                  "used_mw": [110.0, 55.0]}).to_csv(out / "ic_tranches_built.csv", index=False)
    pd.DataFrame({"ic_uprate": ["p1_gets"], "ic_zone": ["p1"], "period": [2030],
                  "built_mw": [100.0]}).to_csv(out / "ic_uprates_built.csv", index=False)
    pd.DataFrame({"ic_zone": ["p1"], "period": [2030], "released_mw": [20.0]}).to_csv(
        out / "ic_release_built.csv", index=False)
    pns.chain_ic_inputs(inp, out, nxt, "c")
    z = pd.read_csv(nxt / "ic_zones.chained.c.csv").iloc[0]
    assert z.ic_base_capacity_mw == 1100
    assert np.isclose(z.ic_start_saturation, (0.2 * 1000 + 110 + 55 + 20) / 1100, atol=1e-6)
    st = pd.read_csv(nxt / "ic_tranches.chained.c.csv").set_index("IC_TRANCHE")["ic_tranche_width"]
    assert np.isclose(st["p1_nu1"], 0) and np.isclose(st["p1_nu2"], 0.2 - 55 / 1100, atol=1e-6)
    u = pd.read_csv(nxt / "ic_uprates.chained.c.csv").iloc[0]
    assert u.ic_uprate_max_mw == 0
    # no headroom outputs -> nothing written
    pns.chain_ic_inputs(tmp_path / "in/none", out, tmp_path / "in/none2", "c")
