# s0_workflow/data

## coal_cf_caps_eia923_2021_2024.csv

Zonal maximum annual capacity factor for existing coal, used by the S0 production setting
`s0_production.coal_cf_caps` (pg/settings/s0_production.yml). One row per ReEDS zone with coal history.

| Column | Meaning |
|---|---|
| `ba` | ReEDS zone (p1..p134) |
| `hist_mw` | winter MW of the units with a valid capacity-factor history |
| `n_units` | number of those units |
| `cap_cf` | winter-capacity-weighted mean of each unit's highest annual CF in 2021-24 (winter-capacity basis; the cap) |
| `cap_cf_nameplate` | the same on a nameplate basis (reference only) |

**Source (public):**
- EIA-923, annual files 2021-2024 (`f923_<year>.zip`, Schedules 2-5, sheet "Page 4 Generator Data":
  monthly net generation by generator), https://www.eia.gov/electricity/data/eia923/
- EIA-860, annual files 2021-2024 (`eia860<year>.zip`, `3_1_Generator_Y<year>.xlsx`, sheets Operable and
  Retired and Canceled), https://www.eia.gov/electricity/data/eia860/
- county -> zone: `interconnection_headroom/data/reference/county2zone.csv` (ReEDS)

**Method:** `s0_workflow/coal_cf.py`. Units are coal generators (energy source ANT, BIT, LIG, SUB, SGC,
WC, RC) operable (status OP) in EIA-860 2024 with no planned retirement before 2035. Unit-year
CF = annual net generation / (that year's winter capacity x hours). Skipped unit-years: first year in
service, retirement year, fewer than 12 monthly values, zero winter capacity. Unit max = highest valid
CF (clipped to 0-1). Zone cap = winter-capacity-weighted mean of unit maxima.

**Applied by** `s0_workflow/production.py` (`apply_coal_cf_caps`): every existing coal cluster in a zone
gets `gen_max_annual_availability = cap_cf / (1 - gen_forced_outage_rate)`, capped at 1, enforced by
`study_modules.gen_annual_availability_limits`. Zones with less than 500 MW of history (or none) get 0.65.
Aggregated load zones use the history-weighted mean of their member zones.

**Regenerate:** `python s0_workflow/scripts/fetch_coal_cf_eia923.py` (downloads ~180 MB to
`s0_workflow/data/raw/`, gitignored). The 2026-10-03 build: 69 zones, 133.8 GW with history, capacity-
weighted national cap 0.584, 51 zones with at least 500 MW.

**Difference from the test-run step (B8):** B8 read the same EIA-923/860 records from PUDL
(`pudl.2025_08.sqlite`). Values should agree closely; the VM regression recipe
(`s0_workflow/VM_RECIPES.md`) compares this table with the B8 `b8_zone_caps.csv`.

## Coal specification rev. 2 tables (S0 default since Oct 2026)

`coal_cap_units_860m.csv`, `coal_fleet_860m.csv`, `coal_plant_st_fuel.csv` and `coal_holds.csv`. They are
built from public EIA data by `python s0_workflow/scripts/fetch_coal_spec_eia.py`:
- EIA-860M August 2026 and June 2025;
- EIA-860 2020-24;
- EIA-923 2021-24, 2025 final and 2026 year-to-date.

The script checks them against the spec's validation tables in `s0_workflow/specs/coal/`; `--check-only`
writes nothing. Used by `s0_production.coal_spec` and `coal_holds` (`s0_workflow/coal_spec.py`,
`coal_fleet.py`); see `Guides and documentation/s0_production.md` (Coal specification). The table above
(`coal_cf_caps_eia923_2021_2024.csv`) is now the legacy setting, used by the regression case.

| File | Rows | Content |
|---|---|---|
| `coal_cap_units_860m.csv` | 429 | 860M Operating, Conventional Steam Coal, OP/SB/OA, any planned retirement: zone (plant map, then county), winter MW, max valid annual CF 2021-24 |
| `coal_fleet_860m.csv` | 2,249 | 860M Operating and Retired rows of every plant with a coal-group unit (EIA-860 2024 or this 860M); `conversion_year` for NG-coded units |
| `coal_plant_st_fuel.csv` | 404 | monthly ST fuel (MMBtu) and net generation (MWh), NG and coal, 2023-26, plants with a converted unit |
| `coal_model_basis_860er2024.csv` | 467 | public reconstruction of PowerGenome's coal-group fleet (EIA-860 2024 early release, not OS; retirement from the planned year or the July 2025 860M Retired sheet; zone from plant map, then county), for the per-option validation tables (`scripts/build_coal_option_tables.py` → `s0_workflow/specs/coal/by_option/`) |
| `coal_holds.csv` | 10 | held units: hold cap (max(CF since the order, 0.001); 0.01 with < 3 months), window, S0 / holds_persist flags, encoded years, order basis |

## fuel/ (CHANGES §67)

EIA tables behind `s0_production.fuel_prices` (`s0_workflow/fuel_prices.py`), written by
`python s0_workflow/scripts/fetch_fuel_prices_eia.py` from EIA's published files (downloads cached in `raw/fuel/`,
gitignored); `--check` re-downloads and compares. `SOURCES.yml` gives the editions (STEO September 2026; AEO2026,
April 2026), URLs and sha256 of each download.
- `steo_power_fuel_monthly.csv`: STEO monthly 2022-2027: CLEUDUS / NGEUDUS (nominal $/MMBtu to the electric power
  sector), NGEPCON (bcf/d), CLEPCON_TON (million short tons), CICPIUS (CPI-U).
- `aeo2026_power_fuel_national.csv`: AEO2026 Table 3 Electric Power natural gas and steam coal (2025 $/MMBtu), cases
  reference (Counterfactual Baseline), low_ogs, high_ogs.
- `aeo2026_power_fuel_emm.csv`: AEO2026 supplemental Tables 54.1-54.25 by EMM region: price (2025 $/MMBtu) and power
  sector consumption (quadrillion Btu), reference case.
- `fuel_price_options_by_stage.csv`: national price by S0 model year for each option against hist5 / hist5_high_gas
  (`s0_workflow/scripts/compare_fuel_prices.py`).
