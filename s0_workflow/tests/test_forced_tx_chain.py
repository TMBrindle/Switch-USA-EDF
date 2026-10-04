"""Forced transmission in chained S0 stages (CHANGES §57): each forced line is built once over a chain.

Toy: Switch's 3-zone example with five periods (2020-2060). Two forced lines: N-C (in service 2018: the first
period, like SunZia in 2028) and C-S (in service 2035: the third period, like TransWest in 2035). As in the S0
case build (forced_tx_expansion_limit: minimum), a forced line's expansion limit is its minimum in the forced
period; it is 0 in every other period, so any repeated forcing would show up as extra MW. Small hand-built
inputs: fixtures, not results."""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s0_workflow import production as s0prod  # noqa: E402
from toyutil import add_period, run_chain, subset_periods, toy_inputs  # noqa: E402

YEARS = [2020, 2030, 2040, 2050, 2060]
FORCED = {"N-C": (4.0, 2018), "C-S": (5.0, 2035)}          # line: (MW, in-service year)
EXPECT = {"N-C": 2020, "C-S": 2040}


def five_periods(inp):
    for p in (2040, 2050, 2060):
        add_period(inp, (p, p - 3, p + 6), 2030, 1.0)


def stage_files(d: Path, stage_years, rule: str):
    """trans_build_minimum.csv / trans_path_expansion_limit.csv for one stage as pg_to_switch writes them.
    rule "chain": forced_tx_period over the whole chain (the fix); "stage": the first stage period at or after
    the in-service year (the bug: re-forced in every later stage)."""
    rows = []
    for line, (mw, yr) in FORCED.items():
        if rule == "chain":
            per = s0prod.forced_tx_period(yr, YEARS, stage_years)
        else:
            per = next((p for p in stage_years if p >= yr), None)
        if per is not None:
            rows.append({"TRANSMISSION_LINE": line, "PERIOD": per, "trans_build_minimum_mw": mw})
    if rows:
        pd.DataFrame(rows).to_csv(d / "trans_build_minimum.csv", index=False)
    forced = {(r["TRANSMISSION_LINE"], r["PERIOD"]): r["trans_build_minimum_mw"] for r in rows}
    pd.DataFrame([{"TRANSMISSION_LINE": line, "PERIOD": p, "trans_path_expansion_limit_mw": forced.get((line, p), 0.0)}
                  for line in FORCED for p in stage_years]).to_csv(d / "trans_path_expansion_limit.csv", index=False)


def chain(tmp_path, name, mode, rule, pns_source=None, before_run=None):
    run = toy_inputs(tmp_path, name, ["trans_build_minimum", "trans_path_expansion_limit"], five_periods)
    stages = s0prod.plan_stages(YEARS, mode, 2)
    for st in stages:
        d = run / "in" / st["name"] / "case"
        d.parent.mkdir(parents=True, exist_ok=True)
        subset_periods(run / "inputs", d, st["years"])
        s0prod.write_stage_info(d, st)
        stage_files(d, st["years"], rule)
    (run / "mods/prepare_next_stage.py").write_text(
        pns_source or (REPO / "switch/study_modules/prepare_next_stage.py").read_text())
    aliases = before_run(run, stages) if before_run else None
    outs = run_chain(run, stages, modules=["trans_build_minimum", "trans_path_expansion_limit"],
                     extra=["--include-module", "switch_model.generators.extensions.storage"], aliases=aliases)
    return run, stages, outs


def committed_tx(stages, outs) -> pd.DataFrame:
    """New transmission each stage hands on (its commit period; the last stage all of its periods)."""
    rows = []
    for st, out in zip(stages, outs):
        b = pd.read_csv(out / "BuildTx.csv")
        for line, per, mw in zip(b.TRANS_BLD_YRS_1, b.TRANS_BLD_YRS_2, b.BuildTx):
            if mw > 1e-6 and (per <= st["commit_period"] or not st["next"]):
                rows.append({"stage": st["name"], "line": line, "period": int(per), "mw": mw})
    return pd.DataFrame(rows, columns=["stage", "line", "period", "mw"])


def assert_once(built: pd.DataFrame):
    tot = built.groupby("line").mw.sum()
    for line, (mw, _) in FORCED.items():
        assert tot.get(line, 0.0) == pytest.approx(mw, abs=1e-4), built
        assert set(built[built.line == line].period) == {EXPECT[line]}, built


def test_forced_period_rule():
    a = s0prod.plan_stages(YEARS, "myopic")
    got = {line: [st["name"] for st in a if s0prod.forced_tx_period(yr, YEARS, st["years"])] for line, (_, yr) in FORCED.items()}
    assert got == {"N-C": ["2020"], "C-S": ["2040"]}
    b = s0prod.plan_stages(YEARS, "windows", 2)
    got = {line: {st["name"]: s0prod.forced_tx_period(yr, YEARS, st["years"]) for st in b} for line, (_, yr) in FORCED.items()}
    # mode B: forced in each window that models the period, committed only by the window committing it
    assert got["N-C"] == {"2020_2030": 2020, "2030_2040": None, "2040_2050": None, "2050_2060": None}
    assert got["C-S"] == {"2020_2030": None, "2030_2040": 2040, "2040_2050": 2040, "2050_2060": None}
    assert [st["commit_period"] for st in b] == [2020, 2030, 2040, 2060]
    # the S0 schedule: SunZia (2026) in 2028, TransWest (2032) in 2035, once each in a mode-A chain
    s0 = [2028, 2030, 2035, 2040, 2045]
    assert [s0prod.forced_tx_period(2026, s0, [y]) for y in s0] == [2028, None, None, None, None]
    assert [s0prod.forced_tx_period(2032, s0, [y]) for y in s0] == [None, None, 2035, None, None]
    # outside chains (chain = stage years) the rule is the old one
    assert s0prod.forced_tx_period(2032, s0, s0) == 2035 and s0prod.forced_tx_period(2050, s0, s0) is None


def test_mode_a_five_stage_chain_builds_each_forced_line_once(tmp_path):
    run, stages, outs = chain(tmp_path, "A", "myopic", "chain")
    assert len(stages) == 5
    assert_once(committed_tx(stages, outs))
    # carried forward as existing capacity, not rebuilt
    tl = pd.read_csv(run / "in/2060/case/transmission_lines.chained.case.csv").set_index("TRANSMISSION_LINE")
    base = pd.read_csv(run / "inputs/transmission_lines.csv").set_index("TRANSMISSION_LINE")
    for line, (mw, _) in FORCED.items():
        assert tl.at[line, "existing_trans_cap"] == pytest.approx(base.at[line, "existing_trans_cap"] + mw, abs=1e-4)


def test_mode_a_safeguard_when_minimum_repeats(tmp_path):
    """The old per-stage minima (every later stage re-forces each line): prepare_next_stage subtracts what the
    chain already built, so each line is still built once."""
    run, stages, outs = chain(tmp_path, "A_old", "myopic", "stage")
    assert len(pd.read_csv(run / "in/2060/case/trans_build_minimum.csv")) == 2          # both re-forced in 2060
    ch = pd.read_csv(run / "in/2060/case/trans_build_minimum.chained.case.csv").set_index("TRANSMISSION_LINE")
    assert (ch.trans_build_minimum_mw == 0).all()
    lim = pd.read_csv(run / "in/2060/case/trans_path_expansion_limit.chained.case.csv")
    assert (lim.trans_path_expansion_limit_mw == 0).all()
    todate = pd.read_csv(run / "in/2060/case/trans_built_to_date.chained.case.csv").set_index("TRANSMISSION_LINE")
    assert todate.trans_built_to_date_mw.to_dict() == pytest.approx({k: v[0] for k, v in FORCED.items()}, abs=1e-4)
    assert_once(committed_tx(stages, outs))


def test_mode_b_windows_build_each_forced_line_once(tmp_path):
    run, stages, outs = chain(tmp_path, "B", "windows", "chain")
    assert [st["name"] for st in stages] == ["2020_2030", "2030_2040", "2040_2050", "2050_2060"]
    # C-S is forced at 2040 in windows 2 and 3; window 2 does not commit 2040, so window 3 still has to build it
    ch = pd.read_csv(run / "in/2040_2050/case/trans_build_minimum.chained.case.csv")
    assert ch.set_index("TRANSMISSION_LINE").trans_build_minimum_mw.to_dict() == {"C-S": 5.0}
    assert not (run / "in/2050_2060/case/trans_build_minimum.csv").exists()
    b2 = pd.read_csv(outs[1] / "BuildTx.csv")
    assert b2[(b2.TRANS_BLD_YRS_1 == "C-S") & (b2.TRANS_BLD_YRS_2 == 2040)].BuildTx.sum() == pytest.approx(5.0, abs=1e-4)
    assert_once(committed_tx(stages, outs))


def test_mode_b_safeguard_when_minimum_repeats(tmp_path):
    run, stages, outs = chain(tmp_path, "B_old", "windows", "stage")
    assert_once(committed_tx(stages, outs))


def test_scenario_lines_alias_chained_forced_files(tmp_path):
    """pg_to_switch's stage lines read the chained forced-line files in later stages that force a line."""
    import shlex
    from test_s0_production import _case_settings, _scenario_files
    sf = _scenario_files()
    s = _case_settings(2035, "on")
    s0prod.apply_settings({"A": {2035: s}})
    years = [2028, 2030, 2035, 2040, 2045]
    stages = s0prod.plan_stages(years, "myopic")
    for st in stages:
        (tmp_path / st["name"] / "A").mkdir(parents=True)
    for y in (2028, 2035):
        (tmp_path / str(y) / "A" / "trans_build_minimum.csv").write_text("TRANSMISSION_LINE,PERIOD,trans_build_minimum_mw\n")
        (tmp_path / str(y) / "A" / "trans_path_expansion_limit.csv").write_text("x\n")
    sf(tmp_path, {"A": {y: s for y in years}}, False, {"A": stages})
    lines = (tmp_path / "scenarios_A.txt").read_text().splitlines()
    for st, ln in zip(stages, lines):
        args = shlex.split(ln)
        al = args[args.index("--input-aliases") + 1:] if "--input-aliases" in args else []
        has = "trans_build_minimum.csv=trans_build_minimum.chained.A.csv" in al
        assert has == (st["name"] == "2035"), (st["name"], ln)       # stage 1 has no chained files
        assert ("trans_built_to_date.csv=trans_built_to_date.chained.A.csv" in al) == (st["name"] != "2028")
        assert ("trans_path_expansion_limit.csv=trans_path_expansion_limit.chained.A.csv" in al) == has


@pytest.mark.parametrize("mode", ["myopic", "windows"])
def test_fix_aliases_on_a_pre_fix_build(tmp_path, mode):
    """The VM case: stages built before §57 (every later stage re-forces each line) and run with the
    prepare_next_stage of that build (1eab1ac: no safeguard). The aliases from fix_forced_tx_aliases.py alone make
    each forced line build once, with no extra build on those lines."""
    import importlib.util
    import subprocess
    old_pns = subprocess.run(["git", "show", "1eab1ac:switch/study_modules/prepare_next_stage.py"], cwd=REPO,
                             capture_output=True, text=True, check=True).stdout
    assert "chain_forced_tx" not in old_pns
    spec = importlib.util.spec_from_file_location("fixal", REPO / "s0_workflow/scripts/fix_forced_tx_aliases.py")
    fixal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixal)
    table = tmp_path / "forced_toy.csv"
    pd.DataFrame([{"from_zone": "North", "to_zone": "Central", "new_cap_mw": 4.0, "new_cap_year": 2018},
                  {"from_zone": "Central", "to_zone": "South", "new_cap_mw": 5.0, "new_cap_year": 2035}]).to_csv(
        table, index=False)
    got = {}

    def fix(run, stages):
        assert fixal.main([str(run / "in"), "--case", "case", "--forced-table", str(table)]) == 0
        al = pd.read_csv(run / "in/forced_tx_aliases.case.csv", dtype={"stage": str}).set_index("stage")
        got.update(al.aliases.to_dict())
        return {k: v.split() for k, v in al.aliases.items()}
    run, stages, outs = chain(tmp_path, f"fix_{mode}", mode, "stage", pns_source=old_pns, before_run=fix)
    assert_once(committed_tx(stages, outs))
    if mode == "myopic":
        assert got["2030"] == "trans_build_minimum.csv=none trans_path_expansion_limit.csv=trans_path_expansion_limit.fixed.csv"
        fx = pd.read_csv(run / "in/2060/case/trans_path_expansion_limit.fixed.csv")
        assert (fx.trans_path_expansion_limit_mw == 0).all()                  # re-forced rows back at the normal 0
    # the same chain without the aliases builds the lines again (the bug)
    run2, stages2, outs2 = chain(tmp_path, f"nofix_{mode}", mode, "stage", pns_source=old_pns)
    tot = committed_tx(stages2, outs2).groupby("line").mw.sum()
    assert tot["N-C"] > FORCED["N-C"][0] + 1 and tot["C-S"] > FORCED["C-S"][0] + 1
