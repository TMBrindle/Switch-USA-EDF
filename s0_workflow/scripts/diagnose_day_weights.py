"""Re-run the elastic diagnosis of a failed day-weights LP (diagnostic; nothing in the build reads this; CHANGES §139).

  python s0_workflow/scripts/diagnose_day_weights.py <case>/time_sampling/<year>/fi_failed_lp.npz
      [--relax 2.25] [--drop-tail <substring>] [--drop-target <substring>] [--tol-scale <substring>=<x>]
      [--w-min <days>] [--out fi_elastic_slack_variant.csv]

The build writes fi_failed_lp.npz when the fleet-independent weights LP has no solution even after its relaxation
steps (day_selection.select_days). This script reloads it and solves the elastic LP again, by default as the build did
(every family elastic, then each family alone: target, tail, w_min, weight_sum). The options test what-ifs without
rebuilding: drop a tail or a target, scale the tolerances of the targets whose name contains a substring, change the
relaxation or w_min. Each option is a what-if on the same candidate days; none of them is a fix by itself.
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from s0_workflow import day_selection as ds  # noqa: E402


def load(path: Path) -> dict:
    z = np.load(path)
    lp = {k: z[k] for k in z.files}
    lp["names"] = [str(x) for x in lp["names"]]
    lp["tails"] = [(str(n), lp["tail_coef"][k], float(lp["tail_const"][k]), float(lp["tail_lo"][k]),
                    float(lp["tail_hi"][k])) for k, n in enumerate(lp["tail_names"])]
    lp["slots"] = [str(x) for x in lp["slots"]]
    for k in ("total", "w_min", "relax"):
        lp[k] = float(lp[k])
    return lp


def variant(lp: dict, relax=None, drop_tail=(), drop_target=(), tol_scale=(), w_min=None) -> dict:
    v = dict(lp)
    keep = [i for i, n in enumerate(lp["names"]) if not any(s in n for s in drop_target)]
    v["A"], v["b"], v["t"] = lp["A"][keep], lp["b"][keep], lp["t"][keep].astype(float).copy()
    v["names"] = [lp["names"][i] for i in keep]
    for sub, x in tol_scale:
        for i, n in enumerate(v["names"]):
            if sub in n:
                v["t"][i] *= x
    v["tails"] = [tl for tl in lp["tails"] if not any(s in tl[0] for s in drop_tail)]
    if relax is not None:
        v["relax"] = relax
    if w_min is not None:
        v["w_min"] = w_min
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("npz", type=Path)
    ap.add_argument("--relax", type=float)
    ap.add_argument("--drop-tail", action="append", default=[])
    ap.add_argument("--drop-target", action="append", default=[])
    ap.add_argument("--tol-scale", action="append", default=[], help="<substring>=<factor>")
    ap.add_argument("--w-min", type=float)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    ts = [(s.rsplit("=", 1)[0], float(s.rsplit("=", 1)[1])) for s in a.tol_scale]
    v = variant(load(a.npz), a.relax, a.drop_tail, a.drop_target, ts, a.w_min)
    tab = ds.slack_table(v["A"], v["b"], v["t"], v["names"], v["tails"], v["total"], v["w_min"], v["relax"], v["slots"])
    rng = ds.target_range(v["A"], v["b"], v["t"], v["names"], v["total"], v["relax"])
    print(f"{len(v['slots'])} candidate days, {len(v['names'])} targets, {len(v['tails'])} tails, "
          f"relax x{v['relax']:.3f}, w_min {v['w_min']:g}, total {v['total']:.3f}")
    print(ds.summarise(tab, rng))
    if a.out:
        tab.to_csv(a.out, index=False)
        print(f"wrote {a.out}")
    return tab


if __name__ == "__main__":
    main()
