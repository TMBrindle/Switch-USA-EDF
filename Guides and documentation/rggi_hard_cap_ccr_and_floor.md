# RGGI Hard Cap, CCR, Minimum Reserve Price & Related Fixes

**Branch:** `tom/regional-wind-caps` (merged via PR #7 into `edf-baseline`)
**Completed:** 2026-07-17
**Supersedes/extends:** [`rggi_scenario_framework.md`](rggi_scenario_framework.md) — that
doc still holds for the run-matrix/alias mechanics (NSP/VA/HF variants), but its CCR
section and results table predate the floor-price refactor described here (Part 3).
This doc covers what was added/changed in `carbon_policies_regional.py` and
`pg_to_switch.py` after that doc was written, plus the RGGI_VA `gen_zone_ratio`
constraint and two supporting bug fixes.

**Note on run scripts:** the personal orchestration scripts referenced in the older
doc (`run_rggi_solves.py`, `run_all_rggi.py`, etc.) were deliberately dropped from
this PR before merge (they're local dev tooling, not part of the feature). Nothing
below depends on them — all of this is driven by `pg_to_switch.py` +
`scenario_management.yml` + `--input-alias`/`--include-module` flags at solve time.

---

## Part 1 — Per-Program Hard Cap vs. Soft Cap

### What it does

`carbon_policies_regional.py` supports each CO2 program having its own cap
enforcement style, instead of one scalar escape-valve price applying everywhere:

```yaml
# pg/settings/switch.yml
carbon_cost_by_program:
  "ETS 1": .       # RGGI — hard cap (no escape valve)
  "ETS 2": 33.43   # CA cap-and-trade — soft cap, $/tCO2 escape-valve price
  "ETS 3": 33.43   # WA cap-and-invest — soft cap
```

`"."` (or omitted) means a **hard cap**: `AnnualCapViolation` for that program is
bounded to exactly 0 in `carbon_policies_regional.py`, so emissions can never
exceed the (CCR-augmented, see Part 2) cap — the model becomes infeasible if it
can't otherwise comply, rather than paying to exceed it. A numeric value means a
**soft cap**: the model may exceed the cap and pay that $/tCO2 price on the
excess via `AnnualCapViolation`.

This lets RGGI (ETS 1) behave as a true hard-capped program (matching how RGGI
actually functions — allowances are a fixed, auctioned quantity) while CA/WA
keep their existing soft-cap/carbon-tax-like treatment.

### Preparing inputs / building scenarios

`pg_to_switch.py`'s `other_tables()` reads `carbon_cost_by_program` from
scenario settings (falling back to the scalar `carbon_cost_dollar_per_tco2` for
any program not listed) and writes it into
`carbon_policies_regional.csv`'s `carbon_cost_dollar_per_tco2` column per
program/period/zone. No scenario-file changes are needed beyond what
`policies: current` already sets in `scenario_management.yml`
(`pg/settings/scenario_management.yml:503`) — this is active in the current
policy scenario already.

To override for a specific scenario (e.g. the `decarb` scenario, which needs a
single $200/tonne carbon tax to apply everywhere), set
`carbon_cost_by_program: {}` alongside a scalar `carbon_cost_dollar_per_tco2` —
the empty dict clears the per-program overrides so the scalar applies to every
program uniformly (see `policies: decarb` in `scenario_management.yml`).

### Turning it off

Set `carbon_cost_by_program["ETS 1"]` to a finite number (e.g. the same
$33.43 used for CA/WA) instead of `.` to make RGGI a soft cap like the others.
This automatically also makes the CCR tiers (Part 2) irrelevant for pricing
purposes, since soft-cap programs price at the escape-valve cost when binding,
not via CCR — though CCR pool constraints in `carbon_policies_ccr.csv` would
still exist as unused variables if you don't also drop them from
`carbon_ccr`/`carbon_ccr_prices`.

### Caveats

- **A hard-capped program with no CCR pool and no floor is riskier to solve** —
  if a scenario makes it infeasible to hit the cap even by building out clean
  generation, the run will fail infeasible rather than reporting a high but
  finite carbon price. Check for CCR headroom (Part 2) before assuming a hard
  cap will solve.

---

## Part 2 — Two-Tier CCR (Cost Containment Reserve)

The mechanics here match RGGI's real CCR design and are already documented in
detail in [`rggi_scenario_framework.md` § CCR Implementation](rggi_scenario_framework.md#ccr-implementation)
— pool sizes, price escalation, the step-function supply curve, and the
`crossover=1` solver requirement all still apply unchanged. Two things to add:

- **Pool sizes now live in `pg/settings/switch.yml`** under `carbon_ccr` (not
  hardcoded per-scenario):
  ```yaml
  carbon_ccr:
    "ETS 1":
      ccr_pools_tco2_per_yr: [10656120, 10656120]  # Tier 1, Tier 2 (metric tonnes)
  ```
  Year-specific **trigger prices** remain in `scenario_management.yml` under
  `{year}: policies: current: carbon_ccr_prices` (e.g. `"ETS 1": [23.00, 34.50]`
  for 2028).
- **If VA rejoins RGGI, CCR pools are not automatically rescaled** — see the
  existing doc's note on this; it's still true and still a deliberately
  conservative simplification (I-16 in `docs/issues/carbon_cap_design.md`).

### Caveats specific to this PR

- The `carbon_program_clearing_prices.csv` schema changed since the older doc
  was written — see Part 3 below for the current column list (it now includes
  `floor_price_dollar_per_tco2` and `auction_revenue_dollar_per_yr`, and the
  `clearing_price_dollar_per_tco2` computation now accounts for the floor —
  don't rely on the column list in `rggi_scenario_framework.md`).

---

## Part 3 — Minimum Reserve Price (Floor) via Supply Restriction

### What it does

RGGI (and CA/WA) auctions have a **minimum reserve price** — allowances don't
sell below a floor price even if the cap isn't binding. This is now modeled via
a `FloorAllowances` decision variable rather than a fixed per-zone cost adder
(the original implementation in commit `bd1026c` added a flat mandatory cost;
commit `95e5b9f` replaced it with the supply-restriction mechanism described
here, which is what's in the merged code today).

Mechanism (`carbon_policies_regional.py`, `define_components`):
- `FloorAllowances[program, period]` — bounded between 0 and the program's
  total cap — replaces the fixed cap in the emissions constraint.
- The regulator effectively **withholds** `(cap − FloorAllowances)` allowances
  whenever the market-clearing price would otherwise fall below the floor.
- Three regimes, all encoded directly in the constraint's dual value (no
  post-solve floor/scarcity-premium arithmetic needed anymore):

  | Regime | `FloorAllowances` | Constraint dual (clearing price) |
  |---|---|---|
  | Cap non-binding | = emissions | = floor price |
  | Binding, CCR active | = cap | = CCR tier trigger price |
  | Binding, no CCR | = cap | somewhere in `[floor, CCR trigger]` |
- When `carbon_floor_price_dollar_per_tco2_program = 0` (the default), the
  model is free to set `FloorAllowances = cap` and the mechanism has no effect
  — this is a no-op for any program that doesn't set a floor price.

### Preparing inputs

`pg_to_switch.py` reads `carbon_floor_price_by_program` from scenario settings
(a dict, e.g. `{"ETS 1": 10.62}`, $/metric tonne) and writes it to the
`carbon_floor_price_dollar_per_tco2` column of `carbon_policies_regional.csv`.
Programs not listed default to `0.0` (no floor).

Year-specific floor prices are set in `scenario_management.yml`:
```yaml
2028:
  policies:
    current:
      carbon_floor_price_by_program:
        "ETS 1": 10.62   # $9.63/short ton ÷ 0.907185
```
Values for 2028/2030/2035 are already populated in the merged
`scenario_management.yml` (search `carbon_floor_price_by_program`), derived
from the RGGI 3rd Program Review reserve-price schedule, converted from
$/short ton to $/metric tonne.

**VA zones inherit the floor price automatically** — `_add_va_zones()` in
`pg_to_switch.py` copies the floor price from the existing ETS 1 rows for that
period onto any VA rows it appends, so you don't need a separate VA floor-price
setting.

### Turning it off

Set `carbon_floor_price_by_program` to `{}` (or omit the key) for a given
policy scenario — floor prices default to `0.0` per program, which makes
`FloorAllowances` unconstrained up to the cap and restores pre-floor behavior
(clearing price is purely the cap's scarcity dual, or the CCR trigger, or the
soft-cap escape price).

### Caveats

- **The floor only binds when the cap is *not* binding.** If your scenario
  always blows through the cap (needs CCR or violates a soft cap), the floor
  price setting has no visible effect on the clearing price — it only matters
  in low-demand/high-clean-buildout scenarios where the market would otherwise
  clear below the regulatory floor.
- **`carbon_floor_price_dollar_per_tco2_program` takes the *first* zone's floor
  price** for a program/period (`min(m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, pe])`,
  by zone name) — it assumes all zones in a program share one floor price. If
  you ever set per-zone floor prices that differ within one program, only the
  alphabetically-first zone's value is used program-wide; the others are
  silently ignored for this purpose (they'd still need to be consistent inputs
  or this is a modeling bug waiting to surface).

---

## Part 4 — Clearing Price & Auction Revenue Output

### What it does

`carbon_policies_regional.py`'s `post_solve()` writes
`carbon_program_clearing_prices.csv` to `outputs_dir` after every solve, one
row per program/period. **Current column list** (supersedes the table in
`rggi_scenario_framework.md`):

| Column | Meaning |
|---|---|
| `CO2_PROGRAM` | Program name (e.g. `ETS 1`) |
| `PERIOD` | Model year |
| `cap_tco2_per_yr` | Total allowance budget (sum of finite zone caps) |
| `emissions_tco2_per_yr` | Actual covered emissions |
| `surplus_tco2_per_yr` | `cap − emissions` |
| `violation_tco2_per_yr` | `AnnualCapViolation` (soft-cap excess, 0 for hard caps) |
| `floor_price_dollar_per_tco2` | The program's floor/reserve price input |
| `clearing_price_dollar_per_tco2` | Computed clearing price — floor price if cap non-binding, CCR trigger if CCR active, LP dual otherwise (hard caps); escape-valve price if binding (soft caps) |
| `auction_revenue_dollar_per_yr` | **New**: `clearing_price × emissions` — the transfer payment, separated from net resource cost |
| `ccr_tier{N}_purchases_tco2`, `ccr_tier{N}_pool_tco2`, `ccr_tier{N}_price_dollar_per_tco2` | Per-tier CCR usage, only present for program/periods with CCR configured |

### Why `auction_revenue_dollar_per_yr` matters

Allowance auction revenue is a **transfer**, not a real resource cost — it
moves money from generators/ratepayers to the state, and (depending on how
`carbon_policies_regional`'s revenue-recycling assumptions are set elsewhere in
the model) may flow back to consumers. When comparing total system cost across
RGGI policy variants (e.g. hard cap vs. no-RGGI counterfactual), subtracting
`auction_revenue_dollar_per_yr` from `EmissionsCost` isolates the *net resource
cost* of the policy from the *revenue transfer* it also creates. Without this
column you'd have to reconstruct it from `clearing_price × emissions` by hand
from other output files.

### Caveats

- `auction_revenue_dollar_per_yr` uses the *effective* clearing price
  (including the floor-price regime), not just the CCR/base dual — so it moves
  correctly across all three floor-mechanism regimes described in Part 3.
- Still subject to the existing `crossover=1` requirement — without it, LP
  duals (and thus this derived revenue figure) are unreliable for hard-capped
  programs when the cap binds without CCR draws (see
  `rggi_scenario_framework.md` § "Solver requirement: crossover=1").

---

## Part 5 — RGGI_VA `gen_zone_ratio` Regional Constraint

### What it does

`hierarchy.csv` gained a new `rggi_va` column, mapping the 16 zones spanning
RGGI + Virginia (CT/DE/MA/MD/ME/NH/NJ/NY/RI/VT/VA — `p99, p100, p118, p124` and
others) into a single `RGGI_VA` group; all other zones are blank (not in the
group). This lets `gen_zone_ratio` (the existing in-zone-generation-vs-load
ratio constraint module — see the general module reference for its full
mechanics) be applied at "RGGI+VA as one region" granularity, in addition to
the existing `ba`/`st`/`transreg`/`hurdlereg` aggregation levels.

### A bug fix that affects *all* `gen_zone_ratio` scenarios, not just RGGI_VA

`gen_zone_ratio.py` previously required `hierarchy.csv` to be present directly
in the model's `inputs_dir`. If it wasn't there (e.g. a per-case inputs
directory that doesn't carry a copy of the project-root `hierarchy.csv`), group
constraints were **silently skipped** — the module would run with no error, but
without enforcing any group-level ratio constraint. This PR adds a walk-up
search (up to 6 parent directories) for `hierarchy.csv` if it's not found in
`inputs_dir`, printing `gen_zone_ratio: using hierarchy.csv from <path>` when it
finds one further up. If none is found anywhere in the walk, it now falls back
to the same silent-skip behavior with an explicit `WARNING gen_zone_ratio:
hierarchy.csv was not found` message, so at least it's visible in the solve log.

**Practical effect:** if you've run `gen_zone_ratio` scenarios before this fix
and *didn't* see the "hierarchy.csv was not found" warning in the log, your
group constraints (for any non-`_zoned` scenario, i.e. any case where
`hierarchy.csv` wasn't copied into the per-case inputs dir) were likely never
enforced. Worth re-checking older `gen_zone_ratio` output if this matters for
a past comparison.

### Preparing inputs / building an RGGI_VA scenario

There is **no preset `rggi_va` entry** in the `gen_zone_ratio:` block of
`scenario_management.yml` yet (only `ba_*`, `st_*`, `transreg_*`, `hurdlereg_*`
presets exist — see `pg/settings/scenario_management.yml:563`). To build an
RGGI_VA-scoped `gen_zone_ratio` scenario, add an entry following the existing
pattern, e.g.:

```yaml
gen_zone_ratio:
  rggi_va_min_only:
    gen_zone_ratio_agg: rggi_va
    gen_zone_ratio_min_spec: min
```

then set `gen_zone_ratio: rggi_va_min_only` in the relevant `scenario_inputs.csv`
row (the `gen_zone_ratio` column already exists there) and re-run
`pg_to_switch.py`. You'll also need a `gen_zone_load_ratio_rggi_va.csv`
reference file — generate it the same way as the other aggregation levels:
```
python make_zone_ratios.py --agg-by rggi_va --output pg/extra_inputs/gen_zone/gen_zone_load_ratio_rggi_va.csv
```
(matching the pattern documented in `scenario_management.yml` for `ba`/`st`/`transreg`).

**Reminder (applies to `gen_zone_ratio` generally, not just RGGI_VA):**
`gen_group_load_ratio.csv` and `gen_zone_ratio_group_by.csv` are written into
each case's `switch/in/{year}/{case}/` folder by `pg_to_switch.py`, and
`switch/in/` is entirely gitignored — these files must be regenerated (by
re-running `pg_to_switch.py`) any time you rebuild that case's inputs, they
won't persist across a fresh checkout or a `git clean`.

### Turning it off

Set `gen_zone_ratio: none` for the scenario (already the default in
`scenario_inputs.csv` unless a row explicitly opts in), or don't add
`study_modules.gen_zone_ratio` to `--include-module` for the solve.

### Caveats

- Since there's no preset yet, using RGGI_VA-scoped `gen_zone_ratio` requires
  the manual `scenario_management.yml` edit above — it's not a flip-a-flag
  scenario dimension the way NSP/VA/HF are in the RGGI run matrix.
- The `gen_zone_ratio_agg: rggi_va` grouping only makes sense combined with the
  RGGI/VA carbon-cap variants (Part 1) — applying it to a non-RGGI scenario
  would just constrain an odd 16-zone bucket with no corresponding policy
  rationale.

---

## Part 6 — Explicit `tp_duration_hours` Column

### What it does

`scenario_inputs.csv` gained a `tp_duration_hours` column (values `1` or `2`),
making the timepoint resolution an explicit, visible scenario dimension instead
of an implicit default buried in `switch.yml`'s `model_adjustment_scripts`
ordering. `adjust/increase_timepoint_duration.py` now reads this column for the
case/year being built and:
- Raises a clear error if the column or the matching row is missing (previously
  this would fall through to a default with no indication anything was
  configured).
- **Skips the 2-hour timepoint merge entirely when `tp_duration_hours == 1`**,
  printing `tp_duration_hours=1 for <year>/<case>; skipping timepoint merge.`
- For foresight/multi-period builds with no single `{year}` in the path, all
  matched rows for that `case_id` must agree on `tp_duration_hours` (this is
  the same lookup logic covered by Bug Fix 2 in
  [`regional_wind_caps_and_zonal_lmp.md`](regional_wind_caps_and_zonal_lmp.md) —
  the two fixes shipped together).

### Preparing inputs

Set `tp_duration_hours` to `1` or `2` for every `case_id`/`year` row in
`pg/extra_inputs/scenario_inputs.csv`. There's no default fallback — a missing
value for a row that gets built will raise `KeyError`/`ValueError` rather than
silently picking a resolution.

For `make_split_models: yes` cases, `scenario_management.yml` documents that
the timepoint-merge script should also be explicitly suppressed
(`Increase timepoint duration to 2 hours: ~`) to keep those cases at 1-hour
resolution — **the comment there is a reminder that this must match whatever
`tp_duration_hours` says for that row**; the two settings aren't
cross-validated against each other automatically.

### Caveats

- **These two settings can drift out of sync.** Setting
  `tp_duration_hours: 2` in `scenario_inputs.csv` for a `make_split_models: yes`
  case that still has the merge script suppressed in
  `model_adjustment_scripts` won't raise an error — the script suppression in
  `scenario_management.yml` wins (the script never runs), while
  `tp_duration_hours` in the CSV is only *read* by the script itself, so if the
  script doesn't run, the CSV value is simply unused for that case. Don't
  assume the CSV column is authoritative on its own; check whether the
  adjustment script is actually registered for that scenario combination.

---

## Bug Fix — `FUEL_BASED_GENS_IN_PERIOD` Variable Shadowing

`switch/study_modules/generators_core_dispatch.py`'s initializer for
`FUEL_BASED_GENS_IN_PERIOD` used an inner loop variable `p` that shadowed the
outer function parameter `p` (the period being initialized), so `d.pop(p)`
removed the wrong period's entry on the first call. This only manifests in
**multi-period (foresight) models** — myopic (single-period-at-a-time) runs
happened to have the loop variable land on the same value as the parameter, so
the bug was invisible there. Fixed by renaming the inner loop variable.

**No action needed** beyond pulling the updated code — this is a pure
correctness fix with no configuration surface. If you have foresight-mode RGGI
(or any other) results generated before this fix, treat
`FUEL_BASED_GENS_IN_PERIOD`-dependent outputs (which includes the emissions
constraint in `carbon_policies_regional.py` itself, since it filters on
`FUEL_BASED_GENS`) as suspect and worth re-running.

---

## Quick reference: what to run, in what order

```
# 1. Regenerate policy caps (national + regional wind, ESR, OSW, RGGI CO2, etc.)
python make_emission_policies.py

# 2. Build/rebuild Switch input case folders — writes carbon_policies_regional.csv,
#    carbon_policies_regional_va.csv (if VA alias configured), carbon_policies_ccr.csv,
#    and (if gen_zone_ratio != none) gen_group_load_ratio.csv / gen_zone_ratio_group_by.csv
python pg_to_switch.py <settings...>

# 3. Solve with crossover=1 (required for reliable RGGI clearing-price duals)
switch solve ... --solver-options-string "crossover=1" \
  [--input-alias carbon_policies_regional.csv=carbon_policies_regional_va.csv]  # for VA variant \
  [--include-module study_modules.gen_zone_ratio]                              # for gen_zone_ratio scenarios

# 4. Post-solve outputs of interest:
#    carbon_program_clearing_prices.csv  — clearing price, floor, CCR usage, auction revenue
#    gen_zone_ratio_summary.csv          — if gen_zone_ratio module included
```
