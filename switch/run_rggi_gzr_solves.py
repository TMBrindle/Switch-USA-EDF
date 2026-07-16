"""Run RGGI10+VA solve variants with RGGI_VA gen_zone_ratio >= 0.75 constraint.

Only runs the va and nsp_va variants (Virginia participates in RGGI10); skips
baseline, nsp, hf, and combined-HF variants.

Mirrors run_rggi_solves.py but adds --include-module study_modules.gen_zone_ratio.
Input dirs must contain gen_group_load_ratio.csv and gen_zone_ratio_group_by.csv
(already written to switch/in/{year}/s4x1_{edf_med,icf}/).
Outputs go to out/RGGI_gzr/ to keep results separate from unconstrained runs.

Usage:
  python run_rggi_gzr_solves.py              # all 3 years
  python run_rggi_gzr_solves.py 2028         # single year
  python run_rggi_gzr_solves.py 2028 2030    # two years
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
VA = ["--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_va.csv"]
GZR = ["--include-module", "study_modules.gen_zone_ratio"]
CROSSOVER = ["--solver-options-string", "crossover=1"]


def runs_for_year(year):
    y = str(year)
    em = f"in/{y}/s4x1_edf_med"
    ic = f"in/{y}/s4x1_icf"
    o = f"out/RGGI_gzr/{y}"
    return [
        (em, f"{o}/s4x1_edf_med/va",     VA),
        (em, f"{o}/s4x1_edf_med/nsp_va", NSP + VA),
        (ic, f"{o}/s4x1_icf/va",         VA),
        (ic, f"{o}/s4x1_icf/nsp_va",     NSP + VA),
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
