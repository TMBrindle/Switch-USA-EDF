# Testing methodology for new Switch/pg_to_switch functionality

Generalized guidance distilled from smoke-testing `tom/edf-baseline` + `tom/carbon-caps` +
`tom/regional-wind-caps` against `origin/edf-baseline` (see `edf_baseline_feature_test_plan.md` /
`edf_baseline_feature_test_results.md` for that specific pass). Use this as a checklist for validating any
new study module, pg_to_switch feature, or scenario_inputs.csv column before opening a PR.

## 1. Isolate test artifacts, but respect existing path assumptions

Build inputs and solve outputs into dedicated directories so a test pass can't clobber real scenario data,
but **check whether any code hardcodes directory depth** before nesting deeper than one level. At least one
adjust script in this repo (`adjust/increase_timepoint_duration.py`) computed the project root as
`in_dir.parent.parent.parent.parent`, assuming inputs always live at exactly `.../switch/in/{year}/{case_id}`.
An extra nesting level (`switch/in/tests/{year}/{case_id}`) silently broke it. Prefer **sibling** directories
at the same depth as the real ones (`switch/in_tests/`, `switch/out_tests/`) over nesting inside them
(`switch/in/tests/`), unless you've confirmed nothing in the build pipeline assumes a fixed depth.

Failures like this can be **silently swallowed**: `pg_to_switch.py` catches non-zero exit codes from its
adjustment-script subprocesses and prints a generic `WARNING: The previous script exited with error status 1`
without surfacing the underlying exception. Don't assume "the build finished with exit code 0" means every
adjustment step actually ran — check the full build log for these warnings, and spot-check that features
controlled by adjustment scripts (e.g. a `tp_duration_hours` column that's supposed to change timepoint
resolution) actually show up in the built inputs.

## 2. Rebuild inputs fresh; don't trust old `switch/in/` folders

If a feature touches `pg_to_switch.py`'s output schema (new columns, new files, changed values), assume any
pre-existing `switch/in/{year}/{case_id}/` folder was built before the change landed and is stale. Compare
file mtimes against the module/script mtimes, or just rebuild. A stale-schema input silently missing a new
file (e.g. `carbon_policies_ccr.csv`) won't necessarily error — the model may build and solve with the old
behavior, giving false confidence.

## 3. Solve with the same rigor real runs use

- Match the project's standing solver conventions (e.g. this repo's RGGI run scripts always force
  `--solver-options-string "crossover=1"` when a feature's outputs depend on duals/shadow prices — a
  barrier-only solve, the `options.txt` default, doesn't reach an exact vertex and can leave small
  constraint violations that look like bugs but are just solver tolerance).
- If comparing a fresh feature's output value against a hard constraint bound, note which solve mode
  produced it. A ~0.05% overshoot on a barrier-only (`crossover=0`) solve is expected numerical slack, not
  evidence the constraint logic is wrong — re-verify with crossover on if the comparison matters.
- Set `TEMP`/`TMP` to a drive with space before solving (`D:/tmp` in this environment) — Gurobi/AMPL scratch
  files can be large.

## 4. Prove constraints have teeth, not just that the model solves

"The solve completed" is necessary but not sufficient evidence a new constraint works. Check that the
constraint's actual value is at or near its bound for at least one binding case (e.g. `actual_annual_ratio
== min_annual_ratio` exactly, not just `>=` with huge slack) — that confirms the constraint is shaping the
solution, not silently absent or unreachable due to a data-loading bug. Cross-check the solve log for
"skipped" / "not found" warnings the module itself might print when its input file is missing or malformed.

## 5. When a solve is infeasible, isolate before assuming the new feature is at fault

1. **Disambiguate infeasible vs. unbounded**: re-solve with `--solver-options-string "... DualReductions=0"`.
   Gurobi's presolve often reports the ambiguous `infeasibleOrUnbounded` by default.
2. **Bisect by module**: re-solve with `--exclude-module <the new module>` (repeatable flag, or space-separated
   after one flag) and see if the infeasibility persists. If it does, the new feature isn't the cause —
   don't spend more time on it in that PR's scope.
3. **Built-in diagnostics have limits**: `switch_model.balancing.diagnose_infeasibility` (add via
   `--include-module`) can pinpoint violated constraints, but it can itself crash on unrelated pre-existing
   data issues (encountered here: a `KeyError` from an invalid predetermined build-year index unrelated to
   what was being tested). `--suffixes iis` requires solver support that isn't available through this
   project's `--solver-io lp` interface (`GUROBI solver plugin cannot extract solution suffix=iis`). Don't
   assume these tools will always work; have a manual bisection plan ready.
4. **Check whether the same case/year combo ever solved before**, via existing `switch/out/` folders. If an
   old (stale-schema) build of the same case at a *different* year solved fine, that's a strong signal the
   infeasibility is year- or dataset-specific rather than caused by any code change.
5. **Document what you ruled out**, even if you can't find the root cause in the time budgeted. "Not caused
   by X or Y, still open" is a useful, honest result — better than silently picking a different test case
   without saying why the original one didn't work.

## 6. Write small, throwaway checker scripts against output CSVs

Prefer a short Python script reading the relevant output CSV(s) and asserting pass/fail per row over manual
eyeballing — it's reusable if the test needs to be re-run after a fix, and it forces you to state the actual
numeric criteria (e.g. `built_mw <= cap_mw + tolerance`) rather than "looks about right." Keep these in a
dedicated `switch/test_scripts/` (or similar) directory alongside the test docs.

## 7. Parallelize independent build/solve steps

`pg_to_switch.py` builds and `switch solve` runs are both long-running (minutes each). When testing multiple
independent features/cases, kick off builds for unrelated cases in the background while a solve for another
case is running — there's no need to serialize work that doesn't depend on each other.

## 8. Record findings even when they're not what you went looking for

A test pass aimed at validating specific new functionality will often turn up unrelated bugs (path-depth
assumptions, mixed-type sort crashes, pre-existing infeasible datasets). Document these clearly and
separately from the pass/fail verdict on the feature you were actually testing — conflating "the feature I
was testing is broken" with "I found an unrelated bug while testing" makes both harder to act on.
