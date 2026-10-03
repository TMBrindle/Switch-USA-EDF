"""
Build a gen_build_costs.csv alias file with the storage ITC (48E, 30% capex
credit) removed, by dividing out the 0.7x multiplier applied to
Utility-Scale Battery Storage_Lithium Ion_Advanced generators, without
re-running PowerGenome.

Usage: python make_no_storage_itc_alias.py <inputs_dir>
Writes <inputs_dir>/gen_build_costs.no_storage_itc.csv
"""
import sys
import pandas as pd

inputs_dir = sys.argv[1]
gen_info = pd.read_csv(f"{inputs_dir}/gen_info.csv")
costs = pd.read_csv(f"{inputs_dir}/gen_build_costs.csv", na_values=["."])

storage_gens = set(
    gen_info.loc[
        gen_info["gen_tech"] == "Utility-Scale Battery Storage_Lithium Ion_Advanced",
        "GENERATION_PROJECT",
    ]
)
mask = costs["GENERATION_PROJECT"].isin(storage_gens)
print(f"Adjusting {mask.sum()} of {len(costs)} gen_build_costs.csv rows "
      f"({len(storage_gens)} storage generators)")

# undo the 0.7x storage-ITC capex multiplier (mul, 0.7) applied by PowerGenome
factor = 1 / 0.7
costs.loc[mask, "gen_overnight_cost"] *= factor
costs.loc[mask, "gen_storage_energy_overnight_cost"] *= factor

out_path = f"{inputs_dir}/gen_build_costs.no_storage_itc.csv"
costs.to_csv(out_path, index=False, na_rep=".")
print(f"Wrote {out_path}")
