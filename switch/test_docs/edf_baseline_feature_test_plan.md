# Feature test plan: tom/carbon-caps, tom/regional-wind-caps, tom/edf-baseline vs origin/edf-baseline

## Why

`tom/edf-baseline`, `tom/carbon-caps`, and `tom/regional-wind-caps` add real functional changes on top of
`origin/edf-baseline` (the current GitHub state) that aren't exercised by CI. Before opening PRs for
`tom/carbon-caps` and `tom/regional-wind-caps`, this test pass smoke-tests each new mechanism end-to-end
(build inputs -> solve -> verify outputs), rather than only confirming the branches merge cleanly.

Tested on branch `tom/regional-wind-caps` (commit `35d292d`), which is a superset containing all four
features below and is already reconciled with `origin/edf-baseline`.

## Features under test

1. **RGGI carbon-cap mechanics** (`switch/study_modules/carbon_policies_regional.py`): hard cap enforcement,
   two-tier CCR (`carbon_policies_ccr.csv`), and a floor-price-as-supply-restriction mechanism
   (`FloorAllowances` variable), plus `carbon_program_clearing_prices.csv` / `auction_revenue_dollar_per_yr`
   output.
2. **`gen_zone_ratio` group constraints** (`switch/study_modules/gen_zone_ratio.py`): the `hurdlereg_min_only`
   group-level ratio constraint, gated by the `gen_zone_ratio` column in `scenario_inputs.csv`.
3. **Regional wind growth caps by transreg** (`make_emission_policies.py` + `pg/update_max_cap_files.py`):
   disaggregates the national `MaxCapTag_WindGrowth` limit into nine per-transreg tags, using a
   pipeline-based method (<=2028) and a regional-share method (2029-2030).
4. **`write_zonal_lmp` study module**: a new post-solve writer that extracts zonal LMPs from the
   `Distributed_Energy_Balance` dual (needs `m.dual`, already declared by `carbon_policies_regional.py`).

Pre-existing `switch/in/{year}/{case}/` folders for these cases were built before these changes landed and
are stale (e.g. missing `carbon_policies_ccr.csv` / floor-price columns). All cases below are rebuilt fresh
via `pg_to_switch.py` into an isolated `switch/in/tests/` tree rather than reusing old folders.

## Directory layout for this test pass

- Inputs: `switch/in_tests/{year}/{case_id}/`
- Outputs: `switch/out_tests/{year}/{case_id}_<suffix>/`
- Checker scripts: `switch/test_scripts/`
- This plan + results log: `switch/test_docs/`

Note: originally planned as `switch/in/tests/` and `switch/out/tests/`, but switched to sibling directories
`switch/in_tests/` / `switch/out_tests/` (same nesting depth as `switch/in/` / `switch/out/`) after finding
that `adjust/increase_timepoint_duration.py` hardcodes a fixed parent-directory depth to locate
`scenario_inputs.csv`, which broke under the extra `tests/` nesting. See results doc, Finding 3.

## Track A -- RGGI carbon-cap mechanics + gen_zone_ratio + zonal LMP

Case: `s4x1_caelp_parclust_zoned`. Originally planned for year 2028 (fastest of the 6 rows with
`gen_zone_ratio != none`), but 2028 turned out to be infeasible for this case for reasons unrelated to the
features under test (see results doc, Finding 4) -- used year 2035 instead, which has proven-solvable
history.

```
python pg_to_switch.py pg/settings switch/in_tests --case-id s4x1_caelp_parclust_zoned --year 2035

cd switch
switch solve --inputs-dir in_tests/2035/s4x1_caelp_parclust_zoned \
  --outputs-dir out_tests/2035/s4x1_caelp_parclust_zoned_smoketest \
  --include-module study_modules.write_zonal_lmp \
  --solver-options-string "crossover=1"
```

Checks (`switch/test_scripts/check_track_a_rggi_genzoneratio.py`):
- Solve terminates optimal, no infeasibility/unboundedness.
- `carbon_program_clearing_prices.csv` exists; clearing price >= floor price; `auction_revenue_dollar_per_yr`
  present and non-negative.
- `gen_zone_ratio_summary.csv` shows the `hurdlereg_min_only` group's `actual_annual_ratio` >= `min_annual_ratio`
  (constraint satisfied, not silently skipped).
- `zonal_lmp.csv` / `zonal_lmp_annual.csv` non-empty, no NaNs, LMPs in a plausible $/MWh range.

## Track B -- Regional wind growth caps by transreg

Case: `s4x1_edf_med`, years 2028 and 2030 (already in `pg/update_max_cap_files.py`'s `CASES` list; independent
of RGGI/gen_zone_ratio).

```
python pg_to_switch.py pg/settings switch/in/tests --case-id s4x1_edf_med --year 2028 --year 2030

cd switch
switch solve --inputs-dir in/tests/2028/s4x1_edf_med --outputs-dir out/tests/2028/s4x1_edf_med_windcap_smoketest
switch solve --inputs-dir in/tests/2030/s4x1_edf_med --outputs-dir out/tests/2030/s4x1_edf_med_windcap_smoketest
```

Checks (`switch/test_scripts/check_track_b_wind_caps.py`):
- `max_cap_requirements.csv` has nine `MaxCapTag_WindGrowth_<transreg>` rows per year, values differing
  between 2028 (pipeline-based) and 2030 (regional-share).
- Built wind capacity summed by transreg (via `hierarchy.csv`) does not exceed each transreg's cap.

## Track C -- Allowance banking (not run)

Allowance banking is not wired to a `scenario_inputs.csv` column -- only reachable via
`switch/run_rggi_foresight_banking.py`'s dedicated alias files, which is a foresight (multi-period) solve.
Out of scope for this smoke-test pass; noted here for completeness.

See `edf_baseline_feature_test_results.md` for results.
