"""
Compare 2028 s4x1_edf_med va results:
  NEW: RGGI_gzr_mrp  (with regional wind caps)
  OLD: RGGI_gzr      (same GZR module, no MRP, no regional caps — best available proxy)

Focus: onshore wind by transreg, carbon price, total cost.
"""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent.parent
HIER = ROOT / "hierarchy.csv"
hier = pd.read_csv(HIER)
zone_transreg = hier.set_index("ba")["transreg"].to_dict()

def load_run(run_group, year, case, variant):
    p = ROOT / "switch" / "out" / run_group / str(year) / case / variant
    gb = pd.read_csv(p / "gen_build.csv")
    gb["BuildGen"] = pd.to_numeric(gb["BuildGen"], errors="coerce").fillna(0)
    gb["transreg"] = gb["gen_load_zone"].map(zone_transreg)

    cost = open(p / "total_cost.txt").read().strip()

    prices = pd.read_csv(p / "carbon_program_clearing_prices.csv")

    return gb, float(cost), prices

def wind_by_transreg(gb):
    wind = gb[gb["gen_tech"].str.contains("wind|Wind", na=False, regex=True)].copy()
    return wind.groupby(["transreg", "gen_tech"])["BuildGen"].sum().reset_index()

# Load both runs
gb_new, cost_new, prices_new = load_run("RGGI_gzr_mrp", 2028, "s4x1_edf_med", "va")
gb_old, cost_old, prices_old = load_run("RGGI_gzr",     2028, "s4x1_edf_med", "va")

# ── Carbon clearing prices ────────────────────────────────────────────────────
print("=== Carbon clearing prices ($/mt CO2) ===")
prices_new["run"] = "new (gzr+mrp)"
prices_old["run"] = "old (gzr only)"
pc = pd.concat([prices_new, prices_old]).sort_values(["CO2_PROGRAM", "run"])
print(pc.to_string(index=False))
print()

# ── Total cost ────────────────────────────────────────────────────────────────
print(f"=== Total system cost ===")
print(f"  New (gzr+mrp, with regional caps): ${cost_new:,.0f}")
print(f"  Old (gzr only, no regional caps):  ${cost_old:,.0f}")
print(f"  Delta:                             ${cost_new - cost_old:+,.0f}  ({(cost_new/cost_old - 1)*100:+.2f}%)")
print()

# ── Wind build by transreg ────────────────────────────────────────────────────
wn = wind_by_transreg(gb_new).rename(columns={"BuildGen": "new_mw"})
wo = wind_by_transreg(gb_old).rename(columns={"BuildGen": "old_mw"})
wc = wn.merge(wo, on=["transreg", "gen_tech"], how="outer").fillna(0)
wc["delta_mw"] = wc["new_mw"] - wc["old_mw"]
wc = wc[wc["delta_mw"].abs() > 1].sort_values("delta_mw")

print("=== Wind build changes (new - old, >1 MW delta) ===")
print(f"{'transreg':<15} {'gen_tech':<45} {'old_mw':>10} {'new_mw':>10} {'delta_mw':>10}")
print("-" * 95)
for _, r in wc.iterrows():
    print(f"{r['transreg']:<15} {r['gen_tech']:<45} {r['old_mw']:>10.0f} {r['new_mw']:>10.0f} {r['delta_mw']:>+10.0f}")

# ── Regional cap utilisation (new run only) ────────────────────────────────────
print()
print("=== Regional cap utilisation (new run) ===")
caps = pd.read_csv(ROOT / "switch/in/2028/s4x1_edf_med/max_cap_requirements.csv")
caps = caps[caps["MAX_CAP_PROGRAM"].str.startswith("MaxCapTag_WindGrowth_")]
max_cap_gens = pd.read_csv(ROOT / "switch/in/2028/s4x1_edf_med/max_cap_generators.csv")
max_cap_gens = max_cap_gens[max_cap_gens["MAX_CAP_PROGRAM"].str.startswith("MaxCapTag_WindGrowth_")]
pre = pd.read_csv(ROOT / "switch/in/2028/s4x1_edf_med/gen_build_predetermined.csv")
pre["build_gen_predetermined"] = pd.to_numeric(pre["build_gen_predetermined"], errors="coerce").fillna(0)

print(f"{'Program':<45} {'Cap':>10} {'Predet':>10} {'New opt':>10} {'Total':>10} {'Slack':>8}")
print("-" * 98)
for _, row in caps.sort_values("MAX_CAP_PROGRAM").iterrows():
    tag = row["MAX_CAP_PROGRAM"]
    cap = row["max_cap_mw"]
    tagged = max_cap_gens[max_cap_gens["MAX_CAP_PROGRAM"] == tag]["MAX_CAP_GEN"].tolist()
    pred_total = pre[pre["GENERATION_PROJECT"].isin(tagged)]["build_gen_predetermined"].sum()
    new_opt = gb_new[gb_new["GENERATION_PROJECT"].isin(tagged)]["BuildGen"].sum()
    total = pred_total + new_opt
    slack = cap - total
    flag = " BINDING" if abs(slack) < 10 else ""
    print(f"{tag:<45} {cap:>10.0f} {pred_total:>10.0f} {new_opt:>10.0f} {total:>10.0f} {slack:>8.0f}{flag}")
