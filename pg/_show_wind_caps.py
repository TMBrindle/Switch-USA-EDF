import yaml
with open("pg/settings/scenario_management.yml") as f:
    ss = yaml.safe_load(f)
sm = ss["settings_management"]
tags = None
for yr in [2028, 2030, 2035]:
    yr_caps = sm.get(yr, {}).get("all_cases", {}).get("MaxCapReq", {})
    if tags is None:
        tags = sorted([k for k in yr_caps if "WindGrowth_" in k])
print("Tag                                  2028      2030      2035")
print("-" * 65)
for tag in tags:
    vals = []
    for yr in [2028, 2030, 2035]:
        v = sm.get(yr, {}).get("all_cases", {}).get("MaxCapReq", {}).get(tag, {}).get("max_mw", "?")
        vals.append(v)
    print(tag.ljust(35) + "  " + str(vals[0]).rjust(8) + "  " + str(vals[1]).rjust(8) + "  " + str(vals[2]).rjust(8))

# Also show the methodology source
print()
print("Basis: EIA 860M 2024 baseline + (10yr install share of national 14,490 MW/yr limit) x (year - 2024)")
print("National baseline ~151,941 MW; annual limit = 2020 peak installation year per EIA 860M")
