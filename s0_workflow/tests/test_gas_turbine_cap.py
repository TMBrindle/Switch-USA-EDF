"""Gas-turbine supply cap in the build-rate module (item 5).
Run from the repo root: SWITCH_SRC=/opt/switch-src pytest -q s0_workflow/tests"""
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_rate.brc import turbine_cap as gtc  # noqa: E402
from toyutil import build, toy_run, total_cost  # noqa: E402

# max_mw of MaxCapTag_GasTurbineSupply that make_emission_policies.py wrote into
# pg/settings/scenario_management.yml before the move (settings_management.<year>.all_cases.MaxCapReq)
OLD_MAXCAPREQ = {2024: 451444.19999999995, 2025: 461110.86666666664, 2026: 470777.53333333327,
                 2027: 480444.19999999995, 2028: 490110.86666666664, 2029: 499777.5333333333,
                 2030: 509444.2, 2035: 557777.5333333332, 2040: 606110.8666666667, 2045: 654444.2,
                 2050: 702777.5333333332}


def settings():
    return yaml.safe_load(open(REPO / "pg/settings/build_rate.yml"))


def test_defaults_reproduce_old_cap():
    cfg = gtc.gtc_settings(settings())
    assert cfg is not None, "gas_turbine_cap must be on by default (it replaces an always-on cap)"
    assert cfg["form"] == "cumulative_in_service" and cfg["cc_accounting"] == "full_plant"
    assert cfg["coverage"] == ["combined_cycle", "combustion_turbine"] and not cfg["retirements_free_room"]
    for y, v in OLD_MAXCAPREQ.items():
        assert gtc.cap_mw(cfg, y, y, y) == pytest.approx(v, rel=1e-12), y
    # nothing left in the shared settings or make_emission_policies.py that writes the old tag's limit
    assert "max_mw" not in open(REPO / "pg/settings/scenario_management.yml").read().split(
        "MaxCapTag_GasTurbineSupply:")[-1][:200] or \
        "MaxCapTag_GasTurbineSupply:\n" not in open(REPO / "pg/settings/scenario_management.yml").read()
    src = open(REPO / "make_emission_policies.py").read()
    assert '"MaxCapTag_GasTurbineSupply",' not in src


def test_classes_and_options():
    c = gtc.gas_class
    assert c("Natural Gas Fired Combined Cycle") == "combined_cycle"
    assert c("Natural Gas Fired Combustion Turbine") == "combustion_turbine"
    assert c("Natural Gas Internal Combustion Engine") == "reciprocating_engine"
    assert c("NaturalGas_1-on-1 Combined Cycle (H-Frame)_Moderate") == "combined_cycle"
    assert c("NaturalGas_Combustion Turbine (F-Frame)_Moderate") == "combustion_turbine"
    assert c("NaturalGas_Combustion Turbine (Aeroderivative)_Moderate") == "aeroderivative"
    assert c("NaturalGas_CCCCSAvgCF_Moderate") == "combined_cycle"     # old tag covered all new NaturalGas
    assert c("NG_GT", "NaturalGas") == "combustion_turbine" and c("NG_CC", "NaturalGas") == "combined_cycle"
    assert c("Natural Gas Steam Turbine") is None and c("Coal_ST", "Coal") is None
    cfg = {**gtc.DEFAULTS, "annual_additions_mw": {2025: 10.0, 2030: 20.0}, "baseline_mw": 100.0}
    assert gtc.cap_mw(cfg, 2032, 0, 0) == pytest.approx(100 + 5 * 10 + 3 * 20)
    cfg = {**cfg, "form": "new_additions_per_period"}
    assert gtc.cap_mw(cfg, 2030, 2026, 2030) == pytest.approx(4 * 10 + 20)
    with pytest.raises(ValueError):
        gtc.gtc_settings({"build_rate": {"gas_turbine_cap": {"enabled": True, "coverage": ["steam"]}}})


def test_case_writer_drops_tag_and_raises_floor(tmp_path):
    pd.DataFrame({"GENERATION_PROJECT": ["cc_old", "ct_old", "cc_new", "rice", "coal"],
                  "gen_tech": ["Natural Gas Fired Combined Cycle", "Natural Gas Fired Combustion Turbine",
                               "NaturalGas_1-on-1 Combined Cycle (H-Frame)_Moderate",
                               "Natural Gas Internal Combustion Engine", "Conventional Steam Coal"],
                  "gen_energy_source": ["naturalgas"] * 4 + ["coal"]}).to_csv(tmp_path / "gen_info.csv", index=False)
    pd.DataFrame({"INVESTMENT_PERIOD": [2030, 2035], "period_start": [2029, 2031],
                  "period_end": [2030, 2035]}).to_csv(tmp_path / "periods.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["cc_old", "ct_old", "rice"], "build_year": [2000, 2001, 2002],
                  "build_gen_predetermined": [600.0, 300.0, 50.0]}).to_csv(tmp_path / "gen_build_predetermined.csv",
                                                                         index=False)
    pd.DataFrame({"MAX_CAP_PROGRAM": [gtc.GAS_CAP_TAG, gtc.GAS_CAP_TAG, "MaxCapTag_Ban"], "PERIOD": [2030, 2035, 2030],
                  "max_cap_mw": [1, 2, 0]}).to_csv(tmp_path / "max_cap_requirements.csv", index=False)
    pd.DataFrame({"MAX_CAP_PROGRAM": [gtc.GAS_CAP_TAG] * 3 + ["MaxCapTag_Ban"],
                  "MAX_CAP_GEN": ["cc_old", "ct_old", "cc_new", "coal"]}).to_csv(tmp_path / "max_cap_generators.csv",
                                                                                index=False)
    s = {"build_rate": {"gas_turbine_cap": {"enabled": True, "baseline_mw": 600.0, "baseline_year": 2028,
                                            "annual_additions_mw": {2029: 100.0}}}}
    files = gtc.write_case_inputs(tmp_path, s)
    assert set(files) == {"gas_turbine_cap_gens.csv", "gas_turbine_cap.csv", "gas_turbine_cap_params.csv"}
    assert gtc.GAS_CAP_TAG not in set(pd.read_csv(tmp_path / "max_cap_requirements.csv")["MAX_CAP_PROGRAM"])
    assert gtc.GAS_CAP_TAG not in set(pd.read_csv(tmp_path / "max_cap_generators.csv")["MAX_CAP_PROGRAM"])
    g = pd.read_csv(tmp_path / "gas_turbine_cap_gens.csv").set_index("GENERATION_PROJECT")
    assert set(g.index) == {"cc_old", "ct_old", "cc_new"}                  # RICE and coal not covered
    cap = pd.read_csv(tmp_path / "gas_turbine_cap.csv").set_index("PERIOD")["gtc_max_mw"]
    assert cap[2030] == pytest.approx(900.0)                                 # floor: 900 predetermined > 600 + 2 x 100
    assert cap[2035] == pytest.approx(600 + 7 * 100)
    # options: CC at its turbine share, RICE covered, new additions per period
    s["build_rate"]["gas_turbine_cap"].update(cc_accounting="turbine_share", cc_turbine_share=0.6,
                                              coverage=["combined_cycle", "combustion_turbine", "reciprocating_engine"],
                                              form="new_additions_per_period")
    gtc.write_case_inputs(tmp_path, s)
    g = pd.read_csv(tmp_path / "gas_turbine_cap_gens.csv").set_index("GENERATION_PROJECT")
    assert g.at["cc_new", "gtc_weight"] == 0.6 and g.at["ct_old", "gtc_weight"] == 1.0 and "rice" in g.index
    cap = pd.read_csv(tmp_path / "gas_turbine_cap.csv").set_index("PERIOD")["gtc_max_mw"]
    assert cap[2030] == pytest.approx(200.0) and cap[2035] == pytest.approx(500.0)   # window sums, no floor
    assert pd.read_csv(tmp_path / "gas_turbine_cap_params.csv").at[0, "gtc_form"] == "new_additions_per_period"


GAS = ["N-NG_CC", "N-NG_GT", "N-NG_CC_CCS", "C-NG_CC", "C-NG_GT", "S-NG_CC", "S-NG_GT"]


def _old_tag(inp, caps):
    # shrink the existing gas fleet so the covered predetermined MW (the cap's floor) stays below the caps
    pre = pd.read_csv(inp / "gen_build_predetermined.csv").astype({"build_gen_predetermined": float})
    pre.loc[pre.GENERATION_PROJECT.str.contains("NG_"), "build_gen_predetermined"] = 0.5
    pre.to_csv(inp / "gen_build_predetermined.csv", index=False)
    gi = pd.read_csv(inp / "gen_info.csv")
    gens = [g for g in gi["GENERATION_PROJECT"] if g in GAS or "NG_" in g]
    pd.DataFrame({"MAX_CAP_PROGRAM": gtc.GAS_CAP_TAG, "PERIOD": list(caps), "max_cap_mw": list(caps.values())}).to_csv(
        inp / "max_cap_requirements.csv", index=False)
    pd.DataFrame({"MAX_CAP_PROGRAM": gtc.GAS_CAP_TAG, "MAX_CAP_GEN": gens}).to_csv(inp / "max_cap_generators.csv",
                                                                                  index=False)


def test_toy_same_solution_as_maxcaptag(tmp_path):
    """The build-rate turbine cap with the default form gives the same solution as the old MaxCapTag
    constraint (max_capacity_constraint.py) on the toy, with the cap binding."""
    free = toy_run(tmp_path, "free", ["max_capacity_constraint"], lambda inp: _old_tag(inp, {2020: 1e6, 2030: 1e6}))
    free = free / "outputs"
    b = build(free)
    gas_free = b[b.gen.str.contains("NG_")].groupby("build_year").mw.sum()
    # in-service gas (all vintages alive) is ~15 MW in 2020 and ~10 MW in 2030 unconstrained; cap it
    caps = {2020: 9.0, 2030: 7.0}

    def old(inp):
        _old_tag(inp, caps)

    def new(inp):
        _old_tag(inp, caps)
        # cap(2020) = 9 + 0 = 9; cap(2030) = 9 + 10 x (-0.2) = 7
        s = {"build_rate": {"gas_turbine_cap": {"enabled": True, "baseline_year": 2019, "baseline_mw": 9.0,
                                                "annual_additions_mw": {2020: 0.0, 2021: -0.2}}}}
        gtc.write_case_inputs(inp, s)
        assert not (inp / "max_cap_requirements.csv").exists() or \
            gtc.GAS_CAP_TAG not in set(pd.read_csv(inp / "max_cap_requirements.csv")["MAX_CAP_PROGRAM"])
    o = toy_run(tmp_path, "old", ["max_capacity_constraint"], old) / "outputs"
    n = toy_run(tmp_path, "new", ["max_capacity_constraint", "build_rate"], new) / "outputs"
    cap = pd.read_csv(n.parent / "inputs/gas_turbine_cap.csv").set_index("PERIOD")["gtc_max_mw"]
    assert cap[2020] == pytest.approx(9.0) and cap[2030] == pytest.approx(7.0)
    res = pd.read_csv(n / "gas_turbine_cap_results.csv").set_index("period")
    assert res.at[2030, "covered_mw"] == pytest.approx(7.0, abs=1e-6)         # binds
    assert res.at[2020, "covered_mw"] == pytest.approx(9.0, abs=1e-6)
    assert total_cost(o) == pytest.approx(total_cost(n), rel=1e-7)
    assert total_cost(n) > total_cost(free)
    bo, bn = build(o), build(n)
    m = bo.merge(bn, on=["gen", "build_year"], suffixes=("_old", "_new"))
    assert (m.mw_old - m.mw_new).abs().max() < 1e-4
    assert gas_free.sum() > 0
