#!/usr/bin/env python3
"""Check the headroom charge of tracked demands against a counterfactual solve without their headroom draw.

Runs `switch solve` twice from RUN_DIR with the given arguments: as given (outputs in OUT), and with
`--td-headroom-draw off` (outputs in OUT_nodraw). Writes OUT/tracked_demand_headroom_counterfactual.csv:

    objective_with_draw, objective_without_draw, objective_difference (NPV, base year),
    attributed_charge_npv (sum of headroom_charge_npv_usd in tracked_demand_connection_costs.csv), ratio

The attributed charge is the headroom shadow price x MW drawn (§108 (f)). It equals the objective difference when
the draw stays on one linear piece of the headroom supply curve, and understates it when the draw moves the
solution onto a dearer piece (the shadow price is marginal).

Usage (CHANGES §108):
    python tracked_demands/scripts/headroom_counterfactual.py RUN_DIR --outputs-dir OUT -- <other switch solve args>
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pandas as pd


def objective(out: Path) -> float:
    return float((out / "total_cost.txt").read_text().strip())


def main(argv=None):
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    own, rest = (argv[:argv.index("--")], argv[argv.index("--") + 1:]) if "--" in argv else (argv, [])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--outputs-dir", required=True)
    ap.add_argument("--switch", default="switch")
    a = ap.parse_args(own)
    run = Path(a.run_dir)
    out, nodraw = a.outputs_dir, a.outputs_dir.rstrip("/") + "_nodraw"
    for o, extra in ((out, []), (nodraw, ["--td-headroom-draw", "off"])):
        r = subprocess.run([a.switch, "solve", *rest, "--outputs-dir", o, *extra], cwd=run,
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"switch solve failed ({o}):\n{r.stderr[-3000:]}")
    with_draw, without = objective(run / out), objective(run / nodraw)
    costs = pd.read_csv(run / out / "tracked_demand_connection_costs.csv")
    attributed = float(costs["headroom_charge_npv_usd"].fillna(0).sum())
    diff = with_draw - without
    res = pd.DataFrame([{"objective_with_draw": with_draw, "objective_without_draw": without,
                         "objective_difference": diff, "attributed_charge_npv": attributed,
                         "ratio": attributed / diff if diff else None}])
    res.to_csv(run / out / "tracked_demand_headroom_counterfactual.csv", index=False)
    print(res.to_string(index=False))
    return res


if __name__ == "__main__":
    main()
