"""Run with: pytest -q  (from the project root)."""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from icsc import cli, estimate, geo, lbnl, synthetic, tranches

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
    c = dict(cfg, paths=dict(cfg["paths"], lbnl_dir=str(d)))
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
    ref = tranches.build_reference(m, now, regimes, c, 2026)
    best = tranches.build_reference(m, now, regimes, c, 2026, regime_override="best")
    assert ref.groupby("ba")["cost_per_kw"].apply(lambda x: (np.diff(x.values) >= -1e-9).all()).all()
    assert (best["cost_per_kw"] <= ref["cost_per_kw"] + 1e-9).all()


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
    for f in ("ic_tranches.csv", "ic_params.csv", "ic_weights.csv"):
        shutil.copy(ROOT / "tests/switch_toy" / f, run / "inputs")
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs"], cwd=run, capture_output=True,
                       text=True, env={**__import__("os").environ, "PYTHONPATH": str(run)})
    assert r.returncode == 0, r.stderr[-2000:]
    hr = pd.read_csv(run / "outputs/ic_headroom.csv")
    assert (hr.new_capacity_mw_weighted <= hr.tranche_mw + hr.freed_headroom_mw + 1e-6).all()
    w = pd.read_csv(run / "outputs/ic_gen_weights.csv").set_index("GENERATION_PROJECT")["ic_weight"]
    assert w["C-NG_CC"] == 1.0 and w["N-Wind-1"] == 0.75 and w["N-Central_PV-1"] == 1.0
    # new gas uses headroom: Central's weighted new capacity includes its new NG_CC builds
    bg = pd.read_csv(run / "outputs/BuildGen.csv")
    new_gas_c = bg[(bg.GEN_BLD_YRS_1 == "C-NG_CC") & (bg.GEN_BLD_YRS_2 >= 2020)].BuildGen.sum()
    c30 = hr[(hr.load_zone == "Central") & (hr.period == 2030)].iloc[0]
    assert c30.new_capacity_mw_weighted >= new_gas_c - 1e-6


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


def test_switch_case_write_with_zone_map(tmp_path, monkeypatch):
    from icsc import switch_case as sc
    tdir = tmp_path / "tr"
    tdir.mkdir()
    pd.DataFrame({"ba": ["p1", "p1", "p2", "p3"], "tranche": ["nu1", "gets", "nu1", "nu1"],
                  "tranche_type": ["network_upgrade", "gets", "network_upgrade", "network_upgrade"],
                  "max_mw": [100, 50, 80, 70], "cost_per_kw": [20, 15, 40, 30],
                  "available_year": [2026, 2028, 2026, 2026]}).to_csv(tdir / "tranches_reference.csv", index=False)
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    settings = {"interconnection_headroom": {"enabled": True, "tranches_dir": "tr"},
                "_zone_map": {"p1": "TX", "p2": "TX", "p3": "OK"}}
    gi = _gen_info().assign(gen_load_zone="TX")
    out = tmp_path / "case"
    out.mkdir()
    sc.write_case_inputs(gi, settings, out)
    t = pd.read_csv(out / "ic_tranches.csv")
    assert set(t["ic_tranche_zone"]) == {"TX"}          # p3 maps to OK, which isn't in the case
    assert t.set_index("IC_TRANCHE").loc["p1_nu1", "ic_tranche_available_year"] == 0
    assert t.set_index("IC_TRANCHE").loc["p1_gets", "ic_tranche_available_year"] == 2028
    assert t["ic_tranche_cost_per_mw"].tolist() == [20000, 15000, 40000]
    assert (out / "ic_weights.csv").exists() and (out / "ic_params.csv").exists()
    # disabled -> writes nothing
    out2 = tmp_path / "case2"
    out2.mkdir()
    sc.write_case_inputs(gi, {"interconnection_headroom": {"enabled": False}}, out2)
    assert not any(out2.iterdir())


def test_prepare_next_stage_chains_tranches(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pns", ROOT.parent / "switch/study_modules/prepare_next_stage.py")
    pns = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pns)
    inp, nxt, out = tmp_path / "in/2030/c", tmp_path / "in/2035/c", tmp_path / "out/2030/c"
    for d in (inp, nxt, out):
        d.mkdir(parents=True)
    pd.DataFrame({"IC_TRANCHE": ["p1_nu1", "p1_nu2"], "ic_tranche_zone": ["p1", "p1"],
                  "ic_tranche_max_mw": [100.0, 200.0], "ic_tranche_cost_per_mw": [1e4, 5e4],
                  "ic_tranche_available_year": [0, 0]}).to_csv(inp / "ic_tranches.csv", index=False)
    pd.DataFrame({"ic_tranche": ["p1_nu1", "p1_nu2"], "load_zone": ["p1", "p1"], "period": [2030, 2030],
                  "built_mw": [100.0, 30.0], "max_mw": [100.0, 200.0], "cost_per_mw": [1e4, 5e4]}).to_csv(
        out / "ic_tranches_built.csv", index=False)
    pns.chain_ic_tranches(inp, out, nxt, "c")
    ch = pd.read_csv(nxt / "ic_tranches.chained.c.csv").set_index("IC_TRANCHE")["ic_tranche_max_mw"]
    assert ch["p1_nu1"] == 0 and ch["p1_nu2"] == 170
    # a second stage reads the chained file, not the original
    out2, nxt2 = tmp_path / "out/2035/c", tmp_path / "in/2040/c"
    out2.mkdir(parents=True); nxt2.mkdir(parents=True)
    pd.DataFrame({"ic_tranche": ["p1_nu2"], "load_zone": ["p1"], "period": [2035],
                  "built_mw": [70.0], "max_mw": [170.0], "cost_per_mw": [5e4]}).to_csv(
        out2 / "ic_tranches_built.csv", index=False)
    pns.chain_ic_tranches(nxt, out2, nxt2, "c")
    ch2 = pd.read_csv(nxt2 / "ic_tranches.chained.c.csv").set_index("IC_TRANCHE")["ic_tranche_max_mw"]
    assert ch2["p1_nu2"] == 100
    # no headroom outputs -> nothing written
    pns.chain_ic_tranches(tmp_path / "in/none", out, tmp_path / "in/none2", "c")
