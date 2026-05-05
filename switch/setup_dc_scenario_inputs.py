#!/usr/bin/env python3
"""
setup_dc_scenario_inputs.py

Create the per-scenario input files for the DC on-site power scenario analysis.
Run once after prepare_dc_onsite_techs.py has been run.

Files created in the inputs directory:
  tracked_demands_dc1gw.csv                       (1 GW DC definition)
  tracked_demand_grid_soft_cap_SC.csv             (250 MW, $750/MWh penalty)
  tracked_demand_grid_soft_cap_MC.csv             (900 MW, $750/MWh penalty)
  tracked_demand_candidate_zones_fixed_p{zone}.csv (6 single-zone files)
  tracked_demand_candidate_zones_flex_p{zone}.csv  (6 all-in-state-zone files)
  tracked_demand_dispatch_bounds_curt.csv          (summer peak curtailment rule)
  tracked_demand_time_blocks_dc.csv               (solar/non-solar block definitions)

Usage:
    python setup_dc_scenario_inputs.py [--inputs-dir PATH]
"""

import argparse
import os
import csv
import pandas as pd

TD_NAME = "dc_main"

# 6 target zones: anchor zone → state zones from hierarchy.csv
ZONE_STATE_ZONES = {
    "p8":   ["p8", "p9", "p10", "p11"],           # CA
    "p27":  ["p27", "p28", "p29", "p30"],          # AZ
    "p33":  ["p33", "p34"],                        # CO
    "p48":  ["p48", "p57", "p59", "p60",
             "p61", "p62", "p63", "p64",
             "p65", "p66", "p67"],                 # TX
    "p94":  ["p94"],                               # GA
    "p100": ["p99", "p100", "p118", "p124"],       # VA
}

# Summer peak curtailment settings
# Applied to non-winter (May/June) sample days, hours 10:00-20:00
CURT_MAX_MW = 800       # reduced max during peak (normal max = 1000 MW)
CURT_MIN_MW = 0         # DC can curtail to zero during grid emergency
PEAK_HOURS = {10, 12, 14, 16, 18, 20}  # steps (= actual hour 00-22)

# Timeseries to apply curtailment to (all non-winter sample days)
# Feb 12 (p705) is excluded; May/June days get curtailment
CURTAIL_SERIES_PREFIXES = [
    "20355081",   # May 8 (p1685)
    "20356040",   # June 4 (p1921)
    "20355072",   # May 7 (p1662) — also exclude _prm suffix
]

def write_csv(path, rows, header=None):
    """Write a CSV file from rows (list of lists or list of dicts)."""
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        if rows and isinstance(rows[0], dict):
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        else:
            writer = csv.writer(f)
            if header:
                writer.writerow(header)
            writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inputs-dir", default="in/2035/s4x1_caelp_parclust_zoned")
    args = parser.parse_args()
    d = args.inputs_dir

    def path(fname):
        return os.path.join(d, fname)

    # ----------------------------------------------------------------
    # 1. tracked_demands_dc1gw.csv
    # ----------------------------------------------------------------
    write_csv(path("tracked_demands_dc1gw.csv"), [
        {"TRACKED_DEMAND": TD_NAME,
         "td_type": "datacenter",
         "td_energy_requirement_mwh_per_year": 8_760_000,
         "td_default_min_power_mw": 1000,
         "td_default_max_power_mw": 1000}
    ])
    print("Wrote tracked_demands_dc1gw.csv")

    # ----------------------------------------------------------------
    # 2. Grid soft cap files
    # ----------------------------------------------------------------
    write_csv(path("tracked_demand_grid_soft_cap_SC.csv"), [
        {"TRACKED_DEMAND": TD_NAME, "td_grid_soft_cap_mw": 250, "td_grid_soft_cap_penalty": 750}
    ])
    print("Wrote tracked_demand_grid_soft_cap_SC.csv  (250 MW, $750/MWh)")

    write_csv(path("tracked_demand_grid_soft_cap_MC.csv"), [
        {"TRACKED_DEMAND": TD_NAME, "td_grid_soft_cap_mw": 900, "td_grid_soft_cap_penalty": 750}
    ])
    print("Wrote tracked_demand_grid_soft_cap_MC.csv  (900 MW, $750/MWh)")

    # ----------------------------------------------------------------
    # 3. Candidate zone files — fixed siting (one zone)
    # ----------------------------------------------------------------
    for anchor, state_zones in ZONE_STATE_ZONES.items():
        rows = [{"TRACKED_DEMAND": TD_NAME, "LOAD_ZONE": anchor}]
        write_csv(path(f"tracked_demand_candidate_zones_fixed_{anchor}.csv"), rows)
    print("Wrote 6 × tracked_demand_candidate_zones_fixed_{zone}.csv")

    # ----------------------------------------------------------------
    # 4. Candidate zone files — flex siting (all in-state zones)
    # ----------------------------------------------------------------
    for anchor, state_zones in ZONE_STATE_ZONES.items():
        rows = [{"TRACKED_DEMAND": TD_NAME, "LOAD_ZONE": z} for z in state_zones]
        write_csv(path(f"tracked_demand_candidate_zones_flex_{anchor}.csv"), rows)
    print("Wrote 6 × tracked_demand_candidate_zones_flex_{zone}.csv")

    # ----------------------------------------------------------------
    # 5. Summer peak curtailment via flex events (caps grid draw, not total dispatch)
    # Using flex_events (TDGridDraw constraint) instead of dispatch_bounds (TDDispatch
    # constraint) keeps CURT scenarios feasible: onsite generation can compensate for
    # reduced grid draw during peak hours.
    # ----------------------------------------------------------------
    tp_df = pd.read_csv(path("timepoints.csv"), na_values=["."])
    # Filter: non-PRM timepoints that match summer prefixes
    curt_rows = []
    for _, row in tp_df.iterrows():
        tp_id = str(row["timepoint_id"])
        if tp_id.endswith("_prm"):
            continue
        prefix_match = any(tp_id.startswith(pfx) for pfx in CURTAIL_SERIES_PREFIXES)
        if not prefix_match:
            continue
        hour = int(tp_id[-2:])
        if hour in PEAK_HOURS:
            curt_rows.append({
                "TRACKED_DEMAND": TD_NAME,
                "TIMEPOINT": tp_id,
                "td_flex_max_grid_draw_mw": CURT_MAX_MW,
                "td_flex_noncompliance_penalty": 750,
            })
    write_csv(path("tracked_demand_flex_events_curt.csv"), curt_rows)
    print(f"Wrote tracked_demand_flex_events_curt.csv  "
          f"({len(curt_rows)} summer-peak TPs, grid cap={CURT_MAX_MW} MW)")

    # ----------------------------------------------------------------
    # 6. Time blocks for DC (no CFE targets, but needed for emissions tracking)
    #    solar_hours = 08:00-20:00, non_solar_hours = 00:00-06:00 + 22:00
    # ----------------------------------------------------------------
    solar_steps = {8, 10, 12, 14, 16, 18, 20}
    tb_rows = []
    for _, row in tp_df.iterrows():
        tp_id = str(row["timepoint_id"])
        if tp_id.endswith("_prm"):
            continue
        hour = int(tp_id[-2:])
        block = "solar_hours" if hour in solar_steps else "non_solar_hours"
        tb_rows.append({"TRACKED_DEMAND": TD_NAME, "time_block": block, "TIMEPOINT": tp_id})
    write_csv(path("tracked_demand_time_blocks_dc.csv"), tb_rows)
    print(f"Wrote tracked_demand_time_blocks_dc.csv  ({len(tb_rows)} TPs)")

    # ----------------------------------------------------------------
    # 7. CFE target files (75% and 100%) for CFE scenario variants
    # ----------------------------------------------------------------
    for target, suffix in [(0.75, "75"), (1.0, "100")]:
        rows = [
            {"TRACKED_DEMAND": TD_NAME, "PERIOD": 2035,
             "time_block": "solar_hours",
             "td_cfe_target": target, "td_cfe_shortfall_penalty": 500},
            {"TRACKED_DEMAND": TD_NAME, "PERIOD": 2035,
             "time_block": "non_solar_hours",
             "td_cfe_target": target, "td_cfe_shortfall_penalty": 500},
        ]
        fname = f"tracked_demand_cfe_targets_dc_{suffix}.csv"
        write_csv(path(fname), rows)
        print(f"Wrote {fname}  (CFE {int(target*100)}%, $500/MWh shortfall)")

    # ----------------------------------------------------------------
    # 8. Empty files for unused optional inputs
    # ----------------------------------------------------------------
    empty_files = {
        "tracked_demand_cfe_targets_dc.csv":
            ["TRACKED_DEMAND", "time_block", "td_cfe_target", "td_shortfall_penalty"],
        "tracked_demand_rec_supply_dc.csv":
            ["TRACKED_DEMAND", "PERIOD", "TIME_BLOCK", "td_rec_supply_mwh", "td_rec_cost_per_mwh"],
        "tracked_demand_h2_storage_dc.csv":
            ["TRACKED_DEMAND", "td_h2_storage_cap_kg", "td_h2_storage_cost_per_kg_yr",
             "td_h2_throughput_cap_kg_per_hr", "td_h2_compressor_cost_per_kg_hr_yr"],
        "tracked_demand_flex_events_dc.csv":
            ["TRACKED_DEMAND", "time_block", "td_flex_max_grid_draw_mw", "td_flex_noncompliance_penalty"],
        "tracked_demand_onsite_predetermined_dc.csv":
            ["TRACKED_DEMAND", "td_onsite_tech", "LOAD_ZONE", "td_onsite_predetermined_mw"],
        "tracked_demand_grid_caps_dc.csv":
            ["TRACKED_DEMAND", "td_grid_interconnect_mw"],
        "tracked_demand_grid_clean_caps_dc.csv":
            ["TRACKED_DEMAND", "td_grid_clean_cap_mwh", "PERIOD"],
        "tracked_demand_dispatch_bounds_empty.csv":
            ["TRACKED_DEMAND", "TIMEPOINT", "td_min_power_mw", "td_max_power_mw"],
        "tracked_demand_grid_soft_cap_empty.csv":
            ["TRACKED_DEMAND", "td_grid_soft_cap_mw", "td_grid_soft_cap_penalty"],
    }
    for fname, cols in empty_files.items():
        fpath = path(fname)
        if not os.path.exists(fpath):
            write_csv(fpath, [], header=cols)
            print(f"Wrote (empty) {fname}")
        else:
            print(f"Skipped (exists) {fname}")

    print("\nDone.")


if __name__ == "__main__":
    main()
