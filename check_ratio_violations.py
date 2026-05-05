import pandas as pd

base = "D:/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS"

hist = pd.read_csv(f"{base}/gen_zone_load_ratio_hurdlereg.csv")
summary = pd.read_csv(f"{base}/switch/out/2035/s4x1_caelp_parclust_zoned_max1.5/gen_zone_ratio_summary.csv")

merged = summary[summary['TYPE']=='group'].merge(
    hist[['LOAD_ZONE','historical_max_annual_ratio']],
    left_on='LOAD_ZONE', right_on='LOAD_ZONE', how='left'
)
merged['excess'] = merged['actual_annual_ratio'] - merged['historical_max_annual_ratio']
merged = merged.sort_values('excess', ascending=False)

print("Groups exceeding historical max (infeasible with hist_range):")
violating = merged[merged['excess'] > 0.001]
print(violating[['LOAD_ZONE','historical_max_annual_ratio','actual_annual_ratio','excess']].to_string(index=False))
print()
print("All groups sorted by excess:")
print(merged[['LOAD_ZONE','historical_max_annual_ratio','actual_annual_ratio','excess']].to_string(index=False))
