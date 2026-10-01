"""Check how much new wind was built per region in the May 16 (pre-cap) solve."""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent

gen_build = pd.read_csv(ROOT / "out/RGGI_gzr_mrp/2028/s4x1_edf_med/va/gen_build.csv")
max_cap_gens = pd.read_csv(ROOT / "in/2028/s4x1_edf_med/max_cap_generators.csv")

wind_tags = max_cap_gens[max_cap_gens["MAX_CAP_PROGRAM"].str.startswith("MaxCapTag_WindGrowth_")].copy()
wind_tags = wind_tags.rename(columns={"MAX_CAP_GEN": "GENERATION_PROJECT"})

merged = gen_build.merge(wind_tags, on="GENERATION_PROJECT", how="inner")
merged["BuildGen"] = pd.to_numeric(merged["BuildGen"], errors="coerce").fillna(0)

# gen_build.csv shows total capacity in period (not build_year breakdown)
# Sum by region tag
total_cap = merged.groupby("MAX_CAP_PROGRAM")["BuildGen"].sum()
new_builds = pd.Series(dtype=float)  # not available in this output format

caps = pd.read_csv(ROOT / "in/2028/s4x1_edf_med/max_cap_requirements.csv")
caps = caps[caps["MAX_CAP_PROGRAM"].str.startswith("MaxCapTag_WindGrowth_")]
caps = caps.set_index("MAX_CAP_PROGRAM")["max_cap_mw"]

result = pd.DataFrame({
    "cap_mw": caps,
    "total_build_may16": total_cap,
})
result["headroom"] = result["cap_mw"] - result["total_build_may16"]
result["over_cap"] = result["total_build_may16"] - result["cap_mw"]

print(result.fillna(0).round(1).to_string())
print()
print("Regions where May 16 solve would violate new regional cap:")
over = result[result["total_build_may16"] > result["cap_mw"] + 0.1]
print(over.fillna(0).round(1).to_string() if not over.empty else "  None")
