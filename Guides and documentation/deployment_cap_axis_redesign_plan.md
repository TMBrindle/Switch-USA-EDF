# Plan: retire `constraint_removal`, fix national/regional deployment-cap release

**Status:** Implemented and verified (2026-07-26/27). All 5 steps under "Recommended fix" are done, and all 6 items under "Verification" have passed: settings-merge check confirmed each of the 5 migrated fedpol families resolves the correct `max_cap_req_fn`/`MaxCapTag_Ban`; a no-regression diff found 0 mismatches across 92 (case, year, tag) combos for non-fedpol cases under `policies: current`; dry `pg_to_switch.py` builds for `s4x1_fedpol_cost_optimal` (2028/2030/2035) and all three `*_constraintremoval` families (2028) show the exact intended release schedule; scratch build folders were deleted afterward. See the corresponding commit for the final diff.

**How to resume:** This doc is now a historical record of the investigation and design rationale, not an active TODO. The "Context" and "Scope confirmed" sections document *why* the change was made; "Recommended fix" documents what was implemented.

## Context

`s4x1_fedpol_cost_optimal` (in `pg/extra_inputs/scenario_inputs.csv`) is designed as a "cost-optimal by 2035" scenario: wind/solar deployment caps stay fully in place through 2028, the *national* wind/solar cap releases at 2030, and both the national cap **and** the 9 regional `MaxCapTag_WindGrowth_<transreg>` sub-caps release at 2035. This is currently implemented via a `constraint_removal` axis (`none`/`partial`/`full`) in `scenario_inputs.csv`, composed with `pg/settings/scenario_management.yml`.

An investigation (2026-07-20/21/26) found the national release silently never takes effect (confirmed via a dry `pg_to_switch.py` build and by directly inspecting PowerGenome's merged settings dict), while the regional release works only by coincidence. Root cause, traced via Explore agents and direct code reads:

- **The Switch model does not need period-varying tag membership.** `switch/study_modules/max_capacity_constraint.py`'s `Enforce_Max_Capacity` sums `BuildGen[g, bld_yr]` for a *fixed* generator set (`GENS_IN_MAX_CAP_PROGRAM`, loaded once from `max_cap_generators.csv`, no period column) against `max_cap_mw[program, period]` (genuinely period-indexed, from `max_cap_requirements.csv`). So a period-varying *ceiling* alone is sufficient to "release" a cap in later periods — no Switch-side schema change is needed, ever, for this problem.
- **The real bug is a settings-merge collision.** `make_emission_policies.py` writes the national `MaxCapTag_WindGrowth`/`MaxCapTag_SolarGrowth` `MaxCapReq` values into an `all_cases` block per year (confirmed at `pg/settings/scenario_management.yml:5501`, inside `2024:` — **not** under `policies: current` as first suspected). `all_cases` merges are applied unconditionally, every year, and are **not** tracked by PowerGenome's merge conflict-checker (`build_scenario_settings()` in `PowerGenome/powergenome/util.py:1092-1138` — the `all_cases` merge at lines 1102-1104 runs *before* `modified_settings = {}` is reset at line 1106). `constraint_removal` lives under `all_years`, applied in an earlier pass, so its override is silently clobbered every time by the year-specific `all_cases` merge that runs afterward. Regional sub-caps only "work" under `full` because `make_emission_policies.py` happens not to populate them past 2030 — not because the mechanism is sound.

**Design decision, discussed with repo owner:** rather than patch `constraint_removal` to avoid the collision (e.g. by duplicating overrides into year-specific blocks — viable but keeps two mechanisms fighting over the same keys and doesn't address that `constraint_removal` bundles three unrelated concerns), **retire `constraint_removal` as a distinct axis entirely.** Framing from the repo owner: "constraint removal is really a scenario, not a distinct policy variable" — it should be expressed as new *options* on the axes that already conceptually own each constraint, not a bolted-on override axis. Additionally, the deployment-cap values themselves move from inline YAML dicts to an **external CSV file per trajectory**, selected via a new settings key (`max_cap_req_fn`) — this sidesteps the dict-merge collision problem structurally (a file path is a scalar, last-write-wins with no ambiguity, unlike a deep-merged nested dict), and lets national and regional caps for a given trajectory live together in one file, open to validation, loosely mirroring (in spirit, not code) how `emission_policies_fn` already bundles a whole policy's worth of values in one file under `policies: decarb`. (Investigated directly: `emission_policies_fn` itself is a different subsystem — GenX's ESR/CO2-cap pipeline via `PowerGenome/powergenome/external_data.py:load_policy_scenarios()` — and PowerGenome's `max_cap_req()` in `GenX.py:1149-1206` has no file-based option today. This is confirmed as a new, moderate-effort addition, not a reuse of an existing PowerGenome mechanism.)

`constraint_removal`'s three previously-bundled concerns get redistributed:
1. **National + regional wind/solar growth caps** → new `max_cap_req_fn`-based CSV files, selected via new levels on the existing `policies` axis.
2. **`MaxCapTag_Ban` release** (only matters for scenarios that also set `offshore_wind_policy: capped_2025`, which tags `OffShoreWind` as banned) → new level on the existing `offshore_wind_policy` axis.
3. (Nothing else was meaningfully exercised by `constraint_removal` for any current scenario — confirmed via `grep` that only the 5 scenario families below ever select a non-`none` value.)

## Scope confirmed

Only these 5 `s4x1_fedpol_*` families (in `pg/extra_inputs/scenario_inputs.csv`) ever use a non-`none` `constraint_removal` value — every other row in the repo already uses `none` (a no-op), so this migration has no blast radius beyond fedpol:

| case_id | current `policies` | current `offshore_wind_policy` | current `constraint_removal` |
|---|---|---|---|
| `s4x1_fedpol_biden_constraintremoval` | `current` | `open` (no ban to release) | `full`, all periods |
| `s4x1_fedpol_current_constraintremoval` | `current` | `capped_2025` (bans OffShoreWind) | `full`, all periods |
| `s4x1_fedpol_reinstate_constraintremoval` | `current` | `capped_2025` (bans OffShoreWind) | `full`, all periods |
| `s4x1_fedpol_cost_optimal` | `current` | `open` (no ban to release) | `none`@2028/2029 → `partial`@2030 → `full`@2035 |

## What's already in the working tree (separate, unrelated to this plan)

As of this writing, `pg/settings/scenario_management.yml`'s `constraint_removal: partial`/`full` blocks still contain the (confirmed non-functional) `model_tag_values`-based national release attempt, plus inline comments documenting the bug. **This plan supersedes that approach entirely** — step 5 below explicitly says to delete the whole `constraint_removal` axis block. Don't try to preserve or fix the existing `model_tag_values`/`MaxCapReq` overrides in that block; they're dead ends (see Context above for why both were tried and abandoned).

## Recommended fix

### 1. Add `max_cap_req_fn` support to `pg_to_switch.py`

In `cap_req_files()` (`pg_to_switch.py:2231`), which currently loops `for model_year, scen_settings in scen_settings_dict.items(): mcr = cap_req(scen_settings)` (calling PowerGenome's inline-dict-only `max_cap_req`/`min_cap_req`): for `minmax == "max"`, before/alongside that call, check `scen_settings.get("max_cap_req_fn")`. If set, read `Path(scen_settings["input_folder"]) / scen_settings["max_cap_req_fn"]` — a CSV with columns `MAX_CAP_PROGRAM, PERIOD, max_cap_mw` (optionally `description`) — filter to `PERIOD == model_year`, and use those rows **in place of** whatever `cap_req(scen_settings)` would have produced for the same `MAX_CAP_PROGRAM` values, while still including any inline-`MaxCapReq`-derived programs *not* covered by the file (e.g. `MaxCapTag_Ban`, `MaxCapTag_NuclearGrowth`, `MaxCapTag_GasTurbineSupply` stay inline — they're unrelated to wind/solar growth scheduling and aren't part of this migration). Follow the existing `load_policy_scenarios()` pattern (`PowerGenome/powergenome/external_data.py:260-310`) for how to resolve/read the file relative to `input_folder`. Only touch the `minmax == "max"` path — `min_cap_req`/`MinCapReq` is untouched by this change.

### 2. Generate the growth-cap CSV files

New folder, e.g. `pg/extra_inputs/growth_caps/`. Three files needed initially (values sourced from direct inspection of `make_emission_policies.py`'s output during this investigation — re-derive from the live settings rather than copying old numbers verbatim, since they may have shifted by the time this is implemented):

- **`current.csv`** — reproduces today's default (unreleased) national + regional schedule for every year 2024–2050, i.e. exactly what `all_cases` currently generates. Used by `policies: current` (unchanged from today — every non-fedpol scenario keeps using this, so existing behavior for the other ~700+ rows must be provably unchanged; ideally generate this file directly from `make_emission_policies.py`'s existing computation, e.g. by adding a mode to dump its computed values to CSV instead of/in addition to writing YAML, so there's a single source of truth rather than two).
- **`uncapped.csv`** — national + all 9 regional programs set to a large ceiling (e.g. `999999999`) for every year. Used by a new `policies: current_uncapped` level (for the `*_constraintremoval` families — full release for their entire horizon).
- **`release_2035.csv`** — national + regional programs at their normal `current.csv` values through 2028, national released (`999999999`) from 2030, regional also released from 2035 (mirroring the schedule already validated during this investigation: `MaxCapTag_WindGrowth`/`MaxCapTag_SolarGrowth` released at 2030, all 9 `MaxCapTag_WindGrowth_<transreg>` released at 2035). Used by a new `policies: current_release_2035` level, selected by `s4x1_fedpol_cost_optimal` for **all** its rows (2024–2035) — the file's own `PERIOD` column carries the whole schedule, so `constraint_removal` is no longer needed for this case at all.

**Validation to add** — a national-vs-regional **max-vs-max** check is not actually meaningful (confirmed via discussion during this investigation): two `MaxCapTag_*` ceilings (national and regional) covering overlapping/nested generator sets can never conflict with each other — they're both upper bounds on the same or related sums, and building 0 always satisfies any number of upper bounds simultaneously, so misaligned max values (e.g. national tighter than the sum of regional sub-caps) just mean the tighter one binds; never infeasible on its own. Searched for an existing regional min-vs-max check (in `pg_to_switch.py`, `make_emission_policies.py`, `update_max_cap_files.py`) and found none — the only existing cross-check is the unrelated "predetermined-build floor" logic (`pg_to_switch.py:2275-2306`, raises a `MaxCapReq` ceiling to accommodate already-committed capacity, nothing to do with `MinCapTag` mandates).

The infeasibility risk that's actually real and **currently unvalidated at either the national or regional level**: a `MinCapTag_*` **minimum** (e.g. a state offshore-wind procurement mandate, or any other min-capacity requirement) exceeding the `MaxCapTag_*` **maximum** ceiling covering the same (or an overlapping) generator pool and period — that's a genuine min > max conflict on the same sum, which is infeasible. Add a check, in whatever script generates the growth-cap CSVs (extending `make_emission_policies.py` or a small sibling script), that for every period: (a) each region's `MinCapTag_<state>_offshorewind` (or other regional min mandate) does not exceed its corresponding `MaxCapTag_WindGrowth_<transreg>` ceiling for generators it overlaps with, and (b) the aggregate/relevant `MinCapTag_*` minimums do not exceed the **national** `MaxCapTag_WindGrowth`/`MaxCapTag_SolarGrowth` ceiling for the same period. Raise a clear error before files are written if violated — do not defer this to solve-time infeasibility. Confirm the exact aggregation logic (which regional mins map to which national tag, given a generator can be tagged by both a regional `MinCapTag` and the national `MaxCapTag_WindGrowth` simultaneously) with the generator-tagging logic in `regional_resource_tags.yml`/`resource_tags.yml` before implementing.

### 3. New `policies` axis levels (`pg/settings/scenario_management.yml`)

Under `policies:` (currently `current`/`decarb`, `scenario_management.yml:1369-1381`), add:

```yaml
policies:
  current:
    max_cap_req_fn: growth_caps/current.csv   # new line; carbon_cost_by_program etc. unchanged
  current_uncapped:
    carbon_cost_by_program:   # copy current's carbon settings verbatim
      "ETS 1": .
      "ETS 2": 33.43
      "ETS 3": 33.43
    max_cap_req_fn: growth_caps/uncapped.csv
  current_release_2035:
    carbon_cost_by_program:   # copy current's carbon settings verbatim
      "ETS 1": .
      "ETS 2": 33.43
      "ETS 3": 33.43
    max_cap_req_fn: growth_caps/release_2035.csv
```

Also **delete the `all_cases: MaxCapReq: MaxCapTag_WindGrowth/SolarGrowth/...` blocks** for every year (they'd otherwise still apply even to `current_uncapped`/`current_release_2035`, and would need the same collision reasoning re-litigated) — replace them with the file-based `current.csv` path under `policies: current` only, so growth caps are set in exactly one place per scenario, no `all_cases` layering. **This is the highest-risk step** — confirm via the verification steps below that every non-fedpol scenario (which all implicitly select `policies: current`) still gets identical `max_cap_requirements.csv` output before/after.

### 4. New `offshore_wind_policy` level, for Ban release

Under `offshore_wind_policy: capped_2025` (used by `current_constraintremoval`/`reinstate_constraintremoval`), add a sibling level, e.g. `capped_2025_released`: identical to `capped_2025` (same state-mandate-zeroing `MinCapReq` overrides) plus `MaxCapReq: MaxCapTag_Ban: {description: banned technologies, max_mw: 999999999}`. `biden_constraintremoval` doesn't need this (already uses `open`, which never tags anything `MaxCapTag_Ban` in the first place).

### 5. Migrate `scenario_inputs.csv` rows and remove `constraint_removal`

| case_id | new `policies` | new `offshore_wind_policy` | `constraint_removal` |
|---|---|---|---|
| `s4x1_fedpol_biden_constraintremoval` | `current_uncapped` | `open` (unchanged) | set to `none` (or drop column) |
| `s4x1_fedpol_current_constraintremoval` | `current_uncapped` | `capped_2025_released` | set to `none` (or drop column) |
| `s4x1_fedpol_reinstate_constraintremoval` | `current_uncapped` | `capped_2025_released` | set to `none` (or drop column) |
| `s4x1_fedpol_cost_optimal` | `current_release_2035` (all 5 rows) | `open` (unchanged) | set to `none` (or drop column) |

Then delete the `constraint_removal:` axis block entirely from `scenario_management.yml` (currently under `all_years`, around the block whose header comment starts "`partial` vs `full`: MaxCapTag_WindGrowth_<transreg>..." — note the changes made to that block during this investigation should be reverted/removed as part of this work, not left in place). Leaving the now-inert `constraint_removal` *column* in `scenario_inputs.csv` (all `none`) is lower-risk than deleting the column outright (700+ rows); full column removal can be a follow-up cleanup once the axis is confirmed gone from `scenario_management.yml` (PowerGenome just logs a harmless "not included in settings_management" warning for unreferenced columns, same as the existing `tp_duration_hours` warning already seen during this investigation's builds).

## Verification

1. **No-regression check for `policies: current`** (highest priority — this touches every non-fedpol scenario in the repo). Before deleting the `all_cases` growth-cap blocks, capture `max_cap_requirements.csv` for a representative sample of *non-fedpol* cases (e.g. one `s4x1`, one `s20x1` case, a couple of years each) via a dry `pg_to_switch.py` build. After the change, rebuild the same cases/years and diff `max_cap_requirements.csv` — must be numerically identical for `MaxCapTag_WindGrowth`/`MaxCapTag_SolarGrowth`/regional tags.
2. **Settings-merge check** for the 5 migrated fedpol families: a short script calling `powergenome.util.build_scenario_settings()` per (case, year), printing `scen_settings["max_cap_req_fn"]`, to confirm the right file is selected at each period.
3. **Dry `pg_to_switch.py` build** for `s4x1_fedpol_cost_optimal` (2028/2030/2035) and the three `*_constraintremoval` families: confirm `max_cap_requirements.csv` shows the intended schedule (native values at 2028 for cost_optimal, `999999999` from 2030/2035 as designed; `999999999` for all periods for the `_constraintremoval` families). Confirm `s4x1_fedpol_current_constraintremoval`/`reinstate_constraintremoval` also show `MaxCapTag_Ban` released (`999999999`) via the new `offshore_wind_policy` level.
4. Delete any scratch build folders (e.g. `switch/in_costopt_test/`) afterward — do not leave them in the repo.
5. No changes needed to `switch/study_modules/max_capacity_constraint.py` or any other Switch-side code — confirmed during this investigation that the constraint only ever needs a period-indexed ceiling, which `max_cap_requirements.csv` already provides.
6. Once verified, `s4x1_fedpol_cost_optimal` and the `*_constraintremoval` sensitivity variants are unblocked for a real solve (not attempted during this investigation).
