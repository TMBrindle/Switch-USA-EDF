"""
Florida generation mix and capacity across RGGI runs.
Zones: p91 (SERTP/Southern Co), p101 (FRCC/Seminole), p102 (FRCC/FPL)
"""
import pandas as pd
from pathlib import Path

SWITCH  = Path(__file__).parent
IN_DIR  = SWITCH / "in/2028/s4x1_edf_med"
OUT_BASE = SWITCH / "out"

FL_ZONES = {"p91", "p101", "p102"}

gi = pd.read_csv(IN_DIR / "gen_info.csv")
gi_fl = gi[gi["gen_load_zone"].isin(FL_ZONES)].copy()

def tech_category(t):
    if pd.isna(t): return "Other"
    tl = t.lower()
    if "landbasedwind" in tl or "onshore" in tl: return "OnshoreWind"
    if "offshorewind" in tl or "offshore" in tl: return "OffshoreWind"
    if "utilitypv" in tl or "solar" in tl:       return "Solar"
    if "battery" in tl or "storage" in tl:        return "Storage"
    if "nuclear" in tl or "uranium" in tl:        return "Nuclear"
    if "naturalgas" in tl or "natural gas" in tl or "combustion turbine" in tl: return "Gas"
    if "coal" in tl:                               return "Coal"
    if "hydro" in tl or "water" in tl:            return "Hydro"
    if "biomass" in tl or "bio" in tl:            return "Biomass"
    if "wind" in tl:                               return "OnshoreWind"
    return "Other"

gi["category"] = gi["gen_tech"].apply(tech_category)

CAT_ORDER = ["Nuclear","OnshoreWind","OffshoreWind","Solar","Storage","Gas","Coal","Hydro","Biomass","Other"]

# Runs to compare: (label, year, case, variant)
RUNS = [
    # edf_med baseline trajectory
    ("2028 baseline edf",  2028, "s4x1_edf_med", "baseline"),
    ("2028 nsp_va edf",    2028, "s4x1_edf_med", "nsp_va"),
    ("2028 norggi edf",    2028, "s4x1_edf_med", "norggi"),
    ("2030 baseline edf",  2030, "s4x1_edf_med", "baseline"),
    ("2030 nsp_va edf",    2030, "s4x1_edf_med", "nsp_va"),
    ("2035 baseline edf",  2035, "s4x1_edf_med", "baseline"),
    ("2035 nsp_va edf",    2035, "s4x1_edf_med", "nsp_va"),
    # ICF higher demand
    ("2028 baseline icf",  2028, "s4x1_icf",     "baseline"),
    ("2028 nsp_va icf",    2028, "s4x1_icf",     "nsp_va"),
    ("2030 nsp_va icf",    2030, "s4x1_icf",     "nsp_va"),
    ("2035 nsp_va icf",    2035, "s4x1_icf",     "nsp_va"),
]

# ── Generation (TWh/yr) ───────────────────────────────────────────────────────
print("=== Florida generation mix (TWh/yr) ===")
print(f"{'Category':<14}", end="")
for label, *_ in RUNS:
    print(f"  {label[:16]:>16}", end="")
print()
print("-" * (14 + len(RUNS) * 18))

run_data = {}
for label, year, case, variant in RUNS:
    path = OUT_BASE / "RGGI" / str(year) / case / variant / "dispatch.csv"
    if not path.exists():
        run_data[label] = {}
        continue
    d = pd.read_csv(path)
    fl = d[d["gen_load_zone"].isin(FL_ZONES)].copy()
    fl = fl.merge(gi[["GENERATION_PROJECT","category"]],
                  left_on="generation_project", right_on="GENERATION_PROJECT", how="left")
    by_cat = fl.groupby("category")["Energy_GWh_typical_yr"].sum() / 1e3  # TWh
    run_data[label] = by_cat

for cat in CAT_ORDER:
    vals = [run_data[l].get(cat, 0) for l, *_ in RUNS]
    if max(vals) < 0.1:
        continue
    print(f"{cat:<14}", end="")
    for v in vals:
        print(f"  {v:>16.1f}", end="")
    print()

# Total
print(f"{'TOTAL':<14}", end="")
for label, *_ in RUNS:
    t = run_data[label].sum() if len(run_data[label]) > 0 else 0
    print(f"  {t:>16.1f}", end="")
print()

# ── Capacity (GW) ─────────────────────────────────────────────────────────────
print("\n\n=== Florida installed capacity (GW) ===")
print(f"{'Category':<14}", end="")
for label, *_ in RUNS:
    print(f"  {label[:16]:>16}", end="")
print()
print("-" * (14 + len(RUNS) * 18))

cap_data = {}
for label, year, case, variant in RUNS:
    path = OUT_BASE / "RGGI" / str(year) / case / variant / "dispatch.csv"
    if not path.exists():
        cap_data[label] = {}
        continue
    d = pd.read_csv(path)
    fl = d[d["gen_load_zone"].isin(FL_ZONES)].copy()
    fl = fl.merge(gi[["GENERATION_PROJECT","category"]],
                  left_on="generation_project", right_on="GENERATION_PROJECT", how="left")
    # GenCapacity_MW is per-timepoint — take max per generator (nameplate)
    cap = fl.groupby(["generation_project","category"])["GenCapacity_MW"].max().reset_index()
    by_cat = cap.groupby("category")["GenCapacity_MW"].sum() / 1e3  # GW
    cap_data[label] = by_cat

for cat in CAT_ORDER:
    vals = [cap_data[l].get(cat, 0) for l, *_ in RUNS]
    if max(vals) < 0.01:
        continue
    print(f"{cat:<14}", end="")
    for v in vals:
        print(f"  {v:>16.2f}", end="")
    print()

# ── Solar CF ──────────────────────────────────────────────────────────────────
print("\n\n=== Florida solar capacity factor (%) ===")
for label, year, case, variant in RUNS:
    path = OUT_BASE / "RGGI" / str(year) / case / variant / "dispatch.csv"
    if not path.exists():
        continue
    d = pd.read_csv(path)
    fl_all = d[d["gen_load_zone"].isin(FL_ZONES)].copy()
    fl_all = fl_all.merge(gi[["GENERATION_PROJECT","category"]],
                          left_on="generation_project", right_on="GENERATION_PROJECT", how="left")
    fl_sol = fl_all[fl_all["category"] == "Solar"]
    if fl_sol.empty:
        continue
    gen_gwh  = fl_sol["Energy_GWh_typical_yr"].sum()
    cap_gw   = fl_sol.groupby("generation_project")["GenCapacity_MW"].max().sum() / 1e3
    cf = gen_gwh / (cap_gw * 8760) * 100 if cap_gw > 0 else 0
    print(f"  {label:<22}  Solar gen={gen_gwh/1e3:>7.1f} TWh  Cap={cap_gw:>6.2f} GW  CF={cf:>5.1f}%")

# ── New builds vs existing ────────────────────────────────────────────────────
print("\n\n=== Florida: new optimised builds (GW above predetermined) ===")
pre = pd.read_csv(IN_DIR / "gen_build_predetermined.csv")
pre["build_gen_predetermined"] = pd.to_numeric(
    pre["build_gen_predetermined"], errors="coerce").fillna(0)
pre = pre.merge(gi[["GENERATION_PROJECT","category","gen_load_zone"]],
                on="GENERATION_PROJECT", how="left")
pre_fl = pre[pre["gen_load_zone"].isin(FL_ZONES)]
pred_by_cat = pre_fl.groupby("category")["build_gen_predetermined"].sum() / 1e3

print(f"\n  Predetermined FL capacity by category (GW):")
for cat in CAT_ORDER:
    v = pred_by_cat.get(cat, 0)
    if v > 0.01:
        print(f"    {cat:<14}  {v:>6.2f}")

print(f"\n  New optimised builds above predetermined (GW):")
print(f"  {'Category':<14}", end="")
for label, *_ in RUNS:
    print(f"  {label[:16]:>16}", end="")
print()

for cat in CAT_ORDER:
    pred = pred_by_cat.get(cat, 0)
    vals = [max(cap_data[l].get(cat, 0) - pred, 0) for l, *_ in RUNS]
    if max(vals) < 0.01:
        continue
    print(f"  {cat:<14}", end="")
    for v in vals:
        print(f"  {v:>16.2f}", end="")
    print()

# ── Emissions ─────────────────────────────────────────────────────────────────
print("\n\n=== Florida CO2 emissions (Mt/yr) ===")
print(f"  {'Run':<24}  {'Gas Mt':>8}  {'Coal Mt':>8}  {'Total Mt':>9}")
for label, year, case, variant in RUNS:
    path = OUT_BASE / "RGGI" / str(year) / case / variant / "dispatch.csv"
    if not path.exists():
        continue
    d = pd.read_csv(path)
    fl = d[d["gen_load_zone"].isin(FL_ZONES)].copy()
    fl = fl.merge(gi[["GENERATION_PROJECT","category"]],
                  left_on="generation_project", right_on="GENERATION_PROJECT", how="left")
    gas  = fl[fl["category"]=="Gas"]["DispatchEmissions_tCO2_per_typical_yr"].sum() / 1e6
    coal = fl[fl["category"]=="Coal"]["DispatchEmissions_tCO2_per_typical_yr"].sum() / 1e6
    tot  = fl["DispatchEmissions_tCO2_per_typical_yr"].sum() / 1e6
    print(f"  {label:<24}  {gas:>8.2f}  {coal:>8.2f}  {tot:>9.2f}")
