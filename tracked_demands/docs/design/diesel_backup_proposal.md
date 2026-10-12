# Diesel backup for tracked demands: design proposal (CHANGES §120)

Status: accepted by Tom on 12 Oct 2026, with the decisions in the decisions register (E1–E6), and implemented in
CHANGES §120 (guide section 16). Written as §109; the section is §120. The text below is the proposal as accepted.

## 0. What the current model already does, and why it matters here

- **Stress timepoints.** These are the zero-weight timeseries `<year>_pN_prm` listed in `prm_timeseries.csv`.
  - S0's greedy rule picks at most 12 single days per model year (`max_days: 12`), drawn from the 7 weather years
    2007–13 to cover 16 reserve regions. Each day is 24 one-hour timepoints (`tp_duration_hrs` = 1).
  - The days are dated (`<case>/prm/<year>/stress_days.csv`).
  - Under S0's `light` formulation (`stress_light_timeseries.csv`) there is no fuel use, emissions or cost on them.
    Under `full` there is, but at weight 0.
- **A tracked demand on a stress day.**
  - Served load is between (1 − c)·L and L, where L comes from the stress mode (max, profile or min) and c is the
    curtailable share.
  - Grid draw is at most the connection.
  - The PRM requirement is a constant per period, min(connection, peak)·(1 − c) (D1b). It is added to every stress
    hour (`TDPrmLoad[z, p]`), and on-site plant gets no reserve credit.
- **Consequence.** Under D1b as it stands, a backup fleet has no modelled job:
  - the grid can always cover the demand up to its connection;
  - diesel costs nothing on zero-weight hours;
  - it earns no reserve credit.

  Its dispatch would be arbitrary (degenerate), and its hours meaningless. So the design needs a cost on stress-day
  running (3, C5) and a decision on what the fleet is for (section 6).

## 1. Spec fields (per tracked demand, case writer)

```yaml
diesel:
  config: grid_backup          # grid_backup | blackstart | none
  multiple: 1.3                # grid_backup: diesel MW = multiple x td_grid_interconnect_mw (not peak load)
  unit_mw: 2.5                 # unit nameplate, for cap coverage (td_onsite_unit_mw)
  tier: 2                      # 2 | 4: NOx reporting and whether non-emergency running is allowed
  heat_rate: 10.2              # MMBtu/MWh (PLACEHOLDER until the input research gives one)
  forced_outage_rate: 0.05     # availability on stress days (PLACEHOLDER)
  variable_om: 10              # $/MWh (PLACEHOLDER)
  fixed_om_per_kw_yr: 0        # sunk reliability plant: 0 by default
  permit_flh: 42               # site permit cap, full-load-equivalent hours per year (VA/MD evidence 24-42)
  test_flh: 0                  # hours per year reserved for testing (counted in the cap, reported)
  non_emergency_flh: 0         # 0 = emergency only (default); > 0 only with tier 4 or permitted_non_emergency
  permitted_non_emergency: false
  stress_year_basis: weather_year   # weather_year | all (section 3, C2)
```

- **Default `config`:** if the spec leaves it out, the writer uses `blackstart` when the demand has gas-fired linked
  generators and `grid_backup` otherwise, and logs the choice.
- **blackstart and none:** no generator. For blackstart the writer logs "black-start diesel not modelled (a few MW,
  negligible energy)"; an optional `blackstart_mw` is reported only.

## 2. Case writer (grid_backup)

One linked generator per tracked demand, `<TD>-diesel`:

- **Generator:**
  - `gen_tech` `TD_diesel`, so it is in no build-rate group;
  - the case's distillate fuel (the writer stops if the zone has none);
  - heat rate, variable O&M and forced outage rate from the spec;
  - UC values from the `diesel` defaults;
  - `gen_unit_size` blank, so the build stays continuous (15.1);
  - `gen_max_age` 40.
- **Existing capacity at zero capex:** a `gen_build_predetermined` row of MW = multiple × `td_grid_interconnect_mw`
  at build year (first period start − 1), with a `gen_build_costs` row for that year at overnight 0 and the spec's
  fixed O&M.
  - No rows for model periods, so the model can't build more.
  - Base and chained files both get the rows, as for the other edited files.
  - A chain carries the capacity forward like any existing plant.
- **Endogenous connection:** sized on `td_grid_interconnect_mw`, the connection's upper bound. The fleet is installed
  for the planned connection whatever size the model picks.
- **Hybrid sites:** sized on the connection, so only the grid share is backed up.
- **Other modules' settings:**
  - `prm_credit none`;
  - ic weight 0;
  - gas-turbine cap category none;
  - gas capex premium class none;
  - never clean in matching (as every linked generator);
  - `td_onsite_unit_mw` = `unit_mw`; `td_onsite_rated_mw` = fleet MW.
- **New file `tracked_demand_diesel.csv`:** `TRACKED_DEMAND, GENERATION_PROJECT, td_diesel_permit_flh,
  td_diesel_test_flh, td_diesel_non_emergency_flh, td_diesel_tier, td_diesel_forced_outage_rate`.
- **New file `tracked_demand_stress_years.csv`:** `TIMESERIES, td_stress_weather_year`, from the case's
  `prm/<year>/stress_days.csv` dates. If those are absent, all stress days are one year (a warning).
- **Checks that stop the writer:**
  - `non_emergency_flh > 0` with tier 2 and no `permitted_non_emergency`;
  - `test_flh + non_emergency_flh > permit_flh`.

## 3. Module (Pyomo, in tracked_demands.py; no shared edits under options A and B)

**Sets and parameters**

- `TD_DIESEL_GENS` (dimen 2, within `TD_ONSITE_GENS`), from `tracked_demand_diesel.csv`, with the parameters above.
- `td_diesel_mw[td, g]`: the predetermined MW, a parameter. This keeps every constraint below linear.
- `TD_STRESS_GROUPS`: (p, w) pairs.
  - `td_stress_group[t]` maps each stress timepoint of period p to weather year w (`tracked_demand_stress_years.csv`).
  - With basis `all`, or without the file, there is one group per period.
- **Real duration of a stress hour.** Use `tp_duration_hrs[t]`, i.e. `ts_duration_of_tp` of its timeseries; 1 h in
  S0, a 24 h day. Each stress timepoint counts once a year: a stress day is a real day that happened once, in its
  weather year. Never `tp_weight`, which is 0.

**Constraints**

- **C1, emergency only.** `TD_Diesel_Emergency_Only[td, g, t]`, for t not in `TD_STRESS_TPS`:
  `DispatchGen[g, t] == 0`. It is skipped when `non_emergency_flh > 0`; then C2's middle term limits normal-hour
  running.
- **C2, permit cap, per year.** `TD_Diesel_Permit_Cap[td, g, p, w]`:

  ```
  sum_{t in stress tps of p in group w} DispatchGen[g, t] * tp_duration_hrs[t]           (emergency, real hours)
  + sum_{t in TPS_IN_PERIOD[p] not stress} DispatchGen[g, t] * tp_weight_in_year[t]     (non-emergency, per year)
  + test_flh * td_diesel_mw
  <= permit_flh * td_diesel_mw
  ```

  - **Per year, not per period.** Every term is per year: the normal-hour term is annualised by
    `tp_weight_in_year`, and each weather-year group is one year. The cap holds in every period and binds on its
    worst weather year.
  - **Why group by weather year:** pooling all of a period's stress days (up to 12 days, 288 h, from 7 weather
    years and 16 regions) into one year would ask the fleet to cover 288 h against a 24–42 h permit. That is far
    stricter than any real year, which would contain only that year's days (typically 1–3).
- **C3, no export from diesel.** `TD_Diesel_No_Export[td, t]`:
  `TDGridExport[td, t] <= sum of DispatchGen of the TD's non-diesel linked generators at t`. Emergency engines
  don't supply the grid.
- **C4, availability on stress days.** `TD_Diesel_Availability[td, g, t]`, for stress t:
  `DispatchGen[g, t] <= td_diesel_mw * (1 - td_diesel_forced_outage_rate)`. prm_regional's light limit uses
  `prm_avail_frac`, which defaults to 1 for a generator with no credit.
- **C5, emergency running cost.** `TDDieselStressCost[p] = sum over stress t of DispatchGen * tp_duration_hrs *
  (fuel cost x heat rate + variable O&M) / n_groups[p]`.
  - It is added to `Cost_Components_Per_Period`: the expected cost per year, with each weather-year group one year.
  - It makes stress-day dispatch determinate: diesel runs only when it relieves something.
  - Fuel cost is the case's distillate cost in the zone and period.
- **C6, visible shortfall.** The module's own `TD_Stress_Load_Min` gains a slack:
  `TDDispatch[td, t] + TDStressShortfall[td, t] >= (1 - c) * L(td, t)`.
  - The slack is penalised at `td_stress_shortfall_penalty` (default 10,000 $/MWh) × `tp_duration_hrs` /
    `n_groups`.
  - With `td_stress_strict = 1` the slack is fixed at 0, and the run is infeasible after the pre-solve message
    below.

**Pre-solve check (item 5).** For each TD, period and weather-year group:

- need = sum of max(0, (1 − c)·L_t − s·connection) × `tp_duration_hrs` (s from section 6, B);
- supply bound = diesel MW × (1 − FOR) × (permit − test − non-emergency hours) + the other linked generators'
  capacity limits × hours + storage energy (when fixed);
- if need > bound: a WARNING naming the TD, period, weather year, the two numbers and the binding item (permit
  hours, fleet size, connection share). In strict mode it is an error.

It is a necessary-condition check: with endogenous sizes it can't prove feasibility, so it warns. The solve's slack
then shows the size of the gap, and the post-solve output lists every timepoint with shortfall > 0 and the cap's
dual.

## 4. Outputs

- **New `tracked_demand_diesel.csv`**, one row per TD, generator, period and weather-year group:
  - sizes and limits: `diesel_mw`, `permit_flh`;
  - hours: `stress_flh`, `non_emergency_flh`, `test_flh`, `total_flh`, `flh_remaining`, `cap_dual`;
  - energy and fuel: `stress_mwh`, `fuel_mmbtu_stress`, `fuel_mmbtu_non_emergency`;
  - emissions: `co2_t_stress`, `co2_t_non_emergency`, `nox_t` (tier factor × MWh, reporting only);
  - `shortfall_mwh`.
- **`tracked_demand_dispatch.csv`** gains `TDDiesel_MW` and `TDStressShortfall_MW`.
- **`tracked_demand_onsite_emissions.csv`:** diesel rows split emergency (stress) from non-emergency tonnes. Stress
  tonnes are flagged "outside the model's cap rows (zero-weight hours)". Stress fuel and CO₂ are computed by the
  module (dispatch × heat rate × real hours × the fuel's CO₂ intensity), the same under `light` and `full`.
- **Summary per TD and period:** the binding weather year, its hours left, and total shortfall.

## 5. Carbon-cap coverage

- **RGGI and VA (unit basis):** 2–3 MW units are below 25 MWe, so the fleet is exempt. This uses the existing
  per-unit test with `td_onsite_unit_mw`.
- **CA and WA (site basis, potential to emit):** the current PTE assumes full load all year (8760 h). That would put
  a 1.3 GW fleet at millions of tonnes, covered on day one.
  - Emergency engines' PTE is conventionally limited: by the permit's hours, or 500 h/yr under EPA's 1995 guidance.
  - Proposed: a new optional `td_onsite_pte_hours` (default 8760; diesel gets `permit_flh` from the writer), used
    by `_pte_tco2`.
  - Even at 42 h, a 1.3 GW fleet is ~40 kt CO₂/yr, above CA/WA's ~25 kt. So whether CARB MRR and WA count
    emergency-generator emissions toward the facility threshold matters. That is a question for the input research.
- **Stress-day tonnes** are outside the model's cap rows in any case (zero weight). They are reported, not deducted.
  Non-emergency hours go through Switch's normal emissions accounting and cap rows.

## 6. What the fleet is for: options (Tom's decision)

- **A. Reporting only.** No stress grid limit, D1b unchanged.
  - Diesel runs only if the stress-day energy balance makes it worth C5's cost: almost never in S0, where the PRM
    test binds first.
  - The outputs then show the fleet, its hours (≈0), its PTE and its cap coverage.
- **B. Stress grid limit (recommended as an option, default off).** A new `td_stress_grid_limit_share` s (default
  1):
  - **On stress days:** grid draw ≤ s × connection. This represents mandatory curtailment of large loads in
    emergencies, where backup generation carries the site (e.g. Texas SB 6 for large loads; utility interruptible
    tariffs).
  - **What covers the rest:** the curtailable share, then diesel, on-site plant and storage, within the permit cap.
    Any remainder is shortfall.
  - **PRM requirement:** becomes min(s·connection, peak)·(1 − c), still a constant per period. So prm_regional is
    untouched (no shared edit), and with s = 1 it is exactly D1b.
  - **The trade:** the requirement falls only as far as the fleet can carry (1 − s)·connection through the stress
    hours under the cap. Otherwise shortfall shows, or strict mode makes the run infeasible.
- **C. Requirement by hour from actual grid draw** (not recommended now). Diesel running would lower the demand's
  PRM load hour by hour. This needs `TDPrmLoad` per timepoint in prm_regional, a shared change to row #113 for
  Ollie.

**Interactions**

- **Stress modes:** L comes from the mode first. `profile` and `min` lower the hours diesel needs; the curtailable
  share comes off before diesel.
- **PRM rule:** under A it is unchanged; under B the requirement uses s·connection. On-site plant, diesel included,
  still gets no reserve credit (D1b).
- **Stress-day selection:** with the greedy rule a period has ≤ 12 days. With `weather_year` grouping, the cap
  applies to each weather year's days, typically 1–3 days, 24–72 h.

## 7. Where I'd differ from the brief

1. **The cap is per weather-year group, not over all stress days.** One pool would apply 288 h of stress days from
   7 years against a one-year permit.
2. **Stress-day running needs a cost (C5).** Otherwise diesel dispatch on zero-weight hours is arbitrary and the
   reported hours mean nothing.
3. **Non-emergency running is restricted.** As I read 40 CFR 60.4211(f) and 63.6640(f), emergency engines may run
   up to 100 h/yr for maintenance and testing, of which up to 50 h/yr non-emergency, but not for peak shaving,
   non-emergency demand response or supplying the grid for compensation. Economic running in normal hours is peak
   shaving, so the option is for tier 4 or engines permitted as non-emergency only. Citations to be confirmed by the
   input research.
4. **PTE for cap coverage must use limited hours (section 5),** or every CA/WA fleet is covered by construction.
5. **The fleet has no job under D1b without option B.** Say whether that is acceptable (A) or the point (B).
6. **Additions to the brief:** no export from diesel (C3) and availability on stress days (C4).

## 8. Test plan (toy, HiGHS; both pandas versions)

1. **Writer:**
   - grid_backup gives predetermined MW = 1.3 × connection in base and chained files, at zero capex, on distillate,
     with prm none, ic 0, gas-turbine cap none and premium none;
   - a hybrid site is sized on its connection; an endogenous connection on its upper bound;
   - blackstart and none add no generator;
   - tier 2 with non-emergency hours stops the writer;
   - `tracked_demand_stress_years.csv` comes from stress-day dates.
2. **Emergency only:** zero dispatch in every weighted timepoint. With s = 1, zero everywhere (C5).
3. **Grid limit s = 0.5:** diesel runs on stress days.
   - Stress hours are counted at `tp_duration_hrs` (FLH = Σ dispatch × hours / MW).
   - A tight permit binds: shortfall is reported, the dual is > 0 and the pre-solve WARNING appears.
   - A larger permit gives shortfall 0.
   - Strict mode is infeasible, after the message.
4. **Grouping:** two stress days in different weather years each get a full cap; two in the same year share one.
5. **Non-emergency (tier 4):** running in weighted hours is counted at `tp_weight_in_year`, shares the cap with
   stress running, and the emissions reach the cap rows.
6. **Stress modes:** profile and min lower diesel hours; a curtailable share lowers the need before diesel.
7. **PRM:** s = 1 gives the same requirement as without diesel; s = 0.5 gives min(0.5·connection, peak)·(1 − c).
8. **Cap coverage:** 2.5 MW units are exempt under RGGI's unit basis; the CA site test uses PTE at permit hours.
9. **Formulations:** light and full give the same diesel fuel and CO₂ outputs on stress days.
10. **Chain:** the fleet is carried into the 2030 stage, and the cap holds in each stage.
11. **Unloaded:** `test_td_unloaded.py` stays byte-identical.

**Size:** about 300 lines of module code, 120 in the writer, 12 tests. There are no shared edits under A or B; C
would touch prm_regional (#113, for Ollie).

## 9. Decisions needed from Tom

1. **Purpose:** A only, or B available (default s = 1)? My recommendation is B as an option.
2. **Stress-year basis:** `weather_year` (recommended) or `all`.
3. **Defaults:** `permit_flh` (24, 36 or 42), `test_flh`, and the default `config` when the spec omits it.
4. **Non-emergency running:** restricted to tier 4 or permitted engines (point 3 of section 7).
5. **PTE hours** for diesel cap coverage (permit hours, 500 or 8760), and the research question on CA/WA treatment
   of emergency engines.
6. **Shortfall:** the penalty value, and whether strict is the default.
