"""
Master RGGI run script — runs unattended overnight.

Sequence:
  1. Myopic solves: 48 runs (2028, 2030, 2035 × 2 cases × 8 variants)
  2. Foresight pg_to_switch rebuild for edf_med and icf (2028+2030+2035)
  3. VA cap patch on foresight input folders (update_va_caps.py)
  4. Foresight solves: 16 runs (2 cases × 8 variants)

All solves use crossover=1 for reliable LP duals.
Outputs: out/RGGI/{year}/{case}/{variant}/  and  out/RGGI/foresight/{case}/{variant}/
"""
import os, subprocess, sys
from pathlib import Path

# Redirect temp files to D: so C: system drive doesn't fill up.
# Pyomo writes LP files to the system temp dir; Gurobi writes work files there too.
_tmp = Path("D:/tmp")
_tmp.mkdir(exist_ok=True)
os.environ["TMP"]    = str(_tmp)
os.environ["TEMP"]   = str(_tmp)
os.environ["TMPDIR"] = str(_tmp)

ROOT = Path(__file__).parent.parent   # Switch-USA-PG-ReEDS/
SWITCH_DIR = Path(__file__).parent    # switch/

# Use absolute path to switch executable so subprocess.run works without conda activation.
# On Windows the entry-point scripts live in Scripts/, not alongside python.exe.
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
VA        = ["--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_va.csv"]
HF        = ["--input-alias", "gen_info.csv=gen_info.high_fossil.csv"]
CROSSOVER = ["--solver-options-string", "crossover=1"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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


def variants_for(in_dir, out_dir):
    """Return list of (inputs, outputs, aliases) for all 8 policy variants."""
    em = in_dir
    o  = out_dir
    return [
        (em, f"{o}/baseline",  []),
        (em, f"{o}/nsp",       NSP),
        (em, f"{o}/va",        VA),
        (em, f"{o}/hf",        HF),
        (em, f"{o}/nsp_va",    NSP + VA),
        (em, f"{o}/nsp_hf",    NSP + HF),
        (em, f"{o}/va_hf",     VA + HF),
        (em, f"{o}/nsp_va_hf", NSP + VA + HF),
    ]


# ---------------------------------------------------------------------------
# 1. Myopic solves — 2028, 2030, 2035
# ---------------------------------------------------------------------------

print("\n" + "="*72, flush=True)
print("PHASE 1: Myopic solves (48 runs)", flush=True)
print("="*72, flush=True)

for year in [2028, 2030, 2035]:
    for case in ["s4x1_edf_med", "s4x1_icf"]:
        in_dir  = f"in/{year}/{case}"
        out_dir = f"out/RGGI/{year}/{case}"
        for inputs, outputs, aliases in variants_for(in_dir, out_dir):
            run_solve(inputs, outputs, aliases)

print("\nAll 48 myopic solves complete.", flush=True)


# ---------------------------------------------------------------------------
# 2. Foresight input rebuild (pg_to_switch, both cases, years 2028+2030+2035)
# ---------------------------------------------------------------------------

print("\n" + "="*72, flush=True)
print("PHASE 2: Foresight input rebuild", flush=True)
print("="*72, flush=True)

for case in ["s4x1_edf_med", "s4x1_icf"]:
    marker = SWITCH_DIR / f"in/foresight/{case}/gen_info.csv"
    if marker.exists():
        print(f"=== SKIP foresight rebuild (already done): {case} ===", flush=True)
        continue
    print(f"Building foresight inputs for {case}...", flush=True)
    result = subprocess.run(
        ["python", "pg_to_switch.py", "pg/settings", "switch/in",
         "--case-id", case, "--year", "2028", "--year", "2030", "--year", "2035"],
        cwd=ROOT,
    )
    if result.returncode != 0:
        print(f"FAILED: pg_to_switch for {case}", flush=True)
        sys.exit(1)
    print(f"Done: {case}", flush=True)


# ---------------------------------------------------------------------------
# 3. VA cap patch on foresight folders
# ---------------------------------------------------------------------------

print("\n" + "="*72, flush=True)
print("PHASE 3: VA cap patch (foresight folders)", flush=True)
print("="*72, flush=True)

result = subprocess.run(["python", "update_va_caps.py"], cwd=ROOT)
if result.returncode != 0:
    print("FAILED: update_va_caps.py", flush=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# 4. Foresight solves
# ---------------------------------------------------------------------------

print("\n" + "="*72, flush=True)
print("PHASE 4: Foresight solves (16 runs)", flush=True)
print("="*72, flush=True)

for case in ["s4x1_edf_med", "s4x1_icf"]:
    in_dir  = f"in/foresight/{case}"
    out_dir = f"out/RGGI/foresight/{case}"
    for inputs, outputs, aliases in variants_for(in_dir, out_dir):
        run_solve(inputs, outputs, aliases)

print("\nAll 16 foresight solves complete.", flush=True)
print("\n*** ALL DONE — 64 solves complete. ***", flush=True)
