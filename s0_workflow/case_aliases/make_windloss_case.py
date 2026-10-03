"""variable_capacity_factors.windloss.csv = variable_capacity_factors.csv with every ONSHORE wind profile (new
LandbasedWind_* and existing 'Onshore Wind Turbine' clusters; both carry the 0.983 loss cap) x 0.881 =
(1 - 0.134) / (1 - 0.017). Offshore wind and solar unchanged. Checks: no value > 1; onshore max = 0.866.
usage: python s0_workflow/case_aliases/make_windloss_case.py <case dir> [<case dir> ...]   (step 1 of 2; then make_windloss2_case.py)"""
from datetime import datetime
from pathlib import Path

import pandas as pd

F = (1 - 0.134) / (1 - 0.017)
import sys
for case in sys.argv[1:]:
    d = Path(case)  # case inputs folder
    out = d / "variable_capacity_factors.windloss.csv"
    if out.exists():
        raise FileExistsError(out)
    gi = pd.read_csv(d / "gen_info.csv", usecols=["GENERATION_PROJECT", "gen_tech"])
    ons = set(gi[gi.gen_tech.str.startswith("LandbasedWind") | (gi.gen_tech == "Onshore Wind Turbine")].GENERATION_PROJECT)
    v = pd.read_csv(d / "variable_capacity_factors.csv", dtype=str, keep_default_na=False)
    col = v.columns[2]
    raw = v[col]
    m = v.GENERATION_PROJECT.isin(ons)
    new_vals = v.loc[m, col].astype(float) * F
    v[col] = raw
    v.loc[m, col] = new_vals.map(repr)
    v.to_csv(out, index=False)
    chk = pd.read_csv(out, float_precision="round_trip")
    allmax = chk[col].max()
    onmax = chk.loc[chk.GENERATION_PROJECT.isin(ons), col].max()
    other_changed = (pd.read_csv(d / "variable_capacity_factors.csv", float_precision="round_trip")[col].values != chk[col].values)[~m.values].sum()
    assert allmax <= 1.0 + 1e-6, allmax   # base solar rows can be 1.00000024 (float32), untouched here
    assert abs(onmax - 0.983 * F) < 1e-3, onmax
    assert other_changed == 0
    print(f"{case}: factor {F:.5f}; rows scaled {int(m.sum())} ({len(ons)} onshore clusters); onshore max {onmax:.4f}; "
          f"overall max {allmax:.4f}; non-onshore rows changed {other_changed}")
    with open(d / "patch_log.ic_v2.txt", "a") as fh:
        fh.write(f"\nmake_windloss.py {datetime.now().isoformat(timespec='seconds')}: variable_capacity_factors.windloss.csv = "
                 f"onshore wind profiles (new LandbasedWind_* and existing Onshore Wind Turbine) x {F:.5f} = (1-0.134)/(1-0.017), "
                 f"ATB/ReEDS 13.4% losses vs the 1.7% in the reV profiles; offshore and solar unchanged. onshore max {onmax:.4f}.\n"
                 "solve with: --input-alias variable_capacity_factors.csv=variable_capacity_factors.windloss.csv\n")
