"""
Compare all available cost-optimal parclust solves.
Run after each new cost-optimal solve completes.
Usage: python compare_cost_optimal.py
"""
import pandas as pd
import os
import sys

BASE = "D:/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS"
PERIOD = 2035

COST_OPTIMAL_SCENARIOS = [
    ("s4x5",   "switch/out/foresight/s4x5_caelp_parclust_prm_cost_optimal"),
    ("s20x1",  "switch/out/foresight/s20x1_caelp_parclust_prm_cost_optimal"),
    ("s6x5",   "switch/out/foresight/s6x5_caelp_parclust_prm_cost_optimal"),
    ("s7x5",   "switch/out/foresight/s7x5_caelp_parclust_prm_cost_optimal"),
]

hier = pd.read_csv("D:/Data compiler/hierarchy.csv")

GEO = {
    "National":   hier["ba"].tolist(),
    "Texas":      hier[hier["st"] == "TX"]["ba"].tolist(),
    "WECC":       hier[hier["interconnect"] == "western"]["ba"].tolist(),
    "Southeast":  hier[hier["st"].isin(
        ["GA","FL","AL","MS","SC","NC","TN","AR","LA","KY","VA","WV"])]["ba"].tolist(),
}

def is_renewable(tech, src):
    t, s = str(tech).lower(), str(src).lower()
    return any(k in t or k in s for k in
               ["solar","wind","hydro","geo","biomass","offshore","nuclear","uranium"])

def is_gas(src):  return str(src).lower() in ["gas","naturalgas","natural_gas","lng"]
def is_coal(src): return str(src).lower() in ["coal","sub_bit_coal","bit_coal","lignite"]

def filt_z(df, zones):
    return df[(df["period"] == PERIOD) & (df["gen_load_zone"].isin(zones))]

def filt_c(df, zones):
    return df[(df["PERIOD"] == PERIOD) & (df["gen_load_zone"].isin(zones))]

def summarise(label, path):
    full = f"{BASE}/{path}"
    if not os.path.isfile(f"{full}/total_cost.txt"):
        return None
    z = pd.read_csv(f"{full}/dispatch_zonal_annual_summary.csv")
    c = pd.read_csv(f"{full}/gen_cap.csv")
    tc = float(open(f"{full}/total_cost.txt").read().strip())

    rows = {}
    for geo, zones in GEO.items():
        zf = filt_z(z, zones)
        cf = filt_c(c, zones)

        energy = zf[zf["Energy_GWh_typical_yr"] > 0].copy()
        total_gen = energy["Energy_GWh_typical_yr"].sum()

        ren  = energy[energy.apply(lambda r: is_renewable(r["gen_tech"], r["gen_energy_source"]), axis=1)]["Energy_GWh_typical_yr"].sum()
        gas  = energy[energy["gen_energy_source"].apply(is_gas)]["Energy_GWh_typical_yr"].sum()
        coal = energy[energy["gen_energy_source"].apply(is_coal)]["Energy_GWh_typical_yr"].sum()
        emis = zf["DispatchEmissions_tCO2_per_typical_yr"].sum() / 1e6

        cap = cf[cf["GenCapacity"] > 0].copy()
        wind_cap  = cap[cap["gen_tech"].str.lower().str.contains("wind",  na=False)]["GenCapacity"].sum() / 1e3
        solar_cap = cap[cap["gen_tech"].str.lower().str.contains("solar", na=False)]["GenCapacity"].sum() / 1e3
        batt_cap  = cap[cap["gen_tech"].str.lower().str.contains("batter|storage", na=False)]["GenCapacity"].sum() / 1e3
        gas_cap   = cap[cap["gen_energy_source"].apply(is_gas)]["GenCapacity"].sum() / 1e3
        coal_cap  = cap[cap["gen_energy_source"].apply(is_coal)]["GenCapacity"].sum() / 1e3

        rows[geo] = {
            "Total_Cost_$B":  round(tc / 1e9, 2),
            "Emissions_MtCO2": round(emis, 1),
            "Ren_%":           round(100 * ren / total_gen if total_gen else 0, 1),
            "Gas_%":           round(100 * gas / total_gen if total_gen else 0, 1),
            "Coal_%":          round(100 * coal / total_gen if total_gen else 0, 1),
            "Gas_TWh":         round(gas / 1e3, 1),
            "Coal_TWh":        round(coal / 1e3, 1),
            "Wind_Cap_GW":     round(wind_cap, 1),
            "Solar_Cap_GW":    round(solar_cap, 1),
            "Battery_Cap_GW":  round(batt_cap, 1),
            "Gas_Cap_GW":      round(gas_cap, 1),
            "Coal_Cap_GW":     round(coal_cap, 1),
        }
    return rows

# ── Load available scenarios ──────────────────────────────────────────────────
available = []
for label, path in COST_OPTIMAL_SCENARIOS:
    result = summarise(label, path)
    if result:
        available.append((label, result))

if len(available) < 2:
    print("Need at least 2 completed cost-optimal scenarios to compare.")
    sys.exit(0)

labels = [a[0] for a in available]
print(f"\nComparing cost-optimal scenarios: {', '.join(labels)}")
print(f"Period: {PERIOD}\n")

# ── Print comparison tables by geography ─────────────────────────────────────
pd.set_option("display.width", 200)
pd.set_option("display.float_format", "{:.1f}".format)

for geo in GEO.keys():
    print(f"{'='*70}")
    print(f"  {geo} — {PERIOD}")
    print(f"{'='*70}")
    table = {label: data[geo] for label, data in available}
    df = pd.DataFrame(table)
    # Add delta columns vs s4x5 baseline if available
    if "s4x5" in table and len(labels) > 1:
        for lbl in labels:
            if lbl != "s4x5":
                df[f"Δ vs s4x5 ({lbl})"] = df[lbl] - df["s4x5"]
    print(df.to_string())
    print()

# ── Save CSV ──────────────────────────────────────────────────────────────────
rows = []
for label, data in available:
    for geo, metrics in data.items():
        rows.append({"scenario": label, "geography": geo, **metrics})
pd.DataFrame(rows).to_csv(f"{BASE}/comparison_cost_optimal_{PERIOD}.csv", index=False)
print(f"Saved to: {BASE}/comparison_cost_optimal_{PERIOD}.csv")
