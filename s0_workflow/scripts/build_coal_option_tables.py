"""Per-option validation tables of the coal specification (coal_spec.md rev. 2.1), one set per pre-2030 retirement
option (s0_production.retirements_pre2030: block_all, planned_only, unrestricted), from the committed public data:

  s0_workflow/specs/coal/by_option/coal_spec_expected_caps_by_stage.<option>.csv   (as coal_spec_expected_caps_by_stage.csv)
  s0_workflow/specs/coal/by_option/coal_spec_hold_by_stage.<option>.csv            (as coal_spec_hold_by_stage.csv)
  s0_workflow/specs/coal/by_option/coal_spec_stage_summary.<option>.csv            (§4's per-stage figures)

The case build checks against the tables of the case's option. Model fleet: the public reconstruction of
PowerGenome's coal basis (s0_workflow/data/coal_model_basis_860er2024.csv), with the overrides of §2.

usage (repo root): python s0_workflow/scripts/build_coal_option_tables.py [--check]
  --check  build in memory and compare with the committed files (exit 1 if they differ)
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import coal_fleet as cf  # noqa: E402
from s0_workflow import coal_spec as cs  # noqa: E402

OUT = cs.SPEC / "by_option"


def texts() -> dict:
    out = {}
    for o in cs.RETIREMENT_OPTIONS:
        caps, holds, summ = cf.option_tables(o)
        for name, df in (("coal_spec_expected_caps_by_stage", caps.round(4)), ("coal_spec_hold_by_stage", holds),
                         ("coal_spec_stage_summary", summ)):
            out[OUT / f"{name}.{o}.csv"] = df.to_csv(index=False, lineterminator="\n")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    t = texts()
    if a.check:
        bad = [p for p, s in t.items() if not p.exists() or p.read_text() != s]
        for p in bad:
            print("differs from a fresh build:", p.relative_to(REPO))
        sys.exit(1 if bad else 0)
    OUT.mkdir(exist_ok=True)
    for p, s in t.items():
        p.write_text(s)
        print("wrote", p.relative_to(REPO))


if __name__ == "__main__":
    main()
