"""Run foresight RGGI solves with allowance banking variants.

Foresight inputs span 2028–2035. MRP floor prices are baked into the
base carbon_policies_regional.csv (rebuilt 2026-05-17). GZR (gen_zone_ratio)
constraint is active for all runs. Three banking variants are available:

  none     — no banking file; banking variables absent from LP
  bank_60  — initial RGGI bank = 60 Mt CO2 entering 2028
  bank_150 — initial RGGI bank = 150 Mt CO2 entering 2028

Policy variants (same as myopic RGGI matrix):
  baseline, nsp, va, hf, nsp_va, nsp_hf, va_hf, nsp_va_hf

Total: 3 banking × 2 cases × 8 policy = 48 solves.
Outputs: out/RGGI_foresight_banking/{banking}/{case}/{policy}/

Usage:
  python run_rggi_foresight_banking.py                       # all 48 runs
  python run_rggi_foresight_banking.py none                  # no-banking only (16 runs)
  python run_rggi_foresight_banking.py bank_60 bank_150      # banking variants only
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

# ── alias flag groups ─────────────────────────────────────────────────────────
NSP = [
    "--input-alias", "rps_requirements.csv=rps_requirements_nsp.csv",
    "--input-alias", "rps_generators.csv=rps_generators_nsp.csv",
    "--input-alias", "min_cap_requirements.csv=min_cap_requirements_nsp.csv",
    "--input-alias", "min_cap_generators.csv=min_cap_generators_nsp.csv",
]
VA  = ["--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_va.csv"]
HF  = ["--input-alias", "gen_info.csv=gen_info.high_fossil.csv"]
GZR = ["--include-module", "study_modules.gen_zone_ratio"]
CROSSOVER = ["--solver-options-string", "crossover=1"]

# Banking aliases keyed by variant name; none = no alias (file absent → banking off)
BANKING_ALIASES = {
    "none":     [],
    "bank_60":  ["--input-alias", "carbon_policies_banking.csv=carbon_policies_banking_bank_60.csv"],
    "bank_150": ["--input-alias", "carbon_policies_banking.csv=carbon_policies_banking_bank_150.csv"],
}

ALL_BANKING_VARIANTS = ["none", "bank_60", "bank_150"]

# ── helpers ───────────────────────────────────────────────────────────────────

def run_solve(inputs, outputs, extra_aliases, label=None):
    if label is None:
        label = "/".join(outputs.split("/")[-4:])
    done_marker = SWITCH_DIR / outputs / "carbon_program_clearing_prices.csv"
    if done_marker.exists():
        print(f"=== SKIP (already done): {label} ===", flush=True)
        return
    print(f"=== START: {label} ===", flush=True)
    cmd = (
        [str(SWITCH_EXE), "solve", "--inputs-dir", inputs, "--outputs-dir", outputs]
        + GZR + extra_aliases + CROSSOVER
    )
    result = subprocess.run(cmd, cwd=SWITCH_DIR)
    if result.returncode != 0:
        print(f"FAILED: {label} (exit {result.returncode})", flush=True)
        sys.exit(1)
    print(f"=== DONE:  {label} ===", flush=True)


def policy_variants(in_dir, out_dir, banking):
    """Return list of (inputs, outputs, aliases) for all 8 policy variants."""
    b = BANKING_ALIASES[banking]
    return [
        (in_dir, f"{out_dir}/baseline",  b),
        (in_dir, f"{out_dir}/nsp",       NSP + b),
        (in_dir, f"{out_dir}/va",        VA + b),
        (in_dir, f"{out_dir}/hf",        HF + b),
        (in_dir, f"{out_dir}/nsp_va",    NSP + VA + b),
        (in_dir, f"{out_dir}/nsp_hf",    NSP + HF + b),
        (in_dir, f"{out_dir}/va_hf",     VA + HF + b),
        (in_dir, f"{out_dir}/nsp_va_hf", NSP + VA + HF + b),
    ]


# ── main ──────────────────────────────────────────────────────────────────────

banking_variants = sys.argv[1:] if len(sys.argv) > 1 else ALL_BANKING_VARIANTS

invalid = [v for v in banking_variants if v not in BANKING_ALIASES]
if invalid:
    print(f"Unknown banking variant(s): {invalid}")
    print(f"Valid options: {ALL_BANKING_VARIANTS}")
    sys.exit(1)

total = len(banking_variants) * 2 * 8
print(f"\nForesight banking solves: {len(banking_variants)} banking variant(s) "
      f"× 2 cases × 8 policy = {total} runs", flush=True)
print(f"Banking variants: {banking_variants}", flush=True)

for banking in banking_variants:
    print(f"\n{'='*72}", flush=True)
    print(f"Banking variant: {banking}", flush=True)
    print(f"{'='*72}", flush=True)
    for case in ["s4x1_edf_med", "s4x1_icf"]:
        in_dir  = f"in/foresight/{case}"
        out_dir = f"out/RGGI_foresight_banking/{banking}/{case}"
        for inputs, outputs, aliases in policy_variants(in_dir, out_dir, banking):
            run_solve(inputs, outputs, aliases)

print(f"\nAll {total} foresight banking solves complete.", flush=True)
