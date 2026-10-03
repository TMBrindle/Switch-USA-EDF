# S0 production case build

The S0 production case is built by `pg_to_switch.py` from tracked settings, with no hand steps. One
settings file, `pg/settings/s0_production.yml`, holds everything; the `s0_production` column of
`pg/extra_inputs/scenario_inputs.csv` turns it on per case. Code: `s0_workflow/production.py`,
`s0_workflow/day_selection.py`, `build_rate/brc/turbine_cap.py`, `switch/study_modules/retirement_rules.py`.
Changes to shared code are listed in `SHARED_CHANGES.md`; history in CHANGES §44.

| `s0_production` | What it builds |
|---|---|
| `off` | the case as before (every row that predates this) |
| `on` | all S0 production settings; fleet-independent days; mode A (myopic chain of single years) |
| `on_windows` | as `on`, mode B (rolling two-period windows) |
| `on_pgdays` | as `on` with PowerGenome's k-means days and one case for all its years (the s4x1 2035 regression case) |

Cases: `S0prod_A` (on), `S0prod_B` (on_windows), each with rows for 2028, 2030, 2035, 2040 and 2045;
`s4x1_S0prod_2035` (on_pgdays): the s4x1 2035 new-stack base for the regression against the ic_v4
B6+B8 + NY buyout run.

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
| `make_partb.py --only B6` (gas capex alias) | `gas_capex.mode: atb_moderate` (GridLab override neutralised); `gridlab_fade` keeps a premium fading to 0 by `zero_by` (2033) | settings before PowerGenome; premium on `gen_build_costs.csv` |
| `b8_coalcf.py` (PUDL) | `coal_cf_caps` from `s0_workflow/data/coal_cf_caps_eia923_2021_2024.csv` (public EIA-923/860, `scripts/fetch_coal_cf_eia923.py`) | `gen_info.csv` `gen_max_annual_availability` |
| `make_windloss_case.py` + `make_windloss2_case.py` | `wind_loss` (onshore and offshore x 0.88098) | `variable_capacity_factors.csv` |
| `make_rps_acp_alias.py` | `rps_acp.programs: {ESR_NY_rps: 45.39}` | `rps_requirements.csv` |
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

The defaults reproduce the old MaxCapTag cap exactly. A research task will supply the new trajectory.
Output: `gas_turbine_cap_results.csv` (covered MW, cap, dual by period).

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

## Tests

```bash
SWITCH_SRC=<switch checkout> pytest -q s0_workflow/tests
```
Covers:
- settings, axis and rows;
- the case steps;
- the day selector's targets, tails and fleet independence;
- the scenario lines for modes A and B;
- the gas-turbine cap (defaults = the old values; toy solve identical to MaxCapTag);
- the retirement rule on the toy;
- toy chains for modes A and B;
- committed handoff of headroom and build-rate state;
- the regression comparison script.
