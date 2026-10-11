# Tracked Demand Module — Final Specification v1.0

## Module: `tracked_demands.py`

**Purpose:** Model specific electricity demands (data centers, electrolyzers) that
face clean energy procurement requirements (24/7 CFE, 45V), grid flexibility
obligations, and have access to on-site resources (storage, backup generation,
up to and including full-scale CCGT with CCS) to meet those requirements. The
module determines optimal siting, load scheduling, on-site investment, and clean
energy procurement within a system-wide capacity expansion optimization.

**Design mode:** Perfect foresight (all periods solved simultaneously). Myopic
compatibility is a passive byproduct but not actively supported.

**Relationship to existing modules:** Builds on the design pattern of the MCET
`flexible_loads.py` module. Extends it with CFE accounting, on-site generation
and storage investment, grid flexibility obligations, and post-solve hourly
emissions accounting.

---

## 1. Architecture Summary

### 1.1 In the optimization

The model simultaneously decides:

- **Where** to locate each tracked demand (if location-flexible)
- **When** to consume electricity (flexible dispatch within a user-specified
  envelope, which may be constant, shaped, or fully flexible)
- **How much** on-site storage, backup generation, and hydrogen storage to build
- **When** to charge/discharge storage and run on-site generators
- **How much** CFE shortfall to accept (paying a penalty)
- **How much** grid flex non-compliance to accept (paying a penalty)

CFE compliance uses **time-block-level soft targets with penalty pricing**. The
time blocks are derived by default from zonal load quartiles crossed with solar
availability (8 blocks), and can be overridden by the user. An exogenous
`grid_clean_fraction[z, t]` parameter keeps the optimization fully linear.

Grid flexibility uses **pre-specified events with a non-compliance penalty**
(default $500/MWh). Flex events can reference individual timepoints or entire
time blocks.

The tracked demand's grid draw is registered as a `Zone_Power_Withdrawal`,
representing **additional load** on top of existing `zone_demand_mw`.

### 1.2 Post-solve

A `post_solve()` function:

1. Computes **endogenous zonal emissions rates** from solved dispatch
2. Performs **hourly emissions accounting** for each tracked demand
3. Computes **hourly and annual CFE scores** by time block
4. Calculates **45V credit tier eligibility** (electrolyzer TDs)
5. Outputs `grid_clean_fraction_computed.csv` for feeding back into subsequent
   runs

### 1.3 Recommended workflow

1. First run: pre-populate `grid_clean_fraction` with eGRID-based values
2. Post-solve computes actual values and writes `grid_clean_fraction_computed.csv`
3. Copy computed file back as input for subsequent runs
4. Iterate until CFE scores converge (typically 1-2 iterations)

---

## 2. Sets

| Set | Dimen | Description |
|-----|-------|-------------|
| `TRACKED_DEMANDS` | 1 | Named tracked demand facilities |
| `TD_CANDIDATE_ZONES` | 2 | `(td, z)` - zones where each TD could be sited |
| `TD_PERIODS` | 2 | `(td, p)` - periods when each TD is active |
| `TD_TIMEPOINTS` | 2 | `(td, t)` - all timepoints in active periods |
| `TD_ONSITE_TECHS` | 1 | On-site generation technologies |
| `TD_FLEX_TIMEPOINTS` | 2 | `(td, t)` - timepoints when grid flex is required |
| `TD_TIME_BLOCKS` | 2 | `(td, block_id)` - named time blocks for CFE targets |
| `TD_BLOCK_TIMEPOINTS` | 3 | `(td, block_id, t)` - timepoints in each block |
| `TD_ELIGIBLE_ENERGY_SOURCES` | 2 | `(td, energy_source)` - clean sources for CFE |

---

## 3. Input Files and Parameters

### 3.1 `tracked_demands.csv` - Facility definitions (REQUIRED)

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `TRACKED_DEMAND` | index | - | Unique identifier |
| `td_type` | str | - | `"datacenter"` or `"electrolyzer"` |
| `td_energy_requirement_mwh_per_year` | float | - | Annual electricity service requirement |
| `td_default_min_power_mw` | float | 0 | Default minimum instantaneous consumption |
| `td_default_max_power_mw` | float | - | Default maximum instantaneous consumption |
| `td_location_fixed` | bool | True | If False, model chooses zone from candidates |
| `td_grid_interconnect_mw` | float | = td_default_max_power_mw | Maximum grid draw |
| `td_onsite_emissions_in_system_cap` | bool | False | Whether on-site emissions count toward system carbon cap |

### 3.2 `tracked_demand_candidate_zones.csv` - Siting options (REQUIRED)

| Column | Type | Description |
|--------|------|-------------|
| `TRACKED_DEMAND` | str | FK |
| `LOAD_ZONE` | str | Candidate zone |

One row per TD if location-fixed. Multiple rows if location-flexible.

### 3.3 `tracked_demand_dispatch_bounds.csv` - Load shape envelope (OPTIONAL)

| Column | Type | Description |
|--------|------|-------------|
| `TRACKED_DEMAND` | str | FK |
| `TIMEPOINT` | int | Timepoint |
| `td_min_power_mw` | float | Minimum consumption this hour |
| `td_max_power_mw` | float | Maximum consumption this hour |

If not provided, scalar defaults from `tracked_demands.csv` apply to all
timepoints. Enables shaped envelopes: higher minimums during business hours,
lower at night, etc.

### 3.4 `tracked_demand_cfe_targets.csv` - CFE requirements (OPTIONAL)

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `TRACKED_DEMAND` | str | - | FK |
| `PERIOD` | int | - | Study period |
| `time_block` | str | `"annual"` | Block identifier or `"annual"` |
| `cfe_target` | float [0-1] | 0 | Required clean energy fraction |
| `cfe_shortfall_penalty` | float ($/MWh) | 0 | Penalty for unmatched MWh |

No rows = no CFE requirement. Targets and penalties can vary by period.

### 3.5 `tracked_demand_time_blocks.csv` - Block definitions (OPTIONAL)

| Column | Type | Description |
|--------|------|-------------|
| `TRACKED_DEMAND` | str | FK |
| `time_block` | str | Block identifier |
| `TIMEPOINT` | int | Timepoint in this block |

**Default generation:** Auto-generated from zonal load quartiles (peak/high/mid/low)
crossed with solar availability (day/night at 10% capacity factor threshold).
8 blocks total. Written for user inspection and modification.

### 3.6 `tracked_demand_eligible_sources.csv` - Clean energy definition (OPTIONAL)

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `TRACKED_DEMAND` | str | - | FK |
| `energy_source` | str | - | Eligible source |
| `td_eligible_source_max_credit_mw` | float | inf | Max MW credited as clean per hour (for 45V nuclear carve-out) |

Defaults to all `NON_FUEL_ENERGY_SOURCES` including nuclear, no cap.

### 3.7 `tracked_demand_flex_events.csv` - Grid flexibility (OPTIONAL)

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `TRACKED_DEMAND` | str | - | FK |
| `TIMEPOINT` | int | - | Specific timepoint (use this OR time_block) |
| `time_block` | str | - | Block identifier (expands to all timepoints in block) |
| `td_flex_max_grid_draw_mw` | float | 0 | Max grid draw. 0 = full curtailment. |
| `td_flex_noncompliance_penalty` | float ($/MWh) | 500 | Penalty for excess grid draw |

### 3.8 `tracked_demand_onsite_techs.csv` - On-site generation (OPTIONAL)

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `td_onsite_tech` | index | - | Technology name |
| `td_onsite_tech_capital_cost` | float ($/MW-yr) | - | Annualized capital cost |
| `td_onsite_tech_variable_om` | float ($/MWh) | 0 | Variable O&M |
| `td_onsite_tech_fuel_cost` | float ($/MWh) | 0 | Fuel cost |
| `td_onsite_tech_emissions` | float (tCO2/MWh) | 0 | Gross emissions rate |
| `td_onsite_tech_capture_rate` | float [0-1] | 0 | CO2 capture rate |
| `td_onsite_tech_max_annual_hours` | float | 8760 | Runtime limit |
| `td_onsite_tech_is_clean` | bool | False | Counts as clean for CFE |
| `td_onsite_tech_min_stable_mw_fraction` | float [0-1] | 0 | Min output fraction (CCGT) |
| `td_onsite_tech_subsidy` | float ($/MWh) | 0 | Default subsidy (45Q, EOR, etc.) |

Net emissions: `emissions * (1 - capture_rate)`
Effective variable cost: `variable_om + fuel_cost - subsidy`

Example technologies:
```
td_onsite_tech, capital_cost, variable_om, fuel_cost, emissions, capture_rate, max_hours, is_clean, min_stable, subsidy
diesel,           15000,        5,          80,        0.65,      0,            200,       False,    0,          0
gas_recip,        55000,        4,          45,        0.40,      0,            4000,      False,    0,          0
ccgt,             95000,        3,          35,        0.35,      0,            8760,      False,    0.4,        0
ccgt_ccs,        145000,        5,          38,        0.35,      0.9,          8760,      False,    0.4,        30
fuel_cell_h2,   120000,         3,          60,        0.00,      0,            8760,      True,     0.1,        0
```

### 3.9 `tracked_demand_onsite_tech_subsidies.csv` - Period-indexed subsidies (OPTIONAL)

| Column | Type | Description |
|--------|------|-------------|
| `td_onsite_tech` | str | FK |
| `PERIOD` | int | Period |
| `td_onsite_tech_subsidy` | float ($/MWh) | Subsidy for this period |

Overrides scalar subsidy for specific periods. Covers 45Q phase-in/out, EOR
revenue changes, etc.

### 3.10 `tracked_demand_storage.csv` - On-site battery (OPTIONAL)

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `TRACKED_DEMAND` | str | - | FK |
| `td_storage_power_cost` | float ($/MW-yr) | - | Annualized inverter cost |
| `td_storage_energy_cost` | float ($/MWh-yr) | - | Annualized cell cost |
| `td_storage_max_hours` | float | 8 | Max energy/power ratio |
| `td_storage_min_hours` | float | 0 | Min energy/power ratio |
| `td_storage_roundtrip_eff` | float [0-1] | 0.85 | Round-trip efficiency |

Behind-the-meter only. No grid injection.

### 3.11 `tracked_demand_h2_storage.csv` - Hydrogen storage (OPTIONAL)

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `TRACKED_DEMAND` | str | - | FK |
| `td_electrolyzer_efficiency_kwh_per_kg` | float | 50 | Electricity per kg H2 |
| `td_h2_storage_cost_per_kg_yr` | float | - | Annualized tank cost |
| `td_h2_storage_max_kg` | float | inf | Max tank capacity |
| `td_h2_min_delivery_kg_per_hr` | float | 0 | Min delivery rate |
| `td_h2_max_delivery_kg_per_hr` | float | inf | Max delivery rate |

If not provided, wide-dispatch-bounds approximation applies instead.

### 3.12 `grid_clean_fraction.csv` - Exogenous grid data (OPTIONAL)

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `LOAD_ZONE` | str | - | Zone |
| `TIMEPOINT` | int | - | Timepoint |
| `grid_clean_fraction` | float [0-1] | 0.5 | Clean fraction of zonal generation |

First run: from eGRID. Subsequent: from post-solve `grid_clean_fraction_computed.csv`.

---

## 4. Decision Variables

| Variable | Index | Domain | Description |
|----------|-------|--------|-------------|
| `TDZoneAllocation` | `(td, z, p)` | [0,1] | Share of TD in zone z. Sums to 1. |
| `TDDispatch` | `(td, t)` | >= 0 | Total facility consumption (MW) |
| `TDGridDraw` | `(td, t)` | >= 0 | Grid draw (MW) |
| `BuildOnsiteTech` | `(td, tech)` | >= 0 | On-site tech built (MW) |
| `DispatchOnsiteTech` | `(td, tech, t)` | >= 0 | On-site tech dispatch (MW) |
| `TDStoragePowerCapacity` | `(td)` | >= 0 | Battery power (MW) |
| `TDStorageEnergyCapacity` | `(td)` | >= 0 | Battery energy (MWh) |
| `TDStorageCharge` | `(td, t)` | >= 0 | Charge rate (MW) |
| `TDStorageDischarge` | `(td, t)` | >= 0 | Discharge rate (MW) |
| `TDStorageLevel` | `(td, t)` | >= 0 | State of charge (MWh) |
| `TDH2Production` | `(td, t)` | >= 0 | H2 produced (kg/hr) |
| `TDH2StorageCapacity` | `(td)` | >= 0 | H2 tank (kg) |
| `TDH2StorageLevel` | `(td, t)` | >= 0 | H2 in tank (kg) |
| `TDH2Delivery` | `(td, t)` | >= 0 | H2 delivered (kg/hr) |
| `TDCFEShortfall` | `(td, block, p)` | >= 0 | CFE shortfall (MWh) |
| `TDFlexExcess` | `(td, t)` | >= 0 | Grid draw above flex limit (MW) |

---

## 5. Constraints

### 5.1 Facility energy balance
```
TDDispatch[td,t] = TDGridDraw[td,t]
    + Sum_tech DispatchOnsiteTech[td,tech,t]
    + TDStorageDischarge[td,t]
    - TDStorageCharge[td,t]
```

### 5.2 Dispatch bounds
```
td_min_power_mw[td,t] <= TDDispatch[td,t] <= td_max_power_mw[td,t]
```
Timepoint-indexed from dispatch bounds file, or scalar defaults.

### 5.3 Annual service requirement
```
Sum_t TDDispatch[td,t] * tp_weight_in_year[t]
    >= td_energy_requirement_mwh_per_year[td]
```

### 5.4 Grid interconnection limit
```
TDGridDraw[td,t] <= td_grid_interconnect_mw[td]
```

### 5.5 CFE time-block targets (soft)
```
[estimated clean in block] + TDCFEShortfall[td,block,p]
    >= cfe_target[td,p,block] * [total consumption in block]

estimated clean = Sum_{t in block} (
    TDGridDraw[td,t] * grid_clean_fraction[z,t]
    + Sum_{clean tech} DispatchOnsiteTech[td,tech,t]
    + TDStorageDischarge[td,t] * grid_clean_fraction[z,t]
) * tp_weight_in_year[t]
```
Storage discharge credited at grid clean fraction (conservative approximation;
post-solve tracks actual clean charge source).

### 5.6 Grid flexibility (soft with penalty)
```
TDGridDraw[td,t] <= td_flex_max_grid_draw_mw[td,t] + TDFlexExcess[td,t]
```
Default penalty $500/MWh. Set very high for effectively hard constraint.

### 5.7 On-site technology
```
DispatchOnsiteTech[td,tech,t] <= BuildOnsiteTech[td,tech]
DispatchOnsiteTech[td,tech,t] >= BuildOnsiteTech[td,tech] * min_stable_fraction
Sum_t Dispatch * tp_weight_in_year[t] <= Build * max_annual_hours
```
Note: min stable output applied as permanent floor (LP relaxation). Integer
commitment deferred to future enhancement.

### 5.8 Battery storage
```
Charge/Discharge <= PowerCapacity
Level <= EnergyCapacity
min_hours * PowerCapacity <= EnergyCapacity <= max_hours * PowerCapacity
Level[t] = Level[t-1] + Charge*sqrt(eff)*dt - Discharge/sqrt(eff)*dt
```

### 5.9 Hydrogen storage (if explicit)
```
H2Production[td,t] = TDDispatch[td,t] / efficiency_kwh_per_kg
H2Level[t] = H2Level[t-1] + H2Production*dt - H2Delivery*dt
H2Level <= H2StorageCapacity <= td_h2_storage_max_kg
min_delivery <= H2Delivery <= max_delivery
```

### 5.10 Location allocation
```
Sum_z TDZoneAllocation[td,z,p] = 1
TDGridDraw linked to allocated zone's energy balance
```

### 5.11 System integration
```
TDGridDrawByZone[z,t] added to Zone_Power_Withdrawals
```

### 5.12 Carbon cap integration (configurable, default off)
When `td_onsite_emissions_in_system_cap = True`:
on-site emissions added to system AnnualEmissions.

---

## 6. Objective Function Components

| Component | Description |
|-----------|-------------|
| `TDOnsiteTechFixedCost` | Capital cost of on-site generation |
| `TDOnsiteTechVariableCost` | Operating cost net of subsidies |
| `TDStorageFixedCost` | Battery capital cost |
| `TDH2StorageFixedCost` | H2 tank capital cost |
| `TDCFEShortfallCost` | CFE penalty payments |
| `TDFlexNoncomplianceCost` | Flex penalty payments |
| `TDOnsiteEmissionsCost` | Carbon cost on opted-in on-site emissions |

---

## 7. Output Files

| File | Description |
|------|-------------|
| `tracked_demand_hourly.csv` | Hour-by-hour dispatch, emissions, CFE score |
| `tracked_demand_annual.csv` | Annual summary per TD per period |
| `tracked_demand_cfe_detail.csv` | CFE performance by time block |
| `tracked_demand_flex_detail.csv` | Flex event compliance detail |
| `tracked_demand_45v.csv` | 45V credit tiers and values (electrolyzer TDs) |
| `grid_clean_fraction_computed.csv` | Endogenous clean fractions for iterative workflow |

See Section 7 detail in previous spec version for full column listings.

---

## 8. Confirmed Design Decisions

| # | Decision | Resolution |
|---|----------|------------|
| 1 | Zonal emissions rate | Exogenous in optimization (eGRID first run, iterate); endogenous post-solve |
| 2 | CFE enforcement | Soft time-block targets in optimization; precise hourly accounting post-solve |
| 3 | Target structure | Time-block targets with period-indexed penalties |
| 4 | Default time blocks | Load quartiles x solar availability (8 blocks), auto-generated, overridable |
| 5 | Demand flexibility | Decision variable with timepoint-indexed min/max bounds; shaped envelopes supported |
| 6 | H2 storage | Both explicit storage and wide-bounds approximation available |
| 7 | Battery storage | Dedicated behind-the-meter. Grid participation deferred. |
| 8 | On-site generation | Tech menu up to CCGT+CCS. Capture rate, min stable output, subsidies. |
| 9 | Subsidies | Period-indexed $/MWh. Covers 45Q, EOR. Endogenous CO2 pricing deferred. |
| 10 | On-site emissions in cap | Configurable per TD, default False |
| 11 | Grid flexibility | Pre-specified events or blocks, penalty default $500/MWh |
| 12 | Solve mode | Perfect foresight primary |
| 13 | Load integration | Additional load via Zone_Power_Withdrawals |
| 14 | Clean eligibility | Default all non-fuel sources incl. nuclear. Per-source MW cap for carve-outs. |
| 15 | Incrementality | Post-solve only. Generator vintage tracking a known limitation. |

---

## 9. Future Enhancements

| Enhancement | Description |
|-------------|-------------|
| Grid-participating storage | On-site storage exports to grid |
| Shared storage clean tracking | Joint grid/TD battery with clean SoC |
| Endogenous reserve product | TD offers curtailment capacity for payment |
| Coincident peak flex | Flex events from endogenous system peaks |
| Integer commitment | On/off for on-site CCGT |
| Endogenous CO2 pricing for CCS | Subsidy from system carbon shadow price |
| Generator-level incrementality | Automated 36-month rule in post-solve |
| Myopic chaining | Dynamic target tightening between periods |
