"""Resume a chain after an interruption (CHANGES §243; s0_workflow/scripts/resume_chain.py). A synthetic three-stage
chain stands in for S0prod_A's folders; nothing here is a result."""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import chain_reuse as cr  # noqa: E402
from s0_workflow.scripts import resume_chain as rc  # noqa: E402

STAGES = ("2028", "2030", "2035")
CASE = "C"
CHAINED = ("gen_build_predetermined", "gen_build_costs", "transmission_lines", "build_rate_prev_build")


@pytest.fixture
def chain(tmp_path):
    sw = tmp_path / "switch"
    lines = []
    for i, y in enumerate(STAGES):
        inp = sw / "in/r" / y / CASE
        inp.mkdir(parents=True)
        for f in ("gen_info.csv", "periods.csv", "gen_build_costs.csv", "stage_info.csv"):
            (inp / f).write_text(f"{f},{y}\n1,2\n")
        line = f"--scenario-name {CASE}_{y} --inputs-dir in/r/{y}/{CASE} --outputs-dir out/r/{y}/{CASE}"
        if i + 1 < len(STAGES):
            line += " --include-module study_modules.prepare_next_stage"
        if i:
            line += " --input-aliases " + " ".join(f"{f}.csv={f}.chained.{CASE}.csv" for f in CHAINED)
        lines.append(line)
    scen = sw / "in/r/scenarios_C.fixed.txt"
    scen.write_text("\n".join(lines) + "\n")
    built = {p: p.read_bytes() for p in (sw / "in/r").rglob("*.csv")}
    return {"sw": sw, "scen": scen, "built": built}


def _out(c, y):
    return c["sw"] / "out/r" / y / CASE


def _inp(c, y):
    return c["sw"] / "in/r" / y / CASE


def solve(c, y, handover=True, log="optimal"):
    """What a finished Switch solve leaves: model_config.json, total_cost.txt, the carried results, a log, and (via
    prepare_next_stage, last) the next stage's chained inputs, written after total_cost.txt."""
    o = _out(c, y)
    o.mkdir(parents=True, exist_ok=True)
    (o / "model_config.json").write_text("{}")
    (o / "total_cost.txt").write_text("123456789.0\n")
    for f in rc.CARRIED:
        (o / f).write_text("x\n")
    (o / "dispatch.csv").write_text("x\n")
    if log:
        (o / "solve.log").write_text(f"...\nOptimization termination condition was {log}.\n")
    i = STAGES.index(y)
    if handover and i + 1 < len(STAGES):
        for f in CHAINED:
            p = _inp(c, STAGES[i + 1]) / f"{f}.chained.{CASE}.csv"
            p.write_text(f"{f} from {y}\n")
            t = (o / "total_cost.txt").stat().st_mtime + 5
            os.utime(p, (t, t))


def states(c):
    return [s["state"] for s in rc.chain_status(c["scen"], c["sw"])[1]]


def built_unchanged(c):
    return all(p.exists() and p.read_bytes() == b for p, b in c["built"].items())


def test_not_started_and_complete(chain):
    assert states(chain) == ["not_started"] * 3
    r = rc.resume(chain["scen"], switch_dir=chain["sw"], log=lambda m: None)
    assert r["start"] == "2028" and r["remaining_file"].read_text().count("\n") == 3
    for y in STAGES:
        solve(chain, y)
    assert states(chain) == ["complete"] * 3
    assert rc.resume(chain["scen"], switch_dir=chain["sw"], log=lambda m: None)["start"] is None


def test_interrupted_in_the_second_stage(chain):
    """2028 solved and handed over; the VM stopped during 2030's post-solve: total_cost.txt written, but no handover
    to 2035 (prepare_next_stage, the last step, never ran) and one chained file half-written."""
    solve(chain, "2028")
    solve(chain, "2030", handover=False)
    (_inp(chain, "2035") / f"gen_build_predetermined.chained.{CASE}.csv").write_text("partial")
    t = (_out(chain, "2030") / "total_cost.txt").stat().st_mtime - 60
    os.utime(_inp(chain, "2035") / f"gen_build_predetermined.chained.{CASE}.csv", (t, t))
    _, st = rc.chain_status(chain["scen"], chain["sw"])
    assert [s["state"] for s in st] == ["complete", "partial", "not_started"]
    assert any("handover missing: 2035 has no gen_build_costs.chained.C.csv" in p for p in st[1]["problems"])
    assert any("handover stale" in p for p in st[1]["problems"])
    # it won't start at 2035 (2030 isn't complete), nor go on over the interrupted run's files without moving them
    with pytest.raises(RuntimeError, match="can't start at 2035: 2030 is partial"):
        rc.resume(chain["scen"], "2035", move_partial=True, switch_dir=chain["sw"], log=lambda m: None)
    with pytest.raises(RuntimeError, match="--move-partial"):
        rc.resume(chain["scen"], switch_dir=chain["sw"], log=lambda m: None)
    dry = rc.resume(chain["scen"], dry_run=True, switch_dir=chain["sw"], log=lambda m: None)
    assert dry["start"] == "2030" and not dry["moved"] and _out(chain, "2030").exists()
    r = rc.resume(chain["scen"], move_partial=True, switch_dir=chain["sw"], log=lambda m: None)
    assert r["start"] == "2030" and r["complete"] == ["2028"]
    # moved, not deleted: 2030's outputs and 2035's chained inputs; 2030's own chained inputs (from 2028) kept
    assert not any(_out(chain, "2030").rglob("*.*")) and Path(r["moved_to"], "outputs/2030/total_cost.txt").exists()
    assert Path(r["moved_to"], f"inputs/2035/gen_build_predetermined.chained.{CASE}.csv").read_text() == "partial"
    assert not list(_inp(chain, "2035").glob("*.chained.*"))
    assert all((_inp(chain, "2030") / f"{f}.chained.{CASE}.csv").exists() for f in CHAINED)
    assert (_out(chain, "2028") / "total_cost.txt").exists()                     # completed stages untouched
    assert r["remaining_file"].name == "scenarios_C.fixed.from_2030.txt"
    assert [ln.split()[1] for ln in r["remaining_file"].read_text().splitlines()] == ["C_2030", "C_2035"]
    runs = pd.read_csv(chain["sw"] / "out/r/handoff_runs.csv")
    assert runs.action.tolist() == ["resume"] and runs.stage.astype(str).tolist() == ["2030"]
    assert built_unchanged(chain)                                                 # the built inputs: never written


def test_solver_status_and_inputs(chain):
    for y in STAGES[:2]:
        solve(chain, y)
    solve(chain, "2035", log="maxTimeLimit")
    _, st = rc.chain_status(chain["scen"], chain["sw"])
    assert st[2]["state"] == "partial" and "termination condition maxTimeLimit" in st[2]["problems"][0]
    # recorded stage whose inputs changed since: not complete
    stages = cr.read_chain(chain["scen"], chain["sw"])
    (_out(chain, "2028") / cr.PROVENANCE).write_text(json.dumps({"git_head": "abc", "input_digest":
                                                                 cr.digest(cr.fingerprint(stages[0]))}))
    assert rc.chain_status(chain["scen"], chain["sw"])[1][0]["state"] == "complete"
    (_inp(chain, "2028") / "gen_info.csv").write_text("changed\n")
    assert "inputs changed since" in rc.chain_status(chain["scen"], chain["sw"])[1][0]["problems"][0]


def test_refuses_rebuilt_inputs(chain):
    """--inputs-not-after: the inputs must be the ones built and checked before the chain started."""
    solve(chain, "2028")
    built_at = datetime.now(timezone.utc)
    later = time.time() + 3600
    os.utime(_inp(chain, "2035") / "gen_info.csv", (later, later))             # a rebuild after the checks
    with pytest.raises(RuntimeError, match="built input files changed after"):
        rc.resume(chain["scen"], dry_run=True, switch_dir=chain["sw"], log=lambda m: None, inputs_not_after=built_at)
    r = rc.resume(chain["scen"], dry_run=True, switch_dir=chain["sw"], log=lambda m: None,
                  inputs_not_after=built_at + timedelta(hours=2))
    assert r["start"] == "2030"
    assert rc.parse_utc("2026-10-14T02:40Z") == datetime(2026, 10, 14, 2, 40, tzinfo=timezone.utc)


def test_cli(chain, capsys):
    solve(chain, "2028")
    assert rc.main(["status", str(chain["scen"]), "--switch-dir", str(chain["sw"])]) == 0
    out = capsys.readouterr().out
    assert "2028: COMPLETE" in out and "termination optimal" in out and "2030: NOT_STARTED" in out
    assert rc.main(["resume", str(chain["scen"]), "--switch-dir", str(chain["sw"])]) == 0
    assert "resume at 2030" in capsys.readouterr().out
