"""
Detailed generation mix, wind cap utilisation, and emissions leakage analysis
across the 95% GZR + NSP + MRP demand sensitivity runs.
"""
import pandas as pd
from pathlib import Path

ROOT    = Path(__file__).parent.parent
SWITCH  = Path(__file__).parent
IN_DIR  = SWITCH / "in/2028/s4x1_edf_med"
OUT_BASE = SWITCH / "out"

hier      = pd.read_csv(ROOT / "hierarchy.csv")
zone_rv   = hier.set_index("ba")["rggi_va"].to_dict()
zone_tr   = hier.set_index("ba")["transreg"].to_dict()
zone_st   = hier.set_index("ba")["st"].to_dict()

gi = pd.read_csv(IN_DIR / "gen_info.csv")
gi["rggi_va"] = gi["gen_load_zone"].map(zone_rv)
gi["transreg"] = gi["gen_load_zone"].map(zone_tr)

RGGI_STATES = {"CT","DE","MA","MD","ME","NH","NJ","NY","PA","RI","VT","VA"}

def is_rggi(zone):
    return zone_st.get(zone, "") in RGGI_STATES

gi["in_rggi"] = gi["gen_load_zone"].apply(is_rggi)

def tech_category(t):
    if t is None: return "Other"
    tl = t.lower()
    if "landbasedwind" in tl or "onshore wind" in tl: return "OnshoreWind"
    if "offshorewind" in tl or "offshore wind" in tl: return "OffshoreWind"
    if "utilitypv" in tl or "solar" in tl:            return "Solar"
    if "battery" in tl or "storage" in tl:            return "Storage"
    if "nuclear" in tl or "uranium" in tl:            return "Nuclear"
    if "naturalgas" in tl or "natural gas" in tl or "combustion turbine" in tl: return "Gas"
    if "coal" in tl:                                  return "Coal"
    if "hydro" in tl or "water" in tl:                return "Hydro"
    if "biomass" in tl or "bio" in tl:                return "Biomass"
    if "wind" in tl:                                  return "OnshoreWind"
    return "Other"

gi["category"] = gi["gen_tech"].apply(tech_category)

# ── Load max_cap data for wind caps ──────────────────────────────────────────
mcr = pd.read_csv(IN_DIR / "max_cap_requirements.csv")
mcr = mcr[mcr["MAX_CAP_PROGRAM"].str.startswith("MaxCapTag_WindGrowth_")]
mcg = pd.read_csv(IN_DIR / "max_cap_generators.csv")
mcg = mcg[mcg["MAX_CAP_PROGRAM"].str.startswith("MaxCapTag_WindGrowth_")]
pre = pd.read_csv(IN_DIR / "gen_build_predetermined.csv")
pre["build_gen_predetermined"] = pd.to_numeric(
    pre["build_gen_predetermined"], errors="coerce").fillna(0)

RUNS = [
    ("baseline (×1.00)",  "RGGI_gzr95_mrp/2028/s4x1_edf_med/nsp_va"),
    ("×1.05",             "RGGI_gzr95_mrp_demand/d105/s4x1_edf_med/nsp_va"),
    ("×1.10",             "RGGI_gzr95_mrp_demand/d110/s4x1_edf_med/nsp_va"),
    ("×1.12",             "RGGI_gzr95_mrp_demand/d112/s4x1_edf_med/nsp_va"),
    ("×1.15",             "RGGI_gzr95_mrp_demand/d115/s4x1_edf_med/nsp_va"),
    ("×1.20",             "RGGI_gzr95_mrp_demand/d120/s4x1_edf_med/nsp_va"),
]

# ── Confirm NSP aliases in options ───────────────────────────────────────────
print("=== Run configuration confirmation ===")
for label, rel_path in RUNS:
    out_dir = SWITCH / "out" / rel_path
    scenarios_f = out_dir / "inputs" / "scenarios.txt" if (out_dir / "inputs").exists() else None
    # Check via switch_inputs_version or confirm via options logged
    log_dir = SWITCH / "logs"
    # Most reliable: check carbon_policies file used
    cp_f = out_dir / "carbon_program_clearing_prices.csv"
    exists = "YES" if cp_f.exists() else "MISSING"
    # Verify NSP from rps_requirements output (nsp file has no requirements)
    rps_f = out_dir / "inputs" / "rps_requirements.csv" if (out_dir / "inputs").exists() else None
    print(f"  {label:<22} outputs: {exists}  path: out/{rel_path}")
print()

# ── Generation mix by region ──────────────────────────────────────────────────
print("=== Generation mix: RGGI_VA vs non-RGGI_VA (TWh/yr) ===")
cat_order = ["Nuclear","OnshoreWind","OffshoreWind","Solar","Hydro","Biomass","Gas","Coal","Storage","Other"]

mix_rows = []
for label, rel_path in RUNS:
    out_dir = SWITCH / "out" / rel_path
    disp = pd.read_csv(out_dir / "dispatch.csv")
    disp = disp.merge(
        gi[["GENERATION_PROJECT","category","rggi_va","in_rggi","transreg","gen_energy_source"]],
        left_on="generation_project", right_on="GENERATION_PROJECT", how="left"
    )
    disp["energy_twh"] = disp["Energy_GWh_typical_yr"] / 1e6

    for region, mask in [("RGGI_VA",  disp["rggi_va"] == "RGGI_VA"),
                          ("non-RGGI_VA", disp["rggi_va"] != "RGGI_VA")]:
        sub = disp[mask].groupby("category")["energy_twh"].sum()
        for cat in cat_order:
            mix_rows.append({"run": label, "region": region, "category": cat,
                             "twh": sub.get(cat, 0)})

mix = pd.DataFrame(mix_rows)

# Print RGGI_VA mix table
print("\n  RGGI_VA generation (TWh/yr):")
rggi_mix = mix[mix["region"] == "RGGI_VA"].pivot(index="category", columns="run", values="twh")
rggi_mix = rggi_mix.reindex(cat_order).fillna(0)
# Drop rows that are all zero
rggi_mix = rggi_mix[(rggi_mix > 0.001).any(axis=1)]
cols = [l for l, _ in RUNS]
print(rggi_mix[cols].applymap(lambda x: f"{x*1000:.0f}").to_string())
print("  (values in GWh/yr)")

# ── Wind cap utilisation by transreg ─────────────────────────────────────────
print("\n\n=== Wind cap utilisation (RGGI transregs only) ===")
RGGI_TRANSREGS = {"ISONE","NYISO","PJM"}

hdr = f"{'Program':<35} {'Cap MW':>8}"
for label, _ in RUNS:
    hdr += f" {label[:7]:>10}"
print(hdr)
print("-" * (len(hdr) + 20))

for _, cap_row in mcr[mcr["MAX_CAP_PROGRAM"].str.contains("ISONE|NYISO|PJM")].sort_values("MAX_CAP_PROGRAM").iterrows():
    tag = cap_row["MAX_CAP_PROGRAM"]
    cap = cap_row["max_cap_mw"]
    tagged = mcg[mcg["MAX_CAP_PROGRAM"] == tag]["MAX_CAP_GEN"].tolist()
    pred_mw = pre[pre["GENERATION_PROJECT"].isin(tagged)]["build_gen_predetermined"].sum()

    row_str = f"{tag:<35} {cap:>8.0f}"
    for label, rel_path in RUNS:
        out_dir = SWITCH / "out" / rel_path
        gb = pd.read_csv(out_dir / "gen_build.csv")
        gb["BuildGen"] = pd.to_numeric(gb["BuildGen"], errors="coerce").fillna(0)
        # Only new ATB builds (not predetermined)
        tagged_gens = set(tagged)
        gb_tagged = gb[gb["GENERATION_PROJECT"].isin(tagged_gens)]
        # Subtract predetermined to get new opt builds
        new_opt = gb_tagged["BuildGen"].sum() - pred_mw
        total = pred_mw + new_opt
        slack = cap - total
        flag = "*" if abs(slack) < 50 else " "
        row_str += f" {new_opt:>9.0f}{flag}"
    print(row_str)
print("  (* = binding within 50 MW)  values = new optimised builds above predetermined")

# ── Emissions leakage ─────────────────────────────────────────────────────────
print("\n\n=== Emissions leakage (CO2 Mt/yr) ===")

leak_hdr = f"{'Region':<20}"
for label, _ in RUNS:
    leak_hdr += f" {label[:7]:>10}"
print(leak_hdr)
print("-" * len(leak_hdr))

for region_label, mask_fn in [
    ("RGGI_VA",      lambda d: d["rggi_va"] == "RGGI_VA"),
    ("RGGI non-VA",  lambda d: (d["in_rggi"]) & (d["rggi_va"] != "RGGI_VA")),
    ("Non-RGGI",     lambda d: ~d["in_rggi"]),
]:
    row_str = f"{region_label:<20}"
    for label, rel_path in RUNS:
        out_dir = SWITCH / "out" / rel_path
        disp = pd.read_csv(out_dir / "dispatch.csv")
        disp = disp.merge(
            gi[["GENERATION_PROJECT","rggi_va","in_rggi"]],
            left_on="generation_project", right_on="GENERATION_PROJECT", how="left"
        )
        co2 = disp[mask_fn(disp)]["DispatchEmissions_tCO2_per_typical_yr"].sum() / 1e6
        row_str += f" {co2:>10.2f}"
    print(row_str)

# Also print total system emissions
row_str = f"{'TOTAL':20}"
for label, rel_path in RUNS:
    out_dir = SWITCH / "out" / rel_path
    disp = pd.read_csv(out_dir / "dispatch.csv")
    disp = disp.merge(gi[["GENERATION_PROJECT","rggi_va","in_rggi"]],
                      left_on="generation_project", right_on="GENERATION_PROJECT", how="left")
    co2 = disp["DispatchEmissions_tCO2_per_typical_yr"].sum() / 1e6
    row_str += f" {co2:>10.2f}"
print(row_str)

# ETS2 (VA state cap) as check on non-RGGI VA leakage
print("\n\n=== ETS2 (VA non-RGGI cap) clearing prices ===")
row_str = f"{'ETS2 $/mt':<20}"
for label, rel_path in RUNS:
    out_dir = SWITCH / "out" / rel_path
    prices = pd.read_csv(out_dir / "carbon_program_clearing_prices.csv")
    ets2 = prices[prices["CO2_PROGRAM"] == "ETS 2"]
    price = ets2["clearing_price_dollar_per_tco2"].iloc[0] if not ets2.empty else float("nan")
    row_str += f" {price:>10.2f}"
print(row_str)
row_str = f"{'ETS2 emis Mt':<20}"
for label, rel_path in RUNS:
    out_dir = SWITCH / "out" / rel_path
    prices = pd.read_csv(out_dir / "carbon_program_clearing_prices.csv")
    ets2 = prices[prices["CO2_PROGRAM"] == "ETS 2"]
    emis = ets2["emissions_tco2_per_yr"].iloc[0] / 1e6 if not ets2.empty else float("nan")
    row_str += f" {emis:>10.2f}"
print(row_str)
