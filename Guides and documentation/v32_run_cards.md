# v3.2 run cards: bill and S-series (CHANGES §133)

Run order: **BILL_central → BILL_central_txonly → S1 → S4 → S2 → S5 → S3**, after S0prod_A v3.2.

## Branch and commit

- **Branch:** `tom/s0-v3.2`. No separate scenarios branch.
  - Every case's row (`pg/extra_inputs/scenario_inputs.csv`) and axis values (`pg/settings/scenario_management.yml`)
    have been on this branch since §115, byte for byte the credits branch's (f8f12bc).
  - The only change since is the regression row's v3.1 pins (`on_pgdays`), which none of these cases uses.
  - A new branch would only copy them.
- **Commit:** build every case at **the same commit as the S0 v3.2 run** (the reuse reference), or at cb529fa.
  - Commits after 7f1b384 change only `pg/settings/s0_production.yml` (the full PV sha256 in place of its prefix), the
    flags-off patch, docs and tests. None changes an input file.
  - If S0 v3.2 was built at 7f1b384 and these at cb529fa, the default reuse code check stops, because
    `pg/settings/` counts as code. Add `--code-check model`, as §80: inputs are then compared by content, which
    catches any real difference.

## The base (what v3.2 adds to every case; tested in `test_s_set_on_v32.py`)

**On:**
- wind interconnection price factor 1.0 (§126);
- storage credit `obbba` with `span_mean` (§113, §126);
- CA free tranche D2, 52.7 TWh/yr (§114);
- `cf_fidelity` rescale (§116);
- `startup_cost_fix` (§119);
- `prm_temp_tz_fix` (§118);
- offshore split (§129);
- PV repair from `pg_data/profiles_pvfix` (§132).

**Off:**
- WA two-tranche (`per_mwh`, as v3.1);
- the build-rate package R1, R11, R2, R4 and R7 (§121–§125).

**Prerequisites:** the same as S0 v3.2. The offshore split folder (recipe L) and `pg_data/profiles_pvfix` must be in
place, or the build stops.

The scenario definitions are v3.1's: the credits, headroom, build-rate level, gas-turbine cap and transmission bill of
each case are unchanged. The test compares each case and year with f8f12bc; the only differences are the v3.2 flag
paths.

## Flags: conflicts and interactions with the v3.2 defaults (not resolved; for Tom)

1. **CONFLICT: the storage credit phase-down in the "reinstated" credit cases (S1, S2, S3; `tax_credits: full_ira` from 2030).**
   - **v3.1:** the battery ITC was a flat ×0.7 in every period, so S1–S3 kept it to 2045, alongside their reinstated
     45Y PTC (`build_years: [2025, None]`, no end).
   - **v3.2:** `storage_credit: obbba` applies the OBBBA schedule to the battery credit in every case. The credit is
     ×0.7 in the 2028–2030 stages, 0.745 in 2035 and gone from 2040.
   - So in S1–S3 the wind and solar credits are reinstated without end, but the storage credit is phased out on
     OBBBA's dates. "Reinstated IRA" would have kept 48E for storage, so the two parts of the scenario no longer match.
   - S0, BILL, S4 and S5 are current-law cases, so OBBBA's schedule is consistent there.
   - **Options:**
     - (a) keep it: storage follows current law everywhere;
     - (b) add `storage_credit: {mode: flat}` to the S1/S2/S3 axis values, keeping v3.1's ×0.7 (a one-line change per
       value, which breaks byte identity with f8f12bc on purpose);
     - (c) a reinstated phase-down, e.g. none to 2045.
   - **Effect on reuse:** S1 2028 still reuses (the stages agree up to 2033). A stage from 2035 differs from v3.1's.
2. **Interaction: CA free tranche 52.7 TWh (D2) in every case.** No case sets its own tranche, so the bill and S deltas
   against S0 v3.2 are like for like. Comparisons with v3.1 results mix in the tranche change (49.4 → 52.7 TWh) along
   with the other v3.2 defaults.
3. **Interaction: wind price factor 1.0 in every headroom scenario.**
   - BILL from 2035 (`atts_reform`), S2/S4 (`atts_planned`) and S3/S5 (`atts_reform_techmax`): wind now pays the full
     interconnection step price where v3.1 paid 0.25 × it.
   - Under `techmax`, any step price left also applies at 1.0. The case definitions are unchanged; the price wind faces
     is not.
4. **Interaction: offshore split (resource offered once, not three times).** This matters most where offshore is
   unconstrained or cheap to connect: S3/S5 (techmax headroom, unconstrained transmission, no build rate) and the
   reform stages of BILL. Every case shares it, so deltas against S0 v3.2 are consistent.
5. **The build-rate package is off.** BILL from 2035 (`reform_bp`) and S2/S4 (`high`) use the existing v3.1 tables.
   S3/S5 have no build rate. Nothing to resolve.
6. **WA:** `per_mwh` as v3.1 (§127). It is immaterial at 2028's volume, and recipe K tests it.

## Run cards

Expected reuse is from `reuse_chain_stages.py expected` on this branch. It is identical to v3.1's pattern; the stages
are byte-identical only if built at the same commit (or as above) with the same data folders.

**Run-time estimates:** scaled from S0 v3.2's 10–11 h for five stages. No per-stage chain timings are recorded in the
repo; the runner's `measure_run.py` per-stage times give the real figure (the sum over the stages each case solves).
Later stages are larger than 2028/2030, so treat each estimate as a range.

| Order | Case | Scenario flags (row columns that differ from S0prod_A) | Reuse from S0 v3.2 | Stages to solve | Estimate |
|---|---|---|---|---|---|
| 1 | `BILL_central` | `tx_bill: bill_central` (2035 on: moratorium in force, 3.0 TW-mi/yr, headroom `atts_reform`, build rate `reform_bp`) | **2028, 2030** | 2035, 2040, 2045 | 6.5–8 h |
| 2 | `BILL_central_txonly` | `tx_bill: bill_central_txonly` (2035 on: the transmission parts only; headroom and build rate as S0) | **2028, 2030** | 2035, 2040, 2045 | 6.5–8 h |
| 3 | `S1` | `s_set: S1`; `tax_credits: full_ira` from 2030 (45Y PTC without end, nuclear placeholder, battery ×0.7); central build rate, `atts_s0` | **2028** | 2030–2045 | 8.5–10 h |
| 4 | `S4` | `s_set: S4` (build rate `high`, headroom `atts_planned`, credits current) | none (2028 differs: build rate) | all five | 10–12 h |
| 5 | `S2` | `s_set: S2`; `tax_credits: full_ira` from 2030 (as S4 plus reinstated credits) | none | all five | 10–12 h |
| 6 | `S5` | `s_set: S5`, `tx_bill: unconstrained` (build rate off, headroom `atts_reform_techmax`, gas-turbine cap off, credits current) | none | all five | 11–14 h (larger unconstrained build space) |
| 7 | `S3` | `s_set: S3`, `tx_bill: unconstrained`; `tax_credits: full_ira` from 2030 (as S5 plus reinstated credits) | none | all five | 11–14 h |

**For each case (recipes C and J):**
1. **Build:**
   ```bash
   pg_to_switch.py pg/settings switch/in/<root> --case-id <case> --year 2028 --year 2030 --year 2035 --year 2040 --year 2045
   ```
2. **Reuse:** for BILL_central, txonly and S1:
   ```bash
   reuse_chain_stages.py reuse switch/in/<root>/scenarios_<case>.txt \
       --reuse-from <S0 v3.2 scenarios .fixed.txt> --solver-args "<S0 v3.2's string>" [--code-check model] --dry-run
   ```
   then without `--dry-run`. Expect the reused stages above and a `solve` row naming the first differing stage. If
   reuse stops earlier, report the first differing file: a 2028/2030 difference for BILL means the base differs from
   S0 v3.2's build.
3. **Solve the rest:** `run_chain_A.py` on the printed `scenarios_<case>.from_<stage>.txt`, then `record` it.

**Build checks (each case):**
- `s0_production_log.txt` shows the PV repair folder (correlation, the full sha256), the offshore split, the start-up
  cost line and `cf_fidelity` rescale.
- `offshore_mw_by_zone.csv` has ratio 1.00 in every zone.
- `trans_import_tranche.csv` has 52,721,000 MWh.
- `investment_credit_report.csv` has the battery multipliers 0.70 / 0.70 / 0.745 / 1.0 / 1.0. This applies to S1–S3 too
  unless Tom changes item 1.
