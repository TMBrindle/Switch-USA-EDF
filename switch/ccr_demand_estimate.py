"""Estimate demand increase needed to trigger T1 and T2 CCR in the 95% GZR NSP run."""
import pandas as pd
from pathlib import Path

OUT = Path("out/RGGI_gzr95_mrp/2028/s4x1_edf_med/nsp_va")
IN  = Path("in/2028/s4x1_edf_med")

disp = pd.read_csv(OUT / "dispatch.csv")
gi   = pd.read_csv(IN / "gen_info.csv")
hier = pd.read_csv("../hierarchy.csv")
zone_rv = hier.set_index("ba")["rggi_va"].to_dict()
gi["rggi_va"] = gi["gen_load_zone"].map(zone_rv)
rggi_gens = set(gi[gi["rggi_va"] == "RGGI_VA"]["GENERATION_PROJECT"])

disp_rv = disp[disp["generation_project"].isin(rggi_gens)].copy()

by_src = disp_rv.groupby("gen_energy_source")[
    ["Energy_GWh_typical_yr", "DispatchEmissions_tCO2_per_typical_yr"]
].sum()
by_src["intensity"] = (
    by_src["DispatchEmissions_tCO2_per_typical_yr"]
    / (by_src["Energy_GWh_typical_yr"] * 1000)
)
by_src = by_src.sort_values("Energy_GWh_typical_yr", ascending=False)
total_e   = by_src["Energy_GWh_typical_yr"].sum()
total_co2 = by_src["DispatchEmissions_tCO2_per_typical_yr"].sum()

print("RGGI_VA dispatch by energy source:")
hdr = f"{'Source':<25} {'TWh':>8} {'Share%':>7} {'CO2 Mt':>8} {'tCO2/MWh':>10}"
print(hdr)
print("-" * len(hdr))
for src, row in by_src.iterrows():
    e   = row["Energy_GWh_typical_yr"]
    co2 = row["DispatchEmissions_tCO2_per_typical_yr"]
    ins = row["intensity"]
    print(f"{src:<25} {e/1e6:>8.2f} {e/total_e*100:>7.1f} {co2/1e6:>8.2f} {ins:>10.3f}")
print(f"{'TOTAL':<25} {total_e/1e6:>8.2f} {'100.0':>7} {total_co2/1e6:>8.2f}")

avg_intensity = total_co2 / (total_e * 1000)
rggi_va_load_twh = 539.7
print(f"\nRGGI_VA load: {rggi_va_load_twh} TWh")
print(f"Average RGGI_VA emission intensity: {avg_intensity:.4f} tCO2/MWh")

# CCR parameters from clearing prices output
base_cap_mt    = 73.882
ccr_t1_mt      = 10.656
ccr_t2_mt      = 10.656
ccr_t1_price   = 23.0
ccr_t2_price   = 34.5
current_price  = 15.70

print(f"\n--- CCR trigger estimates ---")
print(f"Base cap: {base_cap_mt:.2f} Mt  (currently fully binding)")
print(f"CCR T1:   {ccr_t1_mt:.2f} Mt at ${ccr_t1_price}/mt")
print(f"CCR T2:   {ccr_t2_mt:.2f} Mt at ${ccr_t2_price}/mt")
print(f"Current clearing price: ${current_price}/mt")

# Marginal emission rate: assume new load served by gas
# CCGT ~0.40 tCO2/MWh, CT ~0.55 tCO2/MWh
for label, marg_int in [("CCGT margin (~0.40 tCO2/MWh)", 0.40),
                         ("CT margin (~0.55 tCO2/MWh)",   0.55)]:
    t1_twh = ccr_t1_mt / marg_int
    t2_twh = (ccr_t1_mt + ccr_t2_mt) / marg_int
    print(f"\n  Assuming {label}:")
    print(f"    T1 trigger:  need +{ccr_t1_mt:.2f} Mt => +{t1_twh:.1f} TWh load"
          f" (+{t1_twh/rggi_va_load_twh*100:.1f}% of current RGGI_VA load)")
    print(f"    T2 full use: need +{ccr_t1_mt+ccr_t2_mt:.2f} Mt => +{t2_twh:.1f} TWh load"
          f" (+{t2_twh/rggi_va_load_twh*100:.1f}% of current RGGI_VA load)")
