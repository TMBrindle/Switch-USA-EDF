# S0 production case build

The S0 production case is built by `pg_to_switch.py` from tracked settings, with no hand steps. One
settings file, `pg/settings/s0_production.yml`, holds everything; the `s0_production` column of
`pg/extra_inputs/scenario_inputs.csv` turns it on per case. Code: `s0_workflow/production.py`,
`s0_workflow/day_selection.py`, `build_rate/brc/turbine_cap.py`, `switch/study_modules/retirement_rules.py`,
`switch/study_modules/build_rules.py`.
Changes to shared code are listed in `SHARED_CHANGES.md`; history in CHANGES §44-46.

| `s0_production` | What it builds |
|---|---|
| `off` | the case as before (every row that predates this) |
| `on` | all S0 production settings; fleet-independent days; mode A (myopic chain of single years) |
| `on_windows` | as `on`, mode B (rolling two-period windows) |
| `on_pgdays` | **legacy settings** (ATB-only gas capex, the legacy gas-turbine cap, NY RPS buyout only, the current state-policy files) with PowerGenome's k-means days and one case for all its years: the s4x1 2035 regression case, built exactly as before Oct 2026 |
| `on_pgdays_new` | as `on_pgdays` with the new defaults |

Cases:
- `S0prod_A` (on) and `S0prod_B` (on_windows), each with rows for 2028, 2030, 2035, 2040 and 2045. Both
  use the new defaults.
- `s4x1_S0prod_2035` (on_pgdays): the s4x1 2035 new-stack base for the regression against the ic_v4
  B6+B8 + NY buyout run, on the legacy settings.
- `s4x1_S0prod_2035_new` (on_pgdays_new): the same case with the new defaults.

**Defaults since Oct 2026 (CHANGES §45-49), with the legacy setting for each:**

| Setting | S0 default | Legacy (`on_pgdays`) |
|---|---|---|
| `gas_capex` | `mode: premium`, `path: central` | `mode: atb_moderate` (ATB only) |
| `gas_turbine_cap` | `form: allowance`, `path: central` | `form: legacy` (451.4 GW + 9.67 GW/yr in service) |
| `rps_acp` | `mode: flat`, $100/MWh on every state RPS and CES | `mode: programs`, NY RPS $45.39 only |
| `state_policies` | `release: "2026.09.21"`: state RPS/CES files built from the pinned ReEDS release | `release: legacy`: `emission_policies_current.csv` and `regional_resource_tags.yml` |
| `new_build_rule` | `enabled: true`: no new nuclear before the 2035 stage | `enabled: false` (no rule) |
| `coal_spec` | `enabled: true`: coal specification rev. 2 (caps by stage, fleet overrides, checks) | `enabled: false`; `coal_cf_caps` (one table, 500 MW / 0.65 fallback) |
| `coal_holds` | `scenario: s0` (axis `coal_holds`): eight units held in the 2028 stage (and 2030 with block_all) | `enabled: false` (no holds) |
| `retirements_pre2030` | `block_all` (axis and column `retirements_pre2030`): no coal or gas retirement before 2030; or `planned_only`, `unrestricted` | `legacy` (settings as they were) |

```bash
python pg_to_switch.py pg/settings switch/in/s0prod --case-id S0prod_A      # mode A: 5 stage folders
python pg_to_switch.py pg/settings switch/in/s0prod --case-id S0prod_B      # mode B: 4 window folders
```
The `--myopic` flag is not needed; the case's `foresight.mode` decides. Solve the lines of
`switch/in/s0prod/scenarios_<case>.txt` in order. VM recipes: `s0_workflow/VM_RECIPES.md`.

## What the settings replace

| Hand step in the test workflow (s0_workflow/README.md) | Setting (`s0_production.*`) | Where it happens |
|---|---|---|
| `pg_to_switch_netload.py` + `NL_DAYS_CFG` (nl24_fi) | `time_sampling` (method `fleet_independent`) | `operational_files`, per model year |
| `make_partb.py --only B6` (gas capex alias) | `gas_capex.mode: atb_moderate` (GridLab override neutralised); `premium` adds a fraction over ATB by in-service year (default central) | settings before PowerGenome; premium on `gen_build_costs.csv` |
| `b8_coalcf.py` (PUDL) | `coal_spec` (S0 default; see Coal specification below), or legacy `coal_cf_caps` from `s0_workflow/data/coal_cf_caps_eia923_2021_2024.csv` (public EIA-923/860, `scripts/fetch_coal_cf_eia923.py`) | `gen_info.csv` `gen_max_annual_availability` |
| `make_windloss_case.py` + `make_windloss2_case.py` | `wind_loss` (onshore and offshore x 0.88098) | `variable_capacity_factors.csv` |
| `make_rps_acp_alias.py` | `rps_acp` (`programs: {ESR_NY_rps: 45.39}` legacy; `flat` $100 on all state programs, the default) | `rps_requirements.csv` |
| `make_build_rate_alias.py` (central v2) | `settings.build_rate` (central, regional groups wind and solar) | build-rate case writer |
| `patch_case_inputs.py --tag ic_v4`, `--slack-cost 5e7` | `settings.interconnection_headroom` (atts_s0) and `interconnection_slack_cost_per_mw` | headroom case writer; `ic_params.csv` |
| `--include-module study_modules.gen_amortization_period` on every solve | `extra_modules` | the case's scenario line |
| `MaxCapTag_GasTurbineSupply` (make_emission_policies.py) | `build_rate.gas_turbine_cap` (pg/settings/build_rate.yml) | build-rate module |
| single-year cases only (coal lock before 2030) | `retirement_rule` + `foresight` | `retirement_rules` module; stages; `prepare_next_stage.py` |

`s0_production_log.txt` in each case folder records every step.

## Representative days (fleet-independent)

Chosen per model year from PowerGenome's own hourly load and resource profiles. 24 days (21 k-means
medoids, 3 top-load days, 3 low-net-load days, minus overlaps) plus PowerGenome's peak-load day.
Weights come from an LP. Targets, by transreg and nationally:
- mean load within 1%;
- mean onshore-wind CF within 0.010 (after the wind-loss factor);
- mean utility solar CF within 0.005;
- top-1% load hours carry 0.75-1.25% of the weight;
- bottom-1% net-load hours carry 0.5-1.5% of the weight.

"Fleet-independent": every resource cluster counts equally, whatever is built. The low-net-load tail
uses a stylised fleet: wind and solar each sized to 20% of the region's load. You can list solved
runs in `low_net_load_fleets` instead; listing the two C1 fleets should reproduce the nl24_fi days (not checked: the clusters are now classified from PowerGenome's table rather than the case's gen_info).

If a tolerance can't be met, tolerances relax x1.5, at most twice, and the factor is recorded. If
they still can't be met, the build stops. Diagnostics are in `<case>/time_sampling/<year>/`.

### Time sampling: multi-day blocks (§56)

`time_sampling.sample` chooses the sample's shape: `24` (24 single days; the default when absent) or `NxL`, N
chronological L-day blocks (e.g. `4x3`, four 3-day blocks). Each block is one timeseries, so storage state of charge,
hydro and DR carry over its L days instead of wrapping each day.

The targets and tail bands are the same as above. Weights are in days/yr (summing to 365 with the peak day), and each
block occurs at least `w_min` times a year. The selection:
1. **Candidates:** `block_pool` (16) k-means medoids of the record's aligned L-day blocks (features: the standardised
   daily load, wind and solar profiles, concatenated over the block's days), plus a block centred on each of the 3
   top-load and 3 low-net-load days. No candidate contains the peak day.
2. **Search:** every set of N non-overlapping candidates gets the weight LP. The feasible set with the smallest
   error objective wins.
3. **Relaxation only if no set is feasible:** tolerances and bands widen x1.5 per step, at most
   `block_relax_max_steps` (12) steps. With four blocks against about 40 regional targets, real data may need some.
   The factor is logged as a warning, written to `fi_info.json` (`relax_factor`, `relaxed`, and the lower bound
   `min_relax_lower_bound`), and each target's `error / base tolerance` is in `fi_target_errors.csv`.

PowerGenome's peak-load day stays a single day. The PRM stress days (§55) stay single zero-weight days, added on top.
Timeseries are named `<year>_pNxL` for blocks (N = the block's first day in the record) and `<year>_pN` for single
days. Timepoint ids have the usual format.

Model size (1-hour timepoints), each with the peak day: 24 days = 600, 4x3 = 312. The legacy reserve copies
the peak day (+24) and the regional reserve adds 10-12 stress days (+240-288). Recipe F compares the two samples.

## Gas capex premium

New-build ATB CC and CT (not CCS) cost ATB 2024 Moderate x (1 + premium). The premium is a fraction by
in-service year. Each value holds to the year shown and falls to zero after it:

| Path | CC | CT |
|---|---|---|
| central (default) | 0.37 to 2031, 0.28 (2032), 0.18 (2033), 0.09 (2034), 0 from 2035 | 0.45 to 2031, 0.34, 0.23, 0.11, 0 from 2035 |
| low | 0.50 to 2033, 0.40, 0.30, 0.20, 0.10 (2034-37), 0 from 2038 | 0.45 to 2033, 0.36, 0.27, 0.18, 0.09 (2034-37), 0 from 2038 |
| high | 0.37 to 2029, 0.25 (2030), 0.12 (2031), 0 from 2032 | 0.45 to 2029, 0.30, 0.15, 0 from 2032 |

Capacity built in a period is in service across the period's span. The premium is therefore the
mean of the yearly premiums over the span's years (`year_basis: span_mean`, the default, confirmed by
Tom in Oct 2026), the same averaging PowerGenome applies to ATB capex. For example, central CC is +37% for 2028 and 2030 and +18.4% for 2035 (2031-35).
`year_basis: period_label` uses the label year instead (2035: 0).

**Sources.** The `resources.yml` GridLab override values are not in the GridLab gas turbine cost report
(Sep 2025). The premium rests on BNEF ($2,157/kW CC, 2025), E3 RECOST Q1 2026 (about $2,500/kW CC and
$1,700/kW CT, 2030 in-service) and Enverus (about $2,000/kW, Sep 2026). The override stays in
`resources.yml` for other cases, with a source note.

## State policy buyouts

`rps_acp.mode: flat` (S0 default) sets one buyout price, $100/MWh, on every state RPS and CES program in
the case (`ESR_*`, carve-outs included, NY's RPS and CES among them). That is 54 programs in 2035 with
the pinned ReEDS release, 55 with the current file. With a buyout, a shortfall is bought at that price instead of making the case infeasible.
`study_modules.rps_regional` writes two outputs:
- `rps_shortfall.csv`, by program and period;
- `rps_buyout_by_state_year.csv`: buyout MWh and $ by state and calendar year. Values are per year and
  the same in every year of a period's span.

The legacy mode (`programs`) prices NY's RPS at $45.39 only.

## Gas-turbine supply cap

`build_rate.gas_turbine_cap` (on by default, independent of `build_rate.enabled`):

| Setting | Default | Options |
|---|---|---|
| `annual_additions_mw` | {2025: 9,666.67} | MW/yr by build year; later years repeat the last value |
| `baseline_mw`, `baseline_year` | 451,444.2, 2024 | in-service CC + CT at the baseline year |
| `coverage` | [combined_cycle, combustion_turbine] | + aeroderivative, reciprocating_engine |
| `cc_accounting` | full_plant | turbine_share (x `cc_turbine_share`, PLACEHOLDER 0.67) |
| `form` | cumulative_in_service | new_additions_per_period (window sum of annual additions) |
| `retirements_free_room` | false | true: in-service MW net of economic retirements |

The file's defaults are the legacy cap. They reproduce the old MaxCapTag cap exactly, and non-S0 cases
keep them. Output: `gas_turbine_cap_results.csv` (covered MW, cap, dual by period).

**S0 default: `form: cumulative_additions` (s0_production `gas_turbine_cap.form: allowance`).** Source:
Claude Doc "Gas-turbine supply constraint for S0" (3 Oct 2026). The rules:
- Counts cumulative NEW CC + CT additions since 1 January 2025. Planned (predetermined) units count;
  retirements are irrelevant; there is no 451.4 GW installed-base offset.
- Weighted by turbine content: CC 0.65, frame CT and aeroderivative 1.0. Reciprocating engines are
  excluded.
- In each period, weighted cumulative additions to the period's end year must be ≤ the allowance at
  that year.

Allowance paths (GW turbine-equivalent, cumulative; `build_rate.yml allowance_paths`):

| Year | 2025 | 2028 | 2030 | 2035 | 2040 | 2045* |
|---|---|---|---|---|---|---|
| low | 3.5 | 20.4 | 38.8 | 108.5 | 185.1 | 261.7 |
| central (default) | 3.5 | 24.2 | 49.5 | 155.2 | 270.1 | 385.0 |
| high | 3.5 | 30.3 | 64.8 | 206.5 | 359.7 | 512.9 |

\*2041-45 is a **coordinator estimate, not from the research doc**, which stops at 2040. Each path
continues linearly at its 2035-40 rate: central +22.98, low +15.32, high +30.64 GW/yr. The config
(`build_rate.yml`, above `extend_slope_years`) carries the same label, and the case build logs each
extended year as a coordinator estimate.

The S0 default pairs the central allowance with the central capex premium.

**Capacity basis.** The allowance is EIA-860M nameplate. Switch carries existing and planned units at
winter capacity (`resources.yml capacity_col: winter_capacity_mw`). Planned units **and new builds**
are converted to nameplate with the same nameplate / winter ratios, from EIA-860 2024 "Proposed" gas
units: CC 1.049 (24.0 GW), CT and aeroderivative 1.090 (15.2 GW). Planned and new capacity therefore
count on one basis (Tom, Oct 2026). These are `capacity_basis.predetermined_to_allowance` and
`new_build_to_allowance`; each is a number or a per-class map. A new CC MW counts 0.65 x 1.049 =
0.682 against the allowance, and a new CT MW 1.090. If planned additions alone exceed a period's
allowance, the cap is raised to them, with a warning.

## ReEDS state-policy inputs (pinned)

**Pinned release: ReEDS-Model/ReEDS 2026.09.21** (tag object 9a034b4b, commit
`8a1572331da89b15ad3c0f74db448911f715266c`, 2026-09-17). Recorded in
`pg/extra_inputs/reeds_state_policies/REEDS_RELEASE.yml`, beside verbatim copies of the inputs:
- the RPS and CES fractions;
- out-of-state limits;
- the REC table;
- technology eligibility;
- offshore mandates;
- RGGI states and cap.

`make_emission_policies.py` reads these copies and no longer downloads from main.

**Policy files by case.** The new-defaults cases (`s4x1_S0prod_2035_new`, `S0prod_A`, `S0prod_B`) use
policy files built from the release, under new names. The legacy regression case and every non-S0 case
keep the current files.

| | New defaults (`state_policies.release: "2026.09.21"`) | Legacy and other cases |
|---|---|---|
| Targets | `rggi_carbon/emission_policies_reeds_2026.09.21.csv` | `rggi_carbon/emission_policies_current.csv` (2025-12-14 from ReEDS main, regenerated 2026-05-12) |
| ESR eligibility and tag list | `reeds_state_policies/s0_state_policies_2026.09.21.yml` | `regional_resource_tags.yml`, `resource_tags.yml` |

How the files are built and used:
- **Builder.** `s0_workflow/scripts/build_reeds_state_policies.py` writes both files. It applies
  `make_emission_policies.py`'s rules for ESR targets, eligibility and carbon to the pinned copies, and
  writes only new files. It needs only repo data (the region shapefile's attribute table and
  `pg_reeds_tech_map.csv`), so the files are built and committed. `--check` confirms the committed
  files match a fresh build.
- **Case build.** `production.apply_state_policies` swaps in `emission_policies_fn`, the ESR regional
  tags and the ESR tag list. It runs in `pg_to_switch.py` before the region scope, so aggregated zones
  take the new tags. A case on another policy file (e.g. `policies: decarb`) stops with an error rather
  than being replaced silently.
- **Not rebuilt.**
  - Offshore wind mandates: the release gives the same `MinCapReq` values as `scenario_management.yml`
    in every model year (tested).
  - Growth caps: they don't come from ReEDS.

What changes with the release (`s0_workflow/data/reeds_state_policy_diff_2026.09.21.csv`, from
`scripts/compare_reeds_state_policies.py`):
- **Only targets change.** The release's ESR eligibility equals `regional_resource_tags.yml` (1,932
  region-program entries) and its tag list equals `resource_tags.yml`. The RGGI states and carbon
  columns are unchanged.
- **Program-years.** 489 change; 121 by more than 0.005. NY's RPS and CES are unchanged.
- **Largest changes, 2035:**

  | Program | Current file | Release |
  |---|---|---|
  | NC CES | 0.574 | 0.392 |
  | CT RPS (2030 on) | 0.440 | 0.330 |
  | ME CES | 0.805 | 0.867 |
  | CT CES | 0.762 | 0.794 |
  | ME RPS | 0.789 | 0.816 |
  | VA CES | 0.495 | 0.474 |
  | OR CES | 0.582 | 0.571 |
  | MD solar | 0.113 | 0.124 |

  Most others change by under 0.005.
- **Programs dropped:** AZ RPS after 2025, so the AZ regions (p27-p30) have no rows from 2026.
- **Programs added:** `ESR_NS_rps` (Nova Scotia) and `ESR_voluntary_rps`. Neither maps to a US model
  region, so neither appears in the case files.

## Coal specification (rev. 2.1)

The new-defaults cases implement `s0_workflow/specs/coal/coal_spec.md` (Tom, 2026-10-03) with its rev. 2.1
addendum (2026-10-04). It has three
parts, each checked against the spec's validation tables in the same folder.
- **Code:** `s0_workflow/coal_spec.py` (rules), `s0_workflow/coal_fleet.py` (the case build), and
  `s0_workflow/scripts/fetch_coal_spec_eia.py` (public EIA data).
- **Settings:** `s0_production.coal_spec` and `s0_production.coal_holds`.
- **Legacy:** the regression case (`on_pgdays`) keeps the earlier `coal_cf_caps` and has no overrides or
  holds.

### Data (public EIA, committed)

`fetch_coal_spec_eia.py` downloads EIA-860M (August 2026, the latest at build time; June 2025 for the
conversion-year rule), EIA-860 2020-24 and EIA-923 2021-26. It writes:

| File | Content |
|---|---|
| `s0_workflow/data/coal_cap_units_860m.csv` | 429 Conventional Steam Coal units (860M Operating, OP/SB/OA, any planned retirement) with zone and 2021-24 maximum CF |
| `s0_workflow/data/coal_fleet_860m.csv` | 860M Operating and Retired rows of every plant with a coal-group unit, with the conversion year of NG-coded units |
| `s0_workflow/data/coal_plant_st_fuel.csv` | monthly ST fuel and generation (NG, coal) of the plants with a converted unit |
| `s0_workflow/data/coal_holds.csv` | the ten held units: zone, winter MW, hold cap, S0 / holds_persist flags, encoded years, order basis |
| `s0_workflow/data/coal_model_basis_860er2024.csv` | a public reconstruction of PowerGenome's coal basis (EIA-860 2024 early release, the July 2025 860M Retired sheet), used for the per-option tables |

The script checks its results against `coal_cap_units_all.csv`, `coal_spec_hold_online.csv` and
`coal_spec_converted_gas_units.csv`:
- **Cap units:** unit max CF within 5e-5 and zones identical.
- **Holds:** CF and caps exact.
- **Converted units:** winter MW and conversion years exact; heat rates (latest EIA-923, rev. 2.1) exact.
- **Collisions:** the generator-ID collision check finds none among 1,436 coal-group units.

### Zonal caps by stage (§1)

- **Stages:** for each stage p (2028, 2030, 2035, 2040, 2045), the cap units are those with planned
  retirement blank or ≥ p, excluding the ten held units (§1.1).
- **Zone history:** H_z(p) and own_z(p) come from the cap units, and so does the national value N(p).
- **Model MW:** M_z(p) comes from the model's own unit table in the case build. It is the coal MW in service
  in p after the overrides, excluding held and converted units.
- **Cap:** if H/M ≥ 0.5, own history. If it is less, own history blended with the national value,
  (H·own + 500·N)/(H + 500). With no history, N.
- **Switch input:** `gen_max_annual_availability = min(1, cap/(1 − forced outage))` on every coal cluster
  in the zone. A case with more than one period (mode B windows) also gets
  `gen_max_annual_availability_by_period.csv`, read by `study_modules.gen_annual_availability_limits` (new
  optional input), so each period has its own cap.
- **Output and checks:** the build writes `coal_caps_by_stage.csv` and compares it with the table of the case's
  pre-2030 option, `s0_workflow/specs/coal/by_option/coal_spec_expected_caps_by_stage.<option>.csv`. Cap
  (±0.001), rule label and H (±1 MW) must match; a zone with no model coal on either side needs no cap.
  Model-MW differences above 0.1 MW from the expected table are logged, not failed (none expected, A4).

**Unit statuses.** The cap unit set is OP and SB (rev. 2.1, overriding §1.1's OA). The two OA units, Biron Mill
GEN1 (15.3 MW, p76) and WE Soda 5 (10 MW, p21), are left out (`cap_statuses: [OP, SB]`).

**Mapped plants only (A5, §54).** Cap units of plants not in `reeds_plant_map.csv` don't count in H, own or N
(`coal_spec.cap_unit_set`), as those plants aren't in the model. N(p) is 0.5791 / 0.5791 / 0.6007 / 0.6011 / 0.6011
(block_all). The largest cap changes are p70 0.5826 → 0.6210 and p99 0.1477 → 0.1229.

### Retirements before 2030 (rev. 2.1 §A3)

`s0_production.retirements_pre2030` (axis and `scenario_inputs.csv` column of the same name) has three
options for the new-defaults cases. The regression case is `legacy`, which leaves its settings as they are.

| Option | Dated coal and gas retirements before 2030 | Economic retirements before 2030 | Coal 2028 / 2030 stage (GW) |
|---|---|---|---|
| `block_all` (default) | none: fedpol's `blocked_2030_coal_gas` push (2026-29 → 2030), set by S0 whatever the `retirement_policy` axis says | none (retirement rule) | 163.59 / 163.59 |
| `planned_only` | as scheduled (predetermined overrides removed) | none (retirement rule) | 156.35 / 141.14 |
| `unrestricted` | as scheduled | allowed from the first stage (`gen_can_retire_early` 1 on existing coal and gas, no rule) | 156.35 / 141.14, before the model's economic retirements |

**block_all, by stage.**
- **Which stages:** with `--retire early`, a unit with retirement year Y runs in the stage whose period ends at
  p iff Y ≥ p. A unit dated 2026-29 is in service in the 2028 and 2030 stages and first gone from the 2035
  stage.
- **What it covers:**
  - the coal spec's dated retirements;
  - the eight S0 holds, which are held in the 2028 and 2030 stages with their own unit caps;
  - gas units whose cluster technology matches "natural gas" (combined cycle, combustion turbine).
- **Not covered:** the five out-of-service removals (Sandy Creek, Big Cajun 2-1, Merrimack 2, Warrick 2, Biron
  Mill GEN5; 1,969 MW). They are physical status, removed in every option. The case build deletes them from PowerGenome's
  unit tables before clustering (its EIA-860 units and the 860M generators it adds back), so fedpol's push never
  sees them. The build log says `coal removals: 5 removed`; any other count stops the build (§51).
- **Model fleet (coal spec rev. 2.1 A4, §53):**
  - **Not in the model:** plants missing from `reeds_plant_map.csv` (about 1.47 GW of small industrial coal and
    pet coke) never enter PowerGenome's EIA-860 units. They are a known gap for the fleet refresh.
  - **Edwardsport:** CT1 / CT2 count 2 × 240.6 MW in p107.
  - **Fleet dates:** 860M dates only. `update_coal_closures.py` (GEM dates) is not applied in the S0 build.
- **Encoding:** in the S0 new-defaults cases the hook (`coal_fleet.unit_hooks`, `push_pre2030`) encodes the
  pushed coal, held and gas units as 2031, before clustering.
  - It matches on the cluster technology after PowerGenome's grouping, as fedpol's rule does, so gas in
    `Other_peaker` is not pushed.
  - PowerGenome counts a unit in model year M only if Y > M, so with 2030 a cluster of pushed units, such as a
    hold project or an all-pushed gas cluster, would be dropped from the 2030 stage. Switch treats 2031
    exactly like 2030 in every S0 stage.
  - fedpol's `apply_predetermined_retirement_override` is unchanged; it leaves 2031 alone, and every other
    case keeps its 2030 encoding.
- **Cap unit set:** cap units with a planned retirement in 2026-29 count in H and N through the 2030 stage
  too, so the cap unit set follows the fleet.

The per-option figures and the zones whose caps change are in the addendum to `coal_spec.md`.
`planned_only` and `unrestricted` share tables: economic retirement is a solve outcome.

### Fleet overrides (§2)

The overrides are applied to PowerGenome's unit table **before clustering**, through two runtime wrappers.
They are active only while `gc.create_all_generators()` runs for an S0 case with the coal spec;
PowerGenome's files are not edited.
- **`group_technologies`:** the unit table still has EIA technologies at this step.
  - The build derives each coal-group unit's action from the latest 860M, in the spec's precedence:
    hold, then Retired, then OS, then conversion, then planned date, then keep online.
  - It sets the encoded retirement year. A unit kept online gets the operating year + retirement age, as
    PowerGenome encodes "no planned retirement".
  - Converted units are re-labelled "Natural Gas Steam Turbine" with NG fuel and the 860M winter MW, so
    they join the zone's `other_peaker` cluster.
  - Held units get their own technology (see Holds).
- **`atb_fixed_var_om_existing`:** sets the converted units' heat rates. This is the plant's ST/NG fuel ÷
  generation in the latest year with ≥ 10 GWh. With no gas history it is the 2024 coal heat rate, flagged.
  - It uses the latest EIA-923 (rev. 2.1): North Valmy 11.42, Montour 10.11, Pawnee 10.96, Harrington
    10.83, Rogers 10.55, all 2026 ST/NG. These are now the values in `coal_spec_converted_gas_units.csv`.
  - The build checks them (±0.01). `heat_rate_data_through: "2025-05"` gives the rev. 2 values.
- **Pre-2030 push:** with `retirements_pre2030: block_all`, coal-group and held units dated 2026-29 are encoded
  2031 here (see Retirements before 2030). Only units with an override row change their year; a unit the
  basis already retires by its 860M retirement keeps its basis year.
- **Check:** the build writes `coal_overrides_applied.csv` and compares it with `coal_spec_overrides.csv`.
  The units and actions must be the same, the effective year exact and MW within 0.1.
  - S0 has 71 rows: the 73 minus the two Intermountain rows, which apply only to `holds_persist`.
  - Derived from a model basis built from the spec's own rows, the rules reproduce all 71 (73 in
    holds_persist), and the other ~360 coal units give no extra rows.

**Unchanged by decision (§2.5):**
- **Petroleum coke and IGCC** (17 petroleum-coke units, 1,319 MW, and Edwardsport IGCC ST, 578 MW) stay in
  the coal cluster: PowerGenome's tech group `Conventional Steam Coal` includes them, with `# TODO: treat
  as separate fuel` on the Petroleum Coke line. They count in M_z and get the zone cap, which is computed
  from conventional steam coal history only. To be split out in the full fleet refresh.
- **Edwardsport (plant 1004)** is left as is. The August 2026 860M codes CT1/CT2 as natural gas combined
  cycle and the ST as IGCC. When a later 860M codes all three consistently, the conversion rule applies.

### Holds (§3)

- **The holds:** `coal_holds.scenario` (axis `coal_holds`, column `coal_holds` in `scenario_inputs.csv`) is
  `s0` (default) or `holds_persist`.
  - `s0` holds Centralia 2, Campbell 1/2/3, Schahfer 17/18, Culley 2 and Craig 1 (3,238 MW) in the 2028
    stage only. Their encoded retirement year is 2029: in service in period 2028, retired from 2030.
  - `holds_persist` adds Intermountain 1/2 (cap 0.001) and holds all ten (5,038 MW) through 2045.
- **Own projects:** each held unit is its own single-unit project, technology "Conventional Steam Coal Hold
  <plant> <gen>". The coal entries of PowerGenome's technology-keyed settings (fuel, ATB O&M map, startup
  costs; `num_clusters` 1) are copied to it, and the model tags match it by substring. So it keeps its own
  heat rate, O&M and outages, and the resource is `p<zone>_conventional_steam_coal_hold_<plant>_<gen>_1`.
- **Switch input:** `gen_max_annual_availability = min(1, hold cap/(1 − forced outage))`, with a 0.001
  floor so the unit still counts for adequacy (§3.1). `gen_can_retire_early` is 0.
- **Zone values:** held units are excluded from every stage's H, M and N, so the zone caps are the same in
  both scenarios.
- **Check:** the build writes `coal_holds_by_stage.csv` and compares it with `coal_spec_hold_by_stage.csv`
  (exact).

## Forced transmission (§54)

- **Source:** `s0_production.forced_tx: reeds_certain` (S0 default). The forced lines are ReEDS 2026.09.21's certain
  additions (`inputs/transmission/hvdc_planned-baseline.csv`, `certain == 1`, pinned in
  `pg/extra_inputs/transmission/reeds_2026.09.21/`): SunZia p28-p31 3,000 MW (2026, 2028 stage) and TransWest
  Express p24-p25 3,000 MW (2032, 2035 stage).
  - **Why this file:** the release has no `transmission_capacity_future_*` file. ReEDS adds AC capacity through its
    transfer limits, and its "certain" status is the `certain` column of the planned-HVDC file.
  - **Mapping:** the endpoints are placed in our zones with the ReEDS BA shapes, and the year goes to the first
    model period at or after it.
  - **No double counting:** neither line is in the 2024 starting capacity (NARIS 2024 AC ITLs, non-AC 2024 lines,
    ReEDS's `hvdc_existing.csv`).
  - **Table:** `forced_tx_reeds_certain_2026.09.21.csv`, built by `s0_workflow/scripts/build_reeds_forced_tx.py`.
- **Option:** `forced_tx: named_projects` keeps the 72-line list of `transmission_connections.csv` (`new_cap_mw`,
  245,857 MW) for sensitivities. It is the list every non-S0 case uses.
- **Totals** (`forced_tx_comparison_2026.09.21.csv`):

  | option | 2028 | 2030 | 2035 | total MW | MW-km | interregional share |
  |---|---|---|---|---|---|---|
  | reeds_certain | 3,000 | 0 | 3,000 | 6,000 | 3.76 M | 50% (TransWest Express) |
  | named_projects | 36,976 | 53,692 | 155,189 | 245,857 | 71.56 M | 26.4% |

- **Limit:** `forced_tx_expansion_limit: minimum` (S0 default). A forced line's `trans_path_expansion_limit` is its
  minimum in its forced period, and the case's rule in other periods (0 with `trans_expansion: zero`). Before
  this, forced lines were left out of the file, so they could expand without limit. Other cases keep that.
- **Comparison pair:** `s4x1_S0prod_2035_txreeds` / `s4x1_S0prod_2035_txnamed` (VM_RECIPES recipe D).

## Planning reserve requirement (§55)

`s0_production.prm.design`: **regional** (S0 default; `S0prod_A`, `S0prod_B`, `s4x1_S0prod_2035_prm`) or **legacy**.
Legacy is today's per-zone `planning_reserves` plus the extreme-day block. The regression case and the other 2035
comparison cases keep legacy (column `prm_design`). Code:
- `s0_workflow/prm.py` (case build);
- `switch/study_modules/prm_regional.py` (Switch);
- inputs from Tom's research config, committed verbatim as `s0_workflow/specs/prm/prm_redesign_config.yaml`.

### What the current design does (items 9 and 10)

Today's `planning_reserves.py` (the legacy design, unchanged):
- **Regions:** 134 single-zone regions; `prr_enforcement_timescale: peak_load` checks one timepoint per region and
  period. That timepoint is the region's highest `zone_demand_mw` among all timepoints, so it can be a weighted
  sample hour rather than the extreme-day block.
- **Requirement:** (1 + margin) × `zone_demand_mw` / (1 − local T&D loss rate).
  - (c) This is the energy balance's central-node load for base demand, T&D losses included. Other loads that
    modules add to the energy balance are left out.
  - Distributed generators count at their hourly capacity value (PV: its CF) × the T&D gross-up, so distributed PV
    already counts at its output in that hour.
- **Credit:** thermal and hydro at full nameplate; wind and solar at the hourly CF; storage at net scheduled
  dispatch.
- **Imports:** `TXPowerNet`, the zone's scheduled net flow in that hour.
  - (a) After line losses (received = dispatch × efficiency), with no derate on new lines.
  - Capped at `prr_max_tx_import_share` of the requirement (0.5; 1e-6 for CAISO's three zones, a workaround for
    a 0.0 that dropped out of the constraint).
- **(b) Pooled regions:** with more than one zone, capacity is summed and internal flows cancel (except their
  losses). There is no per-zone deliverability check.
- **10. Storage and imports:** the import cap applies only in the one checked timepoint. Storage can charge from
  imports in the block's other hours and count as local capacity at the peak.
- **`planning_reserves_extreme_days.py`:** adds margin × load as a withdrawal in the energy balance on the duplicated
  national-peak block, so the model must dispatch to serve inflated load there. It is not a capacity constraint.

### The regional design

1. **Regions:** 16. ReEDS `nercr` from `hierarchy.csv`, with WECC_NW split by transgrp (NorthernGrid_West,
   NorthernGrid_South, NorthernGrid_East, WestConnect_North). Canada and Mexico zones have no region.
   Zones per region: MISO 32, PJM 22, SPP 17, NorthernGrid_East 9, NorthernGrid_West 7, ERCOT 7, WECC_SW 6,
   NPCC_NE 6, NorthernGrid_South 5, WestConnect_North 5, SERC_SE 4, SERC_E 4, WECC_CA 3, SERC_C 3, SERC_F 2, NPCC_NY 2.
2. **Margins:** `margin(r, p) = (1 + RML(r, p)) × (1 − FOR_w(r, p)) − 1`.
   - **RML:** the NERC 2025 LTRA reference margin level, linear from 2026 to 2030 and flat after.
   - **FOR_w:** the region's capacity-weighted normal-weather forced-outage rate. It uses ReEDS static rates (gas
     0.05, coal 0.08, oil/gas steam 0.05, nuclear 0.03, geothermal 0.129, biopower 0.09) over the existing thermal
     fleet in service in period p.
   - **Why this form:** RML applies to nameplate (ICAP), and thermal capacity counts here at 1 − FOR, so
     (1 + RML) × ICAP ≈ (1 + RML) / (1 − FOR_w) × derated capacity. The margin on derated capacity is therefore
     (1 + RML)(1 − FOR_w) − 1. Simply subtracting FOR_w from RML differs from this by RML × FOR_w (about 0.01).
   - **Values on the committed `s4x1_fedpol_current` fleet** (each build recomputes FOR_w from its own fleet; file
     `prm_margin_basis.csv`):

     | region | RML 2028 / 2030+ | thermal GW | FOR_w | margin 2028 / 2030 / 2035 | import share |
     |---|---|---|---|---|---|
     | PJM | 0.2245 / 0.263 | 172.6 | 0.0517 | 0.1607 / 0.1976 / 0.1976 | 0.079 |
     | MISO | 0.083 / 0.085 | 144.7 | 0.0559 | 0.0205 / 0.0241 / 0.0244 | 0.132 |
     | SPP | 0.190 | 60.6 | 0.0589 | 0.1200 / 0.1200 / 0.1199 | 0.075 |
     | ERCOT | 0.1375 | 81.5 | 0.0536 | 0.0763 / 0.0765 / 0.0765 | 0.009 |
     | NPCC_NY | 0.150 | 31.7 | 0.0481 | 0.0947 | 0.155 |
     | NPCC_NE | 0.132 / 0.130 | 27.1 | 0.0487 | 0.0768 / 0.0749 / 0.0749 | 0.073 |
     | SERC_C | 0.150 | 18.5 | 0.0467 | 0.0889 / 0.0924 / 0.0963 | 0.104 |
     | SERC_E | 0.150 | 45.8 | 0.0507 | 0.0906 / 0.0914 / 0.0917 | 0.064 |
     | SERC_SE | 0.150 | 62.9 | 0.0516 | 0.0907 | 0.104 |
     | SERC_F | 0.150 | 59.8 | 0.0508 | 0.0916 | 0.055 |
     | WECC_CA | 0.198 / 0.193 | 39.3 | 0.0533 | 0.1342 / 0.1294 / 0.1294 | 0.220 |
     | WECC_SW | 0.1275 / 0.122 | 23.5 | 0.0480 | 0.0693 / 0.0645 / 0.0682 | 0.273 |
     | NorthernGrid_West | 0.1665 / 0.155 | 9.3 | 0.0485 | 0.1099 / 0.0990 / 0.0990 | 0.140 |
     | NorthernGrid_South | 0.129 / 0.123 | 15.1 | 0.0626 | 0.0592 / 0.0531 / 0.0527 | 0.140 |
     | NorthernGrid_East | 0.129 / 0.123 | 6.4 | 0.0653 | 0.0552 / 0.0496 / 0.0496 | 0.140 |
     | WestConnect_North | 0.1675 / 0.157 | 12.2 | 0.0614 | 0.0930 / 0.0860 / 0.0860 | 0.140 |

3. **Stress days:** about 10-12 real days per model year from the 7 weather years (2007-2013, 365-day years). They
   are chosen by greedy coverage of three needs per region:
   - its worst summer peak-load day (Jun-Sep, daily maximum load);
   - its worst winter peak-load day (Dec-Feb);
   - its worst low wind/solar high-load day: daily maximum net load with the fleet-independent day selector's
     stylised fleet (wind and solar sized to supply 20% of the region's load each).

   A day covers a need when it is within 2% of the worst value. The tolerance widens in 2% steps until at most 12
   days cover every need. `<case>/prm/<year>/stress_days.csv` lists which day covers which region and need, and
   `stress_coverage.csv` gives each need's worst day and the covering day(s).
   - **Weight:** zero (capacity only).
   - **IDs:** timeseries `<year>_pN_prm`, timepoints `9<id>`, so a stress day can also be a sample day.
   - **Enforcement:** the requirement is checked in every stress hour and only there. The S0 scenario line
     excludes `planning_reserves` and `planning_reserves_extreme_days`, and the case drops the extreme-day script.
   - **Samplers:** works with both PowerGenome's k-means days and the fleet-independent selector.
4. **Thermal and hydro:**
   - **Thermal:** nameplate × (1 − FOR) in each stress hour, using the murphy2019 curves (checked against ReEDS
     2026.09.21's `outage_forced_temperature_murphy2019.csv`; the config's `steam_coal_ogs` is ReEDS's `steam`).
   - **Curve per class:** CC → combined_cycle; CT, aeroderivative, ICE and Other_peaker → combustion_turbine;
     coal and gas steam → steam (coal / o-g-s); nuclear → nuclear; petroleum liquids → diesel. Geothermal,
     biopower and other thermal use their static rate.
   - **Temperatures:** ReEDS reads hourly temperatures by state from `inputs/profiles_temperature/temperature_state.h5`.
     That file is not in the release's git tree, and neither the repo nor PowerGenome's data here has hourly
     temperatures, so the default is the **seasonal** fallback:

     | stress day's month | temperature | e.g. CT / CC / coal / nuclear |
     |---|---|---|
     | Nov-Mar (winter) | the cold end, -15 °C | 0.199 / 0.149 / 0.133 / 0.019 |
     | May-Sep (summer) | the hot end, 35 °C | 0.066 / 0.072 / 0.140 / 0.124 |
     | Apr, Oct | normal weather (static) | 0.05 / 0.05 / 0.08 / 0.03 |

   - **Hourly option:** `thermal_derate.method: hourly` with `temperature_h5:` set. It reads the file as ReEDS does,
     converts it to `temperature_tz` (Etc/GMT+6), maps zone → state (`hierarchy.csv` `st`), and interpolates the
     curve per stress hour, capped at 0.4 as in ReEDS.
   - **Hydro:** its dispatch in each stress hour, which the hydro module limits to the day's water. Storage counts
     its net dispatch; demand response (`load_growth`, `us_exports`) and cross-border import generators count their
     dispatch; wind and solar count CF × capacity; distributed PV counts at its CF × the T&D gross-up.
5. **Imports and deliverability:** reserve transfers (`PrmFlow`) on every line, separate from energy dispatch.
   - **Losses:** delivered = PrmFlow × the line's efficiency (the energy balance's).
   - **Limits:** (existing + 0.85 × new builds) × derating factor, and the NARIS directional limits where set.
   - **Zone check:** each zone's requirement is met by local credit plus net inflows over its lines, and the margin
     applies to each zone's load.
   - **Import cap:** net imports from other regions ≤ share × the region's peak load in the period, in **every**
     stress hour. Shares are ReEDS's 99.9th percentile from the config; WECC_NW's 0.140 is used for all four of its
     parts.
   - **Relax setting:** `imports.mode: flat` (S0) | `relaxed` (ReEDS's 2031_hist/2050_100) | `none`.
   - **CAISO workaround:** the 1e-6 isn't needed; a blank share means no cap, and any share, including 0, is
     enforced.
6. **Storage:** credit = discharge − charge in each stress hour. The storage module tracks state of charge through
   each stress day (cyclic within the day), so storage can only discharge energy it charged that day, and the
   charging counts against the requirement in the hours it happens. Tests show storage can't help with a deficit in
   every hour, and can't "launder" imports (below).
7. **Shortfall:** `PrmZoneShortfall[z, p]` (MW) in every stress hour of the period, summed by region. It costs
   $300/kW-yr (`penalty: central`; the config's value, set just above PJM's gross CT CONE of $283, nominal
   2028$).
   - **Conversion:** treated as 2028$ and converted to the model's 2024$ with CPI-U to 2024
     (`interconnection_headroom/data/reference/cpi_u_annual.csv`) and 2.5%/yr after that:
     $300 / 1.025^4 = **$271.79/kW-yr**.
   - **Sensitivity:** `penalty: low` = $106/kW-yr (PJM RTO Net CONE, 2025$) = **$103.41/kW-yr** in 2024$.
   - **Output:** `prm_shortfall.csv` by region and period.
   - **Note:** the slack applies in every stress hour, so in hours when storage charges it also relaxes the
     requirement. A shortfall therefore reads as "MW short in the worst hour, given storage could also charge on it".
8. **Diagnostics** (post_solve, every run):
   - `prm_zone_hours.csv`: requirement, local credit, net inflow, dual, and price in $/kW-yr.
   - `prm_region_hours.csv`: margin achieved = (local + net imports) / load − 1, import use, and import-cap duals.
   - `prm_summary.csv`: by region and period, the minimum margin achieved, the hour, the maximum import use and the
     reserve price.
   - `prm_capacity_credit.csv`: implied capacity credit by class and region, Σ_t |dual_t| × credited MW_t /
     (Σ_t |dual_t| × capacity). It is blank when no stress hour has a price. Next to it are PJM 2029/30, NYISO
     2026/27 (ROS and NYC), ISO-NE preliminary (summer and winter) and SPP 2024 (summer and winter) values from the
     config.
   - **Duals:** they come from `study_modules.write_dual_costs` (in `modules.txt`, so every run gets the `dual`
     suffix). With S0's Gurobi barrier (`method=2 crossover=0`), LP duals are returned from the interior solution.
     The S0 cases are LPs (no unit sizes or minimum builds); a MIP would give none, and `prm_summary.csv` reports
     `duals_available`.
   - **Sign:** solvers differ in the sign they report for ≥ constraints, so prices and weights use the magnitude.

### Related: the in-state generation rule

`study_modules.gen_zone_ratio` has the same laundering issue: it counts battery discharge as in-state generation
even when the battery charged from imports. A fix was proposed in `docs/plans/gen_zone_ratio_import_cap.md` (P-11,
on the VM checkout) and not implemented. S0 doesn't use `gen_zone_ratio`, so it is left as is.

## 2045 load entries

PowerGenome needs a `flexible_demand_resources` entry for every model year. The 2045 stage needed:
- `edf_epri_med` 2045 and `epri_high` 2040 and 2045 (load-growth axis);
- `flexible_load.yml` 2040 and 2045 (`us_exports`).

They were added in Oct 2026. The follow-up, generating them from the model-year list, is open; see
`Guides and documentation/load_growth.md`.

## Bounded foresight

Production years: 2028, 2030, 2035, 2040, 2045. Period spans, from `model_definition.yml` and
checked against `s0_production.period_spans`:

| Period | Span | Length |
|---|---|---|
| 2028 | 2026-2028 | 3 yr |
| 2030 | 2029-2030 | 2 yr |
| 2035 | 2031-2035 | 5 yr |
| 2040 | 2036-2040 | 5 yr |
| 2045 | 2041-2045 | 5 yr |

Modes:
- **Mode A (myopic):** five single-year stages. Each commits its period.
- **Mode B (windows):** stages 2028-30, 2030-35, 2035-40 and 2040-45. Each commits its first period; the
  last commits both. The next window starts from the committed fleet.

Each stage folder has `stage_info.csv` (`commit_period`, `next_stage`). `prepare_next_stage.py` hands
on only what was committed:
- builds with a build year up to the end of the committed period;
- retirements in committed periods;
- transmission built to the committed period;
- headroom used, uprates and hosted headroom as of the committed period;
- the build-rate ramp history from the committed period.

How the period length enters the model:
- **Costs.** Every year of the span carries the period's annual costs, discounted. ATB capex is the
  mean over the span (PowerGenome).
- **Annuities.** New capacity pays overnight cost x crf(interest rate, `gen_amortization_period`)
  every year it is in service, so a 2-year period carries two years of annuity and a 5-year period five.
- **Build-rate limits.** The build window W is the span length. Cumulative ceilings are sums of
  annual rates over the window. The gas-turbine cap is checked on in-service MW at the period
  (cumulative form) or on the window sum (additions form).

**Retirement rule.** Existing coal and gas can't retire economically in periods before 2030; they can
from 2030 (`study_modules.retirement_rules`). The single Can_Retire flag held them for a whole case,
so any case or window starting before 2030 locked coal in for every period. The
`blocked_2030_coal_gas` predetermined override still moves planned 2026-29 retirements to 2030.

**No new nuclear before 2035** (`new_build_rule`, `study_modules.build_rules`). In the new-defaults
cases (`s4x1_S0prod_2035_new`, `S0prod_A`, `S0prod_B`), no new nuclear can be built in periods before
2035. Periods 2028 and 2030 are blocked and 2035 is open, in a single-year stage and in a window
alike.
- **Why it's needed:** the nuclear growth cap (`MaxCapTag_NuclearGrowth`) counts existing nuclear too.
  If existing plants retire, it can leave room for new units in 2028 or 2030. The rule doesn't depend
  on the cap.
- **What it covers:**
  - Generators whose `gen_tech` contains "nuclear" (large and SMR). The rule is keyed on their energy
    source (`uranium`); the case build stops if another technology uses that source.
  - Existing and planned (predetermined) units are not affected.
- **Files:** `build_rules.csv` (`gen_energy_source`, `br_no_new_build_before`) in the case, the module
  on the scenario line, and `build_rules_check.csv` (new MW by source and period) in the outputs.
- **Legacy:** off for the regression case (`on_pgdays`), which builds exactly as before.

## Tests

```bash
SWITCH_SRC=<switch checkout> pytest -q s0_workflow/tests
```
Covers:
- settings, axis and rows;
- the case steps;
- the day selector's targets, tails and fleet independence;
- the scenario lines for modes A and B;
- the gas-turbine cap: the legacy defaults equal the old values, with a toy solve identical to
  MaxCapTag; the allowance form's paths, extension, weights, capacity basis and floor, with a toy
  solve;
- the gas capex premium paths and year basis;
- that the legacy settings rebuild the regression case byte for byte as before;
- the flat and legacy buyouts and the by-state output;
- the ReEDS pin and its diff report; the policy files built from the release (current with a fresh
  build, targets equal to the release, eligibility and carbon unchanged, offshore mandates equal), and
  their use by the new-defaults cases only (legacy and non-S0 cases unchanged, applied before the
  region scope);
- the retirement rule on the toy;
- the new-build rule on the toy (no new nuclear before the rule's period, planned units untouched),
  its case writer, and that it is off for the legacy case;
- the coal specification (`test_coal_spec.py`):
  - the committed cap units against the spec's unit tables;
  - each stage's H, own, N, caps and rule labels against `coal_spec_expected_caps_by_stage.csv`;
  - ID normalisation and the coal-only collision check; zone placement order;
  - the overrides derived from the 860M against `coal_spec_overrides.csv` (both hold scenarios), and the
    unit edits;
  - conversion years and heat rates (spec vintage and latest);
  - hold caps and holds by stage;
  - the PowerGenome wrappers on a stand-in module;
  - the override exemption in `pg_to_switch.py`;
  - the case-build caps, hold caps and checks, including a failing check;
  - per-period caps on the toy;
  - settings, axis and column; the legacy case untouched;
  - the three pre-2030 options: settings, retirement rule, push semantics, per-option tables (committed = fresh
    build; planned_only = rev. 2 tables), and the case build against each option's tables;
  - the 2045 load entries;
- toy chains for modes A and B;
- committed handoff of headroom and build-rate state;
- the regression comparison script.
