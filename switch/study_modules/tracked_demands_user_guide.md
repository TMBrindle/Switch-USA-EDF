# Tracked Demands Module — User Guide

**Module:** `study_modules/tracked_demands.py`  
**Switch version:** 2.0.9  
**Last updated:** 2026-05-03

---

## Table of Contents

1. [Overview](#1-overview)
2. [Design Philosophy](#2-design-philosophy)
3. [Use Cases](#3-use-cases)
4. [Architecture and Module Stages](#4-architecture-and-module-stages)
5. [Input Files Reference](#5-input-files-reference)
6. [Output Files Reference](#6-output-files-reference)
7. [Command-Line Options](#7-command-line-options)
8. [Input Aliasing and Scenario Management](#8-input-aliasing-and-scenario-management)
9. [Test Suite Results](#9-test-suite-results)
10. [Known Limitations](#10-known-limitations)

---

## 1. Overview

The Tracked Demands module adds one or more named electricity demands to a Switch
capacity expansion model, each with its own:

- Annual energy service requirement (MWh/yr)
- Instantaneous power bounds (MW)
- Physical siting (fixed zone or optimized across candidate zones)
- Clean energy fraction (CFE) targets, enforced by time block
- Behind-the-meter generation and battery storage
- Grid flexibility obligations
- Hydrogen production and H2 buffer storage (electrolyzers)
- Renewable Energy Certificate (REC) purchasing

The module is designed around two primary applications: **large-scale data centers**
requiring 24/7 clean power commitments, and **green hydrogen electrolyzers** seeking
to comply with the IRA 45V production tax credit. It is fully backward-compatible — a
model without any tracked demand input files runs identically to an unmodified Switch
model.

---

## 2. Design Philosophy

### 2.1 Endogenous load with exogenous clean fraction

The module adds new load to the grid, causing the system to build generation capacity
to serve it. CFE compliance uses an **exogenous `grid_clean_fraction` parameter** during
optimization — this keeps the problem linear (LP or MIP depending on tech choices) and
avoids the bilinear product of dispatch × grid-clean-fraction. Accurate post-hoc CFE
accounting is done in `post_solve` (Stage 10) using actual hourly dispatch.

The recommended workflow is iterative:
1. First run: use `grid_clean_fraction.csv` from eGRID or default to 0.5.
2. Post-solve: `grid_clean_fraction_computed.csv` is written to the output directory.
3. Second run: alias `grid_clean_fraction.csv` to the computed file for accurate CFE attribution.

### 2.2 Soft constraints with penalty pricing

CFE targets and flex event limits are enforced as **soft constraints with a shortfall
penalty** ($/MWh). This avoids infeasibility when targets cannot be met given the
available technology portfolio, and allows the model to decide whether it is cheaper to
invest in clean generation or to pay the shortfall penalty. A penalty of zero makes a
target advisory (reported but not affecting the objective).

### 2.3 Proportional grid clean attribution

The TD's grid-clean credit in each timepoint equals:

    TDGridDraw[td, t] × grid_clean_fraction[zone, t]

This is a proportional attribution — the TD gets its fair share of the clean energy
mix in its zone. It does not claim preferential access to specific generators. Stage 9C
(grid clean caps) can limit this attribution to represent deliverability constraints or
match additionality accounting methodologies.

### 2.4 Graceful degradation

Every input file is optional. If a file is absent, the corresponding feature is disabled
with no effect on other stages. A model with only `tracked_demands.csv` and
`tracked_demand_candidate_zones.csv` runs as pure load addition (Stage 1 only).

### 2.5 Multi-TD support

Multiple tracked demands can be defined simultaneously, each in its own row of
`tracked_demands.csv` and with its own optional input entries. TDs are fully
independent — CFE accounting, onsite build decisions, and cost components are all
tracked separately per TD with no cross-contamination.

---

## 3. Use Cases

### 3.1 Data center with 24/7 CFE commitment

A hyperscale data center operates at a fixed power draw (e.g., 100 MW constant) and
has made a public commitment to match its consumption with clean energy on an hourly
basis. The module models this by:

- Setting `td_default_min_power_mw = td_default_max_power_mw` to fix dispatch
- Defining solar and non-solar time blocks representing hours where clean power is
  easy vs. challenging to procure
- Setting `td_cfe_target` and `td_cfe_shortfall_penalty` to drive clean investment
- Allowing onsite generation (solar, wind, geothermal, small nuclear, gas-CCS, diesel)
- Optionally purchasing grid RECs to cover residual CFE gaps

The model endogenously determines whether it is cheaper to build onsite clean
generation, purchase RECs, or pay the shortfall penalty.

**Key insight from testing:** In Mountain West zones already ~90% clean (2035
scenario), strict CFE targets add near-zero system cost because clean capacity is
built for other reasons (planning reserve, RPS). The marginal cost of strict CFE
requirements rises sharply in dirty-grid regions (Mid-Atlantic) or when imposing
near-100% targets.

### 3.2 Green hydrogen electrolyzer (IRA 45V compliance)

An electrolyzer produces hydrogen for industrial or transport use and seeks to qualify
for the 45V clean hydrogen production tax credit, which requires lifecycle emissions
below specified thresholds tied to CFE requirements. The module models this by:

- Setting `td_type = electrolyzer` (enables 45V post-solve assessment)
- Specifying the annual hydrogen delivery target (converted internally from MWh
  of electricity via electrolyzer efficiency)
- Allowing flexible consumption within MW bounds (`td_default_min_power_mw = 0`,
  `td_default_max_power_mw = rated MW`)
- Adding an H2 buffer tank to decouple electricity procurement from hydrogen delivery
- Setting symmetric CFE targets (90% in both solar and non-solar hours per 45V hourly
  matching requirement)
- Applying flex events to shift load toward clean hours

The 45V post-solve assessment writes `tracked_demand_45v_assessment.csv` with
lifecycle CO2 kg per kg-H2 and the corresponding 45V credit tier.

### 3.3 Consequential emissions accounting

Because tracked demands add load to the grid, they affect the dispatch of all
generation, creating consequential emissions separate from the TD's own energy use.
The module writes `tracked_demand_hourly_emissions.csv` attributing both grid and
onsite emissions to each TD per timepoint, enabling analysts to assess whether a CFE
commitment actually reduces system emissions or merely reallocates existing clean power.

**Design note:** A CFE commitment can *increase* system emissions if it reallocates
existing clean capacity to the TD's clean block, forcing fossil generation elsewhere.
Only new clean investment (additionality) avoids this outcome. The REC purchasing
feature (Stage 11) is specifically designed for additionality accounting: the
`td_rec_supply_mwh` cap should reflect only newly built regional clean capacity.

### 3.4 Location optimization

When `td_location_fixed = 0` and multiple candidate zones are listed in
`tracked_demand_candidate_zones.csv`, the model optimally allocates the TD's
interconnect capacity across zones. This answers the question: given a choice of
grid interconnection points, which zone minimizes total system cost including CFE
compliance?

### 3.5 Onsite generation and storage portfolio

The module allows the model to endogenously build and dispatch a portfolio of
behind-the-meter technologies (solar, wind, gas-CCS, geothermal, nuclear, diesel
backup). Combined with battery storage, this creates a fully integrated campus-scale
energy model nested within the regional capacity expansion model.

---

## 4. Architecture and Module Stages

The module is implemented in sequential stages, each adding functionality while
remaining backward-compatible with prior stages.

| Stage | Feature | Key Input Files | Activated When |
|-------|---------|-----------------|----------------|
| 1 | Grid-only load tracking, annual energy requirement, power bounds | `tracked_demands.csv`, `tracked_demand_candidate_zones.csv` | Always (if TD file present) |
| 2 | CFE time-block targets with shortfall penalty | `tracked_demand_time_blocks.csv`, `tracked_demand_cfe_targets.csv`, `grid_clean_fraction.csv` | CFE target file present |
| 3 | On-site generation build and dispatch | `tracked_demand_onsite_techs.csv` | Techs file present |
| 4 | Behind-the-meter battery storage | `tracked_demand_storage.csv` | Storage file present |
| 5 | Grid flexibility events (soft grid draw caps) | `tracked_demand_flex_events.csv` | Flex file present |
| 6 | Timepoint-indexed dispatch bounds (shaped load envelopes) | `tracked_demand_dispatch_bounds.csv` | Bounds file present |
| 7 | Location-flexible siting across candidate zones | Multiple rows in `tracked_demand_candidate_zones.csv`, `td_location_fixed = 0` | TD has `td_location_fixed = 0` |
| 8 | Hydrogen storage (electrolyzers) | `tracked_demand_h2_storage.csv` | H2 file present |
| 9 | Onsite build caps, annual grid caps, grid clean credit caps | `tracked_demand_onsite_build_caps.csv`, `tracked_demand_grid_caps.csv`, `tracked_demand_grid_clean_caps.csv` | Respective cap files present |
| 10 | Post-solve: grid clean fraction, emissions, CFE computed, 45V | (reads `dispatch.csv`) | Always (if dispatch output exists) |
| 11 | REC purchasing | `tracked_demand_rec_supply.csv` | REC file present |

### 4.1 Objective function contributions

The module appends the following cost components to `Cost_Components_Per_Period`:

| Component | Expression | Unit |
|-----------|-----------|------|
| `TDCFEShortfallCost` | CFE shortfall × penalty, summed over TDs and blocks | $/yr |
| `TDOnsiteTechFixedCost` | Annualized capital cost × built MW, summed over techs | $/yr |
| `TDOnsiteTechVariableCost` | (VOM + fuel − subsidy) × dispatch × weight, per tech | $/yr |
| `TDStorageFixedCost` | Annualized power and energy costs × built capacity | $/yr |
| `TDFlexNoncomplianceCost` | Flex excess × penalty × weight, per event | $/yr |
| `TDH2StorageFixedCost` | Tank capital cost × capacity | $/yr |
| `TDRECCost` | REC purchases × price, per block | $/yr |

---

## 5. Input Files Reference

All files are placed in the scenario inputs directory (e.g.,
`in/2035/<scenario_name>/`). All files except `tracked_demands.csv` and
`tracked_demand_candidate_zones.csv` are optional.

---

### 5.1 `tracked_demands.csv` — Core demand definition

One row per tracked demand. Defines the energy service requirement and power bounds.

| Column | Required | Type | Units | Description | Typical Values / Sources |
|--------|----------|------|-------|-------------|--------------------------|
| `TRACKED_DEMAND` | Yes | string | — | Unique identifier for this demand | `dc_hyperscale_1`, `elec_h2_plant_a` |
| `td_type` | Yes | string | — | Demand category. `datacenter` enables DC-specific post-solve outputs; `electrolyzer` enables H2/45V outputs. Any string accepted. | `datacenter`, `electrolyzer` |
| `td_energy_requirement_mwh_per_year` | Yes | float | MWh/yr | Annual electricity consumption enforced as a constraint every period. For a DC, this is the contracted annual load. For an electrolyzer, this is the electricity needed to produce the annual H2 target. | DC: 100 MW × 8,760 hr = 876,000 MWh. Electrolyzer: H2 target (kg/yr) × efficiency (kWh/kg) / 1,000 |
| `td_default_min_power_mw` | No | float | MW | Minimum power draw every timepoint. Set equal to max for a must-run facility (constant DC load). Set to 0 for a fully flexible facility. | DC: 100 (must-run); Electrolyzer: 0 (fully flexible) |
| `td_default_max_power_mw` | Yes | float | MW | Maximum power draw every timepoint. Defines the grid interconnect capacity when `td_grid_interconnect_mw` is not specified. | Nameplate rated load in MW |
| `td_location_fixed` | No | bool (0/1) | — | If 1 (default), TD is sited at its single candidate zone. If 0, model optimally splits load across candidate zones (Stage 7). | 1 for known locations; 0 for site selection studies |
| `td_grid_interconnect_mw` | No | float | MW | Physical grid connection limit. Defaults to `td_default_max_power_mw`. Relevant when grid draw can be supplemented by onsite generation. | Same as max_power_mw unless onsite generation exceeds grid demand |
| `td_onsite_emissions_in_system_cap` | No | bool (0/1) | — | If 1, onsite generation emissions count toward the system carbon cap. Currently stored but not enforced. | 0 (default) |

**Example:**
```
TRACKED_DEMAND,td_type,td_energy_requirement_mwh_per_year,td_default_min_power_mw,td_default_max_power_mw
dc_campus_1,datacenter,876000,100,100
elec_plant_a,electrolyzer,438000,0,100
```

---

### 5.2 `tracked_demand_candidate_zones.csv` — Siting

One row per (TD, zone) pair. For location-fixed TDs, one row only.

| Column | Required | Type | Description |
|--------|----------|------|-------------|
| `TRACKED_DEMAND` | Yes | string | Must match a row in `tracked_demands.csv` |
| `LOAD_ZONE` | Yes | string | Switch load zone ID from `load_zones.csv` |

For location-flexible TDs (`td_location_fixed = 0`), list all candidate zones —
the model will optimize the interconnect allocation across them.

**Example (location-fixed):**
```
TRACKED_DEMAND,LOAD_ZONE
dc_campus_1,p33
```

**Example (location-flexible, 3 zones):**
```
TRACKED_DEMAND,LOAD_ZONE
dc_campus_1,p32
dc_campus_1,p33
dc_campus_1,p34
```

---

### 5.3 `tracked_demand_time_blocks.csv` — CFE time blocks (Stage 2)

Maps each timepoint to a named time block for CFE accounting. Must be provided if
`tracked_demand_cfe_targets.csv` is used.

| Column | Required | Type | Description |
|--------|----------|------|-------------|
| `TRACKED_DEMAND` | Yes | string | TD identifier |
| `time_block` | Yes | string | Block name (e.g., `solar_hours`, `non_solar_hours`) |
| `TIMEPOINT` | Yes | integer | Switch timepoint ID |

One row per (TD, timepoint). Every timepoint that should contribute to CFE accounting
must appear. The same set of time block names must be used in
`tracked_demand_cfe_targets.csv`.

**Design tip:** Blocks should reflect hours where clean energy availability differs
structurally. The canonical split is `solar_hours` (hours 8–20) and `non_solar_hours`
(hours 0–7, 21–23). For electrolyzers, a finer split (morning ramp, midday peak,
evening) can improve 45V tier optimization. An auto-generation script from hour-of-day
rules is a planned future feature.

---

### 5.4 `tracked_demand_cfe_targets.csv` — CFE compliance targets (Stage 2)

One row per (TD, period, time_block) combination where a CFE target is imposed.

| Column | Required | Type | Units | Description | Typical Values / Sources |
|--------|----------|------|-------|-------------|--------------------------|
| `TRACKED_DEMAND` | Yes | string | — | TD identifier |
| `PERIOD` | Yes | integer | — | Model period year (e.g., 2035) |
| `time_block` | Yes | string | — | Must match a block name in `tracked_demand_time_blocks.csv` |
| `td_cfe_target` | Yes | float | fraction [0–1] | Required clean energy fraction for this block. 1.0 = 100% clean, 0.9 = 90% clean. | 0.9 (solar hours); 0.75 (non-solar hours) — typical commercial commitment. 0.9/0.9 — 45V symmetric requirement |
| `td_cfe_shortfall_penalty` | No | float | $/MWh | Cost per MWh of CFE shortfall. 0 = advisory target (reported, not optimized). | 100–500 $/MWh for binding constraints; 0 for reporting only. Compare to LCOE of marginal clean generation (~$40–80/MWh) to calibrate whether the model should invest or pay penalty |

**Example:**
```
TRACKED_DEMAND,PERIOD,time_block,td_cfe_target,td_cfe_shortfall_penalty
dc_campus_1,2035,solar_hours,0.9,200
dc_campus_1,2035,non_solar_hours,0.75,100
```

**Calibration guidance for `td_cfe_shortfall_penalty`:**
- Below ~$40/MWh: model will always pay the penalty rather than invest in new clean generation (LCOE of cheapest option)
- $100–300/MWh: binding in most contexts; model will invest selectively
- $500+/MWh: effectively hard constraint in most scenarios
- $0: advisory — targets are tracked and reported without affecting the objective

---

### 5.5 `grid_clean_fraction.csv` — Exogenous grid clean fraction (Stage 2)

Used in the optimization to estimate how much grid draw counts as clean.

| Column | Required | Type | Units | Description |
|--------|----------|------|-------|-------------|
| `LOAD_ZONE` | Yes | string | — | Switch load zone ID |
| `TIMEPOINT` | Yes | integer | — | Switch timepoint ID |
| `grid_clean_fraction` | Yes | float | fraction [0–1] | Fraction of grid power in this zone and timepoint sourced from clean generators |

**Default behavior:** If this file is absent or a zone/timepoint combination is
missing, the module defaults to 0.5 (conservative first-run assumption).

**Recommended workflow:**
1. Initial run: omit the file or use eGRID regional averages (available at
   epa.gov/egrid by NERC region)
2. After first solve: copy `grid_clean_fraction_computed.csv` from the output
   directory to the inputs directory and alias the filename
3. Re-run: the model now uses actual computed clean fractions from the prior solution

**Sources for initial values:**
- **eGRID**: EPA publishes annual zone-level clean energy percentages; use the
  subregion-to-zone mapping from `hierarchy.csv`
- **Prior model run**: most accurate; use `grid_clean_fraction_computed.csv` output
- **Default 0.5**: conservative; appropriate when clean fractions are unknown or
  when testing whether a CFE target is feasible at all

---

### 5.6 `tracked_demand_onsite_techs.csv` — Technology menu (Stage 3)

Defines the set of technologies available for on-site investment. All TDs share the
same technology menu; per-TD build caps are in `tracked_demand_onsite_build_caps.csv`.

| Column | Required | Type | Units | Description | Typical Values / Sources |
|--------|----------|------|-------|-------------|--------------------------|
| `td_onsite_tech` | Yes | string | — | Technology identifier (index column) | `solar`, `wind`, `geothermal`, `advanced_nuclear`, `gas_ccs`, `diesel` |
| `td_onsite_tech_capital_cost` | Yes | float | $/MW-yr | Annualized capital cost (CAPEX × CRF). Applied every period as a fixed cost per MW built. | NREL ATB: solar ~$65k, wind ~$90k, geothermal ~$280k, advanced nuclear ~$320k, gas CCS ~$180k, diesel ~$15k (all $/MW-yr, 2035) |
| `td_onsite_tech_variable_om` | No | float | $/MWh | Variable operations and maintenance cost | ATB: solar $3, wind $3, geothermal $5, nuclear $10, gas CCS $8, diesel $5 |
| `td_onsite_tech_fuel_cost` | No | float | $/MWh | Fuel cost (output-based). For gas CCS, use $/MWh-elec including heat rate. | AEO natural gas $/MMBTU × heat rate ÷ 3.412. Diesel: ~$80/MWh. Gas CCS: ~$40/MWh |
| `td_onsite_tech_emissions` | No | float | tCO2/MWh | Gross CO2 emissions rate before capture | Gas turbine: ~0.4. Diesel: ~0.65. Clean techs: 0 |
| `td_onsite_tech_capture_rate` | No | float | fraction [0–1] | CO2 capture fraction. Net emissions = gross × (1 − capture_rate) | Gas CCS: 0.90. All other techs: 0 |
| `td_onsite_tech_max_annual_hours` | No | float | hours/yr | Maximum annual operating hours per MW built. Use to model backup-only constraints. | Diesel backup: 200 (emergency only). Solar/wind: 8760 (unlimited by this constraint; CF limits apply separately). Geothermal/nuclear: 8760 |
| `td_onsite_tech_is_clean` | No | bool (0/1) | — | If 1, dispatch from this tech counts toward CFE targets. | Solar, wind, geothermal, nuclear, gas CCS: 1. Diesel: 0 |
| `td_onsite_tech_min_stable_mw_fraction` | No | float | fraction [0–1] | Minimum stable output as fraction of built capacity (LP relaxation of unit commitment). | 0 (default, fully continuous). Gas turbines: 0.4–0.6 if unit commitment is modeled |
| `td_onsite_tech_subsidy` | No | float | $/MWh | Default per-MWh production subsidy reducing the net variable cost | ITC/PTC equivalent value. Wind/solar PTC 2035: ~$25/MWh |
| `td_onsite_tech_min_build_mw` | No | float | MW | Minimum capacity if any is built; triggers MIP binary variable. 0 = continuous (LP). | 0 for most cases. Nuclear: 500–1,000 MW for minimum economic size (MIP) |
| `td_onsite_tech_build_increment_mw` | No | float | MW | Capacity increment above minimum; triggers integer variable. 0 = continuous above minimum. | 0 for most cases. Nuclear: 1,000 MW steps |
| `td_onsite_tech_cf_source` | No | string | — | Energy source string for CF profile lookup. `.` or empty = fully dispatchable. `sun` or `wind` = inherits zone-averaged grid CF. | `sun` for solar, `wind` for wind, `.` for all others. Must match `gen_energy_source` values in the model (case-insensitive) |

**Current reference values (2035 scenarios, this model):**

| Tech | Capital ($/MW-yr) | VOM ($/MWh) | Fuel ($/MWh) | Clean? | Hours | CF Source |
|------|------|------|------|------|------|------|
| diesel | 15,000 | 5 | 80 | No | 200 | . |
| solar | 65,000 | 3 | 0 | Yes | 8,760 | sun |
| wind | 90,000 | 3 | 0 | Yes | 8,760 | wind |
| gas_ccs | 180,000 | 8 | 40 | Yes | 8,760 | . |
| geothermal | 280,000 | 5 | 0 | Yes | 8,760 | . |
| advanced_nuclear | 320,000 | 10 | 10 | Yes | 8,400 | . |

**Note on MIP vs LP:** Setting `td_onsite_tech_min_build_mw > 0` or
`td_onsite_tech_build_increment_mw > 0` converts the tech from a continuous LP variable
to a MIP variable. This significantly increases solve time. For planning studies,
leave both at 0 and accept that the model may build fractional MW values.

---

### 5.7 `tracked_demand_onsite_tech_subsidies.csv` — Period-specific subsidies (Stage 3)

Optional overrides for `td_onsite_tech_subsidy` by period. Useful for ITC/PTC phase-outs.

| Column | Required | Type | Units | Description |
|--------|----------|------|-------|-------------|
| `td_onsite_tech` | Yes | string | — | Must match a tech in `tracked_demand_onsite_techs.csv` |
| `PERIOD` | Yes | integer | — | Model period year |
| `td_onsite_tech_subsidy_by_period` | Yes | float | $/MWh | Subsidy rate for this tech in this period |

---

### 5.8 `tracked_demand_onsite_predetermined.csv` — Fixed capacity (Stage 3)

For (TD, tech) pairs where capacity is predetermined (e.g., existing solar already
installed at the campus). The solver dispatches but does not optimize the size.

| Column | Required | Type | Units | Description |
|--------|----------|------|-------|-------------|
| `TRACKED_DEMAND` | Yes | string | — | TD identifier |
| `td_onsite_tech` | Yes | string | — | Tech identifier |
| `td_onsite_predetermined_mw` | Yes | float | MW | Fixed capacity; excluded from MIP build sizing |

---

### 5.9 `tracked_demand_storage.csv` — Behind-the-meter battery (Stage 4)

One row per TD that has behind-the-meter battery storage. The model optimally sizes
power capacity (MW) and energy capacity (MWh) within the specified duration bounds.

| Column | Required | Type | Units | Description | Typical Values / Sources |
|--------|----------|------|-------|-------------|--------------------------|
| `TRACKED_DEMAND` | Yes | string | — | Index column; TD identifier |
| `td_storage_power_cost` | Yes | float | $/MW-yr | Annualized inverter/power electronics cost | NREL ATB 4-hour BESS power component: ~$80,000 $/MW-yr (2035) |
| `td_storage_energy_cost` | Yes | float | $/MWh-yr | Annualized cell/energy cost | NREL ATB 4-hour BESS energy component: ~$20,000 $/MWh-yr (2035) |
| `td_storage_max_hours` | No | float | hours | Maximum energy-to-power ratio. Constrains `EnergyCapacity ≤ max_hours × PowerCapacity`. Default: 8 | 1–2 for grid balancing; 4 for co-located solar+storage; 8–12 for multi-day shifting |
| `td_storage_min_hours` | No | float | hours | Minimum duration if any storage is built. Default: 0 (no minimum) | 0 for most cases; 2 if a minimum contract duration applies |
| `td_storage_roundtrip_eff` | No | float | fraction [0–1] | Round-trip efficiency. Applied as √RTE per charge/discharge leg. Default: 0.85 | Li-ion BESS: 0.85–0.90. Flow batteries: 0.70–0.75 |

---

### 5.10 `tracked_demand_flex_events.csv` — Flexibility events (Stage 5)

Defines time blocks or specific timepoints where the TD's grid draw is capped
(soft constraint). Supports utility demand response programs, grid emergency events,
or structural load shift obligations.

Accepts either `time_block` or `TIMEPOINT` as the second column:
- `time_block`: expanded using `tracked_demand_time_blocks.csv` to individual timepoints
- `TIMEPOINT`: used directly without expansion

| Column | Required | Type | Units | Description | Typical Values |
|--------|----------|------|-------|-------------|----------------|
| `TRACKED_DEMAND` | Yes | string | — | TD identifier |
| `time_block` or `TIMEPOINT` | Yes | string / integer | — | Block name (expanded) or direct timepoint ID |
| `td_flex_max_grid_draw_mw` | Yes | float | MW | Maximum grid draw during this event. Excess above this limit is penalized. | 10–50% of rated load for demand response. 0 for full islanding test |
| `td_flex_noncompliance_penalty` | No | float | $/MWh | Penalty for exceeding the cap. Default: 500 $/MWh | 500 $/MWh (default) aligns with typical emergency event value-of-lost-load. Reduce to model voluntary programs |

**Example — electrolyzer shifting out of non-solar hours:**
```
TRACKED_DEMAND,time_block,td_flex_max_grid_draw_mw,td_flex_noncompliance_penalty
elec_plant_a,non_solar_hours,10,1000
```

---

### 5.11 `tracked_demand_dispatch_bounds.csv` — Timepoint-specific bounds (Stage 6)

Overrides the scalar `td_default_min/max_power_mw` for specific timepoints. Useful
for modelling shaped load profiles (e.g., a data center that reduces load for
maintenance windows or follows a demand response signal at specific hours).

| Column | Required | Type | Units | Description |
|--------|----------|------|-------|-------------|
| `TRACKED_DEMAND` | Yes | string | — | TD identifier |
| `TIMEPOINT` | Yes | integer | — | Switch timepoint ID |
| `td_min_power_mw` | No | float | MW | Minimum power this timepoint; defaults to `td_default_min_power_mw` |
| `td_max_power_mw` | No | float | MW | Maximum power this timepoint; defaults to `td_default_max_power_mw` |

---

### 5.12 `tracked_demand_h2_storage.csv` — Hydrogen buffer tank (Stage 8)

For electrolyzer TDs: decouples the timing of electricity consumption from hydrogen
delivery. The model optimizes tank capacity to minimize total cost.

| Column | Required | Type | Units | Description | Typical Values / Sources |
|--------|----------|------|-------|-------------|--------------------------|
| `TRACKED_DEMAND` | Yes | string | — | Index column; must be an electrolyzer-type TD |
| `td_h2_storage_cost_per_kg_yr` | Yes | float | $/kg-yr | Annualized cost of H2 storage tank capacity | Compressed gas (350 bar): $8–15 $/kg-yr. Liquid H2: $15–25 $/kg-yr. Underground cavern (large-scale): $1–3 $/kg-yr. DOE H2A model for project-scale estimates |
| `td_electrolyzer_efficiency_kwh_per_kg` | No | float | kWh/kg-H2 | Electricity consumption per kg of H2 produced. Default: 50 | PEM electrolyzer 2025: 50–55 kWh/kg. Alkaline 2025: 48–53 kWh/kg. 2035 forecast: 45–50 kWh/kg (NREL H2A, DOE Hydrogen Shot target: 45 kWh/kg) |
| `td_h2_storage_max_kg` | No | float | kg | Maximum tank capacity. Default: 1×10⁹ kg (effectively unconstrained) | Site-specific; 1,000–50,000 kg for onsite compressed storage; uncapped when optimizing freely |
| `td_h2_min_delivery_kg_per_hr` | No | float | kg/hr | Minimum continuous delivery rate. Default: 0 | Set to offtake contract minimum delivery rate if applicable |
| `td_h2_max_delivery_kg_per_hr` | No | float | kg/hr | Maximum delivery rate (pipeline or truck). Default: 1×10⁹ (unconstrained) | Sized to downstream demand; pipeline: ~1,000 kg/hr for 10 MW equivalent |

---

### 5.13 `tracked_demand_onsite_build_caps.csv` — Technology build limits (Stage 9A)

Per-(TD, tech) upper bounds on `BuildOnsiteTech`. Used to reflect realistic site
constraints (rooftop area limits solar, land constraints limit wind, regulatory caps
on nuclear, etc.). Techs not listed in this file are unconstrained.

| Column | Required | Type | Units | Description | Typical Values |
|--------|----------|------|-------|-------------|----------------|
| `TRACKED_DEMAND` | Yes | string | — | TD identifier |
| `td_onsite_tech` | Yes | string | — | Must match a tech in `tracked_demand_onsite_techs.csv` |
| `td_onsite_build_mw_cap` | Yes | float | MW | Maximum MW that can be built for this (TD, tech) pair. 0 = prohibited. | Diesel: 5 MW (backup only); Solar: 50–400 MW (site-constrained); Wind: 0–300 MW (zoning); Geothermal: 30 MW (resource limit); Nuclear: 0 (regulatory) |

**Reference caps used in this model (dc_realistic scenario):**

| Tech | Cap (MW) | Rationale |
|------|----------|-----------|
| diesel | 5 | Emergency backup only |
| solar | 400 | Rooftop + adjacent land |
| wind | 300 | Available land within campus footprint |
| gas_ccs | 30 | Site gas infrastructure limit |
| geothermal | 30 | Regional resource limit |
| advanced_nuclear | 0 | Regulatory prohibition at facility scale |

---

### 5.14 `tracked_demand_grid_caps.csv` — Annual grid draw limits (Stage 9B)

Per-(TD, period) caps on total annual grid draw. Useful for modelling tariff structures
with annual energy caps, corporate sustainability targets to reduce grid dependence,
or scenarios exploring high self-sufficiency.

| Column | Required | Type | Units | Description | Typical Values |
|--------|----------|------|-------|-------------|----------------|
| `TRACKED_DEMAND` | Yes | string | — | TD identifier |
| `PERIOD` | Yes | integer | — | Model period year |
| `td_annual_grid_max_mwh` | No | float | MWh/yr | Annual grid draw ceiling. Default: unconstrained | Set to (1 − target_self_sufficiency) × annual_load |
| `td_annual_grid_min_mwh` | No | float | MWh/yr | Annual grid draw floor (minimum grid offtake). Default: 0 | Used with grid service contracts requiring minimum offtake |

---

### 5.15 `tracked_demand_grid_clean_caps.csv` — Grid clean credit limits (Stage 9C)

Per-(TD, period, time_block) cap on the grid clean MWh credited toward the CFE target.
When this cap binds, the model must source the remaining CFE requirement from onsite
clean generation or pay the shortfall penalty.

This mechanism models **deliverability constraints** (not all grid clean power is
deliverable to the TD's location), or **additionality requirements** (the TD can only
claim credit for newly built regional clean capacity, not the grid's existing clean mix).

| Column | Required | Type | Units | Description | Typical Values |
|--------|----------|------|-------|-------------|----------------|
| `TRACKED_DEMAND` | Yes | string | — | TD identifier |
| `PERIOD` | Yes | integer | — | Model period year |
| `time_block` | Yes | string | — | Time block name |
| `td_grid_clean_max_mwh` | Yes | float | MWh/yr | Maximum grid clean MWh credited toward CFE for this block | Derived from regional new clean generation dispatch in prior run. For a strict additionality regime, set to 0 |

---

### 5.16 `tracked_demand_rec_supply.csv` — REC purchasing (Stage 11)

Allows the TD to purchase Renewable Energy Certificates to cover residual CFE gaps
not met by grid draw or onsite generation. The supply cap represents the available
pool of RECs from qualifying sources. Costs are added to the objective.

| Column | Required | Type | Units | Description | Typical Values / Sources |
|--------|----------|------|-------|-------------|--------------------------|
| `TRACKED_DEMAND` | Yes | string | — | TD identifier |
| `PERIOD` | Yes | integer | — | Model period year |
| `time_block` | Yes | string | — | Time block (hourly RECs match the CFE time block) |
| `td_rec_supply_mwh` | Yes | float | MWh/yr | Maximum RECs purchasable in this block. Should reflect newly built regional clean capacity only (additionality). | Compute from `dispatch.csv` new clean generation by region and time block, from a prior no-TD baseline run |
| `td_rec_cost_per_mwh` | No | float | $/MWh | REC price. Added to objective per MWh purchased. Default: 0 | Solar RECs (daytime): $15–35/MWh. Non-solar (wind/storage RECs): $25–50/MWh. Sources: LevelTen Energy market reports, Renewable Choice Energy, voluntary market platforms |

**Design note on additionality:** To enforce additionality (RECs must represent
newly built generation), derive `td_rec_supply_mwh` from the difference between
dispatch in a baseline run (without TDs) and a marginal clean capacity estimate.
A future enhancement will link this cap endogenously to the regional RPS surplus.

---

## 6. Output Files Reference

All output files are written to the scenario output directory after solve.

| File | Stage | Description |
|------|-------|-------------|
| `tracked_demand_dispatch.csv` | 1 | Hourly TDDispatch (MW) and TDGridDraw (MW) per (TD, timepoint) |
| `tracked_demand_annual.csv` | 1 | Annual dispatch MWh, grid draw MWh, and energy requirement per (TD, period) |
| `tracked_demand_cfe_detail.csv` | 2 | CFE block scores using optimization-time clean fractions: target, achieved fraction, clean MWh, total MWh, shortfall MWh, REC MWh |
| `tracked_demand_onsite_build.csv` | 3 | Built capacity per (TD, tech) in MW |
| `tracked_demand_onsite_annual.csv` | 3 | Annual dispatch MWh, net variable cost, net emissions tCO2 per (TD, tech, period) |
| `tracked_demand_onsite_dispatch.csv` | 3 | Hourly dispatch MW per (TD, tech, timepoint) |
| `tracked_demand_storage_build.csv` | 4 | Built power (MW) and energy (MWh) capacity per TD |
| `tracked_demand_storage_dispatch.csv` | 4 | Hourly charge, discharge, and SoC per (TD, timepoint) |
| `tracked_demand_flex_dispatch.csv` | 5 | Hourly flex excess (MW) per (TD, timepoint) |
| `tracked_demand_zone_allocation.csv` | 7 | Zone allocation fraction per (TD, zone, period) |
| `tracked_demand_h2_dispatch.csv` | 8 | Hourly H2 production (kg/hr), delivery (kg/hr), and tank SoC (kg) |
| `tracked_demand_h2_annual.csv` | 8 | Annual H2 production, delivery, and tank capacity per period |
| `grid_clean_fraction_computed.csv` | 10 | Computed hourly grid clean fraction per (zone, timepoint) from actual dispatch |
| `grid_co2_intensity_computed.csv` | 10 | Computed hourly grid CO2 intensity (tCO2/MWh) per (zone, timepoint) |
| `tracked_demand_hourly_emissions.csv` | 10 | Hourly grid + onsite CO2 attributed to each TD (tCO2/hr) |
| `tracked_demand_cfe_computed.csv` | 10 | Post-hoc CFE block scores using actual computed clean fractions. Use these for reporting; `cfe_detail.csv` uses optimization-time clean fractions |
| `tracked_demand_45v_assessment.csv` | 10 | Per-TD 45V tier assessment: lifecycle kg-CO2/kg-H2, tier classification, credit $/kg (electrolyzer TDs only) |

**Note on `cfe_detail.csv` vs `cfe_computed.csv`:** The detail file uses the
exogenous `grid_clean_fraction` from the optimization (which may be from a prior run
or default to 0.5). The computed file re-derives clean fractions from the actual
model dispatch. Use `cfe_computed.csv` for reporting and compliance documentation.
Small numerical discrepancies between the two are expected.

---

## 7. Command-Line Options

The module registers one argument:

```
--td-cfe-region-col COLUMN
```

Column in `hierarchy.csv` used to aggregate zones into regions for computing
`grid_clean_fraction_computed.csv` and related Stage 10 outputs.

| Value | Effect |
|-------|--------|
| `h2ptcreg` (default) | 15-region US aggregation matching IRS 45V deliverability regions |
| `ba` | No aggregation; compute at individual load zone level |
| Any column in `hierarchy.csv` | Custom regional grouping |

For 45V compliance, use `h2ptcreg` (default). For detailed zonal analysis, use `ba`.

---

## 8. Input Aliasing and Scenario Management

All input files support Switch's `--input-alias` flag, allowing scenario variants
without copying the full inputs directory.

**Syntax:**
```
switch solve --inputs-dir in/2035/base --outputs-dir out/2035/variant \
  --input-alias tracked_demands.csv=tracked_demands_elec.csv \
  --input-alias tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_99pct.csv
```

**Common alias patterns used in this project:**

| Purpose | Alias |
|---------|-------|
| Switch to electrolyzer scenario | `tracked_demands.csv=tracked_demands_elec.csv` |
| Apply strict 99% CFE | `tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_99pct.csv` |
| Advisory targets only | `tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_advisory.csv` |
| RECs only, no onsite build | `tracked_demand_onsite_build_caps.csv=tracked_demand_onsite_build_caps_no_build.csv` |
| Firm-clean only (no variable RE) | `tracked_demand_onsite_build_caps.csv=tracked_demand_onsite_build_caps_firm_only.csv` |
| Solar + storage only | `tracked_demand_onsite_build_caps.csv=tracked_demand_onsite_build_caps_solar_storage.csv` |
| Alternate location (CA) | `tracked_demand_candidate_zones.csv=tracked_demand_candidate_zones_p8.csv` |
| No storage | `tracked_demand_storage.csv=tracked_demand_storage_empty.csv` |
| No RECs | `tracked_demand_rec_supply.csv=tracked_demand_rec_supply_empty.csv` |

**Excluding the module entirely:**
```
switch solve --inputs-dir in/2035/base --exclude-module study_modules.tracked_demands
```
Use this for baseline runs without any tracked demands. Do not alias
`tracked_demands.csv` to an empty file — this leaves the TRACKED_DEMANDS set empty
but other files may still reference TD names, causing parameter loading errors.

**Important:** Any custom module file that reads tracked demand inputs using
`pd.read_csv()` directly must wrap the path with `apply_input_aliases()`:

```python
from switch_model.utilities import apply_input_aliases
path = apply_input_aliases(switch_data, os.path.join(inputs_dir, "my_file.csv"))
df = pd.read_csv(path)
```

---

## 9. Test Suite Results

The module was validated against a suite of 17 tests using the
`s4x1_caelp_parclust_zoned` scenario (4 representative days × 12 timepoints,
Mountain West, 2035 planning year). Tests are run via `run_td_tests.ps1`.

### 9.1 Test definitions

| ID | Group | Description | Purpose |
|----|-------|-------------|---------|
| A1_DC_noCFE | A | DC load only, no CFE targets | Regression: module loads with no CFE features |
| A2_DC_CFE_99pct | A | DC, 99%/99% CFE, no RECs, uncapped build | Upper-bound: tests near-100% feasibility |
| A3_DC_CFE_advisory | A | DC, 90%/75% CFE, zero penalty (advisory) | Edge case: zero-penalty targets active but costless |
| B1_DC_RECsOnly | B | DC, 90%/75% CFE, RECs only (all onsite build capped at 0) | Stage 11 isolation: RECs alone must meet targets |
| B2_DC_solar_stor | B | DC, 90%/75% CFE, solar + battery only, no RECs | Variable RE matching: tests non-solar CFE with no firm backing |
| B3_DC_firm_only | B | DC, 90%/75% CFE, geothermal/nuclear/gas-CCS only | Firm clean strategy: 24/7 coverage without variable RE |
| B4_DC_full_uncapped | B | DC, 90%/75% CFE, all techs, no RECs | Portfolio optimization: reveals impact of build caps |
| C1_DC_CA | C | DC in California (p8), realistic caps + RECs | Clean-grid context: tests behavior in high-renewable zone |
| C2_DC_MDA | C | DC in Mid-Atlantic (p125), realistic caps + RECs | Dirty-grid stress: tests heavy investment in fossil-heavy region |
| C3_DC_gridcap | C | DC in p33, binding grid clean credit cap | Stage 9C: forces onsite investment when grid attribution limited |
| C4_DC_multizone | C | DC across p32/p33/p34 | Stage 7 multi-zone siting optimization |
| D1_ELEC_basic | D | Electrolyzer, no CFE, no H2 storage | Stage 1 electrolyzer: basic load tracking |
| D2_ELEC_h2stor | D | Electrolyzer + H2 storage, no CFE | Stage 8: H2 storage decouples production from delivery |
| D3_ELEC_flex | D | Electrolyzer + strong flex (10 MW non-solar cap), 3-zone siting | Stage 5: flex constraint shifts production to solar hours |
| E1_ELEC_45v_target | E | Electrolyzer, 90%/90% symmetric CFE, realistic caps | Stage 10: 45V tier assessment |
| E2_ELEC_full_45v | E | Electrolyzer: flex + H2 + 90%/90% CFE + solar-only caps | Integration test: all electrolyzer features simultaneously |
| F1_MULTI_dc_elec | F | DC + Electrolyzer simultaneously in p33 | Multi-TD regression: verifies no cross-contamination |
| F2_noTD | F | No tracked demands (module excluded) | Emissions and cost baseline |

### 9.2 Results summary

All 17 tests solved to optimality. 15 of 17 pass all CFE compliance checks; 2 tests
(B2 and E2) show CFE shortfall in non-solar hours due to intentionally restrictive
technology caps (see notes below).

| ID | Solve | Solar CFE | Non-Solar CFE | Notes |
|----|-------|-----------|----------------|-------|
| A1_DC_noCFE | PASS | — | — | No targets; DC adds load, system adjusts |
| A2_DC_CFE_99pct | PASS | 100.0% | 100.0% | 99% target met; geothermal dominant. Zero marginal cost in 2035 clean grid |
| A3_DC_CFE_advisory | PASS | 100.0% | 100.0% | Advisory (zero penalty); incidentally 100% clean. CFE tracking only |
| B1_DC_RECsOnly | PASS | 90.0% | 74.9% | RECs (171k + 161k MWh) meet targets with zero onsite investment. RECs cost +$190M vs onsite build |
| B2_DC_solar_stor | PASS | 90.0% | **48.5%** | Solar meets solar-hour target. Battery insufficient for non-solar hours without firm backing. Test design exposes solar-only limitation |
| B3_DC_firm_only | PASS | 100.0% | 100.0% | 30 MW geothermal alone achieves 100%/100% CFE. Firm generation trivially satisfies 24/7 |
| B4_DC_full_uncapped | PASS | 100.0% | 100.0% | Same outcome as B3: geothermal cost-optimal even with all techs available |
| C1_DC_CA | PASS | 99.2% | 91.8% | California's clean grid overshoots targets. 152 MW wind built (not solar, despite CA reputation) |
| C2_DC_MDA | PASS | 90.0% | 75.1% | Mid-Atlantic required balanced mix (74 MW wind + 71 MW solar + 30 MW geo + RECs). Targets barely met |
| C3_DC_gridcap | PASS | 90.0% | 75.0% | Grid clean cap (150k/50k MWh) binding; forced 130 MW wind onsite. Stage 9C confirmed functional |
| C4_DC_multizone | PASS | 96.6% | 97.4% | Optimal siting: 87% load in p33, 13% in p34. Multi-zone diversity exceeds single-zone CFE |
| D1_ELEC_basic | PASS | — | — | Electrolyzer loads correctly; no H2/CFE features active |
| D2_ELEC_h2stor | PASS | — | — | H2 tank built (5,412 kg); production shifted to solar hours (16× ramp). Only +$146M cost (+0.02%) |
| D3_ELEC_flex | PASS | — | — | 10 MW flex cap forced 149 MW wind buildout. Strong production shift to solar hours. +$12.3B (+1.5%) |
| E1_ELEC_45v_target | PASS | 90.0% | 97.3% | 30 MW geothermal drives 45V compliance. 45V assessment written |
| E2_ELEC_full_45v | PASS | **89.5%** | **45.3%** | Solar-only caps + zero wind = non-solar CFE impossible. Test config issue; see note |
| F1_MULTI_dc_elec | PASS | 90.1% | 75.0% | DC and electrolyzer fully independent; no cross-contamination confirmed |
| F2_noTD | PASS | — | — | Clean baseline solve; $802.34B total cost |

**Note on B2 and E2:** Both tests impose CFE targets that cannot be met within the
specified technology constraints. B2 caps onsite build to solar and battery only —
without firm generation, non-solar hours cannot reach 75% CFE. E2 caps wind at zero
and prohibits all firm clean techs while requiring 90% non-solar CFE. Both tests
confirm the module behaves correctly (model pays shortfall penalty rather than
becoming infeasible). The configurations are deliberately stress-testing this boundary.
To use solar+storage effectively, lower the non-solar CFE target to ~50% or add a
small firm generation option.

### 9.3 Key findings from testing

**CFE targets are only costly when they bind.** In Mountain West zones projected at
~90% clean by 2035, strict CFE requirements add near-zero system cost because the
model builds that much clean capacity for other reasons (planning reserve, state RPS).

**Firm generation dominates when available.** In every test where geothermal was
uncapped, the optimizer chose it over solar + storage or RECs. At $280k/MW-yr annualized
cost and 100% CF (8,760 hours/yr), geothermal has an effective LCOE of ~$32/MWh —
below the tested REC price ($25–35/MWh) and wind LCOE (~$40/MWh including VOM).

**RECs cost more than onsite build.** B1 (RECs only) costs +$190M vs B4 (uncapped
onsite). At the tested prices ($25–35/MWh for solar/non-solar RECs), RECs are a
convenience premium. This finding is context-dependent: REC prices in high-demand
markets can be much higher ($50–100/MWh in Northeast US).

**H2 storage provides large flexibility at low cost.** D2 vs D1: the H2 tank
(5,412 kg capacity) allowed 16× production ramp during solar hours, adding only $146M
(0.02%) to system cost. H2 tanks are significantly cheaper than equivalent-duration
battery storage for daily cycling at electrolyzer scale.

**Flex constraints can require major investment.** D3's 10 MW non-solar draw cap
(very restrictive) forced construction of 149 MW wind and cost +$12.3B (+1.5%). Flex
programs should be calibrated carefully: a 50% reduction in non-solar draw (50 MW cap
on a 100 MW electrolyzer) is far less disruptive than a 90% reduction.

**Multi-zone siting improves CFE.** C4 achieved 96.6%/97.4% vs the typical 90%/75%
targets, by spreading load across zones with different solar/wind resources.

---

## 10. Known Limitations

### 10.1 Iterative clean fraction update

The exogenous `grid_clean_fraction` used in the optimization is fixed at input time
and does not respond to changes in the model's own dispatch. If the TD's investment
significantly changes the grid mix (e.g., adds substantial new clean generation), the
clean fraction used in the CFE constraint will be stale. For small TDs relative to
total system capacity, this approximation is acceptable. For large TDs, run two or three
iterations until the computed and assumed clean fractions converge.

### 10.2 Proportional attribution vs. physical matching

The module uses proportional attribution — the TD receives the same clean fraction as
any other load in its zone. It does not physically match production from specific
generators to the TD's load. This is consistent with energy attribute certificate
accounting standards but differs from contractual power purchase agreement structures
where production from a specific generator is directly matched.

### 10.3 Single build period

On-site generation and storage are built once and persist across all model periods.
There is no retirement or re-investment decision. This is consistent with Switch's
treatment of generation investments in single-period models.

### 10.4 No minimum commitment duration for RECs

REC purchases can vary by time block but there is no multi-year contract commitment
modeled. Each period is solved independently with no inter-period REC inventory.

### 10.5 LP relaxation of unit commitment for onsite techs

By default, all onsite tech build decisions are continuous (LP). This means the model
may build fractional MW values (e.g., 13.7 MW geothermal). Set
`td_onsite_tech_min_build_mw` and/or `td_onsite_tech_build_increment_mw` to enforce
discrete build sizes, but note this triggers MIP which significantly increases solve time.

### 10.6 Endogenous REC-from-RPS coupling (planned)

Currently the REC supply cap (`td_rec_supply_mwh`) must be set exogenously. A planned
enhancement would link this cap endogenously to the surplus from regional RPS programs
(ExportURECs − ImportURECs from `rps_regional.py`), enabling the model to
simultaneously optimize RPS compliance and voluntary REC procurement from the
same clean generation assets.

### 10.7 Time block coverage

Every timepoint that participates in CFE accounting must appear in
`tracked_demand_time_blocks.csv`. Timepoints not listed in the file do not contribute
to CFE constraints or reporting, even if the TD draws power during those hours. This
means incomplete time block files can cause CFE fractions to be computed over only a
subset of hours — check that block coverage matches your reporting intent.

### 10.8 Time block file requires manual authoring

The `tracked_demand_time_blocks.csv` file must currently be hand-authored or generated
by a separate script (see `project_td_timeblock_generation.md` for the planned
auto-generation feature). For the current 4×1 sample with 2-hour timepoints, the file
maps hours 8–20 to `solar_hours` and all other hours to `non_solar_hours`.

---

## Appendix A: Quick Start Workflows

### A.1 Minimal data center — load tracking only

The simplest possible configuration adds a fixed-load data center with no clean energy
requirements. This is useful as a baseline before enabling CFE features, or when the
goal is purely to assess system cost impacts of large new industrial loads.

**Files required:**

`tracked_demands.csv`:
```
TRACKED_DEMAND,td_type,td_energy_requirement_mwh_per_year,td_default_min_power_mw,td_default_max_power_mw
my_dc,datacenter,876000,100,100
```

`tracked_demand_candidate_zones.csv`:
```
TRACKED_DEMAND,LOAD_ZONE
my_dc,p33
```

**Solve command:**
```
switch solve --inputs-dir in/2035/my_scenario --outputs-dir out/2035/my_scenario_dc
```

**What you get:** The system builds or dispatches enough capacity to serve a new 100 MW
constant load. Output files `tracked_demand_dispatch.csv` and
`tracked_demand_annual.csv` show hourly and annual totals. `grid_clean_fraction_computed.csv`
gives a baseline clean fraction for iterative refinement.

---

### A.2 Data center with 24/7 CFE — standard configuration

Adds CFE targets and the full onsite technology menu. This is the primary data center
use case.

**Step 1: Prepare files**

`tracked_demands.csv` — as above (100 MW constant load)

`tracked_demand_candidate_zones.csv` — as above

`tracked_demand_time_blocks.csv` — map each timepoint to `solar_hours` (hours 8–20)
or `non_solar_hours` (all other hours)

`tracked_demand_cfe_targets.csv`:
```
TRACKED_DEMAND,PERIOD,time_block,td_cfe_target,td_cfe_shortfall_penalty
my_dc,2035,solar_hours,0.9,200
my_dc,2035,non_solar_hours,0.75,100
```

`tracked_demand_onsite_techs.csv` — copy from the reference file; adjust costs
to match your scenario year

`tracked_demand_onsite_build_caps.csv` — set realistic site-specific caps

`tracked_demand_storage.csv` — one row for the DC if battery storage is allowed

`tracked_demand_rec_supply.csv` — optional; add REC availability if applicable

**Step 2: First run** (using default grid_clean_fraction = 0.5)
```
switch solve --inputs-dir in/2035/my_scenario --outputs-dir out/2035/my_dc_run1
```

**Step 3: Copy computed clean fraction**
```
copy out\2035\my_dc_run1\grid_clean_fraction_computed.csv in\2035\my_scenario\grid_clean_fraction.csv
```

**Step 4: Re-run with accurate clean fraction**
```
switch solve --inputs-dir in/2035/my_scenario --outputs-dir out/2035/my_dc_run2
```

**Step 5: Check outputs**
- `tracked_demand_cfe_computed.csv` — actual CFE fractions vs targets
- `tracked_demand_onsite_build.csv` — what technologies were built and how much
- `tracked_demand_onsite_annual.csv` — dispatch, cost, and emissions per tech

---

### A.3 Green hydrogen electrolyzer — 45V compliance

**Files required in addition to the basics:**

`tracked_demands.csv` — set `td_type = electrolyzer`, `td_default_min_power_mw = 0`
(fully flexible), `td_default_max_power_mw` = rated electrolyzer MW

`tracked_demand_cfe_targets.csv` — symmetric 90%/90% targets for both time blocks:
```
TRACKED_DEMAND,PERIOD,time_block,td_cfe_target,td_cfe_shortfall_penalty
my_elec,2035,solar_hours,0.9,300
my_elec,2035,non_solar_hours,0.9,300
```

`tracked_demand_h2_storage.csv`:
```
TRACKED_DEMAND,td_h2_storage_cost_per_kg_yr,td_electrolyzer_efficiency_kwh_per_kg
my_elec,10,50
```

`tracked_demand_flex_events.csv` (optional) — if a demand response obligation exists:
```
TRACKED_DEMAND,time_block,td_flex_max_grid_draw_mw,td_flex_noncompliance_penalty
my_elec,non_solar_hours,20,1000
```

**Key outputs to check:**
- `tracked_demand_45v_assessment.csv` — 45V tier and credit $/kg. This file is only
  written when H2 storage is active (Stage 8 must be enabled).
- `tracked_demand_cfe_computed.csv` — verify 90%/90% achieved
- `tracked_demand_h2_dispatch.csv` — H2 production profile and tank SoC

**45V tier thresholds** (IRS Notice 2023-29):

| Lifecycle CO2 (kg-CO2/kg-H2) | Tier | Credit ($/kg) |
|------------------------------|------|---------------|
| ≤ 0.45 | 1 | $3.00 |
| 0.45 – 1.50 | 2 | $1.00 |
| 1.50 – 2.50 | 3 | $0.75 |
| 2.50 – 4.00 | 4 | $0.60 |
| > 4.00 | — | Not eligible |

Example result from E2 test (low CFE scenario): 3.16 kg-CO2/kg-H2 → Tier 4 ($0.60/kg).
Example result from E1 test (90%/97% CFE): would produce Tier 1–2 depending on grid
emissions intensity.

---

### A.4 Location selection study

To let the model choose the optimal grid interconnection zone:

`tracked_demands.csv` — set `td_location_fixed = 0`

`tracked_demand_candidate_zones.csv` — list all candidate zones:
```
TRACKED_DEMAND,LOAD_ZONE
my_dc,p32
my_dc,p33
my_dc,p34
```

**Key output:** `tracked_demand_zone_allocation.csv` — shows what fraction of the DC's
interconnect capacity (`td_grid_interconnect_mw`) is allocated to each zone. A value
near 1.0 in one zone means the model effectively chose that zone; a split allocation
indicates the model benefits from distributing load across multiple zones.

**Note:** When `td_location_fixed = 0`, CFE accounting uses the zone receiving the
most load (`_td_zone()` returns the single-zone allocation for fixed TDs). Multi-zone
CFE accounting for flexible TDs uses the zone-specific `grid_clean_fraction` weighted
by draw. Ensure `grid_clean_fraction.csv` covers all candidate zones.

---

### A.5 Scenario comparison via input aliasing

To compare multiple scenarios without duplicating the inputs directory, use
`--input-alias`. Create a base inputs directory with the most common configuration,
then override specific files per scenario.

**Example: compare RECs vs onsite build vs no-CFE:**
```powershell
# Scenario 1: no CFE
switch solve --inputs-dir in/2035/base --outputs-dir out/2035/noCFE `
  --input-alias tracked_demand_cfe_targets.csv=tracked_demand_cfe_targets_empty.csv

# Scenario 2: CFE met by onsite build
switch solve --inputs-dir in/2035/base --outputs-dir out/2035/CFE_onsite `
  --input-alias tracked_demand_rec_supply.csv=tracked_demand_rec_supply_empty.csv

# Scenario 3: CFE met by RECs only
switch solve --inputs-dir in/2035/base --outputs-dir out/2035/CFE_RECsOnly `
  --input-alias tracked_demand_onsite_build_caps.csv=tracked_demand_onsite_build_caps_no_build.csv
```

---

## Appendix B: Troubleshooting

### B.1 "Index is not valid" error on load

**Symptom:**
```
RuntimeError: Failed to set value for param=td_min_power_mw,
index=('my_dc', 20352120600) ... Index is not valid
```

**Cause:** A file other than `tracked_demands.csv` (e.g., `tracked_demand_dispatch_bounds.csv`)
references a TD name, but the TD no longer appears in `tracked_demands.csv` or has been
aliased away. The TD_TIMEPOINTS set is empty, so any index referencing that TD is invalid.

**Fix:** If running a scenario with no tracked demands, use `--exclude-module
study_modules.tracked_demands` rather than aliasing `tracked_demands.csv` to an empty
file. All other input files will then be ignored.

---

### B.2 CFE targets not affecting the objective

**Symptom:** CFE constraint shows in the model but shortfall is zero even with
`td_cfe_shortfall_penalty = 0`.

**Cause:** Zero penalty means the constraint is advisory — the shortfall variable is
free and costless. The optimization has no incentive to reduce it.

**Fix:** Set `td_cfe_shortfall_penalty` to a value above the LCOE of the cheapest
available clean generation option. For Mountain West 2035, geothermal LCOE is ~$32/MWh;
a penalty of $50/MWh will make the target binding.

---

### B.3 cfe_computed.csv shows shortfall but cfe_detail.csv shows compliance

**Symptom:** `tracked_demand_cfe_computed.csv` shows a small CFE shortfall (e.g.,
0.001 MWh), but `tracked_demand_cfe_detail.csv` shows CFE = 0.

**Cause:** The two files use different clean fraction inputs. `cfe_detail.csv` uses
the exogenous `grid_clean_fraction` from the optimization; `cfe_computed.csv`
re-derives clean fractions from actual dispatch. Small numerical differences between
the two are expected due to floating-point rounding.

**Fix:** This is expected behavior. Use `cfe_computed.csv` for reporting. If the
computed shortfall is very small (< 1 MWh/yr), it represents a rounding artifact and
can be treated as compliance.

---

### B.4 grid_clean_fraction_computed.csv shows unexpected values

**Symptom:** Computed clean fractions are much lower than expected (e.g., 5% in a
zone known to be largely renewable).

**Cause:** The `--td-cfe-region-col` column used for aggregation does not match the
hierarchy. Check that the column name exists in `hierarchy.csv` and that the load zone
is assigned to the correct region.

**Fix:**
```powershell
# Check hierarchy.csv for correct column
Get-Content path\to\hierarchy.csv | Select-Object -First 5
# Re-run with explicit column
switch solve ... --td-cfe-region-col h2ptcreg
```

If the clean fraction is low because the region genuinely has low clean generation,
this is correct. The computed value reflects the actual model dispatch, not nameplate
clean capacity.

---

### B.5 No tracked_demand_45v_assessment.csv output

**Symptom:** The 45V assessment file is not written even though `td_type = electrolyzer`.

**Cause:** The 45V assessment requires the H2 storage feature (Stage 8) to be active.
The file is only written when `m.TD_H2_STORAGE` is non-empty.

**Fix:** Add a `tracked_demand_h2_storage.csv` file with at least the required columns
(`TRACKED_DEMAND`, `td_h2_storage_cost_per_kg_yr`). Even a small tank cost is
sufficient to activate Stage 8 and enable the assessment.

---

### B.6 --input-alias not applied to a custom file read

**Symptom:** A scenario alias is correctly applied to standard tracked demand files
but ignored when reading a file inside a custom script or post-processing step.

**Cause:** Files read with raw `pd.read_csv()` or `open()` bypass the Switch alias
resolution system. Only files loaded via `switch_data.load_aug()` with
`apply_input_aliases()` wrapping the path will honor aliases.

**Fix:** Wrap all file paths with `apply_input_aliases()`:
```python
from switch_model.utilities import apply_input_aliases
path = apply_input_aliases(switch_data, os.path.join(inputs_dir, "my_file.csv"))
df = pd.read_csv(path)
```

---

### B.7 Solve time increased dramatically after adding a tracked demand

**Symptom:** Adding `tracked_demand_onsite_techs.csv` increased solve time from
~5 minutes to >1 hour.

**Cause:** If any tech has `td_onsite_tech_min_build_mw > 0` or
`td_onsite_tech_build_increment_mw > 0`, the model adds binary or integer variables
(MIP), which is exponentially harder to solve than LP.

**Fix:** Set `td_onsite_tech_min_build_mw = 0` and `td_onsite_tech_build_increment_mw = 0`
for all techs to keep the problem as LP. Accept that build sizes may be fractional.
Only enable MIP sizing when discrete build constraints are truly necessary for the
analysis.

---

### B.8 `TD_CANDIDATE_ZONES` empty / model skips CFE constraint

**Symptom:** No CFE constraint is generated even though targets file is present.

**Cause:** `_cfe_constraint_rule` calls `_td_zone(m, td)` to get the TD's zone.
For location-fixed TDs this returns the single candidate zone. If
`tracked_demand_candidate_zones.csv` is absent or the TD has no rows, `_td_zone()`
returns `None` and the constraint is skipped with `Constraint.Skip`.

**Fix:** Ensure `tracked_demand_candidate_zones.csv` has at least one row for every
TD defined in `tracked_demands.csv`.

---

## Appendix C: Test Runner Reference

Tests are defined in `switch/run_td_tests.ps1` and cover six scenario groups.

### C.1 Usage

```powershell
# Run all tests
.\run_td_tests.ps1

# Run a single test group
.\run_td_tests.ps1 -Group A

# Run a single named test
.\run_td_tests.ps1 -Test B3_DC_firm_only

# Skip tests that already have output
.\run_td_tests.ps1 -SkipExisting

# Check results without re-running
.\run_td_tests.ps1 -CheckOnly
```

Must be run from `Switch-USA-PG-ReEDS\switch\`.

### C.2 Test groups

| Group | Focus | Tests |
|-------|-------|-------|
| A | Data center: CFE target stringency | noCFE, 99%, advisory |
| B | Data center: clean supply strategy | RECs only, solar+storage, firm-only, full-uncapped |
| C | Data center: geography and grid constraints | California, Mid-Atlantic, grid clean cap, multi-zone |
| D | Electrolyzer: basic configurations | basic, H2 storage, flex constraint |
| E | Electrolyzer: 45V compliance | symmetric 90% target, full integration |
| F | Multi-TD and regression | DC+electrolyzer simultaneous, no-TD baseline |

### C.3 CFE compliance check

After each solve the runner calls `Check-CFE`, which reads
`tracked_demand_cfe_computed.csv` and compares every row's `cfe_computed_fraction`
against either the test's declared `$CFETgt` (overrides all rows) or the row's own
`cfe_target` column (used when `$CFETgt` is null). A tolerance of 0.002 (0.2
percentage points) is applied to absorb floating-point rounding.

### C.4 Adding a new test

1. Add a `[PSCustomObject]` entry to the `$Tests` array in `run_td_tests.ps1`
2. Set `ID` (unique), `Grp` (single letter), `Desc`, `Purpose`, `Aliases` (array of
   `$AZ`-style fragments), and optionally `ExcludeModule` and `CFETgt`
3. Create any new alias target files in the inputs directory
4. Run `.\run_td_tests.ps1 -Test MY_NEW_ID` to validate before running the full suite

### C.5 Alias fragment variables

The script defines reusable alias strings. Key fragments:

| Variable | Alias | Purpose |
|----------|-------|---------|
| `$AZ` | gen_group_load_ratio → max1.30 | Gen-zone ratio cap (required for all TD runs) |
| `$AS` | storage → 1hr battery | Enable 1-hour behind-the-meter storage |
| `$ANS` | storage → empty | Disable storage |
| `$AH` | h2_storage → empty | Disable H2 storage |
| `$AP` | predetermined → empty | No predetermined builds |
| `$AGO` | grid_clean_caps → empty | No grid clean cap |
| `$ADB` | dispatch_bounds → empty | No dispatch bound overrides |
| `$CFE_STD` | cfe_targets → standard (90/75) | Standard CFE targets |
| `$CFE_NONE` | cfe_targets → empty | No CFE targets |
| `$CFE_99` | cfe_targets → 99pct | Near-100% CFE |
| `$CFE_ADV` | cfe_targets → advisory | Advisory (zero penalty) |
| `$CFE_45V` | cfe_targets → elec_45v | 45V symmetric 90%/90% |
| `$REC_STD` | rec_supply → standard | RECs available at $25–35/MWh |
| `$REC_NONE` | rec_supply → empty | No RECs |
| `$CAP_REAL` | build_caps → dc_realistic | Realistic site constraints |
| `$CAP_NONE` | build_caps → empty | Uncapped (no constraints) |
| `$CAP_NB` | build_caps → no_build | All caps at 0 (RECs-only test) |
| `$CAP_SS` | build_caps → solar_storage | Solar + diesel only |
| `$CAP_FIRM` | build_caps → firm_only | Geothermal/nuclear/gas-CCS only |
| `$TD_ELEC` | tracked_demands → electrolyzer | Switch to electrolyzer TD |
| `$TD_MULTI` | tracked_demands → multi | DC + electrolyzer simultaneously |
| `$CZ_P8` | candidate_zones → p8 | California location |
| `$CZ_P125` | candidate_zones → p125 | Mid-Atlantic location |
| `$CZ_FLEX` | candidate_zones → flex | 3-zone MW flexible siting |
| `$GC_ON` | grid_clean_caps → binding | Enable grid clean cap constraint |
| `$H2_ELEC` | h2_storage → electrolyzer | Enable H2 buffer tank |
| `$FX_ELEC` | flex_events → electrolyzer | Enable 10 MW non-solar cap |
