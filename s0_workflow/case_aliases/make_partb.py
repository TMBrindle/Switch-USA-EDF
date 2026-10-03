"""Part B sensitivity inputs (originally for s4x1_S0_2035_icoff, 5a_aw2 setup; --case for any case).
usage: python s0_workflow/case_aliases/make_partb.py --case <case dir> [--ref-run <run dir>] [--only B6]
 Suffixed alias files; nothing overwritten.
B1 fuel_cost.aeo_gas.csv: natural gas x k so the 2035 use-weighted national delivered price (weights = 5a_aw2 gas fuel
   use by zone) = 4.98 $/MMBtu (AEO2026 delivered to power, 2025$; applied as model 2024$ because no 2025 CPI is on
   the VM, so the target is overstated by about one year of inflation). Coal, other fuels and the regional pattern
   unchanged.
B4 gen_info.stor30.csv: gen_amortization_period 30 (was 15) for new-build Utility-Scale Battery rows.
B6 gen_build_costs.gas_atb.csv: new-build gas overnight cost x ATB 2024 Moderate / GridLab override
   (CC 1,522,209 / 2,061,000; CT 1,129,622 / 1,606,000; ATB 2031-35 mean, 2022$ table units, same units as the
   override), so regional multipliers and dollar basis are kept.
"""
from datetime import datetime
from pathlib import Path
import numpy as np, pandas as pd

import argparse
ap = argparse.ArgumentParser(description="Part B sensitivity aliases (B1 gas price, B4 storage amortisation, B6 gas capex).")
ap.add_argument("--case", default="switch/in_ictest/cases/2035/s4x1_S0_2035_icoff", help="case inputs folder (repo-relative)")
ap.add_argument("--ref-run", default="switch/out_ictest/2035/s4x1_S0_2035_icoff_aw2", help="solved run whose gas fuel use weights the B1 price target")
ap.add_argument("--only", nargs="*", choices=["B1", "B4", "B6"], default=["B1", "B4", "B6"])
A = ap.parse_args()
D = Path(A.case); O = Path(A.ref_run)
log = []
def out(df, name):
    p = D / name
    if p.exists(): raise FileExistsError(p)
    df.to_csv(p, index=False); return name

# B1
if "B1" in A.only:
    fc = pd.read_csv(D / "fuel_cost.csv", dtype=str, keep_default_na=False)
    gi = pd.read_csv(D / "gen_info.csv", na_values=".")[["GENERATION_PROJECT", "gen_full_load_heat_rate"]]
    d = pd.read_csv(O / "dispatch_gen_annual_summary.csv").merge(gi, left_on="generation_project", right_on="GENERATION_PROJECT")
    u = d[d.gen_energy_source == "naturalgas"].assign(m=lambda x: x.Energy_GWh_typical_yr * x.gen_full_load_heat_rate).groupby("gen_load_zone").m.sum()
    g = fc[(fc.fuel == "naturalgas") & (fc.period == "2035")].copy(); g["p"] = g.fuel_cost.astype(float)
    cur = float(np.average(g.set_index("load_zone").p.reindex(u.index), weights=u))
    k = 4.98 / cur
    m = fc.fuel == "naturalgas"
    fc.loc[m, "fuel_cost"] = (fc.loc[m, "fuel_cost"].astype(float) * k).map(repr)
    out(fc, "fuel_cost.aeo_gas.csv")
    log.append(f"B1 fuel_cost.aeo_gas.csv: gas x {k:.4f} (use-weighted 2035 delivered {cur:.3f} -> 4.980 $/MMBtu; 2025$ target applied as 2024$, no 2025 CPI on VM)")
# B4 (unconditional in the original; see --only)
if "B4" in A.only:
    g2 = pd.read_csv(D / "gen_info.csv", dtype=str, keep_default_na=False)
    mb = g2.gen_tech.str.startswith("Utility-Scale Battery")
    assert set(g2.loc[mb, "gen_amortization_period"]) == {"15.0"} or set(g2.loc[mb, "gen_amortization_period"]) == {"15"}, set(g2.loc[mb, "gen_amortization_period"])
    g2.loc[mb, "gen_amortization_period"] = "30.0"
    out(g2, "gen_info.stor30.csv")
    log.append(f"B4 gen_info.stor30.csv: gen_amortization_period 15 -> 30 for {int(mb.sum())} new-build Utility-Scale Battery rows")
# B6
if "B6" in A.only:
    bc = pd.read_csv(D / "gen_build_costs.csv", dtype=str, keep_default_na=False)
    tech = pd.read_csv(D / "gen_info.csv", usecols=["GENERATION_PROJECT", "gen_tech"]).set_index("GENERATION_PROJECT").gen_tech
    f = {"NaturalGas_1-on-1 Combined Cycle (H-Frame)_Moderate": 1522209.0 / 2061000.0, "NaturalGas_Combustion Turbine (F-Frame)_Moderate": 1129622.0 / 1606000.0}
    fac = bc.GENERATION_PROJECT.map(tech).map(f)
    mg = fac.notna() & (bc.BUILD_YEAR.astype(float) == 2035)
    bc.loc[mg, "gen_overnight_cost"] = (bc.loc[mg, "gen_overnight_cost"].astype(float) * fac[mg]).map(repr)
    out(bc, "gen_build_costs.gas_atb.csv")
    log.append(f"B6 gen_build_costs.gas_atb.csv: new gas 2035 overnight cost x ATB/GridLab (CC {f['NaturalGas_1-on-1 Combined Cycle (H-Frame)_Moderate']:.4f}, CT {f['NaturalGas_Combustion Turbine (F-Frame)_Moderate']:.4f}) on {int(mg.sum())} rows")
open(D / "patch_log.partb.txt", "a").write(f"\nmake_partb.py {datetime.now().isoformat(timespec='seconds')}\n" + "\n".join(log) + "\n")
print("\n".join(log))
