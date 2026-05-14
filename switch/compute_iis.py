"""
Generate LP via Switch, then compute Gurobi IIS to find infeasible constraints.
Usage: conda run -n switch-pg-reeds python compute_iis.py
"""
import os, sys, subprocess, shutil, glob
from pathlib import Path

_tmp = Path("D:/tmp")
_tmp.mkdir(exist_ok=True)
os.environ["TMP"] = str(_tmp)
os.environ["TEMP"] = str(_tmp)

SWITCH_DIR = Path(__file__).parent
INPUTS = "in/2025/s4x1_icf"
OUTPUTS = "out/2025/s4x1_icf_iis_diag"
LP_DEST = _tmp / "switch_2025.lp"
IIS_DEST = _tmp / "switch_2025.ilp"

ALIASES = [
    "--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_noRGGI.csv",
    "--input-alias", "rps_requirements.csv=rps_requirements_nsp.csv",
    "--input-alias", "rps_generators.csv=rps_generators_nsp.csv",
    "--input-alias", "min_cap_requirements.csv=min_cap_requirements_nsp.csv",
    "--input-alias", "min_cap_generators.csv=min_cap_generators_nsp.csv",
    "--input-alias", "trans_build_minimum.csv=trans_build_minimum_nores.csv",
    "--input-alias", "gen_info.csv=gen_info.no_retire.csv",
]

# Step 1: Run Switch with --save-lp to produce the LP file
# Switch doesn't have --save-lp, but we can use --solver-options-string to write result
# Instead, modify temp dir so the LP survives, then run solver manually

# Approach: use Switch's --keep-solver-files if available, or patch TMP so lp isn't cleaned
# Simplest: copy any .lp files from TMP before they're deleted using a wrapper

# Actually: use switch solve to write the lp to a known location via a small pyomo script

import sys
sys.path.insert(0, str(SWITCH_DIR))

# Load switch_model and build the instance
import switch_model.main as sm_main
from pyomo.environ import *
from pyomo.opt import SolverFactory

print("Loading Switch model...", flush=True)
os.chdir(str(SWITCH_DIR))

# Parse model and load data the same way Switch does
argv = [
    "--inputs-dir", INPUTS,
    "--outputs-dir", OUTPUTS,
] + ALIASES

# Use switch_model's model definition
from switch_model.utilities import _ArgumentParser
import switch_model.solve as sm_solve

# Build model via standard Switch pipeline
model = sm_main.define_AbstractModel(*argv)
instance = sm_main.load_inputs(model, *argv)

print("Writing LP file...", flush=True)
from pyomo.repn.plugins.lp_writer import LPWriter
from io import StringIO
import pyomo.environ as pe

# Write LP
opt = SolverFactory('gurobi', solver_io='lp')
lp_path = str(LP_DEST)
results = opt._presolve(instance, keepfiles=True, symbolic_solver_labels=False)

# Get the LP filename from the solver
print(f"LP written. Now loading LP into Gurobi for IIS...", flush=True)

import gurobipy as gp
env = gp.Env()
m = gp.read(lp_path, env)
m.Params.DualReductions = 0
m.optimize()
print(f"Status: {m.Status}, {m.status}", flush=True)

if m.Status == 3:  # INFEASIBLE
    print("Model is INFEASIBLE. Computing IIS...", flush=True)
    m.computeIIS()
    m.write(str(IIS_DEST))
    print(f"IIS written to {IIS_DEST}", flush=True)

    # Print IIS constraints
    print("\n=== IIS Constraints ===")
    for c in m.getConstrs():
        if c.IISConstr:
            print(f"  CONSTR: {c.ConstrName}")
    print("\n=== IIS Bounds ===")
    for v in m.getVars():
        if v.IISLB or v.IISUB:
            print(f"  VAR: {v.VarName}  LB={v.IISLB}  UB={v.IISUB}")
elif m.Status == 5:  # UNBOUNDED
    print("Model is UNBOUNDED. Finding unbounded ray...", flush=True)
    m.Params.InfUnbdInfo = 1
    m.optimize()
    for v in m.getVars():
        if hasattr(v, 'UnbdRay') and v.UnbdRay != 0:
            print(f"  UNBOUNDED VAR: {v.VarName}  ray={v.UnbdRay}")
else:
    print(f"Unexpected status: {m.Status}")
