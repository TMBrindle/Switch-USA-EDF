"""Bounded foresight (item 8): the per-period retirement rule, myopic chains (mode A) and rolling
two-period windows (mode B), on the Switch 3-zone toy with a third period added.
Small hand-built inputs: fixtures, not results. Run from the repo root:
    SWITCH_SRC=/opt/switch-src pytest -q s0_workflow/tests"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s0_workflow import production as s0prod  # noqa: E402
from toyutil import (add_period, build, run_chain, solve, subset_periods, toy_inputs,  # noqa: E402
                     total_cost)

COAL = ["N-Coal_ST", "C-Coal_ST"]


def pns():
    spec = importlib.util.spec_from_file_location("pns", REPO / "switch/study_modules/prepare_next_stage.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def costly_coal(inp, rule=True, can_retire=1):
    """Existing coal with a high fixed O&M, so the model wants to retire it as early as it may."""
    bc = pd.read_csv(inp / "gen_build_costs.csv")
    bc.loc[bc.GENERATION_PROJECT.isin(COAL), "gen_fixed_om"] = 400000.0
    bc.to_csv(inp / "gen_build_costs.csv", index=False)
    gi = pd.read_csv(inp / "gen_info.csv", na_values=".")
    gi["gen_can_retire_early"] = gi.GENERATION_PROJECT.isin(COAL).astype(int) * can_retire
    gi.to_csv(inp / "gen_info.csv", index=False, na_rep=".")
    if rule:
        pd.DataFrame({"gen_energy_source": ["Coal"], "rr_no_retirement_before": [2030]}).to_csv(
            inp / "retirement_rules.csv", index=False)


def three_periods(inp, rule=True, can_retire=1):
    add_period(inp)
    costly_coal(inp, rule, can_retire)


def suspended(out):
    p = Path(out) / "SuspendGen.csv"
    if not p.exists():
        return pd.Series(dtype=float)
    s = pd.read_csv(p)
    s = s[s.GEN_BLD_SUSPEND_YRS_1.isin(COAL)]
    return s.groupby("GEN_BLD_SUSPEND_YRS_3")["SuspendGen"].sum()


# ---------------------------------------------------------------------------------------------- rule
def test_retirement_rule_per_period(tmp_path):
    """No coal retirement before 2030, economic from 2030 (rule); the old single flag either retires
    in the first period (Can_Retire 1) or locks coal in for every period (Can_Retire 0)."""
    rule = toy_inputs(tmp_path, "rule", ["retirement_rules"], lambda i: three_periods(i))
    solve(rule)
    free = toy_inputs(tmp_path, "free", ["retirement_rules"], lambda i: three_periods(i, rule=False))
    solve(free)
    lock = toy_inputs(tmp_path, "lock", ["retirement_rules"], lambda i: three_periods(i, rule=False, can_retire=0))
    solve(lock)
    sr, sf, sl = suspended(rule / "outputs"), suspended(free / "outputs"), suspended(lock / "outputs")
    assert sf.get(2020, 0) > 1                     # unconstrained: coal retires in the first period
    assert sr.get(2020, 0) == pytest.approx(0, abs=1e-6)   # rule: kept through the 2020 period
    assert sr.get(2030, 0) > 1                     # and retired economically from 2030
    assert sl.sum() == pytest.approx(0, abs=1e-6)  # single flag at 0 (blocked_2030 in a pre-2030 case): coal lock
    chk = pd.read_csv(rule / "outputs/retirement_rules_check.csv")
    assert chk[chk.blocked].suspended_mw.abs().max() < 1e-6
    assert total_cost(free / "outputs") <= total_cost(rule / "outputs") <= total_cost(lock / "outputs")


def test_retirement_rule_case_writer(tmp_path):
    pd.DataFrame({"GENERATION_PROJECT": ["coal_old", "gas_old", "gas_new", "wind_old"],
                  "gen_energy_source": ["coal", "naturalgas", "naturalgas", "wind"],
                  "gen_can_retire_early": [0, 0, 0, 0]}).to_csv(tmp_path / "gen_info.csv", index=False)
    pd.DataFrame({"GENERATION_PROJECT": ["coal_old", "gas_old", "wind_old"], "build_year": [1990, 2000, 2010],
                  "build_gen_predetermined": [1, 1, 1]}).to_csv(tmp_path / "gen_build_predetermined.csv", index=False)
    log = s0prod.Log(tmp_path)
    s0prod.write_retirement_rules(tmp_path, {"retirement_rule": {"enabled": True}}, log)
    gi = pd.read_csv(tmp_path / "gen_info.csv").set_index("GENERATION_PROJECT")["gen_can_retire_early"]
    assert gi["coal_old"] == 1 and gi["gas_old"] == 1 and gi["gas_new"] == 0 and gi["wind_old"] == 0
    rr = pd.read_csv(tmp_path / "retirement_rules.csv")
    assert set(rr.gen_energy_source) == {"coal", "naturalgas"} and (rr.rr_no_retirement_before == 2030).all()


# -------------------------------------------------------------------------------------------- stages
def test_plan_stages():
    yrs = [2028, 2030, 2035, 2040, 2045]
    a = s0prod.plan_stages(yrs, "myopic")
    assert [s["name"] for s in a] == ["2028", "2030", "2035", "2040", "2045"]
    assert [s["commit_period"] for s in a] == yrs and [s["next"] for s in a] == ["2030", "2035", "2040", "2045", None]
    b = s0prod.plan_stages(yrs, "windows", 2)
    assert [s["years"] for s in b] == [[2028, 2030], [2030, 2035], [2035, 2040], [2040, 2045]]
    assert [s["commit_period"] for s in b] == [2028, 2030, 2035, 2045]       # the last window commits both
    assert [s["next"] for s in b] == ["2030_2035", "2035_2040", "2040_2045", None]
    assert s0prod.plan_stages(yrs, "single")[0]["years"] == yrs


def _chain(tmp_path, name, mode):
    """Master toy with 2020/2030/2040, split into stages; returns (run dir, stages, outputs)."""
    run = toy_inputs(tmp_path, name, ["retirement_rules"], lambda i: three_periods(i))
    stages = s0prod.plan_stages([2020, 2030, 2040], mode, 2)
    for st in stages:
        d = run / "in" / st["name"] / "case"
        d.parent.mkdir(parents=True, exist_ok=True)
        subset_periods(run / "inputs", d, st["years"])
        s0prod.write_stage_info(d, st)
    (run / "mods/prepare_next_stage.py").write_text(
        (REPO / "switch/study_modules/prepare_next_stage.py").read_text())
    # prepare_next_stage reads BuildStorageEnergy.csv, so the storage module is on (no storage in the toy)
    outs = run_chain(run, stages, modules=["retirement_rules"],
                     extra=["--include-module", "switch_model.generators.extensions.storage"])
    return run, stages, outs


def test_mode_a_myopic_chain(tmp_path):
    run, stages, outs = _chain(tmp_path, "A", "myopic")
    # stage 1 = the 2020 period alone; its builds and retirements become predetermined in stage 2
    b1 = build(outs[0])
    new20 = b1[(b1.build_year == 2020) & (b1.mw > 1e-6)]
    ch = pd.read_csv(run / "in/2030/case/gen_build_predetermined.chained.case.csv")
    for g, mw in zip(new20.gen, new20.mw):
        assert ch[(ch.GENERATION_PROJECT == g) & (ch.build_year == 2020)].build_gen_predetermined.sum() == \
            pytest.approx(mw, rel=1e-6)
    # rule: no coal retirement in 2020; coal retired in 2030 is gone from 2040's predetermined fleet
    assert suspended(outs[0]).get(2020, 0) == pytest.approx(0, abs=1e-6)
    ret30 = suspended(outs[1]).get(2030, 0)
    assert ret30 > 1
    pre_coal = pd.read_csv(run / "in/2040/case/gen_build_predetermined.chained.case.csv")
    pre_coal = pre_coal[pre_coal.GENERATION_PROJECT.isin(COAL)].build_gen_predetermined.sum()
    orig = pd.read_csv(run / "inputs/gen_build_predetermined.csv")
    orig = orig[orig.GENERATION_PROJECT.isin(COAL)].build_gen_predetermined.sum()
    assert pre_coal == pytest.approx(orig - ret30, abs=1e-4)
    assert all((o / "total_cost.txt").exists() for o in outs)


def test_mode_b_rolling_windows(tmp_path):
    run, stages, outs = _chain(tmp_path, "B", "windows")
    assert [s["name"] for s in stages] == ["2020_2030", "2030_2040"]
    b1 = build(outs[0])
    # window 1 builds in both periods, but only 2020 (period end 2026) is handed on
    assert b1[(b1.build_year == 2030)].mw.sum() > 1e-3
    ch = pd.read_csv(run / "in/2030_2040/case/gen_build_predetermined.chained.case.csv")
    assert not (ch.build_year == 2030).any() or set(ch[ch.build_year == 2030].GENERATION_PROJECT) <= set(
        pd.read_csv(run / "in/2030_2040/case/gen_build_predetermined.csv").GENERATION_PROJECT)
    new20 = b1[(b1.build_year == 2020) & (b1.mw > 1e-6)]
    for g, mw in zip(new20.gen, new20.mw):
        assert ch[(ch.GENERATION_PROJECT == g) & (ch.build_year == 2020)].build_gen_predetermined.sum() == \
            pytest.approx(mw, rel=1e-6)
    # retirements: window 1 retires coal in 2030 (allowed) but that is not committed
    s1 = suspended(outs[0])
    assert s1.get(2020, 0) == pytest.approx(0, abs=1e-6) and s1.get(2030, 0) > 1
    pre = pd.read_csv(run / "in/2030_2040/case/gen_build_predetermined.chained.case.csv")
    orig = pd.read_csv(run / "inputs/gen_build_predetermined.csv")
    for g in COAL:
        assert pre[pre.GENERATION_PROJECT == g].build_gen_predetermined.sum() == pytest.approx(
            orig[orig.GENERATION_PROJECT == g].build_gen_predetermined.sum())
    # window 2 re-decides 2030 from the committed 2020 fleet, and commits both its periods (last window)
    s2 = suspended(outs[1])
    assert s2.get(2030, 0) > 1
    assert not (run / "in/2030_2040/case/stage_info.csv").read_text().count("2040_") and \
        pd.read_csv(run / "in/2030_2040/case/stage_info.csv").commit_period.iat[0] == 2040


def test_committed_handoff_headroom_and_build_rate(tmp_path):
    """chain_ic_inputs and chain_build_rate_inputs take the committed period from cumulative files."""
    p = pns()
    inp, out, nxt = tmp_path / "in", tmp_path / "out", tmp_path / "next"
    for d in (inp, out, nxt):
        d.mkdir()
    pd.DataFrame({"IC_ZONE": ["z"], "ic_zone_load_zone": ["Z"], "ic_base_capacity_mw": [100.0],
                  "ic_start_saturation": [0.5], "ic_release_cost_per_mw": [1.0]}).to_csv(inp / "ic_zones.csv", index=False)
    pd.DataFrame({"IC_TRANCHE": ["z_1"], "ic_tranche_zone": ["z"], "ic_tranche_width": [0.2],
                  "ic_tranche_cost_per_mw": [1.0]}).to_csv(inp / "ic_tranches.csv", index=False)
    pd.DataFrame({"IC_UPRATE": ["z_conv"], "ic_uprate_zone": ["z"], "ic_uprate_type": ["conv_reinforcement"],
                  "ic_uprate_max_mw": [50.0], "ic_uprate_cost_per_mw": [1.0], "ic_uprate_available_year": [0],
                  "ic_uprate_mode": ["host"]}).to_csv(inp / "ic_uprates.csv", index=False)
    pd.DataFrame({"ic_tranche": ["z_1", "z_1"], "ic_zone": ["z", "z"], "period": [2028, 2030],
                  "used_mw": [5.0, 15.0]}).to_csv(out / "ic_tranches_built.csv", index=False)
    pd.DataFrame({"ic_uprate": ["z_conv"] * 2, "ic_zone": ["z"] * 2, "period": [2028, 2030],
                  "built_mw": [3.0, 10.0]}).to_csv(out / "ic_uprates_built.csv", index=False)
    pd.DataFrame({"ic_zone": ["z", "z"], "period": [2028, 2030], "released_mw": [0.0, 0.0]}).to_csv(
        out / "ic_release_built.csv", index=False)
    pd.DataFrame({"ic_zone": ["z", "z"], "period": [2028, 2030], "hosted_mw": [3.0, 10.0],
                  "unused_mw": [1.0, 0.0]}).to_csv(out / "ic_hosted_built.csv", index=False)
    p.chain_ic_inputs(inp, out, nxt, "c", commit=2028)
    z = pd.read_csv(nxt / "ic_zones.chained.c.csv").iloc[0]
    assert z.ic_start_saturation == pytest.approx((50 + 5) / 100) and z.ic_hosted_headroom_mw == 1.0
    assert pd.read_csv(nxt / "ic_uprates.chained.c.csv").ic_uprate_max_mw.iat[0] == 47.0
    assert pd.read_csv(nxt / "ic_tranches.chained.c.csv").ic_tranche_width.iat[0] == pytest.approx(0.2 - 0.05)
    p.chain_ic_inputs(inp, out, nxt, "c")           # legacy: the last period in the files
    assert pd.read_csv(nxt / "ic_zones.chained.c.csv").ic_start_saturation.iat[0] == pytest.approx(0.65)

    class P(list):
        def last(self):
            return self[-1]
    m = SimpleNamespace(PERIODS=P([2028, 2030]), BR_GROUP_PERIODS=[("solar", 2028), ("solar", 2030)],
                        BRNewBuild={("solar", 2028): 30.0, ("solar", 2030): 500.0}, br_window_years={2028: 3, 2030: 2})
    chained = lambda *q: Path(Path(*q).parent, f"{Path(*q).stem}.chained.c{Path(*q).suffix}")
    possibly = lambda *q: chained(*q) if chained(*q).exists() else Path(*q)
    p.chain_build_rate_inputs(m, inp, nxt, chained, possibly, lambda q: pd.read_csv(q),
                              lambda df, q: df.to_csv(q, index=False), 2028)
    assert pd.read_csv(nxt / "build_rate_prev_build.chained.c.csv").br_prev_rate_mw_per_yr.iat[0] == 10.0


# ------------------------------------------------------------------------------------- new-build rule
def cheap_nuclear(inp, rule=True):
    """New nuclear made cheap and available from 2020, so the model builds it as early as it may; a
    planned (predetermined) unit in 2015 that the rule must leave alone."""
    bc = pd.read_csv(inp / "gen_build_costs.csv")
    nuc = bc[bc.GENERATION_PROJECT == "N-Nuclear"].copy()
    early = nuc.assign(build_year=2020)
    planned = bc[bc.GENERATION_PROJECT == "C-Nuclear"].assign(build_year=2015)
    bc = pd.concat([bc, early, planned], ignore_index=True)
    bc.loc[bc.GENERATION_PROJECT == "N-Nuclear", ["gen_overnight_cost", "gen_fixed_om"]] = [300000.0, 1000.0]
    bc.to_csv(inp / "gen_build_costs.csv", index=False)
    pre = pd.read_csv(inp / "gen_build_predetermined.csv")
    pd.concat([pre, pd.DataFrame({"GENERATION_PROJECT": ["C-Nuclear"], "build_year": [2015],
                                  "build_gen_predetermined": [1.0]})]).to_csv(
        inp / "gen_build_predetermined.csv", index=False)
    if rule:
        pd.DataFrame({"gen_energy_source": ["Uranium"], "br_no_new_build_before": [2030]}).to_csv(
            inp / "build_rules.csv", index=False)


def test_new_build_rule_per_period(tmp_path):
    """No new nuclear before the rule's period (here 2030 on the toy's 2020/2030 periods; 2035 in S0);
    without the rule the model builds it in the first period. Predetermined units are untouched."""
    rule = toy_inputs(tmp_path, "rule", ["build_rules"], lambda i: cheap_nuclear(i))
    solve(rule)
    free = toy_inputs(tmp_path, "free", ["build_rules"], lambda i: cheap_nuclear(i, rule=False))
    solve(free)
    br, bf = build(rule / "outputs"), build(free / "outputs")
    nuc = lambda b, y: b[(b.gen == "N-Nuclear") & (b.build_year == y)].mw.sum()
    assert nuc(bf, 2020) > 1                                      # unconstrained: built in the first period
    assert nuc(br, 2020) == pytest.approx(0, abs=1e-6)             # rule: none before 2030
    assert nuc(br, 2030) > 1                                       # and allowed from 2030
    assert br[(br.gen == "C-Nuclear") & (br.build_year == 2015)].mw.sum() == pytest.approx(1.0)
    chk = pd.read_csv(rule / "outputs/build_rules_check.csv")
    assert chk[chk.blocked].new_mw.abs().max() < 1e-6 and set(chk.period) == {2020, 2030}
    assert total_cost(free / "outputs") <= total_cost(rule / "outputs")


def test_new_build_rule_case_writer(tmp_path):
    pd.DataFrame({"GENERATION_PROJECT": ["nuc_old", "nuc_new", "smr_new", "gas_new"],
                  "gen_tech": ["Nuclear", "Nuclear_Nuclear - Large_Moderate", "Nuclear - small modular reactor",
                               "NaturalGas_1-on-1 Combined Cycle (H-Frame)_Moderate"],
                  "gen_energy_source": ["uranium", "uranium", "uranium", "naturalgas"]}).to_csv(
        tmp_path / "gen_info.csv", index=False)
    log = s0prod.Log(tmp_path)
    s0prod.write_build_rules(tmp_path, {"new_build_rule": {"enabled": True, "no_new_build_before": 2035,
                                                           "technologies": ["nuclear"]}}, log)
    br = pd.read_csv(tmp_path / "build_rules.csv")
    assert list(br.gen_energy_source) == ["uranium"] and list(br.br_no_new_build_before) == [2035]
    assert any("no new ['nuclear']" in ln for ln in log.lines)
    # off: no file
    (tmp_path / "build_rules.csv").unlink()
    s0prod.write_build_rules(tmp_path, {"new_build_rule": {"enabled": False}}, log)
    assert not (tmp_path / "build_rules.csv").exists()
    # an energy source shared with another technology would block that too: refuse
    pd.DataFrame({"GENERATION_PROJECT": ["nuc", "other"], "gen_tech": ["Nuclear", "Fusion"],
                  "gen_energy_source": ["uranium", "uranium"]}).to_csv(tmp_path / "gen_info.csv", index=False)
    with pytest.raises(ValueError, match="Fusion"):
        s0prod.write_build_rules(tmp_path, {"new_build_rule": {"enabled": True}}, log)
