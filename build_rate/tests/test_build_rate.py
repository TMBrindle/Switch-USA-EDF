"""Run with: pytest -q  (from build_rate/). Small hand-built frames are test fixtures, not results."""
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
    # after the near-term years, R grows at the level's rate from the last near-term rate
    g = CFG["growth"]["central"]["wind_onshore"]
    assert nat["central"][("wind_onshore", 2035)] == pytest.approx(nat["central"][("wind_onshore", 2030)] * (1 + g) ** 5)
    # regional ceiling = max(share x mult x top x R, floor); MISO wind share 0.25 x small R -> floor applies
    t = tabs["central"].set_index(["group", "region", "year"])
    floor = CFG["regional_floor_mw"]["wind_onshore"]
    row = t.loc[("wind_onshore", "MISO", 2030)]
    assert row["ceiling_mw_per_yr"] == pytest.approx(max(0.25 * 1.5 * 2.0 * nat["central"][("wind_onshore", 2030)], floor))
    assert bool(row["floor_applied"])
    # reform raises wind's regional multiplier and floor
    assert tabs["reform"].set_index(["group", "region", "year"]).loc[("wind_onshore", "MISO", 2030), "ceiling_mw_per_yr"] \
        >= row["ceiling_mw_per_yr"]


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
    for key in ("construction_completion", "growth:", "ramp_floor_mw", "regional_mult", "regional_floor_mw",
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
    assert sg("Nuclear_Nuclear", "Uranium") is None
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

TOY = Path("/opt/switch-src/examples/3zone_toy")


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
                       capture_output=True, text=True, env={**__import__("os").environ, "PYTHONPATH": str(run)})
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
