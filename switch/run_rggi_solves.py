"""Run all 48 RGGI solve variants (3 years × 2 cases × 8 policy variants) with crossover=1."""
import subprocess, sys

NSP = [
    "--input-alias", "rps_requirements.csv=rps_requirements_nsp.csv",
    "--input-alias", "rps_generators.csv=rps_generators_nsp.csv",
    "--input-alias", "min_cap_requirements.csv=min_cap_requirements_nsp.csv",
    "--input-alias", "min_cap_generators.csv=min_cap_generators_nsp.csv",
]
VA = ["--input-alias", "carbon_policies_regional.csv=carbon_policies_regional_va.csv"]
HF = ["--input-alias", "gen_info.csv=gen_info.high_fossil.csv"]
CROSSOVER = ["--solver-options-string", "crossover=1"]


def runs_for_year(year):
    y = str(year)
    em = f"in/{y}/s4x1_edf_med"
    ic = f"in/{y}/s4x1_icf"
    o = f"out/RGGI/{y}"
    return [
        (em, f"{o}/s4x1_edf_med/baseline",  []),
        (em, f"{o}/s4x1_edf_med/nsp",       NSP),
        (em, f"{o}/s4x1_edf_med/va",        VA),
        (em, f"{o}/s4x1_edf_med/hf",        HF),
        (em, f"{o}/s4x1_edf_med/nsp_va",    NSP + VA),
        (em, f"{o}/s4x1_edf_med/nsp_hf",    NSP + HF),
        (em, f"{o}/s4x1_edf_med/va_hf",     VA + HF),
        (em, f"{o}/s4x1_edf_med/nsp_va_hf", NSP + VA + HF),
        (ic, f"{o}/s4x1_icf/baseline",      []),
        (ic, f"{o}/s4x1_icf/nsp",           NSP),
        (ic, f"{o}/s4x1_icf/va",            VA),
        (ic, f"{o}/s4x1_icf/hf",            HF),
        (ic, f"{o}/s4x1_icf/nsp_va",        NSP + VA),
        (ic, f"{o}/s4x1_icf/nsp_hf",        NSP + HF),
        (ic, f"{o}/s4x1_icf/va_hf",         VA + HF),
        (ic, f"{o}/s4x1_icf/nsp_va_hf",     NSP + VA + HF),
    ]


years = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [2028, 2030, 2035]
runs = [r for y in years for r in runs_for_year(y)]

for inputs, outputs, aliases in runs:
    label = "/".join(outputs.split("/")[-3:])
    print(f"=== START: {label} ===", flush=True)
    cmd = ["switch", "solve", "--inputs-dir", inputs, "--outputs-dir", outputs] + aliases + CROSSOVER
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"FAILED: {label} (exit {result.returncode})", flush=True)
        sys.exit(1)
    print(f"=== DONE:  {label} ===", flush=True)

print(f"All {len(runs)} solves complete.", flush=True)
