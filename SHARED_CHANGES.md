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
| 36 | `build_rate/brc/rates.py` | §52: `completion_rates` loops over the groups instead of `groupby.apply(include_groups=False)` (pandas ≥ 2.2), with the same sums. | pandas 1.4.4 in `switch-pg-reeds-fedpol`. | none: the 20 pipeline tables are byte-identical (old code on pandas 3.0.6 vs new code on 3.0.6 and 1.4.4) |
| 32 | `s0_workflow/specs/coal/coal_spec_converted_gas_units.csv`, `coal_spec_overrides.csv`, `coal_spec.md` | Converted-unit heat rates replaced by the latest-EIA-923 values (rev. 2.1); addendum rev. 2.1; new `by_option/` validation tables. | Tom's decisions, 2026-10-04. | none (S0 coal spec only) |

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
