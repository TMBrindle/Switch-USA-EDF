"""
Run demand sensitivity solves to find the demand level that triggers $35/mt
RGGI allowance price. Scales loads.csv uniformly by a multiplier, then solves
with 95% GZR + NSP + MRP flags for 2028 s4x1_edf_med.

Usage:
  python run_demand_sensitivity.py
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

# Demand multipliers to test (1.0 = baseline already run as nsp_va)
MULTIPLIERS = [1.05, 1.10, 1.12, 1.15, 1.20]

NSP = [
    "--input-alias", "rps_requirements.csv=rps_requirements_nsp.csv",
    "--input-alias", "rps_generators.csv=rps_generators_nsp.csv",
    "--input-alias", "min_cap_requirements.csv=min_cap_requirements_nsp.csv",
    "--input-alias", "min_cap_generators.csv=min_cap_generators_nsp.csv",
]
VA_MRP  = ["--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_va_mrp.csv"]
GZR95   = ["--input-alias", "gen_group_load_ratio.csv=gen_group_load_ratio_gzr95.csv"]
GZR     = ["--include-module", "study_modules.gen_zone_ratio"]
CROSSOVER = ["--solver-options-string", "crossover=1"]

# ── Generate scaled loads alias files ────────────────────────────────────────
base_loads = pd.read_csv(IN_DIR / "loads.csv")

alias_files = {}
for m in MULTIPLIERS:
    tag = f"d{int(round(m * 100)):03d}"
    alias_fname = f"loads_{tag}.csv"
    alias_path  = IN_DIR / alias_fname
    scaled = base_loads.copy()
    scaled["zone_demand_mw"] = (scaled["zone_demand_mw"] * m).round(3)
    scaled.to_csv(alias_path, index=False)
    alias_files[m] = alias_fname
    print(f"Wrote {alias_fname}  (×{m:.2f})")

# ── Run solves ────────────────────────────────────────────────────────────────
results = []

for m, alias_fname in alias_files.items():
    tag     = f"d{int(round(m * 100)):03d}"
    out_dir = f"out/RGGI_gzr95_mrp_demand/{tag}/s4x1_edf_med/nsp_va"
    label   = f"×{m:.2f} ({tag})"
    print(f"\n=== START: {label} ===", flush=True)

    cmd = (
        [str(SWITCH_EXE), "solve",
         "--inputs-dir", "in/2028/s4x1_edf_med",
         "--outputs-dir", out_dir]
        + GZR + GZR95 + NSP + VA_MRP
        + ["--input-alias", f"loads.csv={alias_fname}"]
        + CROSSOVER
    )
    result = subprocess.run(cmd, cwd=SWITCH_DIR)
    if result.returncode != 0:
        print(f"FAILED: {label} (exit {result.returncode})", flush=True)
        results.append({"multiplier": m, "tag": tag, "status": "FAILED",
                        "ets1_price": None, "ets1_emissions_mt": None, "surplus_mt": None})
        continue

    # Read clearing price
    prices_f = SWITCH_DIR / out_dir / "carbon_program_clearing_prices.csv"
    prices = pd.read_csv(prices_f)
    ets1 = prices[prices["CO2_PROGRAM"] == "ETS 1"].iloc[0]
    price    = ets1["clearing_price_dollar_per_tco2"]
    emis_mt  = ets1["emissions_tco2_per_yr"] / 1e6
    surplus  = ets1["surplus_tco2_per_yr"] / 1e6
    t1_purch = ets1["ccr_tier1_purchases_tco2"] / 1e6
    t2_purch = ets1.get("ccr_tier2_purchases_tco2", 0)
    if pd.notna(t2_purch):
        t2_purch = float(t2_purch) / 1e6
    else:
        t2_purch = 0.0

    results.append({
        "multiplier": m, "tag": tag, "status": "OK",
        "ets1_price": price, "ets1_emissions_mt": emis_mt,
        "surplus_mt": surplus, "t1_purch_mt": t1_purch, "t2_purch_mt": t2_purch,
    })
    print(f"=== DONE: {label}  ETS1=${price:.2f}/mt  emis={emis_mt:.1f}Mt  "
          f"surplus={surplus:.2f}Mt  T1={t1_purch:.2f}Mt  T2={t2_purch:.2f}Mt ===",
          flush=True)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n=== Demand sensitivity summary (95% GZR + NSP + MRP, 2028 edf_med) ===")
print(f"Baseline (x1.00, already run): ETS1=$15.70/mt, emis=73.9Mt, surplus=0.0Mt")
print()
hdr = f"{'Mult':>6}  {'ETS1 $/mt':>10}  {'Emis Mt':>8}  {'Surplus Mt':>10}  {'T1 Mt':>6}  {'T2 Mt':>6}"
print(hdr)
print("-" * len(hdr))
for r in results:
    if r["status"] == "FAILED":
        print(f"{r['multiplier']:>6.2f}  {'FAILED':>10}")
    else:
        print(f"{r['multiplier']:>6.2f}  {r['ets1_price']:>10.2f}  "
              f"{r['ets1_emissions_mt']:>8.1f}  {r['surplus_mt']:>10.2f}  "
              f"{r['t1_purch_mt']:>6.2f}  {r['t2_purch_mt']:>6.2f}")
