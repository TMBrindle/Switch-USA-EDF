"""variable_capacity_factors.windloss2.csv = windloss.csv (onshore x 0.881) with every OFFSHORE wind profile
(new OffShoreWind_* and existing 'Offshore Wind Turbine'; both carry the 0.983 loss cap) also x 0.881 =
(1 - 0.134) / (1 - 0.017). Checks: offshore max = 0.866, no value > 1, no non-wind row changed vs windloss.
usage: python s0_workflow/case_aliases/make_windloss2_case.py <case dir>   (after make_windloss_case.py)"""
from datetime import datetime
from pathlib import Path
import pandas as pd
F = (1 - 0.134) / (1 - 0.017)
import sys
d = Path(sys.argv[1]); out = d / "variable_capacity_factors.windloss2.csv"
if out.exists(): raise FileExistsError(out)
gi = pd.read_csv(d / "gen_info.csv", usecols=["GENERATION_PROJECT", "gen_tech"])
off = set(gi[gi.gen_tech.str.startswith("OffShoreWind") | (gi.gen_tech == "Offshore Wind Turbine")].GENERATION_PROJECT)
v = pd.read_csv(d / "variable_capacity_factors.windloss.csv", dtype=str, keep_default_na=False); col = v.columns[2]
raw = v[col]; m = v.GENERATION_PROJECT.isin(off)
pre_max = v.loc[m, col].astype(float).max()
new_vals = v.loc[m, col].astype(float).astype(float) * F
v[col] = raw; v.loc[m, col] = new_vals.map(repr); v.to_csv(out, index=False)
chk = pd.read_csv(out, float_precision="round_trip", low_memory=False); base = pd.read_csv(d / "variable_capacity_factors.windloss.csv", float_precision="round_trip", low_memory=False)
offmax = chk.loc[chk.GENERATION_PROJECT.isin(off), col].max(); allmax = chk[col].max()
other = (base[col].values != chk[col].values)[~m.values].sum()
assert abs(pre_max - 0.983) < 1e-3, pre_max; assert allmax <= 1 + 1e-6; assert abs(offmax - 0.983 * F) < 1e-3; assert other == 0
print(f"rows scaled {int(m.sum())} ({len(off)} offshore gens); offshore max {pre_max:.4f} -> {offmax:.4f}; overall max {allmax:.4f}; other rows changed {other}")
open(d / "patch_log.ic_v2.txt", "a").write(f"\nmake_windloss2.py {datetime.now().isoformat(timespec='seconds')}: variable_capacity_factors.windloss2.csv = windloss.csv with offshore wind profiles (new OffShoreWind_* and existing Offshore Wind Turbine) also x {F:.5f}; offshore max {offmax:.4f}.\nsolve with: --input-alias variable_capacity_factors.csv=variable_capacity_factors.windloss2.csv\n")
