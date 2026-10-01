"""
Break down non-RGGI emissions by fuel type: RGGI run vs no-RGGI counterfactual at baseline demand.
"""
import pandas as pd
from pathlib import Path

ROOT   = Path(__file__).parent.parent
SWITCH = Path(__file__).parent
IN_DIR = SWITCH / "in/2028/s4x1_edf_med"

hier    = pd.read_csv(ROOT / "hierarchy.csv")
zone_rv = hier.set_index("ba")["rggi_va"].to_dict()
zone_st = hier.set_index("ba")["st"].to_dict()
RGGI_STATES = {"CT","DE","MA","MD","ME","NH","NJ","NY","PA","RI","VT","VA"}

gi = pd.read_csv(IN_DIR / "gen_info.csv")
gi["rggi_va"] = gi["gen_load_zone"].map(zone_rv)
gi["in_rggi"] = gi["gen_load_zone"].apply(lambda z: zone_st.get(z,"") in RGGI_STATES)

RUNS = [
    ("RGGI (baseline)",   "RGGI_gzr95_mrp/2028/s4x1_edf_med/nsp_va"),
    ("No-RGGI (baseline)","RGGI_gzr95_noRGGI_demand/d100/s4x1_edf_med/nsp_va"),
]

def fuel_category(src):
    if pd.isna(src): return "Other"
    s = str(src).lower()
    if "coal" in s:        return "Coal"
    if "gas" in s or "ng" in s: return "Gas"
    if "oil" in s or "distillate" in s: return "Oil"
    if "nuclear" in s or "uranium" in s: return "Nuclear"
    if "wind" in s:        return "Wind"
    if "solar" in s or "sun" in s: return "Solar"
    if "hydro" in s or "water" in s: return "Hydro"
    if "biomass" in s or "bio" in s: return "Biomass"
    return "Other"

print("=== Non-RGGI region: emissions by fuel (Mt/yr) ===")
print(f"{'Fuel':<12}  {'RGGI run':>10}  {'No-RGGI':>10}  {'Difference':>12}  {'%change':>8}")
print("-"*60)

all_fuels = set()
data = {}
for label, rel_path in RUNS:
    d = pd.read_csv(SWITCH / "out" / rel_path / "dispatch.csv")
    d = d.merge(gi[["GENERATION_PROJECT","rggi_va","in_rggi"]],
                left_on="generation_project", right_on="GENERATION_PROJECT", how="left")
    non_rggi = d[~d["in_rggi"]].copy()
    non_rggi["fuel"] = non_rggi["gen_energy_source"].apply(fuel_category)
    by_fuel = non_rggi.groupby("fuel")["DispatchEmissions_tCO2_per_typical_yr"].sum() / 1e6
    data[label] = by_fuel
    all_fuels |= set(by_fuel.index)

rggi_d  = data["RGGI (baseline)"]
norggi_d = data["No-RGGI (baseline)"]

fuel_order = ["Coal","Gas","Oil","Biomass","Other"]
for fuel in fuel_order:
    r  = rggi_d.get(fuel, 0)
    n  = norggi_d.get(fuel, 0)
    diff = r - n
    pct  = (diff / n * 100) if n > 0.001 else float("nan")
    if r > 0.001 or n > 0.001:
        print(f"{fuel:<12}  {r:>10.3f}  {n:>10.3f}  {diff:>12.3f}  {pct:>7.1f}%")

r_tot  = rggi_d.sum()
n_tot  = norggi_d.sum()
print(f"{'TOTAL':<12}  {r_tot:>10.3f}  {n_tot:>10.3f}  {r_tot-n_tot:>12.3f}")

print("\n=== Also: RGGI_VA region emissions by fuel (Mt/yr) ===")
print(f"{'Fuel':<12}  {'RGGI run':>10}  {'No-RGGI':>10}  {'Abatement':>12}")
print("-"*55)
data2 = {}
for label, rel_path in RUNS:
    d = pd.read_csv(SWITCH / "out" / rel_path / "dispatch.csv")
    d = d.merge(gi[["GENERATION_PROJECT","rggi_va","in_rggi"]],
                left_on="generation_project", right_on="GENERATION_PROJECT", how="left")
    rggi_va = d[d["rggi_va"] == "RGGI_VA"].copy()
    rggi_va["fuel"] = rggi_va["gen_energy_source"].apply(fuel_category)
    by_fuel = rggi_va.groupby("fuel")["DispatchEmissions_tCO2_per_typical_yr"].sum() / 1e6
    data2[label] = by_fuel

rggi_d2  = data2["RGGI (baseline)"]
norggi_d2 = data2["No-RGGI (baseline)"]
for fuel in fuel_order:
    r  = rggi_d2.get(fuel, 0)
    n  = norggi_d2.get(fuel, 0)
    if r > 0.001 or n > 0.001:
        print(f"{fuel:<12}  {r:>10.3f}  {n:>10.3f}  {n-r:>12.3f}")
