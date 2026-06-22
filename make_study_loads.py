"""
Create baseline user load profiles for all future years using PowerGenome ReEDS
data for one specific historical year. Then create demand response ("flexible
load") shapes to represent load growth beyond that and international
imports/exports.

This grows 2023 PowerGenome loads to future years, using ICF growth rates
(growth_rates/zone_growth.csv), then adds the difference between that and the
existing PowerGenome loads as "load_growth". (This gives a fairly good match
to EIA's reports of 2023 and 2024 US loads.)

It also adds net exports as a "us_exports" flexible load (which should be
treated as inflexible in PowerGenome). This applies the month-hour average for a
recent period (currently 2024), since time-synced values aren't available for
the historical weather period (EIA 930 covers 2015-present, but ReEDS historical
loads and weather are for 2006-13). This may also better capture changes in
import/export behavior since the historical weather years.

TODO: don't store ReEDS 2023 loads as is for the underlying load shapes;
instead grow them as needed to 2025, then store that as the underlying
load shape and calculate growth on top of that; this may also simplify
the lower growth case

"""

# %%#################
# Setup
####################
from pathlib import Path
import json
import os
import pandas as pd
from powergenome.util import load_settings
from powergenome.generators import GeneratorClusters
from powergenome.util import (
    init_pudl_connection,
    load_settings,
)
from pg_to_switch import short_fn

# TODO: get from argv
settings_dir = "pg/settings"
# Growth rate files produced by R script (growth_rates/<case>/dc/ and nondc/)
# Each subdirectory contains zone_growth_YYYY-YYYY.csv files with columns
# load_zone, avg_growth, peak_growth.
growth_case = "epri_med"
dc_growth_path = f"growth_rates/{growth_case}/dc"
nondc_growth_path = f"growth_rates/{growth_case}/nondc"
reeds_load_table = "load_curves_nrel_reeds"
base_year = 2023
start_year = 2023
end_year = 2050
# baseline loads will be stored here; pg/settings/demand.yml/regional_load_fn
# should point to this file
user_load_file = f"reeds_{base_year}_loads.csv.zip"
# should match pg/settings/demand.yml/electrification
user_load_scenario = "base"
# load growth and exports will be stored here; pg/settings/flexible_load.yml/demand_response_fn
# should point to this file
demand_response_file = f"load_adjustments_{growth_case}.csv.zip"
# should match pg/settings/flexible_load.yml/demand_response
normal_growth_scenario = "base"

# details for lower growth scenario
lower_growth_base_year = 2025  # growth will be restrained after this year
lower_growth_factor = 1 / 3  # reduction in growth beyond 2025 level
lower_growth_scenario = "lower_growth"  # flexible_load.yml/demand_response

# resource names; must match keys in pg/settings/flexible_load.yml/flexible_demand_resources
dc_resource_name = "load_growth_datacenter"
nondc_resource_name = "load_growth_electrification"
exports_resource_name = "us_exports"
export_averaging_years = [2024]
# file within settings["RESOURCE_GROUPS"] with info on virtual generators
# representing imports (path and file will be created if not present)
imports_json = "imports/imports_group.json"

print(f"Reading settings from {settings_dir}")

settings = load_settings(settings_dir)
pudl_engine, pudl_out, pg_engine = init_pudl_connection(
    freq="AS",
    start_year=min(settings.get("eia_data_years")),
    end_year=max(settings.get("eia_data_years")),
    pudl_db=settings.get("PUDL_DB"),
    pg_db=settings.get("PG_DB"),
)

load_file_path = Path(settings["input_folder"]) / user_load_file
dr_file_path = Path(settings["input_folder"]) / demand_response_file


# TODO: read table(s) from pudl_engine instead of directly from latest PUDL
# (for replicability and consistency with other inputs); to do this, we will
# need to include core_eia930__hourly_interchange table in make_retro_pudl_data.py
# and decide whether to use the pre-2023-12 or post-2023-12 schema for it (and here).
def read_pudl(tbl):
    url = f"https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop/nightly/{tbl}.parquet"
    print(f"Reading {url}")
    return pd.read_parquet(url)


# %%#################
# Generate user load profiles (only for one model year, will be reused)
####################
print(
    f"Reading {base_year} ReEDS loads from table {reeds_load_table} via "
    f"{pg_engine.url}"
)
base_loads = pd.read_sql(
    f"select * from {reeds_load_table} where year = {base_year}",
    con=pg_engine,
).rename(columns={"year": "base_year"})

# Shift from UTC to model time zone (PowerGenome does this when using loads
# directly from PG_DB, but not when using user load profiles)
n_steps = settings.get("utc_offset", 0)
print(f"Applying {n_steps} hour offset to shift loads from UTC to model time zone.")
base_loads = base_loads.sort_values(["region", "weather_year", "time_index"])
base_loads["load_mw"] = base_loads.groupby(["region", "weather_year"])["load_mw"].transform(
    lambda s: pd.np.roll(s.values, n_steps)
)

print(f"Saving {base_year} loads for {start_year}-{end_year} in {load_file_path}")
base_wide = (
    pd.concat(
        (base_loads.assign(model_year=y) for y in range(start_year, end_year + 1)),
        ignore_index=True,
    )
    .assign(scenario=user_load_scenario)
    .pivot(
        columns=["model_year", "scenario", "region"],
        index="time_index",
        values="load_mw",
    )
    .sort_index(axis=0)
    .astype(int)
)
base_wide.to_csv(load_file_path, index=False)

# %%#################
# Calculate load growth each year (will be added as flexible load)
####################
print("Calculating base year stats")
base_stats = (
    base_loads.groupby("region")
    .agg(avg_base=("load_mw", "mean"), peak_base=("load_mw", "max"))
    .reset_index()
)


def read_compounded_growth_rates(growth_path):
    """Read zone_growth_YYYY-YYYY.csv files and compound rates across year blocks."""
    target_rates = pd.DataFrame()
    for fl in sorted(os.listdir(growth_path)):
        yrs = [int(y) for y in fl.split('_')[2].split('.')[0].split('-')]
        rates = pd.read_csv(f"{growth_path}/{fl}")
        for s in ["avg", "peak"]:
            rates[f"{s}_growth"] = rates[f"{s}_growth"].clip(0, None)
        rates = pd.concat(
            [rates.assign(year=y) for y in range(start_year, end_year + 1)],
            ignore_index=True,
        )
        for y in range(start_year, end_year + 1):
            for s in ["avg", "peak"]:
                localEnd = yrs[1] if y >= yrs[1] else y if y > yrs[0] else yrs[0]
                mask = rates["year"] == y
                rates.loc[mask, f"{s}_growth"] = (
                    1 + rates.loc[mask, f"{s}_growth"]
                ) ** (localEnd - yrs[0])
        if target_rates.shape[0] == 0:
            target_rates = rates
        else:
            target_rates = target_rates.merge(rates, on=["load_zone", "year"])
            for s in ["avg", "peak"]:
                v = f"{s}_growth"
                target_rates = target_rates.assign(
                    **{v: target_rates[f"{v}_x"] * target_rates[f"{v}_y"]}
                )
            target_rates = target_rates[["load_zone", "year", "avg_growth", "peak_growth"]]
    return target_rates.rename(columns={"load_zone": "region"})


def build_growth_timeseries(rates, resource_name, scenario):
    """Convert compounded growth rates to hourly timeseries for one resource.

    Returns (target_stats DataFrame, long-form growth DataFrame).
    """
    # fraction f and offset b are found by solving:
    # ab * (1 + f) + b = at  and  pb * (1 + f) + b = pt
    # => f = (pt - at) / (pb - ab) - 1  and  b = at - ab * (1 + f)
    ts = base_stats.merge(rates)
    for s in ["avg", "peak"]:
        ts[f"{s}_targ"] = ts[f"{s}_base"] * ts[f"{s}_growth"]
    ts["fraction"] = ts.eval("(peak_targ - avg_targ) / (peak_base - avg_base) - 1")
    ts["offset"] = ts.eval("avg_targ - avg_base * (1 + fraction)")

    g = base_loads.merge(ts[["region", "year", "offset", "fraction"]], on="region")
    growth_mw = (g["load_mw"] * g["fraction"] + g["offset"]).clip(0, None).round(3)
    # keep only the columns needed downstream (pivot + lower-growth merge);
    # this dataframe has one row per region x weather_year x hour x model_year
    # (~230M rows for the full BA/year range), so dropping unused columns and
    # using category dtype for repeated strings avoids multi-GB memory blowup
    g = pd.DataFrame(
        {
            "time_index": g["time_index"],
            "region": g["region"].astype("category"),
            "year": g["year"],
            "growth_mw": growth_mw,
            "resource_name": pd.Categorical([resource_name] * len(g)),
            "scenario": pd.Categorical([scenario] * len(g)),
        }
    )
    return ts, g


print(f"Calculating DC and non-DC load growth from {base_year} for {start_year}-{end_year}.")
dc_rates = read_compounded_growth_rates(dc_growth_path)
nondc_rates = read_compounded_growth_rates(nondc_growth_path)

dc_target_stats, dc_growth = build_growth_timeseries(dc_rates, dc_resource_name, normal_growth_scenario)
nondc_target_stats, nondc_growth = build_growth_timeseries(nondc_rates, nondc_resource_name, normal_growth_scenario)

Path("switch/Scripts/Growth_Profiles").mkdir(parents=True, exist_ok=True)
dc_target_stats.to_csv(f"switch/Scripts/Growth_Profiles/{growth_case}_dc_targets.csv", index=False)
nondc_target_stats.to_csv(f"switch/Scripts/Growth_Profiles/{growth_case}_nondc_targets.csv", index=False)

dc_growth_wide = dc_growth.pivot(
    index="time_index",
    columns=["resource_name", "year", "scenario", "region"],
    values="growth_mw",
).sort_index(axis=0)
nondc_growth_wide = nondc_growth.pivot(
    index="time_index",
    columns=["resource_name", "year", "scenario", "region"],
    values="growth_mw",
).sort_index(axis=0)
growth_wide = pd.concat([dc_growth_wide, nondc_growth_wide], axis=1)
del dc_growth_wide, nondc_growth_wide

# %%############
# create lower-growth scenario (1/3 as much growth in 2026 and beyond)
print(
    f"Creating reduced growth scenario {lower_growth_scenario} with {lower_growth_factor} as much growth after {lower_growth_base_year}"
)

# Apply lower-growth scaling to both resources together
combined_growth = pd.concat([dc_growth, nondc_growth], ignore_index=True)
del dc_growth, nondc_growth

keys = ["resource_name", "scenario", "region", "time_index"]
lower_base = combined_growth.query("year == @lower_growth_base_year")[keys + ["growth_mw"]].rename(
    columns={"growth_mw": "base_mw"}
)
growth_lower = combined_growth[keys + ["year", "growth_mw"]].merge(lower_base)

growth_lower["scenario"] = lower_growth_scenario
mask = growth_lower["year"] > lower_growth_base_year
growth_lower.loc[mask, "growth_mw"] = growth_lower.loc[
    mask, "base_mw"
] + lower_growth_factor * (
    growth_lower.loc[mask, "growth_mw"] - growth_lower.loc[mask, "base_mw"]
)
growth_lower_wide = growth_lower.pivot(
    index="time_index",
    columns=["resource_name", "year", "scenario", "region"],
    values="growth_mw",
).sort_index(axis=0)

del combined_growth, growth_lower

# %%############
# Calculate net exports for each zone by month and hour, then use those to define
# us_exports "flexible" load (for exports) and virtual generators (for imports)
###############
print("Calculating net US exports for each load zone")

ba_trade = read_pudl("core_eia930__hourly_interchange")
ba = read_pudl("core_eia__codes_balancing_authorities").set_index("code")

# simplify column names and get neighbor region name
# note: positive interchange indicates exports (https://www.eia.gov/electricity/gridmonitor/about)
ba_trade = ba_trade.rename(
    columns={
        "interchange_reported_mwh": "exports",
        "balancing_authority_code_eia": "ba_code",
        "balancing_authority_code_adjacent_eia": "neighbor_code",
    }
)
ba_trade["neighbor_region"] = ba_trade["neighbor_code"].map(
    ba["balancing_authority_region_name_eia"]
)

pair_trade = (
    ba_trade.query(f"neighbor_region.isin({'Canada', 'Mexico'})")
    .groupby(["datetime_utc", "neighbor_code", "ba_code"])["exports"]
    .sum()
    .reset_index()
)

# Get averages by month of year and hour of day, in model time zone
print("Calculating average US exports for each month-hour combination")
pair_trade["datetime"] = pair_trade["datetime_utc"] + pd.Timedelta(
    hours=settings["utc_offset"]
)
pair_trade["month"] = pair_trade["datetime"].dt.month
pair_trade["hour"] = pair_trade["datetime"].dt.hour
avg = (
    pair_trade[pair_trade["datetime"].dt.year.isin(export_averaging_years)]
    .groupby(["neighbor_code", "ba_code", "month", "hour"])["exports"]
    .mean()
    .reset_index()
)

# apply shares of each external-internal BA pair to matching ReEDS regions
shares = pd.read_csv(Path(settings["input_folder"]) / "import_reeds_region_shares.csv")
avg = avg.merge(shares, on=["neighbor_code", "ba_code"], how="left")
assert (
    shares["share"].notna().all()
), "International interchange reported for unknown BA pairs."
avg["exports"] *= avg["share"]
avg = avg.groupby(["reeds_region", "month", "hour"])["exports"].sum().reset_index()

# for testing:
# dr_file_path = Path(settings["input_folder"]) / settings["demand_response_fn"]
# print(f"Reading previously stored flexible loads from {dr_file_path}")
# growth_wide = pd.read_csv(dr_file_path, header=[0, 1, 2, 3])

# make a time index the same length as other historical data (e.g., 7 sample
# years)
n_years = len(growth_wide) / 8760
assert n_years == int(n_years), "Loads are not an integer number of 8760-hour blocks"

time_index = pd.DataFrame(
    {
        # create a dummy datetime for any non-leap-year
        "datetime": pd.date_range(
            start="2025-01-01 00:00:00", periods=365 * 24, freq="H"
        )
    }
)
time_index["month"] = time_index["datetime"].dt.month
time_index["hour"] = time_index["datetime"].dt.hour
del time_index["datetime"]  # no longer needed, prevent confusion
time_index = pd.concat([time_index] * int(n_years), axis=0, ignore_index=True)
# assign time_index column matching growth table for reference later
time_index["time_index"] = growth_wide.index

# assign average loads along the whole time index
trade_long = time_index.merge(avg, on=["month", "hour"])[
    ["reeds_region", "time_index", "exports"]
]
assert (
    trade_long["exports"].notna().all
), "Unexpected nans found for exports, may be able to fill with 0"

# repeat for all possible model years and DR scenarios and
# convert to wide format for powergenome
trade_wide = (
    pd.concat(
        (
            trade_long.assign(model_year=y, scenario=scen)
            for y in range(start_year, end_year + 1)
            for scen in [normal_growth_scenario, lower_growth_scenario]
        ),
        ignore_index=True,
    )
    .assign(resource_name=exports_resource_name)
    .pivot(
        columns=["resource_name", "model_year", "scenario", "reeds_region"],
        index="time_index",
        values="exports",
    )
    .sort_index(axis=0)
    .astype(int)
)
# split into positive and negative versions, to save as extra loads and dummy generator
# profiles, respectively (similar to how ReEDS treats exports and imports)

exports_wide = trade_wide.clip(0, None)
imports_wide = (-trade_wide).clip(0, None)
# keep only columns with nonzero values
exports_wide = exports_wide.loc[:, (exports_wide != 0).any(axis=0)]
imports_wide = imports_wide.loc[:, (imports_wide != 0).any(axis=0)]

# Add exports to load
flex = pd.concat([growth_wide, growth_lower_wide, exports_wide], axis=1)
flex = flex.round(3)  # don't need more than kW resolution
print(
    f"Saving hourly load growth and export profiles for {start_year}-{end_year} in {dr_file_path}."
)
flex.to_csv(dr_file_path, index=False)
print(f"Finished writing {dr_file_path}.")

# Create virtual generator profiles for imports
print("Creating virtual generator profiles for US imports.")

# Find imports for first scenario/year, get peak production and normalize
first_year_index = imports_wide.columns[0][:3]
imports_wide = imports_wide.loc[:, first_year_index]
imports_capacity = imports_wide.max(axis=0)
imports_wide /= imports_capacity

# convert column names from region to dummy csa_id (int)
region_cpa = {k: str(i) for i, k in enumerate(imports_capacity.index)}
imports_wide = imports_wide.rename(columns=region_cpa)

# write to input files
imports_data_path = Path(settings["RESOURCE_GROUPS"]) / imports_json

# create imports json file if needed
if not imports_data_path.exists():
    imports_data_path.parent.mkdir(parents=True, exist_ok=True)
    imports_data = {
        "technology": "imports",
        "metadata": "imports_metadata.csv",
        "profiles": "imports_profiles.csv",
    }
    with open(imports_data_path, "w") as f:
        json.dump(imports_data, f, indent=4)

# get names of input files
with open(imports_data_path, "r") as f:
    imports_data = json.load(f)

profile_path = imports_data_path.parent / imports_data["profiles"]
imports_wide.to_csv(profile_path, index=False)
print(f"Saved {profile_path}")

metadata_path = imports_data_path.parent / imports_data["metadata"]
pd.DataFrame(
    {
        # all these columns seem to be needed for new VRE
        "region": imports_capacity.index,
        "id": imports_capacity.index,
        "cpa_id": imports_capacity.index.map(region_cpa),
        "mw": imports_capacity,
    }
).to_csv(metadata_path, index=False)
print(f"Saved {metadata_path}")

# %%
print(f"national growth statistics (change from {lower_growth_base_year} to 2030):")
b = base_wide.xs("base", level=1, axis=1).groupby(level=0, axis=1).sum()
for scenario in ["base", "lower_growth"]:
    g = flex.xs(scenario, level=2, axis=1).groupby(level=1, axis=1).sum()
    total = (
        (b + g)[[lower_growth_base_year, 2030]]
        .agg(["sum", "max"], axis=0)
        .rename({"sum": "sales", "max": "peak"})
        .div(1000)
    )
    # convert from total GWh in 7 years to TWh/year
    total.loc["sales", :] *= 0.001 * 8760 / len(b)
    print(f"\n'{scenario}' scenario:")
    print("absolute change (TWh/y, GW):")
    print((total[2030] - total[lower_growth_base_year]).to_string())
    print("fractional change:")
    print((total[2030] / total[lower_growth_base_year]).to_string())
