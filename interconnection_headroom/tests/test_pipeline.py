"""Run with: pytest -q  (from the project root)."""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from icsc import cli, estimate, geo, linkage, lbnl, reinforcement, synthetic, tranches

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
    assert len(up) == 3 * len(zr)  # plus the conventional-reinforcement backstop
    g = up[up.uprate == "gets"].merge(zr, on="ba")
    gets_cap = c["uprate_levels"][c["uprate_level"]]["gets"]["share_of_capacity"]
    assert np.allclose(g["max_mw_network"], gets_cap * g["base_capacity_mw"])
    # host mode (default): hosted MW = cap on H x the zone's curve-end saturation
    end = tr.groupby("ba")["sat_to"].max().reindex(g["ba"]).values
    assert (g["mode"] == "host").all() and np.allclose(g["max_mw"], gets_cap * g["base_capacity_mw"] * end)
    _, _, ref = tranches.apply_scenario(zr, tr, c, {})
    assert set(ref["uprate"]) == set(c["backstop_uprates"]) | set(c["baseline_uprates"])   # every scenario
    assert len(ref) == len(zr) * len(set(ref["uprate"]))

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
                        ("price_active", ("tranches", "reference_status"), "active"),
                        ("status_pooled", ("tranches", "status_pricing"), "pooled"),
                        ("status_own", ("tranches", "status_pricing"), "own"),
                        ("previous_defaults", ("tranches", "trend_freeze"), "none"),
                        ("new_line_gen", ("reinforcement", "per_mw_h"), "gen"),
                        ("new_line_s0", ("reinforcement", "per_mw_h"), "s0"),
                        ("new_line_stretch", ("reinforcement_mode",), "stretch"),
                        ("new_line_full_build", ("uprate_options", "conv_reinforcement", "cost_per_kw"),
                         {"reeds_reinforcement_x": 2.0}),
                        ("atts_stretch", ("atts_mode",), "stretch"),
                        ("reconductor_low_cost", ("uprate_options", "reconductor", "cost_per_kw"),
                         {"reeds_reinforcement_x": 0.5}),
                        ("reconductor_high_cost", ("uprate_options", "reconductor", "cost_per_kw"),
                         {"reeds_reinforcement_x": 1.0}),
                        ("gets_cost_pjm", ("uprate_options", "gets", "cost_per_kw", "per_kw_gen"), 15.2),
                        ("gets_cost_spp", ("uprate_options", "gets", "cost_per_kw", "per_kw_gen"), 33.7)]:
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


def test_trend_freeze_and_status_pricing(cfg, built):
    """Per-regime trend freeze (last sample queue year, never past reference_year) and the three
    status pricing rules (own / capped within status_cap_ratio x pooled / pooled)."""
    panel, _, c2z = built
    s = estimate.attach_saturation(lbnl.estimation_sample(lbnl.load_all(cfg, c2z), cfg), panel)
    m = estimate.fit(s, cfg)
    last = s.dropna(subset=["saturation", "network_cost_real", "capacity_mw", "queue_year"]).groupby("regime")["queue_year"].max()
    hier = geo.load_hierarchy(cfg["paths"]["hierarchy"])
    ref = cfg["tranches"]["reference_year"]
    z = tranches.zone_regimes(hier, m, cfg)
    for reg, yr in last.items():
        assert (z.loc[z["regime"] == reg, "trend_year"] == min(ref, int(yr))).all(), reg
    assert (z.loc[~z["regime_in_sample"], "trend_year"] == min(ref, int(last.max()))).all()
    assert (z["trend_year"] <= ref).all()
    unfrozen = tranches.zone_regimes(hier, m, dict(cfg, tranches=dict(cfg["tranches"], trend_freeze="none")))
    assert (unfrozen["trend_year"] == ref).all()

    pooled = m.pooled_status_effect("completed")
    lo, hi = cfg["tranches"]["status_cap_ratio"]
    for how in ("own", "capped", "pooled"):
        c = dict(cfg, tranches=dict(cfg["tranches"], status_pricing=how))
        zz = tranches.zone_regimes(hier, m, c)
        est = zz["status_effect_estimated"]
        if how == "own":
            assert np.allclose(zz["status_effect"], est)
        elif how == "pooled":
            assert np.allclose(zz["status_effect"], pooled)
        else:
            assert (zz["status_effect"] >= pooled + np.log(lo) - 1e-12).all()
            assert (zz["status_effect"] <= pooled + np.log(hi) + 1e-12).all()
            inside = est.between(pooled + np.log(lo), pooled + np.log(hi))
            assert np.allclose(zz.loc[inside, "status_effect"], est[inside])       # only out-of-band effects move
    # the frozen first step equals the model's prediction at the zone's trend year
    now = cli.start_saturation(panel, built[1], c2z, cfg, 2026).reset_index()
    zr, tr = tranches.build_reference(m, now, z, cfg, 2026)
    tc = cfg["tranches"]
    for ba in ["p80", "p37"]:
        row = tr[tr["ba"] == ba].iloc[0]
        mid = (row["sat_from"] + row["sat_to"]) / 2 if not row["beyond_support"] else m.sat_support
        want = m.to_cost(m.log_pred(mid, tc["reference_tech"], tc["reference_service"], tc["reference_capacity_mw"],
                                    z.at[ba, "trend_year"], m.base_regime, "active") + z.at[ba, "regime_effect"])
        assert row["cost_per_kw"] == pytest.approx(float(min(want[0], tc["max_cost_per_kw"])))


def test_reinforcement_zone_costs_and_pricing():
    """Capacity-weighted quantiles by zone, transreg / national fallback, and ReEDS-based pricing.
    Small hand-built frames: test fixtures, not results."""
    assert reinforcement.weighted_quantile(np.array([1, 2, 3]), np.array([1, 1, 1]), 0.5) == 2
    assert reinforcement.weighted_quantile(np.array([1, 100]), np.array([9, 1]), 0.5) == 1   # weight, not count
    sites = pd.DataFrame({"sc_point_gid": [1, 2, 3, 4], "FIPS": ["00001", "00001", "00002", "00009"],
                          "cost_reinforcement_usd_per_mw": [100e3, 300e3, 200e3, 50e3]})
    cap = pd.DataFrame({"sc_point_gid": [1, 2, 2, 3, 4], "capacity": [10, 5, 25, 10, 0]})   # 2: UPV + wind
    c2z = pd.DataFrame({"FIPS": ["00001", "00002", "00009"], "ba": ["pA", "pB", "pC"]})
    hier = pd.DataFrame({"ba": ["pA", "pB", "pC", "pD"], "transreg": ["T1", "T2", "T2", "T3"]})
    z = reinforcement.zone_costs(sites, cap, c2z, hier, cpi_factor=1.1).set_index("ba")
    assert z.at["pA", "capacity_mw"] == 40 and z.at["pA", "source"] == "zone"
    assert z.at["pA", "reinforcement_median_per_kw"] == pytest.approx(300 * 1.1)   # 30 of 40 MW at $300/kW
    assert z.at["pC", "source"] == "transreg" and z.at["pC", "reinforcement_median_per_kw"] == pytest.approx(220)
    assert z.at["pD", "source"] == "national"
    assert (z["reinforcement_p10_per_kw"] <= z["reinforcement_median_per_kw"]).all()
    assert (z["reinforcement_median_per_kw"] <= z["reinforcement_p90_per_kw"]).all()
    zc = z["reinforcement_median_per_kw"]
    assert tranches.uprate_cost({"cost_per_kw": "reeds_reinforcement"}, "pB", zc) == pytest.approx(220)
    assert tranches.uprate_cost({"cost_per_kw": 80}, "pB", None) == 80
    with pytest.raises(KeyError):
        tranches.uprate_cost({"cost_per_kw": "reeds_reinforcement"}, "pZ", zc)


def test_reinforcement_table_and_backstop(cfg):
    """The committed zone table covers every zone in the pipeline dollar year; the conventional-reinforcement
    backstop uses it at 1.0 x (host mode) from the first model period; full new build (2.0 x) is a
    sensitivity."""
    t = pd.read_csv(Path(cfg["_root"]) / cfg["reinforcement"]["zone_table"])
    hier = geo.load_hierarchy(cfg["paths"]["hierarchy"])
    assert set(t["ba"]) == set(hier["ba"]) and (t["dollar_year"] == cfg["dollar_year"]).all()
    assert (t["reinforcement_median_per_kw"] > 0).all()
    if (Path(cfg["_root"]) / cfg["reinforcement"]["h5"]).exists():   # rebuild from the raw ReEDS files
        r = reinforcement.build(cfg).set_index("ba").reindex(t["ba"])
        assert np.allclose(r["reinforcement_median_per_kw"], t["reinforcement_median_per_kw"], atol=0.01)
    zones = pd.DataFrame({"ba": ["p1", "p80"], "base_capacity_mw": [1000.0, 2000.0], "start_saturation": [0.1, 0.9]})
    tr = pd.DataFrame({"ba": ["p1", "p1", "p80"], "sat_from": [0.1, 0.4, 0.9], "sat_to": [0.4, 0.67, 0.95],
                       "cost_per_kw": [50.0, 60.0, 90.0]})
    want = t.set_index("ba")["reinforcement_median_per_kw"]
    # default host mode: conventional reinforcement = ReEDS reinforcement as is (ReEDS's 50% factor is
    # conventional reinforcement / reconductoring as standard practice), per kW of generation, no conversion
    opts = cfg["uprate_options"]
    assert cfg["backstop_uprates"] == ["conv_reinforcement"] and "new_line" not in opts
    assert opts["conv_reinforcement"]["cost_per_kw"] == {"reeds_reinforcement_x": 1.0}
    assert opts["conv_reinforcement"]["label"] == "conventional reinforcement (ReEDS)"
    assert opts["conv_reinforcement"]["available_year"] == 0                          # first model period
    _, _, up = tranches.apply_scenario(zones, tr, cfg, {})
    cr = up[up["uprate"] == "conv_reinforcement"].set_index("ba")
    assert cfg["reinforcement_mode"] == "host" and (cr["mode"] == "host").all()
    assert (cr["label"] == "conventional reinforcement (ReEDS)").all()
    assert np.allclose(cr["cost_per_kw"], want[cr.index]) and cr["gen_mw_per_mw_h"].isna().all()
    assert (cr["available_year"] == 0).all()
    # its cap is a numerical bound on hosted MW, not an adoption cap: not converted in host mode
    assert np.allclose(cr["max_mw"], cr["max_mw_network"])
    full = cli.load_cfg(str(ROOT / "sensitivities" / "new_line_full_build.yaml"))
    _, _, uf = tranches.apply_scenario(zones, tr, full, {})
    uf = uf[uf["uprate"] == "conv_reinforcement"].set_index("ba")
    assert (uf["mode"] == "host").all() and np.allclose(uf["cost_per_kw"], 2.0 * want[uf.index])
    _, _, ug = tranches.apply_scenario(zones, tr, cfg, {"uprates": ["gets"]})
    assert cfg["atts_mode"] == "host"
    assert (ug.loc[ug["uprate"].isin(["gets", "reconductor"]), "mode"] == "host").all()   # host by default
    st = cli.load_cfg(str(ROOT / "sensitivities" / "atts_stretch.yaml"))
    _, _, us = tranches.apply_scenario(zones, tr, st, {})
    assert (us.loc[us["uprate"].isin(["gets", "reconductor"]), "mode"] == "stretch").all()  # option
    assert (us.loc[us["uprate"] == "conv_reinforcement", "mode"] == "host").all()
    # stretch mode: $ per MW of H = ReEDS $/MW-gen x gen MW hosted per MW of H
    factors = {"curve_end": {"p1": 0.67, "p80": 0.95}, "s0": {"p1": 0.1, "p80": 0.9}, "gen": {"p1": 1.0, "p80": 1.0}}
    for how, f in factors.items():
        c = dict(cfg, reinforcement_mode="stretch", reinforcement=dict(cfg["reinforcement"], per_mw_h=how))
        _, _, up = tranches.apply_scenario(zones, tr, c, {})
        cr = up[up["uprate"] == "conv_reinforcement"].set_index("ba")
        assert (cr["mode"] == "stretch").all()
        for ba in ("p1", "p80"):
            assert cr.at[ba, "cost_per_kw"] == pytest.approx(want[ba] * f[ba]), (how, ba)
            assert cr.at[ba, "reeds_cost_per_kw_gen"] == pytest.approx(want[ba])
            assert cr.at[ba, "gen_mw_per_mw_h"] == pytest.approx(f[ba])
        if how == "curve_end":
            assert np.allclose(cr["cost_per_kw"] / cr["gen_mw_per_mw_h"], want[cr.index])
    assert np.allclose(cr["max_mw"], opts["conv_reinforcement"]["share_of_capacity"] * zones.set_index("ba")["base_capacity_mw"])
    assert cfg["reinforcement"]["per_mw_h"] == "curve_end"
    with pytest.raises(ValueError, match="reinforcement_mode"):
        tranches.apply_scenario(zones, tr, dict(cfg, reinforcement_mode="nope"), {})
    with pytest.raises(ValueError, match="atts_mode"):
        tranches.apply_scenario(zones, tr, dict(cfg, atts_mode="nope"), {})


def test_uprate_levels_and_sourced_costs(cfg):
    """GETs and advanced-conductor reconductoring caps and first years come from the adoption level; costs
    from the sourced forms (GETs $ per kW of generation enabled; advanced reconductoring = a third of
    greenfield = 0.67 x ReEDS reinforcement). Host mode (default): costs per kW of generation hosted,
    unconverted; caps defined on H (share of H0) and converted to hosted MW with the curve-end saturation.
    Stretch mode (atts_mode: stretch): caps in MW of H, costs converted to $ per kW of H. Conventional
    reinforcement keeps host mode and its own cap in every level."""
    t = pd.read_csv(Path(cfg["_root"]) / cfg["reinforcement"]["zone_table"]).set_index("ba")["reinforcement_median_per_kw"]
    zones = pd.DataFrame({"ba": ["p1", "p80"], "base_capacity_mw": [1000.0, 2000.0], "start_saturation": [0.1, 0.9]})
    tr = pd.DataFrame({"ba": ["p1", "p1", "p80"], "sat_from": [0.1, 0.4, 0.9], "sat_to": [0.4, 0.67, 0.95],
                       "cost_per_kw": [50.0, 60.0, 90.0]})
    end = {"p1": 0.67, "p80": 0.95}
    hier = geo.load_hierarchy(cfg["paths"]["hierarchy"]).set_index("ba")["transreg"]
    gets_cost = cfg["uprate_options"]["gets"]["cost_per_kw"]
    levels = cfg["uprate_levels"]
    assert cfg["uprate_level"] == "s0"                                               # default: current trajectory
    assert set(levels) == {"s0", "s0_mandate_only", "planned", "reform", "reform_techmax"}
    order = ("s0_mandate_only", "s0", "planned", "reform", "reform_techmax")
    for name in ("gets", "reconductor"):
        caps = [levels[lv][name]["share_of_capacity"] for lv in order]
        assert caps == sorted(caps), (name, caps)                                     # levels nest
    assert levels["s0_mandate_only"]["gets"]["share_of_capacity"] == 0
    assert levels["s0"]["gets"]["share_of_capacity"] == pytest.approx(0.01 * 0.25)  # 1% of system x 25% (Liftoff p. 88)
    assert levels["planned"]["gets"]["share_of_capacity"] == pytest.approx(317 / 742 * 0.25, abs=5e-4)
    assert levels["reform"]["gets"]["share_of_capacity"] == pytest.approx(317 / 742 * 0.44, abs=5e-4)
    assert levels["planned"]["reconductor"]["share_of_capacity"] == pytest.approx(0.15 * (2 - 1))
    assert levels["reform"]["reconductor"]["share_of_capacity"] == pytest.approx(0.15 * (3 - 1))     # Liftoff high
    assert levels["reform_techmax"]["reconductor"]["share_of_capacity"] == pytest.approx(0.98 * 0.80 * (2 - 1))
    # S0 cases: reference and atts_s0 both get the s0 level; planned / reform only when selected
    for name in ("reference", "atts_s0", "best_regime"):
        assert (cfg["scenarios"][name] or {}).get("uprate_level", cfg["uprate_level"]) == "s0", name
    _, _, ref = tranches.apply_scenario(zones, tr, cfg, cfg["scenarios"]["reference"])
    r = ref.set_index(["uprate", "ba"])
    assert r.loc[("gets", "p1"), "max_mw_network"] == pytest.approx(0.0025 * 1000) and r.loc[("gets", "p1"), "level"] == "s0"
    assert r.loc[("gets", "p1"), "max_mw"] == pytest.approx(0.0025 * 1000 * end["p1"])   # hosted MW
    # advanced conductors: a third of greenfield = 0.67 x ReEDS ([GL24T] p. 31), inside the sourced 0.5-1.0 x
    assert cfg["uprate_options"]["reconductor"]["cost_per_kw"] == {"reeds_reinforcement_x": 0.67}
    stretch = cli.load_cfg(str(ROOT / "sensitivities" / "atts_stretch.yaml"))
    assert cfg["scenarios"]["atts_reform_techmax"]["uprate_level"] == "reform_techmax"
    for lv in order:
        for c, mode in ((cfg, "host"), (stretch, "stretch")):
            _, _, up = tranches.apply_scenario(zones, tr, c, {"uprates": ["gets", "reconductor"], "uprate_level": lv})
            u = up.set_index(["uprate", "ba"])
            for name in ("gets", "reconductor"):
                for ba, h0 in (("p1", 1000.0), ("p80", 2000.0)):
                    row = u.loc[(name, ba)]
                    cap_h = levels[lv][name]["share_of_capacity"] * h0
                    assert row["max_mw_network"] == pytest.approx(cap_h)                # cap defined on H
                    assert row["available_year"] == levels[lv][name]["available_year"]
                    assert row["mode"] == mode and row["level"] == lv
                    gen_cost = (gets_cost["per_kw_gen_by_transreg"].get(hier[ba], gets_cost["per_kw_gen"])
                                if name == "gets" else 0.67 * t[ba])
                    assert row["cost_per_kw_gen"] == pytest.approx(gen_cost)
                    assert row["gen_mw_per_mw_h"] == pytest.approx(end[ba])
                    if mode == "host":   # hosted MW = MW of H x curve-end saturation; $/kW-gen unconverted
                        assert row["max_mw"] == pytest.approx(cap_h * end[ba])
                        assert row["cost_per_kw"] == pytest.approx(gen_cost)
                    else:                # MW of H; $ per kW of H = $/kW-gen x curve-end saturation
                        assert row["max_mw"] == pytest.approx(cap_h)
                        assert row["cost_per_kw"] == pytest.approx(gen_cost * end[ba])
                    # same total spend at the cap either way (MW x $/kW)
                    assert row["max_mw"] * row["cost_per_kw"] == pytest.approx(cap_h * gen_cost * end[ba])
            cr = u.loc["conv_reinforcement"]
            assert (cr["mode"] == "host").all() and np.allclose(cr["cost_per_kw"], t[cr.index])
            assert cr["level"].isna().all()                                           # not capped by level
    # GETs cost by study region: PJM (RMI) $15.2, SPP (Brattle) $33.7, elsewhere the SPP value
    assert hier["p80"] == "PJM" and gets_cost["per_kw_gen_by_transreg"]["PJM"] == pytest.approx(0.1e9 / 6.6e6, abs=0.1)
    assert gets_cost["per_kw_gen_by_transreg"]["SPP"] == pytest.approx(90e6 / 2.670e6, abs=0.1)
    # scenario-level override; unknown level is an error
    assert cfg["scenarios"]["atts_reform"]["uprate_level"] == "reform"
    with pytest.raises(ValueError, match="uprate_level"):
        tranches.apply_scenario(zones, tr, cfg, {"uprates": ["gets"], "uprate_level": "nope"})
    # the previous placeholders still load (previous_defaults.yaml: uprate_level null)
    old = cli.load_cfg(str(ROOT / "sensitivities" / "previous_defaults.yaml"))
    _, _, up = tranches.apply_scenario(zones, tr, old, {"uprates": ["gets", "reconductor"]})
    u = up.set_index(["uprate", "ba"])
    assert u.loc[("conv_reinforcement", "p1"), "cost_per_kw"] == 250 and u.loc[("conv_reinforcement", "p1"), "mode"] == "stretch"
    assert u.loc[("gets", "p1"), "cost_per_kw"] == 20 and u.loc[("reconductor", "p1"), "max_mw"] == 300
    low = cli.load_cfg(str(ROOT / "sensitivities" / "reconductor_low_cost.yaml"))
    high = cli.load_cfg(str(ROOT / "sensitivities" / "reconductor_high_cost.yaml"))
    for c, f in ((low, 0.5), (cfg, 0.67), (high, 1.0)):
        _, _, up = tranches.apply_scenario(zones, tr, c, {"uprates": ["reconductor"], "uprate_level": "planned"})
        row = up.set_index(["uprate", "ba"]).loc[("reconductor", "p1")]
        assert row["cost_per_kw_gen"] == pytest.approx(f * t["p1"]) and row["mode"] == "host"
        assert row["cost_per_kw"] == pytest.approx(f * t["p1"])                       # host: unconverted


def test_gets_cost_sensitivity(cfg):
    """GETs-cost sensitivity: one study's cost everywhere (PJM RMI $15.2, SPP Brattle $33.7 per kW of
    generation) vs the central split (PJM $15.2, elsewhere $33.7); host mode prices hosted MW at that cost
    unconverted, and only the cost changes, not the cap."""
    hier = geo.load_hierarchy(cfg["paths"]["hierarchy"]).set_index("ba")["transreg"]
    pjm = next(b for b in hier.index if hier[b] == "PJM")
    other = next(b for b in hier.index if hier[b] not in ("PJM", "SPP"))
    zones = pd.DataFrame({"ba": [pjm, other], "base_capacity_mw": [1000.0, 2000.0], "start_saturation": [0.1, 0.9]})
    tr = pd.DataFrame({"ba": [pjm, pjm, other], "sat_from": [0.1, 0.4, 0.9], "sat_to": [0.4, 0.67, 0.95],
                       "cost_per_kw": [50.0, 60.0, 90.0]})
    want = {"config": {pjm: 15.2, other: 33.7},
            "gets_cost_pjm": {pjm: 15.2, other: 15.2},
            "gets_cost_spp": {pjm: 33.7, other: 33.7}}
    caps = None
    for name, w in want.items():
        path = ROOT / ("config.yaml" if name == "config" else f"sensitivities/{name}.yaml")
        c = cli.load_cfg(str(path))
        _, _, up = tranches.apply_scenario(zones, tr, c, {"uprate_level": "planned"})
        g = up[up["uprate"] == "gets"].set_index("ba")
        assert (g["mode"] == "host").all(), name
        for ba, v in w.items():
            assert g.at[ba, "cost_per_kw_gen"] == pytest.approx(v), (name, ba)
            assert g.at[ba, "cost_per_kw"] == pytest.approx(v), (name, ba)             # unconverted
        caps = g["max_mw"] if caps is None else caps
        assert np.allclose(g["max_mw"], caps)                                         # cap unchanged
        # the other options are untouched
        r = up[up["uprate"] != "gets"]
        assert set(r["uprate"]) == {"reconductor", "conv_reinforcement"}
    assert c["paths"]["outputs"].endswith("sens_gets_cost_spp")


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
    assert {"headroom_dual_units", "headroom_value_usd_per_kw_yr"} <= set(hr.columns)           # v3.1: units
    assert (hr.headroom_dual_units == "NPV $ (base year) per weighted MW (raw constraint dual)").all()
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


def _toy_run(tmp_path, zones, tranches_df, uprates_df):
    """Copy the 3-zone toy, make it an LP (no unit sizes / minimum builds, so duals exist), add the
    module and the given IC inputs, solve with HiGHS. Returns the run folder."""
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
    gi = pd.read_csv(run / "inputs/gen_info.csv", na_values=".")
    gi["gen_unit_size"] = np.nan
    gi["gen_min_build_capacity"] = 0
    gi.to_csv(run / "inputs/gen_info.csv", index=False, na_rep=".")
    for f in ("ic_params.csv", "ic_weights.csv"):
        shutil.copy(ROOT / "tests/switch_toy" / f, run / "inputs")
    zones.to_csv(run / "inputs/ic_zones.csv", index=False)
    tranches_df.to_csv(run / "inputs/ic_tranches.csv", index=False)
    uprates_df.to_csv(run / "inputs/ic_uprates.csv", index=False)
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs", "--suffixes", "dual"], cwd=run,
                       capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(run)})
    assert r.returncode == 0, r.stderr[-2000:] + r.stdout[-2000:]
    return run


@pytest.mark.skipif(shutil.which("switch") is None, reason="switch_model not installed")
def test_switch_toy_conv_reinforcement_hosts_at_reeds_cost(tmp_path):
    """North sits past the data edge (one short edge step). New wind there is hosted by host-mode
    conventional reinforcement: it pays only its $/MW of generation (no step or release cost on top), H does not
    change, and the marginal headroom price equals the line cost."""
    zones = pd.DataFrame({"IC_ZONE": ["N", "C", "S"], "ic_zone_load_zone": ["North", "Central", "South"],
                          "ic_base_capacity_mw": [10.0, 10.0, 10.0], "ic_start_saturation": [0.8, 0.3, 0.3],
                          "ic_release_cost_per_mw": [50000.0, 50000.0, 100000.0]})
    steps = pd.DataFrame({"IC_TRANCHE": ["N_nu1", "C_nu1", "C_nu2", "S_nu1", "S_nu2"],
                          "ic_tranche_zone": ["N", "C", "C", "S", "S"],
                          "ic_tranche_width": [0.05, 0.3, 5, 0.1, 5],
                          "ic_tranche_cost_per_mw": [50000.0, 50000.0, 5e6, 100000.0, 5e6],
                          "ic_tranche_available_year": [0] * 5})
    line_cost = 200000.0
    ups = pd.DataFrame({"IC_UPRATE": ["N_gets", "N_conv"], "ic_uprate_zone": ["N", "N"],
                        "ic_uprate_type": ["gets", "conv_reinforcement"], "ic_uprate_max_mw": [0.0, 100.0],
                        "ic_uprate_cost_per_mw": [20000.0, line_cost], "ic_uprate_available_year": [0, 0],
                        "ic_uprate_mode": ["stretch", "host"]})
    run = _toy_run(tmp_path, zones, steps, ups)
    net = pd.read_csv(run / "outputs/ic_network.csv").set_index(["ic_zone", "period"])
    spend = pd.read_csv(run / "outputs/ic_spend.csv")
    n = net.loc[("N", 2030)]
    assert n.hosted_mw > 1 and n.deliberate_mw_added == 0                          # hosted, H unchanged
    assert n.headroom_released_mw == pytest.approx(0, abs=1e-9)                    # no release on top
    assert n.headroom_from_curve_mw <= 0.05 * 10 + 1e-6                            # only the edge step
    assert n.curve_end_saturation == pytest.approx(0.85)
    assert n.hosted_network_mw_implied == pytest.approx(n.hosted_mw / 0.85)
    sp = spend[(spend.ic_zone == "N") & (spend.period == 2030)].set_index("type")
    assert sp.loc["conv_reinforcement", "overnight_cost"] == pytest.approx(n.hosted_mw * line_cost)
    assert sp.loc["conv_reinforcement", "generation_mw_enabled"] == pytest.approx(n.hosted_mw)
    # reactive spend is only the edge step: hosted MW carry no empirical cost
    assert sp.loc["reactive_upgrades", "overnight_cost"] == pytest.approx(n.headroom_from_curve_mw * 50000.0)
    # marginal $/kW of headroom in North 2030 = the line cost (dual / (crf x discount factor))
    hr = pd.read_csv(run / "outputs/ic_headroom.csv").set_index(["load_zone", "period"])
    from switch_model.financials import (capital_recovery_factor, future_to_present_value,
                                         uniform_series_to_present_value)
    fin = pd.read_csv(run / "inputs/financials.csv")
    r, base_year = fin["interest_rate"].iat[0], fin["base_financial_year"].iat[0]
    dr = fin["discount_rate"].iat[0]
    per = pd.read_csv(run / "inputs/periods.csv").set_index("INVESTMENT_PERIOD")
    s, e = per.loc[2030, "period_start"], per.loc[2030, "period_end"]
    # Switch's bring_annual_costs_to_base_year for period 2030
    pv = uniform_series_to_present_value(dr, e - s + 1) * future_to_present_value(dr, s - base_year)
    marginal = abs(hr.loc[("North", 2030), "headroom_dual"]) / (capital_recovery_factor(r, 40) * pv) / 1000
    assert marginal == pytest.approx(line_cost / 1000, rel=1e-3)
    # carry-forward file for myopic chaining: hosted MW in the last period, none of it unused here
    hb = pd.read_csv(run / "outputs/ic_hosted_built.csv").query("period == 2030").set_index("ic_zone")
    assert hb.at["N", "hosted_mw"] == pytest.approx(n.hosted_mw) and hb.at["N", "unused_mw"] == pytest.approx(0, abs=1e-6)
    print(f"marginal headroom price North 2030: ${marginal:.2f}/kW (line cost ${line_cost / 1000:.0f}/kW)")


@pytest.mark.skipif(shutil.which("switch") is None, reason="switch_model not installed")
def test_switch_toy_no_double_counting(tmp_path):
    """Advanced-conductor reconductoring in stretch mode (atts_mode: stretch, a sensitivity) and
    conventional reinforcement (host) in the same zone: reconductoring raises H (and generation using the stretched steps and the release pays their
    empirical costs); hosted MW pay only the conventional-reinforcement cost, with no step or release
    cost on top, and add nothing to H."""
    zones = pd.DataFrame({"IC_ZONE": ["N", "C", "S"], "ic_zone_load_zone": ["North", "Central", "South"],
                          "ic_base_capacity_mw": [10.0, 10.0, 10.0], "ic_start_saturation": [0.8, 0.3, 0.3],
                          "ic_release_cost_per_mw": [50000.0, 50000.0, 100000.0]})
    steps = pd.DataFrame({"IC_TRANCHE": ["N_nu1", "C_nu1", "C_nu2", "S_nu1", "S_nu2"],
                          "ic_tranche_zone": ["N", "C", "C", "S", "S"],
                          "ic_tranche_width": [0.05, 0.3, 5, 0.1, 5],
                          "ic_tranche_cost_per_mw": [50000.0, 50000.0, 5e6, 100000.0, 5e6],
                          "ic_tranche_available_year": [0] * 5})
    rc_cost, conv_cost = 30000.0, 200000.0
    ups = pd.DataFrame({"IC_UPRATE": ["N_reconductor", "N_conv"], "ic_uprate_zone": ["N", "N"],
                        "ic_uprate_type": ["reconductor", "conv_reinforcement"], "ic_uprate_max_mw": [1.0, 100.0],
                        "ic_uprate_cost_per_mw": [rc_cost, conv_cost], "ic_uprate_available_year": [0, 0],
                        "ic_uprate_mode": ["stretch", "host"]})
    run = _toy_run(tmp_path, zones, steps, ups)
    n = pd.read_csv(run / "outputs/ic_network.csv").set_index(["ic_zone", "period"]).loc[("N", 2030)]
    spend = pd.read_csv(run / "outputs/ic_spend.csv")
    sp = spend[(spend.ic_zone == "N") & (spend.period == 2030)].set_index("type")
    assert n.deliberate_mw_added == pytest.approx(1.0) and n.hosted_mw > 1      # both used
    # reconductoring raises H only; hosted MW are not on H
    assert sp.loc["reconductor", "network_mw_added"] == pytest.approx(n.deliberate_mw_added)
    assert sp.loc["reconductor", "overnight_cost"] == pytest.approx(1.0 * rc_cost)
    assert n.headroom_released_mw <= 0.8 * 1.0 + 1e-6 and n.headroom_from_curve_mw <= 0.05 * 11 + 1e-6
    # empirical (reactive) spend covers only steps + release; none of it is on hosted MW
    assert sp.loc["reactive_upgrades", "generation_mw_enabled"] == pytest.approx(
        n.headroom_from_curve_mw + n.headroom_released_mw)
    assert sp.loc["reactive_upgrades", "overnight_cost"] == pytest.approx(
        (n.headroom_from_curve_mw + n.headroom_released_mw) * 50000.0)
    # hosted MW pay the conventional-reinforcement cost once, nothing else
    assert sp.loc["conv_reinforcement", "overnight_cost"] == pytest.approx(n.hosted_mw * conv_cost)
    assert sp.loc["conv_reinforcement", "generation_mw_enabled"] == pytest.approx(n.hosted_mw)
    total = sp["overnight_cost"].sum()
    assert total == pytest.approx(rc_cost + n.hosted_mw * conv_cost
                                  + (n.headroom_from_curve_mw + n.headroom_released_mw) * 50000.0)


@pytest.mark.skipif(shutil.which("switch") is None, reason="switch_model not installed")
def test_switch_toy_atts_host_mode(tmp_path):
    """GETs and advanced-conductor reconductoring in host mode (the default), with conventional
    reinforcement, in a zone past the data edge: each hosts generation directly at its own $ per MW of
    generation, none changes H or releases headroom, hosted MW pay no step or release cost, and the LP
    takes them in merit order (GETs, then advanced conductors at 0.67 x the conventional cost, then
    conventional reinforcement), so advanced conductors are no longer dominated."""
    zones = pd.DataFrame({"IC_ZONE": ["N", "C", "S"], "ic_zone_load_zone": ["North", "Central", "South"],
                          "ic_base_capacity_mw": [10.0, 10.0, 10.0], "ic_start_saturation": [0.8, 0.3, 0.3],
                          "ic_release_cost_per_mw": [50000.0, 50000.0, 100000.0]})
    steps = pd.DataFrame({"IC_TRANCHE": ["N_nu1", "C_nu1", "C_nu2", "S_nu1", "S_nu2"],
                          "ic_tranche_zone": ["N", "C", "C", "S", "S"],
                          "ic_tranche_width": [0.05, 0.3, 5, 0.1, 5],
                          "ic_tranche_cost_per_mw": [50000.0, 50000.0, 5e6, 100000.0, 5e6],
                          "ic_tranche_available_year": [0] * 5})
    conv_cost = 200000.0
    cost = {"gets": 33700.0, "reconductor": 0.67 * conv_cost, "conv_reinforcement": conv_cost}
    cap = {"gets": 0.2, "reconductor": 0.4, "conv_reinforcement": 100.0}       # hosted MW
    ups = pd.DataFrame({"IC_UPRATE": [f"N_{k}" for k in cost], "ic_uprate_zone": ["N"] * 3,
                        "ic_uprate_type": list(cost), "ic_uprate_max_mw": list(cap.values()),
                        "ic_uprate_cost_per_mw": list(cost.values()), "ic_uprate_available_year": [0] * 3,
                        "ic_uprate_mode": ["host"] * 3})
    run = _toy_run(tmp_path, zones, steps, ups)
    n = pd.read_csv(run / "outputs/ic_network.csv").set_index(["ic_zone", "period"]).loc[("N", 2030)]
    spend = pd.read_csv(run / "outputs/ic_spend.csv")
    sp = spend[(spend.ic_zone == "N") & (spend.period == 2030)].set_index("type")
    # H unchanged, nothing released: host uprates don't stretch
    assert n.deliberate_mw_added == pytest.approx(0, abs=1e-6) and n.headroom_released_mw == pytest.approx(0, abs=1e-6)
    # merit order: GETs and advanced conductors at their caps before conventional reinforcement
    assert n.hosted_mw > cap["gets"] + cap["reconductor"] + 1e-3
    for k in ("gets", "reconductor"):
        assert sp.loc[k, "generation_mw_enabled"] == pytest.approx(cap[k], rel=1e-6), k
    assert sp.loc["conv_reinforcement", "generation_mw_enabled"] == pytest.approx(
        n.hosted_mw - cap["gets"] - cap["reconductor"], rel=1e-6)
    # each pays its own $/MW of generation hosted, once
    for k in cost:
        assert sp.loc[k, "overnight_cost"] == pytest.approx(sp.loc[k, "generation_mw_enabled"] * cost[k]), k
        # implied network MW at the curve-end saturation (0.8 + 0.05)
        assert sp.loc[k, "network_mw_added"] == pytest.approx(sp.loc[k, "generation_mw_enabled"] / 0.85), k
    # reactive spend is only the edge step; none of it is on hosted MW
    assert sp.loc["reactive_upgrades", "generation_mw_enabled"] == pytest.approx(n.headroom_from_curve_mw)
    assert sp.loc["reactive_upgrades", "overnight_cost"] == pytest.approx(n.headroom_from_curve_mw * 50000.0)
    assert sp["overnight_cost"].sum() == pytest.approx(
        sum(sp.loc[k, "generation_mw_enabled"] * cost[k] for k in cost) + n.headroom_from_curve_mw * 50000.0)


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
    settings = {"interconnection_headroom": {"enabled": True, "tranches_dir": "tr", "scenario": "reference"},
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


def test_prepare_next_stage_chains_hosted_headroom(tmp_path):
    """Host-mode conventional reinforcement doesn't change H or s0; their unused hosted MW carry forward as free headroom,
    and their cap shrinks by what was built. Stretch uprates still grow H."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pns", ROOT.parent / "switch/study_modules/prepare_next_stage.py")
    pns = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pns)
    inp, nxt, out = tmp_path / "in/2030/c", tmp_path / "in/2035/c", tmp_path / "out/2030/c"
    for d in (inp, nxt, out):
        d.mkdir(parents=True)
    pd.DataFrame({"IC_ZONE": ["p1"], "ic_zone_load_zone": ["p1"], "ic_base_capacity_mw": [1000.0],
                  "ic_start_saturation": [0.7], "ic_release_cost_per_mw": [1e4]}).to_csv(inp / "ic_zones.csv", index=False)
    pd.DataFrame({"IC_TRANCHE": ["p1_nu1"], "ic_tranche_zone": ["p1"], "ic_tranche_width": [0.05],
                  "ic_tranche_cost_per_mw": [1e4], "ic_tranche_available_year": [0]}).to_csv(inp / "ic_tranches.csv", index=False)
    pd.DataFrame({"IC_UPRATE": ["p1_gets", "p1_conv"], "ic_uprate_zone": ["p1", "p1"],
                  "ic_uprate_type": ["gets", "conv_reinforcement"], "ic_uprate_max_mw": [100.0, 10000.0],
                  "ic_uprate_cost_per_mw": [2e4, 3e5], "ic_uprate_available_year": [0, 0],
                  "ic_uprate_mode": ["stretch", "host"]}).to_csv(inp / "ic_uprates.csv", index=False)
    pd.DataFrame({"ic_tranche": ["p1_nu1"], "ic_zone": ["p1"], "period": [2030], "used_mw": [55.0]}).to_csv(
        out / "ic_tranches_built.csv", index=False)
    pd.DataFrame({"ic_uprate": ["p1_gets", "p1_conv"], "ic_zone": ["p1", "p1"], "period": [2030, 2030],
                  "built_mw": [100.0, 400.0]}).to_csv(out / "ic_uprates_built.csv", index=False)
    pd.DataFrame({"ic_zone": ["p1"], "period": [2030], "released_mw": [70.0]}).to_csv(out / "ic_release_built.csv", index=False)
    pd.DataFrame({"ic_zone": ["p1"], "period": [2030], "hosted_mw": [400.0], "unused_mw": [150.0]}).to_csv(
        out / "ic_hosted_built.csv", index=False)
    pns.chain_ic_inputs(inp, out, nxt, "c")
    z = pd.read_csv(nxt / "ic_zones.chained.c.csv").iloc[0]
    assert z.ic_base_capacity_mw == 1100                                  # GETs only; conventional reinforcement adds no H
    assert np.isclose(z.ic_start_saturation, (0.7 * 1000 + 55 + 70) / 1100, atol=1e-6)   # hosted MW not on H
    assert z.ic_hosted_headroom_mw == 150                                 # unused hosted headroom carries forward
    u = pd.read_csv(nxt / "ic_uprates.chained.c.csv").set_index("IC_UPRATE")["ic_uprate_max_mw"]
    assert u["p1_conv"] == 9600 and u["p1_gets"] == 0


def test_s0_cases_default_to_atts_s0():
    """pg_to_switch cases without an explicit scenario get the current-trajectory baseline (atts_s0)."""
    import inspect
    import yaml
    from icsc import switch_case as sc
    s = yaml.safe_load(open(ROOT.parent / "pg/settings/interconnection_headroom.yml"))
    assert s["interconnection_headroom"]["scenario"] == "atts_s0"
    assert 'ic.get("scenario", "atts_s0")' in inspect.getsource(sc.write_case_inputs)
