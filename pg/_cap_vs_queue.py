import yaml, pandas as pd

with open("pg/settings/scenario_management.yml") as f:
    ss = yaml.safe_load(f)
sm = ss["settings_management"]

caps = {}
for yr in [2028, 2030]:
    yr_caps = sm.get(yr, {}).get("all_cases", {}).get("MaxCapReq", {})
    for tag, cfg in yr_caps.items():
        if "WindGrowth_" in tag:
            transreg = tag.replace("MaxCapTag_WindGrowth_", "")
            caps[(transreg, yr)] = cfg.get("max_mw", 0)

bl_df = pd.read_csv("docs/analysis/RGGI/wind_regional_growth_limits.csv", index_col=0)
baselines = bl_df["baseline_mw"].to_dict()

q = pd.read_csv("docs/analysis/RGGI/lbnl_queue_by_state.csv")
q_wind = q[q["tech"] == "OnshoreWind"].groupby(["transreg", "year_cutoff"])[["queue_mw", "ia_executed_mw"]].sum()

focus = ["ISONE", "NYISO", "PJM"]
hdr = ("Region", "Year", "Baseline MW", "Cap (total)", "New allowed", "Queue (active)", "Queue (IA exec)")
print("  ".join(f"{h:>15}" for h in hdr))
print("-" * 120)
for transreg in focus:
    for yr in [2028, 2030]:
        cap = caps.get((transreg, yr))
        baseline = baselines.get(transreg)
        new_allowed = round(cap - baseline) if cap and baseline else None
        try:
            row = q_wind.loc[(transreg, yr)]
            q_active = int(round(row["queue_mw"]))
            q_ia = int(round(row["ia_executed_mw"]))
        except KeyError:
            q_active = 0
            q_ia = 0
        cols = [
            transreg,
            str(yr),
            f"{baseline:,.0f}" if baseline else "?",
            f"{cap:,.0f}" if cap else "?",
            f"{new_allowed:,.0f}" if new_allowed else "?",
            f"{q_active:,}",
            f"{q_ia:,}",
        ]
        print("  ".join(f"{c:>15}" for c in cols))
    print()
