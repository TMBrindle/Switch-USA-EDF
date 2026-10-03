"""Build-rate supply-curve aliases for a case: build_rate_{groups,gens,periods,tiers,zones,regions}.<level>.<suffix>.csv.

Reproduces the manual step used for the test cases ("v2" = build_rate 87729a5 defaults: transreg ceilings for wind_onshore
and solar, storage national-only): copy the case to a scratch folder, run `python -m brc.cli patch-case` there (it writes
plain build_rate_*.csv into the folder it is given), then copy only the build_rate_*.csv files back into the case under
the suffixed names. The case itself is never patched in place.

usage (repo root):
  python s0_workflow/case_aliases/make_build_rate_alias.py <case dir> --scratch <empty scratch dir> [--level central] [--suffix v2]
          [--python <python with build_rate deps>] [--regional-groups wind_onshore solar]
Writes patch_log.build_rate.txt in the case.
"""
import argparse, datetime, shutil, subprocess, sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("case"); ap.add_argument("--scratch", required=True); ap.add_argument("--level", default="central")
ap.add_argument("--suffix", default="v2"); ap.add_argument("--python", default=sys.executable)
ap.add_argument("--regional-groups", nargs="+", default=["wind_onshore", "solar"])
A = ap.parse_args()
case = Path(A.case).resolve(); scratch = Path(A.scratch).resolve(); repo = Path.cwd()
if scratch.exists() and any(scratch.iterdir()): raise SystemExit(f"scratch {scratch} must be empty or absent")
names = ["groups", "gens", "periods", "tiers", "zones", "regions"]
outs = {n: case / f"build_rate_{n}.{A.level}.{A.suffix}.csv" for n in names}
if any(p.exists() for p in outs.values()): raise SystemExit(f"aliases exist already: {[p.name for p in outs.values() if p.exists()]}")
shutil.copytree(case, scratch, dirs_exist_ok=True)
cmd = [A.python, "-m", "brc.cli", "patch-case", str(scratch), "--level", A.level, "--regional-groups", *A.regional_groups]
print(" ".join(cmd)); subprocess.run(cmd, cwd=repo / "build_rate", check=True)
for n in names:
    src = scratch / f"build_rate_{n}.csv"
    if not src.exists(): raise SystemExit(f"patch-case did not write {src.name}")
    shutil.copy2(src, outs[n])
head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=repo).stdout.strip()
with open(case / "patch_log.build_rate.txt", "a") as f:
    f.write(f"{datetime.datetime.now():%Y-%m-%dT%H:%M:%S} make_build_rate_alias.py at {head}: {' '.join(cmd[1:])} on scratch copy; "
            f"wrote {', '.join(p.name for p in outs.values())}\n")
print("wrote", [p.name for p in outs.values()])
