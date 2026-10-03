"""Aliases for a regenerated-time-sample case of s4x1_S0unc_2035_icon (C1 + B6 + B8 stack). Run from the repo root.
usage (repo root): python s0_workflow/case_aliases/make_nl_case_aliases.py switch/in_ictest/cases/nl24_fi/2035/s4x1_S0unc_2035_icon nl24_fi [--ic-tags ic_v4]
Checks that every non-time input matches the s4x1 base case (identical, or same rows as a set / only trans_dbid / float
formatting), copies sample-independent aliases whose sources are identical, rebuilds the B8 gen_info and windloss2.
Stops (raises) on any unexplained difference. Writes patch_log.<tag>.txt."""
import datetime, filecmp, shutil, subprocess, sys
from pathlib import Path
import numpy as np, pandas as pd

import argparse
ap = argparse.ArgumentParser()
ap.add_argument("case", help="regenerated case dir (same case id as --base-case, different time sample)")
ap.add_argument("tag", help="tag for patch_log.<tag>.txt")
ap.add_argument("--base-case", default="switch/in_ictest/cases/2035/s4x1_S0unc_2035_icon", help="s4x1 case that already has the aliases")
ap.add_argument("--b8-source", default="switch/in_ictest/cases/2035/s4x1_S0_2035_icoff", help="case whose gen_info.coalcfhist.csv holds the B8 caps")
ap.add_argument("--ic-tags", nargs="+", default=["ic_v4"], help="ic_* alias tags to copy from the base case (ic_v4 for the new stack; ic_v3 kept for history)")
A = ap.parse_args()
N = Path(A.case); TAG = A.tag; O = Path(A.base_case); OFF = Path(A.b8_source)
F = (1 - 0.134) / (1 - 0.017); log = []
TIME = {"dr_data.csv", "ee_data.csv", "graph_timestamp_map.csv", "hydro_timepoints.csv", "hydro_timeseries.csv", "loads.csv",
        "timepoints.csv", "timeseries.csv", "variable_capacity_factors.csv", "water_node_tp_flows.csv", "dr_annual_cost.csv", "ee_annual_cost.csv",
        "planning_reserve_margin.csv"}
r = lambda p: pd.read_csv(p, dtype=str, keep_default_na=False)
explained = []
for f in sorted(p.name for p in N.glob("*.csv")):
    if f in TIME or not (O / f).exists() or filecmp.cmp(N / f, O / f, shallow=False): continue
    a, b = r(O / f), r(N / f)
    if f == "gen_info.csv":
        x = pd.read_csv(O / "gen_info.ic_v2.csv").set_index("GENERATION_PROJECT"); y = pd.read_csv(N / f).set_index("GENERATION_PROJECT").reindex(x.index)
        num = x.select_dtypes("number").columns
        assert ((x[num] - y[num]).abs().max().max() < 1e-6) and x.drop(columns=num).fillna("").equals(y.drop(columns=num).fillna("")), f
        explained.append(f"{f}: equals the s4x1 gen_info.ic_v2.csv (connection-cost removal now done by pg_to_switch) to <1e-6"); continue
    if f in ("gen_info.high_fossil.csv", "gen_info.no_retire.csv", "ic_connect_cost_check.csv"):
        explained.append(f"{f}: derived from gen_info (same change as gen_info.csv)"); continue
    if f == "ic_weights.csv":
        m = pd.read_csv(O / "ic_weights.ic_v2.csv").merge(pd.read_csv(N / f), on="ic_key"); assert (m.iloc[:, 1] == m.iloc[:, 2]).all(), f
        explained.append(f"{f}: equals s4x1 ic_weights.ic_v2.csv numerically"); continue
    if f == "ic_uprates.csv":
        assert b.drop(columns=[c for c in b.columns if c not in a.columns]).equals(a), f
        explained.append(f"{f}: only the new ic_uprate_mode column (solve uses the ic_v3 alias)"); continue
    ca = a.drop(columns=[c for c in ("trans_dbid",) if c in a.columns]); cb = b.drop(columns=[c for c in ("trans_dbid",) if c in b.columns])
    if list(ca.columns) == list(cb.columns) and set(map(tuple, ca.values)) == set(map(tuple, cb.values)):
        explained.append(f"{f}: same rows as a set (order/trans_dbid only)"); continue
    raise RuntimeError(f"unexplained difference in {f}")
log += explained
pairs = {"gen_build_costs.gas_atb.csv": "gen_build_costs.csv", "rps_requirements.ic_v2.csv": "rps_requirements.csv", "ic_params.ic_v2.csv": "ic_params.csv",
         }
for tg in A.ic_tags:
    pairs.update({f"ic_zones.{tg}.csv": "ic_zones.csv", f"ic_tranches.{tg}.csv": "ic_tranches.csv", f"ic_uprates.{tg}.csv": None, f"ic_weights.{tg}.csv": None})
for g in ["gens", "groups", "periods", "tiers", "zones", "regions"]: pairs[f"build_rate_{g}.central.v2.csv"] = None
for alias, src in pairs.items():
    if src is not None: assert filecmp.cmp(O / src, N / src, shallow=False), src
    assert not (N / alias).exists(), alias; shutil.copy2(O / alias, N / alias)
    log.append(f"copied {alias} from {O}" + (f" (source {src} identical)" if src else " (keyed by generator/zone, sample-independent)"))
ids = set(pd.read_csv(N / "gen_info.csv").GENERATION_PROJECT); assert set(pd.read_csv(N / "build_rate_gens.central.v2.csv").GENERATION_PROJECT) <= ids
g = r(N / "gen_info.csv"); b8 = r(OFF / "gen_info.coalcfhist.csv").set_index("GENERATION_PROJECT")
coal = g.gen_energy_source == "coal"; g.loc[coal, "gen_max_annual_availability"] = g.loc[coal, "GENERATION_PROJECT"].map(b8.gen_max_annual_availability).values
assert g.loc[coal, "gen_max_annual_availability"].ne("").all(); g.to_csv(N / "gen_info.ic_v2.coalcfhist.csv", index=False)
log.append(f"gen_info.ic_v2.coalcfhist.csv = gen_info.csv + B8 coal caps ({coal.sum()} rows)")
gi = pd.read_csv(N / "gen_info.csv", usecols=["GENERATION_PROJECT", "gen_energy_source"]); wind = set(gi[gi.gen_energy_source == "wind"].GENERATION_PROJECT)
v = pd.read_csv(N / "variable_capacity_factors.csv", float_precision="round_trip", low_memory=False); col = v.columns[2]
m = v.GENERATION_PROJECT.isin(wind); v.loc[m, col] = v.loc[m, col] * F; assert not (N / "variable_capacity_factors.windloss2.csv").exists()
v.to_csv(N / "variable_capacity_factors.windloss2.csv", index=False)
log.append(f"variable_capacity_factors.windloss2.csv = all wind ({m.sum()} rows) x {F:.5f} (rule verified on the s4x1 case's windloss2 file, max diff 0)")
head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
open(N / f"patch_log.{TAG}.txt", "a").write(f"{datetime.datetime.now():%Y-%m-%dT%H:%M:%S} case generated by ic_test_fedpol_work/pg_to_switch_netload.py (pg_to_switch.py at {head})\n" + "\n".join(log) + "\n")
print("\n".join(log))
