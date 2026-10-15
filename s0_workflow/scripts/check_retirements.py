"""Check the planned retirements (s0_production.retirement_checks; CHANGES §249, §250) in a stage's chained inputs and
its outputs, after the build. The build checks its own files (retirement_checks.csv); this checks what the previous
stage's solve carried in, and what the stage's solve kept.

  # after the 2030 solve: no Diablo Canyon build in service in 2035 from the chained gen_build_predetermined
  python s0_workflow/scripts/check_retirements.py switch/in/s0prod_A_v32final/2035/S0prod_A --case S0prod_A

  # after the 2035 solve: and no capacity in its outputs (gen_cap.csv)
  python s0_workflow/scripts/check_retirements.py switch/in/s0prod_A_v32final/2035/S0prod_A --case S0prod_A \\
      --outputs switch/out/s0prod_A_v32final/2035/S0prod_A

A build row is in service in a period with Switch's rule (online <= period start < online + gen_max_age, with the
stage's own gen_info.csv, where apply_retirements set gen_max_age). Exit 1 if a checked project is in service after its
last_period (or missing up to it), in the built file, the chained file or the outputs.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import production as s0prod  # noqa: E402


def check(folder: Path, s0: dict, case: str | None = None, outputs: Path | None = None) -> list[str]:
    """Lines of the report; a line starting with FAIL is a failed check."""
    folder = Path(folder)
    zones = set(pd.read_csv(folder / "gen_info.csv", na_values=["."]).gen_load_zone.astype(str))
    files = ["gen_build_predetermined.csv"] + ([f"gen_build_predetermined.chained.{case}.csv"] if case else [])
    out = []
    for c in s0prod.retirement_checks(s0):
        prj, last = str(c["project"]), int(c["last_period"])
        if str(c.get("zone") or prj.split("_")[0]) not in zones:
            out.append(f"ok   {c['name']} ({prj}): zone not in the case")
            continue
        for f in files:
            if not (folder / f).exists():
                out.append(f"FAIL {c['name']} ({prj}): {f} missing")
                continue
            for p, mw in s0prod.operable_mw(folder, prj, f).items():
                good = (mw > 0) == (p <= last)
                out.append(f"{'ok  ' if good else 'FAIL'} {c['name']} ({prj}) {f}: {mw:,.0f} MW in service in {p} "
                           f"(expected {'in service' if p <= last else 'retired'})")
        if outputs is not None:
            cap = pd.read_csv(Path(outputs) / "gen_cap.csv", na_values=["."])
            cap = cap[cap.GENERATION_PROJECT.astype(str) == prj]
            for p, mw in cap.groupby(cap.PERIOD.astype(int)).GenCapacity.sum().items():
                good = (mw > 0) == (p <= last)
                out.append(f"{'ok  ' if good else 'FAIL'} {c['name']} ({prj}) gen_cap.csv: {mw:,.0f} MW in {p} "
                           f"(expected {'in service' if p <= last else 'retired'})")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder", help="a stage's inputs folder, e.g. switch/in/<root>/2035/<case>")
    ap.add_argument("--case", help="check the chained gen_build_predetermined.chained.<case>.csv too")
    ap.add_argument("--outputs", help="the stage's outputs folder: check gen_cap.csv too")
    ap.add_argument("--settings", default=str(REPO / "pg/settings/s0_production.yml"))
    a = ap.parse_args(argv)
    s0 = yaml.safe_load(open(a.settings))["s0_production"]
    lines = check(Path(a.folder), s0, a.case, Path(a.outputs) if a.outputs else None)
    print("\n".join(lines) if lines else "no retirement_checks in the settings: nothing to check")
    bad = [ln for ln in lines if ln.startswith("FAIL")]
    print(f"{len(bad)} failed" if bad else "all passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
