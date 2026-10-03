# s0_workflow: S0 production build (and the staged test-workflow scripts)

**Status (CHANGES §44):** productionised. The S0 production case now builds through tracked settings
with no hand steps: `pg/settings/s0_production.yml`, switched on per case by the `s0_production`
column of `scenario_inputs.csv`. See `Guides and documentation/s0_production.md` (what each setting
replaces), `VM_RECIPES.md` (regression and mode-B recipes) and `../SHARED_CHANGES.md`.

| Production code | Replaces |
|---|---|
| `production.py` | `case_aliases/*` (B6 gas capex, B8 coal caps, windloss/windloss2, NY ACP, build-rate central v2, headroom slack), the amortisation `--include-module`, stages for bounded foresight |
| `day_selection.py` | `day_selection/netload_days_fi.py` + `pg_to_switch_netload.py` |
| `coal_cf.py`, `scripts/fetch_coal_cf_eia923.py`, `data/` | `case_aliases/b8_coalcf.py` (PUDL), from public EIA-923/860 |
| `scripts/compare_s0_runs.py`, `scripts/measure_run.py` | (new) regression and memory/time checks |
| `tests/` | (new) |

The staged scripts below are kept unchanged, for reference and to rebuild the October test runs.
The rest of this file describes them.

**Staged scripts:** nothing below is wired into `pg_to_switch.py`, `modules.txt` or the settings.

These are the scripts behind the October 2026 S0 2035 test runs, collected so they can be reviewed and productionised. They were run from the VM work folder `ic_test_fedpol_work/` (outside the repo). The copies here:
- take paths as arguments (repo-relative defaults) instead of hard-coded folders;
- replace inline steps with script calls where practical.

Run everything from the **repo root** unless a line says otherwise. No model inputs, outputs, caches or licensed data are included.

## Layout

| Path | Purpose |
|---|---|
| `day_selection/netload_days_fi.py` | fleet-independent representative-day selector (drop-in for `kmeans_time_clustering`) |
| `day_selection/pg_to_switch_netload.py` | runs the repo's `pg_to_switch.py` unchanged with the selector swapped in |
| `day_selection/nl_days_config.example.json` | selector configuration (passed as env `NL_DAYS_CFG`) |
| `day_selection/run_pg_netload.bat` | launcher template (env vars for repo, python, config, case id, output folder) |
| `case_aliases/make_nl_case_aliases.py` | aliases for a regenerated-time-sample case, with consistency checks against the s4x1 base |
| `case_aliases/make_partb.py` | B6 gas capex alias (also B1 gas price and B4 storage amortisation; `--only B6`) |
| `case_aliases/b8_coalcf.py` | B8 zonal coal CF caps from EIA-923 2021–24 |
| `case_aliases/make_windloss_case.py`, `make_windloss2_case.py` | wind loss adjustment (onshore, then offshore) |
| `case_aliases/make_rps_acp_alias.py` | NY RPS buyout (ACP) alias |
| `case_aliases/make_build_rate_alias.py` | build-rate supply-curve aliases (the manual `brc patch-case` step, scripted) |
| `solve/solve_newstack_base_s4x1.bat` | solve template for the new-stack base |

## Scripts

### `day_selection/netload_days_fi.py` + `pg_to_switch_netload.py`
- **Purpose:** choose about 24 representative days that are fleet-independent:
  - by transreg (all 11) and nationally, match mean load (±1%), mean onshore-wind CF (±0.010, unweighted across clusters, × 0.881) and mean solar CF (±0.005);
  - give the top-1% load hours 0.75–1.25% of the weight, and the bottom-1% net-load hours 0.5–1.5% (checked on two reference fleets).
  - **Candidates:** k-means medoids, the 3 days with the most top-1% load hours, and the 3 with the most bottom-1% net-load hours. The peak-load day is appended at PowerGenome's weight; weights come from an LP.
  - Raises, and writes no case, if a tolerance can't be met. It relaxes tolerances ×1.5 (at most twice) first and records the factor.
- **Inputs:**
  - PowerGenome's own hourly load and resource arrays, inside `pg_to_switch`;
  - `gen_info.csv` of an existing case with the same case id (cluster → zone and technology);
  - `hierarchy.csv`;
  - for the tail check, the `dispatch_gen_annual_summary.csv` of two solved runs (`tail_fleets`).
- **Outputs:** the case written by `pg_to_switch`, and diagnostics in `diag_dir`:
  - `fi_target_errors.csv`, `fi_tail_shares.csv`;
  - `fi_netload_error_by_fleet.csv` (the net-load error by region on each report fleet, available before solving);
  - `fi_days_selected.csv`, `fi_info.json`.
- **Used by:** `nl24_fi` (`switch/in_ictest/cases/nl24_fi/2035/s4x1_S0unc_2035_icon`). Solved as `nl24fi_icv4B68_S0unc_2035_icon_aw2_icv4_br_central_v2_B6B8`.
- **Command** (Windows; PowerGenome is in the solver env):
  ```bat
  set REPO=<repo root>
  set PG_PYTHON=<env>\python.exe
  set NL_CFG=s0_workflow\day_selection\nl_days_config.example.json
  set CASE_ID=s4x1_S0unc_2035_icon
  set OUT=switch/in_ictest/cases/nl24_fi
  s0_workflow\day_selection\run_pg_netload.bat
  ```
  Equivalent direct call, with `NL_DAYS_CFG` set to the JSON text:
  `python s0_workflow/day_selection/pg_to_switch_netload.py pg/settings switch/in_ictest/cases/nl24_fi --case-id s4x1_S0unc_2035_icon --year 2035`
- **Notes:**
  - `pg_to_switch`'s log still prints "Clustering to 4 1-day timeseries" (the settings value); the selector ignores it.
  - The PRM day is added afterwards, as usual, by `adjust/add_extreme_days.py` (`model_adjustment_scripts`).
  - Single model year only, with 1-day timeseries (asserted).

### `case_aliases/make_nl_case_aliases.py`
- **Purpose:** after regenerating a case with a new time sample, check that every non-time input matches the s4x1 base case. The only allowed differences are row order, `trans_dbid`, float formatting, the native ic_v2 connection-cost removal and the `ic_uprate_mode` column; anything else raises.
  - It then copies the sample-independent aliases: gas_atb, the NY ACP, `ic_params.ic_v2`, `ic_*.<tag>` and `build_rate_*.central.v2`.
  - It rebuilds the time-dependent ones: the B8 `gen_info.ic_v2.coalcfhist.csv`, and windloss2 (all wind × 0.881; the rule reproduces the s4x1 file exactly).
- **Inputs:** the regenerated case; the base case (default `switch/in_ictest/cases/2035/s4x1_S0unc_2035_icon`); the B8 source (default `…/2035/s4x1_S0_2035_icoff`, its `gen_info.coalcfhist.csv`).
- **Outputs:** alias files in the case; `patch_log.<tag>.txt`.
- **Used by:** `nl24_fi` (and, in its earlier inline form, `nl24`).
- **Command:** `python s0_workflow/case_aliases/make_nl_case_aliases.py switch/in_ictest/cases/nl24_fi/2035/s4x1_S0unc_2035_icon nl24_fi --ic-tags ic_v4`

### `case_aliases/make_partb.py` (B6)
- **Purpose:**
  - **B6:** new-build CC/CT overnight cost × ATB 2024 Moderate ÷ GridLab override (CC 1,522,209/2,061,000 = 0.7386; CT 1,129,622/1,606,000 = 0.7034), BUILD_YEAR 2035 rows only.
  - **B1:** gas price scaled to $4.98 use-weighted (needs `--ref-run`).
  - **B4:** storage amortisation 30 years.
- **Inputs:** the case's `gen_build_costs.csv` and `gen_info.csv`.
- **Outputs:** `gen_build_costs.gas_atb.csv` (B6), `fuel_cost.aeo_gas.csv` (B1), `gen_info.stor30.csv` (B4); `patch_log.partb.txt`.
- **Used by:**
  - Part B B6 and B6+B8 (`s4x1_S0_2035_icoff`);
  - the new stack: C1+B6+B8, ic_v4+B6+B8, F1/F2, N1/N2, nl24, nl24_fi. For these the alias was originally made by an inline copy of the factors; this script with `--only B6` reproduces that file to 1e-16 relative.
- **Command:** `python s0_workflow/case_aliases/make_partb.py --case switch/in_ictest/cases/2035_icv4/s4x1_S0unc_2035_icon --only B6`

### `case_aliases/b8_coalcf.py` (B8)
- **Purpose:** for each zone, the capacity-weighted mean of each coal unit's maximum annual CF over 2021–24.
  - Source: EIA-923 via PUDL, winter-capacity basis; partial years skipped; units online in 2035.
  - Applied as `gen_max_annual_availability = zone cap / (1 − forced)` (capped at 1). Zones with < 500 MW of history get 0.65.
- **Inputs:** `pg_data/pudl.2025_08.sqlite` (`--pudl`); `interconnection_headroom/data/reference/county2zone.csv`; the case's `gen_build_predetermined.csv` and the base gen_info (`--base-gen-info`: `gen_info.csv` for 5a, `gen_info.ic_v2.csv` for the headroom stack).
- **Outputs:** `<base>.coalcfhist.csv` (e.g. `gen_info.ic_v2.coalcfhist.csv`); tables `b8_*.csv` in `--tables-dir`; `patch_log.partb.txt`.
- **Enforced by:** `switch/study_modules/gen_annual_availability_limits.py`, which is tracked and in `modules.txt`.
- **Used by:** B8, B6+B8, and all new-stack runs. For the new stack the alias was originally an inline copy; this script with `--base-gen-info gen_info.ic_v2.csv` reproduces it exactly.
- **Command:** `python s0_workflow/case_aliases/b8_coalcf.py --case switch/in_ictest/cases/2035_icv4/s4x1_S0unc_2035_icon --base-gen-info gen_info.ic_v2.csv --tables-dir <tables folder>`

### `case_aliases/make_windloss_case.py`, `make_windloss2_case.py`
- **Purpose:** adjust wind profiles for ATB/ReEDS losses (13.4%) against the 1.7% in the reV profiles: CF × 0.88098.
  - `windloss`: onshore only (new `LandbasedWind_*` and existing "Onshore Wind Turbine").
  - `windloss2`: also offshore (new `OffShoreWind_*` and existing "Offshore Wind Turbine").
  - Checks: no value > 1; maxima 0.866.
- **Outputs:** `variable_capacity_factors.windloss.csv`, then `variable_capacity_factors.windloss2.csv`; `patch_log.ic_v2.txt`.
- **Used by:** every "aw2" run and later (windloss2).
- **Commands:**
  ```
  python s0_workflow/case_aliases/make_windloss_case.py <case>
  python s0_workflow/case_aliases/make_windloss2_case.py <case>
  ```

### `case_aliases/make_rps_acp_alias.py` (NY buyout)
- **Purpose:** `rps_requirements.ic_v2.csv` = `rps_requirements.csv` plus `rps_acp_per_mwh` = 45.39 for `ESR_NY_rps` only. That value is NYSERDA's 2024 Tier 1 ACP, the last one published; from 2025 Tier 1 is a load-share charge with no ACP.
- **Every other program** stays a hard requirement, **including `ESR_NY_ces`** (84.4% in 2035). Requires the opt-in ACP in `study_modules/rps_regional.py` (CHANGES §38).
- **Used by:** 5a_aw2 and every later run (all Part B, C-series, new stack).
- **Command:** `python s0_workflow/case_aliases/make_rps_acp_alias.py <case> [<case> …]`

### `case_aliases/make_build_rate_alias.py`
- **Purpose:** script the manual step behind the `build_rate_*.<level>.v2.csv` aliases:
  1. copy the case to an empty scratch folder;
  2. run `python -m brc.cli patch-case <scratch> --level <level> --regional-groups wind_onshore solar` (build_rate 87729a5 defaults: transreg ceilings for wind and solar, storage national-only);
  3. copy only `build_rate_*.csv` back under suffixed names.

  The case is never patched in place.
- **Inputs:** the case; build_rate pipeline tables (`build_rate/outputs`, from `python -m brc.cli run` in `build_rate/`).
- **Outputs:** six alias files; `patch_log.build_rate.txt`.
- **Used by:** all C-series and new-stack runs (central v2). This script reproduces the six central v2 files byte for byte from the S0 uncapped case.
- **Command:** `python s0_workflow/case_aliases/make_build_rate_alias.py <case> --scratch <empty dir> --level central --python <env python>`

### `solve/solve_newstack_base_s4x1.bat`
- **What it solves:** the new-stack base: S0 uncapped, ic_v4 headroom, build_rate central v2, B6, B8, NY RPS buyout, windloss2, amortisation.
- **Setup:** set `REPO`, `SWITCH_EXE`, `CASE`, `OUT`, `TEMP_DIR`, `GRB_LICENSE_FILE` and optionally `THREADS`.
- **Alias set:** identical to the run `icv4B68_s4x1_S0unc_2035_icon_aw2_icv4_br_central_v2_B6B8` (CO2 1,226 Mt; coal 77.9 GW / 441 TWh / CF 0.65).

## Amortisation: how it is switched on

`switch/study_modules/gen_amortization_period.py` is tracked on every branch checked, but it is **not in `switch/modules.txt` on any of them**:
- branches checked: tom/ic-test-fedpol, origin/tom/s0-integration, vm/ic_v4, origin/main, origin/edf-baseline, origin/ollie/fedpol.
- **Switch-on:** every test run since "aw2" adds `--include-module study_modules.gen_amortization_period` on the solve command line (34 batch files; the solve template does too).
- **Without it:** new-build capital is annualised over `gen_max_age` = 500 years (CRF ≈ 0.05), because `pg_to_switch` fills `retirement_age` with 500.

## Recipe: new-stack base case (s4x1 2035)

1. Generate the base case with `pg_to_switch.py` for case id `s4x1_S0unc_2035_icon` (S0_uncapped, headroom on, `tax_credits = no_wind_solar`). At HEAD, `gen_info.csv` already includes the ic_v2 connection-cost removal.
2. Headroom slack and ic_v4 inputs (the ic_v4 pipeline outputs are gitignored):
   - `python interconnection_headroom/scripts/patch_case_inputs.py <case> --tag ic_v2 --params-only --slack-cost 5e7` (writes `ic_params.ic_v2.csv`).
   - In `interconnection_headroom/`: `python -m icsc.cli run --start-year 2026`.
   - Then `python scripts/patch_case_inputs.py <case> --tag ic_v4 --ic-from outputs --ic-scenario atts_s0 --ic-config config.yaml`.
   - Env note: the headroom pipeline needs `h5py` (VM env `ic-pipeline`); `pg_to_switch` needs PowerGenome and `typer` (VM env `switch-pg-reeds-fedpol`). No single VM env has all of them.
3. Build rate: `make_build_rate_alias.py <case> --scratch <dir> --level central`.
4. Wind losses: `make_windloss_case.py <case>`, then `make_windloss2_case.py <case>`.
5. NY RPS buyout: `make_rps_acp_alias.py <case>`.
6. B6: `make_partb.py --case <case> --only B6`.
7. B8: `b8_coalcf.py --case <case> --base-gen-info gen_info.ic_v2.csv`.
8. Solve with `solve/solve_newstack_base_s4x1.bat`.

**For a regenerated time sample** (e.g. fleet-independent 24 days):
- run `day_selection/` with the same case id into a fresh folder;
- then `make_nl_case_aliases.py <new case> <tag> --ic-tags ic_v4`, which checks the new case against the s4x1 base and copies or rebuilds the aliases;
- solve with the template, pointing `CASE` at the new folder.

## Known issues

- **`gen_inc_heat_rates.csv` ignores `--input-alias`:** `switch_model.generators.core.commit.fuel_use` reads it by a fixed path. Any change to coal min load must also move each unit's heat-rate curve start, and the file has to be replaced in a case copy (as in the F2 coal-flexibility test).
- **NY CES has no buyout.** Only `ESR_NY_rps` has an ACP.
  - On the new stack, about 3.76 GW of new nuclear is built in NYISO (p127) in every run.
  - The tests N1 (no new nuclear) and N2 (NY new nuclear ≤ 1 GW) did not reach a usable solution: numerical failure, and a diverging homogeneous barrier. That suggests infeasibility, but it isn't confirmed (an IIS run failed at LP read).
  - The $15/MWh nuclear credit is labelled "45U existing" but applies only to new-build nuclear.
- **Gas-turbine supply cap** (`MaxCapTag_GasTurbineSupply`):
  - Source: 58 GW of additions over 2025–30 from a Wood Mackenzie press release, extended linearly, enforced on cumulative builds.
  - It binds in every B6 run; relaxing the growth allowance by 25% adds about 18 GW of CC and cuts CO2 by about 34 Mt.
  - **build_rate** refuses a gas group while this cap is active.
- **Single model year only.** The day selector is per model year; a 3-period foresight run at about 625 timepoints per period needs about 120 GB (about 65 MB per timepoint), so it has to be a myopic chain. Foresight runs starting before 2030 also hit the coal-lock issue (`blocked_2030_coal_gas`).
- **`--symbolic-solver-labels`** on the full new-stack s4x1 model produced an LP file Gurobi couldn't read (line 22,985,567). It worked on the small backcast model.
- **Credit spend** for S0 (no new wind/solar credits) has no script yet. It should come from `tax_credit_value.csv` (nuclear PTC) plus storage-ITC value from capex.
- **Not staged here:**
  - analysis/report scripts;
  - IPM comparison scripts (licensed RPE content) and Data Compiler scripts;
  - `netload_days.py` (the first, fleet-based selector);
  - the original fixed-case variants (`make_windloss.py`, `make_windloss2.py`);
  - the backcast scripts.
