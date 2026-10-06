# Transmission-bill scenarios and the S0 transmission baseline

Branch `tom/s0-prod-scripts`, CHANGES §60. Since §66 the S0_tx baseline is the S0 default: `S0prod_A`, `S0prod_B`
and `s4x1_S0prod_2035_new` use `tx_bill = s0_tx` and `reeds_certain_plus_A` (so `S0prod_A` equals `S0_tx`), and the
yml default is `tx_policy.mode: national_cap`; `tx_bill = legacy` sets `tx_policy: legacy` explicitly. Every mechanism below is a setting in
`pg/settings/s0_production.yml`, switched per case through two scenario axes (`tx_bill`, `tx_sens`) and the
`forced_tx` axis. All older rows use `tx_bill = legacy` and `tx_sens = none`, which set nothing, so every existing
case (the regression case `s4x1_S0prod_2035` included) builds as before.

Code: `s0_workflow/tx_policy.py` (case build), `switch/study_modules/tx_build_cap.py` (Switch), `s0_workflow/production.py`
(levels by period, forced options), `switch/study_modules/prepare_next_stage.py` (headroom carried across a scenario
switch), `switch/study_modules/prm_regional.py` (reserve import allowance).

## Scenarios

| `tx_bill` value | Moratorium (first period interregional lines can be built) | National cap on discretionary additions (TW-mi/yr) | Headroom (`interconnection_headroom.scenario`) | Generator build rate | Reserve import allowance |
|---|---|---|---|---|---|
| `s0_tx` (S0, no bill) | 2040 | 0 in 2028, 1.4 from 2030 | atts_s0 | central | 0 (historical shares) |
| `bill_central` | 2035 | 0 in 2028, 1.4 in 2030, 3.0 from 2035 | atts_s0, atts_reform from 2035 | central, reform from 2035 | 0.85 from 2035 |
| `bill_low` | 2040 | 0 in 2028, 1.4 in 2030, 2.0 from 2035 | atts_s0, atts_reform from 2040 | central, reform from 2040 | 0.85 from 2040 |
| `bill_high` | 2035 | 0 in 2028, 2.0 in 2030, 4.0 from 2035 | atts_s0 (2028), atts_reform (2030), atts_reform_techmax from 2035 | central (2028), reform from 2030 | 0.85 from 2035 |
| `bill_central_txonly` | as bill_central | as bill_central | atts_s0 | central | 0.85 from 2035 |
| `bill_central_bronly` | as s0_tx | as s0_tx | as bill_central | as bill_central | 0 |

**Every bill row is S0 until each change takes effect** (§72). The import allowance starts in the first period
new interregional lines may be built (the moratorium's end). Until then a bill row's case inputs are byte-identical to
S0's, so its early stages can be reused from S0prod_A (`s0_workflow/chain_reuse.py`, VM recipe J):
- BILL_central, BILL_low, txonly and bronly: 2028 and 2030;
- BILL_high and BILL_central_S1: 2028.

Before §72 the bill rows used headroom atts_planned, a 1.4 cap and the allowance from 2028.

ERCOT ties and ERCOT-internal lines always keep the no-bill values: moratorium 2040 and the S0 cap trajectory (see
"Interpretations").

The S0 transmission baseline and all the bill cases force `reeds_certain_plus_A`: ReEDS's two certain HVDC lines
plus the class-A projects of the status review. **That list isn't in yet.** `pg/extra_inputs/transmission/forced_tx_status_review.csv`
is a header-only placeholder, and these cases stop at the build with "placeholder until the status-review list is
supplied" until it has class-A rows.

## Cases (`pg/extra_inputs/scenario_inputs.csv`)

- **Mode-A chains, 2028-2045:**
  - `S0_tx`, `BILL_central`, `BILL_low`, `BILL_high`, `BILL_central_txonly`, `BILL_central_bronly`, `BILL_central_S1`.
  - Each is `S0prod_A` (S0 defaults, mode A, regional reserve) with `tx_bill` and `forced_tx = reeds_certain_plus_A`.
- **Single-year 2035 test versions:**
  - `s4x1_<case>_2035`: the 2035 row of the same with `s0_production = on_single` (one stage, fleet-independent days).
  - In a single stage, every forced line is due in 2035, and only each scenario's 2035 values apply.
- **`BILL_central_S1`:** `BILL_central` with fedpol S1's tax credits (`tax_credits`: `no_wind_solar` in 2028,
  `full_ira` from 2030, as `s4x1_fedpol_reinstate_S1` on `ollie/fedpol`). The `policies` value is unchanged. fedpol's
  S1 also differs from its S0 in `policies` (growth caps `S1.csv`) and `trans_expansion` (nerc_growth); those are
  not carried over.
- **Sensitivity hooks (2035 test rows on `S0_tx`):**
  - `s4x1_S0_tx_2035_forcedAB`: classes A and B forced (`tx_sens = forced_ab`, `forced_tx = reeds_certain_plus_AB`);
  - `_floor`: minimum transfer floor by region pair (`transfer_floor`; placeholder file, so it stops until filled);
  - `_txcapex`: transmission capex ×1.5 (`tx_capex_x1_5`);
  - `_brhigh`: build rate "high" in every period (`br_high`);
  - `_osw`: offshore wind approvals restored (`offshore_wind_policy = capped_2025_released`).

## 1. Interregional moratorium

- **Interregional line:** its two ends are in different transmission planning regions (`hierarchy.csv` transreg).
- **Before its first allowed period:** the line's `trans_path_expansion_limit` is 0 in each period.
- **Exceptions:**
  - a forced line in its forced period is not held back;
  - the constrained transmission policy used to block every interregional line not in the forced list
    (`trans_new_build_allowed` 0). Under the national cap those lines are unblocked and governed by the moratorium.
- **ERCOT:** ERCOT ties use the no-bill year (2040) in every case. ERCOT-internal lines are intra-region (ERCOT is
  one transreg), so the moratorium never applies to them.

## 2. National cap on discretionary transmission

- **Constraint, per period:**

      sum over non-exempt lines of BuildTx x trans_length_km  <=  cap (TW-mi/yr) x 1.609344e6 MW-km/TW-mi x period years

- **Units:** BuildTx is MW of transfer capability (nameplate, the model's basis). 1 TW-mi = 1e6 MW × 1.609344 km.
- **Scope:** national, intra-region and interregional together, and reported separately.
- **Forced lines** in their forced period are exempt (`tx_cap_exempt.csv`). In other periods they count like any
  line.
- **No-bill lines:** a second constraint holds lines with an end in ERCOT to the no-bill trajectory.
- **Replaces `trans_expansion_policy`:**
  - the per-line limits of `zero` (0 everywhere) are replaced: no per-line limit except the moratorium rows and a
    forced line's cap at its minimum in its forced period;
  - the line flags, derates, hurdles and directional limits are as before.
- **Cap values:** each key holds until the next, and periods before the first key take its value. S0 has 0 in 2028
  (no discretionary transmission, as today's `zero`) and 1.4 from the 2030 stage.
- **Output `tx_build_cap.csv`, by period:**
  - cap (TW-mi/yr and MW-km);
  - intra-region, interregional and total additions (MW-km and TW-mi/yr);
  - no-bill additions;
  - exempt (forced) MW-km;
  - duals.
- **Transfer floor (sensitivity):** `tx_policy.transfer_floor` is a CSV with columns `region_a, region_b, PERIOD,
  min_transfer_mw` (transreg pairs). For each pair, the transfer capability of the lines between them
  (TxCapacityNameplate, existing included) must reach the floor. Output `tx_floor.csv`. The committed
  `tx_transfer_floor_placeholder.csv` is header-only, so the floor case stops until it is filled.
- **Capex multiplier (sensitivity):** `tx_policy.capex_multiplier` scales `trans_capital_cost_per_mw_km`.

## 3. Headroom scenario and build-rate level by period (mode A)

- **Setting:** `levels_by_period: {interconnection_headroom: {period: scenario}, build_rate: {period: level}}`. Each
  key holds until the next. Each stage of a mode-A chain is built with its own period's scenario and level.
  `level_overrides` sets one value in every period (the `br_high` sensitivity).
- **Build rate:** each stage's `build_rate_*.csv` come from its level, and the chained `build_rate_prev_build` carries
  the ramp history, which doesn't depend on the level.
- **Headroom switch:** the four ATTS scenarios share the zones and the empirical cost curve (`ic_zones`,
  `ic_tranches`). They differ only in the deliberate uprates: GETs and reconductoring caps and available years. When a
  stage's scenario differs from the previous period's:
  - the case build writes `ic_scenario_switch.csv` in that stage;
  - the previous stage's `prepare_next_stage` carries the saturation and the remaining curve as usual;
  - the new stage gets its own scenario's uprates, each less what the chain has already built of it (matched by
    `IC_UPRATE`).

  A switch between scenarios whose zones or curve differ stops with an error rather than mixing two curves.
- **Mode B would need:** a window spans two periods, so a level that changes inside a window needs per-period inputs
  in one stage:
  - headroom: both scenarios' uprates, distinguished by `ic_uprate_available_year` or a per-period cap;
  - build rate: per-period supply-curve parameters.

  At the window handoff, the switch logic above applies to the committed period. The moratorium and the cap are
  already per period and work in windows as written. The reserve allowance uses the window's BuildTx by period.

## 4. Reserve import allowance (bill cases)

- **Cap:** for each PRM region and stress hour, net reserve imports ≤ historical share × peak + 0.85 × the new
  capacity built into the region to date.
- **New capacity:** nameplate MW on lines crossing the PRM region's boundary: this stage's BuildTx up to the period,
  plus earlier stages' `trans_built_to_date`.
- **Setting:** `prm.imports.new_tx_allowance`, a number or a period-keyed table (`{2028: 0.0, 2035: 0.85}`; each key
  holds until the next). S0 is 0, so historical shares only. `prm_params.csv` gets the column only when the stage's
  value is above 0. A stage spanning periods with different values is an error (mode A stages have one period).
- **Region:** this uses the PRM regions (NERC regions, WECC_NW split), the regions the import cap is defined on, not
  transreg.

## 5. Forced transmission: `reeds_certain_plus_A` / `_AB`

`pg/extra_inputs/transmission/forced_tx_status_review.csv` has one row per project. Its columns:

| column | meaning |
|---|---|
| from_zone, to_zone | model zones (pN) |
| project_name | |
| status_class | A or B |
| new_cap_mw | MW of transfer capability |
| mw_basis | must be `transfer_capability`; convert other bases first (the build stops otherwise) |
| new_cap_year | in-service year (forced in the period whose span holds it) |
| trans_length_km, trans_efficiency | needed to add a corridor the network doesn't have |
| source, notes | |

- **Options:** `reeds_certain_plus_A` forces the ReEDS certain lines plus the class-A rows; `reeds_certain_plus_AB`
  adds class B.
- **Same zone pair:** a status-review row for a pair already in the ReEDS table replaces it.
- **Several projects on one pair** (CHANGES §61): each is forced in the period whose span holds its in-service year.
  - Projects in the same period add up.
  - The minimum is cumulative (`trans_build_minimum` counts new capacity up to the period).
  - The forced-period cap is that period's own projects. In a chain, the later stage's minimum less what the earlier
    stage built (`prepare_next_stage`) is that period's projects again.
  - In 2eb325d, `_AB` has two such pairs:
    - p81–p83: #14 (A) and Coffeen North–Roxford (B), both in 2030, so the MW add up;
    - p80–p105: #16 (A) in 2030, then #42 (B) in 2035.

    Class A has none, so `_plus_A` is unchanged.
- **The build stops** if the file has no rows of the classes asked for, a row's MW basis is wrong, or a required
  field is missing.

## Interpretations to confirm

1. **ERCOT, "always the no-bill values":**
   - ERCOT ties keep the 2040 moratorium.
   - ERCOT ties and ERCOT-internal additions together stay within the S0 cap trajectory (a second constraint inside
     the national cap).
   - ERCOT-internal lines are intra-region, so the moratorium doesn't touch them.
2. **S0 cap in 2028 = 0:** "1.4 from the 2030 stage", and today's `zero`.
3. **bill_high in 2028:** S0's headroom (atts_s0), cap (0) and build rate (central). The bill's reforms start in 2030.
4. **The cap counts nameplate transfer capability** (BuildTx) × length. It uses 1.609344e6 MW-km per TW-mi; the
   request gave 1.609e6.
5. **"Interregional":** for the moratorium and the cap it means transreg. For the reserve allowance it means the PRM
   region boundary.
6. **Offshore wind approvals restored** = `offshore_wind_policy: capped_2025_released`.
