# Switch Model Modifications

Master tracker for planned and implemented modifications to the Switch-USA-PG-ReEDS model and data pipeline.

**Statuses:** `implemented` | `implemented, not yet merged to edf-baseline` | `planned` | `scoping`

Detailed supporting documents referenced below (`plans/planned_mods/<MOD-ID>/`,
`plans/<topic>.md`) are local, untracked planning notes under `docs/plans/` —
they are not part of the committed repo, so links to them only resolve if
you have that local working copy. **Implemented changes are recorded in
`CHANGES.md`** (repo root) — that file, not `docs/CHANGELOG.md`, is the
definitive record; section numbers below (`CHANGES.md §N`) refer to it.

---

## MOD-001 · Utility-ownership generator clustering

**Status:** `implemented`
**Component:** PowerGenome / `pg_to_switch.py`
**Docs:** `docs/plans/planned_mods/MOD-001_utility_clustering/HANDOFF.md` (original design; actual implementation differed — see `CHANGES.md` §23)
**Source:** https://claude.ai/chat/7af5a107-cefe-4a88-b7a1-afa2d50769af

### Description
Separate generator clusters for assets owned by specific utilities, layered on top of the existing age/heat-rate clustering logic. Controlled via a `gen_clustering` column in `scenario_inputs.csv`.

### As implemented
- Parameter: `gen_clustering` in `scenario_inputs.csv` (values: `none`, `top5_co2`, `top10_co2`)
- Setting: `cluster_existing_by_utility` in PowerGenome (not `cluster_by_owner` as originally designed)
- Upstream implementation: direct edits to `PowerGenome/powergenome/generators.py`
- Resource ID format: `{region}_{technology}_{utility_id}_{k}`
- CO2 ranking: global across all regions/technologies

See `CHANGES.md` §23 for full implementation details.

---

## MOD-002 · Hourly emissions accounting / tracked demands module

**Status:** `implemented, not yet merged to edf-baseline`
**Component:** New Switch module (`switch_model/policies/tracked_demands.py`)
**Branch:** `tom/tracked-demands` (also `study/tracked-demands`) — **not** on
`edf-baseline` or any branch this repo's `CHANGES.md` covers; not reflected there.
**Docs:**
- `docs/plans/tracked_demands.md` — summary plan entry
- `docs/plans/planned_mods/MOD-002_tracked_demands/HANDOFF.md` — 10-stage implementation plan for Claude Code
- `docs/plans/planned_mods/MOD-002_tracked_demands/spec_v1.md` — full module specification
- `docs/plans/planned_mods/MOD-002_tracked_demands/design_decisions.md` — design decisions register with open items
**Source:** https://claude.ai/chat/bf73f2fc-3445-4d6e-8e42-d1b87ad751b1

### Description
A new Switch module enabling hourly clean energy matching for specific demand loads (e.g. electrolyzers under IRA 45V, data centres pursuing 24/7 CFE commitments). Overlays clean energy accounting on top of the normal energy balance without replacing the system-wide carbon cap or carbon cost.

### Status as implemented
Stages 1–11 of the original roadmap are complete on the `tom/tracked-demands`
branch: regional CFE accounting, hourly emissions, 45V tiered credit, and REC
purchasing are all working, with a realistic data-center CFE scenario tested.
This has **not** been merged into `edf-baseline` — if you need this
functionality on a case built from `edf-baseline` (or from this repo's current
branch), it must be merged in separately; don't assume it's present just
because this tracker shows it as implemented.

### Key features
- Hourly and annual matching constraint variants
- 45V tiered hydrogen production tax credit calculation in objective function
- Deliverability region logic and incrementality filtering
- Optional on-site generation (diesel, gas, CCGT+CCS) and battery storage
- Location flexibility — model can endogenously choose zone for a new facility
- Grid flexibility events (demand response / curtailment obligations)
- Iterative workflow: exogenous `grid_clean_fraction` in optimisation → post-solve computes endogenous rates → feed back for next run

### Next iteration (not yet started)
6 seasonal time blocks, activated REC supply, endogenous REC price via 2-pass
dual extraction — see `docs/plans/tracked_demands.md` and
`docs/analysis/.../dc_cfe_time_blocks_and_recs.md` (local/untracked).

---

## MOD-003 · Exogenous grid emissions rates input

**Status:** `implemented, not yet merged to edf-baseline` (bundled with MOD-002 on `tom/tracked-demands`)
**Component:** `pg_to_switch.py` data pipeline
**Depends on:** MOD-002

### Description
Companion data pipeline addition to MOD-002. Generates `grid_emissions_rates.csv` — zonal, hourly emissions intensity values — from a reference model run or external data source (e.g. eGRID). Required for Phase 1 of the tracked demands module. Covered within MOD-002 documents/branch.

---

## MOD-004 · Flexible electrolyzer demand (endogenous scheduling)

**Status:** `implemented, not yet merged to edf-baseline` (bundled with MOD-002 on `tom/tracked-demands`)
**Component:** Extension of MOD-002
**Depends on:** MOD-002 Phase 1–2

### Description
Extension of the tracked demands module to allow the model to endogenously optimise electrolyzer production scheduling around clean energy availability. Flagged as Phase 3 of MOD-002. Covered within MOD-002 documents/branch.

---

## MOD-005 · Transmission modeling enhancements

**Status:** `implemented`
**Component:** Four Switch modules (`switch/study_modules/trans_hurdle_cost.py`,
`trans_new_build_derate.py`, `trans_asymmetric_capacity.py`,
`trans_build_minimum.py`) + `pg_to_switch.py` — **not** the single
`transmission_enhancements.py` module originally planned; implemented as
separate, independently-toggleable modules instead (see `CHANGES.md` §1–9 for
why: each has its own scenario toggle column and load-order requirement).
**Docs:**
- `docs/plans/transmission_enhancements.md` — summary plan entry
- `docs/plans/planned_mods/MOD-005_directional_transmission/HANDOFF.md` — full implementation spec for Claude Code
**Source:** https://claude.ai/chat/6ba5cf53-2b15-4d54-bb53-7e32863f5e88

### Description
Three enhancements to improve realism of inter-regional transmission representation, addressing the model's tendency to overestimate usable transmission capacity and over-rely on imports relative to historical regional generation patterns.

### As implemented — differences from original plan
- **Directional capacity limits** → `trans_asymmetric_capacity.py`, reading
  `trans_directional_limits.csv` (not `transmission_lines_directional.csv`).
  Supplements (doesn't replace) the symmetric `Maximum_DispatchTx` constraint —
  the tighter of the two applies.
- **Derating** → `trans_new_build_derate.py`, a flat 15% (0.85) factor applied
  only to *new-build* capacity crossing `transgrp` boundaries, replacing
  `Maximum_DispatchTx` with `Maximum_DispatchTx_Derated` — not a general
  existing-capacity derating factor as originally scoped.
- **Hurdle rates** → `trans_hurdle_cost.py`, reading `trans_hurdle_cost.csv`
  (sourced from `cost_hurdle_intra.csv`), applied symmetrically to
  cross-`hurdlereg` flows.
- **Additional, unplanned enhancement**: `trans_build_minimum.py` enforces
  minimum new-build capacity for planned/committed transmission projects by a
  target period — this wasn't in the original three-enhancement scope.
- **Additional, unplanned enhancement**: REFS2009 → NARIS2024 transmission
  constraints dataset migration, with an `asymmetry: old` toggle to revert
  (`CHANGES.md` §7–8).

Full details, scenario toggle columns (`transmission`, `trans_expansion`,
`hurdle`, `degrade`, `asymmetry`, `build_minimum`), and known data gaps: see
`CHANGES.md` §1–9.

---

## MOD-006 · In-zone generation ratio constraints (`gen_zone_ratio`)

**Status:** `implemented`
**Component:** New Switch module (`switch/study_modules/gen_zone_ratio.py`) + `make_zone_ratios.py` helper
**Docs:** `CHANGES.md` §10–12 (module description), §14 (hierarchy.csv walk-up
bug fix), §19 (`apply_input_aliases` bug fix)

### Description
Constrains in-zone annual generation as a fraction of in-zone annual load,
and/or in-zone generation as a fraction of contemporaneous load at every
timepoint, at both individual-zone and named-group levels (state, utility
territory, transmission region, RGGI+VA, etc. — any `hierarchy.csv` column).
Not in the original mod-tracking scope; added directly, documented in
`CHANGES.md` from the start.

### Notable bug history (both fixed, both worth knowing if comparing to older runs)
- **2026-05-01:** an `apply_input_aliases()` bypass caused `--input-alias`
  max-ratio variants to silently run against the baseline file instead —
  invalidates any max×1.5/×2.0/×3.0 runs from before this fix.
- **2026-05-15 to 2026-07-17:** `hierarchy.csv` lookup only checked
  `inputs_dir`, silently skipping all group-level constraints if not found
  there — invalidates any group-constraint run where `hierarchy.csv` wasn't
  copied into the per-case inputs dir, before the walk-up fix.

---

## MOD-007 · Region scope / geographic aggregation

**Status:** `implemented`
**Component:** `pg_to_switch.py`
**Docs:** `CHANGES.md` §20

### Description
Scenario-manageable geographic aggregation via a `region_scope` column in
`scenario_inputs.csv` — aggregates the 134-BA model into 48 states (`st`) or 3
interconnects (`interconnect`) for smaller, faster solves, remapping all policy
types (CO2 caps, RPS, PRR, min/max cap requirements) after aggregation.

**Known limitation:** interconnect-level model is currently infeasible for
offshore wind due to state mandate aggregation (`docs/issues/offshore_wind_infeasibility.md`,
local/untracked).

---

## MOD-008 · RGGI carbon cap framework — hard cap, two-tier CCR, minimum reserve price

**Status:** `implemented`
**Component:** `switch/study_modules/carbon_policies_regional.py` + `pg_to_switch.py`
**Docs:** `CHANGES.md` §13–14; usage guidance in
`Guides and documentation/rggi_scenario_framework.md` (run matrix/alias
mechanics) and `Guides and documentation/rggi_hard_cap_ccr_and_floor.md`
(hard cap, CCR, floor-price mechanism, RGGI_VA `gen_zone_ratio`)

### Description
RGGI (ETS 1) enforces as a true hard cap (no escape valve) with a two-tier Cost
Containment Reserve providing bounded priced flexibility, while CA (ETS 2) and
WA (ETS 3) keep soft escape-valve pricing — all per-program, configured via
`carbon_cost_by_program`. A minimum reserve (floor) price is enforced via a
`FloorAllowances` supply-restriction variable rather than a flat cost adder.
`carbon_program_clearing_prices.csv` reports clearing price, floor price, CCR
usage, and auction revenue per program/period. Requires
`--solver-options-string "crossover=1"` for reliable LP duals.

Also includes the RGGI+VA rejoining alias mechanism (`carbon_policies_regional_va.csv`)
and an `rggi_va` `hierarchy.csv` column for scoping `gen_zone_ratio` group
constraints to the combined RGGI+VA footprint.

---

## MOD-009 · Regional (per-transreg) wind growth caps

**Status:** `implemented`
**Component:** `make_emission_policies.py` + `pg/update_max_cap_files.py`
**Docs:** `CHANGES.md` §15, §17 (predetermined-capacity floor fix); usage
guidance in `Guides and documentation/regional_wind_caps_and_zonal_lmp.md`

### Description
Disaggregates the national onshore wind growth cap (`MaxCapTag_WindGrowth`)
into nine per-transreg caps via a three-tier methodology: near-term (≤2028)
grounded in LBNL interconnection-queue data, mid-term (2029–2030) in EIA 860M
historical regional deployment shares, long-term (≥2031) reverting to the
national cap only. Requires `docs/analysis/RGGI/lbnl_queue_by_state.csv` as an
input (local/untracked, regenerable from the LBNL queue workbook).

---

## MOD-010 · `write_zonal_lmp` study module

**Status:** `implemented`
**Component:** New Switch module (`switch/study_modules/write_zonal_lmp.py`)
**Docs:** `CHANGES.md` §16; usage guidance in
`Guides and documentation/regional_wind_caps_and_zonal_lmp.md`

### Description
Post-solve reporting module that derives zonal LMPs ($/MWh) from the
`Distributed_Energy_Balance` constraint dual, writing per-timepoint and
load-weighted annual-average (extreme-day-excluded) output files. Purely
additive — no effect on the optimization itself.

---

*Last updated: 2026-07-17.*
