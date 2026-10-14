"""Resume a mode-A chain after an interruption: verify each stage, then write the scenarios file from the first stage
that must be solved (CHANGES §243). run_chain_A.py, the VM's chain runner, is not in this repo; this tool runs before
it, as reuse_chain_stages.py does, and writes `scenarios_<case>.from_<stage>.txt` for it.

  # read only: what each stage's folders show
  python s0_workflow/scripts/resume_chain.py status switch/in/s0prod_A_v32/scenarios_S0prod_A.fixed.txt

  # the resume plan, then (without --dry-run) move partial outputs aside and write the remaining scenarios file
  python s0_workflow/scripts/resume_chain.py resume switch/in/s0prod_A_v32/scenarios_S0prod_A.fixed.txt \\
      [--from-stage 2035] [--move-partial] [--dry-run]

A stage is COMPLETE when all of these hold:
  1. its outputs folder has model_config.json (Switch writes it once the solver returns a solution; an infeasible or
     failed solve raises before it) and total_cost.txt with a number (written by switch_model.reporting after the
     solve);
  2. the results the chain carries forward are there: BuildGen.csv, plus, for a stage with a next stage,
     BuildStorageEnergy.csv, SuspendGen.csv and BuildTx.csv (prepare_next_stage reads them);
  3. for a stage with a next stage: every `*.chained.<case>.csv` file the next stage's scenario line aliases is in
     the next stage's inputs folder and was written after this stage's total_cost.txt. prepare_next_stage
     (`--include-module`, the last post-solve step) writes them: built and retired capacity (gen_build_predetermined),
     costs (gen_build_costs), transmission (transmission_lines, trans_built_to_date, forced-line minimums), the
     interconnection headroom state (ic_*) and the build-rate history (build_rate_prev_build, from the solved model's
     values, so it can't be re-derived from outputs: a stage whose handover is missing must be solved again);
  4. if a solver log is in the outputs folder, its termination condition is optimal;
  5. if chain_provenance.json is there (reuse_chain_stages.py record), the inputs still match its digest.

A stage with any output file but not complete is PARTIAL; a stage with no outputs is NOT STARTED.

resume:
  - starts at --from-stage, or by default at the first stage that is not complete;
  - refuses to start at a stage unless every earlier stage is complete;
  - refuses while the start stage or any later stage has outputs, or a later stage has chained inputs, unless
    --move-partial. Those files came from the interrupted run, and the restarted stages write them again.
    --move-partial moves them (never deletes) to <outputs root>/_resume_partial_<UTC time>/{outputs,inputs}/<stage>/.
    The start stage's own chained inputs came from its complete predecessor and are kept.
  - never writes a stage's built inputs, and with --inputs-not-after <UTC> refuses if any built (non-chained) input
    file changed after that time (the build that the pre-solve checks passed);
  - writes scenarios_<case>.from_<stage>.txt next to the scenarios file, and a `resume` row in handoff_runs.csv
    (outputs root).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import chain_reuse as cr  # noqa: E402

CARRIED = ("BuildGen.csv", "BuildStorageEnergy.csv", "SuspendGen.csv", "BuildTx.csv")
MTIME_SLACK_S = 2.0          # file-system timestamp resolution (FAT: 2 s)
TERMINATION = re.compile(r"termination condition was (\w+)", re.I)


def _chained_targets(nxt: dict) -> list[str]:
    return sorted(v for v in nxt["aliases"].values() if ".chained." in v)


def _outputs_present(st: dict) -> list[Path]:
    d = st["outputs"]
    return sorted(p for p in d.rglob("*") if p.is_file()) if d.exists() else []


def stage_status(st: dict, nxt: dict | None) -> dict:
    """{stage, state: complete | partial | not_started, problems: [...], notes: [...]} (module docstring)."""
    out, probs, notes = st["outputs"], [], []
    files = _outputs_present(st)
    if not files:
        return {"stage": st["stage"], "state": "not_started", "problems": ["no outputs"], "notes": []}
    tc = out / "total_cost.txt"
    if not (out / "model_config.json").exists():
        probs.append("no model_config.json (the solve did not finish)")
    if not tc.exists():
        probs.append("no total_cost.txt (post-solve reporting did not run)")
    else:
        try:
            notes.append(f"total cost {float(tc.read_text().split()[0]):,.6g}")
        except (ValueError, IndexError):
            probs.append("total_cost.txt is empty or not a number")
    for f in CARRIED if nxt is not None else CARRIED[:1]:
        if not (out / f).exists():
            probs.append(f"no {f}")
    for log in [p for p in files if p.suffix in (".log", ".txt") and p.name != "total_cost.txt"]:
        m = TERMINATION.findall(log.read_text(errors="ignore"))
        if m:
            notes.append(f"{log.name}: termination {m[-1]}")
            if m[-1].lower() != "optimal":
                probs.append(f"{log.name}: termination condition {m[-1]}, not optimal")
    if nxt is not None and tc.exists():
        t0 = tc.stat().st_mtime
        for name in _chained_targets(nxt):
            p = nxt["inputs"] / name
            if not p.exists():
                probs.append(f"handover missing: {nxt['stage']} has no {name}")
            elif p.stat().st_mtime + MTIME_SLACK_S < t0:
                probs.append(f"handover stale: {nxt['stage']}/{name} is older than this stage's total_cost.txt")
    prov = out / cr.PROVENANCE
    if prov.exists():
        rec = json.loads(prov.read_text())
        if rec.get("input_digest") and rec["input_digest"] != cr.digest(cr.fingerprint(st)):
            probs.append(f"inputs changed since {cr.PROVENANCE} was recorded")
        else:
            notes.append(f"{cr.PROVENANCE}: head {str(rec.get('git_head'))[:10]}")
    return {"stage": st["stage"], "state": "partial" if probs else "complete", "problems": probs, "notes": notes}


def chain_status(scenarios_file: Path, switch_dir: Path = cr.SWITCH_DIR) -> tuple[list[dict], list[dict]]:
    chain = cr.read_chain(scenarios_file, switch_dir)
    return chain, [stage_status(st, chain[i + 1] if i + 1 < len(chain) else None) for i, st in enumerate(chain)]


def built_inputs_after(chain: list[dict], when: datetime) -> list[Path]:
    """Built (non-chained) input files of any stage modified after `when`: the inputs must be the ones built and
    checked before the chain started; this tool never writes them, and neither should anything else."""
    t = when.timestamp()
    return [f for st in chain if st["inputs"].exists() for f in sorted(st["inputs"].iterdir())
            if f.is_file() and ".chained." not in f.name and f.stat().st_mtime > t + MTIME_SLACK_S]


def parse_utc(text: str) -> datetime:
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def partial_files(chain: list[dict], start: int) -> list[tuple[str, Path]]:
    """(kind, path) of what the interrupted run left from the start stage on: outputs of the start stage and later,
    and chained inputs of the stages after it."""
    out = []
    for i, st in enumerate(chain[start:], start):
        out += [("outputs", p) for p in _outputs_present(st)]
        if i > start and st["inputs"].exists():
            out += [("inputs", p) for p in sorted(st["inputs"].glob(f"*.chained.{st['case']}.*"))]
    return out


def resume(scenarios_file: Path, from_stage: str | None = None, move_partial: bool = False, dry_run: bool = False,
           switch_dir: Path = cr.SWITCH_DIR, log=print, inputs_not_after: datetime | None = None) -> dict:
    chain, status = chain_status(scenarios_file, switch_dir)
    names = [st["stage"] for st in chain]
    if inputs_not_after is not None:
        late = built_inputs_after(chain, inputs_not_after)
        if late:
            raise RuntimeError(f"{len(late)} built input files changed after {inputs_not_after.isoformat()} (first: "
                               f"{late[0]}); the chain's inputs must be the ones built and checked. Stop and report.")
    if from_stage is None:
        start = next((i for i, s in enumerate(status) if s["state"] != "complete"), None)
        if start is None:
            return {"start": None, "reason": "every stage is complete", "remaining_file": None, "moved": []}
    else:
        if str(from_stage) not in names:
            raise ValueError(f"--from-stage {from_stage}: the chain's stages are {names}")
        start = names.index(str(from_stage))
    bad = [s for s in status[:start] if s["state"] != "complete"]
    if bad:
        raise RuntimeError(f"can't start at {names[start]}: {bad[0]['stage']} is {bad[0]['state']} "
                           f"({'; '.join(bad[0]['problems'])}). Resume from {bad[0]['stage']} or earlier.")
    left = partial_files(chain, start)
    if left and not move_partial and not dry_run:
        raise RuntimeError(f"{len(left)} files from the interrupted run at or after {names[start]} (first: "
                           f"{left[0][1]}). Re-run with --move-partial to move them aside (nothing is deleted).")
    res = {"start": names[start], "reason": f"{names[start]} is {status[start]['state']}"
           + (f" ({'; '.join(status[start]['problems'])})" if status[start]["problems"] else ""),
           "complete": [s["stage"] for s in status[:start]], "partial_files": [str(p) for _, p in left], "moved": [],
           "remaining_file": None}
    if dry_run:
        log(f"would resume at {names[start]}; {len(left)} partial files to move")
        return res
    root_out = chain[0]["outputs"].parents[1]
    if left:
        dest = root_out / f"_resume_partial_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        for kind, p in left:
            st = next(s for s in chain if p.is_relative_to(s[kind]))
            t = dest / kind / st["stage"] / p.relative_to(st[kind])
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(t))
            res["moved"].append(str(t))
        res["moved_to"] = str(dest)
        log(f"moved {len(left)} partial files to {dest}")
    rem = Path(scenarios_file).with_name(f"{Path(scenarios_file).stem}.from_{names[start]}.txt")
    rem.write_text("".join(st["line"] + "\n" for st in chain[start:]))
    res["remaining_file"] = rem
    root_out.mkdir(parents=True, exist_ok=True)
    cr._append_runs(root_out / "handoff_runs.csv", [{
        "scenario": chain[start]["case"], "stage": names[start], "action": "resume", "reference": "",
        "reference_outputs": "", "input_digest": "", "git_head": cr._git("rev-parse", "HEAD"),
        "reference_git_head": "", "solver_version": "", "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": res["reason"] + (f"; partial files moved to {res.get('moved_to')}" if left else "")}])
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="read only: each stage complete, partial or not started, and why")
    rs = sub.add_parser("resume", help="verify, move partial outputs aside, write the remaining scenarios file")
    rs.add_argument("--from-stage")
    rs.add_argument("--move-partial", action="store_true")
    rs.add_argument("--dry-run", action="store_true")
    rs.add_argument("--inputs-not-after", type=parse_utc,
                    help="UTC time the inputs were built and checked (e.g. 2026-10-14T02:40Z): refuse if any built "
                         "(non-chained) input file is newer")
    for p in (st, rs):
        p.add_argument("scenarios", type=Path)
        p.add_argument("--switch-dir", type=Path, default=cr.SWITCH_DIR)
    a = ap.parse_args(argv)
    if a.cmd == "status":
        _, status = chain_status(a.scenarios, a.switch_dir)
        for s in status:
            print(f"{s['stage']}: {s['state'].upper()}" + (f" — {'; '.join(s['problems'])}" if s["problems"] and
                                                          s["state"] != "not_started" else "")
                  + (f" [{'; '.join(s['notes'])}]" if s["notes"] else ""))
        return 0
    r = resume(a.scenarios, a.from_stage, a.move_partial, a.dry_run, a.switch_dir, inputs_not_after=a.inputs_not_after)
    if r["start"] is None:
        print("every stage is complete: nothing to resume")
        return 0
    print(f"complete: {', '.join(r['complete']) or 'none'}; resume at {r['start']} ({r['reason']})")
    if r["remaining_file"]:
        print(f"run_chain_A.py: {r['remaining_file']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
