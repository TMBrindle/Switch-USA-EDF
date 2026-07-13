"""
Compare new DC vs. new electrification/baseline load growth by zone and year.

Reads the target-stat files written by make_study_loads.py
(switch/Scripts/Growth_Profiles/{growth_case}_dc_targets.csv and
..._nondc_targets.csv) and reports how much load each pathway adds on top of
the base_year load, for every zone/year in those files.

Note: DC growth is applied as a flat, constant MW addition across all hours
(see build_growth_timeseries(..., flat_profile=True) in make_study_loads.py),
so the realized peak addition for DC equals its avg addition -- the
peak_targ/peak_growth columns in the DC file are not actually applied to the
load shape and are ignored here. Non-DC growth is scaled to match both the
avg and peak targets, so its peak_targ - peak_base is the real peak addition.

Usage:
    python compare_growth_breakdown.py [growth_case] [--zone p99] [--year 2035]

With no --zone/--year, writes the full breakdown for all zones/years to
growth_breakdown_{growth_case}.csv and prints nothing else.
With --zone/--year (either or both), also prints a filtered table to stdout.
"""

import argparse
from pathlib import Path

import pandas as pd

GROWTH_PROFILES_DIR = Path("switch/Scripts/Growth_Profiles")


def load_breakdown(growth_case: str) -> pd.DataFrame:
    dc = pd.read_csv(GROWTH_PROFILES_DIR / f"{growth_case}_dc_targets.csv")
    nondc = pd.read_csv(GROWTH_PROFILES_DIR / f"{growth_case}_nondc_targets.csv")

    dc = dc[["region", "year", "avg_base", "peak_base", "avg_targ"]].copy()
    dc["dc_avg_added_mw"] = dc["avg_targ"] - dc["avg_base"]
    dc["dc_peak_added_mw"] = dc["dc_avg_added_mw"]  # flat profile: peak add == avg add

    nondc = nondc[["region", "year", "avg_base", "peak_base", "avg_targ", "peak_targ"]].copy()
    nondc["nondc_avg_added_mw"] = nondc["avg_targ"] - nondc["avg_base"]
    nondc["nondc_peak_added_mw"] = nondc["peak_targ"] - nondc["peak_base"]

    out = dc[["region", "year", "dc_avg_added_mw", "dc_peak_added_mw"]].merge(
        nondc[["region", "year", "nondc_avg_added_mw", "nondc_peak_added_mw"]],
        on=["region", "year"],
    )
    out["dc_minus_nondc_avg_mw"] = out["dc_avg_added_mw"] - out["nondc_avg_added_mw"]
    out["dc_avg_share_of_total"] = out["dc_avg_added_mw"] / (
        out["dc_avg_added_mw"] + out["nondc_avg_added_mw"]
    )
    return out.sort_values(["region", "year"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("growth_case", nargs="?", default="epri_med")
    parser.add_argument("--zone", help="filter to a single load_zone, e.g. p99")
    parser.add_argument("--year", type=int, help="filter to a single model year, e.g. 2035")
    args = parser.parse_args()

    breakdown = load_breakdown(args.growth_case)

    out_path = Path(f"growth_breakdown_{args.growth_case}.csv")
    breakdown.to_csv(out_path, index=False)
    print(f"Wrote full DC vs. non-DC breakdown for all zones/years to {out_path}")

    if args.zone or args.year:
        filtered = breakdown
        if args.zone:
            filtered = filtered[filtered["region"] == args.zone]
        if args.year:
            filtered = filtered[filtered["year"] == args.year]
        print(filtered.to_string(index=False))


if __name__ == "__main__":
    main()
