# RESUME: the S0 v3.2 chain after the VM shut down (14 Oct 2026; CHANGES §243)

**For a fresh runner session with no memory of the last one.** Read all of this before acting.

## What is known (Tom, from the last runner report)

- **The build:** the S0 v3.2 build (`s0_v32.md`, S0prod_A, five stages) finished cleanly at **02:40 UTC on 14 Oct**,
  at **4894d6a**. All 88 pre-solve checks passed.
- **The chain:** it started at **02:42 UTC**, with all five stages (2028, 2030, 2035, 2040, 2045) and no reuse. It was
  due to finish at about 12:30 UTC.
- **The shutdown:** the VM shut down overnight. **How many stages solved is unknown.**
- **The inputs:**
  - The built inputs were checked at 02:40 and **must not be rebuilt**. Don't run `pg_to_switch.py` for S0prod_A.
  - The chained inputs (`*.chained.S0prod_A.csv`) are written by each solved stage for the next one; they aren't built
    inputs.

## The commit

- **This card's commit:** `READY_S0_V32` names it, the same as `READY_SCENARIOS`.
- **What differs from 4894d6a:** only the files the reuse code check exempts (`s0_workflow/scripts/`, tests, run cards,
  `*.md`). The build code and the Switch modules are 4894d6a's, so the stages solved before the shutdown and the
  stages solved after it run the same model.
- **Check it:**
  ```bat
  git diff --stat 4894d6a <READY commit> -- . ":!s0_workflow/scripts" ":!s0_workflow/tests" ":!s0_workflow/runcards" ":!*.md"
  ```
  It must print nothing. **STOP** if it prints anything.
- **Move the S0 v3.2 worktree to it (a checkout, not a build):**
  ```bat
  git -C <wt-v32> fetch origin tom/s0-v3.2
  git -C <wt-v32> checkout --detach <READY commit>
  ```
- **A READY re-issue for this card is not a rebuild.** Don't rerun recipe L or the `s0_v32.md` build. The 02:40
  inputs, the cluster cache and the split folders stay as they are.

## 1. Assess the state (read only)

Run from `<wt-v32>`, with `PYTHONPATH=<wt-v32>\PowerGenome` as in `s0_v32.md`.

- **The scenarios file the chain ran on:**
  - `switch\in\s0prod_A_v32\scenarios_S0prod_A.fixed.txt` if recipe C's alias step made one;
  - otherwise `switch\in\s0prod_A_v32\scenarios_S0prod_A.txt`.
  - Your chain runner's log or command line shows which. Use the same file below; call it `<scen>`.
- **Each stage's state:**
  ```bat
  python s0_workflow\scripts\resume_chain.py status <scen>
  ```
  It prints one line per stage: COMPLETE, PARTIAL (with the reasons) or NOT_STARTED.
- **The resume plan, without changing anything:**
  ```bat
  python s0_workflow\scripts\resume_chain.py resume <scen> --inputs-not-after 2026-10-14T02:42Z --dry-run
  ```
  It prints the stage to start at and the partial files it would move. **STOP** if it reports `built input files
  changed after`: an input the build wrote changed after the chain started.
- **Report all of the above,** plus:
  - the newest file in `switch\out\s0prod_A_v32` and its time;
  - whether Gurobi or Switch processes survived (they shouldn't, after a shutdown).

## 2. Verify each completed stage

`status` calls a stage COMPLETE only if all of these hold. For each stage it calls COMPLETE, confirm a–c by hand too.

**a. The solve finished, in `switch\out\s0prod_A_v32\<year>\S0prod_A\`:**
- `model_config.json`: Switch writes it once the solver returns, and an infeasible solve raises before it;
- `total_cost.txt` holding a number;
- `BuildGen.csv`, `BuildStorageEnergy.csv`, `SuspendGen.csv` and `BuildTx.csv`.

**b. The solver status:** that stage's solve log (your chain runner's or `measure_run.py`'s log) has `Optimization
termination condition was optimal.` and then `Switch completed successfully`. Report the log path.

**c. The handover to the next stage** (not for 2045):
- The next stage's inputs folder has every `*.chained.S0prod_A.csv` file its scenario line aliases. They are written
  after this stage's `total_cost.txt`.
- They come from `prepare_next_stage`, the last post-solve step, and carry this stage forward: built and retired
  capacity (`gen_build_predetermined`), costs (`gen_build_costs`), transmission (`transmission_lines`,
  `trans_built_to_date`, forced-line minimums), interconnection headroom (`ic_zones`, `ic_tranches`, `ic_uprates`) and
  the build-rate history (`build_rate_prev_build`).
- The build-rate history comes from the solved model's own values and can't be rebuilt from outputs. **A stage whose
  handover is missing or stale is solved again.**

**d. Not complete:** a stage whose solve finished but whose post-solve did not (e.g. `total_cost.txt` written, no
handover) shows as PARTIAL, and the resume starts there.

## 3. Clear the partial outputs (moved, never deleted)

These come from the interrupted run:
- the outputs of the stage to resume at and of every later stage;
- the chained inputs of the stages after it.

Restarting the chain writes all of them again. The resume stage's own chained inputs come from its complete
predecessor and are kept, as are the built inputs and every complete stage's outputs.

```bat
python s0_workflow\scripts\resume_chain.py resume <scen> --inputs-not-after 2026-10-14T02:42Z --move-partial
```

- **What it moves:** those files go to `switch\out\s0prod_A_v32\_resume_partial_<UTC time>\{outputs,inputs}\<year>\`.
  Report the folder and the number of files.
- **What it writes:** `scenarios_S0prod_A[.fixed].from_<year>.txt`, holding the scenario lines from the resume stage
  on, and a `resume` row in `switch\out\s0prod_A_v32\handoff_runs.csv`.
- **It refuses:**
  - to start at a stage whose predecessor isn't COMPLETE (`--from-stage` included);
  - while partial files are in place without `--move-partial`;
  - and with `--inputs-not-after`, if any built input changed after the chain started.
- **Also clear:** Gurobi's leftover temporary files in the solver `--tempdir` (e.g. `D:\tmp`), if any. They are not
  results.
- **If every stage is COMPLETE,** it prints "nothing to resume". Go to section 5.

## 4. Resume

Run the chain runner on the remaining lines, with the same solver arguments as at 02:42. The full lines are in
`s0_v32.md` section 5 and recipe C.
- **If it takes a scenarios file:** give it `scenarios_S0prod_A[.fixed].from_<year>.txt` printed above.
- **If it has a start-stage option:** start it at that year on the original `<scen>`, with no other change.
- **Run nothing else:** no `pg_to_switch.py`, no recipe L, no cache moves.
- **Report:**
  - the resume stage;
  - per stage solved: wall time, peak memory and the termination line.

## 5. When all five stages are COMPLETE

1. `python s0_workflow\scripts\resume_chain.py status <scen>` shows five COMPLETE lines.
2. Record the reference (as `s0_v32.md` section 5; the code is 4894d6a's for every stage):
   ```bat
   python s0_workflow/scripts/reuse_chain_stages.py record <scen> --git-head 4894d6a48febdfd8d458fad2d72a304918c1d478 ^
       --solver-args "<the exact solver string>" --solver-version "gurobi <x.y.z>"
   ```
3. The rest of `s0_v32.md` section 5: credits paid, and the report (per stage: cost, emissions, new GW against S0
   v3.1, the `vcf_fidelity` summary, the offshore groups and limits, and the day-selection swaps).

## 6. After S0 v3.2: the READY_SCENARIOS queue

`READY_SCENARIOS` lists the seven scenario cards at the same commit as this card. Run them in this order, each by its
own card:

| # | Card | Notes |
|---|---|---|
| 1 | `bill_central.md` | reuses S0 v3.2's 2028 and 2030 |
| 2 | `txonly.md` | reuses 2028 and 2030 |
| 3 | `s1.md` | reuses 2028 |
| 4 | `s4.md` | solves all five |
| 5 | `s2.md` | solves all five |
| 6 | `s5.md` | solves all five |
| 7 | `s3.md` | solves all five |

- Each card builds its own case at the `READY_SCENARIOS` commit, then runs `reuse_chain_stages.py reuse` against
  S0 v3.2's recorded chain with `--code-check model`.
- The reference's head is 4894d6a. The commit differs from it only in exempt files, so the check passes.
- **If a scenario chain is interrupted the same way,** resume it with this card's sections 1–4 on that case's
  scenarios file (`switch\in\<root>\scenarios_<case>[.fixed].txt`). Its `--inputs-not-after` is the time that chain
  started.

**Report and STOP** (don't improvise) if any of these happens:
- a `status`, `resume` or `git diff` result above isn't what this card says;
- a COMPLETE stage's solve log isn't `optimal`;
- a resumed stage fails.
