"""
Diagnose regional wind cap infeasibility.
Checks whether predetermined + existing capacity per region exceeds the cap.

Usage:
  cd switch
  python diagnose_max_cap.py 2028 s4x1_edf_med
"""
import sys, pandas as pd
from pathlib import Path

year = sys.argv[1] if len(sys.argv) > 1 else "2028"
case = sys.argv[2] if len(sys.argv) > 2 else "s4x1_edf_med"

case_dir = Path(__file__).parent / "in" / year / case

# Load files
gen_info = pd.read_csv(case_dir / "gen_info.csv", usecols=["GENERATION_PROJECT", "gen_tech", "gen_load_zone"])
max_cap_gens = pd.read_csv(case_dir / "max_cap_generators.csv")
max_cap_reqs = pd.read_csv(case_dir / "max_cap_requirements.csv")
gen_build_pre = pd.read_csv(case_dir / "gen_build_predetermined.csv")

gen_build_pre["build_gen_predetermined"] = pd.to_numeric(
    gen_build_pre["build_gen_predetermined"], errors="coerce"
).fillna(0)

print(f"Diagnosing {year}/{case}")
print(f"  gen_build_predetermined rows: {len(gen_build_pre)}")
print(f"  max_cap_generators rows: {len(max_cap_gens)}")
print()

# Check each regional WindGrowth_ tag
tags = max_cap_reqs[max_cap_reqs["MAX_CAP_PROGRAM"].str.startswith("MaxCapTag_WindGrowth_")]
tags = tags[tags["PERIOD"] == int(year)]

print(f"{'Tag':<45} {'Cap_MW':>10} {'Pred_total':>12} {'Deficit':>10}")
print("-" * 82)

any_problem = False
for _, row in tags.iterrows():
    tag = row["MAX_CAP_PROGRAM"]
    cap = row["max_cap_mw"]

    # Get generators tagged for this program
    tagged_gens = max_cap_gens[max_cap_gens["MAX_CAP_PROGRAM"] == tag]["MAX_CAP_GEN"].tolist()

    # Sum ALL predetermined capacity for these generators (all build years)
    pred_rows = gen_build_pre[gen_build_pre["GENERATION_PROJECT"].isin(tagged_gens)]
    pred_total = pred_rows["build_gen_predetermined"].sum()

    deficit = pred_total - cap
    flag = " <<< INFEASIBLE" if deficit > 0.1 else ""
    if deficit > 0.1:
        any_problem = True
    print(f"{tag:<45} {cap:>10.1f} {pred_total:>12.1f} {deficit:>10.1f}{flag}")

print()
if any_problem:
    print("*** At least one tag has predetermined capacity exceeding cap — INFEASIBLE ***")
    print()
    # Show which generators are the culprits
    for _, row in tags.iterrows():
        tag = row["MAX_CAP_PROGRAM"]
        cap = row["max_cap_mw"]
        tagged_gens = max_cap_gens[max_cap_gens["MAX_CAP_PROGRAM"] == tag]["MAX_CAP_GEN"].tolist()
        pred_rows = gen_build_pre[gen_build_pre["GENERATION_PROJECT"].isin(tagged_gens)].copy()
        pred_total = pred_rows["build_gen_predetermined"].sum()
        if pred_total > cap + 0.1:
            print(f"\n{tag} detail (cap={cap:.1f}, pred_total={pred_total:.1f}):")
            pred_rows = pred_rows.sort_values("build_gen_predetermined", ascending=False)
            print(pred_rows.head(30).to_string(index=False))
else:
    print("No infeasibility detected from predetermined capacity alone.")
    print("The infeasibility may come from another constraint (e.g. RPS, load balance).")
    print("Consider running compute_iis.py to find the IIS.")
