# Run card 1/7: BILL_central (v3.2)

**Commit:** the hash on this card's line in `s0_workflow/runcards/v32/READY_SCENARIOS`. Run only once that file
exists. That is normally S0 v3.2's commit (`READY_S0_V32`) or a later one whose changes since are only in
`s0_workflow/runcards/` or a scenario decision.

**Order:** BILL_central → BILL_central_txonly → S1 → S4 → S2 → S5 → S3. This card is number 1.

**Definition:** v3.1's, unchanged (rows and axis values byte for byte f8f12bc's; `test_s_set_on_v32.py`).
- `tx_bill: bill_central`. From 2035: moratorium on interregional lines, national cap 3.0 TW-mi/yr, headroom `atts_reform`, build rate `reform_bp`. 2028–2030 as S0.
- **Base:** the v3.2 defaults, as S0 v3.2 (`s0_v32.md`, "Settings in force"); WA `per_mwh`; the build-rate package
  R1–R7 off.

## Prerequisites

- S0 v3.2 solved and recorded as the reuse reference (`s0_v32.md` section 5).
- Recipe L in force in this worktree: offshore split folder, `pg_data/profiles_pvfix`, the v3.2 cluster cache with
  its `S0_V32_CACHE.json` marker, and `set PYTHONPATH=<wt>\PowerGenome` (the build stops otherwise).

## Build, reuse, solve

```bat
"<switch-pg-reeds-fedpol python>" pg_to_switch.py pg/settings switch/in/bill_central_v32 --case-id BILL_central ^
    --year 2028 --year 2030 --year 2035 --year 2040 --year 2045
python s0_workflow/scripts/reuse_chain_stages.py reuse switch/in/bill_central_v32/scenarios_BILL_central.txt ^
    --reuse-from switch/in/s0prod_A_v32/scenarios_S0prod_A.fixed.txt --solver-args "<S0 v3.2's string>" --dry-run
```
- **Reuse:** if the dry run agrees, run it again without `--dry-run`. Then run `run_chain_A.py` on the printed
  `scenarios_BILL_central.from_<stage>.txt`, and `record` the case's scenarios file.
- **Code check:** add `--code-check model` only if the reuse step reports code changes in `pg/settings/` or
  `s0_workflow/` since S0 v3.2's commit. Inputs are compared by content either way.

| | |
|---|---|
| Expected reuse from S0 v3.2 | 2028, 2030 |
| Stages to solve | 2035, 2040, 2045 |
| Estimated run time | 6.5–8 h (scaled from S0 v3.2's 10–11 h; use S0 v3.2's per-stage times) |

If reuse stops earlier than expected, report the first differing file. For a stage expected to reuse, any difference
means this build's base differs from S0 v3.2's.

## Pre-solve checks

- **The v3.2 base:** the same as `s0_v32.md` section 4, items 2–8 and 10 (PowerGenome, PV repair, offshore, cache,
  start-up costs, sample fidelity, CA tranche, wind price factor).
- **This case:** From 2035: `tx_policy` lines in `s0_production_log.txt` show the moratorium and 3.0 TW-mi/yr; `ic_*` files use `atts_reform`; `build_rate_periods.csv` has the `reform_bp` rates.

**Report:** the reuse result, then per stage the wall time, total cost, emissions and new capacity by technology, and
the difference from S0 v3.2.
