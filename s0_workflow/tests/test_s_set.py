"""S-set scenario rows (CHANGES §75): settings by case and period, the new per-period switches, unconstrained
transmission, credit spend by vintage. Small hand-built inputs: fixtures, not results."""
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s0_workflow import chain_reuse as cr  # noqa: E402
from s0_workflow import credit_spend as cs  # noqa: E402
from s0_workflow import production as s0prod  # noqa: E402
from s0_workflow import tx_policy  # noqa: E402

SI = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv", dtype=str, keep_default_na=False)
SM = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]
BASE = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
YEARS = [2028, 2030, 2035, 2040, 2045]
CHAINS = ["S1", "S2", "S4", "L", "P", "S3", "S5"]


def resolved(case, year):
    """(s0_production after the row's axis values, levels for the year, gas-turbine path, credits value)."""
    row = SI[(SI.case_id == case) & (SI.year == str(year))].iloc[0]
    s = {"s0_production": BASE}
    for col in SI.columns:
        if col in ("case_id", "year"):
            continue
        for sec in (SM["all_years"], SM.get(year, {})):
            v = (sec.get(col) or {}).get(row[col]) if isinstance(sec.get(col), dict) else None
            if v and "s0_production" in v:
                s = s0prod.deep_merge(s, {"s0_production": v["s0_production"]})
    s0 = s["s0_production"]
    lv = {"build_rate": dict(s0["settings"]["build_rate"]),
          "interconnection_headroom": dict(s0["settings"]["interconnection_headroom"])}
    s0prod.apply_levels_by_period(lv, s0, case, year)
    br = lv["build_rate"]["level"] if lv["build_rate"].get("enabled") else "off"
    hr = lv["interconnection_headroom"]["scenario"] if lv["interconnection_headroom"].get("enabled") else "off"
    return s0, br, hr, str(s0prod.gas_turbine_path(s0, year)), row["tax_credits"]


EXPECT = {   # case: (build rate, headroom, gas turbine, tx mode, credits), each a list over 2028-2045
    "S1": (["central"] * 5, ["atts_s0"] * 5, ["central"] * 5, "national_cap", "reinstated"),
    "S2": (["high"] * 5, ["atts_planned"] * 5, ["central"] * 5, "national_cap", "reinstated"),
    "S4": (["high"] * 5, ["atts_planned"] * 5, ["central"] * 5, "national_cap", "current"),
    "L": (["high_reform"] * 5, ["atts_reform_techmax"] * 5, ["high"] * 5, "national_cap", "reinstated"),
    "P": (["high", "high", "high_reform", "off", "off"],
          ["atts_planned", "atts_planned", "atts_reform", "atts_reform_techmax", "atts_reform_techmax"],
          ["central", "central", "high", "off", "off"], "national_cap", "reinstated"),
    "S3": (["off"] * 5, ["atts_reform_techmax"] * 5, ["off"] * 5, "unconstrained", "reinstated"),
    "S5": (["off"] * 5, ["atts_reform_techmax"] * 5, ["off"] * 5, "unconstrained", "current"),
}


@pytest.mark.parametrize("case", CHAINS)
def test_s_set_settings_by_period(case):
    br, hr, gt, mode, cred = EXPECT[case]
    got = [resolved(case, y) for y in YEARS]
    assert [g[1] for g in got] == br
    assert [g[2] for g in got] == hr
    assert [g[3] for g in got] == gt
    for s0, *_ in got:
        t = tx_policy.tx_settings(s0)
        assert t["mode"] == mode and t["moratorium_first_period"] == 2040 and s0["forced_tx"] == "reeds_certain_plus_A"
        assert tx_policy.needs_module(s0) == (mode == "national_cap")
    want = ["no_wind_solar"] + ["full_ira"] * 4 if cred == "reinstated" else ["no_wind_solar"] * 5
    assert [g[4] for g in got] == want
    # the single-year 2035 version: the chain's 2035 settings; reinstated credits in 2035
    one = resolved(f"s4x1_{case}_2035", 2035)
    assert one[1:4] == got[2][1:4]
    assert one[4] == ("full_ira" if cred == "reinstated" else "no_wind_solar")


def test_rows_are_s0_plus_the_s_set_settings_only():
    """Each chain row = S0prod_A's row except case_id, s_set and tax_credits; each 2035 row = s4x1_S0prod_2035_new's;
    every other row has s_set none (sets nothing), so S0 and the legacy regression case resolve as before."""
    a = SI[SI.case_id == "S0prod_A"].set_index("year")
    n = SI[SI.case_id == "s4x1_S0prod_2035_new"].iloc[0]
    for c in CHAINS:
        x = SI[SI.case_id == c].set_index("year")
        assert list(x.index) == [str(y) for y in YEARS]
        diff = {k for k in SI.columns if k != "year" and (x[k] != a[k]).any()}
        assert diff <= {"case_id", "s_set", "tax_credits"}, (c, diff)
        y = SI[SI.case_id == f"s4x1_{c}_2035"].iloc[0]
        assert {k for k in SI.columns if y[k] != n[k]} <= {"case_id", "s_set", "tax_credits"}
    rest = SI[~SI.case_id.isin(CHAINS + [f"s4x1_{c}_2035" for c in CHAINS])]
    assert (rest.s_set == "none").all() and SM["all_years"]["s_set"]["none"] is None
    # S0: no overrides, the allowance path and levels of s0_production.yml in every period
    for y in YEARS:
        s0, br, hr, gt, cred = resolved("S0prod_A", y)
        assert (br, hr, gt, cred) == ("central", "atts_s0", "central", "no_wind_solar")
        assert "level_overrides" not in s0 or not s0["level_overrides"]


def test_gas_turbine_off_drops_every_turbine_limit(tmp_path):
    """gas_turbine_cap off: no cap files and the old MaxCapTag_GasTurbineSupply rows removed."""
    from build_rate.brc import turbine_cap
    pd.DataFrame({"MAX_CAP_PROGRAM": ["MaxCapTag_GasTurbineSupply", "Other"], "PERIOD": [2040, 2040],
                  "max_cap_mw": [1.0, 2.0]}).to_csv(tmp_path / "max_cap_requirements.csv", index=False)
    pd.DataFrame({"MAX_CAP_PROGRAM": ["MaxCapTag_GasTurbineSupply", "Other"], "MAX_CAP_GEN": ["ct", "w"]}).to_csv(
        tmp_path / "max_cap_generators.csv", index=False)
    s = {"build_rate": {"gas_turbine_cap": {"enabled": False, "off": True}}}
    assert turbine_cap.write_case_inputs(tmp_path, s) == []
    assert list(pd.read_csv(tmp_path / "max_cap_requirements.csv").MAX_CAP_PROGRAM) == ["Other"]
    assert list(pd.read_csv(tmp_path / "max_cap_generators.csv").MAX_CAP_PROGRAM) == ["Other"]
    assert not (tmp_path / "gas_turbine_cap.csv").exists()
    s0 = {"gas_turbine_cap": {"form": "allowance", "path": "central"},
          "levels_by_period": {"gas_turbine_cap": {2028: "central", 2035: "high", 2040: "off"}}}
    assert [s0prod.gas_turbine_path(s0, y) for y in YEARS] == ["central", "central", "high", "off", "off"]
    assert s0prod.gas_turbine_path({"gas_turbine_cap": {"path": "low"}}, 2035) == "low"


def test_unconstrained_transmission(tmp_path):
    """mode unconstrained: interregional lines may be built from the first period (no moratorium rows), no national
    cap files, and forced lines keep their minimum and forced-period cap exactly as under national_cap."""
    from test_tx_policy import SETTINGS, _stage
    chain = [2028, 2030, 2035, 2040, 2045]
    out = {}
    for name, tp in (("cap", SETTINGS["s0_tx"]), ("free", dict(SETTINGS["s0_tx"], mode="unconstrained"))):
        (tmp_path / name).mkdir()
        d = _stage(tmp_path / name, [2028, 2030], chain)
        tx_policy.write_case_inputs(d, {"tx_policy": tp}, {2028: {"forced_tx_expansion_limit": "minimum"}},
                                    lambda x: None)
        out[name] = d
    lim = {k: pd.read_csv(v / "trans_path_expansion_limit.csv") for k, v in out.items()}
    forced = set(pd.read_csv(out["cap"] / "tx_cap_exempt.csv").itertuples(index=False, name=None))
    assert len(lim["cap"]) > len(forced)                                     # moratorium rows under national_cap
    free = set(zip(lim["free"].TRANSMISSION_LINE.astype(str), lim["free"].PERIOD))
    assert free == {(str(a), int(b)) for a, b in forced}                      # only forced line-periods remain
    pd.testing.assert_frame_equal(
        lim["free"].sort_values(["TRANSMISSION_LINE", "PERIOD"]).reset_index(drop=True),
        lim["cap"][[(str(a), int(b)) in free for a, b in zip(lim["cap"].TRANSMISSION_LINE, lim["cap"].PERIOD)]]
        .sort_values(["TRANSMISSION_LINE", "PERIOD"]).reset_index(drop=True))
    assert not (out["free"] / "tx_cap_periods.csv").exists() and (out["cap"] / "tx_cap_periods.csv").exists()
    pd.testing.assert_frame_equal(pd.read_csv(out["free"] / "transmission_lines.csv"),
                                  pd.read_csv(out["cap"] / "transmission_lines.csv"))


def _stage_dirs(tmp, name, period, credits, builds, energy, model_credit, base_pre, last=False):
    i, o = tmp / "in" / str(period) / name, tmp / "out" / str(period) / name
    i.mkdir(parents=True)
    o.mkdir(parents=True)
    pd.DataFrame({"INVESTMENT_PERIOD": [period], "period_start": [period - 1], "period_end": [period]}).to_csv(
        i / "periods.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["w_new", "w_old", "nuc"],
                  "gen_tech": ["LandbasedWind_Class3", "Onshore Wind Turbine", "Nuclear_Nuclear"],
                  "gen_max_age": [30, 30, 60]}).to_csv(i / "gen_info.csv", index=False)
    pd.DataFrame(base_pre, columns=["GENERATION_PROJECT", "build_year", "build_gen_predetermined"]).to_csv(
        i / "gen_build_predetermined.csv", index=False)
    if credits:
        pd.DataFrame([(g, period, v) for g, v in credits.items()],
                     columns=["GENERATION_PROJECT", "PERIOD", "gen_ptc_value_per_mwh"]).to_csv(
            i / "gen_tax_credits.csv", index=False)
    pd.DataFrame(builds, columns=["GEN_BLD_YRS_1", "GEN_BLD_YRS_2", "BuildGen"]).to_csv(o / "BuildGen.csv", index=False)
    pd.DataFrame([(g, period, e) for g, e in energy.items()],
                 columns=["generation_project", "period", "Energy_GWh_typical_yr"]).to_csv(
        o / "dispatch_gen_annual_summary.csv", index=False)
    pd.DataFrame([(g, period, v) for g, v in model_credit.items()],
                 columns=["GENERATION_PROJECT", "PERIOD", "tax_credit_value_dollars"]).to_csv(
        o / "tax_credit_value.csv", index=False)
    return {"inputs": i, "outputs": o}


def test_credit_spend_by_vintage(tmp_path):
    """Reinstated chain: the 2028 stage has no wind credit (nuclear yes); 2030 credits wind. 860M-pipeline wind (w_old,
    in service 2026) earns the post-hoc credit; the 2028 wind build is paid but was not optimised on (post-hoc in 2028,
    the model's credit share in 2030); the 2030 build and nuclear were optimised on."""
    base = [("w_old", 2026, 100.0)]
    s28 = _stage_dirs(tmp_path, "R", 2028, {"nuc": 15.0}, [("w_old", 2026, 100.0), ("w_new", 2028, 50.0),
                                                           ("nuc", 2028, 10.0)],
                      {"w_old": 300.0, "w_new": 150.0, "nuc": 80.0}, {"nuc": 15.0 * 80e3}, base)
    s30 = _stage_dirs(tmp_path, "R", 2030, {"w_new": 27.5, "nuc": 15.0},
                      [("w_old", 2026, 100.0), ("w_new", 2028, 50.0), ("w_new", 2030, 150.0), ("nuc", 2028, 10.0)],
                      {"w_old": 300.0, "w_new": 600.0, "nuc": 80.0}, {"w_new": 27.5 * 600e3, "nuc": 15.0 * 80e3}, base)
    cfg = dict(cs.yaml.safe_load(open(cs.CONFIG)), value_per_mwh=27.5)      # the fixture's numbers use 27.5
    long = cs.spend([s28, s30], cfg, case="R")
    t = long.groupby(["period", "category"]).dollars_per_yr.sum()
    assert t[(2028, "existing_pipeline")] == pytest.approx(300e3 * 27.5)
    assert t[(2028, "paid_not_optimised")] == pytest.approx(150e3 * 27.5)           # post-hoc, reinstated 2028
    assert t[(2028, "optimised_on")] == pytest.approx(15.0 * 80e3)                  # nuclear
    assert t[(2030, "paid_not_optimised")] == pytest.approx(27.5 * 600e3 * 50 / 200)  # 2028 vintage's share
    assert t[(2030, "optimised_on")] == pytest.approx(27.5 * 600e3 * 150 / 200 + 15.0 * 80e3)
    s = cs.summary(long).set_index("period")
    assert s.at[2030, "total"] == pytest.approx(round((300e3 * 27.5 + 27.5 * 600e3 + 15 * 80e3) / 1e6, 1), abs=0.11)
    # current-law chain: no wind credit in any stage -> 2028 builds get nothing; the pipeline plant still earns
    c28 = _stage_dirs(tmp_path, "C", 2028, {"nuc": 15.0}, [("w_old", 2026, 100.0), ("w_new", 2028, 50.0)],
                      {"w_old": 300.0, "w_new": 150.0}, {}, base)
    lc = cs.spend([c28], cfg, case="C")
    assert set(lc.category) == {"existing_pipeline"}
    assert lc.dollars_per_yr.sum() == pytest.approx(300e3 * 27.5)


def test_expected_reuse_of_the_s_set():
    """S1 (and BILL_central_S1) reuse 2028 from S0prod_A; S2, S4, L, P, S3, S5 differ in 2028 (build rate)."""
    df = cr.expected_reuse(REPO / "pg/extra_inputs/scenario_inputs.csv", REPO / "pg/settings/scenario_management.yml")
    got = df[df.expected_reuse].groupby("case").stage.apply(list).to_dict()
    assert got.get("S1") == [2028] and got.get("BILL_central_S1") == [2028]
    for c in ("S2", "S4", "L", "P", "S3", "S5"):
        assert c not in got, c
    assert "build_rate" in df[(df.case == "S2") & (df.stage == 2028)].differences.iat[0]
    assert "tax_credit" in df[(df.case == "S1") & (df.stage == 2030)].differences.iat[0]


def test_bill_central_siting_sensitivity_row():
    """§76: BILL_central_siting = BILL_central with tx_sens br_reform_siting: the build rate switches to reform_bp_siting
    (reform_bp + the old wind siting relief) at 2035; S0 before, so 2028 and 2030 are reusable from S0prod_A."""
    a = SI[SI.case_id == "BILL_central"].set_index("year")
    b = SI[SI.case_id == "BILL_central_siting"].set_index("year")
    assert {k for k in SI.columns if k != "year" and (a[k] != b[k]).any()} == {"case_id", "tx_sens"}
    assert (b.tx_sens == "br_reform_siting").all()
    lv = [resolved("BILL_central_siting", y)[1] for y in YEARS]
    assert lv == ["central", "central"] + ["reform_bp_siting"] * 3
    assert [resolved("BILL_central", y)[1] for y in YEARS] == ["central", "central"] + ["reform_bp"] * 3
    df = cr.expected_reuse(REPO / "pg/extra_inputs/scenario_inputs.csv", REPO / "pg/settings/scenario_management.yml",
                           cases=["BILL_central_siting"])
    assert list(df[df.expected_reuse].stage) == [2028, 2030]


def test_credit_spend_sourced_values():
    """§76: sourced values in credit_spend.yaml: 30 $/MWh (2024$, post-2021 facilities), 29 $/MWh before 2022, solar
    PTC share 0.40, current-law in-service horizon 2030 (OBBBA + Notice 2025-42 continuity safe harbour)."""
    cfg = yaml.safe_load(open(cs.CONFIG))
    assert (cfg["value_per_mwh"], cfg["pre2022_value_per_mwh"], cfg["ptc_years"]) == (30.0, 29.0, 10)
    assert cfg["existing_pipeline"]["solar"] == {"first_in_service": 2022, "ptc_share": 0.40}
    assert cfg["existing_pipeline"]["current_last_in_service"] == 2030


def test_credit_spend_pre2022_and_solar_share(tmp_path):
    """Existing wind in service 2020 earns the pre-2022 value; a 2010 plant is past its 10-year term; existing solar in
    service 2023 earns value x the PTC share; a 2031 pipeline unit is outside current law's 2030 horizon."""
    s = _stage_dirs(tmp_path, "C", 2028, {"nuc": 15.0},
                    [("w_old", 2010, 10.0), ("w_old", 2020, 30.0), ("nuc", 2023, 50.0)], {"w_old": 400.0, "nuc": 100.0},
                    {}, [("w_old", 2010, 10.0), ("w_old", 2020, 30.0), ("nuc", 2023, 50.0)])
    gi = pd.read_csv(s["inputs"] / "gen_info.csv")
    gi.loc[gi.GENERATION_PROJECT == "nuc", "gen_tech"] = "Solar Photovoltaic"
    gi.to_csv(s["inputs"] / "gen_info.csv", index=False)
    long = cs.spend([s], case="C").set_index("vintage")
    assert 2010 not in long.index
    assert long.loc[2020, "dollars_per_yr"] == pytest.approx(400e3 * 30 / 40 * 29.0)
    assert long.loc[2023, "dollars_per_yr"] == pytest.approx(100e3 * 30.0 * 0.40)
