"""reV CF basis, re-assessed after the zeroed-PV-profile finding (CHANGES §111).

  python s0_workflow/scripts/rev_cf_reassess.py rev_cf_mismatch_118.csv [--zones zones_affected.csv] [--out ...csv]

rev_cf_mismatch_118.csv (data/rev-cf-mismatch): zone, technology, model_cf, rev_cf, ratio (= model / reV), by stage.
zones_affected.csv: zone, pv_cf_understatement (share of the true CF the zeroed sites remove, e.g. 0.30 for p1: the
model CF is (1 - u) x what it would be without the zeroed years), or the dashboards' cf_understated_pct (percent). Without it, the shares the dashboards session quoted
for six zones are used (p1 0.30, p5 0.24, p11 0.21, p14 0.18, p26 0.12, p79 0.12) and every other PV zone is shown
uncorrected (it lost 1-8%).
Output per pair: corrected model CF = model_cf / (1 - u) (PV only), corrected ratio, and the remaining gap.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

QUOTED = {"p1": 0.30, "p5": 0.24, "p11": 0.21, "p14": 0.18, "p26": 0.12, "p79": 0.12}


def reassess(m: pd.DataFrame, zones: pd.DataFrame | None = None) -> pd.DataFrame:
    if zones is not None and "pv_cf_understatement" not in zones and "cf_understated_pct" in zones:
        zones = zones.assign(pv_cf_understatement=zones.cf_understated_pct / 100.0)     # data/rev-cf-mismatch 9383b09
    u = dict(zip(zones.zone, zones.pv_cf_understatement)) if zones is not None else QUOTED
    out = m.copy()
    pv = out.technology.eq("solar_pv")
    out["pv_understatement"] = [u.get(z) if p else None for z, p in zip(out.zone, pv)]
    out["understatement_source"] = ["zones_affected" if zones is not None and p and z in u else
                                    "quoted" if zones is None and p and z in u else
                                    ("not given (1-8%)" if p else "wind: unaffected") for z, p in zip(out.zone, pv)]
    f = 1 - out.pv_understatement.fillna(0.0)
    out["model_cf_corrected"] = out.model_cf / f
    out["ratio_corrected"] = out.model_cf_corrected / out.rev_cf
    out["in_band_after"] = out.ratio_corrected.between(0.85, 1.15)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mismatch", type=Path)
    ap.add_argument("--zones", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    r = reassess(pd.read_csv(a.mismatch), pd.read_csv(a.zones) if a.zones else None)
    if a.out:
        r.to_csv(a.out, index=False)
    pd.set_option("display.width", 200)
    cols = ["zone", "technology", "model_cf", "rev_cf", "ratio", "pv_understatement", "model_cf_corrected",
            "ratio_corrected", "in_band_after"]
    print(r[r.technology == "solar_pv"].sort_values("ratio_corrected")[cols].round(4).to_string(index=False))
    print(r.groupby("technology").ratio_corrected.describe().round(3).to_string())


if __name__ == "__main__":
    sys.exit(main())
