# Tracked Demands Module — Claude Code Implementation Handoff

## Context

You are implementing a new Switch model module called `tracked_demands.py` for
the Switch-USA-PG-ReEDS capacity expansion model. The full specification is in
the accompanying file `tracked_demands_final_spec_v1.md`. Read that file
thoroughly before starting.

## Step 0: Read these files first

Before writing any code, read and understand these existing files in order:

1. **The MCET flexible loads module** — this is the design template:
   https://github.com/opendecarbonization/MCET/blob/main/switch_modules/mcet_modules/flexible_loads.py
   Clone or fetch this file. Study how it:
   - Defines zone sets and allocates load across zones
   - Registers flexible load components as `Zone_Power_Withdrawals`
   - Uses `load_aug` to read input data
   - Structures constraints for daily and annual shiftable loads

2. **`switch_model/policies/carbon_policies.py`** — installed with switch-model.
   Study how it:
   - Defines `define_components()`, `load_inputs()`, and `post_solve()`
   - Uses `Cost_Components_Per_Period` to add terms to the objective
   - Reads optional input files
   - Exports results using `reporting.write_table`

3. **`switch_model/policies/rps_simple.py`** — installed with switch-model.
   Study how it:
   - Tracks eligible energy sources
   - Sums generation by source type across timepoints
   - Enforces a target as a constraint on total demand

4. **`switch_model/generators/core/dispatch.py`** — installed with switch-model.
   Study:
   - How `DispatchEmissions` and `AnnualEmissions` are defined
   - How `Zone_Power_Injections` and `Zone_Power_Withdrawals` work
   - The structure of `GEN_TPS`, `GEN_TP_FUELS`
   - How `gen_load_zone`, `gen_energy_source` connect generators to zones

5. **`switch_model/timescales.py`** — study:
   - `TIMEPOINTS`, `PERIODS`, `TPS_IN_PERIOD`
   - `tp_weight_in_year`, `tp_weight`, `tp_duration_hrs`
   - `tp_period`, `tp_ts`, `tp_date`
   - `DATES`, `TPS_IN_DATE`

6. **`switch_model/balancing/load_zones.py`** — study:
   - `LOAD_ZONES`, `ZONE_TIMEPOINTS`
   - `zone_demand_mw`
   - How `Zone_Power_Withdrawals` list works
   - The `Satisfy_Load` constraint

7. **`switch_model/generators/core/build.py`** — study:
   - `GENERATION_PROJECTS`, `gen_load_zone`, `gen_energy_source`
   - `NON_FUEL_ENERGY_SOURCES`
   - `GENS_IN_ZONE`

8. **`switch_model/financials.py`** — study:
   - How `Cost_Components_Per_Period` works
   - `bring_annual_costs_to_base_year` for discounting

9. **The model's `modules.txt`** — find this in the switch input directory to
   understand what modules are active. The new module should be added after
   the generators and policies modules.

10. **Existing input .csv files** — look at the structure of files like
    `loads.csv`, `gen_info.csv`, `fuels.csv`, `carbon_policies.csv` to
    understand the conventions for column naming, index structure, and how
    optional columns are handled.

## Step 1: Create the module file

Create `tracked_demands.py` in the study modules directory (likely
`switch/study_modules/` or a similar local modules path).

The module must define these functions:
- `define_components(mod)` — all Sets, Params, Vars, Expressions, Constraints
- `load_inputs(mod, switch_data, inputs_dir)` — read all input CSV files
- `post_solve(model, outdir)` — compute endogenous emissions rates, hourly
  accounting, write output files

It must also define:
```python
dependencies = (
    'switch_model.timescales',
    'switch_model.balancing.load_zones',
    'switch_model.financials',
    'switch_model.generators.core.build',
    'switch_model.generators.core.dispatch',
)
```

## Step 2: Implementation order

Implement in this order, testing after each stage:

### Stage 1: Basic tracked demand with grid draw only
- `TRACKED_DEMANDS` set, basic parameters from `tracked_demands.csv`
- `TD_CANDIDATE_ZONES` from `tracked_demand_candidate_zones.csv`
- `TDDispatch` and `TDGridDraw` variables
- Energy balance (grid draw only, no on-site resources yet)
- Dispatch bounds (scalar defaults only first)
- Annual service requirement constraint
- Grid interconnection limit
- Register `TDGridDrawByZone` in `Zone_Power_Withdrawals`
- Test: add a simple tracked demand to a small case, verify it appears in
  the energy balance and the system builds enough to serve it

### Stage 2: CFE targets with penalty
- Time block sets and parameters from `tracked_demand_time_blocks.csv`
  and `tracked_demand_cfe_targets.csv`
- `grid_clean_fraction` parameter from `grid_clean_fraction.csv`
- `TDCFEShortfall` variable
- CFE constraint (Section 5.5 of spec)
- `TDCFEShortfallCost` added to `Cost_Components_Per_Period`
- Test: verify that increasing the CFE target drives the system to build
  more clean generation; verify shortfall penalty appears in costs

### Stage 3: On-site generation
- `TD_ONSITE_TECHS` set and parameters from `tracked_demand_onsite_techs.csv`
- `BuildOnsiteTech` and `DispatchOnsiteTech` variables
- Capacity, runtime, and min stable output constraints
- Update energy balance to include on-site generation
- Add on-site tech costs to objective
- Period-indexed subsidies from `tracked_demand_onsite_tech_subsidies.csv`
- Configurable system carbon cap integration
- Test: add diesel and gas options, verify model chooses cheapest option
  for backup; verify emissions accounting

### Stage 4: Battery storage
- Parameters from `tracked_demand_storage.csv`
- Storage variables (power, energy, charge, discharge, level)
- Storage constraints (Section 5.8 of spec)
- Update energy balance to include storage
- Add storage costs to objective
- Test: verify storage charges during cheap/clean hours and discharges
  during expensive/dirty hours

### Stage 5: Grid flexibility
- Flex events from `tracked_demand_flex_events.csv` (with time block
  expansion)
- `TDFlexExcess` variable
- Flex constraint (Section 5.6 of spec)
- `TDFlexNoncomplianceCost` in objective
- Test: verify the model uses on-site resources to meet flex requirements;
  verify non-compliance when penalty is set low

### Stage 6: Dispatch bounds file and shaped envelopes
- Timepoint-indexed bounds from `tracked_demand_dispatch_bounds.csv`
- Override scalar defaults when file is present
- Test: verify shaped load profiles are respected

### Stage 7: Location flexibility
- `TDZoneAllocation` variable for location-flexible TDs
- Linking grid draw to allocated zone
- Sum-to-one constraint
- Test: with multiple candidate zones, verify model picks cheapest zone

### Stage 8: Hydrogen storage (electrolyzer TDs)
- Parameters from `tracked_demand_h2_storage.csv`
- H2 production, storage, delivery variables and constraints
- Test: verify electrolyzer shifts production to clean hours when H2
  storage is available

### Stage 9: Eligible energy sources and caps
- `TD_ELIGIBLE_ENERGY_SOURCES` from `tracked_demand_eligible_sources.csv`
- `td_eligible_source_max_credit_mw` for nuclear carve-out
- Update CFE constraint to use eligible sources
- Test: verify excluding a source reduces CFE score

### Stage 10: Post-solve accounting
- Compute endogenous zonal emissions rates from solved dispatch
- Hourly emissions attribution for each TD
- CFE scores by time block (using actual clean fractions, not exogenous)
- Write all output files (see spec Section 7)
- Write `grid_clean_fraction_computed.csv`
- 45V tier calculation for electrolyzer TDs

## Step 3: Key implementation patterns

### Reading optional input files

Follow the pattern from `carbon_policies.py`:
```python
switch_data.load_aug(
    filename=os.path.join(inputs_dir, 'tracked_demand_storage.csv'),
    optional=True,
    param=(mod.td_storage_power_cost, ...),
    optional_params=(mod.td_storage_power_cost, ...),
)
```

### Adding costs to objective

```python
mod.Cost_Components_Per_Period.append('TDTotalCosts')
```
Where `TDTotalCosts` is an Expression indexed by `mod.PERIODS` that sums
all TD-related costs for that period.

### Registering load withdrawal

Follow the flexible loads pattern:
```python
mod.Zone_Power_Withdrawals.append('TDGridDrawByZone')
```
Where `TDGridDrawByZone` is an Expression indexed by
`(mod.LOAD_ZONES, mod.TIMEPOINTS)`.

### Post-solve output

Use `reporting.write_table` for simple tables, or `pandas.DataFrame.to_csv`
for more complex outputs:
```python
import switch_model.reporting as reporting
reporting.write_table(
    model, model.PERIODS,
    output_file=os.path.join(outdir, 'tracked_demand_annual.csv'),
    headings=(...),
    values=get_row,
)
```

### Handling conditional components

Many features (storage, H2, on-site gen, flex events) are optional. Use
patterns like:
```python
# Only define storage constraints if storage data was provided
if len(mod.TD_STORAGE_TDS):
    mod.TDStorageBalance = Constraint(...)
```

Or check for empty sets in constraint rules:
```python
rule=lambda m, td, t: Constraint.Skip if td not in m.TD_STORAGE_TDS else ...
```

## Step 4: Testing approach

1. Start with the smallest available case (s4x1 or similar)
2. Create minimal input files for a single tracked demand:
   - A 100 MW data center in one zone
   - Fixed load (min = max = 100)
   - No CFE requirement initially
   - Verify the system serves the additional load
3. Progressively add features and verify each one changes results as expected
4. Key sanity checks:
   - Total system generation should increase by the TD's consumption
   - With high CFE penalty, system should build more clean generation
   - On-site diesel should only dispatch during flex events (if CFE target exists)
   - Storage should charge during high-solar hours and discharge at night
   - `grid_clean_fraction_computed.csv` values should be between 0 and 1

## Step 5: Input data preparation

After the module is working, create a companion script
`make_tracked_demand_inputs.py` that:

1. Reads the model's `loads.csv` and `variable_capacity_factors.csv`
2. Computes default time blocks (load quartiles x solar availability)
3. Writes `tracked_demand_time_blocks.csv`
4. Optionally generates `grid_clean_fraction.csv` from eGRID data or a
   prior model run's dispatch results

## Important notes

- All input files use .csv format with headers matching parameter names
- The module must work with Switch 2.0.9 (current installed version)
- Use `from pyomo.environ import *` or specific imports as needed
- Follow the naming conventions visible in existing Switch modules
- The module should be self-contained: if no tracked demand input files
  are present, it should do nothing (empty TRACKED_DEMANDS set)
- When in doubt about Switch API conventions, look at the actual source
  code in the installed switch_model package, not at documentation
