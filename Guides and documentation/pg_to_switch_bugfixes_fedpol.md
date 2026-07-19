# pg_to_switch.py / PowerGenome bug fixes found during federal policy scenario build

Found and fixed while building and solving the `s4x1_fedpol_biden` / `_current` / `_reform`
scenario family (`project/Aug26_fed_policy` branch, Aug 2026). Written up so these carry over
into the `pg_to_switch.py` → `pg_build.py`/`pg_common.py`/`pg_core_build.py`/`pg_scenario_build.py`
refactor. Each entry: symptom, root cause, fix, and whether it's a core pipeline bug (needs
porting) vs. a scenario-specific data/config choice (context only).

## 1. `atb_data_year` request silently returns zero rows for retired ATB category names

**Symptom:** `ValueError: Investment variable capex costs contains nan values` in
`nrelatb.py::atb_new_generators()` → `investment_cost_calculator()`, for any `atb_new_gen`
entry using an ATB technology/tech_detail name that stopped being populated in the DB after a
given `atb_year`.

**Root cause:** `fetch_atb_costs()` (`nrelatb.py`) does a strict SQL filter
`AND atb_year == {settings['atb_data_year']}`. NREL restructured ATB's CCS technology category
*names* starting ~ATB2023 — old names (`Coal/CCS90AvgCF`, `Coal/CCS30AvgCF`,
`NaturalGas/CCCCSAvgCF`) stopped getting new rows after `atb_year=2022`, even though the DB
itself has data through `atb_year=2024`. The upstream validity check in `fetch_atb_costs()`
(`db_col_values()`) only confirms each *token* (e.g. `"Coal"`, `"Moderate"`) exists *somewhere*
in its own column — it never checks that the specific 5-way combination
(technology, tech_detail, financial_case, cost_case, atb_year) actually has rows. So a
technology+cost_case pairing can pass validation and still silently return zero rows → empty
`.mean()` → NaN.

**This is a real pipeline bug** (silent failure mode, no validation catches it) —
**relevant to the refactor**: any code path that resolves ATB technology cost data by
name should validate that the *exact* (technology, tech_detail, financial_case, cost_case,
atb_year) combination has rows, not just that each token individually exists somewhere in the
table. Consider raising a clear error (e.g. "no cost data for X/Y at atb_year=Z, closest
available years are: ...") instead of proceeding to NaN.

**Fix applied (scenario-specific, not a pipeline fix):** switched to the current ATB2024 names
(`Coal/95%-CCS`, `Coal/99%-CCS`, `NaturalGas/1-on-1 Combined Cycle (H-Frame) 95%/97% CCS`),
verified against the live DB before use. Also discovered and used: these renamed categories
come in real 95%/97%/99% capture-rate variants (not just 90%), useful if capture-rate
scenario axes are wanted elsewhere.

## 2. New technology needs an explicit `THERM`/`VRE`/etc. resource tag or GenX validation fails

**Symptom:** `ValueError: Use the warnings above to fix the resource tags in your settings
file` from `GenX.py::check_resource_tags()`, preceded by warnings like
`The resource Coal_95%-CCS_Moderate in region pXXX does not have any assigned resource tags`.

**Root cause:** `model_tag_values.THERM` in `resource_tags.yml` only listed specific
*existing-fleet* coal technology strings (e.g. "Conventional Steam Coal") — none of which are
substrings of new ATB tech_detail names like `Coal_95%-CCS`. The existing `NaturalGas_` THERM
entry already matched any new gas technology by substring, which is why only coal hit this.

**Not really a pipeline bug** — this is inherent to how `add_resource_tags()`
(case-insensitive substring match, underscores stripped) works: any *new* technology family
added to `atb_new_gen` needs a corresponding tag-matching entry. **Relevant to the refactor
only as a checklist item**: whenever a new technology is added to `atb_new_gen`, verify it has
tag coverage (a generic `Coal_: 1` / `NaturalGas_: 1` style entry, or a specific one) before
relying on it working.

**Fix applied:** added a generic `Coal_: 1` entry to `THERM`, mirroring the existing
`NaturalGas_` pattern.

## 3. `Can_Retire` flag does not stop predetermined (EIA-860m) retirements

**Symptom:** A scenario config that sets `Can_Retire: 0` for coal/gas (to represent a
retirement moratorium) still loses generating units on schedule.

**Root cause:** `Can_Retire` only gates SWITCH's *endogenous economic* retirement decision.
Predetermined retirements are baked into `Existing_Cap_MW` **upstream, inside PowerGenome**,
from each unit's EIA-860m planned-retirement date — this happens in `pg_to_switch.py`'s
`eia_build_info()` before `Can_Retire` is ever consulted, via
`build_year = retirement_year - retirement_age` / `gen_max_age = retirement_age` construction
(these are set up so `build_year + gen_max_age` reproduces `retirement_year` exactly — that's
the actual mechanism SWITCH uses to age a unit out). Blocking *only* `Can_Retire` therefore only
stops the optimizer from *choosing* early retirement — it does nothing for units already
scheduled to retire on a fixed EIA-860m date.

**Real pipeline bug / design gap — relevant to the refactor**: if the refactor wants to
support "block predetermined retirement" as a scenario-controllable feature, it needs to
touch this same insertion point (before the `build_year`/`gen_max_age` arithmetic in
`eia_build_info()`), not just `Can_Retire`. Two independent knobs are needed for a real
"retirement moratorium" scenario: one for predetermined dates (upstream, PowerGenome-side),
one for economic retirement (`Can_Retire`, SWITCH-side).

**Fix applied:** new function `apply_predetermined_retirement_override()`, called inside
`eia_build_info()` before the synthetic `build_year` is computed, shifting a unit's
`retirement_year` from a configurable window (e.g. 2026–2029, inclusive both ends) to a target
year (e.g. 2030), scoped by technology (coal-only or coal+gas). Wired to run alongside a
`Can_Retire` override so both mechanisms are active together for a "blocked retirement" config.

## 4. PowerGenome's settings-merge hard-crashes on ANY two scenario columns touching the same key — even with identical values

**Symptom:** `ValueError` from `build_scenario_settings()` in `powergenome/util.py`, whenever
two different `scenario_inputs.csv` columns' configs both set the same flattened settings key
in the same row — even if the two values are identical.

**Root cause:** After each scenario-axis column is applied to a row's settings, PowerGenome
tracks which flattened keys that column touched (in a `modified_settings` dict) and checks
every subsequent column's touched keys against it. If there's any overlap with a
*previously-touched* key, it raises immediately — **it does not compare values, and does not
let one column win**. This means two independently-designed scenario axes that happen to
*both* declare (say) the full base `atb_modifiers` block will collide, even if neither one
actually changes the underlying behavior for the technologies they don't care about.

**This is inherent PowerGenome behavior, not a bug to fix in PowerGenome** — but
**very relevant to the refactor's design**: any new scenario-axis system must ensure each
axis's config only sets the *minimal, uniquely-owned* set of keys it's actually responsible
for changing, never a full copy of a shared base block "just to be safe." (`update_dictionary()`
in PowerGenome does a real recursive deep-merge with the base settings — re-declaring the base
block defensively is unnecessary and is exactly what causes collisions with sibling axes.)
Recommend: build a small pre-flight check (flatten every axis config's keys pairwise, look for
overlaps) as part of scenario-axis authoring/testing, so this class of bug is caught before a
full PowerGenome run rather than after a ~15 minute build fails.

## 5. `pg_to_switch.py`'s `--year` omission silently expands to the year set of every case in the whole CSV, not just the selected ones

**Symptom:** `KeyError: Requested year(s) {2029} were not found in ... scenario_inputs.csv`
even though the requested `--case-id`(s) don't reference year 2029 at all, and no `--year` flag
was passed.

**Root cause (in `pg_to_switch.py::main()`):** when `--year` isn't given,
`filter_years = available_years.copy()` — but `available_years` is computed from the **entire**
`scenario_inputs.csv` (all cases), not just the `--case-id`-filtered subset. Then the code
validates that every year in that *global* set is present among rows matching the selected
case(s) — which will basically only ever pass if the selected case(s) happen to use every
year value that exists anywhere in the file.

**Real pipeline bug — relevant to the refactor**: `filter_years`'s "no `--year` given" default
should be derived from the years actually available for the *selected* `--case-id`(s), not the
whole file. Recommend fixing this in the refactor rather than porting the current behavior
forward — the workaround (always pass explicit `--year` flags) is not obvious and wastes time
debugging what looks like a data problem.

## 6. A stale `scenario_inputs.csv` row referenced a year not in `model_year`

**Symptom:** `ValueError: The year 2029 is in your scenario definition file for case X but was
not found in the 'model_year' or 'model_periods' settings parameters.`

**Root cause:** not a code bug — a pre-existing `scenario_inputs.csv` row set (the `s4x1`
template we copied from) included year 2029 in its per-case-id year list, but
`model_definition.yml`'s `model_year: [2024, 2025, 2028, 2030, 2035]` doesn't include 2029.
This looks like stale/legacy data (possibly from before `model_year` was last changed) that
nobody currently runs this way.

**Not a pipeline bug, just a data-hygiene note for the refactor**: worth a one-time audit of
`scenario_inputs.csv` for any `(case_id, year)` combination whose year isn't in the current
`model_year` list, so this doesn't surface again for someone copying an old template row.
(Also found, separately, unrelated to this: pre-existing exact-duplicate `(case_id, year)` rows
for `s4x1_caelp` at 2028/2030/2035 — `build_scenario_settings` rejects duplicate `(case_id,
year)` pairs outright, so that case would currently fail to build too, regardless of anything
above.)

## 7. `nerc_growth_pct.csv` only has rows for period *labels*, not every calendar year a period spans

**Symptom:** A scenario selecting `trans_expansion: nerc_growth` produces a numerically
unstable/divergent solve (barrier method fails to converge, objective value grows to a
nonsensical magnitude like 1e20) for reasons that don't show up as an outright infeasibility.

**Root cause:** the transmission-expansion growth-rate lookup keys off `nerc_growth_pct.csv`,
which has rows for period labels (e.g. 2028, 2030, 2035) but the model's actual investment
periods span calendar-year ranges per `periods.csv` (e.g. the "2030" period runs from
`period_start=2029` to `period_end=2030`). A lookup for calendar year 2029 (a `period_start`,
not itself a period label) found no row and silently fell back to `.get(..., 0.0)` — i.e. 0%
growth — for that year, artificially clamping transmission expansion right when the model
needed it most.

**Real pipeline bug — relevant to the refactor**: any period-vs-calendar-year rate lookup
needs to either (a) carry forward the nearest prior period's rate rather than silently
defaulting to zero, or (b) raise if a requested year has no matching data, rather than
returning a default that looks like valid data. Silent-zero fallbacks for numeric parameters
are a recurring failure pattern (see also #5 style issues) — worth grepping for
`.get(..., 0` / `.get(..., 0.0)` patterns generally during the refactor.

**Fix applied:** carry forward the nearest prior period's rate instead of defaulting to 0%.
(Not yet fully confirmed via a real solve whether this alone resolves the divergence —
verification in progress as of this writing.)

## 8. Scenario-level policy conflicts can produce a genuinely infeasible model — not a bug, but worth a design note

**Symptom:** `Model is infeasible or unbounded` / definitively infeasible (confirmed via
Gurobi IIS) for `s4x1_fedpol_current`.

**Root cause:** not a code bug. This scenario pairs `offshore_wind_policy: capped_2025`
(bans new offshore wind beyond Jan-2025-in-progress projects — a *maximum* cap) with
pre-existing state offshore wind procurement mandates enforced by
`switch/study_modules/min_capacity_constraint.py` (`MinCapTag_<STATE>_offshorewind`, a
*minimum* requirement for thousands more MW by 2035 across CA/CT/MA/MD/ME/NJ/NY/RI/VA). Min >
allowed max = infeasible, by construction — this is IIS-confirmed via Gurobi (`computeIIS()`),
not guessed.

**Not a pipeline bug — a modeling/scenario-design pattern worth knowing about for the
refactor**: `min_capacity_constraint.py`'s `Enforce_Min_Capacity` is a hard constraint with no
slack/violation variable. Any new scenario axis that imposes a *maximum* cap on a technology
should be checked against existing *minimum* requirements for that same technology before
assuming the combination is solvable — PowerGenome/pg_to_switch won't catch this at build
time, it only surfaces as a solver-level infeasibility (expensive to diagnose: this one took a
~15 minute build + Gurobi IIS computation to pin down precisely).

**Resolution applied (scenario-specific policy choice, not a code fix):** for
`offshore_wind_policy: capped_2025` specifically, the state offshore-wind `min_cap_mw` values
are zeroed out in the generated `min_cap_requirements.csv` — representing "state mandates go
unmet under a federal permitting freeze" as a deliberate scenario assumption. Other scenarios
(`biden`, `reform`, both `offshore_wind_policy: open`) are untouched; the underlying
`min_capacity_constraint.py` module itself was not modified, since other scenarios still need
it to be a hard, binding constraint.

## 9. Conda/PowerGenome environment can silently point at the wrong checkout

**Not a pg_to_switch.py bug**, but worth flagging since it wasted real debugging time: a
shared conda environment (`switch-pg-reeds`) had PowerGenome editable-installed from a
different directory entirely (a colleague's separate checkout), not this repo's `PowerGenome`
submodule. This meant early solve attempts were silently running PowerGenome package code from
the wrong location, while reading data files from the right location — producing confusing
"file not found" errors for files that clearly existed. Worth a `python -c "import
powergenome; print(powergenome.__file__)"` sanity check whenever solves behave unexpectedly
in a shared environment.
