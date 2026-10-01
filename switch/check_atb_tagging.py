"""Verify ATB wind generators are now tagged by regional MaxCapTag_WindGrowth_ programs."""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent.parent
gen_info = pd.read_csv(
    ROOT / "switch/in/2028/s4x1_edf_med/gen_info.csv",
    usecols=["GENERATION_PROJECT", "gen_tech", "gen_load_zone"],
)
hier = pd.read_csv(ROOT / "hierarchy.csv")
zone_transreg = hier.set_index("ba")["transreg"].to_dict()
gen_info["transreg"] = gen_info["gen_load_zone"].map(zone_transreg)

mcg = pd.read_csv(ROOT / "switch/in/2028/s4x1_edf_med/max_cap_generators.csv")
wind_tags = mcg[mcg["MAX_CAP_PROGRAM"].str.startswith("MaxCapTag_WindGrowth_")]

for tr in ["ISONE", "NYISO", "PJM", "MISO", "NorthernGrid"]:
    tag = f"MaxCapTag_WindGrowth_{tr}"
    tagged_gens = set(wind_tags[wind_tags["MAX_CAP_PROGRAM"] == tag]["MAX_CAP_GEN"])
    tr_gens = gen_info[gen_info["transreg"] == tr]
    tr_wind = tr_gens[tr_gens["gen_tech"].str.contains("wind|Wind", na=False, regex=True)].copy()
    tr_wind["tagged"] = tr_wind["GENERATION_PROJECT"].isin(tagged_gens)
    print(f"--- {tr} (cap tagged: {len(tagged_gens)} gens) ---")
    print(tr_wind.groupby(["gen_tech", "tagged"]).size().rename("count").to_string())
    print()
