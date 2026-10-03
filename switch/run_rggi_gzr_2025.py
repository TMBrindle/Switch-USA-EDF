"""2025 RGGI validation: dispatch-only with RGGI cap active and GZR constraint.

No new builds or retirements (gen_info.dispatch.csv alias). All investment and
planning-reserve constraints disabled. RGGI ETS 1 cap remains active (unlike the
dispatch benchmark in run_2025_dispatch.py which disables it). No VA variant —
VA did not participate in RGGI in 2025.

CCR: pre-2023 Program Review single-tier, trigger ~$18.79/mt ($17.04/st, escalated
     from $13/st base in 2021 at 7%/yr). MRP does not apply before 2027.

Time samples: s4x1_icf (quick), s8x10_icf (higher resolution).

Usage:
  python run_rggi_gzr_2025.py          # all runs
  python run_rggi_gzr_2025.py s4x1     # s4x1_icf only
  python run_rggi_gzr_2025.py s8x10    # s8x10_icf only
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

# Dispatch-only aliases (same as run_2025_dispatch.py) *minus* the noRGGI alias.
# RGGI ETS 1 cap remains active so we get clearing prices.
DISPATCH_ALIASES = [
    # All policy constraints off *except* RGGI carbon cap
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
    "--input-alias", "loads.csv=loads_dispatch.csv",
    "--input-alias", "variable_capacity_factors.csv=variable_capacity_factors_dispatch.csv",
    "--input-alias", "water_node_tp_flows.csv=water_node_tp_flows_dispatch.csv",
    "--input-alias", "dr_data.csv=dr_data_dispatch.csv",
    "--input-alias", "ee_data.csv=ee_data_dispatch.csv",
]

GZR = ["--include-module", "study_modules.gen_zone_ratio"]
CROSSOVER = ["--solver-options-string", "crossover=1"]
UNSERVED_LOAD = ["--include-module", "switch_model.balancing.unserved_load"]

ALL_RUNS = {
    "s4x1":   ("in/2025/s4x1_icf",   "out/RGGI_gzr/2025/s4x1_icf"),
    "s8x10":  ("in/2025/s8x10_icf",  "out/RGGI_gzr/2025/s8x10_icf"),
    "s16x10": ("in/2025/s16x10_icf", "out/RGGI_gzr/2025/s16x10_icf"),
}

filter_keys = sys.argv[1:] if len(sys.argv) > 1 else list(ALL_RUNS.keys())
runs = {k: v for k, v in ALL_RUNS.items() if k in filter_keys}

if not runs:
    print(f"No matching runs for args: {sys.argv[1:]}", flush=True)
    sys.exit(1)

print(f"\nRunning {len(runs)} 2025 RGGI+GZR validation solves...", flush=True)
for label, (inputs, outputs) in runs.items():
    done_marker = SWITCH_DIR / outputs / "carbon_program_clearing_prices.csv"
    if done_marker.exists():
        print(f"=== SKIP (already done): {label} ===", flush=True)
        continue
    print(f"=== START: {label} ===", flush=True)
    cmd = (
        [str(SWITCH_EXE), "solve",
         "--inputs-dir", inputs,
         "--outputs-dir", outputs]
        + DISPATCH_ALIASES + GZR + CROSSOVER + UNSERVED_LOAD
    )
    result = subprocess.run(cmd, cwd=SWITCH_DIR)
    if result.returncode != 0:
        print(f"FAILED: {label} (exit {result.returncode})", flush=True)
        sys.exit(1)
    print(f"=== DONE:  {label} ===", flush=True)

print(f"\nAll {len(runs)} solves complete.", flush=True)
