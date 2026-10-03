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
