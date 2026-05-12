"""Run all 16 RGGI solve variants sequentially with crossover=1 for reliable LP duals."""
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

runs = [
    # edf_med all 8
    ("in/2035/s4x1_edf_med", "out/RGGI/2035/s4x1_edf_med/baseline",  []),
    ("in/2035/s4x1_edf_med", "out/RGGI/2035/s4x1_edf_med/nsp",       NSP),
    ("in/2035/s4x1_edf_med", "out/RGGI/2035/s4x1_edf_med/va",        VA),
    ("in/2035/s4x1_edf_med", "out/RGGI/2035/s4x1_edf_med/hf",        HF),
    ("in/2035/s4x1_edf_med", "out/RGGI/2035/s4x1_edf_med/nsp_va",    NSP + VA),
    ("in/2035/s4x1_edf_med", "out/RGGI/2035/s4x1_edf_med/nsp_hf",    NSP + HF),
    ("in/2035/s4x1_edf_med", "out/RGGI/2035/s4x1_edf_med/va_hf",     VA + HF),
    ("in/2035/s4x1_edf_med", "out/RGGI/2035/s4x1_edf_med/nsp_va_hf", NSP + VA + HF),
    # icf all 8
    ("in/2035/s4x1_icf", "out/RGGI/2035/s4x1_icf/baseline",   []),
    ("in/2035/s4x1_icf", "out/RGGI/2035/s4x1_icf/nsp",        NSP),
    ("in/2035/s4x1_icf", "out/RGGI/2035/s4x1_icf/va",         VA),
    ("in/2035/s4x1_icf", "out/RGGI/2035/s4x1_icf/hf",         HF),
    ("in/2035/s4x1_icf", "out/RGGI/2035/s4x1_icf/nsp_va",     NSP + VA),
    ("in/2035/s4x1_icf", "out/RGGI/2035/s4x1_icf/nsp_hf",     NSP + HF),
    ("in/2035/s4x1_icf", "out/RGGI/2035/s4x1_icf/va_hf",      VA + HF),
    ("in/2035/s4x1_icf", "out/RGGI/2035/s4x1_icf/nsp_va_hf",  NSP + VA + HF),
]

for inputs, outputs, aliases in runs:
    label = "/".join(outputs.split("/")[-2:])
    print(f"=== START: {label} ===", flush=True)
    cmd = ["switch", "solve", "--inputs-dir", inputs, "--outputs-dir", outputs] + aliases + CROSSOVER
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"FAILED: {label} (exit {result.returncode})", flush=True)
        sys.exit(1)
    print(f"=== DONE:  {label} ===", flush=True)

print("All 16 solves complete.", flush=True)
