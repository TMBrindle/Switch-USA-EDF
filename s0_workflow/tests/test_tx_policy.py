"""S0 transmission policy (CHANGES §60): national cap on discretionary transmission (study_modules.tx_build_cap),
interregional moratorium and the case-build step (s0_workflow/tx_policy.py), sensitivity hooks.
Small hand-built inputs: fixtures, not results."""
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s0_workflow import prm, tx_policy  # noqa: E402

MWKM = 1.609344e6


def toy(tmp_path, name, minimum, cap_rate, exempt=(), no_bill=(), no_bill_rate=".", classes=None, floor=None):
    """3-zone toy (periods 2020, 2030). minimum: {(line, period): MW}; cap_rate: {period: TW-mi/yr}."""
    from toyutil import toy_inputs

    def edit(inp):
        pd.DataFrame([{"TRANSMISSION_LINE": ln, "PERIOD": p, "trans_build_minimum_mw": v} for (ln, p), v in minimum.items()],
                     columns=["TRANSMISSION_LINE", "PERIOD", "trans_build_minimum_mw"]).to_csv(
            inp / "trans_build_minimum.csv", index=False)
        pd.DataFrame({"PERIOD": [2020, 2030], "tx_cap_tw_mi_per_yr": [cap_rate.get(2020, "."), cap_rate.get(2030, ".")],
                      "tx_cap_no_bill_tw_mi_per_yr": [no_bill_rate, "."]}).to_csv(inp / "tx_cap_periods.csv", index=False)
        cl = classes or {"N-C": "inter", "C-S": "intra"}
        pd.DataFrame({"TRANSMISSION_LINE": list(cl), "tx_cap_class": list(cl.values()),
                      "tx_cap_no_bill": [int(ln in no_bill) for ln in cl]}).to_csv(inp / "tx_cap_lines.csv", index=False)
        pd.DataFrame(list(exempt), columns=["TRANSMISSION_LINE", "PERIOD"]).to_csv(inp / "tx_cap_exempt.csv", index=False)
        if floor:
            pd.DataFrame([{"TX_FLOOR": "NC", "PERIOD": p, "tx_floor_mw": v} for p, v in floor.items()]).to_csv(
                inp / "tx_floor.csv", index=False)
            pd.DataFrame({"TX_FLOOR": ["NC"], "TRANSMISSION_LINE": ["N-C"]}).to_csv(inp / "tx_floor_lines.csv", index=False)
    return toy_inputs(tmp_path, name, ["trans_build_minimum", "tx_build_cap"], edit)


def solve(run):
    r = subprocess.run(["switch", "solve", "--solver", "appsi_highs", "--suffixes", "dual",
                        "--include-module", "mods.trans_build_minimum", "--include-module", "mods.tx_build_cap"],
                       cwd=run, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(run)})
    return r.returncode == 0, run / "outputs", r.stdout + r.stderr


def years(run):
    per = pd.read_csv(run / "inputs/periods.csv").set_index("INVESTMENT_PERIOD")
    return per


def test_toy_national_cap_exempt_and_no_bill(tmp_path):
    # C-S (intra, 200 km) must build 5 MW in 2020: 1,000 MW-km. N-C (inter, 100 km) 4 MW forced and exempt.
    mins = {("C-S", 2020): 5.0, ("N-C", 2020): 4.0}
    run = toy(tmp_path, "probe", mins, {})
    ok, out, msg = solve(run)
    assert ok, msg
    yrs = pd.read_csv(out / "tx_build_cap.csv").set_index("PERIOD").at[2020, "period_years"]
    rate = 1000.0 / (MWKM * yrs)                                       # cap = exactly 1,000 MW-km in 2020
    run = toy(tmp_path, "cap", mins, {2020: rate}, exempt=[("N-C", 2020)])
    ok, out, msg = solve(run)
    assert ok, msg
    r = pd.read_csv(out / "tx_build_cap.csv").set_index("PERIOD")
    assert r.at[2020, "intra_mw_km"] == pytest.approx(1000.0, rel=1e-6) and r.at[2020, "inter_mw_km"] == pytest.approx(0)
    assert r.at[2020, "exempt_forced_mw_km"] == pytest.approx(400.0, rel=1e-6)
    assert r.at[2020, "cap_mw_km"] == pytest.approx(1000.0, rel=1e-9)
    assert r.at[2020, "total_tw_mi_per_yr"] == pytest.approx(rate, rel=1e-6)
    assert pd.isna(r.at[2030, "cap_tw_mi_per_yr"])                    # blank = no cap
    # a cap just below what C-S must build: infeasible; forced lines don't count (N-C exempt)
    ok, _, _ = solve(toy(tmp_path, "tight", mins, {2020: rate * 0.999}, exempt=[("N-C", 2020)]))
    assert not ok
    # N-C not exempt: it would count (1,400 MW-km > 1,000): infeasible
    ok, _, _ = solve(toy(tmp_path, "noexempt", mins, {2020: rate}))
    assert not ok
    # no-bill cap of 0: a no-bill line can't build discretionary MW; an exempt (forced) one still can
    ok, _, _ = solve(toy(tmp_path, "nb_cs", mins, {}, exempt=[("N-C", 2020)], no_bill=["C-S"], no_bill_rate=0.0))
    assert not ok
    ok, out, msg = solve(toy(tmp_path, "nb_nc", mins, {}, exempt=[("N-C", 2020)], no_bill=["N-C"], no_bill_rate=0.0))
    assert ok, msg
    assert pd.read_csv(out / "tx_build_cap.csv").set_index("PERIOD").at[2020, "no_bill_mw_km"] == pytest.approx(0)


def test_toy_transfer_floor(tmp_path):
    # N-C exists at 3 MW; a floor of 7 MW in 2030 makes 4 MW be built by 2030
    ok, out, msg = solve(toy(tmp_path, "floor", {}, {}, floor={2030: 7.0}))
    assert ok, msg
    f = pd.read_csv(out / "tx_floor.csv").set_index("PERIOD")
    assert f.at[2030, "transfer_mw"] >= 7.0 - 1e-6
    b = pd.read_csv(out / "BuildTx.csv")
    assert b[b.TRANS_BLD_YRS_1 == "N-C"].BuildTx.sum() == pytest.approx(4.0, abs=1e-5)


def _stage(tmp_path, years, chain, **kw):
    """A stage's transmission files from pg_to_switch.transmission_tables (test_forced_tx harness), with periods.csv."""
    from test_forced_tx import build
    (tmp_path / "b").mkdir(exist_ok=True)
    out = build(tmp_path / "b", years=years, source=REPO / "pg_to_switch.py", _chain_years=chain,
                forced_tx_table="pg/extra_inputs/transmission/forced_tx_reeds_certain_2026.09.21.csv",
                forced_tx_expansion_limit="minimum", trans_expansion_policy="zero", **kw)
    pd.DataFrame({"INVESTMENT_PERIOD": years}).to_csv(out / "periods.csv", index=False)
    return out


def _names(out):
    tl = pd.read_csv(out / "transmission_lines.csv")
    return {r.TRANSMISSION_LINE: "-".join(sorted([r.trans_lz1, r.trans_lz2], key=lambda z: int(z[1:])))
            for r in tl.itertuples()}


SETTINGS = {
    "s0_tx": {"mode": "national_cap", "moratorium_first_period": 2040, "cap_tw_mi_per_yr": {2028: 0.0, 2030: 1.4}},
    "bill_central": {"mode": "national_cap", "moratorium_first_period": 2035,
                     "cap_tw_mi_per_yr": {2028: 0.0, 2030: 1.4, 2035: 3.0}},
}


def test_case_build_moratorium_and_cap(tmp_path):
    chain = [2028, 2030, 2035, 2040, 2045]
    before = _stage(tmp_path, [2035], chain)
    tl0 = pd.read_csv(before / "transmission_lines.csv")
    for name, tp in SETTINGS.items():
        out = _stage(tmp_path, [2035], chain)
        logs = []
        tx_policy.write_case_inputs(out, {"tx_policy": tp}, {2035: {"forced_tx_expansion_limit": "minimum"}}, logs.append)
        names = _names(out)
        cls = pd.read_csv(out / "tx_cap_lines.csv").set_index("TRANSMISSION_LINE")
        cls.index = [names[x] for x in cls.index]
        assert cls.at["p24-p25", "tx_cap_class"] == "inter" and cls.at["p28-p31", "tx_cap_class"] == "intra"
        assert cls.at["p60-p61", "tx_cap_class"] == "intra" and cls.at["p60-p61", "tx_cap_no_bill"] == 1   # ERCOT-internal
        ercot_ties = [k for k in cls.index if cls.at[k, "tx_cap_no_bill"] == 1 and cls.at[k, "tx_cap_class"] == "inter"]
        assert ercot_ties                                                             # ERCOT ties exist
        tl = pd.read_csv(out / "transmission_lines.csv")
        inter = set(cls.index[cls.tx_cap_class == "inter"])
        assert (tl[[names[x] in inter for x in tl.TRANSMISSION_LINE]].trans_new_build_allowed == 1).all()
        assert (tl0.trans_new_build_allowed == 0).sum() > 0                             # the constrained policy blocked some
        lim = pd.read_csv(out / "trans_path_expansion_limit.csv")
        L = {(names[r.TRANSMISSION_LINE], r.PERIOD): r.trans_path_expansion_limit_mw for r in lim.itertuples()}
        assert L[("p24-p25", 2035)] == 3000.0                     # TransWest forced in 2035: capped at its minimum
        assert not any(k[0] not in inter for k in L if k != ("p24-p25", 2035))         # no per-line limit intra-region
        zero_inter = {k[0] for k, v in L.items() if v == 0}
        if name == "s0_tx":                                       # moratorium to 2040: every interregional line 0 in 2035
            assert zero_inter == inter - {"p24-p25"}
        else:                                                     # bill from 2035: only the no-bill (ERCOT) ties
            assert zero_inter == set(ercot_ties)
        per = pd.read_csv(out / "tx_cap_periods.csv").set_index("PERIOD")
        assert per.at[2035, "tx_cap_tw_mi_per_yr"] == (1.4 if name == "s0_tx" else 3.0)
        assert per.at[2035, "tx_cap_no_bill_tw_mi_per_yr"] == 1.4
        ex = pd.read_csv(out / "tx_cap_exempt.csv")
        assert [(names[a], b) for a, b in zip(ex.TRANSMISSION_LINE, ex.PERIOD)] == [("p24-p25", 2035)]
        assert any("national_cap" in x for x in logs)


def test_step_values_and_settings():
    t = {2028: 1.4, 2030: 2.0, 2035: 4.0}
    assert [tx_policy.step_value(t, y) for y in (2026, 2028, 2030, 2034, 2035, 2045)] == [1.4, 1.4, 2.0, 2.0, 4.0, 4.0]
    assert tx_policy.tx_settings({})["mode"] == "legacy" and not tx_policy.active({})
    assert tx_policy.tx_settings({"tx_policy": {"no_bill": {"moratorium_first_period": 2045}}})["no_bill"] == {
        "regions": ["ERCOT"], "moratorium_first_period": 2045, "cap_tw_mi_per_yr": {2028: 0.0, 2030: 1.4}}
    with pytest.raises(ValueError, match="mode"):
        tx_policy.tx_settings({"tx_policy": {"mode": "zero"}})
    assert tx_policy.MODES == ("legacy", "national_cap", "unconstrained")


def test_capex_multiplier_and_transfer_floor(tmp_path):
    out = _stage(tmp_path, [2035], [2035])
    tp0 = pd.read_csv(out / "trans_params.csv").trans_capital_cost_per_mw_km.iloc[0]
    tx_policy.write_case_inputs(out, {"tx_policy": {"capex_multiplier": 1.5}}, {2035: {}}, lambda x: None)
    assert pd.read_csv(out / "trans_params.csv").trans_capital_cost_per_mw_km.iloc[0] == pytest.approx(tp0 * 1.5)
    assert not (out / "tx_cap_periods.csv").exists()                                   # legacy mode: no cap files
    # the placeholder floor file has no rows: the build stops
    ph = "pg/extra_inputs/transmission/tx_transfer_floor_placeholder.csv"
    assert list(pd.read_csv(REPO / ph).columns) == tx_policy.FLOOR_COLUMNS
    with pytest.raises(ValueError, match="placeholder"):
        tx_policy.write_case_inputs(out, {"tx_policy": {"transfer_floor": ph}}, {2035: {}}, lambda x: None)
    f = tmp_path / "floor.csv"
    pd.DataFrame([{"region_a": "PJM", "region_b": "MISO", "PERIOD": 2035, "min_transfer_mw": 12000}]).to_csv(f, index=False)
    tx_policy.write_case_inputs(out, {"tx_policy": {"transfer_floor": str(f)}}, {2035: {}}, lambda x: None)
    fl = pd.read_csv(out / "tx_floor.csv")
    assert fl.to_dict("records") == [{"TX_FLOOR": "MISO-PJM", "PERIOD": 2035, "tx_floor_mw": 12000.0}]
    assert len(pd.read_csv(out / "tx_floor_lines.csv")) > 0


def test_forced_tx_plus_status_review_classes(tmp_path):
    from s0_workflow import production as s0prod
    from test_forced_tx import build
    # the committed list has the agreed columns and passes the checks (class A and B rows)
    ph = pd.read_csv(REPO / s0prod.STATUS_REVIEW_TABLE)
    assert list(ph.columns) == s0prod.STATUS_REVIEW_COLUMNS and {"A", "B"} <= set(ph.status_class)
    for opt in ("reeds_certain_plus_A", "reeds_certain_plus_AB"):
        s0prod.apply_forced_tx({}, {"forced_tx": opt})
    # an empty list (the placeholder before 2eb325d) stops the build
    empty = tmp_path / "empty.csv"
    pd.DataFrame(columns=s0prod.STATUS_REVIEW_COLUMNS).to_csv(empty, index=False)
    for opt in ("reeds_certain_plus_A", "reeds_certain_plus_AB"):
        with pytest.raises(ValueError, match="placeholder"):
            s0prod.apply_forced_tx({}, {"forced_tx": opt, "forced_tx_status_review": str(empty)})
    f = tmp_path / "status.csv"
    rows = [{"from_zone": "p1", "to_zone": "p2", "project_name": "projA", "status_class": "A", "new_cap_mw": 1500.0,
             "mw_basis": "transfer_capability", "new_cap_year": 2029, "trans_length_km": 120.0, "trans_efficiency": 0.98,
             "source": "test", "notes": ""},
            {"from_zone": "p3", "to_zone": "p4", "project_name": "projB", "status_class": "B", "new_cap_mw": 900.0,
             "mw_basis": "transfer_capability", "new_cap_year": 2033, "trans_length_km": 80.0, "trans_efficiency": 0.99,
             "source": "test", "notes": ""}]
    pd.DataFrame(rows, columns=s0prod.STATUS_REVIEW_COLUMNS).to_csv(f, index=False)
    s = {}
    s0prod.apply_forced_tx(s, {"forced_tx": "reeds_certain_plus_A", "forced_tx_status_review": str(f),
                               "forced_tx_expansion_limit": "minimum"})
    assert s["forced_tx_table"] == ["pg/extra_inputs/transmission/forced_tx_reeds_certain_2026.09.21.csv", str(f)]
    assert s["forced_tx_status_classes"] == ["A"]
    (tmp_path / "b").mkdir()
    for classes, want in ((["A"], {("p28-p31", 2028), ("p1-p2", 2030), ("p24-p25", 2035)}),
                          (["A", "B"], {("p28-p31", 2028), ("p1-p2", 2030), ("p24-p25", 2035), ("p3-p4", 2035)})):
        out = build(tmp_path / "b", years=[2028, 2030, 2035], source=REPO / "pg_to_switch.py",
                    forced_tx_table=s["forced_tx_table"], forced_tx_status_classes=classes,
                    forced_tx_expansion_limit="minimum")
        names = _names(out)
        bm = pd.read_csv(out / "trans_build_minimum.csv")
        assert {(names[a], b) for a, b in zip(bm.TRANSMISSION_LINE, bm.PERIOD)} == want, classes
    # a project not on the model's MW basis stops the build
    pd.DataFrame([dict(rows[0], mw_basis="nameplate")], columns=s0prod.STATUS_REVIEW_COLUMNS).to_csv(f, index=False)
    with pytest.raises(ValueError, match="transfer_capability"):
        s0prod.apply_forced_tx({}, {"forced_tx": "reeds_certain_plus_A", "forced_tx_status_review": str(f)})


def _merged(*axis_values):
    import yaml
    from s0_workflow import production as s0prod
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    ax = yaml.safe_load(open(REPO / "pg/settings/scenario_management.yml"))["settings_management"]["all_years"]
    s = {"s0_production": dict(s0, enabled=True)}
    for axis, val in axis_values:
        if ax[axis][val]:
            s = s0prod.deep_merge(s, ax[axis][val])
    return s["s0_production"], ax


EXPECT = {   # tx_bill value: (moratorium, cap by period 2028-2045, headroom by period, build rate by period,
             # import allowance by period); §72: S0 (0 / 1.4, atts_s0, central, 0) until each change takes effect
    "s0_tx": (2040, [0.0, 1.4, 1.4, 1.4, 1.4], None, None, [0.0] * 5),
    "bill_central": (2035, [0.0, 1.4, 3.0, 3.0, 3.0], ["atts_s0"] * 2 + ["atts_reform"] * 3,
                     ["central"] * 2 + ["reform_bp"] * 3, [0.0] * 2 + [0.85] * 3),
    "bill_low": (2040, [0.0, 1.4, 2.0, 2.0, 2.0], ["atts_s0"] * 3 + ["atts_reform"] * 2,
                 ["central"] * 3 + ["reform_bp"] * 2, [0.0] * 3 + [0.85] * 2),
    "bill_high": (2035, [0.0, 2.0, 4.0, 4.0, 4.0], ["atts_s0", "atts_reform"] + ["atts_reform_techmax"] * 3,
                  ["central"] + ["reform_bp"] * 4, [0.0] * 2 + [0.85] * 3),
    "bill_central_txonly": (2035, [0.0, 1.4, 3.0, 3.0, 3.0], None, None, [0.0] * 2 + [0.85] * 3),
    "bill_central_bronly": (2040, [0.0, 1.4, 1.4, 1.4, 1.4], ["atts_s0"] * 2 + ["atts_reform"] * 3,
                            ["central"] * 2 + ["reform_bp"] * 3, [0.0] * 5),
}


def test_bill_axis_values():
    from s0_workflow import production as s0prod
    years = [2028, 2030, 2035, 2040, 2045]
    for val, (mor, cap, hr, br, allow) in EXPECT.items():
        s0, ax = _merged(("tx_bill", val))
        t = tx_policy.tx_settings(s0)
        assert t["mode"] == "national_cap" and t["moratorium_first_period"] == mor, val
        assert [tx_policy.step_value(t["cap_tw_mi_per_yr"], y) for y in years] == cap, val
        assert t["no_bill"] == {"regions": ["ERCOT"], "moratorium_first_period": 2040,
                                "cap_tw_mi_per_yr": {2028: 0.0, 2030: 1.4}}, val          # ERCOT: no-bill values
        assert [s0prod.level_for(s0, "interconnection_headroom", y) for y in years] == (hr or [None] * 5), val
        assert [s0prod.level_for(s0, "build_rate", y) for y in years] == (br or [None] * 5), val
        assert [prm.import_allowance(s0["prm"], [y]) for y in years] == allow, val
        assert "--include-module study_modules.tx_build_cap" in s0prod.scenario_options({"s0_production": s0})
    # legacy (every older row, the regression case): nothing changes
    s0, ax = _merged(("tx_bill", "legacy"), ("tx_sens", "none"))
    assert not tx_policy.active(s0) and s0["levels_by_period"] == {"interconnection_headroom": {}, "build_rate": {}}
    assert "tx_build_cap" not in s0prod.scenario_options({"s0_production": s0})
    assert all(prm.import_allowance(s0["prm"], [y]) == 0.0 for y in years)          # §82: a table, 0 in every period
    # sensitivity hooks (forced A + B is the forced_tx column's value since §81: one column per key)
    assert "forced_ab" not in ax["tx_sens"] and "br_reform_siting" not in ax["tx_sens"]
    s0, _ = _merged(("tx_bill", "s0_tx"), ("forced_tx", "reeds_certain_plus_AB"))
    assert s0["forced_tx"] == "reeds_certain_plus_AB"
    # §81: unconstrained (S3, S5) = s0_tx's transmission keys with mode unconstrained
    s0, _ = _merged(("tx_bill", "unconstrained"))
    assert tx_policy.tx_settings(s0)["mode"] == "unconstrained" and not tx_policy.needs_module(s0)
    s0, _ = _merged(("tx_bill", "s0_tx"), ("tx_sens", "tx_capex_x1_5"))
    assert tx_policy.tx_settings(s0)["capex_multiplier"] == 1.5
    s0, _ = _merged(("tx_bill", "s0_tx"), ("tx_sens", "transfer_floor"))
    assert tx_policy.tx_settings(s0)["transfer_floor"].endswith("tx_transfer_floor_placeholder.csv")
    s0, _ = _merged(("tx_bill", "bill_central"), ("tx_sens", "br_high"))
    cs = {"c": {y: {"s0_production": s0, "model_first_planning_year": y - 1} for y in years}}
    s0prod.apply_levels_by_period(cs["c"][2035], s0, "c", 2035)
    assert cs["c"][2035]["build_rate"]["level"] == "high" and cs["c"][2035]["interconnection_headroom"]["scenario"] == "atts_reform"


def test_bill_case_rows():
    si = pd.read_csv(REPO / "pg/extra_inputs/scenario_inputs.csv")
    a = si[si.case_id == "S0prod_A"].set_index("year")
    names = {"S0_tx": "s0_tx", "BILL_central": "bill_central", "BILL_low": "bill_low", "BILL_high": "bill_high",
             "BILL_central_txonly": "bill_central_txonly", "BILL_central_bronly": "bill_central_bronly",
             "BILL_central_S1": "bill_central"}
    for cid, val in names.items():
        c = si[si.case_id == cid].set_index("year")
        assert list(c.index) == [2028, 2030, 2035, 2040, 2045] and (c.tx_bill == val).all() and (c.tx_sens == "none").all()
        extra = {"tax_credits"} if cid.endswith("_S1") else set()
        for y in c.index:                                              # mode-A chains: S0prod_A + the new columns
            diff = {k for k in si.columns if k not in ("case_id", "year") and str(a.at[y, k]) != str(c.at[y, k])}
            assert diff <= {"tx_bill", "forced_tx"} | extra, (cid, y, diff)
        assert (c.forced_tx == "reeds_certain_plus_A").all() and (c.s0_production == "on").all()
        t = si[si.case_id == f"s4x1_{cid}_2035"].iloc[0]                # single-year 2035 test version
        diff = {k for k in si.columns if k not in ("case_id", "year") and str(a.at[2035, k]) != str(t[k])}
        assert diff <= {"tx_bill", "forced_tx", "s0_production"} | extra and t.s0_production == "on_single", (cid, diff)
    # S1: BILL_central with fedpol S1's tax credits (no_wind_solar in 2028, full_ira from 2030); same policies
    s1 = si[si.case_id == "BILL_central_S1"].set_index("year")
    bc = si[si.case_id == "BILL_central"].set_index("year")
    assert list(s1.tax_credits) == ["no_wind_solar"] + ["full_ira"] * 4 and (s1.policies == bc.policies).all()
    assert si[si.case_id == "s4x1_BILL_central_S1_2035"].iloc[0].tax_credits == "full_ira"
    # sensitivity rows on S0_tx 2035
    base = si[si.case_id == "s4x1_S0_tx_2035"].iloc[0]
    for k, col, v in (("forcedAB", "forced_tx", "reeds_certain_plus_AB"), ("floor", "tx_sens", "transfer_floor"),
                      ("txcapex", "tx_sens", "tx_capex_x1_5"), ("brhigh", "tx_sens", "br_high"),
                      ("osw", "offshore_wind_policy", "capped_2025_released")):
        r = si[si.case_id == f"s4x1_S0_tx_2035_{k}"].iloc[0]
        assert [c for c in si.columns if c != "case_id" and str(r[c]) != str(base[c])] == [col] and r[col] == v
    for k, v in (("life60", "life_coal60"), ("life70", "life_coal70"), ("nofriction", "friction_off")):   # §66
        r = si[si.case_id == f"s4x1_S0_tx_2035_{k}"].iloc[0]
        assert [c for c in si.columns if c != "case_id" and str(r[c]) != str(base[c])] == ["retirement_sens"]
        assert r.retirement_sens == v
    # S0 defaults (§66): S0prod_A / B and s4x1_S0prod_2035_new on the S0_tx baseline; every older row: legacy / none
    # (+ its §69 light/compact variants, which differ only in prm_design)
    final = (si.case_id.isin(["S0prod_A", "S0prod_B"]) | si.case_id.str.startswith("s4x1_S0prod_2035_new")
             | (si.s_set != "none"))                                         # §75: the S-set rows are on S0
    unc = si.s_set.isin(["S3", "S5"])                                     # §81: unconstrained transmission (tx_bill)
    assert (si[final & ~unc].tx_bill == "s0_tx").all() and (si[unc].tx_bill == "unconstrained").all()
    assert (si[final].forced_tx == "reeds_certain_plus_A").all()
    old = si[~si.case_id.str.contains("S0_tx|BILL") & ~final]
    assert (old.tx_bill == "legacy").all() and (old.tx_sens == "none").all()


def _forced_files(tmp_path, out_dir, years, chain, classes, source=None, **kw):
    """trans_build_minimum / trans_path_expansion_limit (as {(pair, period): MW}) of a reeds_certain_plus_* stage built
    from the committed status-review file."""
    from s0_workflow import production as s0prod
    from test_forced_tx import build
    (tmp_path / out_dir).mkdir(exist_ok=True)
    extra = {"_chain_years": chain} if chain else {}
    out = build(tmp_path / out_dir, years=years, source=source or REPO / "pg_to_switch.py",
                forced_tx_table=["pg/extra_inputs/transmission/forced_tx_reeds_certain_2026.09.21.csv",
                                 s0prod.STATUS_REVIEW_TABLE],
                forced_tx_status_classes=classes, forced_tx_expansion_limit="minimum", **extra, **kw)
    names = _names(out)
    bm = pd.read_csv(out / "trans_build_minimum.csv") if (out / "trans_build_minimum.csv").exists() else None
    lim = pd.read_csv(out / "trans_path_expansion_limit.csv")
    m = {} if bm is None else {(names[a], b): v for a, b, v in zip(bm.TRANSMISSION_LINE, bm.PERIOD, bm.trans_build_minimum_mw)}
    L = {(names[a], b): v for a, b, v in zip(lim.TRANSMISSION_LINE, lim.PERIOD, lim.trans_path_expansion_limit_mw)}
    return out, m, L


def test_several_projects_on_one_pair(tmp_path):
    """reeds_certain_plus_AB: two zone pairs have two projects each (p81-p83: #14 class A and Coffeen North-Roxford
    class B, both in the 2030 period; p80-p105: #16 class A in 2030 and #42 class B in 2035). Projects in the same
    period add up; projects in different periods are each forced in their own period. The minimum is cumulative
    (trans_build_minimum counts new capacity up to the period); the forced-period cap is the period's own projects.
    Expected values come from the committed file, so a correction to its MW or years keeps the test valid."""
    from s0_workflow import production as s0prod
    sr = pd.read_csv(REPO / "pg/extra_inputs/transmission/forced_tx_status_review.csv")
    sr["pair"] = ["-".join(sorted([a, b], key=lambda z: int(z[1:]))) for a, b in zip(sr.from_zone, sr.to_zone)]
    two = sr[sr.pair.duplicated(keep=False)]
    assert set(two.pair) == {"p81-p83", "p80-p105"}
    assert all(sorted(g.status_class) == ["A", "B"] for _, g in two.groupby("pair"))
    chain = [2028, 2030, 2035, 2040, 2045]

    def expected(pair, classes, ch, stage):
        rows = [(mw, s0prod.forced_tx_period(yr, ch, ch)) for mw, yr, c in
                zip(two[two.pair == pair].new_cap_mw, two[two.pair == pair].new_cap_year, two[two.pair == pair].status_class)
                if c in classes]
        return {(pair, p): (sum(mw for mw, q in rows if q is not None and q <= p), sum(mw for mw, q in rows if q == p))
                for p in sorted({q for _, q in rows if q is not None and q in stage})}

    per = {pr: {s0prod.forced_tx_period(y, chain, chain) for y in two[two.pair == pr].new_cap_year} for pr in set(two.pair)}
    assert len(per["p81-p83"]) == 1 and len(per["p80-p105"]) == 2          # one shared period; two different ones
    for name, years, ch in (("f", chain, chain), ("s", [2035], [2035])):
        _, m, L = _forced_files(tmp_path, name, years, ch, ["A", "B"])
        got = {k: (m[k], L[k]) for k in m if k[0] in ("p81-p83", "p80-p105")}
        want = {**expected("p81-p83", "AB", ch, years), **expected("p80-p105", "AB", ch, years)}
        assert got == want, (name, got, want)
        if name == "f":                                               # summed in one period / cumulative over two
            mw = dict(zip(zip(two.pair, two.status_class), two.new_cap_mw))
            assert got[("p81-p83", 2030)] == (mw[("p81-p83", "A")] + mw[("p81-p83", "B")],) * 2
            p1, p2 = sorted(per["p80-p105"])
            first = [mw[("p80-p105", c)] for c in "AB"
                     if s0prod.forced_tx_period(int(two[(two.pair == "p80-p105") & (two.status_class == c)].new_cap_year.iloc[0]),
                                                chain, chain) == p1][0]
            assert got[("p80-p105", p1)] == (first, first)
            assert got[("p80-p105", p2)] == (mw[("p80-p105", "A")] + mw[("p80-p105", "B")],
                                             mw[("p80-p105", "A")] + mw[("p80-p105", "B")] - first)
    # mode A: each stage forces the projects of its own period
    for y in chain:
        _, m, L = _forced_files(tmp_path, f"a{y}", [y], chain, ["A", "B"])
        got = {k: (m[k], L[k]) for k in m if k[0] in ("p81-p83", "p80-p105")}
        assert got == {**expected("p81-p83", "AB", chain, [y]), **expected("p80-p105", "AB", chain, [y])}, y
    # class A only: one project per pair, as before
    _, m, L = _forced_files(tmp_path, "fa", chain, chain, ["A"])
    got = {k: (m[k], L[k]) for k in m if k[0] in ("p81-p83", "p80-p105")}
    assert got == {**expected("p81-p83", "A", chain, chain), **expected("p80-p105", "A", chain, chain)}
    assert all(a == b for a, b in got.values())


def test_plus_A_unchanged_by_the_multi_project_merge(tmp_path):
    """reeds_certain_plus_A builds byte-identical forced-line files with the code before this change (8f4a266), for a
    multi-period stage, mode-A stages and the single-year version; plus_AB differs only on the two shared pairs."""
    old = tmp_path / "old_pg_to_switch.py"
    old.write_text(subprocess.run(["git", "show", "8f4a266:pg_to_switch.py"], cwd=REPO, capture_output=True, text=True,
                                  check=True).stdout)
    chain = [2028, 2030, 2035, 2040, 2045]
    for years, ch in ((chain, chain), ([2030], chain), ([2035], chain), ([2035], [2035])):
        a, *_ = _forced_files(tmp_path, "o", years, ch, ["A"], source=old)
        b, *_ = _forced_files(tmp_path, "n", years, ch, ["A"])
        for f in ("trans_build_minimum.csv", "trans_path_expansion_limit.csv", "transmission_lines.csv"):
            assert (a / f).read_bytes() == (b / f).read_bytes(), (years, ch, f)
        _, mo, Lo = _forced_files(tmp_path, "oab", years, ch, ["A", "B"], source=old)
        _, mn, Ln = _forced_files(tmp_path, "nab", years, ch, ["A", "B"])
        diff = {k[0] for k in set(mo) | set(mn) if mo.get(k) != mn.get(k)} | \
            {k[0] for k in set(Lo) | set(Ln) if Lo.get(k) != Ln.get(k)}
        assert diff <= {"p81-p83", "p80-p105"} and (diff or years == [2028]), (years, ch, diff)


def test_multi_project_pair_in_national_cap_and_chain(tmp_path):
    """The national-cap rewrite keeps the forced-period cap at the period's own projects, and in a chain the
    cumulative minimum less what earlier stages built is the period's own projects again (prepare_next_stage)."""
    import importlib.util
    chain = [2028, 2030, 2035, 2040, 2045]
    out, m, _ = _forced_files(tmp_path, "nc", [2035], chain, ["A", "B"])
    pd.DataFrame({"INVESTMENT_PERIOD": [2035]}).to_csv(out / "periods.csv", index=False)
    tx_policy.write_case_inputs(out, {"tx_policy": SETTINGS["bill_central"]},
                                {2035: {"forced_tx_expansion_limit": "minimum"}}, lambda x: None)
    names = _names(out)
    lim = pd.read_csv(out / "trans_path_expansion_limit.csv")
    L = {(names[a], b): v for a, b, v in zip(lim.TRANSMISSION_LINE, lim.PERIOD, lim.trans_path_expansion_limit_mw)}
    assert m[("p80-p105", 2035)] == 1792.0 and L[("p80-p105", 2035)] == 896.0
    # chain: the 2030 stage built 896 MW on p80-p105 (and 2,146 on p81-p83); the 2035 stage's chained files
    spec = importlib.util.spec_from_file_location("pns", REPO / "switch/study_modules/prepare_next_stage.py")
    pns = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pns)
    line = {v: k for k, v in names.items()}
    inp, nxt = tmp_path / "in", out
    inp.mkdir()
    built = pd.DataFrame({"TRANSMISSION_LINE": [line["p80-p105"], line["p81-p83"]], "BuildTx": [896.0, 2146.0]})
    pns.chain_forced_tx(inp, nxt, built, lambda *q: Path(Path(*q).parent, f"{Path(*q).stem}.chained.c{Path(*q).suffix}"),
                        lambda q: pd.read_csv(q, na_values=["."]), lambda df, q: df.to_csv(q, index=False, na_rep="."))
    cm = pd.read_csv(nxt / "trans_build_minimum.chained.c.csv")
    cl = pd.read_csv(nxt / "trans_path_expansion_limit.chained.c.csv")
    assert cm.set_index("TRANSMISSION_LINE").at[line["p80-p105"], "trans_build_minimum_mw"] == 896.0
    assert cl.set_index(["TRANSMISSION_LINE", "PERIOD"]).at[(line["p80-p105"], 2035), "trans_path_expansion_limit_mw"] == 896.0


# the first period each bill row differs from S0 (§72): every case writer gives S0's files before it
FIRST_CHANGE = {"bill_central": 2035, "bill_low": 2035, "bill_high": 2030, "bill_central_txonly": 2035,
                "bill_central_bronly": 2035}


def test_bill_rows_write_s0_inputs_until_their_change(tmp_path):
    """§72: before its first change a bill row writes byte-identical transmission-policy files (moratorium rows, cap
    by period, line classes), no reserve import allowance, the same headroom scenario and build-rate level, and no
    headroom switch marker, so its early stages can be reused from S0prod_A (s0_workflow/chain_reuse.py)."""
    from s0_workflow import production as s0prod
    chain = [2028, 2030, 2035, 2040, 2045]
    s0_ref, _ = _merged(("tx_bill", "s0_tx"))

    def build(s0, y, tag):
        out = _stage(tmp_path / tag, [y], chain)
        tx_policy.write_case_inputs(out, s0, {y: {"forced_tx_expansion_limit": "minimum"}}, lambda x: None)
        s0prod.write_level_switch(out, s0, {y: {"_chain_years": chain}}, lambda x: None)
        return {f.name: f.read_bytes() for f in sorted(out.iterdir()) if f.is_file()}

    for val, first in FIRST_CHANGE.items():
        s0, _ = _merged(("tx_bill", val))
        for y in [c for c in chain if c < first]:
            (tmp_path / f"{val}{y}").mkdir()
            (tmp_path / f"ref{val}{y}").mkdir()
            assert build(s0, y, f"{val}{y}") == build(s0_ref, y, f"ref{val}{y}"), (val, y)
            assert prm.import_allowance(s0["prm"], [y]) == 0.0
            for what in ("interconnection_headroom", "build_rate"):
                assert s0prod.level_for(s0, what, y) in (None, {"interconnection_headroom": "atts_s0",
                                                               "build_rate": "central"}[what]), (val, y, what)
        y = first                                      # and the first change shows in that period
        s0_vals = (tx_policy.step_value(tx_policy.tx_settings(s0)["cap_tw_mi_per_yr"], y),
                   s0prod.level_for(s0, "interconnection_headroom", y), prm.import_allowance(s0["prm"], [y]))
        assert s0_vals != (1.4, None, 0.0) and s0_vals != (1.4, "atts_s0", 0.0), val
    with pytest.raises(ValueError, match="single-period stages"):
        prm.import_allowance({"imports": {"new_tx_allowance": {2028: 0.0, 2035: 0.85}}}, [2030, 2035])


def test_expected_reuse_of_the_bill_rows():
    """§72 acceptance: from the case definitions, BILL_central, BILL_low and the two decomposition rows reuse 2028
    and 2030 from S0prod_A; BILL_high and BILL_central_S1 reuse 2028."""
    from s0_workflow import chain_reuse as cr
    df = cr.expected_reuse(REPO / "pg/extra_inputs/scenario_inputs.csv", REPO / "pg/settings/scenario_management.yml")
    got = df[df.expected_reuse].groupby("case").stage.apply(list).to_dict()
    assert got["S0_tx"] == [2028, 2030, 2035, 2040, 2045]
    for c in ("BILL_central", "BILL_low", "BILL_central_txonly", "BILL_central_bronly"):
        assert got.get(c) == [2028, 2030], c
    for c in ("BILL_high", "BILL_central_S1"):
        assert got.get(c) == [2028], c
    s1 = df[(df.case == "BILL_central_S1") & (df.stage == 2030)].differences.iat[0]
    assert "tax_credit" in s1
