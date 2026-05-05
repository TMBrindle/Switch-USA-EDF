#!/usr/bin/env python3
"""
prepare_dc_onsite_techs.py

Generate per-zone tracked_demand_onsite_techs, tracked_demand_onsite_build_caps,
and tracked_demand_storage CSV files from Switch generator input data.

Run this whenever gen_build_costs.csv or gen_info.csv are regenerated, or when
new technologies are added to the model. The script is data-driven: it processes
all new-buildable generators found in gen_build_costs for the specified study
period. Adding CCS or geothermal to the model will auto-include them once their
entries appear in TECH_RULES below.

Usage:
    python prepare_dc_onsite_techs.py [--inputs-dir PATH] [--zones p8 p27 ...]
                                      [--period INT] [--output-dir PATH]
                                      [--td-name STR]

Example:
    python prepare_dc_onsite_techs.py --inputs-dir in/2035/s4x1_caelp_parclust_zoned
"""

import argparse
import os
import sys
import pandas as pd

# ============================================================
# TECH MAPPING  (extend this when adding new techs to the model)
# ============================================================
# List of (energy_source, keyword_in_gen_tech_lower, result) tuples.
# Rules are tested in order; first match wins.
# energy_source must match gen_energy_source exactly (case-insensitive).
# keyword='' matches any gen_tech with that energy source.
# result: (td_onsite_tech_name, td_onsite_tech_cf_source, td_onsite_tech_is_clean)
#         None  → battery storage (goes to tracked_demand_storage file)
#         'skip' → exclude entirely (hydro, biomass, imports, etc.)
TECH_RULES = [
    # Thermal — check for CCS first (keyword is more specific)
    ('naturalgas', 'ccs',               ('gas_ccs',    '.', 1)),
    ('naturalgas', 'combined cycle',    ('gas_cc',     '.', 0)),
    ('naturalgas', 'combustion turbine',('gas_ct',     '.', 0)),
    ('naturalgas', 'peaker',            ('gas_ct',     '.', 0)),
    ('naturalgas', '',                  ('gas_ct',     '.', 0)),   # fallback for any other gas
    # Variable renewables
    ('sun',         '', ('solar',       'sun',  1)),
    ('wind',        '', ('wind',        'wind', 1)),
    # Firm clean
    ('uranium',     '', ('nuclear',     '.', 1)),
    ('geothermal',  '', ('geothermal',  '.', 1)),
    # Battery storage → separate file
    ('storage',     '', None),
    # Skip everything else
    ('biomass',          '', 'skip'),
    ('water',            '', 'skip'),
    ('coal',             '', 'skip'),
    ('distillate',       '', 'skip'),
    ('demand_response',  '', 'skip'),
    ('',                 '', 'skip'),   # final catch-all
]

# CO2 emission factor (tonnes CO2 per MMBtu, combustion only, EIA)
CO2_TONNE_PER_MMBTU = {
    'naturalgas': 0.05307,
    'coal':       0.09564,
    'distillate': 0.07425,
    'uranium':    0.0,
    'biomass':    0.0,
    'storage':    0.0,
    'sun':        0.0,
    'wind':       0.0,
    'geothermal': 0.0,
}

DEFAULT_BATTERY_DURATION_HOURS = 4
DEFAULT_BATTERY_MIN_HOURS = 0


# ============================================================
# Helpers
# ============================================================

def crf(interest_rate: float, amortization_years: float) -> float:
    """Capital Recovery Factor: annualises overnight capital cost."""
    i = interest_rate
    n = amortization_years
    return i * (1 + i) ** n / ((1 + i) ** n - 1)


def map_tech(energy_source, gen_tech):
    """
    Return the TECH_RULES result for this (energy_source, gen_tech) pair,
    or 'skip' if nothing matches.
    """
    es = str(energy_source).lower().strip()
    tech_lower = str(gen_tech).lower()
    for rule_source, keyword, result in TECH_RULES:
        if rule_source == es or rule_source == '':
            if keyword == '' or keyword in tech_lower:
                return result
    return 'skip'


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '--inputs-dir', default='in/2035/s4x1_caelp_parclust_zoned',
        help='Switch inputs directory')
    parser.add_argument(
        '--zones', nargs='+',
        default=['p8', 'p27', 'p33', 'p48', 'p94', 'p100'],
        help='Load zone IDs to generate files for')
    parser.add_argument(
        '--period', type=int, default=2035,
        help='Study period / BUILD_YEAR to use for cost lookup')
    parser.add_argument(
        '--output-dir', default=None,
        help='Directory to write output files (default: same as --inputs-dir)')
    parser.add_argument(
        '--td-name', default='dc_main',
        help='TRACKED_DEMAND name written to build_caps and storage files')
    args = parser.parse_args()

    inputs_dir = args.inputs_dir
    out_dir = args.output_dir or inputs_dir

    os.makedirs(out_dir, exist_ok=True)

    # ----- Load inputs -----
    def load(fname):
        path = os.path.join(inputs_dir, fname)
        if not os.path.exists(path):
            sys.exit(f"ERROR: cannot find {path}")
        return pd.read_csv(path, na_values=['.'])

    gi = load('gen_info.csv')
    bc = load('gen_build_costs.csv')
    fin = load('financials.csv')
    fc = load('fuel_cost.csv')

    interest_rate = float(fin['interest_rate'].iloc[0])
    print(f"Interest rate from financials.csv: {interest_rate:.4f}")

    # New-build generators for the target period
    nb = bc[(bc['BUILD_YEAR'] == args.period) & (bc['gen_overnight_cost'] > 0)].copy()
    nb = nb.merge(gi, on='GENERATION_PROJECT', how='left')
    nb = nb[nb['gen_load_zone'].isin(args.zones)]

    if nb.empty:
        sys.exit(f"No new-build entries found for period={args.period} in zones {args.zones}")

    # Fuel cost lookup: (load_zone, fuel) → $/MMBtu for the study period
    fc_lookup = (
        fc[fc['period'] == args.period]
        .set_index(['load_zone', 'fuel'])['fuel_cost']
    )

    print(f"\nGenerating files for zones: {args.zones}")
    print(f"TD name in cap/storage files: {args.td_name}")
    print()

    for zone in args.zones:
        zdf = nb[nb['gen_load_zone'] == zone].copy()
        if zdf.empty:
            print(f"  {zone}: no new-build generators found — skipping")
            continue

        onsite_rows = []
        cap_rows = []
        storage_data = {}   # store latest battery params

        # Track which td_tech names have been written (for dedup)
        seen_techs = {}     # td_tech → first row (for cost) so we can add capacity limits

        for _, row in zdf.iterrows():
            mapping = map_tech(row['gen_energy_source'], row['gen_tech'])

            # ---- Battery storage ----
            if mapping is None:
                amort = float(row['gen_amortization_period']) if pd.notna(row['gen_amortization_period']) else 15.0
                r = crf(interest_rate, amort)
                power_cost = round(r * float(row['gen_overnight_cost']) + float(row['gen_fixed_om']))
                energy_overnight = float(row['gen_storage_energy_overnight_cost']) if pd.notna(row.get('gen_storage_energy_overnight_cost')) else 0
                energy_fixed = float(row['gen_storage_energy_fixed_om']) if pd.notna(row.get('gen_storage_energy_fixed_om')) else 0
                energy_cost = round(r * energy_overnight + energy_fixed)
                eff = float(row['gen_storage_efficiency']) if pd.notna(row['gen_storage_efficiency']) else 0.85
                storage_data = {
                    'TRACKED_DEMAND': args.td_name,
                    'td_storage_power_cost': power_cost,
                    'td_storage_energy_cost': energy_cost,
                    'td_storage_max_hours': DEFAULT_BATTERY_DURATION_HOURS,
                    'td_storage_min_hours': DEFAULT_BATTERY_MIN_HOURS,
                    'td_storage_roundtrip_eff': round(eff, 4),
                }
                continue

            if mapping == 'skip':
                continue

            td_tech, cf_source, is_clean = mapping

            # ---- Capacity limit accumulation (sum across same-tech generators) ----
            cap_limit = row['gen_capacity_limit_mw']
            if pd.notna(cap_limit) and float(cap_limit) > 0:
                existing_cap = 0.0
                for cr in cap_rows:
                    if cr['td_onsite_tech'] == td_tech:
                        existing_cap = cr['td_onsite_build_mw_cap']
                        cap_rows.remove(cr)
                        break
                cap_rows.append({
                    'TRACKED_DEMAND': args.td_name,
                    'td_onsite_tech': td_tech,
                    'td_onsite_build_mw_cap': round(existing_cap + float(cap_limit), 1),
                })

            # ---- Onsite tech cost (use first occurrence = lowest index = cheapest) ----
            if td_tech in seen_techs:
                continue
            seen_techs[td_tech] = True

            amort = float(row['gen_amortization_period']) if pd.notna(row['gen_amortization_period']) else 30.0
            r = crf(interest_rate, amort)
            capital_cost = round(r * float(row['gen_overnight_cost']) + float(row['gen_fixed_om']))
            var_om = float(row['gen_variable_om']) if pd.notna(row['gen_variable_om']) else 0.0

            # Fuel cost and emissions for thermal techs
            heat_rate = row['gen_full_load_heat_rate']
            if pd.notna(heat_rate) and float(heat_rate) > 0:
                energy_src = str(row['gen_energy_source']).lower()
                fuel_price = float(fc_lookup.get((zone, row['gen_energy_source']), 0.0))
                fuel_cost = round(fuel_price * float(heat_rate), 2)
                co2_factor = CO2_TONNE_PER_MMBTU.get(energy_src, 0.0)
                emissions = round(co2_factor * float(heat_rate), 4)
            else:
                fuel_cost = 0.0
                emissions = 0.0

            # max_annual_hours: variable techs are uncapped (CF constraint handles variability);
            # thermal techs deduct forced outage rate
            if row.get('gen_is_variable', 0):
                max_hours = 8760
            else:
                fo = float(row['gen_forced_outage_rate']) if pd.notna(row['gen_forced_outage_rate']) else 0.0
                max_hours = round(8760 * (1.0 - fo))

            onsite_rows.append({
                'td_onsite_tech':                   td_tech,
                'td_onsite_tech_capital_cost':      capital_cost,
                'td_onsite_tech_variable_om':       round(var_om, 2),
                'td_onsite_tech_fuel_cost':         fuel_cost,
                'td_onsite_tech_emissions':         emissions,
                'td_onsite_tech_capture_rate':      0,
                'td_onsite_tech_max_annual_hours':  max_hours,
                'td_onsite_tech_is_clean':          is_clean,
                'td_onsite_tech_min_stable_mw_fraction': 0,
                'td_onsite_tech_subsidy':           0,
                'td_onsite_tech_min_build_mw':      0,
                'td_onsite_tech_build_increment_mw':0,
                'td_onsite_tech_cf_source':         cf_source,
            })

        # ---- Write tracked_demand_onsite_techs_{zone}.csv ----
        if onsite_rows:
            df_out = pd.DataFrame(onsite_rows)
            path = os.path.join(out_dir, f'tracked_demand_onsite_techs_{zone}.csv')
            df_out.to_csv(path, index=False)
            techs_summary = ', '.join(df_out['td_onsite_tech'].tolist())
            print(f"  {zone}: onsite_techs  ({len(onsite_rows)} techs: {techs_summary})")
            # Print cost summary
            for _, r in df_out.iterrows():
                note = f"  cap={r['td_onsite_tech_capital_cost']:,} $/MW-yr"
                if r['td_onsite_tech_fuel_cost'] > 0:
                    note += f"  fuel=${r['td_onsite_tech_fuel_cost']:.1f}/MWh"
                    note += f"  CO2={r['td_onsite_tech_emissions']:.3f} t/MWh"
                note += f"  max_hrs={r['td_onsite_tech_max_annual_hours']}"
                print(f"         {r['td_onsite_tech']:20s} {note}")
        else:
            print(f"  {zone}: WARNING — no onsite techs produced")

        # ---- Write tracked_demand_onsite_build_caps_{zone}.csv ----
        path = os.path.join(out_dir, f'tracked_demand_onsite_build_caps_{zone}.csv')
        if cap_rows:
            df_cap = pd.DataFrame(cap_rows)
            df_cap.to_csv(path, index=False)
            caps_summary = ', '.join(f"{r['td_onsite_tech']}={r['td_onsite_build_mw_cap']:.0f} MW"
                                     for _, r in df_cap.iterrows())
            print(f"  {zone}: build_caps    ({caps_summary})")
        else:
            pd.DataFrame(columns=['TRACKED_DEMAND', 'td_onsite_tech', 'td_onsite_build_mw_cap']
                         ).to_csv(path, index=False)
            print(f"  {zone}: build_caps    (empty — no capacity limits in model)")

        # ---- Write tracked_demand_storage_{zone}.csv ----
        path = os.path.join(out_dir, f'tracked_demand_storage_{zone}.csv')
        if storage_data:
            pd.DataFrame([storage_data]).to_csv(path, index=False)
            print(f"  {zone}: storage       power={storage_data['td_storage_power_cost']:,}  "
                  f"energy={storage_data['td_storage_energy_cost']:,}  "
                  f"eff={storage_data['td_storage_roundtrip_eff']}")
        else:
            pd.DataFrame(columns=['TRACKED_DEMAND', 'td_storage_power_cost',
                                   'td_storage_energy_cost', 'td_storage_max_hours',
                                   'td_storage_min_hours', 'td_storage_roundtrip_eff']
                         ).to_csv(path, index=False)
            print(f"  {zone}: storage       (empty — no battery in model)")

        print()

    print("Done.")


if __name__ == '__main__':
    main()
