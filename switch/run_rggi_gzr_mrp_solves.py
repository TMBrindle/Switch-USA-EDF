"""Run RGGI10+VA variants with both gen_zone_ratio (GZR) and minimum reserve price (MRP).

GZR: RGGI_VA group min gen/load ratio >= 0.75 (--include-module gen_zone_ratio)
MRP: RGGI auction floor prices (2028: $10.62/mt, 2030: $12.15/mt, 2035: $17.04/mt)
     via carbon_policies_regional_va_mrp.csv alias

Only runs va and nsp_va variants. Outputs to out/RGGI_gzr_mrp/.

Usage:
  python run_rggi_gzr_mrp_solves.py              # all 3 years
  python run_rggi_gzr_mrp_solves.py 2028         # single year
  python run_rggi_gzr_mrp_solves.py 2028 2030    # two years
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
if not Path(SWITCH_EXE).exists():
    SWITCH_EXE = Path("C:/ProgramData/miniconda3/envs/switch-pg-reeds/Scripts/switch.exe")

NSP = [
    "--input-alias", "rps_requirements.csv=rps_requirements_nsp.csv",
    "--input-alias", "rps_generators.csv=rps_generators_nsp.csv",
    "--input-alias", "min_cap_requirements.csv=min_cap_requirements_nsp.csv",
    "--input-alias", "min_cap_generators.csv=min_cap_generators_nsp.csv",
]
# MRP alias replaces the standard VA carbon policy with the MRP-augmented version
VA_MRP = ["--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_va_mrp.csv"]
GZR = ["--include-module", "study_modules.gen_zone_ratio"]
CROSSOVER = ["--solver-options-string", "crossover=1"]


def runs_for_year(year):
    y = str(year)
    em = f"in/{y}/s4x1_edf_med"
    ic = f"in/{y}/s4x1_icf"
    o = f"out/RGGI_gzr_mrp/{y}"
    return [
        (em, f"{o}/s4x1_edf_med/va",     VA_MRP),
        (em, f"{o}/s4x1_edf_med/nsp_va", NSP + VA_MRP),
        (ic, f"{o}/s4x1_icf/va",         VA_MRP),
        (ic, f"{o}/s4x1_icf/nsp_va",     NSP + VA_MRP),
    ]


years = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [2028, 2030, 2035]
runs = [r for y in years for r in runs_for_year(y)]

for inputs, outputs, aliases in runs:
    label = "/".join(outputs.split("/")[-3:])
    print(f"=== START: {label} ===", flush=True)
    cmd = (
        [str(SWITCH_EXE), "solve", "--inputs-dir", inputs, "--outputs-dir", outputs]
        + GZR + aliases + CROSSOVER
    )
    result = subprocess.run(cmd, cwd=SWITCH_DIR)
    if result.returncode != 0:
        print(f"FAILED: {label} (exit {result.returncode})", flush=True)
        sys.exit(1)
    print(f"=== DONE:  {label} ===", flush=True)

print(f"All {len(runs)} solves complete.", flush=True)
