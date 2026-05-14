"""12 no-RGGI myopic runs: 2 cases × 3 years × 2 state-policy variants (baseline/nsp)."""
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

NSP = [
    "--input-alias", "rps_requirements.csv=rps_requirements_nsp.csv",
    "--input-alias", "rps_generators.csv=rps_generators_nsp.csv",
    "--input-alias", "min_cap_requirements.csv=min_cap_requirements_nsp.csv",
    "--input-alias", "min_cap_generators.csv=min_cap_generators_nsp.csv",
]
NORGGI   = ["--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_noRGGI.csv"]
CROSSOVER = ["--solver-options-string", "crossover=1"]


def run_solve(inputs, outputs, aliases, label=None):
    if label is None:
        label = "/".join(outputs.split("/")[-3:])
    done_marker = SWITCH_DIR / outputs / "carbon_program_clearing_prices.csv"
    if done_marker.exists():
        print(f"=== SKIP (already done): {label} ===", flush=True)
        return
    print(f"=== START: {label} ===", flush=True)
    cmd = [str(SWITCH_EXE), "solve",
           "--inputs-dir", inputs,
           "--outputs-dir", outputs] + aliases + CROSSOVER
    result = subprocess.run(cmd, cwd=SWITCH_DIR)
    if result.returncode != 0:
        print(f"FAILED: {label} (exit {result.returncode})", flush=True)
        sys.exit(1)
    print(f"=== DONE:  {label} ===", flush=True)


runs = []
for year in [2028, 2030, 2035]:
    for case in ["s4x1_edf_med", "s4x1_icf"]:
        in_dir  = f"in/{year}/{case}"
        out_dir = f"out/RGGI/{year}/{case}"
        runs += [
            (in_dir, f"{out_dir}/norggi",     NORGGI),
            (in_dir, f"{out_dir}/norggi_nsp", NORGGI + NSP),
        ]

print(f"\nRunning {len(runs)} no-RGGI myopic solves...", flush=True)
for inputs, outputs, aliases in runs:
    run_solve(inputs, outputs, aliases)
print(f"\nAll {len(runs)} no-RGGI solves complete.", flush=True)
