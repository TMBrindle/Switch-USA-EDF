"""
Diagnose infeasibility in the 2025 dispatch model by computing Gurobi IIS.
"""
import os, sys, tempfile
from pathlib import Path

# Redirect TMP
_tmp = Path("D:/tmp")
_tmp.mkdir(exist_ok=True)
os.environ["TMP"] = str(_tmp)
os.environ["TEMP"] = str(_tmp)
os.environ["TMPDIR"] = str(_tmp)

# Force gurobipy from the system gurobi install if not already on path
sys.path.insert(0, r"C:\gurobi1300\win64\python")

import gurobipy as gp

INPUTS = str(Path(__file__).parent / "in/2025/s4x1_icf")
LP_FILE = str(Path("D:/tmp/switch_2025_iis_test.lp"))
IIS_FILE = str(Path("D:/tmp/switch_2025.ilp"))

# We need to first generate the LP file by running Switch with --solver-io lp
# and catching the temp file path. Easier approach: generate LP via Pyomo directly.

# Build the model via switch_model machinery
sys.path.insert(0, str(Path(__file__).parent))

import switch_model.solve as solve_mod
import switch_model.main as main_mod
from switch_model.utilities import _ArgumentParser

# Construct args as if called from command line
import argparse

# Read the standard options.txt
options_path = Path(__file__).parent / "options.txt"
input_aliases = [
    "--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_noRGGI.csv",
    "--input-alias", "rps_requirements.csv=rps_requirements_nsp.csv",
    "--input-alias", "rps_generators.csv=rps_generators_nsp.csv",
    "--input-alias", "min_cap_requirements.csv=min_cap_requirements_nsp.csv",
    "--input-alias", "min_cap_generators.csv=min_cap_generators_nsp.csv",
    "--input-alias", "trans_build_minimum.csv=trans_build_minimum_nores.csv",
    "--input-alias", "gen_info.csv=gen_info.no_retire.csv",
]

argv = [
    "--inputs-dir", INPUTS,
    "--outputs-dir", "out/2025/s4x1_icf_diag",
] + input_aliases

# Load model using switch_model
model, inputs_dir = main_mod.get_model_inputs(*argv[:2], argv[2:])

# Build instance
instance = model.create_instance(inputs_dir)

# Write LP
from pyomo.opt import SolverFactory
solver = SolverFactory("gurobi", solver_io="lp")
results = solver.solve(instance, tee=False, keepfiles=True, symbolic_solver_labels=True)

print("Done!")
