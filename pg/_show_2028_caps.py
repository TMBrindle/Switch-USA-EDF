import pandas as pd
import yaml

# LBNL queue: IA-executed + 25% of non-IA active, aggregated to transreg
q = pd.read_csv("docs/analysis/RGGI/lbnl_queue_by_state.csv")
wind28 = q[(q["tech"] == "OnshoreWind") & (q["year_cutoff"] == 2028)].copy()
wind28["non_ia_mw"] = wind28["queue_mw"] - wind28["ia_executed_mw"]
wind28["pipeline_new_mw"] = wind28["ia_executed_mw"] + 0.25 * wind28["non_ia_mw"]

by_transreg = wind28.groupby("transreg")[["queue_mw", "ia_executed_mw", "non_ia_mw", "pipeline_new_mw"]].sum()

# Baselines from EIA 860M analysis
bl = pd.read_csv("docs/analysis/RGGI/wind_regional_growth_limits.csv", index_col=0)
by_transreg = by_transreg.join(bl[["baseline_mw", "regional_annual_limit_mw"]])

by_transreg["cap_2028_pipeline"] = (by_transreg["baseline_mw"] + by_transreg["pipeline_new_mw"]).round(0)

# Also show existing regional methodology cap for comparison
with open("pg/settings/scenario_management.yml") as f:
    ss = yaml.safe_load(f)
sm = ss["settings_management"]
current_caps = {}
for tag, cfg in sm.get(2028, {}).get("all_cases", {}).get("MaxCapReq", {}).items():
    if "WindGrowth_" in tag:
        transreg = tag.replace("MaxCapTag_WindGrowth_", "")
        current_caps[transreg] = cfg.get("max_mw", 0)
by_transreg["cap_2028_regional_method"] = pd.Series(current_caps)

by_transreg["diff_mw"] = (by_transreg["cap_2028_pipeline"] - by_transreg["cap_2028_regional_method"]).round(0)
by_transreg["diff_pct"] = (by_transreg["diff_mw"] / by_transreg["cap_2028_regional_method"] * 100).round(1)

cols = ["baseline_mw", "ia_executed_mw", "non_ia_mw", "pipeline_new_mw",
        "cap_2028_pipeline", "cap_2028_regional_method", "diff_mw", "diff_pct"]
print("2028 onshore wind caps: pipeline method vs regional method")
print("(cap = baseline + IA-executed + 25% non-IA;  regional = baseline + share*14490*4)\n")
print(by_transreg[cols].fillna(0).round(0).to_string())

# State-level detail for ISONE, NYISO, PJM
print("\n--- State-level detail (ISONE / NYISO / PJM) ---")
detail = wind28[wind28["transreg"].isin(["ISONE", "NYISO", "PJM"])].copy()
detail["cap_new"] = detail["pipeline_new_mw"].round(0)
print(detail[["transreg", "state", "queue_mw", "ia_executed_mw", "non_ia_mw", "pipeline_new_mw"]].round(0).sort_values(["transreg","state"]).to_string(index=False))
