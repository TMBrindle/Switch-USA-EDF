#!/usr/bin/env python3
"""
analyze_dc_scenarios.py

Cross-scenario analysis of DC on-site power results.
Reads Switch output directories and produces summary tables and charts.

Usage:
    python analyze_dc_scenarios.py [--out-prefix OUT_PREFIX] [--scenarios ID ...]

Output files written to the current directory:
    dc_analysis_summary.csv       — one row per scenario with all three CO2 metrics
    dc_analysis_onsite_build.csv  — tech MW by scenario
    dc_analysis_grid_draw_pivot.csv

Emissions columns
-----------------
co2_lb_kg_per_MWh    Location-based Scope 1+2: (onsite combustion + grid draw × avg
                     grid intensity) / total dispatch. GHG Protocol location-based method.

co2_mb_kg_per_MWh    Market-based Scope 1+2: onsite combustion (Scope 1) + uncovered
                     grid draw × avg grid intensity (Scope 2 MB).  "Uncovered" = grid draw
                     not offset by retained clean-energy credits (onsite wind/solar/nuclear
                     RECs, or CFE-matched clean energy for CFE-constrained scenarios).
                     For CFE100 fully met: ≈ Scope 1 only.  For BASE with no onsite
                     renewables: equals location-based.

system_co2_tonne     Total system CO2 from all grid generators in this run (t/yr).
                     Use this to compute consequential emissions vs the NO_DC run.
"""

import argparse
import os
import sys
import pandas as pd
import numpy as np

# ── Scenario registry (matches run_dc_scenarios.ps1) ─────────────────────────
OUT_BASE = "out/2035/s4x1_caelp_parclust_zoned"
INPUTS_DIR = "in/2035/s4x1_caelp_parclust_zoned"

NO_DC_SCENARIO = "NO_DC"   # special: no zone prefix, no tracked demand

ZONES = {
    "p8":   "CA",
    "p27":  "AZ",
    "p33":  "CO",
    "p48":  "TX",
    "p94":  "GA",
    "p100": "VA",
}

VARIANTS = [
    "BASE", "SC", "MC", "SC_CURT", "MC_CURT", "FLEX_SC", "FLEX_MC",
    "SC_CFE75", "SC_CFE100", "MC_CFE75", "MC_CFE100",
    "FLEX_SC_CFE100", "FLEX_MC_CFE100",
]

CAP_MW = {
    "BASE": None, "SC": 250, "MC": 900,
    "SC_CURT": 250, "MC_CURT": 900,
    "FLEX_SC": 250, "FLEX_MC": 900,
    "SC_CFE75": 250, "SC_CFE100": 250,
    "MC_CFE75": 900, "MC_CFE100": 900,
    "FLEX_SC_CFE100": 250, "FLEX_MC_CFE100": 900,
}

CFE_TARGET = {
    "BASE": None, "SC": None, "MC": None,
    "SC_CURT": None, "MC_CURT": None,
    "FLEX_SC": None, "FLEX_MC": None,
    "SC_CFE75": 0.75, "SC_CFE100": 1.0,
    "MC_CFE75": 0.75, "MC_CFE100": 1.0,
    "FLEX_SC_CFE100": 1.0, "FLEX_MC_CFE100": 1.0,
}

SITING = {v: ("flex" if v.startswith("FLEX_") else "fixed") for v in VARIANTS}

# Techs that produce zero combustion emissions (clean for REC / market-based purposes)
CLEAN_TECHS = {"wind", "solar", "nuclear", "hydro", "geothermal", "biomass"}

ALL_SCENARIOS = [f"{z}_{v}" for z in ZONES for v in VARIANTS]


def scenario_outdir(scenario_id):
    return f"{OUT_BASE}_{scenario_id}"


def load_tp_weights():
    """Return Series: timepoint_id → tp_weight_in_year (hours)."""
    tp = pd.read_csv(os.path.join(INPUTS_DIR, "timepoints.csv"), na_values=["."])
    ts = pd.read_csv(os.path.join(INPUTS_DIR, "timeseries.csv"), na_values=["."])
    periods = pd.read_csv(os.path.join(INPUTS_DIR, "periods.csv"), na_values=["."])
    years_in_period = int(periods["period_end"].iloc[0]) - int(periods["period_start"].iloc[0]) + 1
    tp = tp.merge(ts[["timeseries", "ts_duration_of_tp", "ts_scale_to_period"]],
                  on="timeseries", how="left")
    tp["tp_weight_in_year"] = tp["ts_duration_of_tp"] * tp["ts_scale_to_period"] / years_in_period
    return tp.set_index("timepoint_id")["tp_weight_in_year"]


def load_annual(scenario_id):
    path = os.path.join(scenario_outdir(scenario_id), "tracked_demand_annual.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    return df[df["tracked_demand"] == "dc_main"]


def load_onsite_build(scenario_id):
    path = os.path.join(scenario_outdir(scenario_id), "tracked_demand_onsite_build.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    return df[df["tracked_demand"] == "dc_main"]


def load_onsite_annual(scenario_id):
    """Return DataFrame of onsite dispatch by tech (annual MWh and emissions)."""
    path = os.path.join(scenario_outdir(scenario_id), "tracked_demand_onsite_annual.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    return df[df["tracked_demand"] == "dc_main"]


def load_storage_build(scenario_id):
    path = os.path.join(scenario_outdir(scenario_id), "tracked_demand_storage_build.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    return df[df["tracked_demand"] == "dc_main"]


def load_cap_summary(scenario_id):
    path = os.path.join(scenario_outdir(scenario_id), "tracked_demand_grid_cap_summary.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    return df[df["tracked_demand"] == "dc_main"]


def load_total_cost(scenario_id):
    path = os.path.join(scenario_outdir(scenario_id), "total_cost.txt")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return float(f.read().strip())


def load_emissions(scenario_id, tp_weights):
    """Return hourly emissions DataFrame with annual weights applied."""
    path = os.path.join(scenario_outdir(scenario_id), "tracked_demand_hourly_emissions.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df = df[df["tracked_demand"] == "dc_main"].copy()
    df["tp_weight"] = df["timepoint"].map(tp_weights)
    df["annual_grid_co2_tonne"] = df["grid_co2_tonne_per_hr"] * df["tp_weight"]
    df["annual_onsite_co2_tonne"] = df["onsite_co2_tonne_per_hr"] * df["tp_weight"]
    df["annual_co2_tonne"] = df["annual_grid_co2_tonne"] + df["annual_onsite_co2_tonne"]
    return df


def load_cfe_computed(scenario_id):
    """Return CFE computed DataFrame (only present for CFE-constrained scenarios)."""
    path = os.path.join(scenario_outdir(scenario_id), "tracked_demand_cfe_computed.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    return df[df["tracked_demand"] == "dc_main"]


def load_system_co2(scenario_id):
    """Sum DispatchEmissions from all grid generators. Returns tonne/yr."""
    path = os.path.join(scenario_outdir(scenario_id), "dispatch_gen_annual_summary.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    if "DispatchEmissions_tCO2_per_typical_yr" not in df.columns:
        return None
    return float(df["DispatchEmissions_tCO2_per_typical_yr"].sum())


def compute_market_based_co2(
    scope1_co2_tonne,     # onsite combustion CO2 (tonne/yr)
    grid_draw_mwh,        # annual grid draw
    grid_co2_tonne,       # CO2 attributed to grid draw via location-based (tonne/yr)
    cfe_df,               # tracked_demand_cfe_computed DataFrame or None (unused, kept for sig compat)
    onsite_annual_df,     # tracked_demand_onsite_annual DataFrame or None
):
    """
    Market-based CO2 = Scope 1 (combustion) + Scope 2 MB (uncovered grid draw).

    The DC retains RECs from its onsite clean generation (wind, solar, nuclear).
    These RECs are applied against grid draw first.  Any grid draw not covered by
    retained RECs is Scope 2 MB at the average grid intensity (approximation for
    residual mix).

    Note: the CFE model-computed clean fraction is NOT used here.  Under GHG
    Protocol market-based, only formal instruments (RECs retained from onsite
    generation or purchased certificates) offset Scope 2.  The clean portion of
    average grid draw — while it lowers location-based emissions — is not a
    transferable market instrument and therefore does not reduce Scope 2 MB.

    Uses the same average grid intensity as location-based (approximation for
    residual mix — true residual would be slightly higher since clean has been claimed).
    """
    avg_grid_intensity = grid_co2_tonne / grid_draw_mwh if grid_draw_mwh > 0 else 0.0

    # Sum onsite clean dispatch (RECs retained by the DC)
    clean_dispatch_mwh = 0.0
    if onsite_annual_df is not None and not onsite_annual_df.empty:
        for _, row in onsite_annual_df.iterrows():
            tech = str(row["tech"]).lower()
            # Clean = zero combustion emission, non-gas, non-storage
            is_clean = (
                float(row.get("annual_net_emissions_tco2", 1)) == 0
                and "gas" not in tech
                and "storage" not in tech
            )
            if is_clean:
                clean_dispatch_mwh += float(row["annual_dispatch_MWh"])

    # Uncovered grid draw = grid draw not offset by retained clean RECs
    uncovered_grid_draw_mwh = max(0.0, grid_draw_mwh - clean_dispatch_mwh)
    market_based_co2 = scope1_co2_tonne + uncovered_grid_draw_mwh * avg_grid_intensity
    return market_based_co2, uncovered_grid_draw_mwh


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenarios", nargs="+", default=ALL_SCENARIOS,
        help="Scenario IDs to include (default: all DC scenarios)")
    parser.add_argument("--out-prefix", default="dc_analysis",
        help="Prefix for output files")
    parser.add_argument("--min-build-mw", type=float, default=0.01,
        help="Minimum build MW to report (filter LP noise, default 0.01 MW)")
    parser.add_argument("--include-no-dc", action="store_true",
        help="Also extract NO_DC baseline system CO2 and print consequential comparison")
    args = parser.parse_args()

    tp_weights = load_tp_weights()

    rows = []
    onsite_rows = []

    print(f"Analyzing {len(args.scenarios)} scenarios...")

    for sid in args.scenarios:
        parts = sid.split("_", 1)
        zone = parts[0]
        variant = parts[1] if len(parts) > 1 else "UNKNOWN"
        state = ZONES.get(zone, "?")
        cap_mw = CAP_MW.get(variant)
        cfe_target = CFE_TARGET.get(variant)
        siting = SITING.get(variant, "?")

        # Annual summary
        ann = load_annual(sid)
        if ann is None or ann.empty:
            print(f"  {sid}: not found, skipping")
            continue

        grid_draw_mwh = float(ann["annual_grid_draw_MWh"].iloc[0])
        total_dispatch_mwh = float(ann["annual_dispatch_MWh"].iloc[0])
        grid_fraction = grid_draw_mwh / total_dispatch_mwh if total_dispatch_mwh > 0 else 1.0
        onsite_mwh = total_dispatch_mwh - grid_draw_mwh

        # Onsite build
        build_df = load_onsite_build(sid)
        build_total_mw = 0.0
        tech_summary = {}
        if build_df is not None and not build_df.empty:
            build_df = build_df[build_df["built_MW"] >= args.min_build_mw]
            for _, row in build_df.iterrows():
                tech_summary[row["tech"]] = float(row["built_MW"])
                build_total_mw += float(row["built_MW"])
                onsite_rows.append({
                    "scenario_id": sid,
                    "zone": zone,
                    "state": state,
                    "variant": variant,
                    "tech": row["tech"],
                    "built_MW": float(row["built_MW"]),
                })

        # Onsite dispatch by tech (for market-based)
        onsite_annual_df = load_onsite_annual(sid)

        # Storage build
        stor_df = load_storage_build(sid)
        stor_power_mw = 0.0
        stor_energy_mwh = 0.0
        if stor_df is not None and not stor_df.empty:
            stor_df = stor_df[stor_df["built_power_MW"] >= args.min_build_mw]
            if not stor_df.empty:
                stor_power_mw = float(stor_df["built_power_MW"].sum())
                stor_energy_mwh = float(stor_df["built_energy_MWh"].sum())

        # Cap summary (grid draw soft cap)
        cap_df = load_cap_summary(sid)
        annual_excess_mwh = 0.0
        hours_binding = 0.0
        grid_cap_penalty = 0.0
        if cap_df is not None and not cap_df.empty:
            annual_excess_mwh = float(cap_df["annual_excess_mwh"].iloc[0])
            hours_binding = float(cap_df["hours_binding"].iloc[0])
            grid_cap_penalty = float(cap_df["penalty_cost_per_yr"].iloc[0])

        # Total system cost
        total_cost = load_total_cost(sid)

        # Emissions — split into grid and onsite components
        emis_df = load_emissions(sid, tp_weights)
        total_co2_tonne = 0.0
        grid_co2_tonne = 0.0
        scope1_co2_tonne = 0.0
        if emis_df is not None and not emis_df.empty:
            total_co2_tonne = float(emis_df["annual_co2_tonne"].sum())
            grid_co2_tonne = float(emis_df["annual_grid_co2_tonne"].sum())
            scope1_co2_tonne = float(emis_df["annual_onsite_co2_tonne"].sum())

        # CFE computed (market-based shortfall)
        cfe_df = load_cfe_computed(sid)

        # CFE shortfall MWh and penalty (from cfe_computed, not grid cap)
        cfe_shortfall_mwh = 0.0
        cfe_shortfall_penalty = 0.0
        if cfe_df is not None and not cfe_df.empty:
            cfe_shortfall_mwh = float(cfe_df["computed_shortfall_MWh"].sum())
            # Reconstruct penalty: shortfall × $500/MWh (model's shortfall penalty)
            cfe_shortfall_penalty = cfe_shortfall_mwh * 500.0

        # Market-based CO2
        mb_co2_tonne, uncovered_mwh = compute_market_based_co2(
            scope1_co2_tonne, grid_draw_mwh, grid_co2_tonne,
            cfe_df, onsite_annual_df,
        )

        # System-level CO2 (all grid generators, for consequential comparison)
        system_co2_tonne = load_system_co2(sid)

        # Mean grid draw per hour
        grid_draw_mw_mean = grid_draw_mwh / 8760

        rows.append({
            "scenario_id": sid,
            "zone": zone,
            "state": state,
            "variant": variant,
            "siting": siting,
            "soft_cap_mw": cap_mw,
            "cfe_target": cfe_target,
            "grid_draw_MWh_yr": round(grid_draw_mwh),
            "grid_draw_MW_mean": round(grid_draw_mw_mean, 1),
            "onsite_MWh_yr": round(onsite_mwh),
            "grid_fraction_pct": round(grid_fraction * 100, 1),
            "onsite_build_MW": round(build_total_mw, 1),
            "storage_power_MW": round(stor_power_mw, 1),
            "storage_energy_MWh": round(stor_energy_mwh, 1),
            "annual_cap_excess_MWh": round(annual_excess_mwh, 1),
            "hours_above_grid_cap": round(hours_binding, 1),
            "grid_cap_penalty_per_yr": round(grid_cap_penalty),
            "cfe_shortfall_MWh": round(cfe_shortfall_mwh, 1),
            "cfe_shortfall_penalty_per_yr": round(cfe_shortfall_penalty),
            # Location-based CO2 (Scope 1 + Scope 2 LB)
            "annual_co2_tonne": round(total_co2_tonne),
            "co2_lb_kg_per_MWh": round(total_co2_tonne * 1000 / total_dispatch_mwh, 1) if total_dispatch_mwh > 0 else 0,
            # Market-based CO2 (Scope 1 + Scope 2 MB)
            "scope1_co2_tonne": round(scope1_co2_tonne),
            "mb_uncovered_grid_MWh": round(uncovered_mwh),
            "annual_co2_mb_tonne": round(mb_co2_tonne),
            "co2_mb_kg_per_MWh": round(mb_co2_tonne * 1000 / total_dispatch_mwh, 1) if total_dispatch_mwh > 0 else 0,
            # System CO2 (for consequential emissions vs NO_DC run)
            "system_co2_tonne": round(system_co2_tonne) if system_co2_tonne is not None else None,
            "total_system_cost": total_cost,
            # Key technologies built
            "gas_cc_MW": round(tech_summary.get("gas_cc", 0), 1),
            "gas_ct_MW": round(tech_summary.get("gas_ct", 0), 1),
            "nuclear_MW": round(tech_summary.get("nuclear", 0), 1),
            "wind_MW": round(tech_summary.get("wind", 0), 1),
            "solar_MW": round(tech_summary.get("solar", 0), 1),
        })

        print(f"  {sid}: grid={grid_draw_mw_mean:.0f} MW mean  "
              f"CO2_LB={total_co2_tonne:.0f}  CO2_MB={mb_co2_tonne:.0f} t/yr  "
              f"CFE_shortfall={cfe_shortfall_mwh:.0f} MWh")

    if not rows:
        print("No scenarios found. Have scenarios been run?")
        sys.exit(1)

    # ── NO_DC baseline (optional) ─────────────────────────────────────────────
    no_dc_system_co2 = None
    if args.include_no_dc:
        no_dc_system_co2 = load_system_co2(NO_DC_SCENARIO)
        if no_dc_system_co2 is not None:
            print(f"\nNO_DC baseline: system_co2 = {no_dc_system_co2:,.0f} t/yr")
        else:
            print(f"\nNO_DC baseline not found at {scenario_outdir(NO_DC_SCENARIO)}")

    # ── Output: main summary ──────────────────────────────────────────────────
    df = pd.DataFrame(rows)

    # Consequential emissions column (requires NO_DC run)
    if no_dc_system_co2 is not None:
        df["consequential_co2_tonne"] = df["system_co2_tonne"].apply(
            lambda x: round(x - no_dc_system_co2) if pd.notna(x) else None
        )
        df["co2_consequential_kg_per_MWh"] = df["consequential_co2_tonne"].apply(
            lambda x: round(x * 1000 / 8_760_000, 1) if pd.notna(x) else None
        )
    else:
        df["consequential_co2_tonne"] = None
        df["co2_consequential_kg_per_MWh"] = None

    out_path = f"{args.out_prefix}_summary.csv"
    df.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}  ({len(df)} rows)")

    # ── Output: onsite build breakdown ───────────────────────────────────────
    if onsite_rows:
        ob_df = pd.DataFrame(onsite_rows)
        ob_path = f"{args.out_prefix}_onsite_build.csv"
        ob_df.to_csv(ob_path, index=False)
        print(f"Wrote {ob_path}")

    # ── Output: zone × variant grid draw pivot ────────────────────────────────
    pivot = df.pivot_table(
        index="state", columns="variant",
        values="grid_draw_MW_mean", aggfunc="first"
    )
    pivot_path = f"{args.out_prefix}_grid_draw_pivot.csv"
    pivot.to_csv(pivot_path)
    print(f"Wrote {pivot_path}")

    # ── Print emissions comparison table ─────────────────────────────────────
    print("\n" + "="*100)
    print("EMISSIONS COMPARISON: Location-based vs Market-based (kg CO2/MWh of DC output)")
    print("="*100)
    print(f"{'Scenario':<22} {'State':<5} {'CO2_LB':>8} {'CO2_MB':>8} {'Scope1':>8} "
          f"{'MB_Gap%':>8} {'CFE_short':>10}")
    print("-"*100)
    for _, row in df.sort_values(["variant", "state"]).iterrows():
        mb_gap = (row.co2_lb_kg_per_MWh - row.co2_mb_kg_per_MWh) / row.co2_lb_kg_per_MWh * 100 \
            if row.co2_lb_kg_per_MWh > 0 else 0
        print(f"{row.scenario_id:<22} {row.state:<5} {row.co2_lb_kg_per_MWh:>8.1f} "
              f"{row.co2_mb_kg_per_MWh:>8.1f} "
              f"{row.scope1_co2_tonne * 1000 / 8_760_000:>8.1f} "
              f"{mb_gap:>8.1f}% "
              f"{row.cfe_shortfall_MWh:>10.0f}")

    if no_dc_system_co2 is not None and "co2_consequential_kg_per_MWh" in df.columns:
        print("\n" + "="*100)
        print("CONSEQUENTIAL EMISSIONS (vs NO_DC baseline)")
        print(f"NO_DC system CO2: {no_dc_system_co2:,.0f} t/yr")
        print("="*100)
        for _, row in df.sort_values(["variant", "state"]).iterrows():
            if pd.notna(row.co2_consequential_kg_per_MWh):
                print(f"{row.scenario_id:<22} {row.state:<5} "
                      f"consequential={row.co2_consequential_kg_per_MWh:>8.1f} kg/MWh  "
                      f"({row.consequential_co2_tonne:>+,.0f} t/yr vs NO_DC)")

    print("\nDone.")


if __name__ == "__main__":
    main()
