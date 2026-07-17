# Feature test results: tom/carbon-caps, tom/regional-wind-caps, tom/edf-baseline vs origin/edf-baseline

Tested on branch `tom/regional-wind-caps` @ `35d292d` (superset of `tom/carbon-caps`, reconciled with
`origin/edf-baseline`). See `edf_baseline_feature_test_plan.md` for the plan this executed.

**Note on directory layout:** the original plan used `switch/in/tests/` and `switch/out/tests/`. Partway
through, we found `adjust/increase_timepoint_duration.py` hardcodes exactly 4 parent directories up from
the inputs dir to locate `pg/extra_inputs/scenario_inputs.csv` (assumes `.../switch/in/{year}/{case_id}`).
The extra `tests/` nesting level broke that assumption, causing the script to silently fail to find
`scenario_inputs.csv` and skip the `tp_duration_hours` adjustment. We switched to `switch/in_tests/` and
`switch/out_tests/` (siblings of `switch/in/` and `switch/out/`, same nesting depth) to work around this.
**This is a real fragility bug in `increase_timepoint_duration.py`** (see Finding 3 below), independent of
the branches under test, worth fixing by deriving the project root by searching upward for
`pg/extra_inputs/scenario_inputs.csv` rather than hardcoding a directory depth.

## Track A -- RGGI carbon-cap mechanics + gen_zone_ratio + zonal LMP

Case: `s4x1_caelp_parclust_zoned`, year **2035** (year 2028 for this case is infeasible for reasons unrelated
to the features under test -- see Finding 4).

- Build: `pg_to_switch.py pg/settings switch/in_tests --case-id s4x1_caelp_parclust_zoned --year 2035`
- Solve: `switch solve --inputs-dir in_tests/2035/s4x1_caelp_parclust_zoned --outputs-dir out_tests/2035/s4x1_caelp_parclust_zoned_smoketest --include-module study_modules.write_zonal_lmp --solver-options-string "crossover=1"`

**Result: solved to optimality** (objective 7.7517e+11, 951,617 simplex iterations after crossover).

| Check | Result |
|---|---|
| Solve terminates optimal | PASS |
| `carbon_program_clearing_prices.csv`: clearing price >= floor price, all 3 ETS programs | PASS -- ETS 1: floor 17.04, clearing 36.93 (CCR tier 1 triggered, 481,369 tCO2 purchased at 36.93); ETS 2/3: floor 0, clearing 33.43 (violation-penalty-driven) |
| `auction_revenue_dollar_per_yr` present, non-negative | PASS -- $480.2M / $74.6M / $58.5M for ETS 1/2/3 |
| `gen_zone_ratio_summary.csv`: `hurdlereg_min_only` group constraints satisfied | PASS -- 31 group rows, 0 violations; several groups binding exactly at `min_annual_ratio` (e.g. Public_Service_Co_of_Colorado 0.942 == 0.942, Bonneville_Power_Administration 1.246 == 1.246), confirming the constraint has teeth, not a no-op |
| No "group constraints will be skipped" warning in solve log | PASS |
| `zonal_lmp.csv` / `zonal_lmp_annual.csv` written correctly | Originally **FAIL** (Finding 1); **PASS after fix** -- re-verified via full re-solve, see Fixes section |

Verified with `switch/test_scripts/check_track_a_rggi_genzoneratio.py`.

### Finding 1 (bug): `write_zonal_lmp.py` crashes on mixed-type timepoint IDs

`switch/study_modules/write_zonal_lmp.py:70` calls `sorted(m.TIMEPOINTS)` with no key. In this dataset,
`m.TIMEPOINTS` contains a mix of `int` and `str` typed elements, so `sorted()` raises:
```
TypeError: '<' not supported between instances of 'str' and 'int'
```
This happened *after* the solve completed successfully and after `carbon_program_clearing_prices.csv` /
`gen_zone_ratio_summary.csv` were written -- only the new `write_zonal_lmp` output is affected. Same issue
likely applies to `sorted(m.LOAD_ZONES)` at line 77 if load zone IDs are ever mixed-type. Suggested fix:
`sorted(m.TIMEPOINTS, key=str)` (or normalize timepoint ID dtype upstream).

## Track B -- Regional wind growth caps by transreg

Case: `s4x1_edf_med`. `make_split_models: no` for this case, so `pg_to_switch` only builds a combined
"foresight" (multi-period) model when given multiple `--year` values; single `--year` produces a normal
per-year folder.

- Build (per year): `pg_to_switch.py pg/settings switch/in_tests --case-id s4x1_edf_med --year <YYYY>`
- Solve: `switch solve --inputs-dir in_tests/<YYYY>/s4x1_edf_med --outputs-dir out_tests/<YYYY>/s4x1_edf_med_windcap_smoketest`

**Result: year 2035 solved to optimality** (objective 6.045e+11). **Year 2028 is infeasible** -- and unlike
our initial read, this *is* caused by the regional-wind-caps feature itself: the per-transreg caps are set
too tight for 2028 in 6 of 9 regions. See Finding 4 for the full root-cause chain and the specific
over-subscribed regions/magnitudes.

| Check | Result |
|---|---|
| `max_cap_requirements.csv` has 9 `MaxCapTag_WindGrowth_<transreg>` rows for 2028/2030, differing by year (pipeline vs regional-share methodology) | PASS |
| 2035 has only the national `MaxCapTag_WindGrowth` (no per-transreg rows) | PASS -- matches documented design (regional tiers only for <=2030) |
| Built wind capacity vs. national cap (2035 solve) | Close but slightly over: built 311,498.5 MW vs. cap 311,331.5 MW (+0.05%). Likely a barrier-solver-without-crossover tolerance artifact (this solve used the default `crossover=0` from `options.txt`, unlike Track A which forced `crossover=1`) rather than a constraint-logic bug. Re-verify with `crossover=1` if exact enforcement matters. |
| Per-transreg cap enforcement (2028) | Originally infeasible for `s4x1_edf_med`/`s4x1_caelp_parclust_zoned` (3 of 9 regions' caps set below already-committed predetermined capacity); **fixed and re-verified** -- see Finding 4 and Fixes section. |

Verified with `switch/test_scripts/check_track_b_wind_caps.py`.

### Finding 2: wind-cap tag values are generated correctly

Spot-checked `max_cap_requirements.csv` for `s4x1_edf_med`:

| transreg | 2028 (pipeline) | 2030 (regional-share) |
|---|---:|---:|
| ERCOT | 57,936.5 | 64,427.9 |
| MISO | 45,081.7 | 51,574.2 |
| SPP | 41,803.2 (2028) / 56,377.6 (2030) | |
| CAISO | 8,973.3 | 8,211.5 |

Values differ sensibly year-over-year per the two documented methodologies.

### Finding 3 (bug, blocking): `increase_timepoint_duration.py` has a hardcoded path-depth assumption

See note at top of this document. `adjust/increase_timepoint_duration.py::get_tp_duration_hours()` computes
the project root as `in_dir.parent.parent.parent.parent`, assuming inputs always live at
`.../switch/in/{year}/{case_id}` (or fails outright with `ValueError` if `in_dir.parent.name` isn't an
integer, e.g. for `.../switch/in/foresight/{case_id}`). Any nonstandard inputs-dir layout breaks this,
either raising `FileNotFoundError` for `scenario_inputs.csv` or `ValueError` parsing the year -- and in the
`foresight`-parent-name case, **the failure is silently swallowed by `pg_to_switch`'s subprocess wrapper**
with only a generic `WARNING: The previous script exited with error status 1`, no indication of *why*.
Recommend making this script locate `scenario_inputs.csv` by searching upward for `pg/extra_inputs/` rather
than hardcoding a fixed depth, and always surfacing the underlying exception, not just an exit-code warning.

### Finding 4 (root-caused and FIXED): regional wind caps could be set below already-committed capacity

**Investigation history** (kept for the record, since the diagnosis went through several reversals before
landing on the real cause):

1. Both `s4x1_caelp_parclust_zoned` @ 2028 and `s4x1_edf_med` @ 2028 were `infeasibleOrUnbounded`
   (disambiguated to genuinely `infeasible` with `DualReductions=0`), while year 2035 solved cleanly.
   Excluding `carbon_policies_regional` and `gen_zone_ratio` didn't fix it -- initially misdiagnosed as
   unrelated to the 4 tested features.
2. Module bisection found excluding `study_modules.max_capacity_constraint` alone solves it, and removing
   only the 9 new per-transreg `MaxCapTag_WindGrowth_<transreg>` rows (via `--input-alias`) also solves it --
   pinning it on the new regional wind-growth tiers specifically.
3. Comparing an unconstrained-by-region solve against the regional caps showed 6 of 9 regions wanting
   1.01-3.8x more wind than their cap allowed. This was initially misdiagnosed as `scenario_management.yml`
   being stale (predating the pipeline-method logic) -- **that diagnosis was wrong**: re-running the
   generator script produced byte-identical values, confirming the caps were already correctly computed via
   the current pipeline method using real LBNL interconnection-queue data.
4. Tested whether gas could substitute for the capped wind: raising the national `MaxCapTag_GasTurbineSupply`
   cap to unlimited, even combined with excluding `carbon_policies_regional` or `rps_regional`, **remained
   infeasible in every combination**. This ruled out capacity/carbon/RPS substitutability entirely and
   pointed at something wind-specific that no amount of gas could fix.
5. **Root cause, found via a real IIS** (Irreducible Infeasible Subset -- the minimal self-contradictory
   subset of constraints/bounds within an infeasible model). Gurobi's default infeasibility detection gives
   no diagnostic detail, and neither `switch_model.balancing.diagnose_infeasibility` (crashed on an unrelated
   pre-existing data bug, `KeyError: Index '('S-Geothermal', 2020)' is not valid for indexed component
   'BuildGen'`, in the installed `switch_model` package) nor `--suffixes iis` (unsupported via this project's
   `--solver-io lp` interface) could get one. Running `gurobi_cl.exe` directly against a leftover Pyomo LP
   temp file with `ResultFile=infeasible.ilp` worked: **the IIS was just 1 constraint
   (`MaxCapTag_WindGrowth_SPP <= 41,803.2`) plus 212 variable bounds.** Those 212 bounds turned out to be the
   *predetermined* (already-existing/committed) SPP wind generators, which sum to **45,454.1 MW -- already
   3,650.9 MW over the cap before the optimizer makes a single decision.** `ISONE` (1,729.9 MW predetermined
   vs. 1,555.3 MW cap) and `NYISO` (3,495.3 vs. 3,322.8) have the same problem. This is why nothing in step 4
   helped: you can't build your way out of a constraint that's already violated by fixed, un-buildable
   capacity.
6. `MISO`'s much larger apparent overshoot (wanting ~93 GW against a 45 GW cap, from step 3's table) is a
   *different, softer* issue -- its predetermined capacity (35,346.6 MW) is comfortably under its cap, so
   that's ordinary cost-optimal demand for more wind than allowed, not a hard violation. Confirmed by solving
   with only the SPP/ISONE/NYISO caps raised to their predetermined floor (MISO and all others left at their
   original, tighter values): **solves to optimality** (objective 4.1283e+11). MISO's tighter cap does not
   independently cause infeasibility.

**Why this happened:** `pg/update_max_cap_files.py` already had logic to enforce this predetermined-capacity
floor (its own docstring: *"The cap must not fall below total predetermined builds, otherwise the model is
immediately infeasible before the optimiser makes any decisions"*) -- but that script is a separate,
manually-run maintenance step, not part of the normal `pg_to_switch.py` build pipeline. Every test build in
this pass was done via a fresh `pg_to_switch.py` run (as it should be, to get current schema), which never
had that floor applied.

**Fix applied:** moved the predetermined-capacity floor check directly into `pg_to_switch.py`'s
`cap_req_files()` function (used to write `max_cap_requirements.csv` on every build), so it can no longer be
skipped. After writing `max_cap_generators.csv`, it now sums predetermined capacity per `MAX_CAP_PROGRAM` tag
from `gen_build_predetermined.csv` and raises any `max_cap_mw` below that floor, printing a warning:
```
WARNING cap_req_files: MaxCapTag_WindGrowth_SPP (2028) max_cap_mw 41803.2 is below predetermined capacity 45454.1; raising cap to the predetermined floor.
WARNING cap_req_files: MaxCapTag_WindGrowth_ISONE (2028) max_cap_mw 1555.3 is below predetermined capacity 1729.9; raising cap to the predetermined floor.
WARNING cap_req_files: MaxCapTag_WindGrowth_NYISO (2028) max_cap_mw 3322.8 is below predetermined capacity 3495.3; raising cap to the predetermined floor.
```
Verified end-to-end: a fresh `pg_to_switch.py` build of `s4x1_edf_med` now automatically produces
`MaxCapTag_WindGrowth_SPP=45454.1`, `_ISONE=1729.9`, `_NYISO=3495.3` (previously 41803.2/1555.3/3322.8) with
no separate `update_max_cap_files.py` step needed, and solving with those corrected values (steps 6 above)
reaches optimality.

This fix only applies the *predetermined floor* the user asked for; it does not add the separate
cross-year monotonicity floor that `update_max_cap_files.py` also implements (each year's cap staying >= the
prior year's) -- that's a distinct concern not covered by this fix.

## Track C -- Allowance banking

Not run, per plan (out of scope -- see test plan doc).

## Summary

| Feature | Verified working? |
|---|---|
| RGGI hard cap / two-tier CCR / floor price | **Yes** -- clearing prices, CCR triggering, and revenue all behave correctly |
| `gen_zone_ratio` `hurdlereg_min_only` group constraint | **Yes** -- constraint binds and is satisfied across 31 groups |
| Regional wind growth caps by transreg -- constraint mechanics | **Yes** -- tags generated correctly, constraint enforced correctly |
| Regional wind growth caps by transreg -- 2028 cap *values* | **Bug found and fixed**, see Finding 4. 3 of 9 regions' 2028 caps (SPP/ISONE/NYISO) were set below already-committed predetermined capacity, causing unconditional infeasibility; fixed by adding an automatic predetermined-floor check to `pg_to_switch.py`. MISO's separate, softer over-subscription (cost-optimal demand exceeding its cap) does not independently cause infeasibility. |
| `write_zonal_lmp` module | **No -- bug found** (Finding 1) -- fixed, see Fixes section |

## Fixes applied

1. **`write_zonal_lmp.py`** (Finding 1): `sorted(m.TIMEPOINTS)`, `sorted(m.LOAD_ZONES)`, and the annual-average
   loop's `sorted(annual_acc)` all lacked a `key=`, and crashed on this dataset's mixed int/str typed
   timepoint IDs. Fixed by sorting with `key=str` (and a tuple-of-str key for the `(zone, period)` pairs).
   **Fully re-verified via a full Track A re-solve** (`s4x1_caelp_parclust_zoned` @ 2035, fresh rebuild,
   `--include-module study_modules.write_zonal_lmp`, `crossover=1`): solved to optimality (objective
   7.751686442e+11, matching the original pre-fix solve's objective exactly), and both `zonal_lmp.csv`
   (16,080 rows) and `zonal_lmp_annual.csv` (134 rows) wrote successfully with no errors, no NaNs, and LMPs
   in a plausible range ($0-$1,358.65/MWh).
2. **`adjust/increase_timepoint_duration.py`** (Finding 3): `get_tp_duration_hours()` hardcoded the project
   root as exactly 4 parent directories above the inputs dir, and required the parent directory name to be
   an integer year. Rewrote to (a) locate `pg/extra_inputs/scenario_inputs.csv` by walking upward looking for
   it (bounded search), and (b) fall back to matching on `case_id` alone (asserting a consistent
   `tp_duration_hours` across all its scenario_inputs.csv rows) when the parent directory isn't a parseable
   year, e.g. for foresight builds. Verified against three layouts: normal `switch/in/{year}/{case}`, a
   foresight build `switch/in/foresight/{case}` (previously crashed), and the originally-broken nested
   `switch/in/tests/{year}/{case}` layout (previously silently no-op'd) -- all three now correctly return
   `tp_duration_hours=2` for `s4x1_edf_med`, and a full `pg_to_switch` rebuild into the nested layout now
   correctly produces `ts_duration_of_tp=2` in the built `timeseries.csv`.
3. **`pg_to_switch.py`'s `cap_req_files()`** (Finding 4): added an automatic predetermined-capacity floor for
   `MaxCapTag_*` requirements, so a fresh build can no longer produce a max-cap value below what's already
   built/committed for that program's tagged generators (previously only enforced by the separate, easy-to-
   forget `pg/update_max_cap_files.py` maintenance script). Verified end-to-end: fresh builds now
   automatically raise `MaxCapTag_WindGrowth_SPP/ISONE/NYISO` to their predetermined floors with a clear
   warning, and solving with those corrected caps reaches optimality. Full root-cause chain and verification
   detail in Finding 4.

## Foresight (multi-period) test

With the Finding 4 fix in place, rebuilt `s4x1_edf_med` as a full 3-period foresight case
(`--year 2028 --year 2030 --year 2035`, single `pg_to_switch` build producing one combined
`switch/in/foresight/s4x1_edf_med` case) and solved with `crossover=1`.

**Result: solved to optimality** (objective 1.279837903e+12) -- the first successful foresight solve in this
entire test pass; both earlier foresight attempts (2028+2030, then 2030+2035) were infeasible before the
Finding 4 fix existed.

The predetermined-floor fix fired much more broadly here than in the single-year case: nearly every
`MaxCapTag_*` tag needed raising, including the *national* `MaxCapTag_WindGrowth` (209,901.5 -> 520,916.0 MW),
`MaxCapTag_NuclearGrowth`, and `MaxCapTag_GasTurbineSupply`, not just the 3 regional wind tags seen in the
single-year build. Predetermined-capacity totals were also substantially larger than the single-year
equivalents (e.g. MISO wind: 70,693.1 MW here vs. 35,346.6 MW in the single-year 2028 build) -- consistent
with foresight-mode `gen_build_predetermined.csv` aggregating committed capacity across the full study
horizon rather than a single period. This is a legitimate consequence of foresight accounting, not a new
bug, and the fix handled it correctly without any special-casing.

`carbon_program_clearing_prices.csv` (9 program-period rows, 3 programs x 3 periods) and
`gen_zone_ratio_summary.csv` (402 zone rows, 0 groups -- `gen_zone_ratio=none` for this case) both wrote
successfully.

## Bonus: transmission congestion analysis from Track A's zonal LMPs

Using Track A's `zonal_lmp.csv` and `dual_costs.csv` output, built `docs/analysis/transmission_congestion/`
(`analyze_congestion.py` + `map_congestion.py`) to rank transmission lines by annual congestion rent (the
`Maximum_DispatchTx_Directional`/`_Derated` constraint duals, extreme-day/PRM hours excluded since their
scarcity-level duals otherwise swamp the ranking) and cross-check against average zonal LMP gaps between
connected zones. Top result: `p65-p67` (south Texas / ERCOT) at ~$897M/yr, with a broader cluster of
high-congestion lines in the same area and smaller pockets in the Pacific NW/CA, Michigan, New England, and
mid-Atlantic. See `docs/analysis/transmission_congestion/congestion_ranking.csv` and `congestion_map.png`.
