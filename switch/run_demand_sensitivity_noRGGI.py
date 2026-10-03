"""
No-RGGI counterfactual demand sensitivity solves.
Same assumptions as run_demand_sensitivity.py (95% GZR, NSP, edf_med 2028)
but with all carbon policies removed — used to isolate true RGGI leakage.

Outputs: out/RGGI_gzr95_noRGGI_demand/{d100,d105,...}/s4x1_edf_med/nsp_va
"""
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

_tmp = Path("D:/tmp")
_tmp.mkdir(exist_ok=True)
os.environ["TMP"]    = str(_tmp)
os.environ["TEMP"]   = str(_tmp)
os.environ["TMPDIR"] = str(_tmp)

SWITCH_DIR = Path(__file__).parent
IN_DIR = SWITCH_DIR / "in/2028/s4x1_edf_med"

_py_dir = Path(sys.executable).parent
SWITCH_EXE = _py_dir / "Scripts" / "switch.exe"
if not SWITCH_EXE.exists():
    SWITCH_EXE = Path("C:/ProgramData/miniconda3/envs/switch-pg-reeds/Scripts/switch.exe")

# Include baseline (×1.00) so the no-RGGI counterfactual is self-contained
MULTIPLIERS = [1.00, 1.05, 1.10, 1.12, 1.15, 1.20]

NSP = [
    "--input-alias", "rps_requirements.csv=rps_requirements_nsp.csv",
    "--input-alias", "rps_generators.csv=rps_generators_nsp.csv",
    "--input-alias", "min_cap_requirements.csv=min_cap_requirements_nsp.csv",
    "--input-alias", "min_cap_generators.csv=min_cap_generators_nsp.csv",
]
NO_RGGI = ["--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_noRGGI.csv"]
GZR95   = ["--input-alias", "gen_group_load_ratio.csv=gen_group_load_ratio_gzr95.csv"]
GZR     = ["--include-module", "study_modules.gen_zone_ratio"]
CROSSOVER = ["--solver-options-string", "crossover=1"]

# ── Generate scaled loads alias files (reuse from prior run if already present) ─
base_loads = pd.read_csv(IN_DIR / "loads.csv")

alias_files = {}
for m in MULTIPLIERS:
    tag = f"d{int(round(m * 100)):03d}"
    alias_fname = f"loads_{tag}.csv"
    alias_path  = IN_DIR / alias_fname
    if not alias_path.exists():
        scaled = base_loads.copy()
        scaled["zone_demand_mw"] = (scaled["zone_demand_mw"] * m).round(3)
        scaled.to_csv(alias_path, index=False)
        print(f"Wrote {alias_fname}  (x{m:.2f})")
    else:
        print(f"Reusing {alias_fname}  (x{m:.2f})")
    alias_files[m] = alias_fname

# ── Run solves ────────────────────────────────────────────────────────────────
results = []

for m, alias_fname in alias_files.items():
    tag     = f"d{int(round(m * 100)):03d}"
    out_dir = f"out/RGGI_gzr95_noRGGI_demand/{tag}/s4x1_edf_med/nsp_va"
    label   = f"x{m:.2f} ({tag})"
    print(f"\n=== START: {label} ===", flush=True)

    load_alias = [] if m == 1.00 else ["--input-alias", f"loads.csv={alias_fname}"]

    cmd = (
        [str(SWITCH_EXE), "solve",
         "--inputs-dir", "in/2028/s4x1_edf_med",
         "--outputs-dir", out_dir]
        + GZR + GZR95 + NSP + NO_RGGI
        + load_alias
        + CROSSOVER
    )
    result = subprocess.run(cmd, cwd=SWITCH_DIR)
    if result.returncode != 0:
        print(f"FAILED: {label} (exit {result.returncode})", flush=True)
        results.append({"multiplier": m, "tag": tag, "status": "FAILED",
                        "total_co2_mt": None})
        continue

    disp_f = SWITCH_DIR / out_dir / "dispatch.csv"
    disp   = pd.read_csv(disp_f)
    total_co2 = disp["DispatchEmissions_tCO2_per_typical_yr"].sum() / 1e6
    results.append({"multiplier": m, "tag": tag, "status": "OK",
                    "total_co2_mt": total_co2})
    print(f"=== DONE: {label}  total_CO2={total_co2:.1f} Mt ===", flush=True)

print("\n=== No-RGGI counterfactual summary ===")
print(f"{'Mult':>6}  {'Total CO2 Mt':>14}")
print("-" * 25)
for r in results:
    if r["status"] == "FAILED":
        print(f"{r['multiplier']:>6.2f}  {'FAILED':>14}")
    else:
        print(f"{r['multiplier']:>6.2f}  {r['total_co2_mt']:>14.1f}")
