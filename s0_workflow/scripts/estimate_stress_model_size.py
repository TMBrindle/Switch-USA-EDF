"""Expected model size of an S0 stage under the full and light stress-day formulations (CHANGES §69), before a VM run.

Builds (does not solve) a real case with the S0 operating modules (switch/modules.txt with the regional reserve in
place of the per-zone reserve modules, --spinning-requirement-rule 3+5, --retire early), once per formulation, and
counts variables and constraints by the timepoint they belong to: sample (weighted) timepoints, zero-weight stress
timepoints, and no timepoint (capacity, period and policy totals). Per-timepoint rates from one period then scale to
the target stage:

    total(stage) = no-timepoint count + sample rate x sample timepoints + stress rate x stress timepoints

Default case: switch/in/foresight/s4x1_fedpol_current (committed; s4x1 stack: 2,818 generators, 134 zones, 313 lines;
3 periods of 4 sample days + 1 zero-weight stress day each). Its stress days get synthetic regional-reserve inputs
(prm_zones, regions and margins, credits by class, availability 0.9 on capacity-credit units) so prm_regional builds;
sizes don't depend on those values. The target defaults to the 2035 stage of s4x1_S0prod_2035_new: 24 sample days +
the peak day (600 timepoints) and 15 stress days (360).

Memory: the VM measured 114 GB peak for the full formulation at 960 timepoints; the light estimate scales that by the
ratio of (variables + constraints), and also gives the full-formulation rate implied by it.

usage (repo root): python s0_workflow/scripts/estimate_stress_model_size.py [--case DIR] [--period 2035]
                   [--sample-tps 600] [--stress-tps 360] [--measured-gb 114]
Writes s0_workflow/data/stress_model_size.csv (component counts) and prints the summary.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

BUILD = r'''
import json, sys
from collections import defaultdict
from pyomo.environ import Var, Constraint
from switch_model import solve
inputs, out = sys.argv[1], sys.argv[2]
args = ["--inputs-dir", inputs, "--outputs-dir", inputs + "_out", "--retire", "early",
        "--spinning-requirement-rule", "3+5", "--no-warn-on-extra-capacity-factors",
        "--exclude-module", "study_modules.planning_reserves", "--exclude-module", "study_modules.planning_reserves_extreme_days",
        "--exclude-module", "study_modules.copy_inputs_to_outputs",
        "--include-module", "study_modules.prm_regional"]
m = solve.main(args=args, return_instance=True)
tps = set(m.TIMEPOINTS)
stress = {t for t in tps if str(m.tp_ts[t]).endswith("_prm")}
per = {t: int(m.tp_period[t]) for t in tps}
rows = defaultdict(int)
for kind, label in ((Var, "var"), (Constraint, "con")):
    for c in m.component_objects(kind, active=True):
        for idx in c:
            ii = idx if isinstance(idx, tuple) else (idx,)
            t = next((x for x in ii if x in tps), None)
            cls = "none" if t is None else ("stress" if t in stress else "sample")
            rows[(label, c.name, cls, per.get(t, 0))] += 1
# reserve rows (Prm_ constraints): nonzeros in rows indexed by a stress timepoint ("hourly") and in the rest (the
# compact form's once-per-group definitions and links, the transmission-capacity rows), and per column the number of
# rows it appears in (fixed variables, e.g. existing builds, are constants)
from collections import Counter
from pyomo.repn import generate_standard_repn
prm = {"hourly_nnz": Counter(), "once_nnz": 0, "max_row_nnz": 0, "groups": len(getattr(m, "PRM_ACC_GROUPS", []))}
cols = Counter()
for c in m.component_objects(Constraint, active=True):
    if not c.name.startswith("Prm_"):
        continue
    for idx in c:
        ii = idx if isinstance(idx, tuple) else (idx,)
        t = next((x for x in ii if x in tps), None)
        vs = {id(v): v for v in generate_standard_repn(c[idx].body, quadratic=False).linear_vars}
        if t is None:
            prm["once_nnz"] += len(vs)
        else:
            prm["hourly_nnz"][str(per[t])] += len(vs)
            prm["max_row_nnz"] = max(prm["max_row_nnz"], len(vs))
        for v in vs.values():
            cols[v.name] += 1
prm["col_rows"] = sorted(Counter(cols.values()).items())     # [rows per column, number of columns]
json.dump({"rows": [list(k) + [v] for k, v in rows.items()], "prm": prm,
           "tps": {"sample": {str(p): sum(1 for t in tps if t not in stress and per[t] == p) for p in set(per.values())},
                   "stress": {str(p): sum(1 for t in stress if per[t] == p) for p in set(per.values())}},
           "light_tps": len(getattr(m, "LIGHT_TPS", []))}, open(out, "w"))
'''


def prepare(case: Path, dst: Path, light: bool, compact: bool = False) -> Path:
    from s0_workflow import prm
    shutil.copytree(case, dst)
    gi = pd.read_csv(dst / "gen_info.csv", na_values=".")
    tsr = pd.read_csv(dst / "timeseries.csv")
    tp = pd.read_csv(dst / "timepoints.csv")
    lz = pd.read_csv(dst / "load_zones.csv")
    per = pd.read_csv(dst / "periods.csv")
    stress = tsr[tsr.timeseries.astype(str).str.endswith("_prm")].timeseries
    regions = prm.zone_regions(list(lz.LOAD_ZONE))
    pd.DataFrame({"LOAD_ZONE": list(regions), "PRM_REGION": list(regions.values())}).to_csv(dst / "prm_zones.csv", index=False)
    pd.DataFrame([{"PRM_REGION": r, "PERIOD": p, "prm_margin": 0.1, "prm_import_share": 0.1}
                  for r in sorted(set(regions.values())) for p in per.INVESTMENT_PERIOD]).to_csv(
        dst / "prm_region_periods.csv", index=False)
    pd.DataFrame({"TIMESERIES": stress}).to_csv(dst / "prm_timeseries.csv", index=False)
    cl = prm.classify(gi)
    cl[["GENERATION_PROJECT", "prm_credit", "prm_class"]].to_csv(dst / "prm_gen_credit.csv", index=False)
    cap = cl[cl.prm_credit == "capacity"].GENERATION_PROJECT
    st = tp[tp.timeseries.isin(set(stress))].timepoint_id
    pd.DataFrame([(g, t, 0.9) for t in st for g in cap], columns=["GENERATION_PROJECT", "TIMEPOINT", "prm_avail_frac"]
                 ).to_csv(dst / "prm_gen_availability.csv", index=False)
    pd.DataFrame({"prm_new_tx_derate": [0.15], "prm_shortfall_cost_per_mw_yr": [271.79], "prm_import_cap_all_hours": [1],
                  "prm_compact_capacity": [int(compact)]}).to_csv(dst / "prm_params.csv", index=False)
    if light:
        pd.DataFrame({"TIMESERIES": stress}).to_csv(dst / "stress_light_timeseries.csv", index=False)
    return dst


BUILDS = {"full": (False, False), "light": (True, False), "full_compact": (False, True), "light_compact": (True, True)}


def build(case: Path, name: str, work: Path) -> dict:
    light, compact = BUILDS[name]
    d = prepare(case, work / name, light, compact)
    out = work / f"{d.name}.json"
    r = subprocess.run([sys.executable, "-c", BUILD, str(d), str(out)], cwd=REPO / "switch", capture_output=True,
                       text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-4000:] + r.stdout[-2000:])
    return json.load(open(out))


def summarise(res: dict, period: int, sample_tps: int, stress_tps: int, measured_gb: float) -> tuple[pd.DataFrame, dict]:
    rows = []
    for form, r in res.items():
        df = pd.DataFrame(r["rows"], columns=["kind", "component", "tp_class", "period", "n"])
        df["formulation"] = form
        rows.append(df)
    df = pd.concat(rows, ignore_index=True)
    out = {}
    for form, r in res.items():
        d = df[df.formulation == form]
        ns, nst = r["tps"]["sample"][str(period)], r["tps"]["stress"][str(period)]
        o = {}
        for kind in ("var", "con"):
            k = d[d.kind == kind]
            none = int(k[k.tp_class == "none"].n.sum())
            a = k[(k.tp_class == "sample") & (k.period == period)].n.sum() / ns
            b = k[(k.tp_class == "stress") & (k.period == period)].n.sum() / nst if nst else 0.0
            o[kind] = {"none": none, "per_sample_tp": round(a, 1), "per_stress_tp": round(b, 1),
                       "stage_total": round(none + a * sample_tps + b * stress_tps),
                       "stage_on_stress": round(b * stress_tps)}
        out[form] = o
    tot = {f: out[f]["var"]["stage_total"] + out[f]["con"]["stage_total"] for f in out}
    if "full" in tot:
        out["memory_gb"] = {f: round(measured_gb * tot[f] / tot["full"], 1) for f in tot}
        out["memory_gb_basis"] = (f"{measured_gb} GB measured (VM, full, {sample_tps}+{stress_tps} timepoints) x "
                                  f"(variables + constraints) / full's")
    return df, out


def reserve_rows(res: dict, period: int, stress_tps: int, seasons: int) -> dict:
    """Reserve-row nonzeros and dense columns scaled to the target's stress hours. Hourly rows scale with stress hours;
    the compact form's once-per-group rows with zones x derate groups (seasons). In the hourly form each capacity-credit
    unit's new-build column and the zone's shortfall appear in every stress-hour row of the zone (rows = stress hours of
    the period); in the compact form only the group's accredited-capacity and shortfall columns do (rows = the group's
    hours). Columns counted as dense: in at least the period's stress hours of the built case (the case has one stress
    day, one group, per period)."""
    out = {}
    for name, r in res.items():
        pr = r["prm"]
        nst = r["tps"]["stress"][str(period)]
        per_tp = pr["hourly_nnz"].get(str(period), 0) / nst
        nper = len(pr["hourly_nnz"])
        dense = sum(n for k, n in pr["col_rows"] if k >= nst)
        compact = name.endswith("_compact")
        groups = pr["groups"] / nper if nper else 0         # per period in the built case (one group per zone)
        once = pr["once_nnz"] / nper if nper else 0
        o = {"hourly_nnz_per_stress_tp": round(per_tp, 1), "max_hourly_row_nnz": pr["max_row_nnz"],
             "stage_hourly_nnz": round(per_tp * stress_tps),
             "dense_columns_per_period": round(dense / nper) if nper else 0}
        if compact:
            o["groups_per_period_target"] = round(groups * seasons)
            o["stage_once_nnz"] = round(once * seasons)   # definitions and links scale with groups
            o["dense_column_rows_target"] = round(stress_tps / seasons)
        else:
            o["stage_once_nnz"] = round(once)
            o["dense_column_rows_target"] = stress_tps
        o["stage_prm_nnz"] = o["stage_hourly_nnz"] + o["stage_once_nnz"]
        o["fill_proxy"] = o["dense_columns_per_period"] * (seasons if compact else 1) * o["dense_column_rows_target"] ** 2
        out[name] = o
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", type=Path, default=REPO / "switch/in/foresight/s4x1_fedpol_current")
    ap.add_argument("--period", type=int, default=2035)
    ap.add_argument("--sample-tps", type=int, default=600)
    ap.add_argument("--stress-tps", type=int, default=360)
    ap.add_argument("--measured-gb", type=float, default=114.0)
    ap.add_argument("--out", type=Path, default=REPO / "s0_workflow/data/stress_model_size.csv")
    ap.add_argument("--builds", default="full,light,full_compact",
                    help=f"comma-separated, from {list(BUILDS)}; one at a time (each about 13 GB, 16 min here)")
    ap.add_argument("--seasons", type=int, default=3, help="derate groups per zone and period at the target (compact)")
    a = ap.parse_args(argv)
    res = {}
    with tempfile.TemporaryDirectory() as w:
        for b in a.builds.split(","):
            res[b] = build(a.case, b, Path(w))
            shutil.rmtree(Path(w) / b, ignore_errors=True)
    df, out = summarise(res, a.period, a.sample_tps, a.stress_tps, a.measured_gb)
    out["reserve_rows"] = reserve_rows(res, a.period, a.stress_tps, a.seasons)
    df.groupby(["formulation", "kind", "component", "tp_class"]).n.sum().reset_index().to_csv(a.out, index=False)
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
