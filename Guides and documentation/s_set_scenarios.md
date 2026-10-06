# S-set scenarios (S1–S5, L, P) on the S0 v3 defaults

CHANGES §75. Rows in `pg/extra_inputs/scenario_inputs.csv`, settings in `pg/settings/scenario_management.yml` (axis
`s_set`, column `s_set`).

## Cases

Each case is the S0 v3 defaults plus only the settings below:
- **Mode-A chains 2028–2045:** case ids `S1`, `S2`, `S4`, `L`, `P`, `S3`, `S5`. Each is S0prod_A's rows with only
  `case_id`, `s_set` and `tax_credits` changed.
- **Single-year 2035 s4x1 versions:** `s4x1_<case>_2035`. Each is `s4x1_S0prod_2035_new`'s row with the same three
  columns changed.

| Case | Build rate | Headroom | Gas-turbine supply | Transmission | Credits |
|---|---|---|---|---|---|
| S1 | central | atts_s0 | central | s0_tx | reinstated |
| S2 | high | atts_planned | central | s0_tx | reinstated |
| S4 | high | atts_planned | central | s0_tx | current |
| L | high_reform (all periods) | atts_reform_techmax | high | s0_tx | reinstated |
| P | high (2028–30) → high_reform (2035) → off (2040+) | atts_planned (2028–30) → atts_reform (2035) → atts_reform_techmax (2040+) | central (2028–30) → high (2035) → off (2040+) | s0_tx | reinstated |
| S3 | off | atts_reform_techmax | off | unconstrained | reinstated |
| S5 | off | atts_reform_techmax | off | unconstrained | current |

## How the settings are set

`s0_production.settings` fixes S0's build-rate level (central) and headroom scenario (atts_s0) over the case's
`build_rate` and `interconnection_headroom` columns. So each S-set value sets them in its own `s0_production` block:
- **`level_overrides`:** one value in every period. Used by S2, S4, L, S3 and S5.
- **`levels_by_period`:** each key holds until the next. Used by P.
- **Keys:**
  - `build_rate`: a level name, or `"off"`;
  - `interconnection_headroom`: a scenario name, or `"off"`;
  - `gas_turbine_cap`: an allowance path name (`low`, `central`, `high`), or `"off"`.
- **`"off"`:** no limit of that kind in the period.
  - build rate: no `build_rate_*.csv`, and the chained history is not aliased;
  - headroom: no `ic_*.csv`;
  - gas-turbine cap: neither the cap files nor PowerGenome's `MaxCapTag_GasTurbineSupply` rows.
- **`high_reform`** is a build-rate level (`build_rate/config.yaml`; §74: per region and year the larger of `high`
  and `reform_bp`). L and P follow its definition with no edits to the rows.

**Transmission `unconstrained`** (S3, S5; `s0_production.tx_policy.mode: unconstrained`):
- the same line handling as the S0_tx national cap: interregional lines may be built, and the case's per-line limits
  are replaced;
- the forced `reeds_certain_plus_A` lines keep their minimum and forced-period cap;
- no interregional moratorium and no national cap (no `tx_build_cap` module).

**Credits** (`tax_credits` column):
- **Reinstated:** `no_wind_solar` in the 2028 stage, `full_ira` from 2030 (chains), `full_ira` in the 2035 versions.
  IRA-style wind and solar credits are restored from 2028. Build decisions respond from 2030: the 2028 stage's builds
  are optimised without them, but paid them.
- **Current:** S0's `no_wind_solar` throughout (no credit for new wind and solar).
- Storage (ITC as a capex modifier) and new-nuclear ($15/MWh) credits are in both.

## Credit spend (run on the VM after each solve)

```bash
python s0_workflow/scripts/credit_spend.py switch/in/<root>/scenarios_<case>.txt [...] --out credit_spend.csv
```

Per case and model period ($/yr, typical year; long table by technology and vintage, plus a summary in $M/yr):

| Category | What |
|---|---|
| existing_pipeline | wind and solar already built or in the EIA-860M pipeline (the stage's base predetermined builds, safe-harbour projects included); not in the model, so computed: energy × $27.5/MWh while within 10 years of entering service, for eligible in-service years |
| paid_not_optimised | in reinstated cases, the 2028 builds: paid from 2028 though optimised without the credit (computed in 2028; the model's credit share in later stages) |
| optimised_on | the model's own credits (`tax_credit_value.csv`) on vintages whose build stage had them |

- **How the split is made:** a generator's energy is split over its vintages by capacity (`BuildGen.csv`).
- **Values** (`s0_workflow/specs/credits/credit_spend.yaml`, sourced §76):
  - **PTC value:** $30/MWh in 2024$ (3.0 ¢/kWh with prevailing wage and apprenticeship, 2024 and 2025 IRS amounts;
    inflation-adjusted yearly, so about constant in real terms); $29/MWh for wind placed in service before 2022.
  - **Solar share electing the PTC:** 0.40. Norton Rose Fulbright: 2023 solar financings split about 60/40
    ITC/PTC. Berkeley Lab finds the PTC more valuable for 72% of projects (economics, not elections). FOR REVIEW.
  - **Current-law eligibility:** in service by end-2027, or construction begun by 4 July 2026 and in service within
    the four-year continuity safe harbour (by end-2030) (P.L. 119-21; IRS Notice 2025-42). 860M pipeline units dated
    2028–30 are taken as safe-harboured (FOR REVIEW).
  - **Wind phase-down:** the 2017–21 construction-start phase-down is not mapped (share 1.0; PLACEHOLDER).
  - **Value mismatch:** the model's own `full_ira` value is $27.5/MWh, below the sourced $30. Aligning it is flagged
    as a separate decision.
- **Credit term:** the model's own credits follow `gen_tax_credits.csv`, which pays a project's whole dispatch in a
  period, whatever the vintage's age.

## Reuse from S0prod_A (`reuse_chain_stages.py expected`)

| Case | Reusable stages |
|---|---|
| S1, BILL_central_S1 | 2028 (credits differ from 2030) |
| S2, S4, L, P, S3, S5 | none (the build rate differs from 2028) |
