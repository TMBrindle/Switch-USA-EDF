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
- `s4x1_S0prod_2035_new` (`on_single` since §66): the 2035 case on the final S0 configuration (one stage).

**Final S0 configuration (CHANGES §66, Tom's decisions, Oct 2026):** `S0prod_A`, `S0prod_B`, `s4x1_S0prod_2035_new` and
the S0_tx-based bill and S-scenario cases build with:
- the regional reserve (demand response off) on 24 fleet-independent single days plus the stress days, chosen by
  the greedy 12-day cover at 6% since §70 (below; `cover_plus_interconnect_wind` of §68 and the guaranteed rule of
  §66 are options), with light stress days and compact reserve rows (§69-§70);
- the S0_tx transmission baseline: `reeds_certain_plus_A` forced, national discretionary cap 0 in 2028 and 1.4 TW-mi/yr
  from 2030, interregional moratorium to 2040 (`tx_bill = s0_tx`);
- RGGI 3PR with Virginia from 2028; the CA/WA central linked price, with the imports generators in p11 / p1 / p3;
- the lifetime backstop (coal 65, gas 55 years), retirement friction 0.5 and existing units' fixed O&M by period.

See "Final configuration (§66)" below. The 2035 comparison rows (`_txreeds`, `_txnamed`, `_prm`, `_fi24`, `_fi4x3`) keep
their own transmission and reserve columns (`tx_bill = legacy` now sets `tx_policy: legacy` explicitly) and take the
other §66 defaults.

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

**Per-period settings (S0 itself sets none):**
- **`level_overrides`** (one value in every period) and **`levels_by_period`** (`{period: value}`; each key holds
  until the next, earlier periods take the first) accept these keys:
  - `build_rate`: a level from `build_rate/config.yaml` (central, high, reform, reform_bp, high_reform, ...);
  - `interconnection_headroom`: a scenario (atts_s0, atts_planned, atts_reform, atts_reform_techmax, ...);
  - `gas_turbine_cap`: an allowance path (low, central, high), with `form: allowance` (§75).
- **`"off"`** (quoted in YAML) means no limit of that kind in the period (§73, §75).
- **`tx_policy.mode`:** `legacy`, `national_cap` (S0_tx and the bill rows), or `unconstrained` (§75: the
  national-cap line handling and forced lines, without the moratorium or the cap).
- **Where they're used:** the S-set rows (`Guides and documentation/s_set_scenarios.md`) and the bill rows.

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

## RGGI (§62)

The cap, auction reserve price (floor) and Cost Containment Reserve all follow the RGGI Third Program Review (3PR)
Model Rule in every S0 case. Setting: `s0_production.rggi`.

- **Cap:** `pg/extra_inputs/rggi_carbon/rggicon_3pr.csv` (3PR Model Rule, May 2026), through the policy files. For S0
  that is `emission_policies_reeds_2026.09.21.csv`, built by `build_reeds_state_policies.py`. It declines to 2037 and
  is held at the 2037 level after.
  - **Superseded:** the pinned ReEDS copy `reeds_state_policies/emission_constraints/rggicon.csv` has an older,
    shallower trajectory. Nothing uses it; don't use it.
- **Floor and CCR** (`rggi.mode: 3pr`, the S0 default): `rggi_3pr_parameters.csv` gives, for 2027–2037, the auction
  reserve price and both CCR tiers (trigger prices and volumes). `production.apply_rggi` gives each S0 case its model
  year's values through the existing mechanisms:
  - `carbon_floor_price_by_program` → the `FloorAllowances` floor in `carbon_policies_regional.csv`;
  - `carbon_ccr_prices` and the `carbon_ccr` pools → the `ETS 1_CCR1` / `_CCR2` tiers in `carbon_policies_ccr.csv`.

  This applies whatever the policies preset. Before, only the `current` preset set a floor and CCR, and only for
  2028, 2030 and 2035, so every S0 case (all on `S0_uncapped`) ran with neither.
- **After 2037:**
  - floor and trigger prices keep rising 7% a year: the Model Rule's rate, which is the file's own year-on-year growth
    to the cent for 2027–2037. The escalation is applied to the short-ton prices and converted at / 0.907185 to the
    cent, as the file does;
  - CCR volumes are held at 2037's;
  - the cap is held at 2037's.
- **No emissions containment reserve (ECR):** the Third Program Review removed it, and the model has none.
- **`legacy`:** the preset's own values. This is the regression case (`on_pgdays`, `S0_uncapped`: no floor or CCR), so
  it stays byte-identical. Non-S0 cases (fedpol's included) are unchanged: this is an S0 setting, not a preset edit.

Values ($ and tonnes per metric tonne; RGGI10, no Virginia):

| Model year | Cap (t) | Floor | CCR tier 1 trigger | CCR tier 2 trigger | CCR volume per tier (t) |
|---|---|---|---|---|---|
| 2028 | 55,411,816 | 10.62 | 23.01 | 34.50 | 10,656,120 |
| 2030 | 39,579,868 | 12.15 | 26.34 | 39.50 | 10,656,120 |
| 2035 | 12,520,768 | 17.04 | 36.93 | 55.39 | 10,656,120 |
| 2040 | 8,191,313 | 23.90 | 51.80 | 77.69 | 10,656,120 |
| 2045 | 8,191,313 | 33.52 | 72.65 | 108.96 | 10,656,120 |

The `current` preset's own 2028 tier-1 trigger is 23.00 (rounded); the S0 cases use the file's 23.01.

### Reported allowance price (§88)

`carbon_program_clearing_prices.csv` (`study_modules/carbon_policies_regional.py`) takes a hard-cap program's price
from the LP duals:
- **No CCR tier used:** the cap's dual (scarcity price, or the floor).
- **Tier 1 partly used:** tier 1's trigger.
- **Tier 1 exhausted:** tier 1's trigger + its pool rent (the reduced cost of its purchases). This equals tier 2's
  trigger when tier 2 is partly used, and lies above it when both tiers are exhausted.

**When a tier counts as used:**
- **Used:** purchases above 1e-4 × its pool. Barrier solutions without crossover leave small residuals, which don't
  count.
- **Exhausted:** purchases within 1e-4 × the pool of the full pool.

**New columns:**
- `price_source`;
- `cap_dual_price_dollar_per_tco2`, the check (in an exact LP solution the two prices agree);
- `ccr_tierN_rent_dollar_per_tco2`.

**Dual export:** the cap's dual (`Enforce_Regional_Carbon_Cap`) is always written to `dual_costs.csv`. Before, it was
skipped because its bound is 0.

**Before §88:** the price was the trigger of the highest tier with purchases above 0.001 t. A barrier residual on
tier 2 therefore reported tier 2's trigger, and both tiers exhausted reported tier 2's trigger, understating the
price.

## California-Washington carbon (§65)

One linked-market carbon price for California (`ETS 2`) and Washington (`ETS 3`) from 2028. Setting:
`s0_production.ca_wa_carbon`. It replaces the presets' flat $33.43/t.

- **How it bites:** both programs have a zero cap in the policy files, and the price is their per-tonne cost
  (`carbon_cost_by_program` → `carbon_cost_dollar_per_tco2`). Every tonne of power-sector CO2 in their zones pays the
  full price, in dispatch and in investment. Free or consigned allowances don't lower it.
- **Prices**, 2024$ per metric tCO2 (the model's dollar year, `target_usd_year: 2024`, so no conversion; the build
  stops if they differ). Linear between model years, held after 2045:

  | Path | 2028 | 2030 | 2035 | 2040 | 2045 | Source |
  |---|---|---|---|---|---|---|
  | central (S0 default) | 48.6 | 53.4 | 67.8 | 86.0 | 109.2 | CARB ISOR (Jan 2026), Table 21: midpoint of the auction floor and APCR Tier 1 |
  | low | 29.1 | 32.0 | 40.6 | 51.6 | 65.4 | floor (Greenline/EDF; WA Ecology 26-14-020) |
  | high | 52.8 | 74.8 | 149.1 | 189.2 | 240.0 | Bushnell (Feb 2026) |

  **Caveat:** CARB calls these scenario assumptions, not market projections. Switch the path with the `ca_wa_price`
  column (`central` / `low` / `high`).
- **Import cost:**
  - Unspecified imports into a CA or WA zone from a zone outside both pay price × the state's default emission
    factor per delivered MWh:
    - CA: 0.428 tCO2/MWh (CARB default, reaffirmed Jan 2026);
    - WA: 0.437 tCO2/MWh. **This WA factor is unverified.**
  - The charge is directional (the import direction only), so CA↔WA flows and exports pay nothing. It is in
    `trans_import_cost.csv`, read by `study_modules.trans_hurdle_cost`, and reported in
    `trans_import_cost_results.csv`.
  - 2035, central: $29.02/MWh into CA and $29.63/MWh into WA.
- **CA and WA zones:** the zones of the ETS 2 / ETS 3 programs in the case's `carbon_policies_regional.csv` (the zones
  whose emissions are priced): CA p8–p11 and WA p1–p4. They are checked against `hierarchy.csv` states, and the build
  stops if one isn't in its state.
- **No double counting:**
  - The model had no CA or WA import carbon charge before.
  - The existing hurdle (`trans_hurdle_cost.csv`, cost_hurdle_intra) is a symmetric market-friction charge on lines
    crossing hurdle regions (about $4–5/MWh), not a carbon cost. The import cost is added to it.
- **Not covered:** the "imports" generators inside CA and WA zones (Canada into p1/p3, Mexico into p11) are generators,
  not transmission flows, so they don't pay the import cost.
- **`legacy`:** the presets' $33.43 and no import cost. That is the regression case, which stays byte-identical. Non-S0
  cases are unchanged.
- **S0 v3.1 (§93):** the two-tranche CA charge. CA imports are free up to 49.4 TWh/yr and every MWh above pays the
  full rate; see "Two-tranche CA import charge" below. The text above describes v3's charge
  (`import_charge: all_default`).

### Import charge options (§86)

`ca_wa_carbon.import_charge` sets the charge on transmission imports into CA and WA zones. The S0 v3.1 default is
`two_tranche` (§93, Tom's decision). It was `all_default` in v3 and `unspecified_share` at 96b4780.

| Option | Charge into CA | 2035 central into CA |
|---|---|---|
| `two_tranche` (S0 v3.1, §92–§93) | free up to 49.4 TWh/yr of CA imports per period; price × 0.428 on every MWh above | $0 below the tranche, $29.02/MWh above |
| `all_default` (v3, §65) | the state default factor on every MWh (CA 0.428, WA 0.437) | $29.02/MWh |
| `unspecified_share` (96b4780, §91) | share × default + (1 − share) × `specified_factor` per MWh (a number, default 0, or `source_table`) | $3.95/MWh (share 0.136, specified 0) |
| `source_table` | the exporting zone's factor, from a table | depends on the exporter |

WA has v3's per-MWh charge (0.437) under every option except `unspecified_share` with a WA share below 1.

- **Why:** under CARB's rules only unspecified imports pay the 0.428 default. Specified imports (owned or contracted,
  directly delivered) carry their source's factor: hydro, nuclear and renewables ≈ 0, coal and gas their own rates.
  The §65 charge puts 0.428 on every MWh. Attribution run A8 showed this raises CA/WA in-state emissions by about
  20 Mt in 2035, because imports cost more than in-state gas.
- **Unspecified share data** (CEC Total System Electric Generation; CEC power-mix categories, **not** CARB MRR's).
  CARB's MRR files on specified versus unspecified MWh could not be fetched (ww2.arb.ca.gov is blocked from here), so
  this calibration needs checking against them.

  | Year | Unspecified imports, GWh | Total imports, GWh | Share | Note |
  |---|---|---|---|---|
  | 2022 | 20,428 | ≈ 83,963 | ≈ 0.243 | Imports derived: total system 287,220 − in-state 203,257 (secondary source) |
  | 2023 | 10,373 | 76,400 | 0.136 | Default: the latest year with both figures |
  | 2024 | 4,051 | n/a | n/a | Total imports not found; the share is still falling |

  - CARB inventory 2020: specified-import emissions ≈ 9.8 MMTCO2e, unspecified ≈ 8.8. That is about half of import
    emissions, from far fewer MWh.
  - WA: no Washington figure was found, so CA's share is used. **This is an assumption.**
  - `unspecified_share` takes a number or a `{year: share}` path per state (linear between keys).
- **Exporting-zone factors:** `source_table` is a CSV with columns `zone`, `tco2_per_mwh` and an optional `period`.
  A row with a period wins over a row without one. Every exporting zone needs a row, or the build stops.
  - `s0_workflow/scripts/zone_import_factors.py` derives the table from a solved reference case's
    `dispatch_zonal_annual_summary.csv`:
    - `average`: emissions ÷ generation;
    - `fossil`: the rate of the emitting fleet, a proxy for the marginal rate.
  - Factors from a solve are model output for that case, not observed data.
  - **Limitation:** the exporting zone is the adjacent zone, so flows that pass through it take its rate.
- **Not changed:**
  - `import_gens` (Mexico into p11, Canada into p1/p3) keep their own factors.
  - Exports and CA↔WA flows pay nothing.
  - `legacy` writes no import cost.
  - `study_modules/trans_hurdle_cost.py` is unchanged: every option is a different number in the same
    `trans_import_cost.csv`.

**Trade-offs:**

- **Linearity:** all three options give a fixed $/MWh per import direction and period. The objective stays linear and
  the model size doesn't change.
- **Circularity of endogenous intensities:**
  - **Average rate:** charging imports at the exporter's in-solve average rate (emissions ÷ generation, both
    variables) multiplies the flow by a ratio of variables. That is bilinear and non-convex, and outside an LP.
  - **Marginal rate:** the marginal rate is a dual of the same solve, so it can't enter its own primal.
  - **What works instead:** a lagged or iterated table. Solve, derive factors, rebuild, re-solve, and repeat until
    they settle. Each round is a full solve (80–100 GB at production size), and convergence isn't guaranteed.
    Charging the exporter's rate also lowers imports from dirty zones, which changes those zones' rates.
- **Policy fidelity:**
  - `unspecified_share` follows CARB's rule (default factor on the unspecified part, regardless of source) on
    average.
  - `source_table` models every import as specified. That lets the model pick clean exporters, which is resource
    shuffling: CARB counts it as specified only with a contract and direct delivery.
- **Margins:**
  - A flat share treats each marginal import MWh as historically mixed. In reality, extra imports beyond contracted
    volumes are unspecified unless new contracts are signed.
  - A marginal-correct form would cap the specified volume (an annual specified-MWh allowance per state at the
    specified factor, the rest at 0.428). It needs a new variable and constraint in `trans_hurdle_cost.py`, so it is
    **not implemented**.

### Average-share import charge (§91; 96b4780's default, now an option)

**`import_charge: unspecified_share`** with `specified_factor: 0`. In CA, only the unspecified share of transmission
imports pays the default factor:
- each delivered MWh pays price × 0.428 × **0.136** tCO2;
- at the 2035 central price: $67.8 × 0.428 × 0.136 = **$3.95/MWh**, against $29.02/MWh in v3.

**The CA share, 0.136: a PLACEHOLDER.**
- **Source:** CEC, 2023 Total System Electric Generation: unspecified imports 10,373 GWh of 76,400 GWh total imports.
  2023 is the latest year with both figures; 2022 was about 0.243 (20,428 of about 83,963 GWh, with total imports
  derived by subtraction), and 2024 unspecified imports were 4,051 GWh (total not found), so the share is falling.
- **Not CARB MRR:** this is CEC's power-mix category, not CARB's Mandatory Reporting count of specified versus
  unspecified MWh. The MRR data couldn't be reached from the build environment (the CARB site was unreachable).
  Replace the share with MRR's latest year (`ca_wa_carbon.unspecified_share.CA`, a number or `{year: share}`) when it
  is checked.
- **Cross-check:** CARB's 2020 inventory has specified-import emissions of about 9.8 MMTCO2e and unspecified about
  8.8 MMTCO2e.

**WA is as in v3.** The share is 1.0, so every MWh imported into WA zones still pays 0.437 tCO2; that factor is itself
unverified. No Ecology data on WA's specified versus unspecified imports was found, so WA keeps the v3 (overstated)
charge.

**Remaining bias:**
1. **Average share, not margin.** A flat share treats every extra import MWh as historically mixed. In reality,
   imports beyond contracted (specified) volumes are unspecified and pay the full 0.428 unless new contracts are
   signed. The charge on additional imports is therefore understated, and imports may be overstated relative to a
   specified-volume cap.
2. **Specified imports pay nothing.** The 0 factor ignores specified coal and gas imports, which carry their own
   factors: about 5.9 TWh coal and 8.0 TWh gas in 2022 (CEC). This understates the carbon cost of imports.
3. **WA is overstated**, as in v3 (above).
4. **The share is held flat** at 2023's value in every period, while the trend is falling; held flat, it overstates
   the charge.
5. **Imports generators:** Mexico into p11 still pays 0.428 on every MWh (`import_gens`, unspecified); Canada into
   p1/p3 pays 0.

**Back to v3:** `import_charge: all_default`.


### Two-tranche CA import charge (§92; the S0 v3.1 default, §93)

**`import_charge: two_tranche`.** The S0 v3.1 default from §93, on `tom/s0-v3.1-two-tranche` (the runner launched
from cdd0491 with the setting explicit; the default writes the same files byte for byte) and
`tom/s0-v3.1-credits`. It is not on `tom/s0-v3.1` (96b4780).

**Why it was adopted (Tom):**
- CA imports into the model in 2028 were 21 TWh under v3's charge (0.428 on every MWh) and 117 TWh under the average
  share (96b4780). CEC's 2023 figure is 76 TWh.
- The model's import basis is therefore comparable to CEC's, and v3's 21 TWh was suppressed by the charge.
- A tranche on CEC's basis is consistent with the model's flows, and the marginal import pays the full rate, as
  under CARB's rule for imports beyond contracted (specified) volumes.

**How it works:**
- In each period, CA's delivered imports over all links into CA zones (from zones outside CA and WA; annual MWh, by
  timepoint weights) are free up to a free tranche.
- Every MWh above the tranche pays price × 0.428:

  ExcessImportsCA[p] ≥ Σ_t w_t × imports_t − FreeTranche, ExcessImportsCA ≥ 0,
  cost = price × 0.428 × ExcessImportsCA

- This is linear and convex, and the marginal import above the tranche pays the full rate.
- **Files:** `study_modules.trans_hurdle_cost` (`trans_import_tranche.csv`, `trans_import_tranche_dirs.csv`); the
  cost component is `TxImportTrancheCost`.
- **Run outputs:** `trans_import_tranche_results.csv` (delivered, free, excess, rate, cost) and
  `trans_import_tranche_info.csv` (mode, tranche size, source).
- **Unchanged:** WA keeps v3's per-MWh charge, and the Mexico generator is as before.

**Free tranche: a PLACEHOLDER, 49.4 TWh/yr, held flat.**
- **Source:** CEC 2023 Total System Electric Generation, specified non-emitting imports (hydro, nuclear, wind,
  solar): 76,400 GWh total, less 23,679 thermal and unspecified, 2,569 geothermal and 753 biomass, gives 49,399 GWh
  (64.7%).
- **On CEC's basis.** The model's import basis is comparable (above). Still to do (v3.2): the MRR or CEC source, the
  treatment of geothermal (counting it raises the tranche to 52.0 TWh), and explaining the remaining zone-border
  differences.
- **Later refinement:** contract expiries (v4).

**Remaining bias (two-tranche):**
1. **The tranche is a placeholder.** It is CEC 2023's specified non-emitting imports on CEC's basis, derived by
   subtraction, not checked against CARB's MRR data; counting geothermal would add 2.6 TWh.
2. **Held flat.** Contracts expire and new ones are signed; this is a v4 refinement.
3. **All CA imports count toward the tranche,** whatever the exporting zone. A clean import from a zone with no CA
   contracts uses the free tranche as a contracted one would.
4. **Specified emitting imports** (coal and gas, about 14 TWh in 2022) are not separately charged; above the tranche
   every MWh pays the unspecified 0.428.
5. **WA keeps v3's per-MWh charge** (0.437 on every MWh, unverified factor), so WA imports stay overstated.
6. **The Mexico imports generator** (p11) pays 0.428 on every MWh, outside the tranche.

### Import charge: v3.2 items (from the S0 v3.1 run)

1. **Free tranche calibration.** The 49.4 TWh/yr tranche bound in every stage of the S0 v3.1 run. Still to settle:
   - the source: CARB MRR (specified vs unspecified MWh) versus CEC's power mix, and whether geothermal counts
     (+2.6 TWh);
   - a trajectory over time instead of flat (contract expiries and new contracts);
   - whether the tranche should scale with WECC clean supply.
2. **Unspecified imports are zero, against about 27 TWh historically.** Check the hurdle level (cost_hurdle_intra,
   about $4–5/MWh) and the charge level above the tranche (price × 0.428) against observed flows, and whether the
   binding tranche plus the full charge leave no room for the historical unspecified volume.
3. **WA emissions rise from 1.3 to 2.9 Mt by 2045.** WA keeps v3's per-MWh import charge (0.437 on every MWh,
   unverified factor), which pushes WA towards in-state gas, the same mechanism as CA before the fix. Review the WA
   charge (an Ecology data source, a two-tranche form) and check the WA results.

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
- **Chains (§57): each forced line is forced once.**
  - **Forced period:** the period whose span holds the in-service year: the first period of the whole chain at or
    after it, so a year before the first span goes to the first period. A stage forces a line only if it models that
    period:
    - mode A forces it once;
    - mode B forces it in each window holding the period, but only the window that commits the period hands the
      build on.

    SunZia is forced in the 2028 stage only and TransWest in the 2035 stage only. This applies to both
    `reeds_certain` and `named_projects`. Before the fix, each stage forced every line dated at or before it, and
    `prepare_next_stage` also carried the earlier builds forward as existing capacity, so SunZia would have been
    built 5 times and TransWest 3 times.
  - **Safeguard:** `prepare_next_stage` (S0 chains, `stage_info.csv`) keeps `trans_built_to_date.chained.<case>.csv`
    (committed new capacity by line). It writes the next stage's `trans_build_minimum` and
    `trans_path_expansion_limit` as chained files, with each minimum less what the line already has (floored at 0)
    and the cap-at-minimum row reduced by the same amount. Later stages' scenario lines alias them when the stage
    has a `trans_build_minimum.csv`.
  - **Unchanged:** a single-stage case (the regression case) and every non-S0 case, which have no `_chain_years`
    and no `stage_info.csv`.

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

3. **Stress days** (`stress_days.rule`), real days from the 7 weather years (2007-2013, 365-day years), chosen on
   weather alone (load and wind/solar profiles, not the fleet):
   - **`greedy` (S0 default since §70; `cover_tolerance` 0.06 in `s0_production.yml`):** greedy coverage of the
     summer and winter peak-load needs and a low wind/solar need (daily maximum net load with a stylised fleet), within
     6% of each worst value, the tolerance widening (by 0.02) only if more than 12 days (`max_days`) would be needed.
     At most 12 days, 288 stress timepoints. The VM's 2035 light + compact test used it (CHANGES §70). Before §66 S0
     ran it at 2% (the code default, `cover_tolerance` 0.02).
   - **`cover_plus_interconnect_wind` (S0 default in §68-§69, now an option):**
     - **Cover part:** recipe E's greedy cover rule at `cover_plus_tolerance` 0.06. A day covers a region's need if
       it is within 6% of that region's worst value, for each region's summer peak, winter peak and low wind/solar
       needs below. The tolerance widens only if more than `max_days` (12) would be needed.
     - **Interconnection part:** for each interconnection (Eastern, Western, ERCOT; `hierarchy.csv` `interconnect`),
       its lowest-wind high-load day. That is, of the top 1% of 2007-2013 days by interconnection-wide daily peak
       load, the one with the lowest daily mean onshore-wind CF across the interconnection's profiles, chosen on
       weather alone.
     - **Duplicates:** a day already in the set is not added again; `stress_coverage.csv` marks it
       `no (already in the set)`.
     - **Count:** at most 12 + 3 = 15 days per model year.
     - **Why:** the guaranteed rule gave 36 days (864 of 1,464 timepoints) and the 2035 test hit the VM's 120 GB stop.
   - **`guaranteed` (S0 default in §66, now an option):** for each reserve region, the stated rule exactly:
     - its worst summer peak-load day (Jun-Sep, highest daily peak load);
     - its worst winter peak-load day (Dec-Feb);
     - its lowest-wind day among its top-load days: of the region's top 1% of days by daily peak load
       (`top_load_share` 0.01: 26 of 2,555), the one with the lowest daily mean onshore-wind CF (the mean over the
       region's onshore-wind profiles; the national mean for a region without any, flagged `wind_basis national`).

     Every one of these days is in the set, however many that makes (16 regions × 3 needs: at most 48; a day that is
     the worst for several needs or regions counts once). There is no day cap and no tolerance. The count and the
     model size are in the build log (`Model size <year>: <n> timepoints (<sample> sample, <stress> on <n> stress
     days)`) and in `<case>/prm/<year>/stress_info.txt`.

   `<case>/prm/<year>/stress_days.csv` lists which day covers which region and need, and `stress_coverage.csv` gives
   each need's worst day (with the low-wind day's CF and the top-load-day count).
   - **Weight:** zero (capacity only).
   - **IDs:** timeseries `<year>_pN_prm`, timepoints `9<id>`, so a stress day can also be a sample day.
   - **Enforcement:** the requirement is checked in every stress hour and only there. The S0 scenario line
     excludes `planning_reserves` and `planning_reserves_extreme_days`, and the case drops the extreme-day script.
   - **Samplers:** works with both PowerGenome's k-means days and the fleet-independent selector.
   - **Formulation** (`stress_days.formulation`, CHANGES §69): `light` (S0 default since §70) or `full`; see "Light
     stress days" below. Reserve rows (`reserve_rows`): `compact` (S0 default since §70) or `hourly`.
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
     In a chain, lines built by earlier stages arrive as existing capacity; `trans_built_to_date.csv` (written by
     `prepare_next_stage`, §57) keeps them at 0.85 too.
   - **New lines across a region boundary (§87, every case):** the flow splits in two.
     - **Existing-capacity part:** per direction, under the import cap.
     - **New-capacity part:** new(a→b) + new(b→a) ≤ (1 − `new_tx_derate`) × new capacity in each stress hour,
       exempt from the cap.
     - **So:** new capacity serves one end at a time, and both ends in turn when their stresses don't coincide.
     - **Replaces:** the bill cases' import allowance, retired with its setting (`transmission_bill_scenarios.md`
       section 4).
     - **Outputs:** `prm_region_hours.csv` reports `new_tx_net_import_mw`, and the margin achieved includes it.
       `prm_summary.csv` reports `max_new_tx_net_import_mw`.
   - **Zone check:** each zone's requirement is met by local credit plus net inflows over its lines, and the margin
     applies to each zone's load.
   - **Import cap:** net imports from other regions over existing capacity ≤ share × the region's peak load in the
     period, in **every** stress hour. Shares are ReEDS's 99.9th percentile from the config; WECC_NW's 0.140 is used for all four of its
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
     reserve price (§64).
     - **Zone price:** each zone's price is the sum over the period's stress hours of its requirement's dual, in
       $/kW-yr (`prm_zone_prices.csv`). The zone's shortfall slack is one MW figure per period. It appears in every
       stress-hour constraint and costs the penalty once a year, so the price is at most the penalty, and equals it
       when the zone is short.
     - **Region price:** `reserve_price_usd_per_kw_yr` is the zones' prices weighted by their peak load. The highest
       zone price and that zone are given too.
     - **Before §64** the region figure summed the hourly duals over all its zones. That is not a price: PJM showed
       $2,555/kW-yr against a $271.8 penalty.
   - **Demand response:** `demand_response_investment` counts at its dispatch in the stress hours, through served
     load, and appears as class `dr` in `prm_capacity_credit.csv`. S0 cases include it only with
     `s0_production.demand_response.enabled` (off). PowerGenome's `load_growth` / `us_exports` virtual generators
     (energy source `demand_response`) are load curtailment switched off in S0, so they get no credit (class
     `flex_load`).
   - `prm_capacity_credit.csv`: implied capacity credit by class and region, Σ_t |dual_t| × credited MW_t /
     (Σ_t |dual_t| × capacity). It is blank when no stress hour has a price. Next to it are PJM 2029/30, NYISO
     2026/27 (ROS and NYC), ISO-NE preliminary (summer and winter) and SPP 2024 (summer and winter) values from the
     config.
     - **What it is:** a marginal model credit. It weights each class's credited MW by the reserve duals of the
       stress hours, so it measures how much a MW of the class counts in the hours that bind in this solution. It
       differs from ISO accreditation (ELCC or class UCAP ratings over many simulated years and outage draws) by
       design. The comparison columns are context, not targets: the stress days are not tuned toward ISO values
       (§66).
     - **Gas is credited above PJM's class values.** Thermal capacity counts at 1 − FOR(T) on the stress day's
       temperature, but correlated winter fuel-supply failures (gas curtailment, frozen equipment beyond the
       temperature curves) are not modelled. PJM's ELCC class ratings for gas include them, so model credits for gas
       CC / CT, especially in winter stress hours, come out higher.
   - **Duals:** they come from `study_modules.write_dual_costs` (in `modules.txt`, so every run gets the `dual`
     suffix). With S0's Gurobi barrier (`method=2 crossover=0`), LP duals are returned from the interior solution.
     The S0 cases are LPs (no unit sizes or minimum builds); a MIP would give none, and `prm_summary.csv` reports
     `duals_available`.
   - **Sign:** solvers differ in the sign they report for ≥ constraints, so prices and weights use the magnitude.

### Light stress days (§69)

The stress days have zero weight and exist only for the reserve test. Under `full`, they carry the sample days' whole
operating model. The VM's 2035 stage (24 sample days + 15 stress days, 960 timepoints) took 4 h 29 min and peaked at
114 GB of 128 GB; the stress days are 360 of the 960 timepoints.

`light` (`prm.stress_days.formulation: light`; the case build writes `stress_light_timeseries.csv` with the stress
timeseries) keeps, on the stress days:
- dispatch with hourly wind, solar and hydro availability;
- storage with its state of charge through each stress day (charging counted);
- transmission flows with limits and losses;
- the energy balance with every withdrawal (load, local T&D, storage charging);
- the regional requirement, the import cap in every stress hour and the shortfall slack.

It removes, on those timepoints only:
- **Unit commitment:** no `CommitGen`, start-up or shut-down variables, minimum load, or minimum up and down time.
  Dispatch is limited to available capacity (`GenCapacityInTP` × `gen_availability`, × the capacity factor for
  variable units). Thermal units are further limited to nameplate × `prm_avail_frac`, the seasonal 1 − FOR the
  reserve credit already uses (`prm_regional.Prm_Light_Thermal_Limit`).
- **Ramp limits.**
- **Spinning and operating reserves:** balancing-area timepoints are the operating ones.
- **Scheduled-outage variables.**
- **Fuel use and emissions:** `GenFuelUseRate`, the heat-rate constraints, `DispatchEmissions`.
- **Per-timepoint policy variables and constraints:** bundled-REC trade, the in-zone generation shares.
- **Per-timepoint cost terms:** variable O&M, fuel, start-up, hurdle, import carbon cost, tax credits. These are 0 there;
  zero weight already removed them from the objective.

**Mechanism:** one set, `LIGHT_TPS` (from `stress_light_timeseries.csv`; each listed timeseries must have zero
weight), is defined in `study_modules.generators_core_dispatch`. The modules that index over every timepoint use
`OP_TIMEPOINTS` / `GEN_TPS_OP` instead. `switch/modules.txt` now loads repo copies of Switch's `commit.operate`,
`operating_reserves.areas` and `spinning_reserves` that do this. Without the file, every case builds the same model as
before: the toy test compares variable and constraint counts by component and the objective against the core
modules. See `SHARED_CHANGES.md` #81-#88.

**Every module that sums over a period's timepoints** must skip `LIGHT_TPS` where it uses commitment, fuel or reserve
components. The first VM build failed in `gen_annual_availability_limits` (`CommitUpperLimit[g, t]` on a stress
timepoint); it now sums over operating timepoints only (zero weight added nothing there). `test_light_stress.py`
builds and solves a toy with light and every module in `switch/modules.txt`, plus S0's scenario-line modules, to catch
the next one.

**What changes in the answer:**
- Light drops operating limits that only matter on zero-weight days. Commitment, minimum loads and ramping on a stress
  day can no longer move the hour the reserve binds, and thermal dispatch there is bounded by the derated capacity
  instead.
- The toy gives the same builds, shortfall and cost when commitment doesn't bind.
- **S0 default since §70**, after the VM test below. `prm_design = regional_full` (row `s4x1_S0prod_2035_new_full`)
  gives the earlier full stress days with hourly reserve rows.

**VM test (2035, greedy stress days, at 0a29884; CHANGES §70):**

| | full + hourly | light + compact |
|---|---|---|
| wall time | 2 h 50 min | 1 h 43 min (−39%) |
| peak memory | 102.7 GB | 79.7 GB (−22%) |
| results | | within about 1%: CO2 +10 Mt, new solar −8 GW, new CT −1.7 GW, reserve prices 0-23% lower |

**Why the results move:** light takes spinning reserves, minimum load and unit commitment off the stress days. The
NERC reference margins the regional requirement uses already include operating reserves, so holding spinning reserve
and committed minimum load in a stress hour as well counted them twice. Without them the stress hours are easier to
meet: reserve prices are 0-23% lower and slightly less new capacity is built (solar −8 GW, CT −1.7 GW). The VM
reported CO2 +10 Mt alongside; the run-level breakdown of that is not in this guide.

Compact reserve rows alone gave the same optimum as hourly, with 50 more barrier iterations. Turning ramp limits off
gave no gain.

**Size (2035 stage of `s4x1_S0prod_2035_new`: 600 sample + 360 stress timepoints):** 

| | full | light |
|---|---|---|
| variables (stage) | 22,576,685 | 15,801,380 |
| constraints (stage) | 28,477,267 | 20,386,267 |
| variables on stress days | 8,575,560 | 1,806,840 |
| constraints on stress days | 10,960,920 | 2,869,920 |
| per stress timepoint (variables / constraints) | 23,821 / 30,447 | 5,019 / 7,972 |
| memory (scaled from the VM's 114 GB) | 114 GB | about 81 GB |

Sample timepoints are the same in both (23,195 variables and 29,067 constraints each). Counts come from building
`s4x1_fedpol_current` with synthetic reserve inputs and scaling its per-timepoint rates
(`s0_workflow/scripts/estimate_stress_model_size.py`; `s0_workflow/data/stress_model_size.csv`).

### Compact reserve rows (§69)

The VM traced the 4 h 29 min to factorisation fill-in (barrier factor ops 1.26e13 against 5.75e11 in v2). The cause
is the regional reserve's structure. Every capacity-credit unit's new-build column and each zone's shortfall column
appear in every stress-hour row of the zone (360 rows), and the region rows link up to 689 generators per hour. Each
such column joins all its rows into one dense block of the normal matrix.

`prm.reserve_rows: compact` (`prm_params.csv` `prm_compact_capacity = 1`; default `hourly`) reformulates:
- **Accredited capacity:** stress hours of a zone in a period are grouped by their derate vector (with the seasonal
  thermal derate: summer, winter and shoulder, at most three groups). One variable `PrmAccCap[z, p, k]` per group is
  defined once, `Prm_Acc_Cap_Def`: Σ capacity × `prm_avail_frac` over the zone's thermal (capacity-credit) units. The
  group's hourly rows use it in place of the units' capacity columns.
- **Kept hourly:** wind, solar and hydro (hourly profiles), storage (its dispatch, so the state-of-charge check stays;
  crediting storage at its power capacity would change the answer), demand response, transmission inflows and
  imports.
- **Shortfall:** a shortfall must relax every hourly row, so it can't be linked into one row only. Each group gets
  `PrmGroupShortfall[z, p, k]` in its hourly rows, linked once to the zone's shortfall
  (`PrmZoneShortfall ≥ PrmGroupShortfall`, `Prm_Group_Shortfall_Link`). The zone's shortfall, its cost and the
  reserve prices are as before.

It is the same LP (the toy test gives the same builds, shortfall, prices and cost). The nonzeros move from the hourly
rows into a few definition rows. A unit's capacity column now appears in one row per group, not in every stress hour.

Measured on the same build (reserve rows = `Prm_*` constraints; 2035 stage, 360 stress hours; compact with three
derate groups per zone, 402 groups):

| reserve rows | hourly | compact | change |
|---|---|---|---|
| nonzeros per stress hour | 23,023 | 12,169 | −47% |
| nonzeros, stage (hourly rows + once-per-group rows) | 8,288,280 | 4,380,840 + 32,318 = 4,413,158 | −47% |
| most nonzeros in one hourly row | 503 | 206 | −59% |
| columns in every stress-hour reserve row of a period | 10,140 | 3,231 | −68% |

Columns still in every stress-hour row under compact are, by construction: the accredited-capacity and group
shortfall columns, the wind, solar and hydro new-build columns (their credit follows the hourly profile), and new
transmission capacity in the flow-limit rows. Variables and constraints barely change (+804 each in the built case;
memory to build is the same). The gain is in the solver's factorisation, which only the VM can measure (recipe I:
Gurobi's factor nonzeros and factor ops).

The hourly form stays available (`reserve_rows: hourly`). **VM result:** compact rows gave only about 2% fewer factor
ops and the same optimum (alone, 50 more barrier iterations; turning ramp limits off gave nothing), so the reserve rows
are not the main source of the fill-in; light stress days are the lever that matters. Compact is the S0 default since
§70 together with light, the pair the VM tested.

**Not added: a contiguous window of each stress day** (e.g. 12 hours around the peak). It would cut stress timepoints
by half again, but:
- the k-means sample path builds one 24-hour timeseries per slot, so mixed-length stress timeseries need the
  block-sampling timepoint code in both samplers;
- storage is cyclic within each timeseries, so a window would let storage start the peak hours full only if it
  recharges within the window. That is an assumption about the hours outside the window that the full day avoids.

If wanted, it is a follow-up with that storage assumption documented.

### Related: the in-state generation rule

`study_modules.gen_zone_ratio` has the same laundering issue: it counts battery discharge as in-state generation
even when the battery charged from imports. A fix was proposed in `docs/plans/gen_zone_ratio_import_cap.md` (P-11,
on the VM checkout) and not implemented. S0 doesn't use `gen_zone_ratio`, so it is left as is.

## Final configuration (§66)

Settings in `pg/settings/s0_production.yml`; each is off or legacy in the regression case (`on_pgdays` pins).

### Lifetime backstop (`lifetime_backstop`)

- **Rule:** unit by unit, on PowerGenome's unit table before clustering (`coal_fleet.unit_hooks` →
  `apply_lifetime`). An existing coal-group unit retires at the earlier of its planned date and its EIA operating
  year + 65; an existing gas CC, CT, steam or engine unit at operating year + 55. The operating year is the unit's
  EIA operating date, not pg_to_switch's `build_year`, which encodes a planned retirement as year − 500 (e.g. 1530).
  The technology-wide `retirement_ages` hook is not used: it would break that encoding.
- **Before 2030:** under `retirements_pre2030 block_all`, a unit due by 2030 is held through the 2030 stage and
  encoded 2031, the same as the pushed planned dates. Otherwise a unit already past its lifetime retires at the
  first stage (`floor_year` 2026).
- **Not touched:** hold projects (their order dates), units without an operating year, and non-coal/gas units.
  Converted coal-to-gas units count as gas steam. The coal spec's caps and checks are computed on the fleet before
  the backstop, so the zonal CF caps and the spec tables are unchanged.
- **Encoding:** a retirement year Y runs in stage p if Y ≥ p (Switch `--retire early`), as for planned dates. A
  unit due in 2035 therefore runs in the 2035 stage and is gone from 2040.
- **Report:** `lifetime_retirements.csv` (units moved earlier) and `lifetime_retirements_by_stage.csv` (coal and gas
  GW out of service by lifetime per stage), plus a log line.
- **Sensitivities:** column `retirement_sens`: `life_coal60`, `life_coal70`, `lifetime_off`.
- **Coal, on the committed model basis (block_all; GW out of service because of the lifetime, of the spec's model
  coal):**

  | Coal lifetime | 2028 | 2030 | 2035 | 2040 | 2045 |
  |---|---|---|---|---|---|
  | 60 | 0 | 0 | 37.06 | 61.92 | 91.96 |
  | 65 (S0) | 0 | 0 | 12.78 | 37.06 | 61.92 |
  | 70 | 0 | 0 | 3.67 | 12.78 | 37.06 |

  Model coal in service with 65 years: 163.59 / 163.59 / 112.70 / 86.32 / 61.46 GW (before the backstop: 163.59 /
  163.59 / 125.48 / 123.38 / 123.38). Gas comes from PowerGenome's unit table at the build.

### Retirement friction (`retirement_friction`)

- **Rule (ReEDS-style):** retiring an existing coal or gas unit economically (SuspendGen) from 2030 avoids only half
  of its fixed O&M. The other half stays in the objective (`RetirementFrictionCost`, `study_modules.retirement_rules`,
  input `retirement_friction.csv`). A unit therefore retires only if it recovers less than 50% of its fixed costs.
  Fixed O&M includes PowerGenome's age-based capital additions for existing units; no separate capex is charged on
  existing capacity. With `existing_fixed_om: by_period`, the per-period value counts.
- **Settings:** `fraction` 0.5 (S0) or 0 (off; `retirement_sens friction_off`), `from_period` 2030, energy sources
  coal and naturalgas.
- **Interactions:**
  - the pre-2030 block (`retirement_rule`) forbids economic retirement before 2030, so there is nothing to charge then;
  - lifetime and planned retirements are not economic (build-year encoding), so they pay no friction and don't count;
  - in a mode-A chain a stage that retires a unit pays the friction in its own period; later stages no longer carry
    the capacity. In a mode-B window the friction persists in both periods.
- **Output:** `retirement_rules_check.csv` adds `friction_fraction` and `friction_cost_per_yr` (only when the file is
  there).
- **Toy:** at a fixed O&M of 400 k$/MW-yr, coal retires in 2030 without friction and stays with friction 0.5.

### Existing units' fixed O&M by period (`existing_fixed_om`)

- **Within a stage**, pg_to_switch already charged each period its own fixed O&M: `gen_fixed_om` holds the mean over
  the stage's model years and `gen_om_by_period.csv` the deviations.
- **Across stages** the next stage kept the earlier stage's `gen_fixed_om` for carried capacity
  (`prepare_next_stage`). So in a mode-A chain an existing unit's fixed O&M stayed at the 2028 value, and in mode B
  at the first window's mean.
- **`by_period` (S0):** the case writes `existing_fom_by_period.csv` (existing-only generators), and
  `prepare_next_stage` gives those generators the next stage's own value. A cluster that PowerGenome no longer counts
  in the next model year keeps the earlier value. `mean` is the legacy behaviour.

### Virginia in RGGI (`rggi.virginia`)

- **Membership:** from 2028, Virginia's zones (p99, p100, p118, p124; `hierarchy.csv` st VA) join ETS 1. They bring
  Virginia's budget on top of the RGGI10 3PR cap; RGGI confirms Virginia's budget and CCR are additional to the
  published RGGI10 volumes.
- **Budget:** `s0_workflow/specs/rggi/va_budget.csv` (year, short tons), converted to metric tonnes (× 0.907185) and
  split equally over the four zones. The rows take ETS 1's floor and cost (the 3PR Model Rule floor; hard cap).
  `carbon_policies.csv` gets the budget too.
- **CCR:** each ETS 1 tier grows by 10% of Virginia's budget, as in 2026. The trigger prices are the Model Rule's.
- **Initial budget:** 22.96M short tons (2026) × the RGGI10 3PR cap path relative to 2026, held after 2037. Replace
  it with DEQ's numbers when Revision D26 is adopted (`s0_workflow/specs/rggi/README.md`).
- **Checks:** the build stops if a Virginia zone is already in ETS 1 (`rggi_va_fraction`, RGGI10+VA), so Virginia is
  never counted twice. Report: `rggi_va_budget.csv`.

| Model year | VA budget (short t) | VA (metric t) | RGGI10 3PR cap (metric t) | Combined (metric t) | CCR per tier (metric t) |
|---|---|---|---|---|---|
| 2027 (reference) | 20,413,052 | | | | |
| 2028 | 17,861,420 | 16,203,612 | 55,411,816 | 71,615,428 | 12,276,481 |
| 2030 | 12,758,157 | 11,574,009 | 39,579,868 | 51,153,877 | 11,813,521 |
| 2035 | 4,035,939 | 3,661,343 | 12,520,768 | 16,182,111 | 11,022,254 |
| 2040, 2045 | 2,640,384 | 2,395,317 | 8,191,313 | 10,586,630 | 10,895,652 |

(The RGGI10 column is `rggicon_3pr.csv`; the case's ETS 1 total comes from the policy file.)

For reference, DEQ's reported proposed 2027 figure is 20,408,889 short tons (unverified; the file is 0.02% above). The
current regulation has 22.12M (2027) to 19.60M (2030); the file's 2030 value, 12.76M, follows the much steeper 3PR
path.

### Imports generators inside CA / WA zones (`ca_wa_carbon.import_gens`)

- **Mexico into p11 (California):** an unspecified import. Its output pays price × 0.428 tCO2/MWh per MWh, as
  `gen_variable_om_by_period` (central: $20.80 in 2028, $22.86 in 2030, $29.02 in 2035, $36.81 in 2040, $46.74 in
  2045).
- **Canada into p1 and p3 (Washington):** treated as specified low-emission hydro, largely BC Hydro (Powerex, an
  asset-controlling supplier), at **0 tCO2/MWh, an assumption**. CARB's ACS factor table could not be reached to cite
  a primary source. Powerex reports 0.0233 t/MWh for data year 2019, about $1.2/MWh at the 2030 price; it is a
  candidate value once confirmed against CARB's table (set `import_gens: {p1: <factor>, p3: <factor>}`).
- **Scope:** imports generators are `gen_tech` containing "imports". Imports over lines still pay the §65 import
  cost; this covers the generators that sit inside the zones.

## Fuel prices (§67)

`s0_production.fuel_prices` (`s0_workflow/fuel_prices.py`; axis and column `fuel_prices`). It covers natural gas and
coal delivered to the electric power sector, in 2024 $/MMBtu. It replaces the flat historical price (`hist5_high_gas`
for the S0 rows: the 2020-24 SEDS state average, gas +15%).

| Mode | 2026-2027 | 2028-2034 | 2035 on |
|---|---|---|---|
| `steo_aeo` (S0 default) | STEO | linear from STEO 2027 to AEO 2035 | AEO2026 reference |
| `steo_aeo_low_supply` | STEO | same glide | AEO2026 Low Oil and Gas Supply |
| `steo_aeo_high_supply` | STEO | same glide | AEO2026 High Oil and Gas Supply |
| `hist5` | the case's `fuel_price_forecast` as it is (the regression case) | | |

**Sources** (pinned in `s0_workflow/data/fuel/`, with editions, release dates, URLs and file hashes in `SOURCES.yml`;
refresh with `python s0_workflow/scripts/fetch_fuel_prices_eia.py`, verify with `--check`):
- **STEO, September 2026 edition** (released 9 Sep 2026; modelling completed 3 Sep 2026). From the monthly
  workbook: the cost of natural gas (NGEUDUS) and coal (CLEUDUS) to the electric power sector, in nominal $/MMBtu.
  - **Annual value:** the month's price weighted by the month's power-sector burn (NGEPCON × days; CLEPCON_TON), as
    EIA computes annual averages.
  - **Dollars:** to 2024 $ with STEO's CPI-U (CICPIUS, annual mean). BLS and FRED are not reachable from the build
    environment; STEO carries the same index.
- **AEO2026** (released 8 Apr 2026):
  - **National, Table 3:** the Electric Power rows (natural gas, steam coal), 2025 $ → 2024 $ with STEO's CPI-U.
  - **Cases:** EIA renamed the reference case the **"Counterfactual Baseline"** (`cb2026`; "formerly known as the
    Reference case"), and that is the case used. The side cases are `lowogs` and `highogs`.
  - **Regional, supplemental Tables 54.1-54.25:** fuel prices to the electric power sector and its fuel consumption,
    by EMM region (reference case only: the side cases publish national tables).
- `api.eia.gov` is blocked from the build environment, so the script reads EIA's published xlsx files, which hold the
  same numbers.

**Regional prices:**
- **Formula:** zone price(y) = path(y) × the zone's EMM region price(y) / the consumption-weighted average of the 25
  regions' prices(y). The consumption-weighted national average of the regional prices therefore equals the path
  every year.
- **Side cases:** they use the reference case's regional pattern.
- **Missing prices:** a region with no AEO price in a year (no consumption, e.g. coal after its plants retire) takes
  the nearest year's ratio. A region AEO never prices for a fuel (coal in ISO-NE, NY, California) gets 2 × the
  national value, the hist5 rule for states without prices.
- **Zones → EMM regions:** `s0_workflow/specs/fuel/zone_emm.csv` (zone, emm, abbr, share), all 134 US zones. It is
  the existing ReEDS-zone → EMM crosswalk `growth_rates/crosswalk_v7.csv` (on `ollie/edf-baseline`, commit 43fb0ec,
  2026-07-13; the crosswalk behind the EPRI/AEO load-growth rates). That file is a county overlay with each zone's
  load share in each region.
  - **Split zones:** five zones straddle two regions (p7, p8, p21, p24, p89). They take the load-share-weighted average
    of the regions' factors.
  - **Earlier draft:** the first §67 commit used a hand-built crosswalk. Its main region differed in 10 zones (p7, p8, p13,
    p19, p22, p89, p118, p119, p121, p124), and p21 and p24 gained a second region's share. Points to confirm in crosswalk_v7:
    - p118 → PJM West and p124 → PJM East, not Dominion;
    - p13 (Las Vegas) → Basin, not Southwest.
- **Not regionalised:** non-US zones keep the case's prices, and so do distillate and uranium.

**Stage value:** the mean over the period's years (2028 = 2026-28, 2030 = 2029-30, 2035 = 2031-35, ...). The case
build replaces the gas and coal rows of `fuel_cost.csv` and writes `fuel_prices_by_stage.csv` (zone, fuel, period,
price, national).

**National price by model year (2024 $/MMBtu; stage means):**

| Natural gas | 2028 | 2030 | 2035 | 2040 | 2045 |
|---|---|---|---|---|---|
| current setting `hist5_high_gas` (flat) | 5.44 | 5.44 | 5.44 | 5.44 | 5.44 |
| `hist5` (flat) | 4.73 | 4.73 | 4.73 | 4.73 | 4.73 |
| `steo_aeo` | 3.46 | 3.73 | 4.44 | 5.06 | 5.01 |
| `steo_aeo_low_supply` | 3.60 | 4.84 | 7.12 | 9.57 | 11.48 |
| `steo_aeo_high_supply` | 3.41 | 3.40 | 3.65 | 3.76 | 3.34 |

| Coal | 2028 | 2030 | 2035 | 2040 | 2045 |
|---|---|---|---|---|---|
| `hist5` / `hist5_high_gas` (flat) | 2.68 | 2.68 | 2.68 | 2.68 | 2.68 |
| `steo_aeo` | 2.32 | 2.34 | 2.39 | 2.46 | 2.43 |
| `steo_aeo_low_supply` | 2.33 | 2.40 | 2.53 | 2.64 | 2.63 |
| `steo_aeo_high_supply` | 2.32 | 2.36 | 2.43 | 2.50 | 2.47 |

- **How the rows are computed:** the steo_aeo rows are the national path. The hist5 rows weight the zone prices by
  AEO2026's 2026 power-sector burn per EMM region, split over its zones by their load shares.
- **Same weights for both:** with those weights, steo_aeo's zone prices give gas 3.45 / 3.71 / 4.46 / 5.12 / 5.06
  and coal 2.32 / 2.32 / 2.30 / 2.33 / 2.27 (`s0_workflow/data/fuel/fuel_price_options_by_stage.csv`;
  `python s0_workflow/scripts/compare_fuel_prices.py`).
- **Annual path, steo_aeo** (2024 $):
  - gas 3.73 (2026), 3.22 (2027), 3.42 (2028), 3.83 (2030), 4.85 (2035), 5.08 (2040), 4.79 (2045);
  - coal 2.33, 2.30, 2.32, 2.35, 2.42, 2.50, 2.27.

**Sensitivity rows:** `s4x1_S0_tx_2035_fuel_low`, `_fuel_high` and `_fuel_hist5` (the hist5 row keeps the
`hist5_high_gas` column value, as every S0 row).

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
