"""Run with: pytest -q  (from build_rate/). Small hand-built frames are test fixtures, not results."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(REPO))

from build_rate.brc import cli, groups, rates, switch_case  # noqa: E402

CFG = cli.load_cfg(str(ROOT / "config.yaml"))


# ---------------------------------------------------------------------------- pipeline

def _additions():
    rows = []
    for y, w, s in [(2021, 10, 20), (2022, 8, 22), (2023, 6, 24), (2024, 4, 26), (2025, 2, 28)]:
        rows += [{"group": "wind_onshore", "transreg": "SPP", "year": y, "mw": w * 0.75},
                 {"group": "wind_onshore", "transreg": "MISO", "year": y, "mw": w * 0.25},
                 {"group": "solar", "transreg": "ERCOT", "year": y, "mw": s}]
    rows.append({"group": "solar", "transreg": None, "year": 2025, "mw": 1.0})   # unmapped: national only
    return pd.DataFrame(rows)


def test_base_rates_and_r0_rules():
    b = rates.base_rates(_additions(), 2021, 2025)
    nat = b[b["region"] == "national"].set_index(["group", "year"])["mw"]
    assert nat[("wind_onshore", 2021)] == 10 and nat[("solar", 2025)] == 29
    assert (b[b["region"] == "MISO"]["mw"] >= 0).all()
    low, cen, high = (rates.r0(b, CFG["r0_rule"][k]) for k in ("low", "central", "high"))
    assert low["wind_onshore"] == pytest.approx(4) and cen["wind_onshore"] == pytest.approx(6)
    assert high["wind_onshore"] == 10 and high["solar"] == 29
    # central = max of the two means; low = min; solar's recent mean (26.33) beats its 5-year mean (24.2)
    assert cen["solar"] == pytest.approx((24 + 26 + 29) / 3) and low["solar"] == pytest.approx(24.2)


def test_r0_order_low_central_high():
    b = rates.base_rates(_additions(), 2021, 2025)
    r0s = pd.DataFrame({k: rates.r0(b, CFG["r0_rule"][k]) for k in ("low", "central", "high")})
    assert ((r0s["low"] <= r0s["central"]) & (r0s["central"] <= r0s["high"])).all()
    rates.check_r0_order(r0s)
    with pytest.raises(ValueError, match="out of order"):
        rates.check_r0_order(r0s.assign(low=r0s["high"] + 1))
    real = ROOT / "outputs" / "r0.csv"          # the real-data run, when present
    if real.exists():
        rates.check_r0_order(pd.read_csv(real, index_col=0))
    sh = rates.regional_shares(b, [2021, 2025]).set_index(["group", "region"])["share"]
    assert sh[("wind_onshore", "SPP")] == pytest.approx(0.75)


def _queue():
    return pd.DataFrame([
        # resolved IA-executed cohort: wind 2 of 4 MW operational -> 50%
        {"req": 0, "group": "wind_onshore", "mw": 2, "transreg": "SPP", "status": "operational", "phase": "IA Executed", "q_year": 2015, "prop_year": 2017, "on_year": 2018, "ia_year": 2016},
        {"req": 1, "group": "wind_onshore", "mw": 2, "transreg": "SPP", "status": "withdrawn", "phase": "IA Executed", "q_year": 2016, "prop_year": 2019, "on_year": np.nan, "ia_year": 2017},
        # active pipeline
        {"req": 2, "group": "wind_onshore", "mw": 100, "transreg": "SPP", "status": "active", "phase": "IA Executed", "q_year": 2023, "prop_year": 2028, "on_year": np.nan, "ia_year": 2025},
        {"req": 3, "group": "wind_onshore", "mw": 50, "transreg": "MISO", "status": "active", "phase": "Construction", "q_year": 2022, "prop_year": 2026, "on_year": np.nan, "ia_year": 2024},
        {"req": 4, "group": "wind_onshore", "mw": 25, "transreg": "MISO", "status": "active", "phase": "IA Executed", "q_year": 2018, "prop_year": 2020, "on_year": np.nan, "ia_year": 2019},  # overdue
        {"req": 5, "group": "wind_onshore", "mw": 80, "transreg": "SPP", "status": "active", "phase": "System Impact Study", "q_year": 2024, "prop_year": 2027, "on_year": np.nan, "ia_year": np.nan},
    ])


def test_completion_delay_and_phasing():
    q = _queue()
    cr = rates.completion_rates(q, CFG)
    assert cr.set_index(["stage", "group"]).loc[("IA Executed", "wind_onshore"), "rate"] == pytest.approx(0.5)
    delay = rates.cod_delay(q, CFG)
    assert delay["wind_onshore"] == 2          # 2018 operation - 2016 IA (only operational request)
    nt = rates.near_term(q, cr, delay, CFG)
    nat = nt[nt["region"] == "national"].set_index("year")["mw"]
    cc = CFG["near_term"]["construction_completion"]
    spread = 25 * 0.5 / 5                       # overdue request spread over 2026-2030
    assert nat[2028] == pytest.approx(100 * 0.5 + spread)   # max(2028, 2025 + 2)
    assert nat[2026] == pytest.approx(50 * cc + spread)
    assert nat.sum() == pytest.approx(100 * 0.5 + 50 * cc + 25 * 0.5)   # study-phase request excluded


def test_rate_table_levels_regional_floor():
    b = rates.base_rates(_additions(), 2015, 2025)
    shares = rates.regional_shares(b, [2016, 2025])
    nt = rates.near_term(_queue(), rates.completion_rates(_queue(), CFG), rates.cod_delay(_queue(), CFG), CFG)
    cfg = dict(CFG, groups=["wind_onshore", "solar"])
    tabs = {lv: rates.rate_table(cfg, lv, b, nt, shares) for lv in ("low", "central", "high", "reform")}
    nat = {lv: t[t["region"] == "national"].set_index(["group", "year"])["r_data_mw_per_yr"] for lv, t in tabs.items()}
    for y in (2026, 2030, 2035, 2050):
        assert nat["low"][("solar", y)] <= nat["central"][("solar", y)] + 1e-9 or y <= 2030
        assert nat["central"][("wind_onshore", y)] <= nat["high"][("wind_onshore", y)] + 1e-9
    # after the near-term years, R grows along the level's growth path (§76) from the last near-term rate
    gp = rates.growth_path(CFG, "central", "wind_onshore")
    assert nat["central"][("wind_onshore", 2035)] == pytest.approx(
        nat["central"][("wind_onshore", 2030)] * np.prod([1 + gp[y] for y in range(2031, 2036)]))
    # regional ceiling = max(share x mult x top x R, floor); MISO wind share 0.25 x small R -> floor applies
    t = tabs["central"].set_index(["group", "region", "year"])
    floor = CFG["regional_floor"]["wind_onshore"]["floor_min_mw"]     # no basis -> floor_min
    row = t.loc[("wind_onshore", "MISO", 2030)]
    assert row["ceiling_mw_per_yr"] == pytest.approx(max(0.25 * 1.5 * 2.0 * nat["central"][("wind_onshore", 2030)], floor))
    assert bool(row["floor_applied"])
    # reform raises wind's regional multiplier and floor
    assert tabs["reform"].set_index(["group", "region", "year"]).loc[("wind_onshore", "MISO", 2030), "ceiling_mw_per_yr"] \
        >= row["ceiling_mw_per_yr"]


def test_regional_floor_stock_and_peak():
    add = pd.DataFrame([{"group": "wind_onshore", "transreg": "MISO", "year": y, "mw": mw}
                        for y, mw in [(2009, 9000), (2012, 3000), (2012, 1000), (2020, 2500), (2025, 800)]])
    stock = pd.DataFrame([{"group": "wind_onshore", "transreg": "MISO", "mw": 40000},
                          {"group": "wind_onshore", "transreg": "PJM", "mw": 2000}])
    fb = rates.floor_basis(add, stock, [2010, 2025]).set_index(["group", "region"])
    assert fb.loc[("wind_onshore", "MISO"), "peak_build_mw"] == 4000          # 2012 total; 2009 outside the window
    f = CFG["regional_floor"]["wind_onshore"]
    miso = rates.regional_floor(CFG, "central", "wind_onshore", 40000, 4000)
    assert miso == max(f["floor_min_mw"], f["k_stock"] * 40000, f["k_peak"] * 4000)
    assert rates.regional_floor(CFG, "central", "wind_onshore", 2000, 0) == f["floor_min_mw"]
    rf = CFG["levels"]["reform"]["regional_floor"]["wind_onshore"]
    assert rates.regional_floor(CFG, "reform", "wind_onshore", 40000, 4000) == max(
        rf["floor_min_mw"], rf["k_stock"] * 40000, rf["k_peak"] * 4000) > miso
    # the floor reaches the rate table
    b = rates.base_rates(_additions(), 2015, 2025)
    shares = rates.regional_shares(b, [2016, 2025])
    nt = rates.near_term(_queue(), rates.completion_rates(_queue(), CFG), rates.cod_delay(_queue(), CFG), CFG)
    basis = pd.DataFrame([{"group": "wind_onshore", "region": "MISO", "stock_mw": 40000, "peak_build_mw": 4000}])
    t = rates.rate_table(dict(CFG, groups=["wind_onshore"]), "central", b, nt, shares, basis)
    row = t.set_index(["region", "year"]).loc[("MISO", 2030)]
    assert row["ceiling_mw_per_yr"] == pytest.approx(miso) and bool(row["floor_applied"])


def test_tiers_and_switch():
    t = rates.tiers(CFG, "wind_onshore")
    assert list(t["upto"]) == [1.3, 1.75, 2.0] and list(t["adder"]) == [0.0, 0.15, 0.50]
    assert rates.tiers(dict(CFG, tier_set="reeds_exact"), "solar")["adder"].tolist() == [0.0, 0.10, 0.50]
    assert rates.tiers(CFG, "gas")["adder"].tolist() == [0.0, 0.44, 1.40]


def test_high_ipm_implied_build_windows_and_ipm_shape():
    assert rates.ipm_windows(CFG) == {2028: (2026, 2029), 2030: (2030, 2031), 2035: (2032, 2036)}
    r = rates.ipm_r0(CFG, "wind_onshore")
    assert r == {2028: 68555 / 4, 2030: 33089 / 2, 2035: 82724 / 5}
    assert rates.ipm_r0(CFG, "storage") is None
    assert rates.ipm_rate_for_year(CFG, r, 2026, 0.1) == 68555 / 4
    assert rates.ipm_rate_for_year(CFG, r, 2031, 0.1) == 33089 / 2
    assert rates.ipm_rate_for_year(CFG, r, 2036, 0.1) == 82724 / 5
    assert rates.ipm_rate_for_year(CFG, r, 2038, 0.1) == pytest.approx(82724 / 5 * 1.1 ** 2)
    t = rates.tiers(CFG, "solar", rates.level_tier_set(CFG, "high_ipm"))
    assert t["upto"].tolist() == [1.0, 1.74, 1000] and t["adder"].tolist() == [0.0, 0.46, 1.47]
    assert (t["adders_last_year"] == 2036).all()
    assert rates.tiers(CFG, "gas", "ipm2025")["adder"].tolist() == [0.0, 0.44, 1.40]
    st = rates.tiers(CFG, "storage", "ipm2025")
    assert st["upto"].tolist() == [1000] and st["adder"].tolist() == [0.0]       # IPM: no storage adder
    assert rates.tiers(CFG, "wind_onshore", rates.level_tier_set(CFG, "central"))["upto"].max() == 2.0


def test_placeholders_marked():
    text = (ROOT / "config.yaml").read_text()
    for key in ("construction_completion", "growth:", "ramp_floor_mw", "regional_mult", "regional_floor:", "k_stock", "k_peak",
                "life_years", "overdue_rule"):
        block = text[text.index(key) - 400: text.index(key) + 300]
        assert "PLACEHOLDER" in block, key


def test_switch_groups():
    sg = groups.switch_group
    assert sg("LandbasedWind_Class3_Moderate", "Wind") == "wind_onshore"
    assert sg("OffShoreWind_Class1", "Wind") is None
    assert sg("UtilityPV_Class1_Moderate", "Solar") == "solar"
    assert sg("Distributed_Solar", "Solar") is None and sg("Residential_PV", "Solar") is None
    assert sg("Battery_Moderate_4hr", "Electricity") == "storage"
    assert sg("NaturalGas_CCAvgCF_Moderate", "Naturalgas") == "gas"
    assert sg("NaturalGas_CCCCSAvgCF_Moderate", "Naturalgas") is None
    assert sg("Nuclear_Nuclear", "Uranium") == "nuclear"                                  # §79 path group
    assert sg("Nuclear - small modular reactor", "") == "nuclear"
    assert sg("UtilityPV_Class1", "Solar", 1) is None


# ---------------------------------------------------------------------------- case writer checks

def _case(tmp_path, gas_cap=False, min_cap=None):
    d = tmp_path / "case"
    d.mkdir()
    pd.DataFrame({"INVESTMENT_PERIOD": [2030], "period_start": [2026], "period_end": [2030]}).to_csv(d / "periods.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["w1", "s1", "g1"], "gen_tech": ["LandbasedWind_C1", "UtilityPV_C1", "NaturalGas_CCAvgCF"],
                  "gen_energy_source": ["Wind", "Solar", "Naturalgas"], "gen_load_zone": ["p35", "p60", "p60"],
                  "gen_is_distributed": [0, 0, 0]}).to_csv(d / "gen_info.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["w1", "s1", "g1"], "build_year": [2030] * 3,
                  "gen_overnight_cost": [1.5e6, 1.0e6, 1.0e6], "gen_fixed_om": [0, 0, 0]}).to_csv(d / "gen_build_costs.csv", index=False)
    pd.DataFrame(columns=["GENERATION_PROJECT", "build_year", "build_gen_predetermined"]).to_csv(d / "gen_build_predetermined.csv", index=False)
    mcr = pd.DataFrame({"MAX_CAP_PROGRAM": ["MaxCapTag_GasTurbineSupply"] if gas_cap else [], "PERIOD": [2030] if gas_cap else [],
                        "max_cap_mw": [1e5] if gas_cap else []})
    mcr.to_csv(d / "max_cap_requirements.csv", index=False)
    if min_cap is not None:
        pd.DataFrame({"MIN_CAP_PROGRAM": ["MinCapTag_Wind"], "PERIOD": [2030], "min_cap_mw": [min_cap]}).to_csv(d / "min_cap_requirements.csv", index=False)
        pd.DataFrame({"MIN_CAP_PROGRAM": ["MinCapTag_Wind"], "MIN_CAP_GEN": ["w1"]}).to_csv(d / "min_cap_generators.csv", index=False)
    return d


def _tables(tmp_path):
    t = tmp_path / "tables"
    t.mkdir()
    rows = []
    for g, r in [("wind_onshore", 8000.0), ("solar", 20000.0), ("gas", 6000.0)]:
        for y in range(2026, 2051):
            rows.append({"group": g, "region": "national", "year": y, "r_data_mw_per_yr": r, "ceiling_mw_per_yr": 2 * r,
                         "r0_mw_per_yr": r, "growth": 0.05, "floor_applied": False})
            rows.append({"group": g, "region": "SPP", "year": y, "r_data_mw_per_yr": r / 2, "ceiling_mw_per_yr": 500.0,
                         "r0_mw_per_yr": r / 2, "growth": 0.05, "floor_applied": True})
    pd.DataFrame(rows).to_csv(t / "rates_central.csv", index=False)
    pd.concat([rates.tiers(CFG, g).assign(group=g) for g in ("wind_onshore", "solar", "gas")]).to_csv(t / "tiers.csv", index=False)
    return t


def test_case_writer_and_gas_cap_error(tmp_path, monkeypatch):
    monkeypatch.setattr(switch_case, "REPO_ROOT", REPO)
    tdir = _tables(tmp_path)
    d = _case(tmp_path)
    s = {"build_rate": {"enabled": True, "level": "central", "tables_dir": str(tdir),
                        "groups": ["wind_onshore", "solar"]}}
    files = switch_case.write_case_inputs(d, s)
    assert "build_rate_periods.csv" in files and "build_rate_regions.csv" in files
    per = pd.read_csv(d / "build_rate_periods.csv").set_index("BR_GROUP")["br_rate_data_mw"]
    assert per["wind_onshore"] == 8000.0
    tiers = pd.read_csv(d / "build_rate_tiers.csv")
    w = tiers[tiers["BR_GROUP"] == "wind_onshore"].set_index("BR_TIER")
    assert w.loc["t2", "br_tier_adder_per_mw"] == pytest.approx(0.15 * 1.5e6)   # 15% of the group's capex
    zones = pd.read_csv(d / "build_rate_zones.csv").set_index("LOAD_ZONE")["br_zone_region"]
    assert zones["p35"] == "SPP" and zones["p60"] == "ERCOT"
    # gas on while GasTurbineSupply is active -> error at case build
    d2 = _case(tmp_path / "x", gas_cap=True) if (tmp_path / "x").mkdir() is None else None
    s2 = {"build_rate": dict(s["build_rate"], groups=["wind_onshore", "gas"])}
    with pytest.raises(ValueError, match="GasTurbineSupply"):
        switch_case.write_case_inputs(d2, s2)
    # disabled -> nothing
    assert switch_case.write_case_inputs(d, {"build_rate": {"enabled": False}}) == []


def test_case_writer_ipm2025_adders_stop_after_2036(tmp_path, monkeypatch):
    monkeypatch.setattr(switch_case, "REPO_ROOT", REPO)
    tdir = _tables(tmp_path)
    shutil.copy(tdir / "rates_central.csv", tdir / "rates_high_ipm.csv")
    pd.concat([rates.tiers(CFG, g, "ipm2025").assign(group=g) for g in ("wind_onshore", "solar", "gas")]) \
        .to_csv(tdir / "tiers_high_ipm.csv", index=False)
    d = _case(tmp_path)
    pd.DataFrame({"INVESTMENT_PERIOD": [2030, 2040, 2045], "period_start": [2026, 2036, 2041],
                  "period_end": [2035, 2040, 2045]}).to_csv(d / "periods.csv", index=False)
    s = {"build_rate": {"enabled": True, "level": "high_ipm", "tables_dir": str(tdir), "groups": ["wind_onshore"],
                        "regional": False}}
    switch_case.write_case_inputs(d, s)
    t = pd.read_csv(d / "build_rate_tiers.csv").set_index(["PERIOD", "BR_TIER"])
    assert t.loc[(2030, "t3"), "br_tier_width"] == pytest.approx(1000 - 1.74)       # no hard ceiling
    assert t.loc[(2030, "t2"), "br_tier_adder_per_mw"] == pytest.approx(0.46 * 1.5e6)
    assert t.loc[(2040, "t2"), "br_tier_adder_per_mw"] == pytest.approx(0.46 * 1.5e6 / 5)   # only 2036 of 2036-40
    assert t.loc[(2045, "t3"), "br_tier_adder_per_mw"] == 0


def test_min_cap_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(switch_case, "REPO_ROOT", REPO)
    tdir = _tables(tmp_path)
    ceiling = 2.0 * 8000 * 5
    s = {"build_rate": {"enabled": True, "level": "central", "tables_dir": str(tdir), "groups": ["wind_onshore"]}}
    switch_case.write_case_inputs(_case(tmp_path, min_cap=ceiling - 1), s)        # fits
    (tmp_path / "y").mkdir()
    with pytest.raises(ValueError, match="MinCap"):
        switch_case.write_case_inputs(_case(tmp_path / "y", min_cap=ceiling + 1), s)
    (tmp_path / "z").mkdir()
    s["build_rate"]["ceiling_slack_cost"] = 5000
    switch_case.write_case_inputs(_case(tmp_path / "z", min_cap=ceiling + 1), s)  # slack on: warning only


# ---------------------------------------------------------------------------- Switch toy (HiGHS)

def _switch_src() -> Path:
    """Switch source tree: $SWITCH_SRC, else /opt/switch-src, else the installed switch_model's parent."""
    if os.environ.get("SWITCH_SRC"):
        return Path(os.environ["SWITCH_SRC"])
    if Path("/opt/switch-src").exists():
        return Path("/opt/switch-src")
    try:
        import switch_model
        return Path(switch_model.__file__).resolve().parents[1]
    except ImportError:
        return Path("/opt/switch-src")


TOY = _switch_src() / "examples" / "3zone_toy"


def _toy(tmp_path, wind_rate=0.5, adders=(0.0, 0.15, 0.50), prev=None, slack=None, regions=None, floor=10.0, solar_rate=100.0):
    if shutil.which("switch") is None or not TOY.exists():
        pytest.skip("switch / 3zone_toy example not available")
    run = tmp_path / "toy"
    shutil.copytree(TOY, run)
    (run / "br_mod").mkdir()
    shutil.copy(REPO / "switch/study_modules/build_rate.py", run / "br_mod")
    (run / "br_mod/__init__.py").touch()
    with open(run / "inputs/modules.txt", "a") as f:
        f.write("\nbr_mod.build_rate\n")
    inp = run / "inputs"
    gi = pd.read_csv(inp / "gen_info.csv", na_values=".")
    # the toy is a MIP (no duals); make it an LP so the duals can be checked
    # (unit sizes and minimum build sizes add integers)
    if "gen_unit_size" in gi:
        gi["gen_unit_size"] = np.nan
    if "gen_min_build_capacity" in gi:
        gi["gen_min_build_capacity"] = 0
        gi.to_csv(inp / "gen_info.csv", index=False, na_rep=".")
    wind = gi[gi["gen_tech"] == "Wind"]["GENERATION_PROJECT"]
    pv = gi[gi["gen_tech"] == "Central_PV"]["GENERATION_PROJECT"]
    pd.DataFrame({"GENERATION_PROJECT": list(wind) + list(pv),
                  "br_gen_group": ["wind_onshore"] * len(wind) + ["solar"] * len(pv)}).to_csv(inp / "build_rate_gens.csv", index=False)
    pd.DataFrame({"BR_GROUP": ["wind_onshore", "solar"], "br_growth": [0.05, 0.05], "br_ramp_floor_mw": [floor, 100.0],
                  "br_life_years": [30, 30],
                  "br_ceiling_slack_cost_per_mw": [slack if slack is not None else -1, -1]}).to_csv(inp / "build_rate_groups.csv", index=False)
    rows_p, rows_t = [], []
    for p in (2020, 2030):
        for g, r in (("wind_onshore", wind_rate), ("solar", solar_rate)):
            rows_p.append({"BR_GROUP": g, "PERIOD": p, "br_rate_data_mw": r})
            for k, (w, a) in enumerate(zip((1.3, 0.45, 0.25), adders)):
                rows_t.append({"BR_GROUP": g, "PERIOD": p, "BR_TIER": f"t{k + 1}", "br_tier_width": w,
                               "br_tier_adder_per_mw": a * 1.5e6})
    pd.DataFrame(rows_p).to_csv(inp / "build_rate_periods.csv", index=False)
    pd.DataFrame(rows_t).to_csv(inp / "build_rate_tiers.csv", index=False)
    if prev is not None:
        pd.DataFrame({"BR_GROUP": ["wind_onshore"], "br_prev_rate_mw_per_yr": [prev]}).to_csv(inp / "build_rate_prev_build.csv", index=False)
    if regions is not None:
        zones, caps = regions
        pd.DataFrame({"LOAD_ZONE": list(zones), "br_zone_region": list(zones.values())}).to_csv(inp / "build_rate_zones.csv", index=False)
        pd.DataFrame([{"BR_GROUP": "wind_onshore", "BR_REGION": r, "PERIOD": p, "br_region_max_mw_per_yr": c}
                      for r, c in caps.items() for p in (2020, 2030)]).to_csv(inp / "build_rate_regions.csv", index=False)
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs", "--suffixes", "dual"], cwd=run,
                       capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(run)})
    assert r.returncode == 0, r.stderr[-3000:] + r.stdout[-2000:]
    return run


def test_toy_tiers_fill_in_order_ceiling_binds_costs_in_objective(tmp_path):
    run = _toy(tmp_path)      # unconstrained toy builds 12 MW wind in 2030; ceiling = 2.0 x 0.5 x 10 = 10 MW
    t = pd.read_csv(run / "outputs/build_rate_tiers_built.csv")
    w = t[(t.group == "wind_onshore") & (t.period == 2030)].set_index("tier")
    assert w["built_mw"].sum() == pytest.approx(10.0, abs=1e-5)                  # ceiling binds
    assert w.loc["t1", "full"] and w.loc["t2", "full"]                              # cheaper tiers fill first
    duals = pd.read_csv(run / "outputs/build_rate_duals.csv")
    top = duals[(duals.group == "wind_onshore") & (duals.period == 2030) & (duals.constraint == "band_t3")]
    assert top["dual_overnight_per_kw"].iat[0] > 0                                 # the ceiling has a price
    # adders reach the objective: BuildRateCosts = sum(tier x adder) annualised over active periods
    costs = pd.read_csv(run / "outputs/costs_itemized.csv")
    br = costs[costs["Component"] == "BuildRateCosts"].set_index("PERIOD")["AnnualCost_Real"]
    rate = 0.07
    crf = rate / (1 - (1 + rate) ** -30)
    overnight = (w["built_mw"] * w["adder_per_kw"] * 1000).sum()
    assert overnight > 0 and br.loc[2030] == pytest.approx(overnight * crf, rel=1e-6)


def test_toy_ramp_and_myopic_prev_rate(tmp_path):
    # chained history: wind's previous best rate 0.2 MW/yr, floor 0 -> R(2020) <= 1.05^10 x 0.2
    run = _toy(tmp_path, wind_rate=5.0, prev=0.2, floor=0.0)
    nb = pd.read_csv(run / "outputs/build_rate_new_build.csv").set_index(["group", "period"])
    assert nb.loc[("wind_onshore", 2020), "rate_mw_per_yr"] <= 1.05 ** 10 * 0.2 + 1e-6
    # perfect foresight ramp: R(2030) <= 1.05^10 x build(2020)/10 + floor (0)
    r30 = nb.loc[("wind_onshore", 2030), "rate_mw_per_yr"]
    b20 = nb.loc[("wind_onshore", 2020), "new_build_mw_per_yr"]
    assert r30 <= 1.05 ** 10 * b20 + 1e-6
    # ...and it binds: 2030 wind is held to the ceiling the ramp allows, below the 12 MW built unconstrained
    b30 = nb.loc[("wind_onshore", 2030), "new_build_mw"]
    assert b30 <= 2.0 * 10 * 1.05 ** 10 * b20 + 1e-6 and b30 < 12 - 1e-3


def test_toy_regional_floor(tmp_path):
    # North gets a 0.1 MW/yr regional ceiling (= its floor); unconstrained, the toy builds 5 MW of wind
    # in North in 2030, so the regional limit binds at 1 MW per period
    run = _toy(tmp_path, wind_rate=5.0, regions=({"North": "N", "Central": "C", "South": "S"},
                                                 {"N": 0.1, "C": 100.0, "S": 100.0}))
    bg = pd.read_csv(run / "outputs/BuildGen.csv")
    north = bg[bg.GEN_BLD_YRS_1.str.startswith("N-Wind") & bg.GEN_BLD_YRS_2.isin([2020, 2030])]
    assert north.groupby("GEN_BLD_YRS_2")["BuildGen"].sum().max() <= 0.1 * 10 + 1e-6


def test_chain_build_rate_inputs(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("pns", REPO / "switch/study_modules/prepare_next_stage.py")
    pns = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pns)
    inp, nxt = tmp_path / "in", tmp_path / "next"
    inp.mkdir(); nxt.mkdir()
    pd.DataFrame({"BR_GROUP": ["wind_onshore", "solar"], "br_prev_rate_mw_per_yr": [9.0, 1.0]}).to_csv(
        inp / "build_rate_prev_build.chained.c.csv", index=False)

    class P(dict):
        def last(self):
            return 2030
    m = SimpleNamespace(PERIODS=P(), BR_GROUP_PERIODS=[("wind_onshore", 2030), ("solar", 2030)],
                        BRNewBuild={("wind_onshore", 2030): 20.0, ("solar", 2030): 40.0},
                        br_window_years={2030: 4})
    chained = lambda *p: Path(Path(*p).parent, f"{Path(*p).stem}.chained.c{Path(*p).suffix}")
    possibly = lambda *p: chained(*p) if chained(*p).exists() else Path(*p)
    pns.chain_build_rate_inputs(m, inp, nxt, chained, possibly, lambda p: pd.read_csv(p),
                                lambda df, p: df.to_csv(p, index=False))
    out = pd.read_csv(nxt / "build_rate_prev_build.chained.c.csv").set_index("BR_GROUP")["br_prev_rate_mw_per_yr"]
    assert out["wind_onshore"] == 9.0       # best rate so far (earlier stage) is kept
    assert out["solar"] == 10.0             # this stage: 40 MW / 4 years


# ---------------------------------------------------------------------------- window sums (toy case)

def _annual_tables(tmp_path):
    """Rising annual R (fixture): wind 0.02 x (y - 2016) MW/yr, solar and storage 10x that; one regional row."""
    t = tmp_path / "annual"
    t.mkdir()
    rows = []
    for g, k in (("wind_onshore", 0.02), ("solar", 0.2), ("storage", 0.2)):
        for y in range(2015, 2051):
            r = k * (y - 2016)
            rows.append({"group": g, "region": "national", "year": y, "r_data_mw_per_yr": r, "ceiling_mw_per_yr": 2 * r,
                         "r0_mw_per_yr": r, "growth": 0.05, "floor_applied": False})
            # regional ceiling crosses its floor (0.5 MW/yr) mid-window
            rows.append({"group": g, "region": "SPP", "year": y, "r_data_mw_per_yr": r / 2,
                         "ceiling_mw_per_yr": max(r, 0.5), "r0_mw_per_yr": r / 2, "growth": 0.05, "floor_applied": r < 0.5})
    rates_df = pd.DataFrame(rows)
    rates_df.to_csv(t / "rates_central.csv", index=False)
    pd.concat([rates.tiers(CFG, g).assign(group=g) for g in ("wind_onshore", "solar", "storage")]) \
        .to_csv(t / "tiers.csv", index=False)
    return t, rates_df


def _toy_case(tmp_path, periods):
    if not TOY.exists():
        pytest.skip("3zone_toy example not available")
    d = tmp_path / f"case{len(periods)}"
    shutil.copytree(TOY / "inputs", d)
    pd.DataFrame(periods, columns=["INVESTMENT_PERIOD", "period_start", "period_end"]).to_csv(d / "periods.csv", index=False)
    return d


def test_toy_cumulative_limits_window_sum_single_vs_three_periods(tmp_path, monkeypatch):
    monkeypatch.setattr(switch_case, "REPO_ROOT", REPO)
    tdir, rd = _annual_tables(tmp_path)
    s = {"build_rate": {"enabled": True, "level": "central", "tables_dir": str(tdir), "groups": ["wind_onshore", "solar"]},
         "_zone_map": {"p35": "North"}}          # toy zone North -> SPP (ReEDS BA p35)
    one = _toy_case(tmp_path, [(2035, 2026, 2035)])
    three = _toy_case(tmp_path, [(2028, 2026, 2028), (2030, 2029, 2030), (2035, 2031, 2035)])
    out = {}
    for name, d in (("one", one), ("three", three)):
        switch_case.write_case_inputs(d, s)
        per = pd.read_csv(d / "periods.csv").set_index("INVESTMENT_PERIOD")
        w = (per["period_end"] - per["period_start"] + 1)
        p = pd.read_csv(d / "build_rate_periods.csv")
        t = pd.read_csv(d / "build_rate_tiers.csv")
        reg = pd.read_csv(d / "build_rate_regions.csv")
        lim = {}
        for g in ("wind_onshore", "solar"):
            pg = p[p.BR_GROUP == g].set_index("PERIOD")["br_rate_data_mw"]
            tg = t[t.BR_GROUP == g]
            # module: Tier[k] <= width_k x R x W, with R <= br_rate_data_mw
            lim[(g, "1.0R")] = float((pg * w).sum())
            lim[(g, "ceiling")] = float(sum(tg[tg.PERIOD == q]["br_tier_width"].sum() * pg[q] * w[q] for q in pg.index))
            rg = reg[(reg.BR_GROUP == g) & (reg.BR_REGION == "SPP")].set_index("PERIOD")["br_region_max_mw_per_yr"]
            lim[(g, "SPP")] = float((rg * w).sum())
        out[name] = lim
    for g in ("wind_onshore", "solar"):
        nat = rd[(rd.group == g) & (rd.region == "national")].set_index("year")
        spp = rd[(rd.group == g) & (rd.region == "SPP")].set_index("year")
        window_sum = nat.loc[2026:2035, "r_data_mw_per_yr"].sum()
        for key, want in (("1.0R", window_sum), ("ceiling", 2.0 * window_sum),
                          ("SPP", spp.loc[2026:2035, "ceiling_mw_per_yr"].sum())):   # floors summed year by year
            assert out["one"][(g, key)] == pytest.approx(want)
            assert out["three"][(g, key)] == pytest.approx(want)
        # and not R[2035] x W
        assert out["one"][(g, "1.0R")] != pytest.approx(nat.loc[2035, "r_data_mw_per_yr"] * 10)


def test_toy_solve_ceiling_is_window_sum(tmp_path, monkeypatch):
    """Switch solve on the 3-zone toy: the writer's window-mean rate x the module's W caps 2030-window
    wind at 2.0 x sum of R[y] over 2027-36 (not 2.0 x R[2030] x 10)."""
    if shutil.which("switch") is None or not TOY.exists():
        pytest.skip("switch / 3zone_toy example not available")
    monkeypatch.setattr(switch_case, "REPO_ROOT", REPO)
    tdir, rd = _annual_tables(tmp_path)
    run = tmp_path / "toy"
    shutil.copytree(TOY, run)
    (run / "br_mod").mkdir()
    shutil.copy(REPO / "switch/study_modules/build_rate.py", run / "br_mod")
    (run / "br_mod/__init__.py").touch()
    with open(run / "inputs/modules.txt", "a") as f:
        f.write("\nbr_mod.build_rate\n")
    gi = pd.read_csv(run / "inputs/gen_info.csv", na_values=".")
    gi["gen_unit_size"] = np.nan
    gi["gen_min_build_capacity"] = 0
    gi.to_csv(run / "inputs/gen_info.csv", index=False, na_rep=".")
    cfg = dict(CFG, ramp_floor_mw=dict(CFG["ramp_floor_mw"], wind_onshore=100.0, solar=1000.0))   # ramp slack
    switch_case.write_case_inputs(run / "inputs", {"build_rate": {"enabled": True, "level": "central",
                                  "tables_dir": str(tdir), "groups": ["wind_onshore"], "regional": False}}, cfg)
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs"], cwd=run, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(run)})
    assert r.returncode == 0, r.stderr[-3000:] + r.stdout[-2000:]
    nb = pd.read_csv(run / "outputs/build_rate_new_build.csv").set_index(["group", "period"])
    nat = rd[(rd.group == "wind_onshore") & (rd.region == "national")].set_index("year")["r_data_mw_per_yr"]
    ceiling = 2.0 * nat.loc[2027:2036].sum()             # 6.2 MW; R[2030] x 10 x 2 would be 5.6
    assert nb.loc[("wind_onshore", 2030), "new_build_mw"] == pytest.approx(ceiling, rel=1e-6)


def test_patch_case_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(switch_case, "REPO_ROOT", REPO)
    tdir, _ = _annual_tables(tmp_path)
    d = _toy_case(tmp_path, [(2035, 2026, 2035)])
    zm = tmp_path / "zm.csv"
    pd.DataFrame({"ba": ["p35"], "zone": ["North"]}).to_csv(zm, index=False)
    cli.main(["patch-case", str(d), "--config", str(ROOT / "config.yaml"), "--tables-dir", str(tdir),
              "--groups", "wind_onshore", "solar", "--zone-map", str(zm)])
    for f in ("build_rate_gens.csv", "build_rate_groups.csv", "build_rate_periods.csv", "build_rate_tiers.csv",
              "build_rate_zones.csv", "build_rate_regions.csv"):
        assert (d / f).exists(), f
    gens = pd.read_csv(d / "build_rate_gens.csv")
    assert set(gens["br_gen_group"]) == {"wind_onshore", "solar"}
    assert "--include-module study_modules.build_rate" in capsys.readouterr().out


def test_rps_check_warns_without_acp(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(switch_case, "REPO_ROOT", REPO)
    tdir, _ = _annual_tables(tmp_path)          # SPP ceilings are floor-set early in the window
    d = _toy_case(tmp_path, [(2035, 2026, 2035)])
    s = {"build_rate": {"enabled": True, "level": "central", "tables_dir": str(tdir), "groups": ["wind_onshore", "solar"]},
         "_zone_map": {"p35": "North"}}
    req = pd.DataFrame({"RPS_PROGRAM": ["ESR_X", "ESR_LOW"], "LOAD_ZONE": ["North", "North"], "PERIOD": [2035, 2035],
                        "rps_share": [0.5, 0.1], "unbundled_rec_limit_fraction": [1, 1]})
    req.to_csv(d / "rps_requirements.csv", index=False)
    with caplog.at_level("WARNING"):
        switch_case.write_case_inputs(d, s)
    msgs = [r.getMessage() for r in caplog.records if "RPS" in r.getMessage()]
    assert len(msgs) == 1 and "ESR_X" in msgs[0] and "SPP" in msgs[0] and "ACP" in msgs[0]
    caplog.clear()
    req.assign(rps_acp_per_mwh=[60, ""]).to_csv(d / "rps_requirements.csv", index=False)   # ACP on -> no warning
    with caplog.at_level("WARNING"):
        switch_case.write_case_inputs(d, s)
    assert not [r for r in caplog.records if "RPS" in r.getMessage()]


def test_regional_groups_storage_national_only(tmp_path, monkeypatch):
    monkeypatch.setattr(switch_case, "REPO_ROOT", REPO)
    tdir, _ = _annual_tables(tmp_path)
    d = _toy_case(tmp_path, [(2035, 2026, 2035)])
    gi = pd.read_csv(d / "gen_info.csv", na_values=".")
    batt = gi[gi["GENERATION_PROJECT"].str.contains("Wind")].assign(            # toy has no battery: add one in North
        GENERATION_PROJECT="N-Battery", gen_tech="Battery_Storage", gen_energy_source="Electricity").head(1)
    pd.concat([gi, batt]).to_csv(d / "gen_info.csv", index=False, na_rep=".")
    base = {"enabled": True, "level": "central", "tables_dir": str(tdir), "groups": ["wind_onshore", "solar", "storage"]}
    zm = {"p35": "North"}
    switch_case.write_case_inputs(d, {"build_rate": base, "_zone_map": zm})        # default regional_groups
    gens = pd.read_csv(d / "build_rate_gens.csv")
    assert "storage" in set(gens["br_gen_group"])                                  # national limit still applies
    assert "storage" in set(pd.read_csv(d / "build_rate_periods.csv")["BR_GROUP"])
    reg = pd.read_csv(d / "build_rate_regions.csv")
    assert set(reg["BR_GROUP"]) == {"wind_onshore", "solar"}                       # storage national-only
    switch_case.write_case_inputs(d, {"build_rate": dict(base, regional_groups=["wind_onshore", "solar", "storage"]),
                                      "_zone_map": zm})
    assert "storage" in set(pd.read_csv(d / "build_rate_regions.csv")["BR_GROUP"])  # opt-in
    switch_case.write_case_inputs(d, {"build_rate": dict(base, regional_groups=["wind_onshore"]), "_zone_map": zm})
    assert set(pd.read_csv(d / "build_rate_regions.csv")["BR_GROUP"]) == {"wind_onshore"}


def test_patch_case_regional_groups_option(tmp_path, monkeypatch):
    monkeypatch.setattr(switch_case, "REPO_ROOT", REPO)
    tdir, _ = _annual_tables(tmp_path)
    d = _toy_case(tmp_path, [(2035, 2026, 2035)])
    zm = tmp_path / "zm.csv"
    pd.DataFrame({"ba": ["p35"], "zone": ["North"]}).to_csv(zm, index=False)
    args = ["patch-case", str(d), "--config", str(ROOT / "config.yaml"), "--tables-dir", str(tdir),
            "--groups", "wind_onshore", "solar", "--zone-map", str(zm)]
    cli.main(args + ["--regional-groups", "wind_onshore"])
    assert set(pd.read_csv(d / "build_rate_regions.csv")["BR_GROUP"]) == {"wind_onshore"}
    cli.main(args)                                                                 # default: wind and solar
    assert set(pd.read_csv(d / "build_rate_regions.csv")["BR_GROUP"]) == {"wind_onshore", "solar"}


def _round1_trimmed_top_quartile_mean(rates_, trim_frac=0.10, top_frac=0.25):
    """Round 1's rule, as in cap_derivation_methodology.py (Switch_cap_methodology.zip), for comparison."""
    s = pd.Series(rates_).dropna().sort_values().reset_index(drop=True)
    n = len(s)
    trim_n = round(n * trim_frac)
    middle = s.iloc[trim_n: n - trim_n] if trim_n > 0 else s
    top_n = round(len(middle) * top_frac)
    return middle.sort_values(ascending=False).head(top_n).mean()


def test_benchmark_matches_round_one_rule():
    """§74: the best-performer benchmark is round 1's Implied Rate rule (trim round(10% n) at each end, mean of the
    top round(25% m)); for lower-is-better values (duration) the best are the smallest."""
    rng = np.random.default_rng(7)
    for n in (5, 9, 10, 11, 15, 20, 32, 36, 49):
        v = rng.uniform(0, 1, n)
        assert rates.benchmark(v, True, 0.10, 0.25) == pytest.approx(_round1_trimmed_top_quartile_mean(v))
        assert rates.benchmark(v, False, 0.10, 0.25) == pytest.approx(-_round1_trimmed_top_quartile_mean(-v))
    # 10 units: trim 1 at each end, top round(8 x 0.25) = 2 of the remaining 8
    assert rates.benchmark(range(10), True, 0.10, 0.25) == pytest.approx((8 + 7) / 2)
    assert rates.benchmark(range(1, 11), False, 0.10, 0.25) == pytest.approx((2 + 3) / 2)


def _reform_queue():
    """Two states per transreg, hand-built: resolved cohort (completion), recent completions (duration), actives."""
    rows, k = [], 0
    spec = {  # state: (transreg, completed MW of 10 resolved, durations of 5 completions, active MW)
        "A": ("R1", 4, [3] * 5, 100), "B": ("R1", 1, [8] * 5, 200),
        "C": ("R2", 2, [4] * 5, 100), "D": ("R2", 3, [5] * 5, 50)}
    for st, (tr, done, durs, act) in spec.items():
        for i in range(10):
            rows.append({"req": k, "group": "solar", "mw": 1.0, "transreg": tr, "state": st,
                         "status": "operational" if i < done else "withdrawn", "phase": "IA Executed", "q_year": 2010,
                         "prop_year": 2014, "on_year": 2014 if i < done else np.nan, "ia_year": 2012})
            k += 1
        for d in durs:
            rows.append({"req": k, "group": "solar", "mw": 1.0, "transreg": tr, "state": st, "status": "operational",
                         "phase": "IA Executed", "q_year": 2022 - d, "prop_year": 2022, "on_year": 2022, "ia_year": 2020})
            k += 1
        rows.append({"req": k, "group": "solar", "mw": act, "transreg": tr, "state": st, "status": "active",
                     "phase": "Feasibility Study", "q_year": 2024, "prop_year": 2028, "on_year": np.nan, "ia_year": np.nan})
        k += 1
    return pd.DataFrame(rows)


def test_reform_benchmark_and_implied_rate_increment():
    """States raised to the benchmark (completion up, duration down), better ones keep theirs; a transreg's increment
    is its requests' implied-rate gain over the national implied rate."""
    q = _reform_queue()
    rb = dict(CFG["reform_benchmark"], groups=["solar"], proxy={}, completion_cohort_queue_years=[2000, 2018],
              duration_on_years=[2018, 2025], min_basis_mw=5, min_operational=3, trim_share=0.0, top_share=0.5)
    bm, up = rates.reform_uplift(q, CFG, rb)
    b = bm.set_index("unit")
    comp = {s: b.at[s, "completion"] for s in "ABCD"}
    dur = {s: b.at[s, "duration_years"] for s in "ABCD"}
    assert dur == {"A": 3.0, "B": 8.0, "C": 4.0, "D": 5.0}
    bc = rates.benchmark(list(comp.values()), True, 0.0, 0.5)
    bd = rates.benchmark(list(dur.values()), False, 0.0, 0.5)
    assert b.at["A", "benchmark_completion"] == pytest.approx(bc) and bd == pytest.approx(3.5)
    assert b.at["B", "completion_reform"] == pytest.approx(max(comp["B"], bc)) and b.at["B", "duration_reform_years"] == 3.5
    assert b.at["A", "duration_reform_years"] == 3.0 and not b.at["A", "cut_duration"]       # better keeps its own
    I0 = {s: m * comp[s] / dur[s] for s, m in zip("ABCD", (100, 200, 100, 50))}
    I1 = {s: m * max(comp[s], bc) / min(dur[s], bd) for s, m in zip("ABCD", (100, 200, 100, 50))}
    tot = sum(I0.values())
    u = up.set_index("region")
    assert u.at["R1", "delta"] == pytest.approx((I1["A"] + I1["B"] - I0["A"] - I0["B"]) / tot)
    assert u.at["R2", "delta"] == pytest.approx((I1["C"] + I1["D"] - I0["C"] - I0["D"]) / tot)
    assert u.at["national", "delta"] == pytest.approx(sum(I1.values()) / tot - 1)


def test_reform_bp_and_high_reform_tables():
    """reform_bp: regional R = (share + delta) x R_central, national = R_central x I'/I, ceilings recomputed (2.0 x R
    national; share x mult x 2.0 x R regional, floored); reform (old) unchanged and equal to central nationally.
    high_reform: per region and year the larger of high and reform_bp; national at least both."""
    b = rates.base_rates(_additions(), 2015, 2025)
    shares = rates.regional_shares(b, [2016, 2025])
    nt = rates.near_term(_queue(), rates.completion_rates(_queue(), CFG), rates.cod_delay(_queue(), CFG), CFG)
    cfg = dict(CFG, groups=["wind_onshore", "solar"])
    up = pd.DataFrame([{"group": "wind_onshore", "region": "SPP", "delta": 0.30},
                       {"group": "wind_onshore", "region": "MISO", "delta": 0.10},
                       {"group": "wind_onshore", "region": "national", "delta": 0.40},
                       {"group": "solar", "region": "ERCOT", "delta": 0.0},
                       {"group": "solar", "region": "national", "delta": 0.0}])
    t = rates.rate_tables(cfg, ["central", "reform", "reform_bp", "high", "high_reform"], b, nt, shares, None, up)
    ix = {k: v.set_index(["group", "region", "year"]) for k, v in t.items()}
    c, r = ix["central"], ix["reform_bp"]
    for y in (2026, 2030, 2035):
        rc = c.loc[("wind_onshore", "national", y), "r_data_mw_per_yr"]
        assert r.loc[("wind_onshore", "national", y), "r_data_mw_per_yr"] == pytest.approx(rc * 1.40)
        assert r.loc[("wind_onshore", "national", y), "ceiling_mw_per_yr"] == pytest.approx(2.0 * rc * 1.40)
        s_spp = c.loc[("wind_onshore", "SPP", y), "r_data_mw_per_yr"] / rc
        assert r.loc[("wind_onshore", "SPP", y), "r_data_mw_per_yr"] == pytest.approx((s_spp + 0.30) * rc)
        assert r.loc[("wind_onshore", "SPP", y), "ceiling_mw_per_yr"] == pytest.approx(
            max((s_spp + 0.30) * rc * 1.5 * 2.0, CFG["regional_floor"]["wind_onshore"]["floor_min_mw"]))
        assert r.loc[("solar", "national", y), "r_data_mw_per_yr"] == pytest.approx(
            c.loc[("solar", "national", y), "r_data_mw_per_yr"])
    nat = lambda k: ix[k].xs("national", level="region")  # noqa: E731
    pd.testing.assert_series_equal(nat("reform")["r_data_mw_per_yr"], nat("central")["r_data_mw_per_yr"])
    hr, hi = ix["high_reform"], ix["high"]
    for col in ("r_data_mw_per_yr", "ceiling_mw_per_yr"):
        assert (hr[col] >= np.maximum(hi[col], r[col].reindex(hi.index)) - 1e-6).all()
    assert hr["growth"].equals(hi["growth"])                                           # high's growth (ramp)
    assert {"reform_bp", "high_reform", "reform"} <= set(cli.LEVELS)
    assert CFG["levels"]["high_reform"]["max_of"] == ["high", "reform_bp"]
    assert CFG["levels"]["reform_bp"]["benchmark_of"] == "central"


def test_growth_paths_anchored_to_round_one():
    """§76: central / high growth after 2030 = round 1's Quadratic Trend / Implied Rate, converted to the growth of annual
    additions (scripts/round1_growth_paths.py reproduces the config values and round 1's implemented caps); storage
    follows solar; years after 2045 take the last value; the ramp growth is the path's geometric mean; low (no path)
    keeps its scalar growth."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("r1g", ROOT / "scripts/round1_growth_paths.py")
    r1 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(r1)
    paths, info = r1.paths()                       # asserts the quadratic reproduces the implemented S0/S1 caps
    for lv in ("central", "high"):
        for g in ("wind_onshore", "solar"):
            cfgp = rates.growth_path(CFG, lv, g)
            assert sorted(cfgp) == list(range(2031, 2046))
            for y in range(2031, 2046):
                assert cfgp[y] == pytest.approx(paths[lv][g][y], abs=5e-5), (lv, g, y)
        assert rates.growth_path(CFG, lv, "storage") == rates.growth_path(CFG, lv, "solar")   # storage follows solar
    assert rates.growth_path(CFG, "low", "wind_onshore") is None and rates.growth_path(CFG, "central", "gas") is None
    # high = the Implied Rate within each period (round 1's implemented 2031-35 rates)
    assert paths["high"]["wind_onshore"][2033] == pytest.approx((351.0 / 235.9) ** 0.2 - 1)
    assert paths["high"]["solar"][2033] == pytest.approx((727.8 / 364.4) ** 0.2 - 1)
    assert info["quadratic_caps"]["solar"][2028] == pytest.approx(243.7, abs=0.06)
    b = rates.base_rates(_additions(), 2015, 2025)
    shares = rates.regional_shares(b, [2016, 2025])
    nt = rates.near_term(_queue(), rates.completion_rates(_queue(), CFG), rates.cod_delay(_queue(), CFG), CFG)
    t = rates.rate_table(dict(CFG, groups=["wind_onshore", "solar", "storage"]), "central", b, nt, shares)
    nat = t[t.region == "national"].set_index(["group", "year"])
    gp = rates.growth_path(CFG, "central", "solar")
    assert nat.loc[("solar", 2050), "r_data_mw_per_yr"] == pytest.approx(
        nat.loc[("solar", 2045), "r_data_mw_per_yr"] * (1 + gp[2045]) ** 5)
    assert nat.loc[("solar", 2031), "growth"] == pytest.approx(rates.ramp_growth(gp))
    tl = rates.rate_table(dict(CFG, groups=["wind_onshore"]), "low", b, nt, shares)
    tl = tl[tl.region == "national"].set_index("year")["r_data_mw_per_yr"]
    assert tl[2035] == pytest.approx(tl[2030] * (1 + CFG["growth"]["low"]["wind_onshore"]) ** 5)


def test_reform_bp_siting_level():
    """§76 bill sensitivity: reform_bp_siting = reform_bp nationally, with the old reform's wind regional multiplier and
    floor terms (computed from the floor basis)."""
    b = rates.base_rates(_additions(), 2015, 2025)
    shares = rates.regional_shares(b, [2016, 2025])
    nt = rates.near_term(_queue(), rates.completion_rates(_queue(), CFG), rates.cod_delay(_queue(), CFG), CFG)
    basis = pd.DataFrame({"group": ["wind_onshore"] * 2, "region": ["SPP", "MISO"], "stock_mw": [40000.0, 30000.0],
                          "peak_build_mw": [6000.0, 3000.0]})
    up = pd.DataFrame([{"group": "wind_onshore", "region": "SPP", "delta": 0.2},
                       {"group": "wind_onshore", "region": "national", "delta": 0.2}])
    t = rates.rate_tables(dict(CFG, groups=["wind_onshore"], path_groups={}), ["reform_bp", "reform_bp_siting"], b, nt,
                          shares, basis, up)
    a, s = (t[k].set_index(["region", "year"]) for k in ("reform_bp", "reform_bp_siting"))
    pd.testing.assert_series_equal(a.loc["national"]["ceiling_mw_per_yr"], s.loc["national"]["ceiling_mw_per_yr"])
    f = CFG["levels"]["reform_bp_siting"]["regional_floor"]["wind_onshore"]
    assert f == CFG["levels"]["reform"]["regional_floor"]["wind_onshore"]
    for reg, stock, peak in (("SPP", 40000.0, 6000.0), ("MISO", 30000.0, 3000.0)):
        floor = max(f["floor_min_mw"], f["k_stock"] * stock, f["k_peak"] * peak)
        r = s.loc[(reg, 2035), "r_data_mw_per_yr"]
        assert s.loc[(reg, 2035), "ceiling_mw_per_yr"] == pytest.approx(max(r * 3.0 * 2.0, floor))
        assert s.loc[(reg, 2035), "ceiling_mw_per_yr"] >= a.loc[(reg, 2035), "ceiling_mw_per_yr"]
    assert "reform_bp_siting" in cli.LEVELS


# ---------------------------------------------------------------------------- §77 deliverability layer

def test_deliverability_paths_match_brief():
    """§77: the config's paths are the brief's 2025 actuals compounded by its growth table, annually; they reproduce
    the brief's rounded 2030 / 2045 ceilings; levels map to central (central and the existing levels) or high (high
    and the reform levels); gas has none."""
    brief = {"central": {2030: {"solar": 50, "wind_onshore": 15, "storage": 40},
                         2045: {"solar": 75, "wind_onshore": 25, "storage": 65}},
             "high": {2030: {"solar": 65, "wind_onshore": 20, "storage": 55},
                      2045: {"solar": 110, "wind_onshore": 40, "storage": 100}}}
    for path, by_year in brief.items():
        for y, v in by_year.items():
            for g, gw in v.items():
                assert rates.deliverability_path(CFG, path, g)[y] / 1e3 == pytest.approx(gw, rel=0.015), (path, g, y)
    d = rates.deliverability_path(CFG, "central", "solar")
    assert d[2026] == pytest.approx(27200 * 1.129) and d[2031] == pytest.approx(d[2030] * 1.037)
    assert d[2050] == pytest.approx(d[2045] * 1.014 ** 5)                      # after 2045: the last rate
    assert rates.deliverability_path(CFG, "central", "gas") is None
    lp = CFG["deliverability"]["level_path"]
    assert set(lp) == set(cli.LEVELS)
    assert {lv for lv, p in lp.items() if p == "high"} == {"high", "reform_bp", "reform_bp_siting", "high_reform"}
    assert {lv for lv, p in lp.items() if p == "central"} == {"low", "central", "reform", "high_ipm"}
    with pytest.raises(ValueError, match="level_path"):
        rates.level_deliverability(dict(CFG, levels=dict(CFG["levels"], new={"r0": "central", "growth": "central"})),
                                   "new")
    assert rates.level_deliverability({k: v for k, v in CFG.items() if k != "deliverability"}, "central") is None


def _deliv_cfg(d_mw: dict, path="central", floor=None):
    """CFG with a flat deliverability path per group (MW/yr, fixture values) for every level."""
    dv = {"base_year": 2025, "actual_gw": {g: v / 1e3 for g, v in d_mw.items()},
          "cagr": {path: {g: {2045: 0.0} for g in d_mw}}, "level_path": {lv: path for lv in CFG["levels"]},
          "module_floor": floor or {}}
    return dict(CFG, deliverability=dv, path_groups={})


def test_deliverability_layer_min_and_regional_scaling():
    """§77/§78: final national ceiling = min(top x R, D); where D cuts, R (national and regional) and each regional
    ceiling (floors included) are scaled by the national factor, so the ceiling stays 2.0 x R; derived levels take
    the layer after their dependencies' module tables."""
    b = rates.base_rates(_additions(), 2015, 2025)
    shares = rates.regional_shares(b, [2016, 2025])
    nt = rates.near_term(_queue(), rates.completion_rates(_queue(), CFG), rates.cod_delay(_queue(), CFG), CFG)
    base_cfg = dict(CFG, groups=["wind_onshore", "solar"], deliverability=None, path_groups={})
    up = pd.DataFrame([{"group": "wind_onshore", "region": "SPP", "delta": 0.3},
                       {"group": "wind_onshore", "region": "national", "delta": 0.3}])
    mod = rates.rate_tables(base_cfg, ["central", "high_reform"], b, nt, shares, None, up)
    wind_mod = mod["central"].set_index(["group", "region", "year"]).loc[("wind_onshore", "national", 2035),
                                                                          "ceiling_mw_per_yr"]
    d = 0.5 * wind_mod                                    # binds wind in 2035; solar's path is far above its ceiling
    cfg = dict(_deliv_cfg({"wind_onshore": d, "solar": 1e9}), groups=["wind_onshore", "solar"])
    fin = rates.rate_tables(cfg, ["central", "high_reform"], b, nt, shares, None, up)
    for lv in ("central", "high_reform"):
        m, f = (x[lv].set_index(["group", "region", "year"]) for x in (mod, fin))
        pd.testing.assert_series_equal(m["r_data_mw_per_yr"], f["module_r_data_mw_per_yr"], check_names=False)
        pd.testing.assert_series_equal(m["ceiling_mw_per_yr"], f["module_ceiling_mw_per_yr"], check_names=False)
        # §78: R scaled by the factor (national and regional), so the national ceiling is 2.0 x R
        assert np.allclose(f["r_data_mw_per_yr"], f["module_r_data_mw_per_yr"] * f["deliverability_factor"])
        nat = f.xs("national", level="region")
        assert (nat["ceiling_mw_per_yr"] <= nat["module_ceiling_mw_per_yr"] + 1e-9).all()
        w = nat.loc["wind_onshore"]
        assert np.allclose(w["ceiling_mw_per_yr"], np.minimum(w["module_ceiling_mw_per_yr"], d))
        assert np.allclose(w["ceiling_mw_per_yr"], 2.0 * w["r_data_mw_per_yr"])
        fac = w["ceiling_mw_per_yr"] / w["module_ceiling_mw_per_yr"]
        for reg in ("SPP", "MISO"):
            r = f.xs(("wind_onshore", reg), level=("group", "region"))
            assert np.allclose(r["ceiling_mw_per_yr"], r["module_ceiling_mw_per_yr"] * fac.reindex(r.index))
        assert bool(w.loc[2035, "deliverability_binds"]) and not nat.loc["solar"]["deliverability_binds"].any()
        assert (nat.loc["solar"]["ceiling_mw_per_yr"] == nat.loc["solar"]["module_ceiling_mw_per_yr"]).all()
        assert f.loc[("wind_onshore", "SPP", 2035), "deliverability_mw_per_yr"] != f.loc[
            ("wind_onshore", "SPP", 2035), "deliverability_mw_per_yr"]                         # NaN on regional rows
    # null path for a level: no layer
    cfg_null = dict(cfg, deliverability=dict(cfg["deliverability"], level_path=dict(
        cfg["deliverability"]["level_path"], central=None)))
    t = rates.rate_tables(cfg_null, ["central"], b, nt, shares)["central"]
    assert (t["ceiling_mw_per_yr"] == t["module_ceiling_mw_per_yr"]).all() and not t["deliverability_binds"].any()


def test_storage_module_floor_2029_30():
    """§77: R >= the central path in the floor years (storage 2029-30 in config); later years compound from the
    floored 2030 value; other years and groups unchanged."""
    f = CFG["deliverability"]["module_floor"]
    assert f == {"storage": {"years": [2029, 2030], "path": "central"}}
    fl = rates.module_floor(CFG, "storage")
    cen = rates.deliverability_path(CFG, "central", "storage")
    assert fl == {2029: cen[2029], 2030: cen[2030]} and rates.module_floor(CFG, "solar") == {}
    add = pd.concat([_additions(), pd.DataFrame([{"group": "storage", "transreg": "ERCOT", "year": y, "mw": 5.0}
                                                 for y in range(2021, 2026)])])
    b = rates.base_rates(add, 2015, 2025)
    shares = rates.regional_shares(b, [2016, 2025])
    nt = rates.near_term(_queue(), rates.completion_rates(_queue(), CFG), rates.cod_delay(_queue(), CFG), CFG)
    cfg = dict(CFG, groups=["wind_onshore", "storage"])
    t = rates.rate_table(cfg, "central", b, nt, shares)
    nat = t[t.region == "national"].set_index(["group", "year"])["r_data_mw_per_yr"]
    no = rates.rate_table(dict(cfg, deliverability=None), "central", b, nt, shares)
    nat0 = no[no.region == "national"].set_index(["group", "year"])["r_data_mw_per_yr"]
    assert nat[("storage", 2029)] == pytest.approx(cen[2029]) and nat[("storage", 2030)] == pytest.approx(cen[2030])
    assert nat[("storage", 2028)] == nat0[("storage", 2028)]
    gp = rates.growth_path(CFG, "central", "storage")
    assert nat[("storage", 2031)] == pytest.approx(cen[2030] * (1 + gp[2031]))
    pd.testing.assert_series_equal(nat.loc["wind_onshore"], nat0.loc["wind_onshore"])
    reg = t[(t.region == "ERCOT") & (t.group == "storage")].set_index("year")["r_data_mw_per_yr"]
    assert reg[2030] == pytest.approx(cen[2030])                                              # one region, share 1


def test_deliverability_from_2029_only():
    """§78: the layer applies from deliverability.first_year (2029); 2026-28 keep the module's pipeline-based
    ceilings and R even where the path is lower."""
    assert CFG["deliverability"]["first_year"] == 2029
    b = rates.base_rates(_additions(), 2015, 2025)
    shares = rates.regional_shares(b, [2016, 2025])
    nt = rates.near_term(_queue(), rates.completion_rates(_queue(), CFG), rates.cod_delay(_queue(), CFG), CFG)
    cfg = _deliv_cfg({"wind_onshore": 1.0, "solar": 1.0})       # a tiny flat path: binds in every year it applies
    cfg["deliverability"]["first_year"] = 2029
    cfg["groups"] = ["wind_onshore", "solar"]
    t = rates.rate_tables(cfg, ["central"], b, nt, shares)["central"].set_index(["group", "region", "year"])
    for y in (2026, 2027, 2028):
        r = t.loc[("wind_onshore", "national", y)]
        assert not r["deliverability_binds"] and r["deliverability_mw_per_yr"] != r["deliverability_mw_per_yr"]
        assert r["ceiling_mw_per_yr"] == r["module_ceiling_mw_per_yr"]
        assert r["r_data_mw_per_yr"] == r["module_r_data_mw_per_yr"]
    for y in (2029, 2035):
        r = t.loc[("wind_onshore", "national", y)]
        assert r["deliverability_binds"] and r["ceiling_mw_per_yr"] == pytest.approx(1.0)
        assert r["r_data_mw_per_yr"] == pytest.approx(0.5)


def test_case_writer_keeps_tiers_with_scaled_r(tmp_path, monkeypatch):
    """§78: with R scaled where D binds, the case writer writes the full tier bands (1.3 / 0.45 / 0.25) on the scaled R,
    so the period ceiling is 2.0 x the window mean of R = the window mean of D."""
    monkeypatch.setattr(switch_case, "REPO_ROOT", REPO)
    tdir = _tables(tmp_path)
    t = pd.read_csv(tdir / "rates_central.csv")
    wind = (t["group"] == "wind_onshore") & t["year"].between(2029, 2030)
    t.loc[wind, "r_data_mw_per_yr"] *= 0.75                   # D = 12,000 = 0.75 x 2 x 8,000
    t.loc[wind, "ceiling_mw_per_yr"] *= 0.75
    t.to_csv(tdir / "rates_central.csv", index=False)
    d = _case(tmp_path)          # one period 2030, window 2026-2030: three unscaled years, two scaled
    switch_case.write_case_inputs(d, {"build_rate": {"enabled": True, "level": "central", "tables_dir": str(tdir),
                                                     "groups": ["wind_onshore", "solar"]}})
    w = pd.read_csv(d / "build_rate_tiers.csv").query("BR_GROUP == 'wind_onshore'")
    assert list(w["br_tier_width"]) == pytest.approx([1.3, 0.45, 0.25])
    assert (w["br_tier_adder_per_mw"] > 0).sum() == 2                                    # +15% and +50% bands kept
    r = pd.read_csv(d / "build_rate_periods.csv").set_index("BR_GROUP").loc["wind_onshore", "br_rate_data_mw"]
    assert r == pytest.approx((3 * 8000 + 2 * 6000) / 5)


# ---------------------------------------------------------------------------- §79 nuclear path group

def test_nuclear_path_group_rows():
    """§79: nuclear's national ceiling is the sourced step path from 2031 (central / high by the level's deliverability
    path), R = ceiling / 2.0 (tiers kept), no ramp bound; no rows before 2031."""
    pg = CFG["path_groups"]["nuclear"]
    assert pg["first_year"] == 2031
    assert pg["ceiling_gw"]["central"] == {2031: 0.8, 2036: 2.5, 2041: 4.0}
    assert pg["ceiling_gw"]["high"] == {2031: 2.0, 2036: 6.0, 2041: 10.0}
    assert CFG["ramp_floor_mw"]["nuclear"] >= 1e6 and CFG["life_years"]["nuclear"] == 60
    assert set(pg["level_path"]) == set(cli.LEVELS)
    for lv, path in (("central", "central"), ("low", "central"), ("high_ipm", "central"), ("high", "high"),
                     ("reform_bp", "central"), ("reform_bp_siting", "central"), ("high_reform", "high")):
        r = rates.path_group_rows(CFG, lv).set_index("year")
        assert r.index.min() == 2031 and r.index.max() == CFG["horizon_last_year"] and (r["region"] == "national").all()
        for y, gw in ((2031, path == "high" and 2.0 or 0.8), (2035, path == "high" and 2.0 or 0.8),
                      (2036, path == "high" and 6.0 or 2.5), (2045, path == "high" and 10.0 or 4.0),
                      (2050, path == "high" and 10.0 or 4.0)):
            assert r.loc[y, "ceiling_mw_per_yr"] == pytest.approx(gw * 1e3), (lv, y)
            assert r.loc[y, "r_data_mw_per_yr"] == pytest.approx(gw * 1e3 / 2.0)
        assert (r["growth"] == 0).all()
    none = dict(CFG, path_groups={"nuclear": dict(pg, level_path=dict(pg["level_path"], central=None))})
    assert rates.path_group_rows(none, "central").empty
    # rate_tables appends the rows, whatever cfg["groups"] holds
    b = rates.base_rates(_additions(), 2015, 2025)
    shares = rates.regional_shares(b, [2016, 2025])
    nt = rates.near_term(_queue(), rates.completion_rates(_queue(), CFG), rates.cod_delay(_queue(), CFG), CFG)
    t = rates.rate_tables(dict(CFG, groups=["wind_onshore"]), ["central"], b, nt, shares)["central"]
    assert set(t["group"]) == {"wind_onshore", "nuclear"} and t[t.group == "nuclear"]["year"].min() == 2031
    t_ipm = rates.tiers(CFG, "nuclear", rates.level_tier_set(CFG, "high_ipm"))     # central bands in every level
    assert list(t_ipm["upto"]) == [1.3, 1.75, 2.0] and list(t_ipm["adder"]) == [0.0, 0.15, 0.5]


def test_case_writer_nuclear_from_2035_period(tmp_path, monkeypatch):
    """§79: the case writer limits nuclear only in periods whose window reaches the path (2035 on: window 2031-35);
    a case with no such period gets no nuclear group or generators in its files (earlier stages unchanged)."""
    monkeypatch.setattr(switch_case, "REPO_ROOT", REPO)
    tdir = _tables(tmp_path)
    t = pd.read_csv(tdir / "rates_central.csv")
    pd.concat([t, rates.path_group_rows(CFG, "central")]).to_csv(tdir / "rates_central.csv", index=False)
    pd.concat([rates.tiers(CFG, g).assign(group=g) for g in ("wind_onshore", "solar", "gas", "nuclear")]).to_csv(
        tdir / "tiers.csv", index=False)
    s = {"build_rate": {"enabled": True, "level": "central", "tables_dir": str(tdir),
                        "groups": ["wind_onshore", "solar", "nuclear"]}}

    def case(name, periods):
        (tmp_path / name).mkdir()
        d = _case(tmp_path / name)
        pd.DataFrame(periods, columns=["INVESTMENT_PERIOD", "period_start", "period_end"]).to_csv(
            d / "periods.csv", index=False)
        gi = pd.read_csv(d / "gen_info.csv")
        gi = pd.concat([gi, pd.DataFrame([{"GENERATION_PROJECT": "n1", "gen_tech": "Nuclear_Nuclear_Large",
                                           "gen_energy_source": "Uranium", "gen_load_zone": "p60",
                                           "gen_is_distributed": 0}])])
        gi.to_csv(d / "gen_info.csv", index=False)
        bc = pd.read_csv(d / "gen_build_costs.csv")
        pd.concat([bc, pd.DataFrame([{"GENERATION_PROJECT": "n1", "build_year": periods[-1][0],
                                      "gen_overnight_cost": 7.0e6, "gen_fixed_om": 0}])]).to_csv(
            d / "gen_build_costs.csv", index=False)
        switch_case.write_case_inputs(d, s)
        return d

    d = case("a", [(2030, 2029, 2030), (2035, 2031, 2035), (2040, 2036, 2040)])
    per = pd.read_csv(d / "build_rate_periods.csv")
    n = per[per.BR_GROUP == "nuclear"].set_index("PERIOD")["br_rate_data_mw"]
    assert list(n.index) == [2035, 2040]
    assert n[2035] == pytest.approx(400.0) and n[2040] == pytest.approx(1250.0)   # 0.8 / 2.5 GW / 2.0
    tiers = pd.read_csv(d / "build_rate_tiers.csv").query("BR_GROUP == 'nuclear'")
    assert list(tiers[tiers.PERIOD == 2040]["br_tier_width"]) == pytest.approx([1.3, 0.45, 0.25])
    assert tiers[(tiers.PERIOD == 2040) & (tiers.BR_TIER == "t2")]["br_tier_adder_per_mw"].iat[0] == pytest.approx(
        0.15 * 7.0e6)
    grp = pd.read_csv(d / "build_rate_groups.csv").set_index("BR_GROUP")
    assert grp.loc["nuclear", "br_ramp_floor_mw"] >= 1e6 and grp.loc["nuclear", "br_growth"] == 0
    assert pd.read_csv(d / "build_rate_gens.csv").set_index("GENERATION_PROJECT").loc["n1", "br_gen_group"] == "nuclear"
    d2 = case("b", [(2030, 2029, 2030)])
    assert "nuclear" not in set(pd.read_csv(d2 / "build_rate_groups.csv")["BR_GROUP"])
    assert "n1" not in set(pd.read_csv(d2 / "build_rate_gens.csv")["GENERATION_PROJECT"])
    assert "nuclear" not in set(pd.read_csv(d2 / "build_rate_periods.csv")["BR_GROUP"])
