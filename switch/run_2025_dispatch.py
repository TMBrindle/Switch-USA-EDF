"""
2025 dispatch benchmark runs: dispatch-only (no new builds) with ICF demand,
validated against EPA 2025 historical data.

Two time samples:
  s4x1_icf    -- 4 representative days x 1 day  (quick check)
  s8x10_icf   -- 8 representative days x 10 days (higher resolution)

All planning/investment constraints are disabled via input aliases.
"""
import os, subprocess, sys
from pathlib import Path

_tmp = Path("D:/tmp")
_tmp.mkdir(exist_ok=True)
os.environ["TMP"]    = str(_tmp)
os.environ["TEMP"]   = str(_tmp)
os.environ["TMPDIR"] = str(_tmp)

SWITCH_DIR = Path(__file__).parent

_py_dir = Path(sys.executable).parent
SWITCH_EXE = _py_dir / "Scripts" / "switch.exe"
if not SWITCH_EXE.exists():
    SWITCH_EXE = _py_dir / "switch.exe"
if not SWITCH_EXE.exists():
    SWITCH_EXE = _py_dir / "switch"

# All aliases needed to run a no-constraint dispatch benchmark.
# These disable: RGGI, RPS, min/max capacity, trans build minimums,
# scheduled outages, planning reserves, and the zero-weight PRM extreme day.
DISPATCH_ALIASES = [
    # Policy constraints — all off
    "--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_noRGGI.csv",
    "--input-alias", "rps_requirements.csv=rps_requirements_nsp.csv",
    "--input-alias", "rps_generators.csv=rps_generators_nsp.csv",
    "--input-alias", "min_cap_requirements.csv=min_cap_requirements_nsp.csv",
    "--input-alias", "min_cap_generators.csv=min_cap_generators_nsp.csv",
    "--input-alias", "max_cap_requirements.csv=max_cap_requirements_nores.csv",
    "--input-alias", "max_cap_generators.csv=max_cap_generators_nores.csv",
    "--input-alias", "trans_build_minimum.csv=trans_build_minimum_nores.csv",
    # Generator info: no retire, no scheduled outages
    "--input-alias", "gen_info.csv=gen_info.dispatch.csv",
    # Planning reserves — margin disabled, PRM extreme day removed
    "--input-alias", "planning_reserve_margin.csv=planning_reserve_margin_nores.csv",
    "--input-alias", "timeseries.csv=timeseries_dispatch.csv",
    "--input-alias", "timepoints.csv=timepoints_dispatch.csv",
    # All timepoint-indexed files filtered to remove PRM day
    "--input-alias", "loads.csv=loads_dispatch.csv",
    "--input-alias", "variable_capacity_factors.csv=variable_capacity_factors_dispatch.csv",
    "--input-alias", "water_node_tp_flows.csv=water_node_tp_flows_dispatch.csv",
    "--input-alias", "dr_data.csv=dr_data_dispatch.csv",
    "--input-alias", "ee_data.csv=ee_data_dispatch.csv",
]

CROSSOVER = ["--solver-options-string", "crossover=1"]

# Allow unserved load so capacity-constrained zones (e.g. p119) don't cause
# hard infeasibility. lost_load_cost.csv sets the penalty at 10,000 $/MWh.
UNSERVED_LOAD = ["--include-module", "switch_model.balancing.unserved_load"]


def run_solve(inputs, outputs, label=None):
    if label is None:
        label = outputs
    done_marker = SWITCH_DIR / outputs / "carbon_program_clearing_prices.csv"
    if done_marker.exists():
        print(f"=== SKIP (already done): {label} ===", flush=True)
        return
    print(f"=== START: {label} ===", flush=True)
    cmd = [str(SWITCH_EXE), "solve",
           "--inputs-dir", inputs,
           "--outputs-dir", outputs] + DISPATCH_ALIASES + CROSSOVER + UNSERVED_LOAD
    result = subprocess.run(cmd, cwd=SWITCH_DIR)
    if result.returncode != 0:
        print(f"FAILED: {label} (exit {result.returncode})", flush=True)
        sys.exit(1)
    print(f"=== DONE:  {label} ===", flush=True)


runs = [
    ("in/2025/s4x1_icf",   "out/2025/s4x1_icf_dispatch",   "2025/s4x1_icf_dispatch"),
    ("in/2025/s8x10_icf",  "out/2025/s8x10_icf_dispatch",  "2025/s8x10_icf_dispatch"),
    ("in/2025/s16x10_icf", "out/2025/s16x10_icf_dispatch", "2025/s16x10_icf_dispatch"),
]

print(f"\nRunning {len(runs)} 2025 dispatch benchmark solves...", flush=True)
for inputs, outputs, label in runs:
    run_solve(inputs, outputs, label)
print(f"\nAll {len(runs)} dispatch solves complete.", flush=True)
