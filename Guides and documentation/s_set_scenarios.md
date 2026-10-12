# S-set scenarios (S1–S5, L, P) on the S0 v3 defaults

CHANGES §75. Rows in `pg/extra_inputs/scenario_inputs.csv`, settings in `pg/settings/scenario_management.yml` (axis
`s_set`, column `s_set`).

## Cases

Each case is the S0 v3 defaults plus only the settings below:
- **Mode-A chains 2028–2045:** case ids `S1`, `S2`, `S4`, `L`, `P`, `S3`, `S5`. Each is S0prod_A's rows with only
  `case_id`, `s_set` and `tax_credits` changed (S3 and S5 also `tx_bill = unconstrained`, §81).
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

**Transmission `unconstrained`** (S3, S5; `tx_bill = unconstrained`, which sets `s0_production.tx_policy.mode:
unconstrained`; §81: before, the `s_set` value set the mode on top of `tx_bill = s0_tx`):
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
- **Values FOR REVIEW** (`s0_workflow/specs/credits/credit_spend.yaml`):
  - eligible in-service years: wind from 2016, solar from 2022; under current law up to 2030 (placed in service by
    end-2027, or safe-harboured);
  - the share electing a production credit (1.0);
  - the $/MWh value.
- **Credit term:** the model's own credits follow `gen_tax_credits.csv`, which pays a project's whole dispatch in a
  period, whatever the vintage's age. From §90 that rate is the levelised value (below), so the model's total over a
  plant's life has the statutory present value.

## Levelised credits (§90)

From §90, `full_ira` carries statutory terms (`tax_credit_terms`, `s0_workflow/tax_credits.py`):
- **45Y:** $27.5/MWh (2024$) for 10 years, on new wind and solar placed in service from 2025.
- **Levelisation:** the case build turns each term into an in-model $/MWh on every MWh of the plant's in-model life:

  value × AF(r, duration) / AF(r, life), where AF(r, n) = (1 − (1 + r)^−n) / r

  This has the same present value at r as the statutory profile.
- **r and life** come from the fields Switch annualises that generator's capital with:
  - **r:** `interest_rate` (`financials.csv`; 0.05 from `switch.yml`). It is not a PowerGenome per-technology WACC:
    Switch annualises overnight capex with `crf(interest_rate, n)`.
  - **life:** `gen_amortization_period` when the case loads `study_modules.gen_amortization_period` (S0 and the
    S-set: `s0_production.extra_modules`), else `gen_max_age`.
  - **Wind and solar here:** life 30 (PowerGenome `atb_cap_recovery_years`; their `gen_max_age` is 500), r 0.05.
    The factor is AF(0.05, 10) / AF(0.05, 30) = 7.7217 / 15.3725 = **0.5023**, so **$13.81/MWh**.
  - A credit that lasts at least the life is not scaled.
- **Checks:**
  - The build stops on a hand-entered `levelised_value_per_mwh` that disagrees with the computed value, and on a
    project-period credited both by `tax_credit_values` and by a term with a different value.
  - It warns when a term's rate differs from `interest_rate`, and that the period discount rate (0.03) differs from
    the levelisation rate.
- **Outputs:** `credit_levelisation_report.csv` in the case folder, per credit, technology and period: value,
  duration, r, life (and its source), factor, result, run mode (perfect foresight / myopic / rolling, from
  `s0_production.foresight.mode`) and the discount rate. The treatment is the same in every mode.
  - `gen_tax_credits_by_vintage.csv` is the hook for vintage-indexed dispatch credits. Nothing reads it yet.
  - These files are written only when a statutory term applies, so S0 cases gain no files.
- **New nuclear's $15:** a placeholder, already an in-model value (`prelevelised: true` in `full_ira`; S0's
  `no_wind_solar` keeps it as the hand-entered `tax_credit_values` $15, exactly as in v3). It is flagged in the
  report.
- **Old version:** `full_ira_unlev_v3` is the v3 full-life version ($27.5 on every MWh of a credited plant's in-model
  life). It is only for reproducing S1_v3 / S2_v3, and for the fedpol rows (`s4x1_fedpol_biden*`, `_reinstate*`),
  which keep their inputs.
- **Statutory tally after a solve:**

  ```bash
  python s0_workflow/scripts/credit_tally.py switch/in/<root>/scenarios_<case>.txt --out credit_tally.csv
  ```

  For every credited vintage (model and predetermined builds): energy × the statutory $/MWh for its first 10 years in
  service, by calendar year, with a PV column at the model's discount rate to its base year
  (`s0_workflow/credit_tally.py`).

## Reuse from S0prod_A (`reuse_chain_stages.py expected`)

| Case | Reusable stages |
|---|---|
| S1, BILL_central_S1 | 2028 (credits differ from 2030) |
| S2, S4, L, P, S3, S5 | none (the build rate differs from 2028) |
