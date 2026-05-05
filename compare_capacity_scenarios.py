"""
Compare capacity (MW) and capacity factors between unconstrained and max1.30.
Uses gen_cap.csv (includes both existing and new build) and gen_build.csv.
"""
import pandas as pd

base = "D:/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS"
out = f"{base}/switch/out/2035"

hier = pd.read_csv(f"{base}/hierarchy.csv")
zone_state = hier.set_index("ba")["st"].to_dict()
zone_hurdlereg = hier.set_index("ba")["hurdlereg"].to_dict()

def tech_cat(row):
    src = str(row.get("gen_energy_source", "")).lower()
    tech = str(row.get("gen_tech", "")).lower()
    if src in ("naturalgas", "coal", "petroleum"):
        return "Fossil"
    elif src == "uranium":
        return "Nuclear"
    elif src == "water":
        return "Hydro"
    elif "wind" in tech or src == "wind":
        return "Wind"
    elif "solar" in tech or "pv" in tech or "photovoltaic" in tech or src == "sun":
        return "Solar"
    elif "battery" in tech or "storage" in tech or src == "storage":
        return "Storage"
    elif src == "biomass":
        return "Biomass"
    else:
        return "Other"

def load_cap(run_dir):
    df = pd.read_csv(f"{out}/{run_dir}/gen_cap.csv")
    df["state"] = df["gen_load_zone"].map(zone_state)
    df["hurdlereg"] = df["gen_load_zone"].map(zone_hurdlereg)
    df["tech_cat"] = df.apply(tech_cat, axis=1)
    df["GenCapacity"] = df["GenCapacity"].fillna(0)
    df["SuspendGen_total"] = df["SuspendGen_total"].fillna(0)
    df["ActiveCap_MW"] = df["GenCapacity"] - df["SuspendGen_total"]
    return df

def load_build(run_dir):
    """New builds only (positive BuildGen values)."""
    df = pd.read_csv(f"{out}/{run_dir}/BuildGen.csv")
    df.columns = ["GENERATION_PROJECT","build_year","BuildGen"]
    df = df[df["BuildGen"] > 0.01]
    # extract zone from gen project name (prefix before first underscore-number)
    gi = pd.read_csv(f"{out}/{run_dir}/gen_info.csv")
    df = df.merge(gi[["GENERATION_PROJECT","gen_tech","gen_energy_source","gen_load_zone"]],
                  on="GENERATION_PROJECT", how="left")
    df["state"] = df["gen_load_zone"].map(zone_state)
    df["hurdlereg"] = df["gen_load_zone"].map(zone_hurdlereg)
    df["tech_cat"] = df.apply(tech_cat, axis=1)
    return df

def load_suspend(run_dir):
    """Suspended (retired) capacity."""
    df = pd.read_csv(f"{out}/{run_dir}/SuspendGen.csv")
    df.columns = ["GENERATION_PROJECT","build_year","suspend_year","SuspendGen"]
    df = df[df["SuspendGen"] > 0.01]
    gi = pd.read_csv(f"{out}/{run_dir}/gen_info.csv")
    df = df.merge(gi[["GENERATION_PROJECT","gen_tech","gen_energy_source","gen_load_zone"]],
                  on="GENERATION_PROJECT", how="left")
    df["state"] = df["gen_load_zone"].map(zone_state)
    df["hurdlereg"] = df["gen_load_zone"].map(zone_hurdlereg)
    df["tech_cat"] = df.apply(tech_cat, axis=1)
    return df

base_run = "s4x1_caelp_parclust_zoned_high_fossil_build"
con_run = "s4x1_caelp_parclust_zoned_max1.30"

cap_b = load_cap(base_run)
cap_c = load_cap(con_run)
bld_b = load_build(base_run)
bld_c = load_build(con_run)
sus_b = load_suspend(base_run)
sus_c = load_suspend(con_run)

# ── 1. National capacity by tech ─────────────────────────────────────────────
def nat_cap(df):
    return df.groupby("tech_cat")["ActiveCap_MW"].sum()

nc_b = nat_cap(cap_b)
nc_c = nat_cap(cap_c)
nc = pd.DataFrame({"Unconstrained_MW": nc_b, "Max1.30_MW": nc_c}).fillna(0)
nc["Delta_MW"] = nc["Max1.30_MW"] - nc["Unconstrained_MW"]
print("=" * 60)
print("NATIONAL CAPACITY BY TECH (MW active in 2035)")
print("=" * 60)
print(nc.sort_values("Delta_MW").round(0).to_string())

# ── 2. National new builds by tech ───────────────────────────────────────────
def nat_build(df):
    return df.groupby("tech_cat")["BuildGen"].sum()

nb_b = nat_build(bld_b)
nb_c = nat_build(bld_c)
nb = pd.DataFrame({"Unconstrained_MW": nb_b, "Max1.30_MW": nb_c}).fillna(0)
nb["Delta_MW"] = nb["Max1.30_MW"] - nb["Unconstrained_MW"]
print("\n" + "=" * 60)
print("NATIONAL NEW BUILDS BY TECH (MW)")
print("=" * 60)
print(nb.sort_values("Delta_MW").round(0).to_string())

# ── 3. National retirements by tech ──────────────────────────────────────────
def nat_sus(df):
    return df.groupby("tech_cat")["SuspendGen"].sum()

ns_b = nat_sus(sus_b)
ns_c = nat_sus(sus_c)
ns = pd.DataFrame({"Unconstrained_MW": ns_b, "Max1.30_MW": ns_c}).fillna(0)
ns["Delta_MW"] = ns["Max1.30_MW"] - ns["Unconstrained_MW"]
print("\n" + "=" * 60)
print("NATIONAL RETIREMENTS BY TECH (MW suspended)")
print("=" * 60)
print(ns.sort_values("Delta_MW").round(0).to_string())

# ── 4. State-level capacity shifts ───────────────────────────────────────────
def state_cap(df):
    return df.groupby(["state","tech_cat"])["ActiveCap_MW"].sum()

sc_b = state_cap(cap_b)
sc_c = state_cap(cap_c)
sc = pd.DataFrame({"Unconstrained": sc_b, "Max1.30": sc_c}).fillna(0)
sc["Delta"] = sc["Max1.30"] - sc["Unconstrained"]

state_totals = sc.groupby(level=0)["Delta"].apply(lambda x: x.abs().sum()).nlargest(10)
print("\n" + "=" * 60)
print("STATE x TECH CAPACITY SHIFTS (top 10 states by change)")
print("=" * 60)
for state in state_totals.index:
    sub = sc.loc[state][sc.loc[state]["Delta"].abs() > 50].sort_values("Delta")
    if not sub.empty:
        print(f"\n  {state} (total abs change: {state_totals[state]:,.0f} MW):")
        print(sub[["Unconstrained","Max1.30","Delta"]].round(0).to_string())

# ── 5. BA-level new builds ───────────────────────────────────────────────────
def ba_build(df):
    return df.groupby(["hurdlereg","tech_cat"])["BuildGen"].sum()

bb_b = ba_build(bld_b)
bb_c = ba_build(bld_c)
bb = pd.DataFrame({"Unconstrained": bb_b, "Max1.30": bb_c}).fillna(0)
bb["Delta"] = bb["Max1.30"] - bb["Unconstrained"]

ba_totals = bb.groupby(level=0)["Delta"].apply(lambda x: x.abs().sum()).nlargest(10)
print("\n" + "=" * 60)
print("HURDLEREG NEW BUILDS (top 10 BAs by change)")
print("=" * 60)
for ba in ba_totals.index:
    sub = bb.loc[ba][bb.loc[ba]["Delta"].abs() > 50].sort_values("Delta")
    if not sub.empty:
        print(f"\n  {ba} (total abs change: {ba_totals[ba]:,.0f} MW):")
        print(sub[["Unconstrained","Max1.30","Delta"]].round(0).to_string())

# ── 6. Capacity factors for key fossil plants in changed regions ──────────────
print("\n" + "=" * 60)
print("CAPACITY FACTORS: PacifiCorp_West zones (p6, p8)")
print("=" * 60)
for run_name, cap_df, dis_df_path in [
    ("Unconstrained", cap_b, f"{out}/{base_run}/dispatch_zonal_annual_summary.csv"),
    ("Max1.30", cap_c, f"{out}/{con_run}/dispatch_zonal_annual_summary.csv"),
]:
    dis = pd.read_csv(dis_df_path)
    dis = dis[dis["gen_load_zone"].isin(["p6","p8"])]
    print(f"\n  {run_name}:")
    cf = dis[["gen_tech","gen_energy_source","Energy_GWh_typical_yr","GenCapacity_MW","capacity_factor"]].copy()
    cf = cf[cf["GenCapacity_MW"] > 0.01]
    cf["tech_cat"] = cf.apply(tech_cat, axis=1)
    fossil = cf[cf["tech_cat"] == "Fossil"]
    if not fossil.empty:
        print(fossil[["gen_tech","Energy_GWh_typical_yr","GenCapacity_MW","capacity_factor"]]
              .sort_values("Energy_GWh_typical_yr", ascending=False)
              .round(1).to_string(index=False))
