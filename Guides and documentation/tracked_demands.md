# Tracked Demands Module — User Guide

**Module:** `switch/study_modules/tracked_demands.py` (opt-in; not in `switch/modules.txt`)  
**Branch:** `tom/tracked-demands-v2` (from `tom/s0-v3.1` at 9d68751; CHANGES §107)  
**Switch version:** 2.0.9 (Pyomo 6.9.1, the newest Switch 2.0.9 allows)  
**Last updated:** 2026-10-09 (step 1: port and audit). Sections 1–10 and the appendices are the May 2026 guide,
corrected where the port changed something; sections 11–14 are new.

> **Status (Oct 2026).** Ported onto the S0 v3.1 line and tested on the toy only. Not yet used in a real case.
> Section 12 lists the interactions with today's model and the gaps to close before the first real case; four of
> them are pinned by `xfail(strict=True)` tests in `tracked_demands/tests/test_td_matching.py`.

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
11. [Loading the Module (Opt-in)](#11-loading-the-module-opt-in)
12. [Interactions with Today's Model (Oct 2026 Audit)](#12-interactions-with-todays-model-oct-2026-audit)
13. [Interfaces](#13-interfaces)
14. [Tests](#14-tests)
15. [Step 2: On-Site Generators, Cap Coverage, Matching (§108)](#15-step-2-on-site-generators-cap-coverage-matching-108)

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
to comply with the IRA 45V production tax credit. It is opt-in: it is not in
`switch/modules.txt` and is loaded per scenario (section 11). Unloaded, every case builds and solves byte for byte as
before (`tracked_demands/tests/test_td_unloaded.py`). Loaded with no `tracked_demands.csv`, it adds nothing to the
plan or the objective.

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

**Baseline runs:** the module is not in `switch/modules.txt`, so a scenario line without
`--include-module study_modules.tracked_demands` is the baseline (May 2026: the module was in modules.txt and
baselines used `--exclude-module`). Do not alias
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

## 9. Test Suite Results (May 2026, full case; historical)

These are the May 2026 results on the old base (`s4x1_caelp_parclust_zoned`, a case that no longer builds on the S0
v3.1 line). The runner is kept as `tracked_demands/scripts/legacy/run_td_tests.ps1`. The current tests are the toy
suite in section 14, which ports each of these groups.

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

(Oct 2026: `project_td_timeblock_generation.md` is not in the repo. On S0 cases, write blocks from `timepoints.csv`
and leave the zero-weight stress timepoints out; see 12.4.)

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

---

## 11. Loading the Module (Opt-in)

The module is not in `switch/modules.txt` and must stay out of it. A case uses it in one of two ways:

- **On the scenario line:** `--include-module study_modules.tracked_demands` (plus `--td-cfe-region-col` if not
  `h2ptcreg`). The input files go in the case's inputs folder or are selected with `--input-aliases`.
- **For S0 production cases:** list it in the case's `s0_production.extra_modules` (a scenario-axis value in
  `pg/settings/scenario_management.yml`, not the file default). `s0_workflow/production.py:scenario_options()`
  turns that list into `--include-module` options on the stage lines. The case build doesn't yet write the
  tracked-demand inputs; that is step 2 (section 12.6).

Unloaded, the input files are ignored: a toy with every tracked-demand file present and the module unloaded writes
the same bytes in every output file as the toy without them (`tracked_demands/tests/test_td_unloaded.py`).

## 12. Interactions with Today's Model (Oct 2026 Audit)

> **Status after §108 (step 2, Oct 2026).** Closed so far:
> - on-site fuel plant and carbon caps (12.2 carbon row, 12.5): sections 15.1–15.2;
> - the hourly double credit through the battery, the timestamp keying of the computed clean fraction, and the
>   stress-hour CO₂ intensity (12.4): section 15.4;
> - stage-chain carry-forward (12.3): section 15.5;
> - the tracked load on stress days and in the PRM requirement (12.2 prm row, 12.4): section 15.6;
> - gen_zone_ratio (12.2): section 15.7; grid connection cost, size and headroom (12.2 headroom row): section 15.8.
>
> The rest of this section is the step-1 audit as written.

Line numbers are for `tom/tracked-demands-v2` at the step-1 commit. "TD" = tracked demand. Items marked
**(gap)** are open; four are pinned by strict xfail tests.

### 12.1 Versions

- Solves on Switch 2.0.9 with Pyomo 6.9.1 (Switch's upper pin) and HiGHS, under Python 3.11 / pandas 3.0.6 and
  Python 3.10 / pandas 1.4.4 / numpy 1.23.5. No deprecation warnings from the module.
- Fixed in the port: `_compute_grid_metrics` read `dispatch.csv`'s `is_storage` column, which Switch writes only
  when a storage module is loaded (post-solve `KeyError` otherwise; `tracked_demands.py:2129`).
- `dependencies` (`tracked_demands.py:45`) names `switch_model.generators.core.dispatch`; S0 loads the patched
  `study_modules.generators_core_dispatch`. Switch 2.0.9 doesn't check `dependencies`, so this is cosmetic.
- Light stress days (§69, `LIGHT_TPS`): the module's terms there all carry zero weight, so its costs are unaffected.
  The TD's grid draw stays in the energy balance on those hours, as the light formulation keeps all withdrawals.

### 12.2 Policy modules

| Module | Does the TD's grid load count? | Does on-site generation count? |
|---|---|---|
| `gen_tax_credits` (45Y/48E, 45V) | n/a | **No.** Credits are per `GENERATION_PROJECT` (`gen_tax_credits.py:51`); on-site techs are private variables. The module's own `td_onsite_tech_subsidy` (`tracked_demands.py:741`, netted into the variable cost at 956) must be set by hand to match `tax_credit_values`. **(gap)** 45V is post-solve only (`_write_45v_assessment`, 2373; tiers at 2358 are right), not in the objective; its "lifecycle" intensity is combustion CO₂ only (regional average grid intensity × grid draw, plus on-site), with no upstream methane and no EAC rules; no start-of-construction date. |
| `carbon_policies_regional` | Yes: the grid generation serving it is in the cap (`carbon_policies_regional.py:238`). | **No, whatever the setting.** The cap sums `DispatchEmissions` of fuel-based generators; on-site dispatch is private (`tracked_demands.py:297`, emissions from a tCO₂/MWh param at 711). `td_onsite_emissions_in_system_cap` (245, default 0) is loaded (1566) and used nowhere. **(gap)** |
| `prm_regional` | **No.** The requirement is `PrmServedLoad` (`prm_regional.py:450`): `Distributed_Power_Withdrawals` (with local_td) or `zone_demand_mw` + DR. The TD registers at the central node (`Zone_Power_Withdrawals`, `tracked_demands.py:539`). It is also left out of `prm_region_peak_mw` (`prm_regional.py:342`), the import-cap basis. **(gap)** | No reserve credit (not generators). |
| `build_rate` | n/a | No: groups are subsets of `GENERATION_PROJECTS` (`build_rate.py:78`, gas-turbine cap 165). On-site gas engines/turbines are outside the turbine cap. |
| `interconnection_headroom` | **No:** the TD's grid connection (`td_grid_interconnect_mw`) uses no headroom and pays no network upgrade. | No (only `NEW_GEN_BLD_YRS` of generators, `interconnection_headroom.py:248`). Behind the meter, which is arguably right. |
| `gen_zone_ratio` | **Asymmetric:** load is `zone_demand_mw` only (`gen_zone_ratio.py:113`, peak share 167/181, groups 268), but the generation serving the TD counts (105). Hosting a TD raises the zone's ratio and can bind a max-ratio row. **(gap)** | No. |

### 12.3 Stage chains (mode A) and stage reuse

- **Nothing carries forward today.** `prepare_next_stage.chain_stage` (`prepare_next_stage.py:115`) knows no TD file.
  On-site capacity (`BuildOnsiteTech`, `tracked_demands.py:820`), battery MW/MWh (1013), H₂ tank (1271) and the
  siting share of location-flexible TDs (`TDZoneAllocation`, 1124) are single, un-vintaged decisions re-made in
  every stage, each paying its annualised cost in every period (946, 1090, 1339). A 2030 stage can drop 2028's
  on-site plant or move a flexible TD. **(gap)**
- **Must carry:** on-site MW by (TD, tech), battery MW and MWh, H₂ tank kg, as existing capacity that later stages
  can add to; the zone of a flexible TD after its first stage; optionally the computed grid clean fraction as the
  next stage's starting value (needs 12.4's keying fix and a timepoint map, since stages sample different days).
  `tracked_demand_onsite_predetermined.csv` (936) is a partial stopgap: it fixes capacity exactly (no expansion)
  and covers on-site techs only. Windows (mode B, `commit_period`) need builds indexed by period.
- **Stage reuse (`s0_workflow/chain_reuse.py`):** inputs are fingerprinted file by file over the whole stage inputs
  folder, so `tracked_demand_*.csv` and `grid_clean_fraction.csv` are covered with no change; the
  `--include-module` and `--td-cfe-region-col` options are compared, so TD and non-TD lines never match. TD chained
  files written by `prepare_next_stage` from output CSVs would not be `MODEL_DEPENDENT` (line 65). **Code check:**
  `switch/study_modules/` is in `CODE_PATHS` and `MODEL_CODE_PATHS` (lines 54, 62), so adding this module fails the
  check against any reference recorded at a head before it, also for lines that don't load it. Record references on
  this branch, or narrow the check to the modules a line loads (shared change; not made).

### 12.4 Time sampling

Everything the module sums over a year (energy requirement 403, on-site hours cap 907, CFE blocks 595, costs) is
weighted by `tp_weight_in_year`, as in Switch. On zero-weight stress days:

- **CFE blocks:** stress hours add nothing to either side (tested), which is right for annual-MWh matching. A block
  made only of stress hours is vacuous; time-block files for S0 cases should leave stress timepoints out.
- **(gap) The TD load can vanish.** The annual requirement doesn't see stress hours, so a TD with
  `td_default_min_power_mw` 0 drops to 0 there at no cost; with prm_regional also leaving it out (12.2) the stress
  test doesn't see the TD at all. Toy: `TDDispatch` 0 on both stress timepoints.
- The on-site annual-hours cap doesn't see stress hours either: backup runs there are unlimited and free. Harmless
  until the TD must be served on stress days.
- **(gap)** Post-solve CO₂ intensity reads 0 on stress hours (`tracked_demands.py:2136`: weighted emissions ÷ weight).
- Storage state of charge is cyclic within each timeseries (`tp_previous`, 1072); stress days are their own series.

Hourly matching accounting (**gaps**):

- On-site clean output that charges the battery is credited when generated, and the discharge is credited again at
  the grid clean fraction (`_cfe_constraint_rule`, 595–632). Toy hourly blocks reach 114% of consumption.
- `grid_clean_fraction_computed.csv` writes timestamps in its `TIMEPOINT` column (2217); `grid_clean_fraction.csv`
  is keyed by timepoint id, so the documented iteration (10.1) can't read it back.
- A location-flexible TD's CFE uses one zone's clean fraction (`_td_zone`, 71: the last listed candidate zone).

### 12.5 On-site gas and Switch's fuel accounting

**Today it does not go through standard fuel accounting.** On-site gas is `DispatchOnsiteTech` (297) priced by
`td_onsite_tech_fuel_cost` in $/MWh of output (704), with emissions from `td_onsite_tech_emissions` in tCO₂/MWh
(711). There is no fuel, heat rate or `GenFuelUseRate`. The gas-network design (`tom/gas-network`,
`gas_network/docs/design_note_step1.md` §e) defines power burn as Σ `GenFuelUseRate[g, t, gas]` ×
`tp_weight_in_year[t]` by zone, so it would not see on-site burn, upstream methane wouldn't cover it, and when that
module removes gas from `fuel_cost.csv` for its zones, on-site gas would keep its fixed $/MWh price.

**Proposed smallest change (step 2, not made):** fuel-burning on-site techs (gas engines, turbines, fuel cells on
gas, diesel backup) become ordinary generation projects in the TD's zone: rows in `gen_info.csv` /
`gen_build_costs.csv` written by the case build (fuel, heat rate, costs), linked to their TD by a new file
`tracked_demand_onsite_gens.csv` (`TRACKED_DEMAND, GENERATION_PROJECT`). In the module (about 40 lines):

1. the TD's energy balance adds Σ `DispatchGen[g, t]` of its linked generators;
2. the TD's zone withdrawal becomes `TDGridDraw` + Σ `DispatchGen` of its linked generators (they inject through
   the standard dispatch module, so the zone nets to the grid draw);
3. optional no-export: Σ `DispatchGen[g, t]` ≤ `TDDispatch` + battery charge;
4. linked fuel-based generators count as not clean in CFE blocks.

Fuel use, fuel cost (`fuel_costs_simple` or markets), emissions, carbon caps and the gas network then come from
standard accounting. `td_onsite_emissions_in_system_cap` would become an exclusion list if a cap should leave
on-site plant out. Per-generator settings to decide: `gen_ic_weight` 0 (behind the meter), `prm_class`/credit,
membership of build-rate groups (gas-turbine cap), and whether the TD's gross load counts as zone load in
gen_zone_ratio. Private `DispatchOnsiteTech` stays for non-fuel techs (on-site solar, wind, geothermal).

### 12.6 Before the first real case

In order: (1) the stress-day load rule and the TD in the PRM requirement (12.2, 12.4); (2) on-site fuel techs as
generators (12.5), which also settles the carbon-cap question; (3) carry-forward in `prepare_next_stage` (12.3);
(4) the hourly-credit and keying fixes (12.4); (5) gen_zone_ratio load (12.2); (6) a case-build writer for the
tracked-demand inputs from settings (time blocks without stress timepoints, energy-source names such as `sun`);
(7) 45Y/45V values from `tax_credit_values` rather than hand-set subsidies.

## 13. Interfaces

What other modules read from this one, and what it reads. Model components are available to any module loaded
after it; files are written in `post_solve`.

**Read by a gas-network module** (after the 12.5 change; nothing private needed):

| Item | Index | Units | Where |
|---|---|---|---|
| `GenFuelUseRate` of on-site generators | (generator, timepoint, fuel) | MMBtu/h | Switch's standard component; zone = `gen_load_zone` (the TD's zone), weight = `tp_weight_in_year` |
| `TD_ONSITE_GENS` (proposed) | (TD, generator) | — | the link set from `tracked_demand_onsite_gens.csv`, to report TD burn separately |
| `tracked_demand_onsite_fuel_use.csv` (proposed) | TD, generator, load zone, timepoint, fuel | MMBtu/h and MMBtu/yr | post-solve report of the same numbers |

Until the change, the only on-site fuel information is `tracked_demand_onsite_dispatch.csv` (MW by TD, tech,
timepoint) and `tracked_demand_onsite_annual.csv` (MWh, $ and tCO₂ by TD, tech, period), with no fuel quantity.

**Read by other power modules:**

| Item | Index | Units | Meaning |
|---|---|---|---|
| `TDGridDrawByZone` | (load zone, timepoint) | MW | the TD's net draw, in `Zone_Power_Withdrawals` (central node) |
| `TDDispatch`, `TDGridDraw` | (TD, timepoint) | MW | gross consumption and grid draw |
| `TD_CANDIDATE_ZONES`, `TDZoneAllocation` | (TD, zone[, period]) | share | siting |
| `tracked_demand_dispatch.csv`, `tracked_demand_annual.csv` | — | MW, MWh | as above, written per run |

**Reads:** `tp_weight_in_year`, `tp_previous`, `gen_max_capacity_factor` (zone average by energy source, for
`td_onsite_tech_cf_source`), and after the solve `dispatch.csv` and `hierarchy.csv` (column `--td-cfe-region-col`).

## 14. Tests

`tracked_demands/tests/` (pytest; toy solves with HiGHS on Switch's 3-zone example; set `SWITCH_SRC` if Switch's
source isn't at `/opt/switch-src`). The int64 guard from `s0_workflow/tests/conftest.py` is on.

- `test_td_toy_suite.py`: the legacy groups A–F on the toy, plus dispatch bounds, predetermined builds, MIP sizes,
  the soft grid cap and annual grid caps.
- `test_td_matching.py`: a data centre solving with hourly matching on (one block per timepoint), annual and off;
  zero-weight stress hours outside CFE blocks; clean through the battery counted once; a second pass reading the
  computed clean fraction; no xfails left (§108 closed the last).
- `test_td_onsite_gens.py` (§108): linked on-site generators and carbon-cap coverage, including New York's 15 MWe rule.
- `test_td_chain.py` (§108 (c)): two-stage chain carry-forward and the stage-reuse handover.
- `test_td_stress_prm.py` (§108 (d)): stress-day modes, the PRM requirement and region peak, load order.
- `test_td_unloaded.py`: the opt-in proof (section 11): only new files and notes change against the base; nothing
  loads the module by default; unloaded output bytes identical; loaded with no TD, the same plan and objective.

```
cd Switch-USA-EDF && SWITCH_SRC=/opt/switch-src pytest -q tracked_demands/tests
```

## 15. Step 2: On-Site Generators, Cap Coverage, Matching (§108)

Tom's decisions of 11 Oct 2026 are the dated section at the end of the decisions register,
`tracked_demands/docs/design/design_decisions.md`, which came with the April design documents from
`hk/td-design-docs`. Step 2 runs (a)–(g); this section grows with it.

### 15.1 On-site fuel plant as ordinary generators

Fuel-burning on-site plant (gas engines, turbines, fuel cells on gas, diesel backup) is an ordinary row of
`gen_info.csv` / `gen_build_costs.csv` in the TD's zone, linked by:

```
tracked_demand_onsite_gens.csv
TRACKED_DEMAND,GENERATION_PROJECT,td_onsite_prime_mover,td_onsite_unit_mw,td_onsite_rated_mw
DC1,N-DC1-recip,recip,4.5,.
```

| Column | Default | Meaning |
|---|---|---|
| `td_onsite_prime_mover` | `other` | `turbine`, `recip`, `fuel_cell`, `boiler`, `other` |
| `td_onsite_gtc_class` | from the prime mover | gas-turbine cap category: `combined_cycle`, `combustion_turbine`, `aeroderivative`, `reciprocating_engine` or `none`. Not given: `reciprocating_engine` for a recip, `none` for a fuel cell, boiler or other; a turbine must give it. |
| `td_onsite_unit_mw` | `gen_unit_size`, else rated | unit nameplate for per-unit applicability tests (carbon-cap thresholds with `td_cap_threshold_basis: unit`, the NY 15 MWe rule) |
| `td_onsite_rated_mw` | `gen_capacity_limit_mw` | rated MW for site tests and potential to emit |

`tracked_demands.csv` gains `td_export_limit_share`:

| Value | Meaning |
|---|---|
| `.` (default) | no limit |
| 0 | no export |
| s | annual export ≤ s × gross output of the linked generators (RGGI-style limited export: 0.1) |

In the model, with G = grid draw, X = export (only TDs with linked generators and a non-zero share) and Gen = the
linked generators' output:

- TD balance: `TDDispatch = G − X + Gen + private on-site + discharge − charge`;
- zone withdrawal: `G − X + Gen`. The generators inject `Gen` through the standard dispatch module, so the zone
  nets to `G − X`;
- `G ≤ td_grid_interconnect_mw` and `X ≤ td_grid_interconnect_mw`.

Fuel use (`GenFuelUseRate`), fuel cost (`fuel_costs_simple` or fuel markets), emissions (`DispatchEmissions`) and so
carbon caps and a gas network all come from Switch's standard accounting. Linked generators are never clean in
matching.

**Settings the run checks (`pre_solve`), from Tom's decisions:**

| Module | Required for each linked generator | Where it's set |
|---|---|---|
| prm_regional | `prm_credit = none` (no reserve credit) | `prm_gen_credit.csv` |
| interconnection_headroom | interconnection weight 0 (assumption: behind the meter, no headroom) | `ic_weights.csv` row for its `gen_tech` |
| build_rate (gas-turbine cap on) | in `gas_turbine_cap_gens.csv` exactly when its category is in the case's cap coverage, with that `gtc_class` | `gas_turbine_cap_gens.csv`; coverage in `tracked_demand_gtc_coverage.csv` (else the classes of the case's own generators in the cap) |

A run that breaks one stops with the fix to make. The case-build writer (15.9) writes these rows.

**Gas-turbine cap membership follows the case's cap coverage** (refinement of D3, 12 Oct 2026; it replaces "turbines
count, reciprocating engines don't"). Each linked generator has a category:

| Technology (writer) | Category |
|---|---|
| `frame_ct` | `combustion_turbine` |
| `cc` | `combined_cycle` |
| `aero` | `aeroderivative` |
| `industrial_ct` (small industrial CTs, Titan 130 class) | `aeroderivative` |
| `recip` | `reciprocating_engine` |
| `diesel`, `fuel_cell` | none (never in the cap) |

It joins the cap only if that category is in the case's coverage, i.e. the list `build_rate/brc/turbine_cap.py`
(`gtc_settings`) used when it built the case. Which list that is depends on the case's form, the `gtc_form` column of
the case's `gas_turbine_cap_params.csv`:

| Form (`gtc_form`) | Coverage read from (`build_rate.gas_turbine_cap` in `pg/settings/build_rate.yml`) | Value there | Covered linked generator's weight |
|---|---|---|---|
| `cumulative_in_service` | `coverage` | `[combined_cycle, combustion_turbine]` | 1; CC at `cc_turbine_share` with `cc_accounting: turbine_share` |
| `new_additions_per_period` | `coverage` | `[combined_cycle, combustion_turbine]` | as above |
| `cumulative_additions` | `allowance_coverage` when set, else `coverage` | `[combined_cycle, combustion_turbine, aeroderivative]` | `class_weights` × `capacity_basis.new_build_to_allowance`: CT and aeroderivative 1.090, CC 0.65 × 1.049 |

S0 v3.1 production cases (S0prod_A and the rest) have form `cumulative_additions`. pg_to_switch writes that form
when the scenario row's `s0_production.gas_turbine_cap.form` is `allowance`. `allowance` is the name of the
production setting; the form the case carries, and the one the writer and the run check read, is
`cumulative_additions`. The legacy-form cases and the plain `build_rate.yml` default (`cumulative_in_service`) use
`coverage`.

So on-site aeroderivatives and industrial CTs are inside the cap in S0 production cases and outside it under the
other two forms. Frame CTs and CCs are inside it under all three. The case-build writer reads the form from the case
and the lists from `build_rate.yml` (15.9).

**Unit size: `td_onsite_unit_mw`, not `gen_unit_size`.** Cap-coverage tests take a linked generator's unit size
from `td_onsite_unit_mw` in `tracked_demand_onsite_gens.csv`. Only if that is blank do they fall back to
`gen_unit_size`, and then to the rated MW. The case-build writer leaves `gen_unit_size` blank and writes the spec's
`unit_mw` into `td_onsite_unit_mw`. The reason: a `gen_unit_size` puts the generator in Switch's
`DISCRETELY_SIZED_GENS`, so it is built in whole units (an integer `BuildUnits`), and the stage becomes a MILP. The
unit size is needed only for the applicability tests, which are fixed before the solve, so the build stays continuous.

Private on-site techs (`tracked_demand_onsite_techs.csv`) stay for non-fuel plant (solar, wind, geothermal); one with
a fuel cost or emissions prints a warning.

### 15.2 Carbon-cap coverage of on-site emissions

Linked generators are ordinary generators, so `carbon_policies_regional` counts them in every programme covering
their zone. `tracked_demand_cap_coverage.csv` sets each programme's rule:

| Column | Default | Meaning |
|---|---|---|
| `CO2_PROGRAM` | — | the programme name in `carbon_policies_regional.csv` |
| `td_cap_counts_onsite` | 1 | 0: on-site emissions never count in this programme |
| `td_cap_threshold_mw` | none | nameplate threshold (MW) |
| `td_cap_threshold_tco2` | none | emissions threshold (tCO₂/yr), tested on potential to emit at rated output |
| `td_cap_threshold_basis` | `unit` | `unit`: each generator; `site`: all the TD's linked fuel generators in the zone |
| `td_cap_limited_export_exemption` | 0 | 1: exempt a site whose `td_export_limit_share` ≤ 0.1 |

A programme not listed counts on-site emissions with no threshold. Applicability is fixed before the solve (a
threshold on endogenous builds or emissions would make the problem non-convex). After the solve,
`tracked_demand_onsite_emissions.csv` reports every on-site tonne by programme with `covered_by_cap`,
`coverage_reason` and `threshold_check`. The check flags a site exempted on potential to emit whose actual
emissions reach the threshold; re-run it as covered. Exempt tonnes are taken out of the cap row, and
`carbon_program_clearing_prices.csv` reports emissions net of them (SHARED_CHANGES #111).

**Current thresholds** (checked 11 Oct 2026). The case-build writer (2g) will map them onto the model's programme
names:

| Programme | Rule | Threshold | Basis | Limited export |
|---|---|---|---|---|
| RGGI | 2017 Model Rule XX-1.4, as adopted in each state's CO2 Budget Trading rule; the 2025 Model Rule (Third Program Review, 3 Jul 2025; states to adopt by 1 Jan 2027) wasn't found to change it | a fossil unit serving a generator ≥ 25 MWe nameplate | unit | permit condition limiting grid supply to ≤ 10% of the unit's annual gross generation (exemption, e.g. Delaware 7 DE Admin. Code 1147) |
| VA | 9VAC5-140-7040 A: Revision A26, Virginia Register Vol. 42 Iss. 20, effective 24 Apr 2026 (6040 repealed); first control period 1 Jul–31 Dec 2026. DEQ's follow-on Revision D26 will align the rule with the 2025 Model Rule | 25 MWe | unit | 7040 B: ≤ 10% of annual net generation to the grid, for sources at or next to a manufacturing facility |
| CA | 17 CCR §95812(c)(1); AB 1207 amendments (proposed Jan 2026, for 2027 budgets) weren't found to change it | 25,000 tCO₂e per data year, per facility | site | none |
| WA | WAC 173-446-030 | 25,000 tCO₂e per year, per facility | site | none |

**Per-state overrides** within a programme (RGGI is one programme with state rules):

```
tracked_demand_cap_coverage_states.csv
CO2_PROGRAM,STATE,td_cap_state_threshold_mw,td_cap_state_condition
RGGI,NY,15,at_budget_source_or_two_units
```

`td_cap_state_condition` is `always` or `at_budget_source_or_two_units`. Zone → state comes from
`tracked_demand_zone_states.csv` (`LOAD_ZONE,STATE`), else `hierarchy.csv` (`ba` → `st`, searched upward from the
inputs folder).

New York's rule is 6 NYCRR §242-1.4(a)(2): from 1 Jan 2021, a unit serving a generator ≥ 15 MWe is a CO2 budget
unit if it is at an existing CO2 budget source, or at a source with two or more units ≥ 15 MWe. So a site is
tested at 15 MWe when one of these holds, and at RGGI's 25 MWe otherwise:
- one of its units is ≥ 25 MWe (the site is a budget source);
- it has two or more units ≥ 15 MWe. A generator counts as rated ÷ unit units.

Other notes: The CA and WA tests
are on CO₂e and on the whole facility; the module tests the on-site generators' CO₂ (combustion and the fuel's
upstream factor, as `DispatchEmissions`). Sources: [Delaware RGGI rule (25 MWe; 10% exemption)](https://archive.regulations.delaware.gov/register/september2013/proposed/17%20DE%20Reg%20303%2009-01-13.htm),
[RI DEM RGGI page](https://dem.ri.gov/environmental-protection-bureau/air-resources/regional-greenhouse-gas-initiative-rggi),
[RGGI program review](https://www.rggi.org/program-overview-and-design/program-review),
[9VAC5-140-7040](https://law.lis.virginia.gov/admincode/title9/agency5/chapter140/section7040/),
[Virginia DEQ, Carbon Trading (Revision A26, effective 24 Apr 2026)](https://www.deq.virginia.gov/air-energy/greenhouse-gases/carbon-trading),
[Virginia Register Vol. 42 Iss. 20](https://register.dls.virginia.gov/vol42/iss20/v42i20.pdf),
[6 NYCRR §242-1.4](https://www.law.cornell.edu/regulations/new-york/6-NYCRR-242-1.4)
([NYSDEC Part 242 text](https://dec.ny.gov/sites/default/files/2025-12/part242_et.pdf)),
[17 CCR §95101/§95812](https://regulations.justia.com/states/california/title-17/division-3/chapter-1/subchapter-10/article-2/subarticle-1/section-95101),
[ICAP: California Cap-and-Invest](https://icapcarbonaction.com/fr/ets_system/45),
[WAC 173-446-030](https://lawfilesext.leg.wa.gov/Law/WAC/WAC%20173%20%20TITLE/WAC%20173%20-446%20%20CHAPTER/WAC%20173%20-446%20-030.htm).

### 15.3 45V (deferred)

45V stays a post-solve assessment. It goes in the objective once upstream methane from the gas work can feed
lifecycle intensity. Under current law, a facility must begin construction before 1 January 2028 (OBBBA, P.L. 119-21,
§70511, amending §45V(c)(3)(C); [Clifford Chance, Nov 2025](https://www.cliffordchance.com/content/dam/cliffordchance/briefings/2025/11/one-big-beautiful-bill-act-notable-terminations-of-tax-credits-for-solar-wind-hydrogen-and-clean-vehicles.pdf)).
The assessment doesn't apply this date: a period's electrolyser must have started construction in time for its
result to mean anything.

### 15.4 Hourly matching: the clean ledger

For each TD with CFE targets and each timepoint t (G = grid draw, gcf = `grid_clean_fraction`):

| Quantity | Limit |
|---|---|
| `TDGridCleanToLoad` + `TDCleanChargeGrid` | ≤ gcf × G |
| `TDCleanChargeOnsite` | ≤ on-site clean output |
| `TDCleanChargeOnsite` + `TDCleanChargeGrid` | ≤ battery charge |
| battery charge − clean charge | ≤ (1 − gcf) × G + non-clean on-site output + linked generators (no relabelling) |
| `TDCleanDischarge` | ≤ battery discharge |
| `TDCleanLevel` | ≤ battery level; tracks clean charge in and clean discharge out at the battery's efficiency |
| clean served = on-site clean − `TDCleanChargeOnsite` + `TDCleanDischarge` + `TDGridCleanToLoad` | ≤ `TDDispatch` |

The block credit is `TDBlockGridCleanMWh` (≤ Σ weight × `TDGridCleanToLoad`, and the stage-9 caps) + RECs + Σ weight
× (on-site clean served − gcf × export). So clean energy counts once, when it's consumed, and never above
consumption in any hour. Clean that goes into the battery counts when it comes out, less the battery's losses. The
export debit is conservative: a TD can't raise its grid clean credit by drawing and exporting in the same hour, and
an hour of real export loses gcf × export.

`tracked_demand_clean_ledger.csv` lists the ledger by TD and timepoint. `tracked_demand_cfe_detail.csv` uses the same
terms as the constraint. `tracked_demand_cfe_computed.csv` re-scores each hour with the solved grid's clean fraction:
on-site clean served plus max(0, G × gcf_actual − clean grid charge), capped at consumption, less export.

**Computed grid metrics.** `grid_clean_fraction_computed.csv` and `grid_co2_intensity_computed.csv` are computed from
the model's `DispatchGen` and `DispatchEmissions` per hour, keyed by timepoint id, with `timestamp` as an extra
column. The first is a valid `grid_clean_fraction.csv` for the next pass (10.1's iteration works). Storage and every
TD's linked on-site generators are left out of the grid mix. Zero-weight stress hours have their own values. Light
stress days (§69) read 0 intensity, as the model has no fuel use there.

### 15.5 Carry-forward between chain stages

In a mode-A chain, `prepare_next_stage` (SHARED_CHANGES #112) calls `tracked_demands.chain_inputs` when the stage
wrote tracked-demand outputs. It writes, in the next stage's inputs folder:

| File (`.chained.<case>.csv`) | From | In the next stage |
|---|---|---|
| `tracked_demand_onsite_existing` | `tracked_demand_onsite_build.csv` | `BuildOnsiteTech ≥ existing` (expandable) |
| `tracked_demand_storage_existing` | `tracked_demand_storage_build.csv` | battery MW and MWh ≥ existing |
| `tracked_demand_h2_existing` | `tracked_demand_h2_build.csv` | tank kg ≥ existing |
| `tracked_demand_siting_fixed` | `tracked_demand_zone_allocation.csv` at the committed period; once fixed, passed on unchanged | allocation fixed (normalised to sum to 1) in every period |
| `tracked_demand_cap_coverage_fixed` | `tracked_demand_onsite_emissions.csv` at the committed period; earlier stages' rows win | coverage as carried (`carried_from_earlier_stage`) |

The module reads `<file>.chained.<case>.csv` itself, where case is the outputs folder's name, as
`prepare_next_stage` names it. An explicit `--input-aliases` entry for the base name wins. The same files can be
written by hand for a first stage, e.g. `tracked_demand_siting_fixed.csv`. Existing capacity keeps paying its
annualised cost each period, like a predetermined generator.

Linked generators carry through the standard predetermined-build chaining. Stage reuse's handover runs the same
code on copied outputs and writes the same bytes (tested).

Limitation: on-site, battery and tank capacity are single builds per stage, so a window (mode B) commits the whole
stage's build.

### 15.6 Stress days and the planning-reserve requirement

Stress days are the zero-weight timepoints. Two new `tracked_demands.csv` columns set the TD's load there:

| Column | Default | Meaning |
|---|---|---|
| `td_stress_mode` | `max` | `max`: `td_max_power_mw`; `profile`: `tracked_demand_stress_profile.csv` (`TRACKED_DEMAND, TIMEPOINT, td_stress_load_mw`, every stress timepoint); `min`: `td_min_power_mw` |
| `td_stress_curtailable_share` | 0 | the load may be cut by up to this share, with any mode: (1 − share) × L ≤ `TDDispatch` ≤ L |

Weighted hours are unchanged. In `max` mode, a TD whose grid connection can't carry (1 − share) × its maximum, and
that has no on-site supply, stops the run with the options to fix it.

**PRM requirement** (prm_regional, SHARED_CHANGES #113), in every stress hour of period p:

    td_prm_requirement_mw[td, p] = min(td_grid_interconnect_mw, max over p of td_max_power_mw) × (1 − share)

- It is added to the served load of the TD's zone at the central node; a location-flexible TD's is shared by its
  allocation.
- The part known before the solve (location-fixed TDs, and flexible TDs with fixed siting) is added to the region
  peak, so the import cap (share × peak) rises with it. A flexible TD whose siting is still free isn't in the region
  peak (the peak is a parameter).
- On-site plant gets no reserve credit (`prm_credit = none`, checked).
- Load order: tracked_demands before prm_regional (S0 scenario lines put `extra_modules` first); the module stops
  otherwise.
- The legacy `planning_reserves` pair doesn't see tracked demands.

### 15.7 gen_zone_ratio

The load side of every zone and group ratio and hourly share adds each tracked demand's zone withdrawal: grid draw
− export + its linked on-site generators. This matches the generation side, which counts those generators. On-site
solar and other private techs are on neither side. gen_zone_ratio declares a placeholder only when `tracked_demands`
is on the module list, and the module fills it (SHARED_CHANGES #114).

### 15.8 Grid connection

New `tracked_demands.csv` columns:

| Column | Default | Meaning |
|---|---|---|
| `td_connection_cost_per_kw` | `.` | own overnight $/kW (wins) |
| `td_connection_cost_multiplier` | 1 | × the zone's network-upgrade $/kW (`tracked_demand_zone_upgrade_costs.csv`: `LOAD_ZONE, td_zone_upgrade_cost_per_kw`) |
| `td_connection_cost_share_demand` | 1 | share of the annual cost paid by the demand (bill reporting only) |
| `td_connection_endogenous` | 0 | 1: the size is a decision up to `td_grid_interconnect_mw` (≤ peak load); location-fixed TDs only |
| `td_connection_uses_headroom` | 0 | 1: the connection added this stage draws interconnection headroom in the TD's zone |
| `td_headroom_weight` | 1 | headroom MW per MW of connection. Used only when the draw is on (`td_connection_uses_headroom = 1` and `--td-headroom-draw on`); otherwise ignored, whatever its value |

`tracked_demand_params.csv` sets `td_connection_life_years` (default 40). The cost is annualised at the model's
interest rate and charged on the whole connection every period (`TDConnectionCost`, in total system cost).

**Outputs.**
- `tracked_demand_connection_costs.csv`, by TD and period: connection MW (existing, new), $/kW and its source,
  annual cost, `paid_by_demand_usd` / `paid_by_other_customers_usd`, headroom draw, headroom value ($/kW-yr), charge
  ($/yr and NPV), and carried draw and charge.
- `tracked_demand_headroom_ledger.csv`: every stage so far.
- `tracked_demand_headroom_summary.csv`: connect-stage, later-stage and total charge (undiscounted, × period years).

**Headroom draw.** The draw enters interconnection_headroom's headroom limit (SHARED_CHANGES #115). The charge is
the limit's shadow price × MW drawn. To check it against the cost increase:

```
python tracked_demands/scripts/headroom_counterfactual.py RUN_DIR --outputs-dir OUT -- <switch solve args>
```

This runs the case with and without `--td-headroom-draw off` and writes `tracked_demand_headroom_counterfactual.csv`
(objective difference vs attributed NPV). They agree while the draw stays on one linear piece of the headroom curve.
The charge understates a draw that moves onto a dearer piece.

In a chain, the connection and its cumulative draw carry forward (`tracked_demand_connection_existing`). Only the
connection added in a stage draws new headroom; the carried draw is charged at each later stage's headroom value.
An endogenous connection, like a still-free siting, is not in the PRM region peak.

### 15.9 Case-build writer

The writer runs after the case is built and works on a copy of its inputs folder:

```
python -m tracked_demands.case_writer --inputs-dir switch/in/2035/<case> --spec tracked_demands/specs/<spec>.yml
```

It writes `<case>_td` (or `--out-dir`), so the original case and the scenarios that share it are untouched. The spec
(`tracked_demands/specs/dc_1gw_example.yml`) lists each tracked demand with these fields:
- zone and MW;
- annual energy share;
- connection (cost multiplier or own $/kW, bill share, endogenous, headroom draw);
- stress mode and curtailable share;
- export limit;
- matching (hourly / annual / hour_ranges, target, penalty);
- private on-site techs (energy source by spec name, cost, `tax_credit_key`, MW cap);
- battery;
- linked generators (prime mover, fuel by spec name, heat rate, $/kW, unit MW, MW limit).

It also gives the case's `CO2_PROGRAM` names mapped to rule families. From the case it takes:

| Input | Taken from the case |
|---|---|
| time blocks | `timepoints.csv`, without zero-weight timeseries and those in `prm_timeseries.csv` / `stress_light_timeseries.csv` |
| energy-source / fuel names | `gen_info.csv` (e.g. solar → `sun`) and the fuel the zone's existing gas plant burns |
| tax credits | `gen_tax_credits.csv` (pg_to_switch's `tax_credit_values`, by period), matched on `tax_credit_key`; a spec `tax_credit_values` block overrides |
| zone upgrade $/kW | ReEDS conventional reinforcement median (`interconnection_headroom/data/reference/reeds_reinforcement_by_zone.csv`, 2024$) |
| reserve credit, ic weight | rows added to `prm_gen_credit.csv`, `ic_weights.csv` when present |
| gas-turbine cap | the form from the case's `gas_turbine_cap_params.csv` (`gtc_form`), and the lists and weights from `build_rate.gas_turbine_cap` in the spec's `build_rate_settings` file (default `pg/settings/build_rate.yml`), with the spec's `gas_turbine_cap:` keys over it. Coverage is `allowance_coverage` under `cumulative_additions` (S0 production cases), else `coverage` (table in 15.1). Covered linked generators go into `gas_turbine_cap_gens.csv`; the coverage goes into `tracked_demand_gtc_coverage.csv`. A class already in the case's file but outside that coverage stops the writer (wrong settings file). |
| gas capex premium | `s0_production.gas_capex` (mode, path, year_basis, premium_paths) from the spec's `s0_production_settings` file (default `pg/settings/s0_production.yml`), with the spec's `gas_capex:` keys over it, checked against the case's own `s0_production_log.txt` (see below) |
| unit size | the spec's `unit_mw` → `td_onsite_unit_mw`; `gen_unit_size` stays blank so the build stays continuous (15.1) |
| headroom weight | `connection.headroom_weight` → `td_headroom_weight` (default 1). Ignored unless `connection.uses_headroom` is set and the draw is on, so the 1 written with the draw off has no effect |
| zone states (NY rule) | `hierarchy.csv` |

`tracked_demands_scenario_options.txt` gives the option to add (`--include-module study_modules.tracked_demands`,
before prm_regional's options). `tracked_demands_writer_log.txt` records what was resolved.

For a chain, run the writer on each stage's folder with the same spec.

**Files the writer edits.** `gen_info`, `gen_build_costs`, `prm_gen_credit`, `ic_weights`, `gas_turbine_cap_gens`
and `gen_tax_credits` are edited in the base file and in every `<file>.chained.<case>.csv`; a chained stage reads
the chained ones. Each file's own column names are used (S0 build costs say `BUILD_YEAR`), and rows already present
are kept.

**Unit commitment of linked generators.** Each generator's `uc:` block sets `min_load_fraction`, `min_uptime`,
`min_downtime`, `startup_om`, `startup_fuel`, `ramp_limit_up` and `ramp_limit_down` (Switch's `gen_` columns). A
field left out takes the default for its `technology` (`recip`, `aero`, `industrial_ct`, `frame_ct`, `cc`,
`fuel_cell`, `diesel`) from `tracked_demands/data/onsite_uc_defaults.yml`, or stays unset (Switch's default), and the
log says so with a WARNING. Only `frame_ct` and `cc` are sourced (the repo's PowerGenome combustion-turbine and
combined-cycle rows); the rest are placeholders. The technology also sets the gas-turbine cap category (15.1); the
spec's old `gtc_class` / `gtc_weight` keys are ignored with a warning.
Nothing is copied from existing plants.

**Gas capex premium on linked generators.** A spec's `overnight_cost_per_kw` is capex without the S0 gas capex
premium, which is how the input research delivers it. When the case was built with `gas_capex.mode: premium`, the
writer multiplies each linked generator's overnight cost in each period by (1 + premium). The premium is the case's
path for the generator's premium class, averaged over the period's in-service years as the case build did it
(`premium_for_period` from `s0_workflow/production.py`: `span_mean`, or the period's last year with `period_label`).
On the central path that gives CT 0.45 and CC 0.37 for 2028, and CT 0.226 and CC 0.184 for 2035 (span 2031–35).

| Technology | Premium class (default) |
|---|---|
| `aero`, `industrial_ct`, `frame_ct` | `ct` |
| `cc` | `cc` |
| `recip`, `diesel`, `fuel_cell` (and storage) | none |

- A generator's `premium_class: ct | cc | none` overrides the default, e.g. a recip premium sensitivity.
- `premium:` (a fraction, or a `{year: fraction}` table averaged the same way) replaces the path for that generator.
- Private on-site techs and storage carry no premium; setting one there stops the writer.
- Modes `atb_moderate` and `gridlab` give no premium.

**Check against the case build.** The writer reads the last `gas capex:` line of the case's
`s0_production_log.txt` and compares it with the resolved settings:
- **Mode, path or year basis differ:** the writer stops (wrong settings), or warns when the spec's `gas_capex:` set
  them deliberately (a sensitivity).
- **Logged premiums** (to 0.1%) must equal the writer's for each class and period.
- **No such log:** the case wasn't built with s0_production, so no premium unless the spec sets one (warned).
- **Regression row** (`on_pgdays`, `gas_capex: atb_moderate`): give the spec `gas_capex: {mode: atb_moderate}`.

The log records each generator's premium by period, or why it has none.

**Limited-export exemption.** It is off in every rule family; turn it on per programme in the spec:
`cap_coverage: {"ETS 1": {family: RGGI, limited_export_exemption: 1}}`.

### 15.10 Ready for a first real case

**Inputs a case needs** (the writer produces all of these from the spec and the case):
`tracked_demands.csv`, `tracked_demand_candidate_zones.csv`, and as used:
- time blocks and CFE targets;
- on-site techs, subsidies, build caps, battery;
- linked generators (`gen_info` / `gen_build_costs` rows, `tracked_demand_onsite_gens.csv`);
- cap coverage, state overrides and zone states;
- zone upgrade costs;
- `grid_clean_fraction.csv`.

**Still manual:**
- the spec values: costs, sizes, targets, penalty. The example's numbers are placeholders;
- the first-pass grid clean fraction: a constant, or a previous run's `grid_clean_fraction_computed.csv`. Iterate
  (10.1) for large demands;
- for aggregated zones, the zone upgrade $/kW (ReEDS has BA zones only);
- adding the option to the scenario line, before prm_regional's options (S0: `s0_production.extra_modules`);
- for chains, running the writer per stage;
- for the headroom draw, `td_connection_uses_headroom` and, as a check, `scripts/headroom_counterfactual.py`.

**Expected size, one 1 GW data centre in one zone** shaped like the example spec (one on-site tech, a battery,
hourly matching, one linked generator, fixed connection). Measured on the toy: +17 variables and +27 constraints
per timepoint (no commitment on the toy). On the S0 configuration (960 timepoints in a 2035 stage; about 15.8 M
variables and 20.4 M constraints with compact reserve rows, CHANGES §70), that is about +16–20 k variables and
+26–30 k constraints, about 0.1%, including the linked generator's commitment rows under `commit_operate`. A few
existing rows gain terms:
- the zone's energy balance;
- the zone's stress-hour reserve rows (a constant);
- gen_zone_ratio rows where the zone has limits;
- the carbon-cap row of its programme (only when on-site emissions are exempt).

For the regression row `s4x1_S0prod_2035` (PowerGenome k-means days), scale by its timepoint count. It is
LP unless `td_onsite_tech_min_build_mw` / `_build_increment_mw` are set (MIP). Duals for the headroom charge need
an LP solution; Gurobi barrier gives them.

## 16. Backup Diesel (§120)

Design: `tracked_demands/docs/design/diesel_backup_proposal.md`, accepted with Tom's decisions of 12 Oct 2026 (decisions
register, "backup diesel"). Option B is implemented; option C (requirement by hour) is not.

### 16.1 Site configurations

| `diesel.config` | Model |
|---|---|
| `grid_backup` | a reliability fleet of `multiple` (1.3) × the grid connection (`connection_mw`, not peak load; an endogenous connection uses this upper bound), so a hybrid site backs up only its grid share |
| `blackstart` | nothing modelled (a few MW, negligible energy); logged, `blackstart_mw` reported if given |
| `none` | nothing |

- **Config omitted** in a `diesel:` block: `blackstart` if the demand has gas-fired linked generators, else
  `grid_backup` (logged).
- **No `diesel:` block:** none.

### 16.2 The fleet

The fleet is existing capacity at zero capex: a `gen_build_predetermined` row in the year before the first period, with
a `gen_build_costs` row at overnight 0, in the base and chained files.
- **On a chained stage** that already carries it, the stage's rows and build year are kept. The chain carries the
  fleet forward like any existing plant.
- **Generator:** `gen_tech` `TD_diesel`, the case's distillate fuel, `gen_unit_size` blank. Switch's outage rates
  are 0; availability is `td_diesel_forced_outage_rate`.
- **Other modules' settings:** reserve credit none; ic weight 0; gas-turbine cap category none; gas capex premium none;
  never clean in matching.

`tracked_demand_diesel.csv`: `TRACKED_DEMAND, GENERATION_PROJECT` and

| Column | Default | Meaning |
|---|---|---|
| `td_diesel_permit_flh` | 100 (PLACEHOLDER) | site permit cap, full-load-equivalent hours per year. Sensitivities 37 and 137: VA permits' annual fuel caps imply 37–137 FLH; MD 7–24; WA 45–100. The caps are sized to keep the site under the major-source NOx threshold |
| `td_diesel_test_flh` | 10 | hours per year of testing and maintenance, inside the permit. Filings: 4–25 h (Laurelwood, CA 10.5 h; others 5–26 h) |
| `td_diesel_emergency_counts_toward_cap` | 1 | 0: C2 leaves out stress-day (emergency) running, so the cap limits only testing and non-emergency hours. Federal rules put no time limit on emergency use; whether state permit caps count emergency hours is being checked |
| `td_diesel_non_emergency_flh` | 0 | non-emergency hours per year; > 0 only for tier 4 or `permitted_non_emergency` |
| `td_diesel_tier` | 2 | 2 or 4: NOx reporting (certification limits 6.4 / 0.67 g/kWh) and the non-emergency rule |
| `td_diesel_permitted_non_emergency` | 0 | 1: permitted as a non-emergency engine |
| `td_diesel_forced_outage_rate` | 0 (writer: 0.05 PLACEHOLDER) | availability on stress days |

**Federal limits** (40 CFR 60.4211(f) for NSPS engines, 63.6640(f) for RICE NESHAP engines; rule text checked by the
input research, 12 Oct 2026):
- **Emergency operation:** no time limit.
- **Maintenance and testing:** up to 100 h/yr. Of those, up to 50 h/yr may be non-emergency running, which may not
  be peak shaving, non-emergency demand response, or supplying the grid for compensation.
- **Emergency demand response is no longer in the 100 h.** The 15 h allowance was vacated (D.C. Circuit 2015,
  mandate 2016) and removed from the CFR in 2022.
- **Paid local-reliability dispatch** within the 50 h is allowed only under the rule's five conditions, for NSPS and
  area-source NESHAP engines.

Economic running in normal hours is peak shaving, so non-emergency running is allowed only for tier 4 engines or
engines permitted as non-emergency; the writer and the run stop otherwise.

### 16.3 Constraints

| | Rule |
|---|---|
| C1 | No running outside stress days (`TD_Diesel_Emergency_Only`). With non-emergency hours: at most those hours a year, counted at `tp_weight_in_year` (`TD_Diesel_Non_Emergency_Limit`) |
| C2 | Permit cap per year (`TD_Diesel_Permit_Cap`, per TD, generator, period and weather-year group): stress MWh at real duration (unless `td_diesel_emergency_counts_toward_cap` = 0) + non-emergency MWh per year + test hours × MW ≤ permit hours × MW |
| C3 | No export from diesel: `TDGridExport` ≤ the TD's other linked generators' output (`TD_Diesel_No_Export`) |
| C4 | Stress-day availability: dispatch ≤ MW × (1 − forced outage rate) (`TD_Diesel_Availability`) |
| C5 | `TDDieselStressCost`: stress MWh × (variable O&M + heat rate × fuel cost) per year, in total cost |
| C6 | `TDStressShortfall` relaxes the stress-day load floor (`TD_Stress_Load_Min`, `TD_Dispatch_Lower_Bound`); `TDStressShortfallCost` at the penalty per MWh per year |

**C2: stress hours.**
- Each stress hour counts at its real duration, `tp_duration_hrs` (`ts_duration_of_tp`; 1 h in S0), once a year.
  Never `tp_weight`, which is 0.
- **Weather-year groups.** With `td_stress_year_basis: weather_year` (default), each weather year's stress days are
  one year: `tracked_demand_stress_years.csv` (`TIMESERIES, td_stress_weather_year`), which the writer takes from the
  case's `prm/<model year>/stress_days.csv`. S0's greedy rule draws up to 12 days from 7 weather years, so pooling
  them would ask one year's permit to cover all of them.
- With `all`, or without the file, each period's stress days are one year.
- A period with no stress days has one group, `all`, for test and non-emergency hours.

**C5: cost of stress-day running.**
- It is divided by the period's number of weather-year groups, giving the expected cost per year.
- Fuel cost is `fuel_cost.csv`, or the regional market's cheapest tier plus the zone adder.
- Stress days carry no other cost, so without C5 the fleet's dispatch there would be arbitrary.
- With it, the fleet runs only where it relieves something:
  - a stress grid limit (16.4);
  - or a binding stress-day energy balance. On the toy at 1.5× stress load it runs even at s = 1. In S0 the reserve
    test, with its margin, binds first.

**C6: shortfall.**
- Only TDs with a fleet or a stress grid limit have the slack.
- **Penalty:** `td_stress_shortfall_penalty` (`tracked_demand_params.csv`), else the case's lost-load cost
  (`unserved_load_penalty_per_mwh`, when the unserved-load module is on), else 10,000 $/MWh; per year, as C5.
- `td_stress_strict = 1` fixes the slack at 0.

### 16.4 Stress grid limit (option B) and the PRM rule

`td_stress_grid_limit_share` s (`tracked_demands.csv`, default 1):
- **Grid draw on stress days:** at most s × connection (`TD_Stress_Grid_Limit`). This represents mandatory
  curtailment of large loads in emergencies, with backup generation carrying the site.
- **What covers the rest:** the curtailable share first, then the fleet, on-site plant and storage, within the permit.
  Anything left is shortfall.
- **PRM requirement:** min(s × connection, peak) × (1 − curtailable share), or s × connection × (1 − share) for an
  endogenous connection. Still a constant per period, so prm_regional is unchanged. s = 1 is exactly §108's rule.
- **Stress modes:** L comes from the mode (max, profile, min). The floor is max((1 − c) × L, the TD's minimum),
  since the minimum applies on stress days too.
- **Reserve credit:** on-site plant still gets none (D1b), diesel included.

### 16.5 Visibility (item 5)

- **Before the solve.** For each TD with a fleet or limit, period and weather-year group, the run compares two
  amounts:
  - the stress-day energy beyond s × connection;
  - what the fleet can give (MW × (1 − forced outage) per hour, and within permit − test hours when emergency hours
    count) plus capacity-limited
    linked generators.

  If the need is larger: a WARNING with both numbers, or an error with `td_stress_strict`. On-site techs, storage or
  unlimited generators make it inconclusive, so it is skipped then.
- **After the solve.** `tracked_demand_stress_summary.csv` gives, per TD and period, the binding weather year, the
  hours left and the shortfall (MWh total and per year). Any shortfall prints a WARNING.

### 16.6 Outputs

- **`tracked_demand_diesel.csv`**, per TD, generator, period and weather-year group:
  - size and hours: `diesel_mw`, `permit_flh`, `stress_flh`, `non_emergency_flh`, `test_flh`, `total_flh`,
    `flh_remaining`, `cap_dual_usd_per_mwh`;
  - energy, fuel and emissions for stress, non-emergency and test running: MWh, MMBtu, tCO₂ each;
  - `nox_t`, from the tier's certification limit;
  - `shortfall_mwh`.

  Stress and test fuel and CO₂ are computed by the module (MWh × heat rate × the fuel's intensity). So they are the
  same under the `light` and `full` stress formulations, and they are outside the model's cap rows. Non-emergency
  running uses Switch's accounting and enters the cap rows.
- **`tracked_demand_dispatch.csv`** gains `TDDiesel_MW` and `TDStressShortfall_MW`, only when a fleet or a stress
  limit exists.
- **`tracked_demand_onsite_emissions.csv`** gains `tco2_emergency_per_yr` and `emergency_note`, only when a fleet
  exists.

### 16.7 Cap coverage

- **Potential to emit** uses `td_onsite_pte_hours` (new column of `tracked_demand_onsite_gens.csv`; "." = all year,
  8766 h). A backup fleet defaults to its permit hours, and the writer writes them. It matters only in programmes
  without a backup-diesel rule; other fuels keep it.
- **Unit-basis programmes (RGGI, VA):** unchanged. The 2–3 MW units are below 25 MWe, so the fleet is exempt.
- **CA and WA never cover backup diesel at the facility** (rule text checked 12 Oct 2026):
  - **CA:** 17 CCR §95101(f)(2). Backup or emergency units designated in an air district permit are not reported,
    so they can't count toward the threshold. The obligation for distillate falls on its suppliers, §95852(d).
  - **WA:** WAC 173-446-040(3)(a)(ii). Purchased diesel is not a facility covered emission; it is the supplier's,
    under (3)(c)(i).
  - **How the model applies it:** the rules file gives CA and WA a `backup_diesel_rule`, which the writer passes as
    `td_cap_backup_diesel_rule`. A programme with one marks the fleet not covered
    (`backup_diesel_not_covered (<citation>)`), whatever its potential to emit. The fleet also stays out of that
    programme's site-basis sum for the site's other generators.

### 16.8 Spec

```yaml
    stress_grid_limit_share: 1.0     # option B; 1 = §108's rule
    stress_strict: false
    diesel:
      config: grid_backup            # grid_backup | blackstart | none; omitted: blackstart with gas linked gens
      multiple: 1.3
      unit_mw: 2.5
      tier: 2
      permit_flh: 100                # PLACEHOLDER; sensitivities 37, 137
      test_flh: 10                   # filings 4-25 h
      emergency_counts_toward_cap: 1 # 0: the cap limits only testing and non-emergency hours
      non_emergency_flh: 0           # > 0 only with tier 4 or permitted_non_emergency
      stress_year_basis: weather_year
      heat_rate: 10.2                # PLACEHOLDER
      forced_outage_rate: 0.05       # PLACEHOLDER
      variable_om: 10                # PLACEHOLDER
stress_shortfall_penalty: 10000      # optional; default the case's lost-load cost, else 10,000
```

Tests: `test_td_diesel.py` (23) and `test_td_case_writer.py` (diesel, 10).
