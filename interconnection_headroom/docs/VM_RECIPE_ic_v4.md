# ic_v4 on energyVm1: host-mode headroom + build rate, from `tom/s0-integration`

Goal: build the ic_v4 cases the same way as the current icv3 + br_central test cases. They are S0 with
the wind/solar growth caps released and build_rate central. The difference is that the interconnection
headroom inputs come from `tom/s0-integration`:
- atts_s0, with GETs, advanced conductors and conventional reinforcement all in host mode;
- conventional reinforcement available from the first period.

Shell: Git Bash. Repo: `/d/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS` (quote
every path; it has spaces).

Ground rules:
- Never check out another branch in the main tree (it is on `tom/ic-test-fedpol` with work in
  progress). All work happens in a separate worktree.
- Never delete files this recipe did not create.
- No installs into shared conda envs.
- Case inputs go to a fresh folder only.

## 0. Variables and a look at the main tree (read-only)

```bash
MAIN="/d/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS"
WT="/d/SWITCH/ReEDS version/Q1 2026 - Strategy runs/Switch-USA-PG-ReEDS_ic_v4"   # new worktree
git -C "$MAIN" status --short | head -40        # just look; nothing here is changed
git -C "$MAIN" fetch origin tom/s0-integration tom/ic-test-fedpol
```

## 1. Worktree on a local branch based on `tom/s0-integration`

`tom/s0-integration` is `tom/ic-atts` at 5450c1e plus `tom/build-rate`. The S0 case definitions
(`S0_uncapped`, growth caps, the RPS ACP, the `interconnection_headroom` and `build_rate` scenario
axes) exist only on `tom/ic-test-fedpol`, so merge that branch into a local branch. The local branch
is `vm/ic_v4`; do not push it. Checked in the cloud: the only conflict is `CHANGES.md`. Keep both
sides, with §40 (fedpol) between §39 and §41.

```bash
git -C "$MAIN" worktree add -b vm/ic_v4 "$WT" origin/tom/s0-integration
cd "$WT"
git merge --no-ff origin/tom/ic-test-fedpol      # resolve CHANGES.md only, then:
git add CHANGES.md && git commit --no-edit
git log --oneline -3
```

The main tree's uncommitted work (e.g. the icv3 / br_central rows in
`pg/extra_inputs/scenario_inputs.csv` and the `build_rate` column) is not in the worktree. Copy
those files from `$MAIN` into `$WT`; `cp` only reads `$MAIN`.

1. List what to copy: `git -C "$MAIN" status --short`.
2. Copy each file, for example:
   ```bash
   cp "$MAIN/pg/extra_inputs/scenario_inputs.csv" "$WT/pg/extra_inputs/"
   ```
3. Add ic_v4 rows: copy each icv3 + br_central row with a new `case_id` (e.g. `..._icv4`). Keep
   `interconnection_headroom` = `on` and `build_rate` = `central`.
4. In `$WT/pg/settings/scenario_management.yml`, under `interconnection_headroom: "on"`, change
   `scenario: reference` to `scenario: atts_s0`. Both give the same result (reference = atts_s0); the
   explicit name makes the intent clear.

## 2. Environment check (no installs)

Use the same conda env as the icv3 runs. If any import fails, stop and report it. Do not install into
the env.

```bash
cd "$WT"
python -c "import pandas, numpy, h5py, yaml, pyomo, switch_model, typer; print('ok')"
which switch
```

## 3. Inputs the worktree lacks (gitignored)

1. List what is missing in the worktree:
   ```bash
   ls interconnection_headroom/data/raw build_rate/data/raw 2>&1
   ```
2. Copy from the main tree (read-only there), or run each module's `scripts/fetch_data.sh`. The
   six LBNL workbooks must be the real ones.
   ```bash
   cp -r "$MAIN/interconnection_headroom/data/raw" interconnection_headroom/data/
   cp -r "$MAIN/build_rate/data/raw" build_rate/data/
   ```
3. Synthetic data: list it, don't delete it. If anything is listed, stop and tell Tom; it must not be
   in a real run.
   ```bash
   ls interconnection_headroom/data/raw/lbnl/SYNTHETIC_*.xlsx 2>/dev/null
   ```
4. Check where `pg_data.yml` and `pg/settings` point for PowerGenome data. If paths are relative to
   the main tree, stop and decide (copy vs. absolute path). Do not move data.
   ```bash
   grep -n "path\|folder\|dir" pg_data.yml | head
   ```

## 4. Tests

```bash
cd "$WT/interconnection_headroom" && SWITCH_SRC="<switch checkout>" pytest -q
#   expect 26 passed, incl. test_s0_integration.py (toy solve with both modules on)
cd "$WT/build_rate" && SWITCH_SRC="<switch checkout>" pytest -q
#   expect 22 passed
```

## 5. Interconnection headroom pipeline (atts_s0)

```bash
cd "$WT/interconnection_headroom"
python -m icsc.cli run --start-year 2026
python -c "import json;s=json.load(open('outputs/run_summary.json'));print(s['n_estimation'],round(s['r2'],3),s['scenarios']['atts_s0'])"
```

Expected values (cloud run):
- n = 2,787, pseudo-R² 0.341;
- median first step $129.0/kW; 20 zones beyond the support edge.

In `outputs/uprates_atts_s0.csv`:
- gets: hosted 2.4 GW (`max_mw`), network 3.4 GW (`max_mw_network`).
- reconductor: 0.
- conv_reinforcement: `available_year` 0.
- Every `mode` is host.
- Costs: GETs $15.2 (PJM) / $33.7 per kW; advanced conductors median $214/kW; conventional median
  $319/kW.

Optional: `--config sensitivities/gets_cost_pjm.yaml` / `gets_cost_spp.yaml` for the GETs-cost
sensitivity.

## 6. Build-rate tables

```bash
cd "$WT/build_rate" && python -m brc.cli run
```

The build-rate code is the same as on `tom/ic-test-fedpol` (`tom/build-rate` is already merged
there), so the tables should match the ones behind icv3 + br_central. Check:
```bash
diff -r outputs "$MAIN/build_rate/outputs"
```

## 7. Case inputs to a fresh folder

Use the same `pg_to_switch` options as the icv3 + br_central build (myopic or not, years), with
`--case-id` set to the new ic_v4 rows. Write to a new folder.

```bash
cd "$WT"
test -e switch/in/ic_v4_hostmode && echo "exists: pick another name" || \
python pg_to_switch.py pg/settings switch/in/ic_v4_hostmode/ --case-id <ic_v4 case ids> [--myopic]
```

Use `pg_to_switch.py` (headroom and build_rate both enabled by the case's scenario axes), not
`patch-case`. `patch-case` is only for adding build_rate to an already-built case. It runs in
foresight, and only the pg_to_switch route writes the myopic `build_rate_prev_build` alias next to
the `ic_*` aliases.

## 8. Checks on the new inputs (per case folder)

`ic_uprates.csv`:
- Types are `gets`, `reconductor` and `conv_reinforcement` only. There must be no `new_line`.
- Every `ic_uprate_mode` is `host`.
- `ic_uprate_cost_per_mw` = 1000 × `cost_per_kw` from `uprates_atts_s0.csv`.
- `reconductor` max is 0 at s0.
- `conv_reinforcement` `ic_uprate_available_year` is 0 (first period).
- `gets` max sums to about 2.4 GW across the case's zones.

`ic_connect_cost_check.csv`: before − after = `tx_capex`.

Build-rate inputs:
- Files present: `build_rate_gens.csv`, `_groups`, `_periods`, `_tiers`, `_zones`, `_regions`.
- Groups: wind_onshore, solar, storage.
- `build_rate_regions.csv` covers wind_onshore and solar only.
- The case uses `S0_uncapped` (no MaxCapTag Wind/SolarGrowth rows in `max_cap_requirements.csv`).
- The files are identical to the icv3 + br_central case:
  ```bash
  diff <icv3 br_central inputs>/build_rate_periods.csv <ic_v4 inputs>/build_rate_periods.csv
  ```

`scenarios_*.txt`:
- `--include-module` / `modules.txt` lists both `study_modules.interconnection_headroom` and
  `study_modules.build_rate`.
- Later myopic stages carry `ic_zones/ic_tranches/ic_uprates.chained` and
  `build_rate_prev_build.chained` in one `--input-aliases` list.

Rerun the wind-loss step as for icv3 (`variable_capacity_factors.windloss.csv` alias).

## 9. Renames from CHANGES §42

VM scripts or notebooks that read outputs need these renames:

| Old name | New name |
|---|---|
| `new_line` (option/type) | `conv_reinforcement`, labelled "conventional reinforcement (ReEDS)" |
| `new_line_mode` | `reinforcement_mode` |
| `new_line_network_mw_implied` (`ic_network.csv`) | `hosted_network_mw_implied` |

- The `gets` and `reconductoring` pipeline scenarios are gone; use `atts_planned`.
- New in §43: `atts_mode` (host default) and `max_mw_network` in `uprates_*.csv`.

Find old names in VM scripts (read-only):
```bash
grep -rn "new_line" "$MAIN" --include=*.py --include=*.ipynb --include=*.yml | grep -v interconnection_headroom/
```

## 10. Clean-up (only what this recipe created)

After the runs, the worktree can be removed with `git -C "$MAIN" worktree remove "$WT"`. Do that
only once outputs are copied out; it deletes the worktree's files, all created by this recipe.
