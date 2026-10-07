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
| `build_reeds_forced_tx.py --check` (C step 1) | `ic-pipeline` | pandas only; reuses the committed zones (endpoint mapping needs pyshp/shapely/pyproj, not installed: fine) |
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

## C. Full S0prod_A chain (mode A, 2028-2045, final S0 configuration)

**§66 (final S0 configuration):** S0prod_A now has `forced_tx = reeds_certain_plus_A` and `tx_bill = s0_tx` (the
S0_tx baseline: it equals `S0_tx`), the guaranteed stress-day rule, the lifetime backstop, retirement friction 0.5,
Virginia in RGGI, the CA/WA imports generators and fixed O&M by period. The new checks are in "§66 checks" at the end
of step 2; the forced-transmission checks below for `reeds_certain` now apply to the certain lines plus the class-A
projects (recipe G has the S0_tx checks).

**§70:** the stress days are now the greedy 12-day cover at 6% (in place of §66's guaranteed rule and §68's
`cover_plus_interconnect_wind`), with light stress days and compact reserve rows (recipe I's result).

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
- forced transmission (`forced_tx: reeds_certain`, CHANGES §54): ReEDS 2026.09.21's certain additions only,
  SunZia p28-p31 3,000 MW (2028 stage) and TransWest Express p24-p25 3,000 MW (2035 stage), instead of the
  72-line project list (245,857 MW); each forced line is limited to its minimum in its forced period;
- the regional planning reserve (`prm_design = regional`, CHANGES §55): 16 reserve regions on 10-12 zero-weight stress
  days per stage, instead of the per-zone requirement and the extreme-day block (recipe E has the details and the
  checks);
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
   "<ic-pipeline python>" s0_workflow/scripts/build_reeds_forced_tx.py --check; echo "check exit $?"
   # only if both checks printed "check exit 0":
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
   - **Planning reserve (regional):** as recipe E steps 2-3. Per stage: `prm/<year>/stress_days.csv`; the
     `prm_*.csv` inputs; no `planning_reserve_margin.csv`; the scenario line excludes `planning_reserves` and
     `planning_reserves_extreme_days` and includes `prm_regional`.
   - **RGGI (§62):** recipe H on each stage folder; the 3PR floor and both CCR tiers for the stage's year.
   - **Forced transmission (reeds_certain):** console log `forced transmission reeds_certain
     (pg/extra_inputs/transmission/forced_tx_reeds_certain_2026.09.21.csv); forced-line expansion limit minimum`.
     - `trans_build_minimum.csv`: one row in the 2028 stage (the p28-p31 line, 3,000 MW) and one in the 2035 stage (the
       p24-p25 line, 3,000 MW); **no file** in 2030, 2040 and 2045. Before §57 every stage re-forced every line dated
       at or before it (2028: SunZia; 2030: SunZia; 2035-2045: SunZia + TransWest). If a stage still shows that, the
       build predates §57: rebuild.
     - Scenario lines: the 2035 stage's `--input-aliases` include
       `trans_build_minimum.csv=trans_build_minimum.chained.S0prod_A.csv` and the same for
       `trans_path_expansion_limit.csv`. No other stage aliases them. Every stage after 2028 aliases
       `trans_built_to_date.csv=trans_built_to_date.chained.S0prod_A.csv`; `prm_regional` keeps earlier stages' new
       lines at 85% for reserves.
     - `trans_path_expansion_limit.csv`: those two lines are now in the file. Each has 3,000 in its forced stage and the
       case's limit (0, `trans_expansion: zero`) in the other stages.
     - `transmission_lines.csv`: p24-p25 is added as a new line (666.3 km), and the named projects' other new corridors
       are absent. Cross-transreg corridors of the old list are no longer exempt from the block
       (`trans_new_build_allowed` 0, e.g. p37-p38).
     - **After solving (each stage):**
       - `BuildTx.csv`: p28-p31 has 3,000 MW in 2028 only and p24-p25 has 3,000 MW in 2035 only, nothing on
         either in other stages.
       - In the 2035 stage folder, `trans_build_minimum.chained.S0prod_A.csv` still says 3,000 for p24-p25
         (nothing built on it before).
       - `trans_built_to_date.chained.S0prod_A.csv` in the 2045 folder has p28-p31 3,000 and p24-p25 3,000, plus any
         economic builds on other lines.
       - **Report both lines' totals.**
   - **Coal caps by stage (block_all):** in `coal_caps_by_stage.csv`, every row has `ok` True (checked against
     `by_option/coal_spec_expected_caps_by_stage.block_all.csv`). The log line `coal caps <year>: N ...`
     should match:

     | Stage | N | Zones with model coal | Model coal after overrides (GW) |
     |---|---|---|---|
     | 2028 | 0.5791 | 72 | 163.59 |
     | 2030 | 0.5791 | 72 | 163.59 |
     | 2035 | 0.6007 | 61 | 125.48 |
     | 2040 | 0.6011 | 61 | 123.38 |
     | 2045 | 0.6011 | 61 | 123.38 |

     These are the coal_spec.md addendum A4 and A5 values. Plants not in `reeds_plant_map.csv` are out of the model
     coal (A4) and of each zone's history and N (A5), and Edwardsport CT1 / CT2 are in at 240.6 MW each. The
     zone caps that move with A5 are listed there, e.g. p70 0.6210, p99 0.1229.

     - **Blend zone:** p111 in every stage (0.5295 / 0.5295 / 0.5493 / 0.5497 / 0.5497).
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
       0.5812 / 69 / 156.35 GW and 2030 0.5934 / 65 / 141.14 GW (`by_option/coal_spec_stage_summary.*.csv`).
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
   - **§66 checks (each stage):**
     - **Stress days (§70: `greedy` at 6%, light, compact):**
       - `prm/<year>/stress_info.txt` says `rule greedy; cover tolerance 0.060` (higher only if 12 days did not
         suffice: report it).
       - `stress_coverage.csv`: 48 region rows, no `NOT COVERED` (no interconnection rows: that is the
         `cover_plus_interconnect_wind` option).
       - Each stage folder has `stress_light_timeseries.csv` listing the same timeseries as `prm_timeseries.csv`, and
         `prm_params.csv` has `prm_compact_capacity` 1. The build log line ends `stress-day formulation light; reserve
         rows compact`.
       - **Report** the number of stress days and the console line `Model size <year>: <n> timepoints (<a> sample,
         <b> on <c> stress days)`.
       - **Expected:** 600 sample timepoints; at most 12 stress days, so at most 888 timepoints.
     - **Lifetime backstop:** `lifetime_retirements_by_stage.csv` and the log line `lifetime backstop (coal 65 yr,
       gas 55 yr ...)`. Coal GW out of service by lifetime (block_all; the committed model basis): 0 / 0 / 12.78 /
       37.06 / 61.92 for 2028 / 2030 / 2035 / 2040 / 2045. The build's own unit table may differ slightly (860M
       re-adds; capacity column): report both rows, coal and gas, for every stage. No unit in
       `lifetime_retirements.csv` has `to_year` below 2031 (block_all holds everything due by 2030 through the 2030
       stage). The coal caps and spec checks are unchanged (they are on the fleet before the backstop).
     - **Retirement friction:** `retirement_friction.csv`: coal and naturalgas, `rf_fraction` 0.5, `rf_from_period`
       2030; log line `retirement friction: retiring existing ... avoids 50% of its fixed O&M`. The scenario line
       includes `retirement_rules`.
     - **Fixed O&M by period:** `existing_fom_by_period.csv` lists the existing-only generators; from the 2030 stage
       on, the solve log has `fixed O&M of <n> existing generators from the next stage's own gen_build_costs.csv`
       (printed by the previous stage's `prepare_next_stage`). **Report** n per stage.
     - **Transmission (S0_tx):** `tx_cap_periods.csv` 0.0 in 2028 and 1.4 from 2030 (no-bill the same);
       interregional lines have `trans_path_expansion_limit` 0 rows in every stage before 2040; the scenario line
       includes `tx_build_cap`. Recipe G has the rest.
     - **Virginia and imports generators:** recipe H.
     - **Fuel prices (§67, steo_aeo):** `fuel_prices_by_stage.csv`; log line `fuel prices steo_aeo (...)`. The national
       2024 $/MMBtu by stage should read gas 3.46 / 3.73 / 4.44 / 5.06 / 5.01 and coal 2.32 / 2.34 / 2.39 / 2.46 / 2.43
       (2028 / 2030 / 2035 / 2040 / 2045). In `fuel_cost.csv`, every US zone's naturalgas and coal rows differ from the
       hist5_high_gas build; distillate and uranium don't. **Report** any `zones without an EMM region` in the log
       line (non-US zones only expected).
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
   - §66: `retirement_rules_check.csv` `suspended_mw` and `friction_cost_per_yr` by source and period; coal and gas
     GW retired by lifetime per stage (from the build); the stress-day count and timepoints per stage. **Expected
     time and memory** (§70): about 1 h 45 min and 80 GB per stage, as the VM's 2035 light + compact test (1 h 43
     min, 79.7 GB; full + hourly was 2 h 50 min, 102.7 GB). Later stages carry more new-build columns; **report** any
     stage above 100 GB. Compare with the measured peak.

Optional: build and solve `s4x1_S0prod_2035_new` (now the 2035 stage on the final configuration, `on_single`; the
same inputs as `s4x1_S0_tx_2035`). Compare it with recipe A's run. The retirement sensitivities on S0_tx 2035:
`s4x1_S0_tx_2035_life60`, `_life70`, `_nofriction` (column `retirement_sens`).

## C-fix. The S0prod_A chain already built at 1eab1ac: forced transmission without a rebuild

The build at 1eab1ac re-forces forced lines in every later stage. Each stage's `trans_build_minimum.csv` has every
certain line dated at or before it, with the expansion limit capped at that minimum. Solved as built, SunZia
would be built 5 times and TransWest 3 times. `fix_forced_tx_aliases.py` writes the files a fixed build would
write as `*.fixed.csv` aliases (CHANGES §58). Don't rebuild.

1. In the worktree at the current head (the script only reads the stage folders and the forced-line table):
   ```bash
   "<ic-pipeline python>" s0_workflow/scripts/fix_forced_tx_aliases.py switch/in/s0prod_A --case S0prod_A \
     --forced-tx reeds_certain --trans-expansion zero
   ```
   It writes:
   - `trans_build_minimum.fixed.csv` and `trans_path_expansion_limit.fixed.csv` in the stage folders;
   - `scenarios_S0prod_A.fixed.txt`, `forced_tx_aliases.S0prod_A.csv` and `forced_tx_fix_report.S0prod_A.csv` next
     to `scenarios_S0prod_A.txt`.

   It changes nothing else, and it stops if any of those files exists (`--overwrite` replaces them). Expected output:
   ```
   S0prod_A: 5 stages, chain [2028, 2030, 2035, 2040, 2045]; 6 re-forced minimum rows removed; ...
     2028: --input-aliases trans_build_minimum.csv=trans_build_minimum.fixed.csv trans_path_expansion_limit.csv=trans_path_expansion_limit.fixed.csv
     2030: --input-aliases trans_build_minimum.csv=none trans_path_expansion_limit.csv=trans_path_expansion_limit.fixed.csv
     2035: --input-aliases trans_build_minimum.csv=trans_build_minimum.fixed.csv trans_path_expansion_limit.csv=trans_path_expansion_limit.fixed.csv
     2040: --input-aliases trans_build_minimum.csv=none trans_path_expansion_limit.csv=trans_path_expansion_limit.fixed.csv
     2045: --input-aliases trans_build_minimum.csv=none trans_path_expansion_limit.csv=trans_path_expansion_limit.fixed.csv
   ```
   **Report** `forced_tx_fix_report.S0prod_A.csv`. It should keep 2 rows (p28-p31 in 2028, p24-p25 in 2035, 3,000 MW
   each) and remove 6: p28-p31 in 2030, 2035, 2040 and 2045, and p24-p25 in 2040 and 2045. Each removed row's limit
   goes from 3,000 to 0. If the script stops ("not in the built ..."), report its message.
2. Solve with the fixed lines, which are the built lines with the two aliases added to each stage's
   `--input-aliases`:
   ```bash
   cd switch
   while read -r line; do "<SWITCH_EXE>" solve $line \
       --solver-options-string "method=2 crossover=0 BarConvTol=1e-6 ScaleFlag=2 Threads=8" --tempdir /d/tmp || break
   done < in/s0prod_A/scenarios_S0prod_A.fixed.txt
   cd ..
   ```
   **run_chain_A.py** (VM-only, not in the repo): point it at `scenarios_S0prod_A.fixed.txt`. If it builds its own
   alias list, append each stage's `aliases` column of `forced_tx_aliases.S0prod_A.csv` to that stage's
   `--input-aliases`. Each stage's console log shows `Applying alias ...trans_build_minimum.csv=...` (none for
   `=none`).
3. Check after solving: `BuildTx.csv` has p28-p31 3,000 MW in 2028 only and p24-p25 3,000 MW in 2035 only, with
   nothing on either line in any other stage.

## D. Forced transmission: 2035 s4x1 pair (reeds_certain vs named_projects)

**Cases:** `s4x1_S0prod_2035_txreeds` (`forced_tx = reeds_certain`) and `s4x1_S0prod_2035_txnamed`
(`forced_tx = named_projects`). They are identical to each other except `forced_tx`, and to
`s4x1_S0prod_2035_new` (new defaults, PowerGenome's s4x1 days, single year 2035) except the case id. Both cap
forced lines at their minimum.

1. Build both into fresh folders (one `--year`, so each is a single 2035 stage):
   ```bash
   for c in txreeds txnamed; do
     test -e switch/in/s0prod_$c && { echo "exists: pick another name"; break; }
     "<switch-pg-reeds-fedpol python>" pg_to_switch.py pg/settings switch/in/s0prod_$c --case-id s4x1_S0prod_2035_$c --year 2035
   done
   ```
2. Input check: only the transmission files should differ.
   ```bash
   "<ic-pipeline python>" s0_workflow/scripts/compare_s0_runs.py inputs \
     switch/in/s0prod_txreeds/2035/s4x1_S0prod_2035_txreeds switch/in/s0prod_txnamed/2035/s4x1_S0prod_2035_txnamed > s0prod_tx_inputs.txt
   ```
   - **trans_build_minimum.csv:** in a single 2035 stage, every forced line is due in 2035. txreeds has 2 rows,
     6,000 MW (SunZia p28-p31, TransWest Express p24-p25). txnamed has 72 rows, 245,857 MW.
   - **trans_path_expansion_limit.csv:** each forced line at its minimum.
   - **transmission_lines.csv:** txnamed has the named projects' new corridors (injected with no existing capacity);
     txreeds has only p24-p25 among them. `trans_new_build_allowed` differs on the old list's cross-transreg
     corridors.
   - Any other difference is unexpected: report it.
3. Solve each scenario line with recipe A's solver options, into fresh output folders.
4. Report for each case: total and interregional new transmission (MW, MW-km), CO2, new wind / solar / storage /
   gas by transreg, and system cost. Compare with `pg/extra_inputs/transmission/forced_tx_comparison_2026.09.21.csv`
   (forced MW, MW-km and interregional share by period for both options).

## E. Regional planning reserve: 2035 s4x1 case vs s4x1_S0prod_2035_txreeds

**Case:** `s4x1_S0prod_2035_prm` (`prm_design = regional`). It is identical to `s4x1_S0prod_2035_txreeds` (recipe D,
per-zone reserve) except `prm_design`. Design: `Guides and documentation/s0_production.md`, "Planning reserve
requirement".

1. Build into a fresh folder (txreeds is built in recipe D; reuse that build if it exists):
   ```bash
   test -e switch/in/s0prod_prm && echo "exists: pick another name" || \
   "<switch-pg-reeds-fedpol python>" pg_to_switch.py pg/settings switch/in/s0prod_prm --case-id s4x1_S0prod_2035_prm --year 2035
   ```
2. **Build log and stage folder** `switch/in/s0prod_prm/2035/s4x1_S0prod_2035_prm/`:
   - The console says `Added <n> zero-weight stress days (2035).` with n between 10 and 12. The "Add extreme day"
     script does not run, and there is no `planning_reserve_margin.csv`.
   - `prm/2035/stress_days.csv`: one row per day, with date, season and the needs it covers.
     `prm/2035/stress_coverage.csv`: all 48 needs (16 regions × summer peak, winter peak, low wind/solar) covered,
     no `NOT COVERED`. `stress_info.txt` gives the tolerance used (0.02 if 12 days sufficed). **Report the list.**
   - `timeseries.csv`: the stress timeseries `2035_pN_prm`, with `ts_scale_to_period` 0. Their timepoints start
     with 9.
   - `prm_zones.csv` has 134 zones in 16 regions. `prm_margin_basis.csv` has RML, FOR_w, margin and import share by
     region. The 2035 margins should be close to the s0_production.md table (PJM about 0.198, MISO 0.024, ERCOT
     0.077), since that table was computed on the same kind of fleet.
   - `prm_gen_availability.csv`: thermal units in the stress hours. A winter-day CT is 0.801 and a summer-day CT
     0.934 (seasonal method).
   - `prm_params.csv`: `prm_new_tx_derate` 0.15, `prm_shortfall_cost_per_mw_yr` 271785.19.
   - `s0_production_log.txt`: the line `prm regional: 16 regions; margins ...`.
   - `scenarios_s4x1_S0prod_2035_prm.txt`: the line has `--exclude-module study_modules.planning_reserves
     --exclude-module study_modules.planning_reserves_extreme_days --include-module study_modules.prm_regional`.
3. **Input check against txreeds:**
   ```bash
   "<ic-pipeline python>" s0_workflow/scripts/compare_s0_runs.py inputs \
     switch/in/s0prod_prm/2035/s4x1_S0prod_2035_prm switch/in/s0prod_txreeds/2035/s4x1_S0prod_2035_txreeds > s0prod_prm_inputs.txt
   ```
   - **Expected differences:**
     - the time files: `timeseries.csv`, `timepoints.csv`, `loads.csv`, `variable_capacity_factors.csv`,
       `hydro_*`, `water_node_tp_flows.csv`, `graph_timestamp_map.csv`, `dr_data.csv` and `ee_data.csv`. prm has the
       stress days; txreeds has the extreme-day copy of the national peak block;
     - the new `prm_*.csv` files;
     - `planning_reserve_margin.csv`, in txreeds only;
     - the scenario line.
   - The sampled (weighted) days should be identical, and so should `dr_annual_cost.csv`. Before §63 it rose in
     107 of 134 zones because the DR cost took its peak over the stress days too. Anything else is unexpected:
     report it.
4. Solve both with recipe A's solver options, into fresh output folders (the same command as recipe D step 3).
   Gurobi's barrier without crossover still returns duals; `prm_summary.csv` says `duals_available`.
5. Report for `s4x1_S0prod_2035_prm`:
   - `prm_shortfall.csv`: MW by region; expect 0 or small. A large shortfall means a region can't build enough.
   - `prm_summary.csv`: by region, the target margin, minimum margin achieved and at which stress hour, maximum
     import use (1.0 = at the cap), and reserve price ($/kW-yr).
     - **Regional price:** the peak-load-weighted mean of the zone prices in `prm_zone_prices.csv`. Each zone price
       is the sum of its stress-hour duals, at most the shortfall penalty ($271.8/kW-yr) and equal to it where a zone
       is short. Any price above the penalty means a build from before §64.
     - **`dr` row:** it appears only when demand response is on (`s0_production.demand_response.enabled`; off by
       default). Class `flex_load` is PowerGenome's switched-off load curtailment, with no credit.
   - `prm_capacity_credit.csv`: implied capacity credit by class and region, next to the PJM, NYISO, ISO-NE and SPP
     columns.
   - `prm_region_hours.csv`: which stress days bind. A price on a day means it binds.

   Against txreeds: new gas CC/CT, storage, wind and solar by region; coal and gas retirements; CO2; system
   cost; interregional transmission builds. txreeds's per-zone margins (8%) and its extreme-day block versus the
   regional margins and stress days explain most differences.

## F. Time sample: 24 single days vs 4x3 blocks (2035 s4x1, today's reserve)

**Cases:** `s4x1_S0prod_2035_fi24` (`time_sample = days24`) and `s4x1_S0prod_2035_fi4x3` (`time_sample = blocks4x3`).
They are identical except `time_sample`, so the sample is the only difference. Both use:
- the fleet-independent selector (`s0_production = on_single`);
- single year 2035 on the s4x1 stack;
- today's (legacy) reserve: the per-zone margin plus the extreme-day copy of the peak day.

Each differs from `s4x1_S0prod_2035_txreeds` (recipe D) only in `s0_production`. Design: `Guides and
documentation/s0_production.md`, "Time sampling: multi-day blocks".

1. Build both into fresh folders:
   ```bash
   for c in fi24 fi4x3; do
     test -e switch/in/s0prod_$c && { echo "exists: pick another name"; break; }
     "<switch-pg-reeds-fedpol python>" pg_to_switch.py pg/settings switch/in/s0prod_$c --case-id s4x1_S0prod_2035_$c --year 2035
   done
   ```
   The block search takes about 20-60 s more than the day selection.
2. **Build log and stage folders** `switch/in/s0prod_<c>/2035/s4x1_S0prod_2035_<c>/`:
   - **Console:** `Fleet-independent day selection (24 days, 2035)` for fi24 and `(4 x 3-day blocks, 2035)` for
     fi4x3. If fi4x3 also logs `block sample 4x3: ... relaxed x<f>`, **report f**.
   - **`time_sampling/2035/fi_info.json`:**
     - fi4x3: `sample` 4x3; `relax_factor` (1.0 = no relaxation); `relaxed`; `min_relax_lower_bound`;
       `n_candidate_sets` (about 7,000).
     - fi24: `relax_factor`.
     - **Report all of them.**
   - **`fi_target_errors.csv`:** every row `within`. Report the largest `error / base tolerance` (fi4x3) and each
     case's largest absolute error by target type.
   - **`fi_tail_shares.csv`:** both tails inside their bands. Report the shares.
   - **`fi_days_selected.csv`:** fi4x3 has 4 three-day blocks plus the peak day. **Report the dates and weights**
     (`weight_days_per_yr`, `occurrences_per_yr`). Its column `start_day` is the block's first day in 2007-2013.
   - **`timeseries.csv`:**

     | | fi24 | fi4x3 |
     |---|---|---|
     | Sampled timeseries | 25 × 24 h (`2035_pN`) | 4 × 72 h (`2035_pNx3`) and 1 × 24 h |
     | Extreme-day copy | 1 × 24 h, `_prm`, weight 0 | 1 × 24 h, `_prm`, weight 0 |
     | Timepoints | 624 | 336 |

     fi24 may be lower if a tail day is also a medoid. The copy is the timeseries holding the system-peak hour of
     `loads.csv`, normally the single peak day. If fi4x3's copy is 72 h, that hour fell in a block: report it. The timepoints come from `wc -l timepoints.csv` minus 1;
     **report both counts**. `sum(ts_scale_to_period × ts_num_tps / 24)` over the non-copy rows is 365 × 5 (the
     stage's 5 years) in both.
3. **Input check:**
   ```bash
   "<ic-pipeline python>" s0_workflow/scripts/compare_s0_runs.py inputs \
     switch/in/s0prod_fi4x3/2035/s4x1_S0prod_2035_fi4x3 switch/in/s0prod_fi24/2035/s4x1_S0prod_2035_fi24 > s0prod_fi_inputs.txt
   ```
   Only the time files (as in recipe E step 3) and the `time_sampling/` diagnostics should differ. Anything else is
   unexpected: report it.
4. Solve both with recipe A's solver options, into fresh output folders (the same command as recipe D step 3).
   **Report:**
   - solve time and peak memory for each;
   - the LP size from the Gurobi log (rows, columns, nonzeros).
5. **Report, fi4x3 against fi24:**
   - new gas CC/CT, storage (MW and MWh, and duration), wind and solar by transreg;
   - coal and gas retirements;
   - CO2;
   - system cost;
   - interregional transmission;
   - storage cycling: annual discharge / energy capacity.

   Chronology mostly affects storage duration and hydro. A large shift in long-duration storage or gas means 3-day
   carry-over matters. A shift in wind or solar means the four blocks miss the targets (check `fi_target_errors.csv`
   first).

## G. Transmission-bill scenarios: 2035 test versions (then the mode-A chains)

**Prerequisite:** the class-A list in `pg/extra_inputs/transmission/forced_tx_status_review.csv`. Until it has
class-A rows, every S0_tx / BILL case stops at the build with "placeholder until the status-review list is supplied".
Design: `Guides and documentation/transmission_bill_scenarios.md`.

**Cases:** `s4x1_S0_tx_2035`, `s4x1_BILL_central_2035`, `s4x1_BILL_low_2035`, `s4x1_BILL_high_2035`,
`s4x1_BILL_central_txonly_2035`, `s4x1_BILL_central_bronly_2035`, `s4x1_BILL_central_S1_2035`.

1. Build each into a fresh folder (`--year 2035`, single stage), as in recipe D step 1.
2. **Build log and folder:**
   - `s0_production_log.txt`: `tx_policy national_cap: N intra-region and M interregional lines (K unblocked ...),
     ... moratorium: interregional from <2040|2035> ...; cap TW-mi/yr 2035: <1.4|3.0|2.0|4.0> (no-bill 1.4)`.
   - `tx_cap_periods.csv`, `tx_cap_lines.csv` and `tx_cap_exempt.csv`. The exempt file lists every forced line in
     2035: the ReEDS certain lines plus the class-A projects.
   - `trans_path_expansion_limit.csv`:
     - S0_tx and bill low: 0 for every interregional line except forced ones;
     - bill central and high: 0 only for ERCOT ties.
   - The scenario line includes `study_modules.tx_build_cap`.
   - Bill cases except bronly: `prm_params.csv` has `prm_import_new_tx_allowance` 0.85.
   - Headroom uprates in 2035:
     - atts_reform: BILL_central, bronly and S1;
     - atts_planned: BILL_low;
     - atts_reform_techmax: BILL_high;
     - atts_s0: S0_tx and txonly.
   - Build rate in 2035: reform for central, bronly, S1 and high; central for low, txonly and S0_tx.
3. Solve with recipe A's solver options.
   - RGGI: recipe H on the stage folder (3PR floor and CCR for 2035).
4. **Report** for each case:
   - `tx_build_cap.csv`: intra-region and interregional TW-mi/yr against the cap, and the dual;
   - interregional builds by transreg pair;
   - `prm_summary.csv` import use;
   - new gas/wind/solar/storage;
   - CO2 and system cost.

   Then run S0_tx against each bill case, and the txonly and bronly decomposition.
5. **Mode-A chains** (`S0_tx`, `BILL_*`): as recipe C. In bill central from the 2035 stage (low from 2040; high from
   2030 and again in 2035), the stage folder has `ic_scenario_switch.csv`. The previous stage's
   `prepare_next_stage` writes `ic_uprates.chained.<case>.csv` with the new scenario's caps less what was built.

## H. RGGI check on a built S0 case (any stage folder)

Every S0 case except the regression case should carry the 3PR floor and both CCR tiers for its model year (CHANGES
§62). Before §62, cases on `S0_uncapped` (all S0 cases) had neither. Run it on each stage folder of S0prod_A
(recipe C), on the recipe G cases, and on the regression case (which should have neither):
```bash
"<ic-pipeline python>" - "switch/in/<build>/<stage>/<case>" <<'PY'
import sys, pandas as pd
d = sys.argv[1]
c = pd.read_csv(f"{d}/carbon_policies_regional.csv")
e = c[c.CO2_PROGRAM == "ETS 1"]
VA = ["p99", "p100", "p118", "p124"]
for p, g in e.groupby("PERIOD"):
    print(p, "cap", round(g.carbon_cap_tco2_per_yr.sum()), "of which VA", round(g[g.LOAD_ZONE.isin(VA)].carbon_cap_tco2_per_yr.sum()),
          "floor", sorted(set(g.carbon_floor_price_dollar_per_tco2)))
print(pd.read_csv(f"{d}/carbon_policies_ccr.csv").to_string(index=False))
import os
if os.path.exists(f"{d}/rggi_va_budget.csv"):
    print(pd.read_csv(f"{d}/rggi_va_budget.csv").to_string(index=False))
om = f"{d}/gen_om_by_period.csv"
gi = pd.read_csv(f"{d}/gen_info.csv")
imp = gi[gi.gen_tech.astype(str).str.lower().str.contains("imports") & gi.gen_load_zone.isin(["p1", "p3", "p11"])]
o = pd.read_csv(om, na_values=["."])
print(o[o.GENERATION_PROJECT.isin(imp.GENERATION_PROJECT)].to_string(index=False))
PY
```
Expected (S0 before §66, RGGI10 only; the 2035 comparison rows still look like this except for Virginia, below):

| Period | ETS 1 cap (t) | Floor | CCR rows (tier: pool t, trigger $) |
|---|---|---|---|
| 2028 | 55,411,816 | 10.62 | 1: 10,656,120, 23.01; 2: 10,656,120, 34.50 |
| 2030 | 39,579,868 | 12.15 | 1: 26.34; 2: 39.50 |
| 2035 | 12,520,768 | 17.04 | 1: 36.93; 2: 55.39 |
| 2040 | 8,191,313 | 23.90 | 1: 51.80; 2: 77.69 |
| 2045 | 8,191,313 | 33.52 | 1: 72.65; 2: 108.96 |

- **Floor and CCR:** every ETS 1 zone row has the same floor. Both CCR tiers are pooled at 10,656,120 t.
- **Cap:** the sum of the zone caps equals the cap shown.
- **Regression case (`s4x1_S0prod_2035`):** floor 0 and an empty `carbon_policies_ccr.csv`, as before.
- **Cases built before §62:** a floor of 0 or no CCR rows on an S0 case means it was built before §62. Rebuild it.
- **California-Washington (§65):**
  - **Price:** ETS 2 and ETS 3 rows have `carbon_cost_dollar_per_tco2` = the linked price: 48.6, 53.4, 67.8, 86.0 and
    109.2 for 2028–2045 (central); 33.43 in the regression case.
  - **`trans_import_cost.csv`:** rows only into p1–p4 / p8–p11 from outside them, at price × 0.437 (WA) or × 0.428 (CA).
  - **After solving:** `trans_import_cost_results.csv` gives the delivered MWh and the cost.
- **Virginia (§66; every S0 case but the regression case):** ETS 1 also has p99, p100, p118 and p124, each with a
  quarter of Virginia's budget; the floor is the same on every row, and each CCR tier grows by 10% of the budget:

  | Period | ETS 1 cap (t) | of which Virginia | CCR pool per tier (t) |
  |---|---|---|---|
  | 2028 | 71,615,428 | 16,203,612 (4,050,903 per zone) | 12,276,481 |
  | 2030 | 51,153,877 | 11,574,009 | 11,813,521 |
  | 2035 | 16,182,111 | 3,661,343 | 11,022,254 |
  | 2040 | 10,586,630 | 2,395,317 | 10,895,652 |
  | 2045 | 10,586,630 | 2,395,317 | 10,895,652 |

  The ETS 1 cap is the RGGI10 cap (the table above) + Virginia's. `rggi_va_budget.csv` lists the budget in short
  tons: 17,861,420 / 12,758,157 / 4,035,939 / 2,640,384 / 2,640,384. **Report** the combined cap by period. A build
  that stops with "already in ETS 1 ... counted twice" means the case also has `rggi_va_fraction` (RGGI10+VA): report
  it. The regression case has no Virginia rows.
- **Imports generators (§66):** `gen_om_by_period.csv` has `gen_variable_om_by_period` on p11's imports generator(s):
  20.8008 (2028), 22.8552 (2030), 29.0184 (2035), 36.808 (2040), 46.7376 (2045) $/MWh (central × 0.428). There are no
  rows for p1 and p3 (Canada at 0). The build log line `ca_wa_carbon imports generators: ...` names them; **report**
  the generator names.

## I. Light stress days and compact reserve rows: 2035 s4x1 (CHANGES §69-§70) — DONE

**Result** (VM, 0a29884, 2035, greedy stress days): light + compact 1 h 43 min and 79.7 GB, against full + hourly
2 h 50 min and 102.7 GB. Results within about 1% (CO2 +10 Mt, new solar −8 GW, new CT −1.7 GW, reserve prices 0-23%
lower), explained by removing spinning reserves, minimum load and commitment from stress days, where the NERC margins
already include operating reserves. Compact alone: same optimum, 50 more barrier iterations. Ramping off: no gain.
Light + compact (with the greedy rule at 6%) is the S0 default since §70.

**To repeat the comparison** (same row except `prm_design`):
- `s4x1_S0prod_2035_new`: the S0 default (light + compact);
- `s4x1_S0prod_2035_new_full` (`regional_full`): full stress days, hourly reserve rows (S0 before §70).

`s4x1_S0prod_2035_new_light`, `_compact` and `_light_compact` remain (the first and last now build the same inputs as
`s4x1_S0prod_2035_new`).

1. **Build** each into a fresh folder:
   ```bash
   for c in s4x1_S0prod_2035_new s4x1_S0prod_2035_new_full; do
     test -e switch/in/s0light/$c && echo "exists: pick another name" || \
     "<switch-pg-reeds-fedpol python>" pg_to_switch.py pg/settings switch/in/s0light --case-id $c --year 2035
   done
   ```
   Only the default folder has `stress_light_timeseries.csv` and `prm_compact_capacity = 1`; every other input file is
   byte-identical (`diff -rq`, excluding `s0_production_log.txt` and `scenarios*.txt`). **Report** any difference.
2. **Solve** both with recipe A's solver options and `measure_run.py`. **Expected:** about 1 h 45 min / 80 GB and
   2 h 50 min / 103 GB.
3. **Compare** (objective; builds by technology with `compare_s0_runs.py`; `prm_shortfall.csv`, `prm_summary.csv`;
   CO2). **Expected:** within about 1%, as above; **report** anything larger.

## J. Reuse unchanged early stages from S0prod_A (mode A chains; CHANGES §71)

`run_chain_A.py` is VM-only, so the reuse runs as a step before it: `s0_workflow/scripts/reuse_chain_stages.py`
copies the reference's solved stages that match exactly, hands over to the next stage as if they had been solved, and
writes `scenarios_<case>.from_<stage>.txt` with the lines left to solve. Run `run_chain_A.py` on that file. If it
builds its own lines, start it at that stage instead.

1. **Record the reference once** (after S0prod_A's chain has solved). Give the code head it was solved at, and the
   exact solver arguments `run_chain_A.py` adds to every line:
   ```bash
   python s0_workflow/scripts/reuse_chain_stages.py record switch/in/s0prod_A/scenarios_S0prod_A.fixed.txt \
       --git-head <sha the chain was solved at> \
       --solver-args "--solver gurobi --solver-options-string 'method=2 crossover=0 BarConvTol=1e-6 ScaleFlag=2 Threads=8' --tempdir /d/tmp" \
       --solver-version "gurobi <x.y.z>"
   ```
   Each stage's outputs folder gets `chain_provenance.json`: head, solver version and arguments (a token list since
   §83; older records hold a string and are still read), input digest. `--tempdir` and its path are left out of the
   comparison, quoted or not, spaces included. The
   solver version defaults to the installed gurobipy's if omitted; give it from the reference's solve log if gurobipy
   has been upgraded since.
2. **Expected reuse from the case definitions** (no builds needed). On the VM checkout, whose `scenario_inputs.csv`
   has the S1-S5, L and P rows:
   ```bash
   python s0_workflow/scripts/reuse_chain_stages.py expected --reference S0prod_A --out chain_reuse_expected.csv
   ```
   **Report** the printed lines (each case: its expected reusable stages, and the first difference). This repo's
   rows (§72): `S0_tx` all stages; BILL_central, BILL_low, txonly and bronly 2028 and 2030; BILL_high and
   BILL_central_S1 2028 (`s0_workflow/data/chain_reuse_expected.csv`).
3. **Per scenario chain** (built, not solved; fresh outputs folders):
   ```bash
   python s0_workflow/scripts/reuse_chain_stages.py reuse switch/in/<root>/scenarios_<case>.txt \
       --reuse-from switch/in/s0prod_A/scenarios_S0prod_A.fixed.txt --solver-args "<the same string>" --dry-run
   python s0_workflow/scripts/reuse_chain_stages.py reuse switch/in/<root>/scenarios_<case>.txt \
       --reuse-from switch/in/s0prod_A/scenarios_S0prod_A.fixed.txt --solver-args "<the same string>" [--link]
   ```
   - The dry run compares each stage's inputs other than the chained files (those follow from the identical earlier
     stages). The real run compares everything, the chained files included, after each handover.
   - The scenario's lines must be in the same form as the reference's: same modules and flags, and the same alias
     convention. With fixed forced-transmission aliases (recipe C), use the `.fixed.txt` lines on both sides.
   - Stops at the first stage with any difference and names the first differing file, alias or option. It also stops
     (reusing nothing) on code changes since the recorded head in Switch modules, module lists or input writing (with
     `--code-check model`: Switch modules, module lists and options only), on uncommitted changes there, or on a
     different solver version or arguments (`--tempdir` aside).
   - **§80 (S0 v3 scenario chains on `tom/s0-v3-scenarios`):** the S0 v3 reference was solved at 00b043b, and every
     commit since touches input-writing code (none touches `switch/`), so the default check stops. Add
     `--code-check model`: it checks only Switch modules, module lists and options, and leaves input writing to the
     input comparison. `--tempdir` is no longer part of the solver comparison, and the program files
     (`max_cap_generators.csv` and the other min/max/RPS program files) match whatever their row order.
   - `--reuse-through 2030` reuses no later stage. `--link` hard-links outputs instead of copying.
   - The handover runs `prepare_next_stage` in the Python of the `switch` command on PATH (its shebang), so it writes
     what a solve would. Pandas versions order rows differently, and a mismatch makes the next stage's comparison
     refuse. If `switch` is not on PATH, pass `--python "<switch-pg-reeds-fedpol python>"`.
4. **Solve the rest:** `run_chain_A.py` on the printed `scenarios_<case>.from_<stage>.txt`. Then `record` the scenario's
   scenarios file, so it can serve as a reference later.
5. **Check and report:**
   - Each reused stage's outputs folder has `reuse_provenance.json` (`reused_from`, the input digest, the files handed
     over).
   - `out/<root>/handoff_runs.csv` has one `reused` row per reused stage and one `solve` row with the reason reuse
     stopped. (`--handoff-runs <csv>` points it at `run_chain_A.py`'s own file instead.)
   - **Once, to validate:** solve one scenario both ways (with reuse and without, into another root). The first
     solved stage's inputs, chained files included, are byte-identical, and the results match.

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
