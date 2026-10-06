"""Tax-credit spend by vintage after a solve (CHANGES §75; s0_workflow/credit_spend.py). Run on the VM after each case:

  python s0_workflow/scripts/credit_spend.py switch/in/<root>/scenarios_<case>.txt [more scenarios files]
      [--out credit_spend.csv]   # long table (by tech and vintage); a summary ($M/yr by category) is printed and
                                 # written next to it as <out stem>_summary.csv

Paths in the scenario lines are relative to switch/ (--switch-dir).
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import chain_reuse, credit_spend  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--switch-dir", type=Path, default=REPO / "switch")
    ap.add_argument("--out", type=Path, default=Path("credit_spend.csv"))
    a = ap.parse_args(argv)
    frames = []
    for f in a.scenarios:
        stages = chain_reuse.read_chain(Path(f), a.switch_dir)
        frames.append(credit_spend.spend(stages, case=stages[0]["case"] if stages else Path(f).stem))
    long = pd.concat(frames, ignore_index=True)
    long.to_csv(a.out, index=False)
    s = credit_spend.summary(long)
    s.to_csv(a.out.with_name(a.out.stem + "_summary.csv"), index=False)
    print(s.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
