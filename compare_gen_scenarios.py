"""
Compare generation between unconstrained (min-only) and max1.30 scenarios.
Maps zones to states via hierarchy.csv.
"""
import pandas as pd

base = "D:/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS"
out = f"{base}/switch/out/2035"

# Load hierarchy for zone->state mapping
hier = pd.read_csv(f"{base}/hierarchy.csv")
zone_state = hier.set_index("ba")["st"].to_dict()
zone_transreg = hier.set_index("ba")["transreg"].to_dict()
zone_hurdlereg = hier.set_index("ba")["hurdlereg"].to_dict()

def load_dispatch(run_dir):
    df = pd.read_csv(f"{out}/{run_dir}/dispatch_zonal_annual_summary.csv")
    df["state"] = df["gen_load_zone"].map(zone_state)
    df["transreg"] = df["gen_load_zone"].map(zone_transreg)
    df["hurdlereg"] = df["gen_load_zone"].map(zone_hurdlereg)
    # Broad tech category
    def tech_cat(row):
        src = str(row["gen_energy_source"]).lower()
        tech = str(row["gen_tech"]).lower()
        if src in ("naturalgas", "coal", "petroleum"):
            return "Fossil"
        elif src == "uranium":
            return "Nuclear"
        elif src == "water":
            return "Hydro"
        elif src in ("wind",) or "wind" in tech:
            return "Wind"
        elif src in ("sun",) or "solar" in tech or "pv" in tech or "photovoltaic" in tech:
            return "Solar"
        elif src == "storage" or "battery" in tech or "storage" in tech:
            return "Storage"
        elif src == "biomass" or "biomass" in tech:
            return "Biomass"
        else:
            return "Other"
    df["tech_cat"] = df.apply(tech_cat, axis=1)
    return df

base_run = "s4x1_caelp_parclust_zoned_high_fossil_build"
constrained_run = "s4x1_caelp_parclust_zoned_max1.30"

df_base = load_dispatch(base_run)
df_con = load_dispatch(constrained_run)

# ── 1. National tech-level summary ──────────────────────────────────────────
def tech_summary(df):
    return df.groupby("tech_cat")["Energy_GWh_typical_yr"].sum().rename("GWh")

ts_base = tech_summary(df_base)
ts_con = tech_summary(df_con)
ts = pd.DataFrame({"Unconstrained": ts_base, "Max1.30": ts_con}).fillna(0)
ts["Delta_GWh"] = ts["Max1.30"] - ts["Unconstrained"]
ts["Delta_pct"] = (ts["Delta_GWh"] / ts["Unconstrained"].abs() * 100).round(1)
ts = ts.sort_values("Delta_GWh")

print("=" * 65)
print("NATIONAL TECH-LEVEL SHIFTS (min-only → max×1.30)")
print("=" * 65)
print(ts.round(0).to_string())

# ── 2. State-level total generation shift ───────────────────────────────────
def state_summary(df):
    return df.groupby("state")["Energy_GWh_typical_yr"].sum().rename("GWh")

ss_base = state_summary(df_base)
ss_con = state_summary(df_con)
ss = pd.DataFrame({"Unconstrained": ss_base, "Max1.30": ss_con}).fillna(0)
ss["Delta_GWh"] = ss["Max1.30"] - ss["Unconstrained"]
ss["Delta_pct"] = (ss["Delta_GWh"] / ss["Unconstrained"].abs() * 100).round(1)
ss = ss.sort_values("Delta_GWh")

print("\n" + "=" * 65)
print("STATE-LEVEL TOTAL GENERATION SHIFTS (largest changes first)")
print("=" * 65)
print(ss[ss["Delta_GWh"].abs() > 500].round(0).to_string())

# ── 3. State × Tech breakdown for most-changed states ───────────────────────
top_states = ss["Delta_GWh"].abs().nlargest(8).index.tolist()

def state_tech_summary(df):
    return df.groupby(["state", "tech_cat"])["Energy_GWh_typical_yr"].sum()

st_base = state_tech_summary(df_base)
st_con = state_tech_summary(df_con)
st = pd.DataFrame({"Unconstrained": st_base, "Max1.30": st_con}).fillna(0)
st["Delta_GWh"] = st["Max1.30"] - st["Unconstrained"]

print("\n" + "=" * 65)
print("STATE × TECH SHIFTS (top 8 most-changed states)")
print("=" * 65)
for state in top_states:
    sub = st.loc[state][st.loc[state]["Delta_GWh"].abs() > 100].sort_values("Delta_GWh")
    if not sub.empty:
        print(f"\n  {state}:")
        print(sub[["Unconstrained", "Max1.30", "Delta_GWh"]].round(0).to_string())

# ── 4. Hurdlereg (BA group) level ────────────────────────────────────────────
def hreg_tech_summary(df):
    return df.groupby(["hurdlereg", "tech_cat"])["Energy_GWh_typical_yr"].sum()

ht_base = hreg_tech_summary(df_base)
ht_con = hreg_tech_summary(df_con)
ht = pd.DataFrame({"Unconstrained": ht_base, "Max1.30": ht_con}).fillna(0)
ht["Delta_GWh"] = ht["Max1.30"] - ht["Unconstrained"]

# Show BAs with largest total changes
hreg_total = ht.groupby(level=0)["Delta_GWh"].apply(lambda x: x.abs().sum()).nlargest(10)
print("\n" + "=" * 65)
print("HURDLEREG (BA) LEVEL — TOP 10 MOST CHANGED")
print("=" * 65)
for hreg in hreg_total.index:
    sub = ht.loc[hreg][ht.loc[hreg]["Delta_GWh"].abs() > 100].sort_values("Delta_GWh")
    if not sub.empty:
        print(f"\n  {hreg} (total change: {hreg_total[hreg]:,.0f} GWh abs):")
        print(sub[["Unconstrained", "Max1.30", "Delta_GWh"]].round(0).to_string())

# ── 5. Transmission check: p6 net export ─────────────────────────────────────
print("\n" + "=" * 65)
print("NET GENERATION IN PacifiCorp_West ZONES (p6, p8)")
print("=" * 65)
for run_name, df in [("Unconstrained", df_base), ("Max1.30", df_con)]:
    sub = df[df["gen_load_zone"].isin(["p6","p8"])]
    total_gen = sub["Energy_GWh_typical_yr"].sum()
    by_tech = sub.groupby("tech_cat")["Energy_GWh_typical_yr"].sum().sort_values(ascending=False)
    print(f"\n  {run_name}: total gen = {total_gen:,.0f} GWh")
    for tech, gwh in by_tech.items():
        if abs(gwh) > 10:
            print(f"    {tech}: {gwh:,.0f} GWh")
