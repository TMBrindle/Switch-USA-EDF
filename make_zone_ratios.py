"""
Compute historical in-zone generation vs load statistics for each ReEDS BA zone
(or an aggregate thereof), to populate the reference columns in
gen_zone_load_ratio.csv.

Data sources:
  - Load:       pg_data/pg_misc_tables_efs_2025.3.sqlite -> load_curves_nrel_reeds
                ('year' = projected model year; 'weather_year' = historical weather
                 pattern 2007-2013. Earliest available model year is 2020.
                 Note: the 7 weather years are historical PATTERNS (not actual
                 loads for 2007-2013); they are applied to projected demand levels.
                 We use model years 2020-2023, averaged over all 7 weather years,
                 as the best available proxy for recent historical loads.)
  - Generation: pg_data/pudl.2025_08.sqlite -> generation_fuel_eia923
                (monthly net generation by EIA plant × fuel, 2008-2025;
                 ~15k plants vs ~1.9k in the unit-level generation_eia923 table)
  - Zone map:   pg/extra_inputs/reeds_plant_map.csv
                (EIA plant_id_eia -> ReEDS BA, e.g. p86)
  - Hierarchy:  hierarchy.csv  (optional; needed for --agg-by)

Output:
  gen_zone_load_ratio.csv  (written to current directory, or --output path)
  Columns:
    LOAD_ZONE                    - ReEDS p-zone (or aggregate zone name if --agg-by)
    PERIOD                       - placeholder (blank; fill with model periods before use)
    historical_gen_twh           - mean annual in-zone generation 2020-2023 (TWh)
                                   NOTE: raw EIA-923 value, NOT coverage-adjusted
    historical_load_twh          - mean annual in-zone load 2020-2023 (TWh)
    historical_annual_ratio      - mean annual gen/load ratio, coverage-adjusted
    historical_min_annual_ratio  - minimum annual gen/load ratio, coverage-adjusted
    historical_max_annual_ratio  - maximum annual gen/load ratio, coverage-adjusted
    historical_peak_load_mw      - mean annual peak load (MW) averaged over 2020-2023
    min_annual_ratio             - constraint: min gen/load (blank or auto-populated)
    max_annual_ratio             - constraint: max gen/load (blank or auto-populated)
    min_peak_share               - constraint: min hourly gen share (blank by default)
    max_peak_share               - constraint: max hourly gen share (blank by default)

  gen_zone_groups.csv  (only when --agg-by is specified)
    Maps each aggregate zone name to its constituent ReEDS BA zones.
    Used by the gen_zone_ratio Switch module for group-level constraints.

Coverage adjustment (--coverage-adjustment, default 1.30):
  EIA-923 systematically undercounts generation relative to actual US totals.
  Two compounding reasons:
    1. EIA-923 coverage: the generation_fuel_eia923 table contains ~81% of actual
       US generation (EIA published ~4,136 TWh/yr vs 3,349 TWh/yr in PUDL 2025-08).
       Excluded generation includes small distributed generators, some CHP facilities,
       and generators filing only the annual survey form.
    2. Plant map gaps: reeds_plant_map.csv captures ~95% of what is in EIA-923,
       dropping a further ~168 TWh/yr of unmapped plants.
    3. T&D losses: the load data represents end-use consumption at the meter, so
       generation must exceed load by ~6% (US average) to cover transmission and
       distribution losses. The true aggregate gen/load ratio is therefore ~1.06,
       not 1.0.

  Combined effect: raw (unadjusted) aggregate gen/load ratio ≈ 0.82, when the
  true value is ~1.06. The correction factor is 1.06 / 0.82 ≈ 1.30.

  This script applies separate correction factors to the ratio columns:
    historical_annual_ratio      ×1.30 (--coverage-adjustment, default 1.30)
    historical_max_annual_ratio  ×1.30 (--coverage-adjustment, default 1.30)
    historical_min_annual_ratio  ×1.00 (--min-coverage-adjustment, default 1.0)

  The minimum ratio is intentionally left unadjusted by default. Raising the
  floor by a national-average factor adds false precision at the zone level and
  makes constraints unnecessarily restrictive. The raw minimum already provides a
  conservative, data-backed floor: it represents the lowest generation documented
  in EIA-923, which is a defensible lower bound for a constraint. Mean and max are
  adjusted so SPEC formulas like "mean*0.9" or "max" reference realistic values.

  Override with --coverage-adjustment / --min-coverage-adjustment as needed.
  To apply the same factor to all columns: set --min-coverage-adjustment equal to
  --coverage-adjustment. To disable all adjustment: set --coverage-adjustment 1.0.

Constraint auto-population (--min-annual-ratio / --max-annual-ratio / etc.):
  Pass a SPEC string to pre-fill constraint columns for all zones. SPEC formats:
    0.8             fixed value for all zones
    mean            use historical_annual_ratio directly
    min             use historical_min_annual_ratio directly
    max             use historical_max_annual_ratio directly
    mean*0.9        multiply historical mean by 0.9 (= 90% of mean)
    min*1.0         100% of historical minimum
    max*0.8         80% of historical maximum
    mean-0.05       historical mean minus 0.05
    min+0.1         historical minimum plus 0.1

Note on peak generation (min_peak_share / max_peak_share):
  Hourly generation data by ReEDS zone is not available in the local PUDL SQLite.
  Use the annual ratio (column historical_min_annual_ratio) as an initial proxy
  for min_peak_share, adjusted downward by ~10-20% to allow for planned outages.
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

# ── Configuration ──────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
PG_DATA = BASE / "pg_data"
EXTRA_INPUTS = BASE / "pg" / "extra_inputs"

PG_MISC_DB = PG_DATA / "pg_misc_tables_efs_2025.3.sqlite"
PUDL_DB = PG_DATA / "pudl.2025_08.sqlite"
PLANT_REGION_MAP = EXTRA_INPUTS / "reeds_plant_map.csv"
HIERARCHY_FILE = BASE / "hierarchy.csv"

HIST_START = 2020
HIST_END = 2023


# ── Constraint spec parser ─────────────────────────────────────────────────────
def parse_spec(spec_str, df, base_cols):
    """
    Parse a constraint specification string and return a Series aligned to df.

    spec_str:  e.g. '0.8', 'mean', 'min*0.9', 'max-0.05', 'mean+0.1'
    df:        summary DataFrame (one row per zone)
    base_cols: dict mapping base name → column in df:
               {'mean': 'historical_annual_ratio',
                'min':  'historical_min_annual_ratio',
                'max':  'historical_max_annual_ratio'}
    """
    spec = spec_str.strip()

    # Pure numeric?
    try:
        val = float(spec)
        return pd.Series(val, index=df.index)
    except ValueError:
        pass

    # BASE[op][value]  where op is *, +, or -
    m = re.match(r"^(mean|min|max)([*+\-])?([\d.]+)?$", spec, re.IGNORECASE)
    if not m:
        raise ValueError(
            f"Cannot parse spec {spec_str!r}. "
            "Expected: a number, 'mean', 'min', 'max', 'mean*0.9', 'min-0.05', etc."
        )
    base_name, op, val_str = m.groups()
    base_series = df[base_cols[base_name.lower()]].copy()

    if not op:
        return base_series

    val = float(val_str)
    if op == "*":
        return base_series * val
    elif op == "+":
        return base_series + val
    elif op == "-":
        return base_series - val


# ── CLI ────────────────────────────────────────────────────────────────────────
def build_parser():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--agg-by",
        metavar="COLUMN",
        default=None,
        help=(
            "Aggregate BA zones to a higher level before computing ratios. "
            "COLUMN must be a column in hierarchy.csv, e.g. 'st', 'transreg', "
            "'nercr', 'transgrp'. "
            "When specified, also writes gen_zone_groups.csv mapping aggregate "
            "zone names to their constituent BA zones."
        ),
    )
    p.add_argument(
        "--include-ba",
        action="store_true",
        default=False,
        help=(
            "When using --agg-by, also include individual BA-level rows below "
            "the aggregate rows in the output. Useful when you want both group "
            "and per-zone constraints."
        ),
    )
    p.add_argument(
        "--min-annual-ratio",
        metavar="SPEC",
        default=None,
        help=(
            "Auto-populate the min_annual_ratio constraint column. "
            "SPEC formats: '0.8', 'mean', 'min', 'max', 'mean*0.9', 'min-0.05', etc."
        ),
    )
    p.add_argument(
        "--max-annual-ratio",
        metavar="SPEC",
        default=None,
        help="Auto-populate the max_annual_ratio constraint column. See --min-annual-ratio.",
    )
    p.add_argument(
        "--min-peak-share",
        metavar="SPEC",
        default=None,
        help=(
            "Auto-populate the min_peak_share constraint column (uses same annual "
            "ratio base columns as proxy for hourly share). SPEC as above."
        ),
    )
    p.add_argument(
        "--max-peak-share",
        metavar="SPEC",
        default=None,
        help="Auto-populate the max_peak_share constraint column. See --min-peak-share.",
    )
    p.add_argument(
        "--coverage-adjustment",
        metavar="FACTOR",
        type=float,
        default=1.30,
        help=(
            "Multiplicative correction applied to historical_annual_ratio and "
            "historical_max_annual_ratio to account for EIA-923 undercounting "
            "and T&D losses. "
            "Default 1.30 = correction for ~77%% EIA-923 plant-map coverage × "
            "~6%% T&D losses (true gen/load ≈ 1.06 vs raw measured ≈ 0.82). "
            "Set to 1.0 to disable. "
            "See also --min-coverage-adjustment."
        ),
    )
    p.add_argument(
        "--min-coverage-adjustment",
        metavar="FACTOR",
        type=float,
        default=1.0,
        help=(
            "Multiplicative correction applied specifically to "
            "historical_min_annual_ratio. Default 1.0 (no adjustment). "
            "Keeping the minimum unadjusted produces a conservative floor: "
            "it reflects the lowest generation actually documented in EIA-923, "
            "without raising it by a national-average factor that may not apply "
            "uniformly at the zone level. Set equal to --coverage-adjustment "
            "to apply the same correction to all three ratio columns."
        ),
    )
    p.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help=(
            "Path for the output CSV. Defaults to gen_zone_load_ratio.csv in the "
            "project directory."
        ),
    )
    p.add_argument(
        "--groups-output",
        metavar="FILE",
        default=None,
        help=(
            "Path for the zone groups CSV (only written when --agg-by is used). "
            "Defaults to gen_zone_groups.csv in the same directory as --output."
        ),
    )
    return p


# ── Data loading ───────────────────────────────────────────────────────────────
def load_generation():
    print("Loading EIA-923 generation data (this may take a moment)...")
    pudl_engine = sa.create_engine(f"sqlite:///{PUDL_DB}")
    gen_sql = """
        SELECT
            CAST(strftime('%Y', report_date) AS INTEGER) AS year,
            plant_id_eia,
            SUM(net_generation_mwh) AS gen_mwh
        FROM generation_fuel_eia923
        WHERE report_date >= :start AND report_date <= :end
        GROUP BY strftime('%Y', report_date), plant_id_eia
    """
    gen_by_plant = pd.read_sql(
        gen_sql,
        pudl_engine,
        params={"start": f"{HIST_START}-01-01", "end": f"{HIST_END}-12-31"},
    )
    print(f"  {len(gen_by_plant):,} plant-year records loaded.")

    plant_map = pd.read_csv(PLANT_REGION_MAP)
    gen_by_zone = (
        gen_by_plant.merge(plant_map[["plant_id_eia", "region"]], on="plant_id_eia", how="inner")
        .groupby(["year", "region"])["gen_mwh"]
        .sum()
        .reset_index()
    )
    gen_by_zone["gen_twh"] = gen_by_zone["gen_mwh"] / 1e6
    return gen_by_zone  # columns: year, region (=BA), gen_mwh, gen_twh


def load_loads():
    print("Loading load stats from load_curves_nrel_reeds...")
    pg_misc_engine = sa.create_engine(f"sqlite:///{PG_MISC_DB}")
    load_sql = """
        SELECT
            year,
            region,
            AVG(annual_mwh)  AS avg_annual_mwh,
            AVG(peak_mw)     AS avg_peak_mw
        FROM (
            SELECT year, weather_year, region,
                   SUM(load_mw)  AS annual_mwh,
                   MAX(load_mw)  AS peak_mw
            FROM load_curves_nrel_reeds
            WHERE year >= :start AND year <= :end
            GROUP BY year, weather_year, region
        ) sub
        GROUP BY year, region
    """
    load_by_zone = pd.read_sql(
        load_sql,
        pg_misc_engine,
        params={"start": HIST_START, "end": HIST_END},
    )
    load_by_zone["load_twh"] = load_by_zone["avg_annual_mwh"] / 1e6
    print(f"  {len(load_by_zone):,} zone-year records loaded.")
    return load_by_zone  # columns: year, region (=BA), avg_annual_mwh, avg_peak_mw, load_twh


# ── Core computation ───────────────────────────────────────────────────────────
def compute_ba_stats(gen_by_zone, load_by_zone):
    """Merge generation and load at BA×year level and return combined df."""
    combined = load_by_zone.merge(
        gen_by_zone[["year", "region", "gen_twh"]],
        on=["year", "region"],
        how="left",
    )
    combined["gen_twh"] = combined["gen_twh"].fillna(0.0)
    combined["annual_ratio"] = combined["gen_twh"] / combined["load_twh"].replace(0, float("nan"))
    return combined  # year-level BA data


def summarise(combined, zone_col="region"):
    """Aggregate year-level data to mean/min/max statistics per zone."""
    summary = (
        combined
        .groupby(zone_col)
        .agg(
            historical_gen_twh=("gen_twh", "mean"),
            historical_load_twh=("load_twh", "mean"),
            historical_annual_ratio=("annual_ratio", "mean"),
            historical_min_annual_ratio=("annual_ratio", "min"),
            historical_max_annual_ratio=("annual_ratio", "max"),
            historical_peak_load_mw=("avg_peak_mw", "mean"),
        )
        .reset_index()
        .rename(columns={zone_col: "LOAD_ZONE"})
        .sort_values("LOAD_ZONE")
    )
    return summary


def apply_hierarchy_agg(combined, hier, agg_col):
    """
    Re-aggregate year-level BA data to a higher hierarchy level.

    Returns (agg_combined, ba_to_agg):
      agg_combined  - year-level data aggregated to agg_col zones
      ba_to_agg     - DataFrame with columns [ba, agg_col] for gen_zone_groups.csv
    """
    ba_to_agg = hier[["ba", agg_col]].rename(columns={agg_col: "agg_zone"})

    # Join BA stats to hierarchy
    merged = combined.merge(
        ba_to_agg.rename(columns={"ba": "region"}),
        on="region",
        how="inner",
    )

    # Re-aggregate: sum gen/load, then recompute peak (max of peak across BAs, averaged over weather years)
    agg = (
        merged
        .groupby(["year", "agg_zone"])
        .agg(
            gen_twh=("gen_twh", "sum"),
            load_twh=("load_twh", "sum"),
            avg_annual_mwh=("avg_annual_mwh", "sum"),
            avg_peak_mw=("avg_peak_mw", "max"),
        )
        .reset_index()
        .rename(columns={"agg_zone": "region"})
    )
    agg["annual_ratio"] = agg["gen_twh"] / agg["load_twh"].replace(0, float("nan"))

    return agg, ba_to_agg.rename(columns={"agg_zone": agg_col})


def build_output(summary, args):
    """
    Add PERIOD placeholder and constraint columns (blank or auto-populated)
    to the summary DataFrame. Applies the coverage adjustment factor to ratio
    columns before deriving constraint values.
    """
    base_cols = {
        "mean": "historical_annual_ratio",
        "min":  "historical_min_annual_ratio",
        "max":  "historical_max_annual_ratio",
    }

    df = summary.copy()

    # Apply coverage adjustments to ratio columns.
    # historical_gen_twh is left uncorrected (raw EIA-923 measured value).
    # Mean and max use --coverage-adjustment (default 1.30).
    # Min uses --min-coverage-adjustment (default 1.0 — conservative unadjusted floor).
    factor = args.coverage_adjustment
    min_factor = args.min_coverage_adjustment
    for col, f in [
        ("historical_annual_ratio",     factor),
        ("historical_max_annual_ratio", factor),
        ("historical_min_annual_ratio", min_factor),
    ]:
        if f != 1.0:
            df[col] = (df[col] * f).round(3)
    if factor != 1.0 or min_factor != 1.0:
        print(
            f"Coverage adjustment: mean/max ×{factor:.3f}, "
            f"min ×{min_factor:.3f} (applied to ratio columns)."
        )

    df.insert(1, "PERIOD", "")

    for col, spec in [
        ("min_annual_ratio", args.min_annual_ratio),
        ("max_annual_ratio", args.max_annual_ratio),
        ("min_peak_share",   args.min_peak_share),
        ("max_peak_share",   args.max_peak_share),
    ]:
        if spec is not None:
            df[col] = parse_spec(spec, df, base_cols).round(4)
        else:
            df[col] = ""

    return df


# ── Main ───────────────────────────────────────────────────────────────────────
def main(argv=None):
    args = build_parser().parse_args(argv)

    # Resolve output paths
    output_file = Path(args.output) if args.output else BASE / "gen_zone_load_ratio.csv"
    groups_output_file = (
        Path(args.groups_output)
        if args.groups_output
        else output_file.parent / "gen_zone_groups.csv"
    )

    # ── Load raw data ──────────────────────────────────────────────────────────
    print("Loading plant-zone map...")
    gen_by_zone = load_generation()
    load_by_zone = load_loads()

    # ── BA-level stats ─────────────────────────────────────────────────────────
    print("Computing annual gen/load ratios...")
    combined_ba = compute_ba_stats(gen_by_zone, load_by_zone)

    # ── Aggregation (optional) ─────────────────────────────────────────────────
    frames = []

    if args.agg_by:
        if not HIERARCHY_FILE.exists():
            sys.exit(f"ERROR: --agg-by requires {HIERARCHY_FILE} but it was not found.")

        hier = pd.read_csv(HIERARCHY_FILE)
        if args.agg_by not in hier.columns:
            sys.exit(
                f"ERROR: column {args.agg_by!r} not found in hierarchy.csv. "
                f"Available: {hier.columns.tolist()}"
            )

        print(f"Aggregating to '{args.agg_by}' level...")
        agg_combined, ba_to_agg = apply_hierarchy_agg(combined_ba, hier, args.agg_by)
        agg_summary = summarise(agg_combined)
        agg_summary["agg_level"] = args.agg_by
        frames.append(agg_summary)

        # Write zone groups mapping
        groups_df = ba_to_agg.rename(columns={"ba": "LOAD_ZONE", args.agg_by: "GROUP_NAME"})[
            ["GROUP_NAME", "LOAD_ZONE"]
        ].sort_values(["GROUP_NAME", "LOAD_ZONE"])
        groups_df.to_csv(groups_output_file, index=False)
        print(f"Written zone groups to {groups_output_file}")

    if not args.agg_by or args.include_ba:
        ba_summary = summarise(combined_ba)
        ba_summary["agg_level"] = "ba"
        frames.append(ba_summary)

    summary = pd.concat(frames, ignore_index=True).sort_values(
        ["agg_level", "LOAD_ZONE"]
    ).drop(columns="agg_level")

    # ── Round reference columns ────────────────────────────────────────────────
    for col in [
        "historical_gen_twh", "historical_load_twh",
        "historical_annual_ratio",
        "historical_min_annual_ratio", "historical_max_annual_ratio",
    ]:
        summary[col] = summary[col].round(3)
    summary["historical_peak_load_mw"] = summary["historical_peak_load_mw"].round(0)

    # ── Add constraint columns ─────────────────────────────────────────────────
    output_df = build_output(summary, args)

    col_order = [
        "LOAD_ZONE", "PERIOD",
        "historical_gen_twh", "historical_load_twh",
        "historical_annual_ratio",
        "historical_min_annual_ratio", "historical_max_annual_ratio",
        "historical_peak_load_mw",
        "min_annual_ratio", "max_annual_ratio",
        "min_peak_share", "max_peak_share",
    ]
    output_df[col_order].to_csv(output_file, index=False)
    print(f"\nWritten {len(output_df)} zones to {output_file}")

    # ── Quick sanity check ─────────────────────────────────────────────────────
    gulf_zones = [
        "p48", "p57", "p58", "p59", "p60", "p61", "p62", "p63",
        "p64", "p65", "p66", "p67", "p86", "p87", "p88", "p89", "p90",
    ]
    sample = output_df[output_df["LOAD_ZONE"].isin(gulf_zones)]
    if not sample.empty:
        print("\nSample (Gulf Coast BAs):")
        print(
            sample[
                [
                    "LOAD_ZONE",
                    "historical_gen_twh", "historical_load_twh",
                    "historical_annual_ratio",
                    "historical_min_annual_ratio", "historical_max_annual_ratio",
                    "min_annual_ratio", "max_annual_ratio",
                ]
            ].to_string(index=False)
        )

    # Also print any aggregate rows if present
    if args.agg_by:
        agg_rows = output_df[~output_df["LOAD_ZONE"].str.match(r"^p\d+$")]
        if not agg_rows.empty:
            print(f"\nSample (aggregate '{args.agg_by}' zones):")
            print(
                agg_rows[
                    [
                        "LOAD_ZONE",
                        "historical_gen_twh", "historical_load_twh",
                        "historical_annual_ratio",
                        "historical_min_annual_ratio", "historical_max_annual_ratio",
                    ]
                ].head(10).to_string(index=False)
            )


if __name__ == "__main__":
    main()
