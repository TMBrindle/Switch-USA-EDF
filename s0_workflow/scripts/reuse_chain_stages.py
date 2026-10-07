"""Reuse unchanged leading stages of a mode-A chain from a reference chain (CHANGES §71; s0_workflow/chain_reuse.py).

run_chain_A.py lives on the VM, so this runs before it: it reuses what matches and writes the scenarios file to solve
from the first differing stage (`scenarios_<case>.from_<stage>.txt`), which run_chain_A.py then runs.

  # once, for the solved reference chain (the head and solver arguments it was solved with):
  python s0_workflow/scripts/reuse_chain_stages.py record switch/in/s0prod_A/scenarios_S0prod_A.txt \\
      --git-head <sha> --solver-args "--solver gurobi --solver-options-string '...'" [--solver-version "gurobi 12.0.3"]

  # for a scenario chain (built, not solved):
  python s0_workflow/scripts/reuse_chain_stages.py reuse switch/in/bill/scenarios_BILL_central.txt \\
      --reuse-from switch/in/s0prod_A/scenarios_S0prod_A.txt [--reuse-through 2030] \\
      --solver-args "<the same string>" [--link] [--dry-run] [--handoff-runs <csv>]

  # expected reuse from the case definitions (no builds needed):
  python s0_workflow/scripts/reuse_chain_stages.py expected [--reference S0prod_A] [--out <csv>]

Paths in scenario lines are relative to switch/ (--switch-dir).
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import chain_reuse as cr  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="write chain_provenance.json in a solved chain's stage outputs")
    rec.add_argument("scenarios")
    rec.add_argument("--git-head")
    rec.add_argument("--solver-version")
    rec.add_argument("--solver-args", default="")
    use = sub.add_parser("reuse", help="reuse matching leading stages; write the remaining scenarios file")
    use.add_argument("scenarios")
    use.add_argument("--reuse-from", required=True, help="the reference chain's scenarios file")
    use.add_argument("--reuse-through", type=int, help="reuse no stage after this year")
    use.add_argument("--solver-version")
    use.add_argument("--solver-args", default="")
    use.add_argument("--link", action="store_true", help="hard-link the reference's outputs instead of copying")
    use.add_argument("--dry-run", action="store_true", help="compare only (inputs other than chained files)")
    use.add_argument("--handoff-runs", type=Path)
    use.add_argument("--code-check", choices=["all", "model"], default="all",
                     help="code paths that must be unchanged since the reference's head: all (default: Switch "
                          "modules and input writing) or model (Switch modules, module lists, options only; inputs "
                          "are compared by content)")
    use.add_argument("--python", help="Python of the Switch environment for the handover (default: the `switch` "
                                      "command's interpreter)")
    exp = sub.add_parser("expected", help="expected reusable stages from scenario_inputs.csv")
    exp.add_argument("--reference", default="S0prod_A")
    exp.add_argument("--scenario-inputs", type=Path, default=REPO / "pg/extra_inputs/scenario_inputs.csv")
    exp.add_argument("--management", type=Path, default=REPO / "pg/settings/scenario_management.yml")
    exp.add_argument("--cases", nargs="*")
    exp.add_argument("--out", type=Path)
    for p in (rec, use):
        p.add_argument("--switch-dir", type=Path, default=REPO / "switch")
    a = ap.parse_args(argv)
    if a.cmd == "record":
        for f in cr.record(Path(a.scenarios), a.git_head, a.solver_version, a.solver_args, a.switch_dir):
            print(f"recorded {f}")
        return 0
    if a.cmd == "reuse":
        r = cr.reuse(Path(a.scenarios), Path(a.reuse_from), a.reuse_through, a.solver_args, a.solver_version, a.link,
                     a.dry_run, a.handoff_runs, a.switch_dir, python=a.python, code_scope=a.code_check)
        print(f"reused: {', '.join(r['reused']) or 'none'}; first stage to solve: {r['start'] or 'none'} ({r['reason']})")
        if r["remaining_file"]:
            print(f"run_chain_A.py: {r['remaining_file']}")
        return 0
    df = cr.expected_reuse(a.scenario_inputs, a.management, a.reference, a.cases)
    if a.out:
        df.to_csv(a.out, index=False)
    summary = (df[df.expected_reuse].groupby("case").stage.apply(lambda s: ", ".join(map(str, s)))
               .reindex(df.case.unique()).fillna("none"))
    for c, s in summary.items():
        first = df[(df.case == c) & ~df.expected_reuse].head(1)
        why = first.differences.iat[0] if len(first) else ""
        print(f"{c}: {s}" + (f"  (first difference: {why})" if why else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
