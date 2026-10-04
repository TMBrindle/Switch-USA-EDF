# S0 production on energyVm1: regression and mode-B test recipes

Branch: `tom/s0-prod-scripts`. Shell: Git Bash. Repo:
`/d/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS` (quote every path; it has spaces).

Ground rules (same as the ic_v4 recipe):
- The main tree stays on `tom/ic-test-fedpol`; it has work in progress. Work in a separate worktree.
  Never check out another branch in the main tree.
- Never delete files these recipes did not create. Synthetic files are listed, never removed.
- No installs into shared conda envs. If an import fails, stop and report it.
- Case inputs and outputs go to fresh folders, never over existing ones.

## 0. Worktree and data

```bash
MAIN="/d/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS"
WT="/d/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS_s0prod"
git -C "$MAIN" status --short | head -40              # look only
git -C "$MAIN" fetch origin tom/s0-prod-scripts
git -C "$MAIN" worktree add -b vm/s0prod "$WT" origin/tom/s0-prod-scripts   # local branch; do not push it
cd "$WT"
```

`tom/s0-prod-scripts` already contains `tom/s0-integration` (headroom host mode + build rate) and
`tom/ic-test-fedpol` (S0 presets, growth caps, RPS ACP). Nothing needs merging.

Copy the gitignored inputs from the main tree. `cp` only reads `$MAIN`.
```bash
ls interconnection_headroom/data/raw build_rate/data/raw 2>&1        # what is missing
cp -r "$MAIN/interconnection_headroom/data/raw" interconnection_headroom/data/
cp -r "$MAIN/build_rate/data/raw" build_rate/data/
ls interconnection_headroom/data/raw/lbnl/SYNTHETIC_*.xlsx 2>/dev/null   # if anything is listed: stop, tell Tom
grep -n "path\|folder\|_DB\|dir" pg_data.yml | head                  # PowerGenome data paths: if relative to
                                                                      # $MAIN, stop and decide (copy vs absolute)
```

Environment checks. The headroom pipeline needs `h5py` (VM env `ic-pipeline`). `pg_to_switch` needs
PowerGenome, `typer`, `scipy` and `sklearn` (VM env `switch-pg-reeds-fedpol`). Check each import in its
own env; do not install anything.

**Which env runs each step** (all recipes). `switch-pg-reeds-fedpol` has an old pandas (1.4.4). The case
build and the solve run there, and so do the `s0_workflow` and `build_rate` tests, because that env builds the
cases (`pg_to_switch.py` runs `brc.switch_case` and `brc.turbine_cap` there):

| Step | Env | Why |
|---|---|---|
| git, `cp`, `ls`, `grep` (worktree and data) | Git Bash, no Python | |
| `icsc.cli run` (headroom tables) | `ic-pipeline` | needs `h5py` |
| `brc.cli run` (build-rate tables) | `ic-pipeline` | runs on pandas 1.4.4 too since §52, with the same tables |
| `pytest` (all three suites) | the env with `pytest` | no VM env has both `pytest` and `scikit-learn`: the day-selection test skips with a message where `sklearn` is missing |
| `pytest s0_workflow/tests` and `build_rate/tests` **again** | `switch-pg-reeds-fedpol` | it builds the cases, with pandas 1.4.4: the coal-spec code must run there (the c4a19f8 build failed on a pandas ≥ 2.2 call that the other env accepted) |
| `build_reeds_state_policies.py --check` (C step 1) | `ic-pipeline` | pandas and PyYAML only (runs on pandas 1.4.4 too) |
| `fetch_coal_spec_eia.py --check-only` (C step 1, optional) | `ic-pipeline` | pandas, openpyxl, PyYAML |
| `pg_to_switch.py` (case builds: A, B, C) | `switch-pg-reeds-fedpol` | PowerGenome, `scipy`, `sklearn` |
| `"<SWITCH_EXE>" solve` | `switch-pg-reeds-fedpol` (its `switch`) | Pyomo, Switch, Gurobi |
| `compare_s0_runs.py` (input and output checks) | `ic-pipeline` | pandas and numpy only |
| `measure_run.py` (B) | `switch-pg-reeds-fedpol` | wraps the solve; `psutil` for peak memory if present |
```bash
"<ic-pipeline python>" -c "import h5py, pandas, yaml; print('ok')"
"<switch-pg-reeds-fedpol python>" -c "import powergenome, typer, scipy, sklearn, pyomo, switch_model; print('ok')"
```

Pipeline tables:
```bash
cd "$WT/interconnection_headroom" && "<ic-pipeline python>" -m icsc.cli run --start-year 2026
cd "$WT/build_rate" && "<ic-pipeline python>" -m brc.cli run
cd "$WT"
```

Tests, in the env with `pytest`. Expect all to pass except one skip:
`test_day_selection_targets_and_tails` skips with "scikit-learn is not installed in this env" where
`sklearn` is missing. The toy tests need a Switch source checkout for `SWITCH_SRC`.
```bash
SWITCH_SRC="<switch checkout>" "<pytest env python>" -m pytest -q -rs s0_workflow/tests
(cd interconnection_headroom && SWITCH_SRC="<switch checkout>" "<pytest env python>" -m pytest -q)
(cd build_rate && SWITCH_SRC="<switch checkout>" "<pytest env python>" -m pytest -q)
```

Then the `s0_workflow` and `build_rate` tests in `switch-pg-reeds-fedpol`, the env that builds the cases
(pandas 1.4.4). They must all pass there too, except the same skip. Check `pytest` first; if the import fails, stop and tell Tom
(don't install it):
```bash
"<switch-pg-reeds-fedpol python>" -c "import pytest, pandas; print(pandas.__version__)"
SWITCH_SRC="<switch checkout>" "<switch-pg-reeds-fedpol python>" -m pytest -q -rs s0_workflow/tests
(cd build_rate && SWITCH_SRC="<switch checkout>" "<switch-pg-reeds-fedpol python>" -m pytest -q)
```

## A. Regression: s4x1 2035 new-stack base through the settings (legacy settings)

The regression case stays on the **legacy** settings: ATB-only gas capex, the legacy gas-turbine cap
(451.4 GW + 9.67 GW/yr in service) and the NY RPS buyout at $45.39 only. The `on_pgdays` axis value
pins them, so the October 2026 defaults (CHANGES §45) don't touch it. A test checks that these
settings rebuild the case files byte for byte as before. It also keeps the current state-policy files
(`state_policies.release: legacy`): `emission_policies_fn` stays
`rggi_carbon/emission_policies_current.csv`. In `s0_production_log.txt`, expect
`gas capex: atb_moderate` and `rps acp: {'ESR_NY_rps': 45.39}`. Expect no `cumulative_additions` in
`gas_turbine_cap_params.csv`.

**Compare against:** the hand-built run `icv4B68_s4x1_S0unc_2035_icon_aw2_icv4_br_central_v2_B6B8`
(ic_v4 + B6 + B8 + NY buyout; solve template `s0_workflow/solve/solve_newstack_base_s4x1.bat`).

**New case:** `s4x1_S0prod_2035` (scenario_inputs.csv). It is identical to `s4x1_S0unc_2035_icon`
except `build_rate = central` and `s0_production = on_pgdays`: every S0 production setting, with
PowerGenome's s4x1 days and no chain.

1. Build the case into a fresh folder:
   ```bash
   test -e switch/in/s0prod_regression && echo "exists: pick another name" || \
   "<switch-pg-reeds-fedpol python>" pg_to_switch.py pg/settings switch/in/s0prod_regression --case-id s4x1_S0prod_2035
   ```
   This writes `switch/in/s0prod_regression/2035/s4x1_S0prod_2035/`, plus `s0_production_log.txt` in it
   and `switch/in/s0prod_regression/scenarios_s4x1_S0prod_2035.txt`.

2. Input check against the old case and the aliases it was solved with. Set `OLD` to the old case folder,
   e.g. `switch/in_ictest/cases/2035_icv4/s4x1_S0unc_2035_icon`.
   ```bash
   NEW=switch/in/s0prod_regression/2035/s4x1_S0prod_2035
   "<ic-pipeline python>" s0_workflow/scripts/compare_s0_runs.py inputs $NEW $OLD \
     --alias rps_requirements.csv=rps_requirements.ic_v2.csv --alias ic_zones.csv=ic_zones.ic_v4.csv \
     --alias ic_tranches.csv=ic_tranches.ic_v4.csv --alias ic_uprates.csv=ic_uprates.ic_v4.csv \
     --alias ic_weights.csv=ic_weights.ic_v4.csv --alias ic_params.csv=ic_params.ic_v2.csv \
     --alias build_rate_groups.csv=build_rate_groups.central.v2.csv --alias build_rate_periods.csv=build_rate_periods.central.v2.csv \
     --alias build_rate_tiers.csv=build_rate_tiers.central.v2.csv --alias build_rate_zones.csv=build_rate_zones.central.v2.csv \
     --alias build_rate_regions.csv=build_rate_regions.central.v2.csv --alias build_rate_gens.csv=build_rate_gens.central.v2.csv \
     --alias gen_info.csv=gen_info.ic_v2.coalcfhist.csv --alias gen_build_costs.csv=gen_build_costs.gas_atb.csv \
     --alias variable_capacity_factors.csv=variable_capacity_factors.windloss2.csv > s0prod_regression_inputs.txt
   ```
   Every file should be identical or in another order, except these expected differences:

   | File | Expected difference |
   |---|---|
   | `gen_info.csv` | coal `gen_max_annual_availability`: public EIA-923/860 table instead of PUDL, small differences. Diff `s0_workflow/data/coal_cf_caps_eia923_2021_2024.csv` against B8's `b8_zone_caps.csv` (column `cap_winter`); a zone differing by more than 0.01 needs a look. `gen_can_retire_early` = 1 on existing coal/gas: same as the old case in 2035, because Can_Retire is 1 from 2030. |
   | `gen_build_costs.csv` | new CC/CT overnight cost: ATB native instead of GridLab x B6 factor. Should agree to about 1e-6 relative; larger means PowerGenome's ATB mean differs from the factor's 2031-35 basis. |
   | `max_cap_requirements.csv`, `max_cap_generators.csv` | no `MaxCapTag_GasTurbineSupply` rows (the cap moved to the build-rate module) |
   | `gas_turbine_cap*.csv` (new) | `gas_turbine_cap.csv` 2035 = the old `MaxCapTag_GasTurbineSupply` max_cap_mw for 2035 (557,777.5 MW, or the same predetermined floor). The members of `gas_turbine_cap_gens.csv` = the old tag's `max_cap_generators.csv` members; pg_to_switch logs a warning if they differ. |
   | `retirement_rules.csv` (new) | coal and naturalgas, 2030: no effect in a 2035 case |
   | `scenarios_*.txt` | adds `--include-module study_modules.gen_amortization_period` (as the old solve command) and `study_modules.retirement_rules` |

   `ic_*`, `build_rate_*`, `variable_capacity_factors.csv`, `rps_requirements.csv`, `periods.csv` and
   `timepoints.csv` should be identical.

3. Solve with the same solver options as the old run, into a fresh outputs folder. Use the line in
   `scenarios_s4x1_S0prod_2035.txt` and add the old solve's `--solver-options-string`, `--tempdir`
   and threads. There are no `--input-alias` options: the case files are the solved inputs.
   ```bash
   cd switch
   "<SWITCH_EXE>" solve $(sed -n 1p in/s0prod_regression/scenarios_s4x1_S0prod_2035.txt) \
     --solver-options-string "method=2 crossover=0 BarConvTol=1e-6 ScaleFlag=2 Threads=8" --tempdir /d/tmp
   cd ..
   ```

4. Output check:
   ```bash
   "<ic-pipeline python>" s0_workflow/scripts/compare_s0_runs.py outputs switch/out/s0prod_regression/2035/s4x1_S0prod_2035 \
     <old outputs folder>/icv4B68_s4x1_S0unc_2035_icon_aw2_icv4_br_central_v2_B6B8 --period 2035
   ```
   **Tolerances.** A metric passes within either bound:

   | Metric | Bound |
   |---|---|
   | CO2 | 1% or 5 Mt |
   | coal capacity | 2% or 1 GW |
   | coal generation | 2% or 5 TWh |
   | new CC, CT, onshore wind, offshore wind, solar and storage | 5% or 1 GW each |

   The old run's reference values: CO2 1,226 Mt; coal 77.9 GW, 441 TWh, CF 0.65.
   Only the coal caps differ by construction, so any FAIL outside coal points to a wiring problem.
   Check, in order:
   - the input report;
   - `s0_production_log.txt`;
   - `gas_turbine_cap_results.csv` (cap 557,777.5 MW; binding as before);
   - `ic_headroom.csv` (slack).

## C. Full S0prod_A chain (mode A, 2028-2045, new defaults)

**Case:** `S0prod_A` (`s0_production = on`). Five single-year stages (2028, 2030, 2035, 2040, 2045) on
the October 2026 defaults:
- fleet-independent days;
- central gas capex premium;
- central gas-turbine allowance (cumulative additions since 2025);
- $100/MWh buyouts on every state RPS and CES;
- state RPS/CES targets from ReEDS release 2026.09.21
  (`rggi_carbon/emission_policies_reeds_2026.09.21.csv`; committed, built from the pinned copies);
- retirements before 2030: `block_all` (S0 default; column `retirements_pre2030`). No coal or gas retirement
  before 2030, economic or dated: fedpol's `blocked_2030_coal_gas` push for coal and gas, plus the retirement
  rule. A unit dated 2026-29 is in service in the 2028 and 2030 stages and first gone from 2035. The other
  options (`planned_only`, `unrestricted`) are for sensitivities;
- no new nuclear before the 2035 stage;
- the coal specification rev. 2.1 (`s0_workflow/specs/coal/coal_spec.md` and its addendum): zonal coal caps by stage, the
  fleet overrides from the August 2026 860M, and the S0 order holds (eight units, 2028 stage only);
- wind loss, build rate central, headroom atts_s0.

**The coal checks stop the build.** For each stage, the case build compares four things with the spec's
validation tables (`s0_workflow/specs/coal/`; caps and holds from `by_option/` for the case's
`retirements_pre2030`):
- the zonal caps (±0.001, same rule, H ±1 MW);
- the overrides it applied (same units and actions, effective year exact, MW ±0.1);
- the holds by stage (exact);
- the converted units' heat rates (±0.01).

A mismatch raises an error after writing `coal_caps_by_stage.csv`, `coal_overrides_applied.csv`,
`coal_holds_by_stage.csv`, `coal_removals.csv` and `coal_not_in_model.csv` (and `coal_converted_heat_rates.csv`)
in the stage folder. Don't patch around it: send Tom those files and
`s0_production_log.txt`.

1. Check the pinned state-policy files, then build into a fresh folder. The build writes all five stage
   folders and `scenarios_S0prod_A.txt`. The check needs no VM data: it rebuilds the policy files in
   memory from the pinned ReEDS copies and compares them with the committed ones. It must print nothing
   and exit 0. If it reports a difference, stop and report it; don't overwrite the files.
   ```bash
   "<ic-pipeline python>" s0_workflow/scripts/build_reeds_state_policies.py --check; echo "check exit $?"
   # only if the check printed "check exit 0":
   test -e switch/in/s0prod_A && echo "exists: pick another name" || \
   "<switch-pg-reeds-fedpol python>" pg_to_switch.py pg/settings switch/in/s0prod_A --case-id S0prod_A \
       --year 2028 --year 2030 --year 2035 --year 2040 --year 2045
   ```
   One `--year` per stage. Without them the build takes every model year in the settings and asks for
   2024, 2025 and 2029 as well.
   Optional, to re-verify the committed coal tables from public EIA data (downloads about 250 MB into
   `s0_workflow/data/raw/`, gitignored). It must end with `CHECK OK`:
   ```bash
   "<ic-pipeline python>" s0_workflow/scripts/fetch_coal_spec_eia.py --check-only
   ```
2. Check each stage folder `switch/in/s0prod_A/<year>/S0prod_A/` before solving:
   - `stage_info.csv`: `commit_period` = the year; `next_stage` = the next year (`.` for 2045).
   - `periods.csv`: one period with the span in `s0_production.yml period_spans`.
   - `s0_production_log.txt`: the gas capex premium line. For central with span means:

     | Year | CC | CT |
     |---|---|---|
     | 2028 | +37% | +45% |
     | 2030 | +37% | +45% |
     | 2035 (2031-35) | +18.4% | +22.6% |
     | 2040 | 0 | 0 |
     | 2045 | 0 | 0 |

   - `s0_production_log.txt`: `rps acp: flat $100/MWh on <n> state programs` (54 in 2035: the release ends AZ's RPS after 2025;
     55 with the current file).
   - The build's console log: `state policies from ReEDS 2026.09.21
     (rggi_carbon/emission_policies_reeds_2026.09.21.csv)` for every year.
   - `rps_requirements.csv`, 2035: NC CES 0.392 (0.574 in the current file) and CT RPS 0.33 (0.44).
     NY's RPS and CES are as before. No AZ RPS from 2028 on.
   - `gas_turbine_cap_params.csv`: `gtc_form cumulative_additions`, `gtc_since_year 2025`.
   - `gas_turbine_cap.csv`, the allowance at the stage's year, or the planned additions if those are
     higher (logged):

     | Year | 2028 | 2030 | 2035 | 2040 | 2045 |
     |---|---|---|---|---|---|
     | Allowance (MW) | 24,200 | 49,500 | 155,200 | 270,100 | 385,000* |

     \*2045 is the coordinator estimate (the 2035-40 rate extended), not from the research doc. The
     console log says so.

   - `gas_turbine_cap_gens.csv`: CC weight 0.65 × 1.049 = 0.682, CT and aeroderivative 1.0 × 1.090 =
     1.090, for planned units and new builds alike (nameplate basis); no reciprocating engines.
   - `time_sampling/<year>/fi_target_errors.csv`: every row within tolerance.
   - `retirement_rules.csv`: coal and naturalgas, 2030 (block_all and planned_only; none with unrestricted).
   - **Console log (block_all):** `retirements_pre2030 block_all <case>/<year>: <n> units dated 2026-2029
     encoded 2031 (...)`, broken down by cluster technology (Conventional Steam Coal, the hold technologies,
     Natural Gas Fired Combined Cycle / Combustion Turbine).
     - These units are in service through the 2030 stage and kept by PowerGenome in model year 2030.
     - Expect **no** `predetermined_retirement_override: pushing back ...` line: fedpol's function finds nothing
       left in its window.
   - **Coal removals (every option):** the console log and `s0_production_log.txt` say
     `coal removals: 5 removed before clustering (...)`, and `coal_removals.csv` lists the five: Sandy Creek S01,
     Big Cajun 2-1, Merrimack 2, Warrick 2, Biron Mill GEN5. They are deleted from PowerGenome's unit tables
     before clustering, so fedpol's `blocked_2030` push never sees them (at c4a19f8 it moved them to 2030).
     - Expected breakdown: 4 `deleted from EIA-860 units and 860M new generators` (PowerGenome would otherwise
       add them back from the 860M Operating sheet, where they are OP / OA) and 1, `10234|GEN5`, `deleted from 860M
       new generators`. Biron Mill (plant 10234) is not in `reeds_plant_map.csv`, so it is not among
       PowerGenome's EIA-860 units; PowerGenome adds it from the 860M by location. That is why the hook logged
       4 remove overrides at c4a19f8. Report the breakdown if it differs.
     - Any count other than 5 stops the build.
   - `build_rules.csv`: `uranium`, 2035 (no new nuclear before the 2035 stage).
   - **Coal caps by stage (block_all):** in `coal_caps_by_stage.csv`, every row has `ok` True (checked against
     `by_option/coal_spec_expected_caps_by_stage.block_all.csv`). The log line `coal caps <year>: N ...`
     should match:

     | Stage | N | Zones with model coal | Model coal after overrides (GW) |
     |---|---|---|---|
     | 2028 | 0.5785 | 72 | 163.59 |
     | 2030 | 0.5785 | 72 | 163.59 |
     | 2035 | 0.5998 | 61 | 125.48 |
     | 2040 | 0.6001 | 61 | 123.38 |
     | 2045 | 0.6001 | 61 | 123.38 |

     These are the coal_spec.md addendum A4 values: plants not in `reeds_plant_map.csv` are out, and Edwardsport
     CT1 / CT2 are in at 240.6 MW each. N and the caps are as before.

     - **Blend zone:** p111 in every stage (0.529 / 0.529 / 0.548 / 0.549 / 0.549).
     - **p130 in 2028 and 2030:** Merrimack 1 alone (108 MW), on its own history, cap 0.128. The
       out-of-service Merrimack 2 is removed in every option.
     - **Model-MW differences:** the log lists any zone whose model MW after the overrides differs from the
       expected table by more than 0.1 MW. None are expected (A4). It is reported, not a failure: report the
       list if there is one.
     - **Not in model:** the log line `coal: 81 coal-group units (1629.9 MW) not in model: plant not in
       reeds_plant_map.csv; outside Alaska 68 units, 1470.4 MW by county zone {...}`, and `coal_not_in_model.csv`.
       Accepted for S0 (A4).
     - **860M re-adds:** if the log has `coal: <n> coal-group units PowerGenome added from the 860M`, report the
       list. Those units are in the case's coal clusters but not in the caps' model MW (A4, what to watch).
     - **Coal clusters:** `gen_max_annual_availability` = cap / (1 − forced outage), capped at 1.
     - **Other options:** for a `planned_only` or `unrestricted` sensitivity, the expected values are 2028
       0.5806 / 69 / 156.35 GW and 2030 0.5926 / 65 / 141.14 GW (`by_option/coal_spec_stage_summary.*.csv`).
   - **Applied overrides:** `coal_overrides_applied.csv` has 71 rows (the spec table's S0 rows), all `ok`:
     - 55 plain `ok`;
     - 3 `ok (already satisfied: ...)`: Brandon Shores 1 / 2 (`model 2029`) and Stanton 1 (`model no date`),
       which PowerGenome's fleet already has, so no override is derived;
     - 12 `not in model: plant not in reeds_plant_map.csv` (the pet-coke units and Seadrift Coke);
     - Biron Mill GEN5 `ok (removal: not in PowerGenome's EIA-860 units)`.
     The log should say `55 overrides applied`. Another split with every row `ok` is fine: report it. Converted
     units are in the zone's `other_peaker` cluster, not the coal clusters.
   - **Fleet dates:** the build reads the unedited July 2025 860M; `update_coal_closures.py` (GEM dates) is not
     applied, and the spec uses 860M dates only (A4).
   - **Converted heat rates:** `coal_converted_heat_rates.csv` is all `ok` against
     `coal_spec_converted_gas_units.csv` (rev. 2.1, latest EIA-923, all 2026 ST/NG). The values are North Valmy
     11.42, Montour 10.11, Pawnee 10.96, Harrington 10.83 and James E. Rogers 10.55.
   - **Holds by stage:** `coal_holds_by_stage.csv` is all `ok`.
     - 2028 **and 2030** stages (block_all): eight hold projects, 3,238 MW, named
       `p<zone>_conventional_steam_coal_hold_<plant>_<gen>_1` (Centralia 2, Campbell 1/2/3, Schahfer 17/18,
       Culley 2, Craig 1). With planned_only or unrestricted, 2028 only.
     - In `gen_info.csv`, each has `gen_max_annual_availability` = hold cap / (1 − forced outage), for
       example Campbell 3 0.5539, and `gen_can_retire_early` 0.
     - No hold projects from the 2035 stage (block_all), or from the 2030 stage with the other options.
   - **2045 stage:** the folder `2045/S0prod_A/` exists with a complete case (`gen_info.csv`,
     `loads.csv`, `scenarios` line). The build reports no `flexible_demand_resources` error: the 2045
     load entries are now in the settings.
3. Solve the lines of `scenarios_S0prod_A.txt` in order, each with the solver options of recipe A,
   into fresh output folders. Stages 2-5 read the `*.chained.S0prod_A.csv` files that the previous
   stage's `prepare_next_stage` wrote. Check they exist before starting each stage.
   ```bash
   cd switch
   while read -r line; do "<SWITCH_EXE>" solve $line \
       --solver-options-string "method=2 crossover=0 BarConvTol=1e-6 ScaleFlag=2 Threads=8" --tempdir /d/tmp || break
   done < in/s0prod_A/scenarios_S0prod_A.txt
   cd ..
   ```
4. Report, by stage:
   - CO2, coal GW and TWh, new CC/CT/wind/solar/storage. Use `compare_s0_runs.py outputs` with
     `--period <year>`; the second folder can be the same run, since only the metrics are needed.
   - `gas_turbine_cap_results.csv`: covered MW, cap, dual.
   - `rps_buyout_by_state_year.csv`: buyout MWh and $ by state.
   - `retirement_rules_check.csv`: no coal or gas retirement in 2028; retirements from 2030.
   - `build_rules_check.csv`: `new_mw` 0 for uranium in 2028 and 2030 (blocked), whatever is built from 2035.
   - Coal: generation of the hold projects (2028) against their caps; coal generation and CF by zone
     against `coal_caps_by_stage.csv`.
   - `ic_headroom.csv` slack.
   - Wall time and peak memory per stage (`measure_run.py`).

Optional: build and solve `s4x1_S0prod_2035_new` (the regression case with the new defaults). Compare
it with recipe A's run to see what the new defaults change in 2035 on the same days.

## B. One mode-B window (2028-2030, s4x1): memory and run time — DEFERRED

**Deferred** (Oct 2026): mode A is the production route for now. Keep this recipe for when mode B is
taken up; it builds with the new defaults like `S0prod_A`.

**Case:** `S0prod_B` (`s0_production = on_windows`). Build only 2028 and 2030. That gives one
window, `2028_2030`, with both periods and no next stage. It is the size of every production window:
two periods of 24 fleet-independent days plus the peak day each, about 1,200 timepoints.

1. Build:
   ```bash
   test -e switch/in/s0prod_modeB_test && echo "exists" || \
   "<switch-pg-reeds-fedpol python>" pg_to_switch.py pg/settings switch/in/s0prod_modeB_test --case-id S0prod_B --year 2028 --year 2030
   ```
   Check:
   - `switch/in/s0prod_modeB_test/2028_2030/S0prod_B/` exists;
   - `stage_info.csv` there reads `commit_period 2030`, `next_stage .`;
   - `periods.csv` has 2028 (2026-2028) and 2030 (2029-2030);
   - `time_sampling/2028/` and `time_sampling/2030/` both exist;
   - in `fi_target_errors.csv`, every row is within tolerance;
   - in `fi_info.json`, `relax_factor` is 1 (or the factor applied).

2. Solve under the meter. Wall time and peak RSS of the whole process tree are recorded with psutil
   when the env has it. Otherwise read peak memory from the PowerShell fallback below.
   ```bash
   cd switch
   "<switch-pg-reeds-fedpol python>" ../s0_workflow/scripts/measure_run.py --log ../s0prod_modeB_measure.json -- \
     "<SWITCH_EXE>" solve $(sed -n 1p in/s0prod_modeB_test/scenarios_S0prod_B.txt) \
     --solver-options-string "method=2 crossover=0 BarConvTol=1e-6 ScaleFlag=2 Threads=8" --tempdir /d/tmp
   cd ..
   ```
   PowerShell fallback for peak memory, run while the solve runs:
   `Get-Process python,gurobi* | Select Name,Id,@{n='PeakGB';e={$_.PeakWorkingSet64/1GB}}`

3. Report:
   - wall time and peak memory (`s0prod_modeB_measure.json`);
   - Gurobi's model size (rows, columns, nonzeros) and barrier time from the solver log;
   - CO2 and new builds by period (`compare_s0_runs.py outputs` with `--period 2028` and `--period 2030`
     against the single-year 2028 and 2030 runs, if there are any).

   The foresight memory estimate from the test runs was about 65 MB per timepoint (3 periods x about
   625 timepoints needed about 120 GB). Two windows' worth of timepoints should need roughly 80 GB;
   compare the measured peak with that.

**Full mode-B chain** (deferred, after B): build `S0prod_B` for all five years into a fresh folder. Solve the lines of `scenarios_<case>.txt` in order. Each stage's
`prepare_next_stage` writes the next stage's `*.chained.<case>.csv` from what the stage committed.

## Renames to carry into VM scripts

The CHANGES §42 renames still apply:

| Old name | New name |
|---|---|
| `new_line` | `conv_reinforcement` |
| `new_line_mode` | `reinforcement_mode` |
| `new_line_network_mw_implied` | `hosted_network_mw_implied` |

New in this branch:
- `gas_turbine_cap_results.csv` replaces the `MaxCapTag_GasTurbineSupply` rows in `max_cap_*`.
- The headroom `ic_*_built.csv` files have one row per period (filter on `period`).
