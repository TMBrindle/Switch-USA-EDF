"""
Compute Gurobi IIS for the 2025 infeasible dispatch model.
Run as: conda run -n switch-pg-reeds python run_iis.py
"""
import os, sys, glob, shutil
from pathlib import Path

_tmp = Path("D:/tmp")
_tmp.mkdir(exist_ok=True)
os.environ["TMP"] = str(_tmp)
os.environ["TEMP"] = str(_tmp)

SWITCH_DIR = Path(__file__).parent
os.chdir(str(SWITCH_DIR))
sys.path.insert(0, str(SWITCH_DIR))

LP_SAVE = _tmp / "switch_2025_saved.lp"
IIS_FILE = _tmp / "switch_2025.ilp"

import switch_model.solve as sm_solve

args = [
    "--inputs-dir", "in/2025/s4x1_icf",
    "--outputs-dir", "out/2025/s4x1_icf_iis",
    "--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_noRGGI.csv",
    "--input-alias", "rps_requirements.csv=rps_requirements_nsp.csv",
    "--input-alias", "rps_generators.csv=rps_generators_nsp.csv",
    "--input-alias", "min_cap_requirements.csv=min_cap_requirements_nsp.csv",
    "--input-alias", "min_cap_generators.csv=min_cap_generators_nsp.csv",
    "--input-alias", "max_cap_requirements.csv=max_cap_requirements_nores.csv",
    "--input-alias", "max_cap_generators.csv=max_cap_generators_nores.csv",
    "--input-alias", "trans_build_minimum.csv=trans_build_minimum_nores.csv",
    "--input-alias", "gen_info.csv=gen_info.dispatch.csv",
    "--input-alias", "planning_reserve_margin.csv=planning_reserve_margin_nores.csv",
    "--input-alias", "timeseries.csv=timeseries_dispatch.csv",
    "--input-alias", "timepoints.csv=timepoints_dispatch.csv",
    "--input-alias", "loads.csv=loads_dispatch.csv",
    "--input-alias", "variable_capacity_factors.csv=variable_capacity_factors_dispatch.csv",
    "--input-alias", "water_node_tp_flows.csv=water_node_tp_flows_dispatch.csv",
    "--input-alias", "dr_data.csv=dr_data_dispatch.csv",
    "--input-alias", "ee_data.csv=ee_data_dispatch.csv",
    "--solver-options-string", "DualReductions=0 crossover=1",
    "--keepfiles",
    "--tempdir", str(_tmp),
    "--symbolic-solver-labels",
    "--log-level", "warning",
]

print("Running Switch (expect infeasibility)...", flush=True)
try:
    sm_solve.main(args)
except Exception as e:
    print(f"Switch raised (expected): {type(e).__name__}: {e}", flush=True)

# Find the most recently modified LP file
lp_files = sorted(glob.glob(str(_tmp / "*.pyomo.lp")), key=os.path.getmtime, reverse=True)
print(f"\nLP files found in {_tmp}: {lp_files[:3]}", flush=True)

if not lp_files:
    print("ERROR: No LP file found. Exiting.", flush=True)
    sys.exit(1)

lp_path = lp_files[0]
shutil.copy2(lp_path, str(LP_SAVE))
print(f"LP copied to {LP_SAVE} ({os.path.getsize(LP_SAVE)//1024//1024} MB)", flush=True)

# Compute IIS using gurobipy
print("\nLoading LP into Gurobi for IIS computation...", flush=True)
import gurobipy as gp

env = gp.Env()
m = gp.read(str(LP_SAVE), env)
m.Params.DualReductions = 0
m.optimize()
print(f"Status: {m.Status} (2=optimal, 3=infeasible, 5=unbounded)", flush=True)

if m.Status == 3:
    print("Infeasible. Computing IIS...", flush=True)
    m.computeIIS()
    m.write(str(IIS_FILE))
    print(f"IIS written to {IIS_FILE}\n", flush=True)

    constrs = [c.ConstrName for c in m.getConstrs() if c.IISConstr]
    print(f"=== {len(constrs)} IIS Constraints ===")
    for name in constrs[:60]:
        print(f"  {name}")
    if len(constrs) > 60:
        print(f"  ... and {len(constrs)-60} more (see {IIS_FILE})")

    bounds = [(v.VarName, v.IISLB, v.IISUB) for v in m.getVars() if v.IISLB or v.IISUB]
    print(f"\n=== {len(bounds)} IIS Bound violations ===")
    for name, lb, ub in bounds[:20]:
        print(f"  {name}  LB={lb} UB={ub}")
else:
    print(f"Unexpected status {m.Status}.")
