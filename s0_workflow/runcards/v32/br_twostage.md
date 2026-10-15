# Build-rate package: two-stage test (2028 + 2030) before S0 v3.2 final (CHANGES §247)

**Run this first.** S0 v3.2 final (`s0_v32.md`) runs only if this test passes. If it fails: **STOP**, report it, and
run nothing else.

**Commit:** the hash in `READY_S0_V32`, on `tom/s0-v3.2`.

**What it tests:** the v3.2 build-rate package, now ON in the defaults:
- **R1:** the ramp base is max(the chained build, the previous stage's data R);
- **R11:** R is max(the data R, the previous stage's build ÷ 1.3);
- **R4:** the ramp floor is ReEDS's 100 MW/yr per state × 48 = 4,800 MW/yr per group;
- **R2:** a linear 2028 → 2030 hand-over of R;
- **R7:** construction-stage completion from LBNL (0.722).

2028 and 2030 are enough to see R1 and R2. The bridge showed why R1 matters: the costlier wind connection cut 2028–30
wind, and the ramp, based on the model's own past build, then locked in 11.5 GW less wind in every later stage.

**Isolation:** the test is compared with **S0 v3.2 pre** (root `s0prod_A_v32`, built at 4894d6a, stages 2028 and 2030).
- The package is the only intended difference.
- The other final-run changes are inert or turned off here:
  - the CA nuclear ban is inert: no new nuclear is offered before 2035;
  - the load/hydro rescale is turned off by `s0_workflow/data/v32/br_twostage_isolation.patch`.

**A test run at an earlier READY commit still counts (§249, §250).** A test already run at 5d2e60a certifies S0 v3.2
final at the current READY commit. Don't re-run it. Since 5d2e60a, the only change to the 2028 and 2030 inputs is
Diablo Canyon's retirement date, set by the case writer (§250):
- `gen_info.csv`: `p9_nuclear_1`'s `gen_max_age` is 45 instead of 500. Both units stay in service in both stages, at
  the same 2,240 MW, with no capital cost (overnight cost 0);
- two new files, `retirements_applied.csv` and `retirement_checks.csv`, and their log lines.

None of this touches the build-rate package, its code or its pass criteria. If the test hasn't run yet, run it at the
current READY commit, after the smoke build (`s0_v32.md` section 2a). The expected-differences list in step 4 then also
includes `retirements_applied.csv`, `retirement_checks.csv` and `gen_info.csv`, the latter for `p9_nuclear_1`'s
`gen_max_age` only.

## 0. Worktree and prerequisites

- A scratch worktree at the READY commit, set up as `s0_v32.md` section 1: the submodule at c4c433c and
  `PYTHONPATH=<wt>\PowerGenome`.
- Recipe L's data in place: `pg_data/profiles_pvfix`, `pg_data/resource_groups_v32` and `…__meta`.
- The v3.2 cluster cache from S0 v3.2 pre (`S0_V32_CACHE.json`). The test's data match its fingerprint, so it is reused.

## 1. The build-rate tables with R2 and R7 (once per VM; S0 v3.2 final uses them too)

```bat
cd <wt>\build_rate
"<ic-pipeline python>" -m brc.cli run --outputs outputs_v32 --set near_term.handover.mode=linear ^
    --set near_term.construction_completion_source=lbnl_post_ia
cd ..
```
- **Inputs:** the same raw files as `build_rate/outputs`: `data/raw/august_generator2026.xlsx` and `queued_up.xlsx`.
- **Check:** `build_rate\outputs_v32\run_info.yaml` has `flags: {handover: linear, construction_completion:
  lbnl_post_ia}` and the eight levels (low … high_ipm). The case build stops if the flags differ.
- `build_rate/outputs*` is gitignored. Every worktree that builds a v3.2 case needs `outputs_v32`: copy the folder or
  re-run the command.

## 2. The isolation patch (not committed)

```bat
git apply s0_workflow\data\v32\br_twostage_isolation.patch
git diff --stat
```
The only change must be `pg/settings/s0_production.yml`, 1 line: `load_hydro: "off"`.

## 3. Build 2028 and 2030

```bat
"<switch-pg-reeds-fedpol python>" pg_to_switch.py pg/settings switch/in/s0prod_A_v32brtest --case-id S0prod_A ^
    --year 2028 --year 2030
```

## 4. Pre-solve checks (both stages, `switch\in\s0prod_A_v32brtest\<year>\S0prod_A\`)

1. **Only the build-rate files differ from S0 v3.2 pre.**
   ```bat
   diff -rq switch\in\s0prod_A_v32\<year>\S0prod_A switch\in\s0prod_A_v32brtest\<year>\S0prod_A
   ```
   Expected differences, and nothing else:
   - `build_rate_groups.csv`, `build_rate_periods.csv`, `build_rate_tiers.csv`, `build_rate_gens.csv` (if any) and the
     new `build_rate_options.csv`;
   - the new `state_nuclear_bans.csv` ("none offered in this stage");
   - `s0_production_log.txt` and `powergenome_provenance.json`.
   **STOP** if any other file differs. Report the first one.
2. **The options:** `build_rate_options.csv` has `br_ramp_base` `max_build_r`, `br_stage_r` `max_data_prev_build` and
   `br_prev_build_divisor` 1.3.
3. **R4:** `build_rate_groups.csv` has `br_ramp_floor_mw` **4800** for wind_onshore, solar and storage (pre: 1000).
4. **R2 and R7:** `build_rate_periods.csv` `br_rate_data_mw`, national, within 1% of the table below
   (`s0_workflow/data/build_rate_v32/stage_r.csv`, column `r2r7`):

   | Group | 2028 (pre → test) | 2030 (pre → test) |
   |---|---|---|
   | solar | 37,087 → **36,289** (R7) | 26,873 → **30,414** (R2) |
   | storage | 23,738 → **23,685** | 11,557 → **16,615** (R2) |
   | wind_onshore | 8,277 → **8,250** | 8,250 → **8,250** |

## 5. Solve both stages

Solve with the same chain runner and solver arguments as S0 v3.2 pre, on `switch\in\s0prod_A_v32brtest\scenarios_S0prod_A.txt`
(or its `.fixed.txt` if recipe C's alias step applies). Report wall time and peak memory per stage.

## 6. Pass criteria (all must hold; report a table of pre against test per stage and group)

1. **Both stages complete:**
   - `python s0_workflow\scripts\resume_chain.py status <scen>` shows both COMPLETE;
   - the solve logs say `optimal`;
   - 2030's inputs have `build_rate_prev_build.chained.S0prod_A.csv` with the R1/R11 columns
     `br_prev_r_data_mw_per_yr` and `br_prev_stage_build_mw_per_yr`.
2. **R1 removes the wind lock-in:** in 2030's `build_rate_duals.csv`, wind_onshore has **no `ramp` row**, or one with
   dual 0. R1 skips the ramp when the data path already allows R. Report pre's 2030 wind ramp dual next to it.
3. **R11:** `build_rate_new_build.csv` has `rate_cap_mw_per_yr` ≥ `rate_data_mw_per_yr` in every row.
4. **Only relaxations, apart from R7:**
   - 2030 new build per year (`build_rate_new_build.csv` `new_build_mw_per_yr`) for wind_onshore, solar and storage is
     ≥ pre's, less 1% for solver noise;
   - 2028 solar may fall by at most 2.2% (R7). 2028 wind and storage are ≥ pre's, less 1%.
5. **No new slack:** `slack_mw` is 0 wherever pre's is 0.

**Report** for each stage and group: R, the cap, new build (pre → test), and the ramp dual (pre → test); also total
cost (`total_cost.txt`) and the 2030 wind build.

## 7. After the test

- **Undo the patch:** `git checkout pg/settings/s0_production.yml`, so `git status` is clean. The test root stays as a
  record.
- **If every criterion holds:** run S0 v3.2 final (`s0_v32.md`).
- **If any criterion fails:** STOP and report. Don't run S0 v3.2 final or the scenarios.
