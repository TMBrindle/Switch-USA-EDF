"""Regional planning reserve requirement (s0_production.prm.design: regional; CHANGES §55): case build
(s0_workflow/prm.py) and the Switch module (switch/study_modules/prm_regional.py, solved on small toys).

Toy inputs are fixtures built by hand (not results). Run from the repo root:
    SWITCH_SRC=/opt/switch-src pytest -q s0_workflow/tests/test_prm.py
"""
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s0_workflow import prm  # noqa: E402
from s0_workflow import production as s0prod  # noqa: E402

CFG = prm.config()
P = prm._merge(prm.DEFAULTS, yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]["prm"])


# ------------------------------------------------------------------------------------------------ 1. regions
def test_regions_nercr_with_wecc_nw_split():
    r = prm.zone_regions()
    assert len(r) == 134 and len(set(r.values())) == 16
    assert {"NorthernGrid_West", "NorthernGrid_South", "NorthernGrid_East", "WestConnect_North"} <= set(r.values())
    assert "WECC_NW" not in r.values()
    h = pd.read_csv(REPO / "hierarchy.csv").set_index("ba")
    assert sum(1 for z, x in r.items() if x == "NorthernGrid_East") == 9 and r["p24"] == "WestConnect_North"
    assert all(r[z] == h.at[z, "nercr"] for z in r if h.at[z, "nercr"] != "WECC_NW")
    # every region has a margin entry and an import share (WECC_NW's for its four parts)
    for x in set(r.values()):
        assert prm.region_entry(CFG, x)["rml_2030"] > 0 and prm.import_share(CFG, x, 2035, P) > 0
    assert prm.import_share(CFG, "NorthernGrid_West", 2035, P) == 0.140
    with pytest.raises(ValueError, match="spans reserve regions"):
        prm.zone_regions(["AGG"], {"p24": "AGG", "p25": "AGG"})          # WestConnect_North + NorthernGrid_South
    assert prm.zone_regions(["AGG"], {"p42": "AGG", "p43": "AGG"}) == {"AGG": "MISO"}


# ------------------------------------------------------------------------------------------------ 2. margins
def test_margin_levels_and_derated_formula():
    pjm = prm.region_entry(CFG, "PJM")
    assert prm.rml(pjm, 2026) == 0.186 and prm.rml(pjm, 2030) == 0.263 and prm.rml(pjm, 2045) == 0.263
    assert prm.rml(pjm, 2028) == pytest.approx((0.186 + 0.263) / 2)
    gi = pd.DataFrame({"GENERATION_PROJECT": ["a_cc", "a_ct", "a_coal", "a_nuc", "a_wind"],
                       "gen_tech": ["Natural Gas Fired Combined Cycle", "Natural Gas Fired Combustion Turbine",
                                    "Conventional Steam Coal", "Nuclear", "Onshore Wind Turbine"],
                       "gen_energy_source": ["naturalgas", "naturalgas", "coal", "uranium", "wind"],
                       "gen_is_variable": [0, 0, 0, 0, 1], "gen_load_zone": ["p120"] * 5, "gen_max_age": [60] * 5})
    gbp = pd.DataFrame({"GENERATION_PROJECT": ["a_cc", "a_ct", "a_coal", "a_nuc", "a_wind", "a_coal"],
                        "build_year": [2010, 2010, 1980, 1990, 2015, 1960],
                        "build_gen_predetermined": [1000.0, 500.0, 800.0, 1200.0, 900.0, 300.0]})
    cl = prm.classify(gi)
    m = prm.margins(CFG, {"p120": "PJM"}, [2028, 2035], gi, gbp, cl, P).set_index("PERIOD")
    fw = (1000 * 0.05 + 500 * 0.05 + 800 * 0.08 + 1200 * 0.03) / 3500        # wind not thermal; 1960 coal retired
    assert m.at[2035, "for_w"] == pytest.approx(fw, abs=1e-6) and m.at[2035, "thermal_mw"] == 3500
    assert m.at[2035, "prm_margin"] == pytest.approx((1 + 0.263) * (1 - fw) - 1, abs=1e-6)
    assert m.at[2028, "prm_margin"] == pytest.approx((1 + (0.186 + 0.263) / 2) * (1 - fw) - 1, abs=1e-6)
    assert m.at[2035, "prm_import_share"] == 0.079


def test_import_modes_and_penalty():
    assert prm.import_share(CFG, "PJM", 2035, P) == 0.079
    rel = dict(P, imports={"mode": "relaxed", "relax_from": 2031, "relax_to": 2050})
    assert prm.import_share(CFG, "PJM", 2030, rel) == 0.079 and prm.import_share(CFG, "PJM", 2050, rel) == 1.0
    assert prm.import_share(CFG, "PJM", 2040, rel) == pytest.approx(0.079 + 0.921 * 9 / 19)
    assert prm.import_share(CFG, "PJM", 2035, dict(P, imports={"mode": "none"})) is None
    # $300/kW-yr (2028$) and $106 (2025$) in the model's 2024$: CPI-U to 2024, then 2.5%/yr
    v, how = prm.penalty_per_mw_yr(P, 2024)
    assert v == pytest.approx(300e3 / 1.025 ** 4, rel=1e-9) and "2028$" in how and "271.79" in how
    v, _ = prm.penalty_per_mw_yr(dict(P, penalty="low"), 2024)
    assert v == pytest.approx(106e3 / 1.025, rel=1e-9)


# ------------------------------------------------------------------------------------------------ 4. FOR
def test_forced_outage_classes_and_seasonal_mapping():
    td = P["thermal_derate"]
    sf = lambda tech, src, season, var=0, st=False: prm.stress_for(  # noqa: E731
        CFG, *prm.gen_class(tech, src, var, st)[1:], season, td)
    assert sf("Natural Gas Fired Combustion Turbine", "naturalgas", "winter") == pytest.approx(0.199)
    assert sf("Natural Gas Fired Combustion Turbine", "naturalgas", "summer") == pytest.approx(0.066)
    assert sf("Natural Gas Fired Combustion Turbine", "naturalgas", "shoulder") == 0.05
    assert sf("Natural Gas Fired Combined Cycle", "naturalgas", "winter") == pytest.approx(0.149)
    assert sf("Conventional Steam Coal", "coal", "summer") == pytest.approx(0.140)
    assert sf("Nuclear", "uranium", "summer") == pytest.approx(0.124)
    assert sf("Petroleum Liquids", "distillate", "winter") == pytest.approx(0.212)
    assert sf("Geothermal", "geothermal", "winter") == 0.129                         # no curve: static
    assert sf("Other_peaker", "naturalgas", "winter") == pytest.approx(0.199)        # peaker -> CT curve
    assert prm.curve_for(CFG, "combined_cycle", 27.5) == pytest.approx(0.038)
    assert prm.curve_for(CFG, "combined_cycle", -30) == pytest.approx(0.149)          # clamped at the ends
    assert [prm.season_of_month(m, td) for m in (1, 4, 7, 10, 11)] == ["winter", "shoulder", "summer", "shoulder",
                                                                        "winter"]
    # the classes of a real case's generators (s4x1_fedpol_current, committed)
    gi = pd.read_csv(REPO / "switch/in/foresight/s4x1_fedpol_current/gen_info.csv", na_values=".")
    c = prm.classify(gi).set_index("GENERATION_PROJECT")
    assert c.at["p1_batteries_1", "prm_credit"] == "storage" and c.at["p1_batteries_1", "prm_class"] == "storage_4h"
    assert c.at["p10_hydroelectric_pumped_storage_1", "prm_class"] == "pumped_storage"
    assert c.at["p1_load_growth_1", "prm_credit"] == "dispatch" and c.at["p1_load_growth_1", "prm_class"] == "dr"
    assert c.at["p1_imports_base_base_1", "prm_credit"] == "dispatch"
    assert tuple(c.loc["p1_distributed_generation_1", ["prm_credit", "prm_class"]]) == ("variable", "solar")
    assert tuple(c.loc["p1_other_peaker_1", ["prm_credit", "prm_class"]]) == ("capacity", "gas_ct")
    hyd = gi[gi.gen_tech == "Conventional Hydroelectric"].GENERATION_PROJECT
    assert (c.loc[hyd, "prm_credit"] == "dispatch").all()
    assert not (c.prm_class == "other").any(), c[c.prm_class == "other"]


def test_hourly_temperature_path(tmp_path):
    """ReEDS's temperature_state.h5 layout (index_<year>, <year>, columns), read in the given time zone."""
    h5py = pytest.importorskip("h5py")
    f = tmp_path / "temperature_state.h5"
    idx = pd.date_range("2007-01-01 06:00", periods=48, freq="h", tz="UTC")
    with h5py.File(f, "w") as h:
        h["columns"] = np.array([b"PA", b"TX"])
        h["index_2007"] = np.array([str(t.tz_localize(None)).encode() for t in idx])
        h["2007"] = np.column_stack([np.linspace(-20, 5, 48), np.full(48, 30.0)])
    t = prm.state_temperatures(f, [2007], "Etc/GMT+6")
    assert t.index[0] == pd.Timestamp("2007-01-01 00:00", tz="Etc/GMT+6") and list(t.columns) == ["PA", "TX"]
    assert prm.zone_states(["p120", "p65"]) == {"p120": "PA", "p65": "TX"}
    assert float(prm.curve_for(CFG, "combustion_turbine", t.iloc[0]["PA"])) == pytest.approx(0.199)


# ------------------------------------------------------------------------------------------------ 3. stress days
def _synthetic_year(regions=("A", "B", "C"), days=7 * 365, seed=0):
    """Hourly load and VRE CF for 7 weather years with planted extremes (fixture)."""
    rng = np.random.default_rng(seed)
    h = np.arange(days * 24)
    cal = prm.day_dates(days, 2007)
    load, net = {}, {}
    plant = {"A": (2007 * 0 + 200, 400), "B": (900, 1100), "C": (1700, 1950)}   # (summer day, winter day)
    for r in regions:
        base = 100 + 10 * np.sin(2 * np.pi * (h % 24) / 24) + rng.normal(0, 1, len(h))
        sd, wd = plant[r]
        assert cal.month[sd] in (6, 7, 8, 9) and cal.month[wd] in (12, 1, 2), (cal.month[sd], cal.month[wd])
        base[sd * 24:(sd + 1) * 24] += 60
        base[wd * 24:(wd + 1) * 24] += 50
        nl = base.copy()
        nl[(sd + 30) * 24:(sd + 31) * 24] += 80                              # a calm day
        load[r], net[r] = base, nl
    return load, net, plant


def test_stress_day_greedy_coverage():
    sd = dict(P["stress_days"])
    sd["summer_months"], sd["winter_months"] = [6, 7, 8, 9], [12, 1, 2]
    # plant days in the right months
    cal = prm.day_dates(7 * 365, 2007)
    jul = int(cal[(cal.year == 2008) & (cal.month == 7) & (cal.dom == 15)].day.iloc[0])
    jan = int(cal[(cal.year == 2010) & (cal.month == 1) & (cal.dom == 20)].day.iloc[0])
    aug = int(cal[(cal.year == 2011) & (cal.month == 8) & (cal.dom == 3)].day.iloc[0])
    rng = np.random.default_rng(1)
    H = 7 * 365 * 24
    load, net = {}, {}
    for r, (s, w, calm) in {"A": (jul, jan, aug), "B": (jul, jan + 1, aug + 2), "C": (aug, jan, aug)}.items():
        x = 100 + rng.normal(0, 1, H)
        x[s * 24:(s + 1) * 24] += 60
        x[w * 24:(w + 1) * 24] += 50
        n = x.copy()
        n[calm * 24:(calm + 1) * 24] += 90
        load[r], net[r] = x, n
    days, cov, tol = prm.select_stress_days(load, net, sd)
    assert tol == sd["cover_tolerance"] and (cov.covered_by != "NOT COVERED").all()
    assert set(days.date) == {"2008-07-15", "2010-01-20", "2010-01-21", "2011-08-03", "2011-08-05"}
    assert len(days) <= sd["max_days"] and days.slot.str.match(r"^p\d+$").all()
    c = cov.set_index(["PRM_REGION", "need"])
    assert c.at[("A", "summer_peak_load"), "covered_by"] == "2008-07-15"
    assert c.at[("C", "low_wind_solar"), "covered_by"] == "2011-08-03"
    assert "A low_wind_solar" in days.set_index("date").at["2011-08-03", "covers"]
    # a tight day cap widens the tolerance until it fits
    days2, cov2, tol2 = prm.select_stress_days(load, net, dict(sd, max_days=2, max_tolerance=0.9))
    assert len(days2) == 2 and tol2 > sd["cover_tolerance"] and (cov2.covered_by != "NOT COVERED").all()


def test_add_stress_days_and_ids():
    """Stress days are appended at zero weight to either sampler's result; their ids never collide with the
    sample's, even when a stress day is also a sample day."""
    import ast
    import math
    src = (REPO / "conversion_functions.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "ts_tp_pg_kmeans")
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "dates_in_year" for t in n.targets))]
    g = {"pd": pd, "math": math, "List": list}
    exec(compile(ast.Module(nodes, []), "conversion_functions", "exec"), g)
    ts_tp_pg_kmeans = g["ts_tp_pg_kmeans"]
    rng = np.random.default_rng(2)
    H = 7 * 365 * 24
    lc = pd.DataFrame({"p120": 100 + rng.normal(0, 1, H), "p65": 90 + rng.normal(0, 1, H)})
    var = pd.DataFrame({"r_wind": rng.uniform(0, 1, H), "r_pv": rng.uniform(0, 1, H), "r_gas": 1.0})
    gens = pd.DataFrame({"Resource": ["r_wind", "r_pv", "r_gas"], "technology": ["LandbasedWind_Class3", "UtilityPV_Class1",
                                                                                 "Natural Gas Fired Combustion Turbine"],
                         "region": ["p120", "p120", "p65"]})
    s = {"model_year": 2035, "s0_production": {"enabled": True, "prm": {"design": "regional"}}}
    sample_days = [10, 200, 400]
    res = {"load_profiles": pd.concat([lc.iloc[d * 24:(d + 1) * 24] for d in sample_days]),
           "resource_profiles": pd.concat([var.iloc[d * 24:(d + 1) * 24] for d in sample_days]),
           "ClusterWeights": [100.0, 150.0, 115.0]}
    rep = pd.DataFrame({"slot": [f"p{d + 1}" for d in sample_days]})
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out, rep2, w2, n = prm.add_stress_days(res, rep, [100.0, 150.0, 115.0], lc, var, gens, s, Path(d))
        days = pd.read_csv(Path(d) / "stress_days.csv")
        assert (Path(d) / "stress_coverage.csv").exists()
    assert n == len(days) >= 2 and w2[-n:] == [0.0] * n and len(out["load_profiles"]) == (3 + n) * 24
    assert len(out["resource_profiles"]) == (3 + n) * 24 and (out["resource_profiles"].r_gas == 1.0).all()
    first = days.day.iloc[0]
    assert np.allclose(out["load_profiles"].iloc[72:96].p120.values, lc.p120.values[first * 24:(first + 1) * 24])
    # force a collision: a stress slot equal to a sample slot
    rep2.loc[len(rep2) - 1, "slot"] = "p201"
    ts, tp = ts_tp_pg_kmeans(rep2["slot"], w2, 1, 2035, 2031)
    ts, tp = prm.rename_stress_rows(ts, tp, n)
    assert ts.timeseries.is_unique and tp.timepoint_id.is_unique and len(tp) == (3 + n) * 24
    assert ts.timeseries.iloc[-n:].str.endswith("_prm").all() and (ts.ts_scale_to_period.iloc[-n:] == 0).all()
    assert tp.timepoint_id.iloc[-24:].str.startswith("9").all() and tp.timepoint_id.astype(int).is_unique


# ------------------------------------------------------------------------------------------------ settings, legacy
def test_settings_axis_column_and_legacy():
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0["prm"]["design"] == "regional" and s0["prm"]["imports"]["mode"] == "flat"
    ax = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"]
    assert ax["prm_design"] == {"regional": {"s0_production": {"prm": {"design": "regional"}}},
                                "legacy": {"s0_production": {"prm": {"design": "legacy"}}}}
    assert ax["s0_production"]["on_pgdays"]["s0_production"]["prm"] == {"design": "legacy"}
    si = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv")
    assert set(si.loc[si.prm_design == "regional", "case_id"]) == {"S0prod_A", "S0prod_B", "s4x1_S0prod_2035_prm"}
    a = si[si.case_id == "s4x1_S0prod_2035_prm"].iloc[0]
    b = si[si.case_id == "s4x1_S0prod_2035_txreeds"].iloc[0]
    assert [c for c in si.columns if a[c] != b[c]] == ["case_id", "prm_design"] and a.year == 2035
    # legacy: no settings change, no extra options; regional: the extreme-day script off, modules swapped
    leg = s0prod.deep_merge(s0, ax["s0_production"]["on_pgdays"]["s0_production"])
    s = {"model_adjustment_scripts": {"Add extreme day": {"script": "x"}, "Other": {"script": "y"}}}
    before = copy.deepcopy(s)
    assert prm.apply_settings(s, leg) is False and s == before and prm.scenario_options(leg) == ""
    assert prm.apply_settings(s, s0) is True
    assert s["model_adjustment_scripts"] == {"Add extreme day": None, "Other": {"script": "y"}}
    assert before["model_adjustment_scripts"]["Add extreme day"] == {"script": "x"}      # not mutated in place
    opt = prm.scenario_options(s0)
    assert "--exclude-module study_modules.planning_reserves " in opt and "--include-module study_modules.prm_regional" in opt
    assert "--exclude-module study_modules.planning_reserves_extreme_days" in opt
    assert "--include-module study_modules.prm_regional" in s0prod.scenario_options(
        {"s0_production": dict(s0, enabled=True)})
    with pytest.raises(ValueError, match="prm.design"):
        prm.prm_settings({"prm": {"design": "national"}})
    src = (REPO / "pg_to_switch.py").read_text()
    assert src.count("s0prm.year_prm(year_settings)") == 3 and "s0prm.rename_stress_rows(" in src


def test_benchmarks_from_config():
    b = prm.benchmark_rows(CFG).set_index(["PRM_REGION", "benchmark", "prm_class"]).benchmark_value
    assert b[("PJM", "PJM_2029_30", "gas_ct")] == 0.59 and b[("PJM", "PJM_2029_30", "storage_4h")] == 0.57
    assert b[("NPCC_NY", "NYISO_2026_27_ROS", "dr")] == 0.7417
    assert b[("NPCC_NE", "ISONE_prelim_winter", "storage_4h")] == 0.27
    assert b[("SPP", "SPP_2024_summer", "onshore_wind")] == 0.154
    assert b[("NPCC_NE", "ISONE_prelim_summer", "gas_cc")] == 0.919


def test_write_case_inputs(tmp_path):
    """The case build's prm files on a small folder with two stress days (fixture)."""
    tsr = pd.DataFrame({"timeseries": ["2035_p10", "2035_p200_prm", "2035_p20_prm"], "ts_period": 2035,
                        "ts_duration_of_tp": 1, "ts_num_tps": 2, "ts_scale_to_period": [1825.0, 0, 0]})
    tsr.to_csv(tmp_path / "timeseries.csv", index=False)
    pd.DataFrame({"timepoint_id": [203510101, 203510102, 92035107190017, 92035107190018, 92035101200007,
                                   92035101200008],
                  "timeseries": ["2035_p10"] * 2 + ["2035_p200_prm"] * 2 + ["2035_p20_prm"] * 2}).to_csv(
        tmp_path / "timepoints.csv", index=False)
    pd.DataFrame({"INVESTMENT_PERIOD": [2035], "period_start": [2031], "period_end": [2035]}).to_csv(
        tmp_path / "periods.csv", index=False)
    pd.DataFrame({"LOAD_ZONE": ["p120", "p65"]}).to_csv(tmp_path / "load_zones.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["ct", "ccgt", "wind", "bat"],
                  "gen_tech": ["Natural Gas Fired Combustion Turbine", "Natural Gas Fired Combined Cycle",
                               "Onshore Wind Turbine", "Batteries"],
                  "gen_energy_source": ["naturalgas", "naturalgas", "wind", "storage"],
                  "gen_is_variable": [0, 0, 1, 0], "gen_storage_efficiency": [None, None, None, 0.85],
                  "gen_load_zone": ["p120", "p65", "p120", "p65"], "gen_max_age": [40, 40, 30, 15]}).to_csv(
        tmp_path / "gen_info.csv", index=False, na_rep=".")
    pd.DataFrame({"GENERATION_PROJECT": ["ct", "ccgt"], "build_year": [2010, 2015],
                  "build_gen_predetermined": [100.0, 200.0]}).to_csv(tmp_path / "gen_build_predetermined.csv", index=False)
    d = tmp_path / "prm" / "2035"
    d.mkdir(parents=True)
    pd.DataFrame({"TIMESERIES": ["2035_p200_prm", "2035_p20_prm"], "season": ["summer", "winter"],
                  "date": ["2007-07-19", "2007-01-20"]}).to_csv(d / "stress_days.csv", index=False)
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    lines = []
    prm.write_case_inputs(tmp_path, s0, {2035: {"target_usd_year": 2024}}, lines.append)
    assert pd.read_csv(tmp_path / "prm_zones.csv").set_index("LOAD_ZONE").PRM_REGION.to_dict() == {"p120": "PJM",
                                                                                                   "p65": "ERCOT"}
    rp = pd.read_csv(tmp_path / "prm_region_periods.csv").set_index("PRM_REGION")
    assert rp.at["PJM", "prm_margin"] == pytest.approx((1.263) * (1 - 0.05) - 1, abs=1e-6)
    assert rp.at["ERCOT", "prm_import_share"] == 0.009
    assert list(pd.read_csv(tmp_path / "prm_timeseries.csv").TIMESERIES) == ["2035_p200_prm", "2035_p20_prm"]
    av = pd.read_csv(tmp_path / "prm_gen_availability.csv").set_index(["GENERATION_PROJECT", "TIMEPOINT"]).prm_avail_frac
    assert av[("ct", 92035107190017)] == pytest.approx(1 - 0.066)        # summer: hot end
    assert av[("ct", 92035101200007)] == pytest.approx(1 - 0.199)        # winter: cold end
    assert av[("ccgt", 92035101200008)] == pytest.approx(1 - 0.149)
    assert ("wind", 92035107190017) not in av.index and len(av) == 8      # capacity class only, stress hours only
    pr = pd.read_csv(tmp_path / "prm_params.csv").iloc[0]
    assert pr.prm_new_tx_derate == 0.15 and pr.prm_shortfall_cost_per_mw_yr == pytest.approx(300e3 / 1.025 ** 4, abs=0.01)
    assert "prm regional: 2 regions" in lines[0] and "271.79" in lines[0]
    gc = pd.read_csv(tmp_path / "prm_gen_credit.csv").set_index("GENERATION_PROJECT")
    assert gc.at["bat", "prm_credit"] == "storage" and gc.at["wind", "prm_credit"] == "variable"


# ------------------------------------------------------------------------------------------------ Switch module toys
GI_COLS = ["GENERATION_PROJECT", "gen_tech", "gen_load_zone", "gen_connect_cost_per_mw", "gen_capacity_limit_mw",
           "gen_full_load_heat_rate", "gen_variable_om", "gen_max_age", "gen_min_build_capacity",
           "gen_scheduled_outage_rate", "gen_forced_outage_rate", "gen_is_variable", "gen_is_baseload", "gen_is_cogen",
           "gen_energy_source", "gen_unit_size", "gen_ccs_capture_efficiency", "gen_ccs_energy_load",
           "gen_storage_efficiency", "gen_store_to_release_ratio"]


def mini_case(tmp_path, name, gens, loads, lines, regions, periods_rows, credit, params, stress_hours=(6, 6, 6, 6),
              cf=None, storage=False, new_tx=False, built_before=None):
    """A one-period (2020) toy: timeseries 2020_norm (weighted, 2 x 12 h) and 2020_stress (zero weight, 4 x 6 h).
    gens: (name, zone, kind, MW[, MWh]) with kind thermal | backup | wind | storage; loads: {zone: ([norm x2],
    [stress x4])}; lines: (name, z1, z2, MW, efficiency)."""
    from toyutil import solve, toy_inputs

    def edit(inp):
        pd.DataFrame({"INVESTMENT_PERIOD": [2020], "period_start": [2017], "period_end": [2026]}).to_csv(
            inp / "periods.csv", index=False)
        pd.DataFrame({"TIMESERIES": ["2020_norm", "2020_stress"], "ts_period": [2020, 2020],
                      "ts_duration_of_tp": [12, 6], "ts_num_tps": [2, 4], "ts_scale_to_period": [3652.5, 0.0]}).to_csv(
            inp / "timeseries.csv", index=False)
        tps = [1, 2, 901, 902, 903, 904]
        pd.DataFrame({"timepoint_id": tps, "timestamp": [2020070100, 2020070112, 2020071500, 2020071506, 2020071512,
                                                         2020071518],
                      "timeseries": ["2020_norm"] * 2 + ["2020_stress"] * 4}).to_csv(inp / "timepoints.csv", index=False)
        pd.DataFrame([{"LOAD_ZONE": z, "TIMEPOINT": t, "zone_demand_mw": v} for z, (n, s) in loads.items()
                      for t, v in zip(tps, list(n) + list(s))]).to_csv(inp / "loads.csv", index=False)
        for f, col in (("fuel_cost.csv", "period"), ("fuel_supply_curves.csv", "period"),
                       ("zone_fuel_cost_diff.csv", "period")):
            if (inp / f).exists():
                x = pd.read_csv(inp / f, na_values=["."])
                x[x[col] == 2020].to_csv(inp / f, index=False, na_rep=".")
        lz = pd.read_csv(inp / "load_zones.csv")
        lz["local_td_loss_rate"] = 0.0
        lz.to_csv(inp / "load_zones.csv", index=False)
        rows, costs, pre = [], [], []
        for g in gens:
            n, z, kind, mw = g[:4]
            src = {"thermal": "Geothermal", "backup": "Geothermal", "wind": "Wind", "storage": "Electricity"}[kind]
            r = {c: "." for c in GI_COLS}
            r.update(GENERATION_PROJECT=n, gen_tech=kind, gen_load_zone=z, gen_connect_cost_per_mw=0,
                     gen_variable_om=1.0 if kind != "backup" else 500.0, gen_max_age=100, gen_min_build_capacity=0,
                     gen_scheduled_outage_rate=0, gen_forced_outage_rate=0, gen_is_variable=int(kind == "wind"),
                     gen_is_baseload=0, gen_is_cogen=0, gen_energy_source=src)
            if kind == "storage":
                r.update(gen_storage_efficiency=0.9, gen_store_to_release_ratio=1.0)
            rows.append(r)
            c = {"GENERATION_PROJECT": n, "build_year": 2000, "gen_overnight_cost": 0.0, "gen_fixed_om": 0.0}
            if storage:
                c["gen_storage_energy_overnight_cost"] = 0.0 if kind == "storage" else "."
            costs.append(c)
            p = {"GENERATION_PROJECT": n, "build_year": 2000, "build_gen_predetermined": mw}
            if storage:
                p["build_gen_energy_predetermined"] = g[4] if kind == "storage" else "."
            pre.append(p)
        pd.DataFrame(rows)[GI_COLS].to_csv(inp / "gen_info.csv", index=False)
        pd.DataFrame(costs).to_csv(inp / "gen_build_costs.csv", index=False, na_rep=".")
        pd.DataFrame(pre).to_csv(inp / "gen_build_predetermined.csv", index=False, na_rep=".")
        pd.DataFrame([{"GENERATION_PROJECT": n, "timepoint": t, "gen_max_capacity_factor": (cf or {}).get(n, 0.5)}
                      for n, z, k, *_ in gens if k == "wind" for t in tps],
                     columns=["GENERATION_PROJECT", "timepoint", "gen_max_capacity_factor"]).to_csv(
            inp / "variable_capacity_factors.csv", index=False)
        pd.DataFrame([{"TRANSMISSION_LINE": n, "trans_lz1": a, "trans_lz2": b, "trans_length_km": 100,
                       "trans_efficiency": e, "existing_trans_cap": mw, "trans_new_build_allowed": int(new_tx)}
                      for n, a, b, mw, e in lines]).to_csv(inp / "transmission_lines.csv", index=False)
        if storage:
            with open(inp / "modules.txt", "a") as fh:
                fh.write("switch_model.generators.extensions.storage\n")
        pd.DataFrame({"TIMESERIES": ["2020_stress"]}).to_csv(inp / "prm_timeseries.csv", index=False)
        pd.DataFrame({"LOAD_ZONE": list(regions), "PRM_REGION": list(regions.values())}).to_csv(
            inp / "prm_zones.csv", index=False)
        pd.DataFrame(periods_rows).to_csv(inp / "prm_region_periods.csv", index=False, na_rep=".")
        pd.DataFrame([{"GENERATION_PROJECT": n, "prm_credit": c, "prm_class": k} for (n, z, k, *_), c in
                      zip(gens, [credit.get(g[0], {"backup": "none", "thermal": "capacity", "wind": "variable",
                                                   "storage": "storage"}[g[2]]) for g in gens])]).to_csv(
            inp / "prm_gen_credit.csv", index=False)
        pd.DataFrame([params]).to_csv(inp / "prm_params.csv", index=False)
        if built_before:                                  # chained stage: new lines of earlier stages
            pd.DataFrame({"TRANSMISSION_LINE": list(built_before), "trans_built_to_date_mw": list(built_before.values())}
                         ).to_csv(inp / "trans_built_to_date.csv", index=False)
        for f in ("zone_coincident_peak_demand.csv",):
            (inp / f).unlink(missing_ok=True)
    run = toy_inputs(tmp_path, name, ["prm_regional"], edit)
    return solve(run, extra=("--include-module", "mods.prm_regional"))


PEN = {"prm_new_tx_derate": 0.15, "prm_shortfall_cost_per_mw_yr": 1e6, "prm_import_cap_all_hours": 1}
BACKUP = [("N-backup", "North", "backup", 1000), ("C-backup", "Central", "backup", 1000), ("S-backup", "South", "backup", 1000)]


def _read(out, f):
    return pd.read_csv(out / f)


def test_toy_deliverability_losses_and_stress_hours_only(tmp_path):
    """One region North + Central: North's capacity reaches Central only over the N-C line, after losses; the
    requirement holds in the stress hours only (the weighted hours have a much higher load and no requirement)."""
    gens = BACKUP + [("N-th", "North", "thermal", 20), ("C-wind", "Central", "wind", 4)]
    loads = {"North": ([50, 50], [0, 0, 0, 0]), "Central": ([50, 50], [10, 10, 10, 10]), "South": ([1, 1], [1, 1, 1, 1])}
    reg = {"North": "R", "Central": "R"}
    per = [{"PRM_REGION": "R", "PERIOD": 2020, "prm_margin": 0.0, "prm_import_share": "."}]
    out = mini_case(tmp_path, "deliv", gens, loads, [("N-C", "North", "Central", 5, 0.9), ("C-S", "Central", "South", 0, 1.0)],
                    reg, per, {}, PEN, cf={"C-wind": 0.5})
    zh = _read(out, "prm_zone_hours.csv")
    assert set(zh.TIMESERIES) == {"2020_stress"}                              # stress hours only
    c = zh[zh.LOAD_ZONE == "Central"]
    assert c.net_inflow_mw.round(6).eq(4.5).all()                             # 5 MW x 0.9 delivered
    assert c.local_credit_mw.round(6).eq(2.0).all()                           # wind 4 MW x CF 0.5
    sf = _read(out, "prm_shortfall.csv").set_index("PRM_REGION")
    assert sf.at["R", "shortfall_mw"] == pytest.approx(10 - 4.5 - 2.0, abs=1e-6)  # region total 22 would suffice
    # implied capacity credit (dual-weighted credited MW / capacity), the reserve price at the penalty
    cc = _read(out, "prm_capacity_credit.csv").set_index("prm_class")
    assert cc.at["thermal", "implied_capacity_credit"] == pytest.approx(1.0, abs=1e-6)
    assert cc.at["wind", "implied_capacity_credit"] == pytest.approx(0.5, abs=1e-6)
    assert cc.at["backup", "implied_capacity_credit"] == pytest.approx(0, abs=1e-9)
    zc = _read(out, "prm_zone_hours.csv")
    assert zc[zc.LOAD_ZONE == "Central"].price_usd_per_kw_yr.sum() == pytest.approx(1e6 / 1000, rel=1e-6)
    ci = _read(out, "costs_itemized.csv").set_index("Component")
    assert ci.at["PrmShortfallCost", "AnnualCost_Real"] == pytest.approx(3.5 * 1e6, rel=1e-6)
    assert sf.at["R", "annual_cost"] == pytest.approx(3.5e6, rel=1e-6)
    # a bigger line delivers it all
    out2 = mini_case(tmp_path, "deliv2", gens, loads, [("N-C", "North", "Central", 50, 0.9),
                                                         ("C-S", "Central", "South", 0, 1.0)], reg, per, {}, PEN,
                     cf={"C-wind": 0.5})
    assert _read(out2, "prm_shortfall.csv").shortfall_mw.max() == pytest.approx(0, abs=1e-6)
    fl = _read(out2, "PrmFlow.csv")
    assert fl[fl.PRM_TX_TPS_1 == "North"].PrmFlow.max() == pytest.approx(8 / 0.9, abs=1e-5)
    s = _read(out2, "prm_summary.csv").iloc[0]
    assert s.min_margin_achieved == pytest.approx(22 / 10 - 1) and bool(s.duals_available)   # region margin
    zc = _read(out2, "prm_zone_hours.csv")
    zc = zc[zc.LOAD_ZONE == "Central"]
    assert (zc.local_credit_mw + zc.net_inflow_mw).round(6).eq(10).all()               # Central's own check binds


def test_toy_import_cap_losses_and_new_line_derate(tmp_path):
    """North alone in its region: net imports (after losses) capped at share x the region's peak in every stress
    hour; new transmission counts 85% for reserves."""
    gens = BACKUP + [("N-th", "North", "thermal", 6), ("C-th", "Central", "thermal", 100)]
    loads = {"North": ([5, 5], [10, 10, 10, 10]), "Central": ([5, 5], [5, 5, 5, 5]), "South": ([1, 1], [1, 1, 1, 1])}
    reg = {"North": "A", "Central": "B"}
    per = [{"PRM_REGION": "A", "PERIOD": 2020, "prm_margin": 0.0, "prm_import_share": 0.2},
           {"PRM_REGION": "B", "PERIOD": 2020, "prm_margin": 0.0, "prm_import_share": "."}]
    lines = [("N-C", "North", "Central", 50, 0.9), ("C-S", "Central", "South", 0, 1.0)]
    out = mini_case(tmp_path, "cap", gens, loads, lines, reg, per, {}, PEN)
    rh = _read(out, "prm_region_hours.csv")
    a = rh[rh.PRM_REGION == "A"]
    assert a.net_import_mw.max() == pytest.approx(2.0, abs=1e-6) and a.import_cap_mw.iloc[0] == pytest.approx(2.0)
    assert _read(out, "prm_shortfall.csv").set_index("PRM_REGION").at["A", "shortfall_mw"] == pytest.approx(2.0, abs=1e-6)
    assert a.import_cap_price_usd_per_kw_yr.max() > 0                                   # the cap binds: a price
    # a share of 0 is enforced as 0 (no 1e-6 workaround needed)
    per0 = [dict(per[0], prm_import_share=0.0), per[1]]
    out0 = mini_case(tmp_path, "cap0", gens, loads, lines, reg, per0, {}, PEN)
    assert _read(out0, "prm_shortfall.csv").set_index("PRM_REGION").at["A", "shortfall_mw"] == pytest.approx(4.0, abs=1e-6)
    r0 = _read(out0, "prm_region_hours.csv")
    assert r0[r0.PRM_REGION == "A"].net_import_mw.max() <= 1e-6
    # no cap, no existing line, new line allowed: build x with 0.85 * 0.9 * x = 4
    per2 = [dict(per[0], prm_import_share="."), per[1]]
    out2 = mini_case(tmp_path, "newtx", gens, loads, [("N-C", "North", "Central", 0, 0.9),
                                                       ("C-S", "Central", "South", 0, 1.0)], reg, per2, {}, PEN,
                     new_tx=True)
    b = _read(out2, "BuildTx.csv")
    assert b[b.TRANS_BLD_YRS_1 == "N-C"].BuildTx.sum() == pytest.approx(4 / (0.85 * 0.9), rel=1e-5)
    assert _read(out2, "prm_shortfall.csv").shortfall_mw.max() == pytest.approx(0, abs=1e-6)
    # a chained stage: the line was built by an earlier stage (now existing capacity, trans_built_to_date.csv);
    # it still counts 85%: 4/0.9 MW delivers 0.85 x 4 = 3.4 MW, shortfall 0.6 (without the file it would be 0)
    lines3 = [("N-C", "North", "Central", 4 / 0.9, 0.9), ("C-S", "Central", "South", 0, 1.0)]
    out3 = mini_case(tmp_path, "chained", gens, loads, lines3, reg, per2, {}, PEN, built_before={"N-C": 4 / 0.9})
    assert _read(out3, "prm_shortfall.csv").set_index("PRM_REGION").at["A", "shortfall_mw"] == pytest.approx(0.6, abs=1e-6)
    out4 = mini_case(tmp_path, "chained0", gens, loads, lines3, reg, per2, {}, PEN)
    assert _read(out4, "prm_shortfall.csv").set_index("PRM_REGION").at["A", "shortfall_mw"] == pytest.approx(0, abs=1e-6)


def test_toy_storage_backed_by_state_of_charge(tmp_path):
    """Storage counts its net discharge, and its state of charge runs through the stress day: with a flat deficit in
    every stress hour it can't help (it would have to charge in a deficit hour), so the shortfall is the deficit."""
    gens = BACKUP + [("N-th", "North", "thermal", 8), ("N-bat", "North", "storage", 10, 40)]
    loads = {"North": ([5, 5], [10, 10, 10, 10]), "Central": ([1, 1], [1, 1, 1, 1]), "South": ([1, 1], [1, 1, 1, 1])}
    per = [{"PRM_REGION": "A", "PERIOD": 2020, "prm_margin": 0.0, "prm_import_share": "."}]
    lines = [("N-C", "North", "Central", 0, 1.0), ("C-S", "Central", "South", 0, 1.0)]
    out = mini_case(tmp_path, "soc", gens, loads, lines, {"North": "A"}, per, {}, PEN, storage=True)
    assert _read(out, "prm_shortfall.csv").shortfall_mw.iloc[0] == pytest.approx(2.0, abs=1e-6)
    # with a peak in one hour only, it shifts energy charged in the other hours (charging counted there)
    loads2 = {"North": ([5, 5], [6, 6, 12, 6]), "Central": ([1, 1], [1, 1, 1, 1]), "South": ([1, 1], [1, 1, 1, 1])}
    out2 = mini_case(tmp_path, "soc2", gens, loads2, lines, {"North": "A"}, per, {}, PEN, storage=True)
    assert _read(out2, "prm_shortfall.csv").shortfall_mw.iloc[0] == pytest.approx(0, abs=1e-6)
    zh = _read(out2, "prm_zone_hours.csv").set_index("TIMEPOINT")
    assert zh.at[903, "local_credit_mw"] == pytest.approx(12, abs=1e-6)                 # 8 thermal + 4 storage
    assert zh.loc[[901, 902, 904], "local_credit_mw"].min() >= 6 - 1e-6                 # charging within the slack
    # nothing costs anything to meet here: no price, so no dual-weighted credit (reported as blank, not zero)
    cc = _read(out2, "prm_capacity_credit.csv").set_index("prm_class")
    assert np.isnan(cc.at["storage", "implied_capacity_credit"]) and cc.at["storage", "binding_weight"] == 0
    assert -0.05 < cc.at["storage", "mean_stress_credit"] < 0                          # round-trip losses over the day


def test_toy_storage_cannot_launder_imports(tmp_path):
    """North imports are capped at 5% of its peak (1 MW). If the cap held only in the peak hour (the diagnostic
    variant), storage would charge from uncapped imports in the other hours and meet the peak with no shortfall.
    With the cap in every stress hour (the default) it can only store the capped imports."""
    gens = BACKUP + [("N-th", "North", "thermal", 10), ("N-bat", "North", "storage", 20, 100),
                     ("C-th", "Central", "thermal", 200)]
    loads = {"North": ([5, 5], [10, 10, 20, 10]), "Central": ([5, 5], [5, 5, 5, 5]), "South": ([1, 1], [1, 1, 1, 1])}
    reg = {"North": "A", "Central": "B"}
    per = [{"PRM_REGION": "A", "PERIOD": 2020, "prm_margin": 0.0, "prm_import_share": 0.05},
           {"PRM_REGION": "B", "PERIOD": 2020, "prm_margin": 0.0, "prm_import_share": "."}]
    lines = [("N-C", "North", "Central", 100, 1.0), ("C-S", "Central", "South", 0, 1.0)]
    naive = mini_case(tmp_path, "naive", gens, loads, lines, reg, per, {}, dict(PEN, prm_import_cap_all_hours=0),
                      storage=True)
    assert _read(naive, "prm_shortfall.csv").set_index("PRM_REGION").at["A", "shortfall_mw"] == pytest.approx(0, abs=1e-6)
    rh = _read(naive, "prm_region_hours.csv")
    assert rh[(rh.PRM_REGION == "A") & (rh.TIMEPOINT != 903)].net_import_mw.max() > 1.5  # laundering through storage
    out = mini_case(tmp_path, "allhours", gens, loads, lines, reg, per, {}, PEN, storage=True)
    sf = _read(out, "prm_shortfall.csv").set_index("PRM_REGION").at["A", "shortfall_mw"]
    # charging hours: 10 + charge <= 10 + 1 + S; peak: 10 + 1 + discharge + S >= 20, discharge <= 0.9 x 3 x (1 + S)
    assert sf == pytest.approx(6.3 / 3.7, abs=1e-5) and sf > 1.0
    rh = _read(out, "prm_region_hours.csv")
    assert rh[rh.PRM_REGION == "A"].net_import_mw.max() <= 1.0 + 1e-6
