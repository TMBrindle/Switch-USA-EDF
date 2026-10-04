# Shared-code changes on `tom/s0-prod-scripts` (for review by Ollie)

Branch `tom/s0-prod-scripts` (from `tom/s0-integration` + `tom/ic-test-fedpol`) productionises the S0
test workflow (CHANGES §44). Most of the work is new files under `s0_workflow/`. This list covers every
change to code or defaults that other cases share, with the reason for each. Nothing here is merged
into `ollie/fedpol`, `edf-baseline` or `main`.

**Rule kept throughout:** a case that doesn't set `s0_production` (every existing row of
`scenario_inputs.csv`) builds the same inputs as before. The one exception is the gas-turbine cap's
file format (item 5). It moved from `max_cap_*` rows to `gas_turbine_cap_*.csv`, with the same
constraint values, and is checked numerically on the toy.

| # | File | Change | Why | Effect on existing cases |
|---|---|---|---|---|
| 1 | `switch/modules.txt` | **No change.** `gen_amortization_period` and `retirement_rules` go on the S0 case's scenario line (`s0_production.extra_modules`; `scenario_options()`), not into modules.txt. | Item 6 asks for amortisation on the S0 case only. | none |
| 2 | `pg/settings/resources.yml` | **Comment only** (§45): a source note above the GridLab CC/CT override (`atb_modifiers.ngcc/ngct`). Its values aren't in the GridLab report (Sep 2025); the note gives current sources. Values unchanged. S0 production cases neutralise the override in their own settings and apply a premium over ATB Moderate (`gas_capex.mode: premium`) or ATB alone (`atb_moderate`, legacy). | Items 2 (§44) and 1 (§45). | none |
| 3 | `pg/settings/model_definition.yml` | `model_year` and `model_first_planning_year` gain 2045 / 2041. | 2045 is a production year; PowerGenome refuses a model year missing from these lists. The span 2041-2045 follows the convention that a period ends at its model year. | none (a list entry used only by cases with a 2045 row) |
| 4 | `pg/settings/scenario_management.yml` | (a) New `s0_production` axis (off / on / on_windows / on_pgdays), touching only `s0_production.*`. (b) The 11 `MaxCapTag_GasTurbineSupply` MaxCapReq entries under `settings_management.<year>.all_cases` removed; one comment updated. | (a) Item 7: one switch per case. (b) Item 5: the cap moved to the build-rate module. | (a) none. (b) see #9 |
| 5 | `pg/extra_inputs/scenario_inputs.csv` | New columns `build_rate` and `s0_production`, `off` in every existing row; 11 new rows: `s4x1_S0prod_2035` (regression; S0unc icon + build rate central + on_pgdays), `S0prod_A` and `S0prod_B` (2028-2045). | Both axes need a column. `build_rate` was VM-only. | none (`off` = previous behaviour; the build_rate axis's "off" sets enabled false, its default) |
| 6 | `pg/settings/build_rate.yml` | New block `build_rate.gas_turbine_cap` (enabled by default). It applies whether or not `build_rate.enabled`. | Item 5. | see #9 |
| 7 | `pg/settings/s0_production.yml` | New file, key `s0_production` (enabled: false). | Item 7. | none (PowerGenome loads every yml in pg/settings; the key is new and off) |
| 8 | `make_emission_policies.py` | `MaxCapTag_GasTurbineSupply` removed from `max_growth_limits`, with a comment giving its old definition and sources; the docstring example now uses NuclearGrowth. | Item 5: one place defines the cap. | A rerun no longer writes the gas-turbine MaxCapReq. The build-rate setting supplies the same cap. |
| 9 | gas-turbine cap move: `build_rate/brc/turbine_cap.py` (new), `switch/study_modules/build_rate.py`, `pg_to_switch.py` (`write_build_rate_files`) | The cap becomes a configurable gas group in the build-rate module: annual additions by year, coverage (CC / CT / aeroderivative / reciprocating), CC at full MW or turbine share, cumulative in-service vs new additions per period. When on, pg_to_switch drops the `MaxCapTag_GasTurbineSupply` rows from `max_cap_requirements.csv` / `max_cap_generators.csv` and writes `gas_turbine_cap*.csv`. | Item 5. The defaults reproduce the old cap exactly: 451,444.2 MW in 2024 + 9,666.67 MW/yr, cumulative, retirements don't free room, raised to the covered predetermined MW like `cap_req_files`. A separate research task will supply the new trajectory. | Same constraint values, now in the build-rate module. Tests: all 11 old values match; on the toy, the old MaxCapTag and the new cap give the same objective and builds with the cap binding. Coverage is cross-checked against the old tag and logged when it differs. |
| 10 | `pg_to_switch.py` | (a) `operational_files`: when `s0_production.time_sampling.method: fleet_independent`, days come from `s0_workflow/day_selection.py`, per model year, instead of PowerGenome's k-means. (b) `main`: `s0prod.apply_settings` merges S0 settings before building. Cases are split into stages: legacy cases follow `--myopic` as before; S0 cases pick single / myopic / windows from settings. Stage folders are named by stage (e.g. `2028_2030`). `s0prod.write_case_inputs` runs before the build-rate files; `stage_info.csv` is written for S0 stages. (c) `scenario_files`: S0 stages get their own lines (modules, `prepare_next_stage`, one `--input-aliases` list with the ic and build-rate chained aliases). Legacy lines are unchanged except `scenario_options()`, which is empty for them. | Items 1, 4, 6, 7, 8. | none for cases without `s0_production`. One small change: years are processed in sorted order. |
| 11 | `switch/study_modules/prepare_next_stage.py` | If the stage's inputs dir has `stage_info.csv`, the next stage's folder comes from it (not the fixed year list). Only committed builds (build year ≤ the end of `commit_period`), retirements in periods ≤ `commit_period`, transmission built to `commit_period`, and the headroom and build-rate state at `commit_period` are handed on. The body moved into `chain_stage()`; `chain_ic_inputs` and `chain_build_rate_inputs` take a `commit` argument (default: the last period). | Item 8, mode B: a window commits only its first period. | none without `stage_info.csv`. Legacy chains take the same path with commit = None. The existing chaining tests pass unchanged. |
| 12 | `switch/study_modules/interconnection_headroom.py` | `ic_tranches_built.csv`, `ic_uprates_built.csv`, `ic_release_built.csv` and `ic_hosted_built.csv` now have one row per period (cumulative), not just the last period. | Item 8: a window hands on its committed period. | Readers must filter on `period`. `chain_ic_inputs` defaults to the last period, so legacy chaining is unchanged. One headroom test was updated to filter on `period`. |
| 13 | gen_info and retirement handling: `switch/study_modules/retirement_rules.py` (new), `s0_workflow/production.py` (`write_retirement_rules`) | For S0 cases only: `gen_can_retire_early` = 1 on existing coal and gas, plus `retirement_rules.csv` and the module, which forbid SuspendGen in periods before 2030. | Item 8 prerequisite. The single Can_Retire flag held coal and gas for every period of a case or window starting before 2030 (`blocked_2030_coal_gas` sets Can_Retire 0 in pre-2030 years): the coal lock. The rule blocks only pre-2030 periods; retirement is economic from 2030. The predetermined-retirement override (window 2026-29 -> 2030) is unchanged. | none (module not in modules.txt; file written only for S0 cases) |
| 14 | policy files: `pg/settings/s0_production.yml` `rps_acp` | For S0 cases, `rps_requirements.csv` gets `rps_acp_per_mwh` = 45.39 on `ESR_NY_rps` only. | Item 4: the NY buyout, previously an alias. Uses the opt-in ACP in `rps_regional.py` (CHANGES §38). | none |
| 15 | `.gitignore` | `s0_workflow/data/raw/` ignored. | EIA downloads for the coal table. | none |

### Added in §45 (Oct 2026: gas capex premium, turbine allowance, buyouts, pinned ReEDS inputs)

The regression case `s4x1_S0prod_2035` keeps the legacy setting for each of these. A test rebuilds
its case files and checks them byte for byte against the previous code.

| # | File | Change | Why | Effect on existing cases |
|---|---|---|---|---|
| 16 | `pg/settings/build_rate.yml` | `gas_turbine_cap` gains the `cumulative_additions` form's keys: `since_year`, `allowance_path`, `allowance_paths` (low/central/high, 2025-40), `extend_slope_years`, `allowance_coverage`, `class_weights`, `capacity_basis`. The file's `form` stays `cumulative_in_service`. | S0 item 2 (§45): the new turbine allowance, selected per case by `s0_production.gas_turbine_cap`. | none: the default form is unchanged |
| 17 | `build_rate/brc/turbine_cap.py`, `switch/study_modules/build_rate.py` | New form `cumulative_additions`: weighted MW built from `gtc_since_year` to each period's end year ≤ the allowance. New optional param `gtc_since_year` in `gas_turbine_cap_params.csv`. | As #16. | none: the legacy forms are unchanged and pass their tests |
| 18 | `switch/study_modules/rps_regional.py` | New output `rps_buyout_by_state_year.csv` (buyout MWh and $ by state and calendar year) and helper `buyouts_by_state_year()`. Written only when some program has an ACP, like `rps_shortfall.csv`. | S0 item 3 (§45). | one extra output file for cases with an ACP |
| 19 | `make_emission_policies.py`, new `pg/extra_inputs/reeds_state_policies/` | Every ReEDS input the script reads now comes from verbatim copies of ReEDS release 2026.09.21 (commit 8a15723), recorded in `REEDS_RELEASE.yml`, instead of live downloads from main (`reeds_input()`). | S0 item 4 (§45): reproducible state-policy inputs. | none until the script is rerun. Rerunning would change the state RPS/CES targets as listed in `s0_workflow/data/reeds_state_policy_diff_2026.09.21.csv` (largest: NC CES, CT RPS, ME CES; NY unchanged). The committed policy files are not regenerated. |
| 20 | `pg/settings/scenario_management.yml`, `pg/extra_inputs/scenario_inputs.csv` | The `on_pgdays` axis value pins the legacy settings; new value `on_pgdays_new`; new row `s4x1_S0prod_2035_new`. | Keep the regression case as built; add its new-defaults twin. | none |

### Added in §46 (Oct 2026: policy files from the pinned ReEDS release, nameplate basis for new builds)

| # | File | Change | Why | Effect on existing cases |
|---|---|---|---|---|
| 21 | `pg_to_switch.py` | One call, `s0prod.apply_state_policies(case_settings)`, after the case settings are built and before the region scope. | §46 item 4: S0 new-defaults cases take the state-policy files of the pinned ReEDS release; before the region scope so aggregated zones take their tags. | none: a no-op unless `s0_production.enabled` and `state_policies.release` is a release (not `legacy`). It builds new dicts, so settings objects shared with other cases are not modified (tested). |
| 22 | new files: `pg/extra_inputs/rggi_carbon/emission_policies_reeds_2026.09.21.csv`, `pg/extra_inputs/reeds_state_policies/s0_state_policies_2026.09.21.yml` (from `s0_workflow/scripts/build_reeds_state_policies.py`) | State RPS/CES targets, UREC limits, carbon columns, and ESR eligibility built from the pinned release with `make_emission_policies.py`'s rules. `emission_policies_current.csv`, `regional_resource_tags.yml` and the tag lists are not touched. | §46 item 4. | none: read only by S0 cases on the release. The release's eligibility, carbon columns and offshore mandates equal the current ones. Only targets differ (diff report). |
| 23 | `build_rate/brc/turbine_cap.py`, `pg/settings/build_rate.yml` | `capacity_basis` factors may be a number or a per-class map. `new_build_to_allowance` is now CC 1.049, CT/aero 1.090 (was 1.0). The 2041-45 extension is labelled and logged as a coordinator estimate. | §46 items 2-3. | none: used only by the `cumulative_additions` form (S0 cases); the file's default form is unchanged. |

### Added in §47 (Oct 2026: no new nuclear before the 2035 stage)

| # | File | Change | Why | Effect on existing cases |
|---|---|---|---|---|
| 24 | `switch/study_modules/build_rules.py` (new), `s0_workflow/production.py` (`write_build_rules`, `scenario_options`) | For S0 new-defaults cases only: `build_rules.csv` and the module, which forbid new nuclear (`BuildGen` of new builds of its energy source) in periods before 2035. | An explicit rule, independent of the nuclear growth cap, which counts existing nuclear and can leave room in 2028/2030 if plants retire. | none (module not in modules.txt; file written only for S0 cases with `new_build_rule.enabled`; off for the legacy regression case) |

### Added in §48 (Oct 2026: coal specification rev. 2, 2045 load entries)

**Flagged for Ollie:** #27 and #28 change shared settings that every case reads. They add model-year
entries and remove a comment block; no existing value changes.

| # | File | Change | Why | Effect on existing cases |
|---|---|---|---|---|
| 25 | `pg_to_switch.py` | (a) `gc.create_all_generators()` runs inside `s0coal.unit_hooks(year_settings)`. For S0 cases with `s0_production.coal_spec` this wraps PowerGenome's `group_technologies` and `atb_fixed_var_om_existing` at runtime, to edit coal units before clustering; it restores them afterwards. (b) `apply_predetermined_retirement_override` honours a new settings key, `predetermined_retirement_override_exempt` (technology substrings), set to `[coal]` by the coal spec. | Coal spec §2: unit-level overrides before clustering, without editing PowerGenome or its data. The exemption keeps the spec's dated coal retirements (and the holds' encoded 2029) from being moved to 2030 by `blocked_2030_coal_gas`. | none: both are no-ops unless the S0 coal spec is on (tested on legacy rules) |
| 26 | `switch/study_modules/gen_annual_availability_limits.py` | New optional input `gen_max_annual_availability_by_period.csv` (GENERATION_PROJECT, PERIOD, value), overriding the per-generator value in that period. | Coal caps differ by stage; a mode-B window has two periods in one case. | none without the file (default: the gen_info value, as before) |
| 27 | `pg/settings/scenario_management.yml` | (a) New axis `coal_holds` (s0 / holds_persist), touching only `s0_production.coal_holds`. (b) `on_pgdays` turns `coal_spec` and `coal_holds` off. (c) `load_growth.edf_epri_med` gains 2045, and `load_growth.epri_high` gains 2040 and 2045 `flexible_demand_resources` entries (same shape as the other years). (d) The investigation-note comment blocks above `edf_epri_med` and `epri_high` are replaced by one-line pointers; their content moved to `Guides and documentation/load_growth.md`. | (a, b) Coal spec §3.4. (c) The 2045 stage, which PowerGenome can't build without the entry. (d) Tom: notes don't belong in the settings. | (a, b) none. (c) Only cases with a 2045 (or, for epri_high, 2040) model year read the new entries. Earlier years are unchanged. (d) Comments only. |
| 28 | `pg/settings/flexible_load.yml` | `flexible_demand_resources` gains 2040 and 2045 `us_exports` entries (fraction 0, as the other years). | As #27(c). | only cases with those model years read them |
| 29 | `pg/extra_inputs/scenario_inputs.csv` | New column `coal_holds`, `s0` in every row. | Coal spec §3.4 (blank would also mean s0 but makes PowerGenome warn for every row). | none (inert unless `s0_production.enabled` with the coal spec) |

### Added in §49 (Oct 2026: pre-2030 retirement options, coal spec rev. 2.1)

**Flagged for Ollie:**
- **New setting:** `s0_production.retirements_pre2030` (#30-31) sets the pre-2030 policy of the S0 new-defaults
  cases. Its default, `block_all`, applies fedpol's `blocked_2030_coal_gas` rule to coal and gas alike.
- **fedpol's block is back to its own code:** the coal exemption added in §48 (#25b) is removed, and
  `apply_predetermined_retirement_override` is byte-for-byte fedpol's again.
- **Encoding difference to note:** for the S0 new-defaults cases only, the S0 hook encodes coal, held **and gas**
  units dated 2026-29 as 2031 rather than 2030 (§50). The units are those whose cluster technology matches the
  rule, as fedpol's function matches it. Switch behaviour is the same: in service through the 2030 stage, gone
  from 2035. Every other case keeps fedpol's 2030, and fedpol's function is unchanged.
- **Likely general bug in fedpol's encoding:** PowerGenome treats `retirement_year <= model_year` as retired, so a
  cluster whose units are all pushed to 2030 gets zero capacity in model year 2030 and is dropped, even though the
  push meant to keep it. A target of 2031 (or keeping zero-capacity clusters that have build-year capacity) would
  fix it.

| # | File | Change | Why | Effect on existing cases |
|---|---|---|---|---|
| 30 | `pg_to_switch.py` | #25(b) withdrawn: the `predetermined_retirement_override_exempt` key and its three lines in `apply_predetermined_retirement_override` are removed; the function is identical to fedpol's (tested against 79c5f35). | Tom: block_all applies fedpol's block to coal and gas alike. | none (the function is as before §48) |
| 31 | `pg/settings/scenario_management.yml`, `pg/extra_inputs/scenario_inputs.csv` | New axis `retirements_pre2030` (block_all / planned_only / unrestricted, touching only `s0_production.retirements_pre2030`; `legacy: ~`). New column `retirements_pre2030`: `block_all` in every row, `legacy` in the regression row. `on_pgdays` sets `retirements_pre2030: legacy`. | Rev. 2.1 §A3. | none: inert unless `s0_production.enabled`; the regression case is unchanged (byte-identical test) |
| 33 | `s0_workflow/coal_fleet.py` (S0 only) | §50: block_all's push no longer moves the five out-of-service coal removals. The hook also pushes gas (cluster technology after PowerGenome's grouping matching "natural gas") to 2031 in S0 new-defaults cases. | Tom, 2026-10-04. | none outside S0 new-defaults cases (the hook is inactive elsewhere, including the legacy case) |
| 34 | `s0_workflow/coal_fleet.py` (S0 only) | §51: the hook deletes the five out-of-service coal removals from PowerGenome's unit tables before clustering, in every pre-2030 option: from its EIA-860 units and from the 860M generators it adds for Operating-sheet units missing from them. Encoding them 2026 (#33) wasn't enough: fedpol's `apply_predetermined_retirement_override`, which runs on the clusters afterwards, moved them to 2030 (VM build at c4a19f8). The build logs `coal removals: 5 removed` and stops on any other count. | Tom, 2026-10-04. | none outside S0 cases with the coal spec or block_all (the hook is inactive elsewhere, including the legacy case); fedpol's function unchanged |
| 35 | `s0_workflow/coal_spec.py`, `coal_cf.py`, `case_aliases/b8_coalcf.py`, `scripts/build_coal_option_tables.py`, `scripts/build_reeds_state_policies.py`, `scripts/fetch_coal_spec_eia.py` | §51: pandas 1.4.4 compatibility. `groupby.apply(include_groups=)` (pandas ≥ 2.2) replaced by plain aggregations; `to_csv(lineterminator=)` (≥ 1.5) by a writer that works on both; the eligibility merge in `build_reeds_state_policies.py` sorts explicitly (inner-merge row order differs between pandas 1.4 and 2.2). Outputs unchanged (both `--check`s pass on pandas 1.4.4 and 3.0.6). | The case-build env `switch-pg-reeds-fedpol` has pandas 1.4.4; the c4a19f8 build failed there. | none (same tables, same files) |
| 37 | `s0_workflow/coal_fleet.py`, `coal_spec.py`, `data/coal_model_basis_860er2024.csv`, `specs/coal/coal_spec_overrides.csv`, `specs/coal/by_option/*`, `specs/coal/coal_spec_not_in_model.csv`, `scripts/build_coal_option_tables.py`, `scripts/fetch_coal_spec_eia.py` (S0 only) | §53, coal spec rev. 2.1 A4: (1) an override PowerGenome's fleet already satisfies passes the check (`ok (already satisfied: ...)`); (2) plants not in `reeds_plant_map.csv` are left out of the model basis and the expected tables, and their spec rows pass as `not in model: plant not in reeds_plant_map.csv` (1.47 GW, a known gap); (3) Edwardsport CT1 / CT2 count 240.6 MW each. Per-option tables regenerated; zone-MW report threshold 0.1 MW (was 1 MW); the build reports coal-group units PowerGenome re-adds from the 860M. | Tom, 2026-10-04, after the VM build at 88e6b30 failed the coal check. | none outside S0 cases with the coal spec; N and the caps are unchanged |
| 36 | `build_rate/brc/rates.py` | §52: `completion_rates` loops over the groups instead of `groupby.apply(include_groups=False)` (pandas ≥ 2.2), with the same sums. | pandas 1.4.4 in `switch-pg-reeds-fedpol`. | none: the 20 pipeline tables are byte-identical (old code on pandas 3.0.6 vs new code on 3.0.6 and 1.4.4) |
| 32 | `s0_workflow/specs/coal/coal_spec_converted_gas_units.csv`, `coal_spec_overrides.csv`, `coal_spec.md` | Converted-unit heat rates replaced by the latest-EIA-923 values (rev. 2.1); addendum rev. 2.1; new `by_option/` validation tables. | Tom's decisions, 2026-10-04. | none (S0 coal spec only) |

### Added in §54 (Oct 2026: forced transmission from ReEDS, forced lines limited, coal history from mapped plants)

**Flagged for Ollie** (all three are for the S0 new-defaults cases only; legacy, fedpol and every other case build
as before, and a test checks that transmission_tables writes byte-identical files without the S0 keys):
- **Forced transmission source (#38, #39).** The S0 default (`s0_production.forced_tx: reeds_certain`) replaces the
  72 named projects of `transmission_connections.csv` (245,857 MW, `new_cap_mw`) with ReEDS 2026.09.21's certain
  additions: SunZia (p28-p31, 3,000 MW, 2026 -> 2028) and TransWest Express (p24-p25, 3,000 MW, 2032 -> 2035), 6,000
  MW in all. The release has no `transmission_capacity_future_*` file; its certain future capacity is
  `inputs/transmission/hvdc_planned-baseline.csv` with `certain == 1` (AC additions go through the ITLs). The named
  list stays as `forced_tx: named_projects` for sensitivities. Worth a look for fedpol: the named list's values look
  like project nameplate, not transfer capability (e.g. Gateway West 11,208 MW).
- **Forced lines were unlimited (#38).** `transmission_tables` leaves forced lines (those with a
  `trans_build_minimum`) out of `trans_path_expansion_limit.csv`, so Switch's default (no limit) lets them expand
  without bound. That is still so for every non-S0 case. With `forced_tx_expansion_limit: minimum` (S0) a forced line
  is limited to its minimum in its forced period, and like any other line in the others; under
  `trans_expansion_policy: unlimited` only the forced periods are written. This is likely worth adopting generally.
- **Coal history (#40).** S0's coal caps take H and N from plants in `reeds_plant_map.csv` only.

| # | File | Change | Why | Effect on existing cases |
|---|---|---|---|---|
| 38 | `pg_to_switch.py` (`transmission_tables`) | (a) The forced-line table is `settings["forced_tx_table"]` when set (same columns as `transmission_connections.csv`), else `transmission_connections.csv` as before; it supplies the planned lines, their minimum builds and the injected new corridors. (b) With `settings["forced_tx_expansion_limit"] == "minimum"`, forced lines are kept in `trans_path_expansion_limit.csv`: their `trans_build_minimum_mw` in the forced period, the policy's limit otherwise (zero / nerc_growth), and only the forced periods under `unlimited`. | §54 items 1-2. | none: neither key is set outside S0 new-defaults cases; byte-identical files tested against 4f882b6 for zero / nerc_growth / unlimited, constrained and unconstrained |
| 39 | `pg/settings/s0_production.yml`, `s0_workflow/production.py` (`apply_forced_tx`), `pg/settings/scenario_management.yml`, `pg/extra_inputs/scenario_inputs.csv`, `pg/extra_inputs/transmission/reeds_2026.09.21/` (new, pinned copies), `forced_tx_reeds_certain_2026.09.21.csv`, `forced_tx_comparison_2026.09.21.csv`, `s0_workflow/scripts/build_reeds_forced_tx.py` (new) | New S0 settings `forced_tx` (reeds_certain default / named_projects) and `forced_tx_expansion_limit` (minimum default / legacy). New axis and column `forced_tx` (reeds_certain in every existing row, legacy in the regression row; `on_pgdays` pins named_projects + legacy). New rows `s4x1_S0prod_2035_txreeds` / `_txnamed` (the 2035 comparison pair). | §54. | none (inert unless `s0_production.enabled`; the regression case keeps the named list and no limit) |
| 40 | `s0_workflow/coal_spec.py` (`cap_unit_set`), `coal_fleet.py`, `specs/coal/by_option/*`, `coal_spec.md` (A5) (S0 only) | The coal cap unit set (H, own, N) keeps plants in `reeds_plant_map.csv` only. N moves +0.0006 to +0.0010; the largest cap changes are p70 +0.038, p99 −0.025, p103 +0.019 (2035-45), p83 +0.016, p21 −0.009. | Tom, 2026-10-04. | none outside S0 cases with the coal spec |

### Added in §55 (Oct 2026: regional planning reserve for S0)

**Flagged for Ollie** (S0 cases with `prm.design: regional` only; the legacy design, the regression case, fedpol and
every other case build as before — tests check the legacy settings and that the hooks do nothing without the key):
- **pg_to_switch.py time sampling (#41).** After either sampler (PowerGenome k-means or the fleet-independent
  selector), `s0_workflow/prm.py` can append stress days at zero weight; after `ts_tp_pg_kmeans` their timeseries and
  timepoints get their own ids (`<year>_pN_prm`, `9<id>`). Both are no-ops unless the year's settings select the
  regional design. The full-record path (no time reduction) raises if the regional design is asked for.
- **Reserve modules swapped on the S0 scenario line, not in modules.txt (#42).** The case's line gets
  `--exclude-module study_modules.planning_reserves --exclude-module study_modules.planning_reserves_extreme_days
  --include-module study_modules.prm_regional`, and the case drops the "Add extreme day" adjustment script.
  `switch/modules.txt`, `planning_reserves.py` and `planning_reserves_extreme_days.py` are unchanged.
- **New module `switch/study_modules/prm_regional.py` (#42)**: not loaded by any case without the scenario option.
- **Worth a look generally:** the legacy design's import cap applies only in the one checked hour (storage can charge
  from uncapped imports in the others), and its CAISO 1e-6 workaround is not needed by the new module (a share of 0
  is enforced; tested). The same storage laundering exists in `gen_zone_ratio.py` (P-11, not implemented; S0 doesn't
  use it).

| # | File | Change | Why | Effect on existing cases |
|---|---|---|---|---|
| 41 | `pg_to_switch.py` (`operational_files`) | Imports `s0_workflow.prm`; appends stress days after time sampling and renames their ids after `ts_tp_pg_kmeans` when `s0_production.prm.design: regional` (`s0prm.year_prm`). | §55 item 3. | none (no-op without the S0 key; regression byte identity tested) |
| 42 | `switch/study_modules/prm_regional.py` (new), `s0_workflow/prm.py` (new), `s0_workflow/production.py` (`apply_settings`, `write_case_inputs`, `scenario_options`), `s0_workflow/specs/prm/prm_redesign_config.yaml` (new) | Regional reserve: 16 regions, LTRA margins on derated capacity, stress days, thermal 1 - FOR(T), hydro/storage/DR at dispatch, reserve transfers with losses, line limits and a 15% new-line derate, zone-level deliverability, import cap in every stress hour, shortfall at $271.79/kW-yr (2024$), diagnostics. | §55, Tom's decisions 2026-10-04. | none outside S0 regional cases |
| 43 | `pg/settings/s0_production.yml`, `pg/settings/scenario_management.yml`, `pg/extra_inputs/scenario_inputs.csv` | New `prm` block (design regional by default); `on_pgdays` pins `prm: {design: legacy}`; axis and column `prm_design` (regional for S0prod_A/B and the new `s4x1_S0prod_2035_prm`, legacy elsewhere). | §55. | none (inert unless `s0_production.enabled`; the regression row is legacy) |
| 44 | `s0_workflow/tests/test_forced_tx.py` | Reads source files as UTF-8. | Windows default encoding. | none |

### Added in §56 (Oct 2026: chronological multi-day blocks in the S0 day selector)

| # | File | Change | Why | Effect on existing cases |
|---|---|---|---|---|
| 45 | `pg_to_switch.py` (`operational_files`) | When the fleet-independent selector returns blocks (`s0days.sample_is_blocks`), builds `timeseries.csv` / `timepoints.csv` with `s0days.ts_tp_blocks` (mixed-length slots, same id format) instead of `ts_tp_pg_kmeans`; the log line names the sample. | §56. | none (only with `time_sampling.sample: NxL`, L > 1) |
| 46 | `s0_workflow/day_selection.py` | `sample` setting (N or NxL); `_select_blocks` (block candidates, set search, relaxation only when no set is feasible); `ts_tp_blocks`. The day path is split into `_prepare` + the unchanged day code (outputs byte-identical, checked against the previous version). | §56. | none |
| 47 | `pg/settings/s0_production.yml`, `pg/settings/scenario_management.yml`, `pg/extra_inputs/scenario_inputs.csv` | `time_sampling.sample` (commented), `block_pool`, `block_relax_max_steps`; `s0_production` axis value `on_single`; axis and column `time_sample` (days24 sets nothing, in every row but `s4x1_S0prod_2035_fi4x3`); rows `s4x1_S0prod_2035_fi24` / `_fi4x3`. | §56, recipe F. | none (days24 sets nothing) |

### Added in §57 (Oct 2026: forced transmission built once in S0 chains)

| # | File | Change | Why | Effect on existing cases |
|---|---|---|---|---|
| 48 | `pg_to_switch.py` (`transmission_tables`, case-stage setup, `scenario_files.add_stage_row`) | S0 chain stages get `_chain_years` (all of the case's years). The forced period is `s0prod.forced_tx_period(year, chain years, stage years)`, so a stage forces only lines whose period it models. Later stages alias `trans_build_minimum` / `trans_path_expansion_limit` to their `.chained.<case>.csv` when the stage has the file. | VM: every chained stage re-forced SunZia / TransWest. | none (without `_chain_years` the rule is the old one: legacy byte identity tested; single-stage S0 cases have chain = stage) |
| 49 | `switch/study_modules/prepare_next_stage.py` (`chain_stage`, new `chain_forced_tx`) | S0 chains only (`stage_info.csv`, `commit` set): writes `trans_built_to_date.chained.<case>.csv`, and the next stage's `trans_build_minimum` / `trans_path_expansion_limit` chained files with each minimum less the line's committed new capacity so far (and the cap-at-minimum row likewise). | Safeguard against re-forcing. | none (the legacy myopic path passes no commit and skips it) |
| 50 | `s0_workflow/production.py` (`forced_tx_period`), `s0_workflow/tests/toyutil.py` (`run_chain` aliases the forced files as the scenario lines do) | The forced-period rule as a helper; test plumbing. | §57. | none |
| 51 | `switch/study_modules/prm_regional.py`, `pg_to_switch.py` (`add_stage_row`) | `prm_regional` reads optional `trans_built_to_date.csv` (`trans_built_to_date_mw`) and derates earlier stages' new lines as new; S0 chain stages after the first alias it to its chained file. Docstring: duals come from `write_dual_costs`' suffix. | §55 item 5 in chains (follow-on to §57). | none (S0 chains only) |
| 52 | `s0_workflow/scripts/fix_forced_tx_aliases.py` (new), `s0_workflow/tests/toyutil.py` (`run_chain`: alias overrides; chained forced files aliased when written) | Corrected forced-line files as `*.fixed.csv` aliases for S0 chains built before §57, plus a fixed scenarios file. | §58: the VM's S0prod_A chain built at 1eab1ac. | none (new files next to a built case; nothing changed) |

**Not changed:**
- `gen_build.py`, the Switch core and `switch/modules.txt`;
- the `retirement_policy` axis, and Can_Retire in `resource_tags.yml`;
- the `MaxCapTag_GasTurbineSupply` model tag in `resource_tags.yml`. It is kept so PowerGenome still lists the old members, which the cap's coverage check uses. Without a MaxCapReq it constrains nothing.

**Review points for Ollie:**
1. (§45) The S0 defaults now include a $100/MWh buyout on every state RPS and CES. A policy branch that
   wants hard targets for S0 should set `rps_acp.enabled: false` or `mode: programs`.
2. Item 9 changes how every case on this branch carries the gas-turbine cap. Any branch that merges
   this one and still has `MaxCapReq` entries for `MaxCapTag_GasTurbineSupply` would have them dropped
   at case build while `gas_turbine_cap.enabled` is on. Set `enabled: false` to keep the old route.
3. The `scenario_management.yml` and `scenario_inputs.csv` edits will conflict with `ollie/fedpol` on
   merge. They are additive: one axis, two columns, rows.
4. `prepare_next_stage.py` now ends `post_solve` through `chain_stage()`; the legacy logic is
   unchanged inside it.
5. (§46) `build_reeds_state_policies.py` duplicates the ESR-target, eligibility and carbon rules of
   `make_emission_policies.py` rather than refactoring that script. A change to those rules there
   should be mirrored. The test that the release's eligibility equals `regional_resource_tags.yml`
   catches a drift in the eligibility rules, but only while the two inputs agree.
6. (§48) The coal spec wraps two PowerGenome functions at runtime (#25a), for S0 cases only. A PowerGenome
   update that renames them or changes their order in `create_region_technology_clusters` would stop the
   coal edits. The case build then stops: the coal check finds no recorded unit table.
7. (§48) #27(c) and #28: the follow-up is to generate the per-year `flexible_demand_resources` entries from
   the model-year list, so extending the horizon can't break a build again
   (`Guides and documentation/load_growth.md`).
8. (§54) The forced-line expansion limit (#38) is S0-only. The same gap applies to every other case, where forced
   lines have no expansion limit in Switch. Consider `forced_tx_expansion_limit: minimum` (or its logic) for
   fedpol's cases too.
9. (§55) The legacy planning reserve (every non-S0 case) has the gaps listed under "What the current design does" in
   `Guides and documentation/s0_production.md`: one checked hour per zone (which can be a weighted hour), thermal and
   hydro at nameplate, imports capped only in that hour (storage can launder uncapped imports), no deliverability
   within multi-zone regions, and the CAISO 1e-6 workaround. `prm_regional` addresses each; consider it for fedpol.
10. (§56) `scenario_inputs.csv` has one more column (`time_sample`); merging with `ollie/fedpol` adds `days24` to its rows.
11. (§57) Non-S0 myopic chains (`--myopic`, e.g. fedpol's) have the same re-forcing: `transmission_tables` builds
    each year's stage with its own years, so a forced line due at or before a later year is forced again there, on
    top of the capacity `prepare_next_stage` carries forward. The fix is S0-only. For fedpol, setting
    `_chain_years` for its myopic stages (or using `forced_tx_period`) would close it; `chain_forced_tx` would
    need `commit` (or a flag) on the legacy path.
