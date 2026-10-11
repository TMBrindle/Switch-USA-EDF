"""Statutory tax-credit tally after a solve (CHANGES §90; s0_workflow/credit_tally.py). Run on the VM per case:

  python s0_workflow/scripts/credit_tally.py switch/in/<root>/scenarios_<case>.txt
      [--out credit_tally.csv]   # long table (vintage x year: energy, statutory $, PV $); a summary by credit,
                                 # technology and vintage is printed and written as <out stem>_summary.csv

Paths in the scenario lines are relative to switch/ (--switch-dir).
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import chain_reuse, credit_tally  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios")
    ap.add_argument("--switch-dir", type=Path, default=REPO / "switch")
    ap.add_argument("--out", type=Path, default=Path("credit_tally.csv"))
    a = ap.parse_args(argv)
    stages = chain_reuse.read_chain(Path(a.scenarios), a.switch_dir)
    t = credit_tally.tally(stages)
    t.to_csv(a.out, index=False)
    s = credit_tally.summary(t)
    s.to_csv(a.out.with_name(a.out.stem + "_summary.csv"), index=False)
    print(s.to_string(index=False) if len(s) else "no statutory credits (no tax_credit_terms.csv, or placeholders only)")


if __name__ == "__main__":
    main()
