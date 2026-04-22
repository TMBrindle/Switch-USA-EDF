"""
Compare s4x5 vs s20x1 parclust outputs for 2035.
National level + Texas, Colorado, WECC, Southeast.
"""
import pandas as pd
import numpy as np
import os

BASE = "D:/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS"

SCENARIOS = {
    "s4x5_cost_opt":    "switch/out/foresight/s4x5_caelp_parclust_prm_cost_optimal",
    "s4x5_constrained": "switch/out/foresight/s4x5_caelp_parclust_prm_constrained",
    "s20x1_cost_opt":   "switch/out/foresight/s20x1_caelp_parclust_prm_cost_optimal",
    "s20x1_constrained":"switch/out/foresight/s20x1_caelp_parclust_prm_constrained",
}

PERIOD = 2035

# ── Geography mappings ────────────────────────────────────────────────────────
hier = pd.read_csv("D:/Data compiler/hierarchy.csv")

GEO = {
    "National": hier["ba"].tolist(),
    "Texas":    hier[hier["st"] == "TX"]["ba"].tolist(),
    "Colorado": hier[hier["st"] == "CO"]["ba"].tolist(),
    "WECC":     hier[hier["interconnect"] == "western"]["ba"].tolist(),
    "Southeast": hier[hier["st"].isin(
        ["GA","FL","AL","MS","SC","NC","TN","AR","LA","KY","VA","WV"]
    )]["ba"].tolist(),
}

# ── Renewable / fossil tech labels ───────────────────────────────────────────
RENEWABLES = {"Solar", "Wind", "Hydro", "Geothermal", "Biomass", "OffshoreWind"}
FOSSIL_GAS  = {"Gas", "CombinedCycle", "Gas_CC", "CCGT", "Gas_CT", "CombustionTurbine",
               "Gas_Steam", "Gas_Peaker"}
FOSSIL_COAL = {"Coal", "Coal_Steam", "Coal_CCS"}

def is_renewable(tech):
    t = str(tech)
    return any(r.lower() in t.lower() for r in
               ["solar","wind","hydro","geo","biomass","offshore","nuclear"])

def is_gas(src):
    return str(src).lower() in ["gas","naturalgas","natural_gas","lng"]

def is_coal(src):
    return str(src).lower() in ["coal","sub_bit_coal","bit_coal","lignite"]

def is_nuclear(src):
    return str(src).lower() in ["uranium","nuclear"]

# ── Load data ─────────────────────────────────────────────────────────────────
def load_scenario(name, path):
    full = f"{BASE}/{path}"
    zonal = pd.read_csv(f"{full}/dispatch_zonal_annual_summary.csv")
    cap    = pd.read_csv(f"{full}/gen_cap.csv")
    total  = open(f"{full}/total_cost.txt").read().strip()
    return dict(name=name, zonal=zonal, cap=cap, total_cost=float(total))

data = {k: load_scenario(k, v) for k, v in SCENARIOS.items()}

# ── Helper: filter to period & zone list ─────────────────────────────────────
def filt(df, zones, period=PERIOD):
    # dispatch_zonal uses lowercase 'period'; gen_cap uses 'PERIOD'
    period_col = "period" if "period" in df.columns else "PERIOD"
    zone_col   = "gen_load_zone" if "gen_load_zone" in df.columns else "gen_load_zone"
    return df[(df[period_col] == period) & (df[zone_col].isin(zones))]

# ── Build comparison table ────────────────────────────────────────────────────
rows = []

for geo_name, zones in GEO.items():
    for scen_name, d in data.items():
        z = filt(d["zonal"], zones)
        c = filt(d["cap"], zones)

        # Emissions (tCO2/yr → MtCO2/yr)
        emissions = z["DispatchEmissions_tCO2_per_typical_yr"].sum() / 1e6

        # Energy by fuel type (GWh/yr) — negative = storage charging, skip
        energy = z[z["Energy_GWh_typical_yr"] > 0].copy()
        energy["is_ren"] = energy["gen_energy_source"].apply(
            lambda s: is_renewable(str(s))) | energy["gen_tech"].apply(
            lambda t: is_renewable(str(t)))
        energy["is_gas"] = energy["gen_energy_source"].apply(is_gas)
        energy["is_coal"] = energy["gen_energy_source"].apply(is_coal)
        energy["is_nuc"]  = energy["gen_energy_source"].apply(is_nuclear)

        total_gen   = energy["Energy_GWh_typical_yr"].sum()
        ren_gen     = energy.loc[energy["is_ren"], "Energy_GWh_typical_yr"].sum()
        gas_gen     = energy.loc[energy["is_gas"], "Energy_GWh_typical_yr"].sum()
        coal_gen    = energy.loc[energy["is_coal"], "Energy_GWh_typical_yr"].sum()
        nuc_gen     = energy.loc[energy["is_nuc"], "Energy_GWh_typical_yr"].sum()

        ren_pct  = 100 * ren_gen  / total_gen if total_gen > 0 else 0
        gas_pct  = 100 * gas_gen  / total_gen if total_gen > 0 else 0
        coal_pct = 100 * coal_gen / total_gen if total_gen > 0 else 0

        # Capacity (MW → GW)
        cap_df = c[c["GenCapacity"] > 0].copy()
        cap_df["is_ren"] = cap_df["gen_energy_source"].apply(
            lambda s: is_renewable(str(s))) | cap_df["gen_tech"].apply(
            lambda t: is_renewable(str(t)))
        cap_df["is_gas"]  = cap_df["gen_energy_source"].apply(is_gas)
        cap_df["is_coal"] = cap_df["gen_energy_source"].apply(is_coal)
        cap_df["is_batt"] = cap_df["gen_tech"].str.lower().str.contains("batter|storage", na=False)
        cap_df["is_solar"] = cap_df["gen_tech"].str.lower().str.contains("solar", na=False)
        cap_df["is_wind"]  = cap_df["gen_tech"].str.lower().str.contains("wind", na=False)

        total_cap = cap_df["GenCapacity"].sum() / 1e3
        ren_cap   = cap_df.loc[cap_df["is_ren"], "GenCapacity"].sum() / 1e3
        gas_cap   = cap_df.loc[cap_df["is_gas"],  "GenCapacity"].sum() / 1e3
        coal_cap  = cap_df.loc[cap_df["is_coal"], "GenCapacity"].sum() / 1e3
        batt_cap  = cap_df.loc[cap_df["is_batt"], "GenCapacity"].sum() / 1e3
        solar_cap = cap_df.loc[cap_df["is_solar"],"GenCapacity"].sum() / 1e3
        wind_cap  = cap_df.loc[cap_df["is_wind"], "GenCapacity"].sum() / 1e3

        rows.append({
            "Geography": geo_name,
            "Scenario":  scen_name,
            "Emissions_MtCO2": round(emissions, 1),
            "Total_Gen_TWh":   round(total_gen / 1e3, 1),
            "Ren_pct":         round(ren_pct, 1),
            "Gas_pct":         round(gas_pct, 1),
            "Coal_pct":        round(coal_pct, 1),
            "Gas_TWh":         round(gas_gen / 1e3, 1),
            "Coal_TWh":        round(coal_gen / 1e3, 1),
            "Total_Cap_GW":    round(total_cap, 1),
            "Ren_Cap_GW":      round(ren_cap, 1),
            "Solar_Cap_GW":    round(solar_cap, 1),
            "Wind_Cap_GW":     round(wind_cap, 1),
            "Battery_Cap_GW":  round(batt_cap, 1),
            "Gas_Cap_GW":      round(gas_cap, 1),
            "Coal_Cap_GW":     round(coal_cap, 1),
        })

df = pd.DataFrame(rows)

# ── Print results ─────────────────────────────────────────────────────────────
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 200)
pd.set_option("display.float_format", "{:.1f}".format)

for geo in GEO.keys():
    print(f"\n{'='*80}")
    print(f"  {geo} — 2035")
    print('='*80)
    sub = df[df["Geography"] == geo].drop(columns="Geography").set_index("Scenario")
    print(sub.T.to_string())

# ── Save CSV ──────────────────────────────────────────────────────────────────
out_path = f"{BASE}/comparison_s4x5_vs_s20x1_2035.csv"
df.to_csv(out_path, index=False)
print(f"\n\nSaved to: {out_path}")
