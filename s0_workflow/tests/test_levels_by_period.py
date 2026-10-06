"""Interconnection-headroom scenario and build-rate level by period within a mode-A chain (CHANGES §60 item 3).
Small hand-built inputs: fixtures, not results."""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import production as s0prod  # noqa: E402

BILL_CENTRAL = {"interconnection_headroom": {2028: "atts_planned", 2035: "atts_reform"},
                "build_rate": {2028: "central", 2035: "reform"}}
BILL_HIGH = {"interconnection_headroom": {2028: "atts_planned", 2030: "atts_reform", 2035: "atts_reform_techmax"},
             "build_rate": {2028: "central", 2030: "reform"}}


def pns():
    spec = importlib.util.spec_from_file_location("pns", REPO / "switch/study_modules/prepare_next_stage.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_levels_by_year():
    years = [2028, 2030, 2035, 2040, 2045]
    for table, hr, br in ((BILL_CENTRAL, ["atts_planned"] * 2 + ["atts_reform"] * 3, ["central"] * 2 + ["reform"] * 3),
                          (BILL_HIGH, ["atts_planned", "atts_reform"] + ["atts_reform_techmax"] * 3,
                           ["central"] + ["reform"] * 4)):
        cs = {"c": {y: {"s0_production": {"enabled": True, "levels_by_period": table},
                        "model_first_planning_year": {2028: 2026, 2030: 2029, 2035: 2031, 2040: 2036, 2045: 2041}[y]}
                    for y in years}}
        s0prod.apply_settings(cs)
        assert [cs["c"][y]["interconnection_headroom"]["scenario"] for y in years] == hr
        assert [cs["c"][y]["build_rate"]["level"] for y in years] == br
    # no levels_by_period: s0_production.settings' levels (atts_s0, central) throughout
    cs = {"c": {2035: {"s0_production": {"enabled": True, "settings": {
        "interconnection_headroom": {"enabled": True, "scenario": "atts_s0"}, "build_rate": {"level": "central"}}},
        "model_first_planning_year": 2031}}}
    s0prod.apply_settings(cs)
    assert cs["c"][2035]["interconnection_headroom"]["scenario"] == "atts_s0" and cs["c"][2035]["build_rate"]["level"] == "central"
    for y in ("atts_planned", "atts_reform", "atts_reform_techmax"):
        for kind in ("zones", "tranches", "uprates"):
            assert (REPO / f"interconnection_headroom/outputs/{kind}_{y}.csv").exists()


def test_switch_marker_per_stage(tmp_path):
    chain = [2028, 2030, 2035, 2040, 2045]
    s0 = {"levels_by_period": BILL_CENTRAL}
    for y in chain:
        d = tmp_path / str(y)
        d.mkdir()
        s0prod.write_level_switch(d, s0, {y: {"_chain_years": chain}}, lambda x: None)
    assert [y for y in chain if (tmp_path / str(y) / "ic_scenario_switch.csv").exists()] == [2035]
    sw = pd.read_csv(tmp_path / "2035/ic_scenario_switch.csv").iloc[0]
    assert (sw["from_scenario"], sw["to_scenario"], sw["from_period"], sw["to_period"]) == ("atts_planned", "atts_reform", 2030, 2035)


def _ic(d, uprate_max):
    pd.DataFrame({"IC_ZONE": ["z"], "ic_zone_load_zone": ["Z"], "ic_base_capacity_mw": [100.0],
                  "ic_start_saturation": [0.5], "ic_release_cost_per_mw": [1.0]}).to_csv(d / "ic_zones.csv", index=False)
    pd.DataFrame({"IC_TRANCHE": ["z_1"], "ic_tranche_zone": ["z"], "ic_tranche_width": [0.2],
                  "ic_tranche_cost_per_mw": [1.0]}).to_csv(d / "ic_tranches.csv", index=False)
    pd.DataFrame({"IC_UPRATE": ["z_gets", "z_rec"], "ic_uprate_zone": ["z", "z"], "ic_uprate_type": ["gets", "reconductor"],
                  "ic_uprate_max_mw": uprate_max, "ic_uprate_cost_per_mw": [1.0, 2.0], "ic_uprate_available_year": [0, 0],
                  "ic_uprate_mode": ["host", "host"]}).to_csv(d / "ic_uprates.csv", index=False)


def test_chain_carries_headroom_onto_the_new_scenario(tmp_path):
    p = pns()
    inp, out, nxt = tmp_path / "in", tmp_path / "out", tmp_path / "next"
    for d in (inp, out, nxt):
        d.mkdir()
    _ic(inp, [10.0, 0.0])                       # this stage: planned (GETs 10, reconductoring 0)
    _ic(nxt, [30.0, 50.0])                      # next stage: reform (more GETs, reconductoring)
    pd.DataFrame({"ic_tranche": ["z_1"], "ic_zone": ["z"], "period": [2030], "used_mw": [5.0]}).to_csv(
        out / "ic_tranches_built.csv", index=False)
    pd.DataFrame({"ic_zone": ["z"], "period": [2030], "released_mw": [0.0]}).to_csv(out / "ic_release_built.csv", index=False)
    pd.DataFrame({"ic_uprate": ["z_gets"], "ic_zone": ["z"], "period": [2030], "built_mw": [4.0]}).to_csv(
        out / "ic_uprates_built.csv", index=False)
    # same scenario next stage: the old chaining (uprates from this stage's file, less what was built)
    p.chain_ic_inputs(inp, out, nxt, "c", commit=2030)
    assert pd.read_csv(nxt / "ic_uprates.chained.c.csv").ic_uprate_max_mw.tolist() == [6.0, 0.0]
    z = pd.read_csv(nxt / "ic_zones.chained.c.csv").iloc[0]
    assert z.ic_start_saturation == pytest.approx(0.55)
    # switch: the next stage's own uprates, less what the chain has built (4 MW of GETs)
    pd.DataFrame([{"from_scenario": "atts_planned", "to_scenario": "atts_reform", "from_period": 2030,
                   "to_period": 2035}]).to_csv(nxt / "ic_scenario_switch.csv", index=False)
    p.chain_ic_inputs(inp, out, nxt, "c", commit=2030)
    assert pd.read_csv(nxt / "ic_uprates.chained.c.csv").ic_uprate_max_mw.tolist() == [26.0, 50.0]
    assert pd.read_csv(nxt / "ic_zones.chained.c.csv").iloc[0].ic_start_saturation == pytest.approx(0.55)
    assert pd.read_csv(nxt / "ic_tranches.chained.c.csv").ic_tranche_width.iat[0] == pytest.approx(0.15)
    # a second stage after the switch: built so far counts from the new scenario's own caps
    inp2, out2, nxt2 = tmp_path / "in2", tmp_path / "out2", tmp_path / "next2"
    for d in (inp2, out2, nxt2):
        d.mkdir()
    _ic(inp2, [30.0, 50.0])
    for f in ("ic_zones", "ic_tranches", "ic_uprates"):
        (inp2 / f"{f}.chained.c.csv").write_bytes((nxt / f"{f}.chained.c.csv").read_bytes())
    _ic(nxt2, [40.0, 80.0])
    pd.DataFrame({"ic_tranche": ["z_1"], "ic_zone": ["z"], "period": [2035], "used_mw": [0.0]}).to_csv(
        out2 / "ic_tranches_built.csv", index=False)
    pd.DataFrame({"ic_zone": ["z"], "period": [2035], "released_mw": [0.0]}).to_csv(out2 / "ic_release_built.csv", index=False)
    pd.DataFrame({"ic_uprate": ["z_rec"], "ic_zone": ["z"], "period": [2035], "built_mw": [20.0]}).to_csv(
        out2 / "ic_uprates_built.csv", index=False)
    (nxt2 / "ic_scenario_switch.csv").write_bytes((nxt / "ic_scenario_switch.csv").read_bytes())
    p.chain_ic_inputs(inp2, out2, nxt2, "c", commit=2035)
    assert pd.read_csv(nxt2 / "ic_uprates.chained.c.csv").ic_uprate_max_mw.tolist() == [36.0, 60.0]   # 4 GETs, 20 rec.
    # scenarios with different curves: not supported, stops
    pd.DataFrame({"IC_TRANCHE": ["z_1"], "ic_tranche_zone": ["z"], "ic_tranche_width": [0.3],
                  "ic_tranche_cost_per_mw": [1.0]}).to_csv(nxt / "ic_tranches.csv", index=False)
    with pytest.raises(NotImplementedError, match="ic_tranches"):
        p.chain_ic_inputs(inp, out, nxt, "c", commit=2030)


def test_off_by_period():
    """§73/§75: "off" (or an unquoted YAML off, read as False) in levels_by_period disables that setting in the
    period; it is not a level name, and the case writers then write nothing for it."""
    for what, key in (("build_rate", "level"), ("interconnection_headroom", "scenario")):
        for v in ("off", False):
            s = {what: {"enabled": True, key: "x"}}
            s0prod.apply_levels_by_period(s, {"levels_by_period": {what: {2028: "x", 2040: v}}}, year=2045)
            assert s[what]["enabled"] is False and s[what][key] == "x"
    from build_rate.brc import switch_case as br_case
    assert br_case.br_settings({"build_rate": {"enabled": False}}) is None
