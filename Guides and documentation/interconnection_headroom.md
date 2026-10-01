# Interconnection Headroom

**Branch:** `tom/interconnection-headroom` (off `edf-baseline`)
**Status:** draft. Pipeline tested on synthetic cost data; Switch module tested on the Switch
2.0.9 three-zone toy; not yet run on a Switch-USA-PG case.

---

## What it does

New wind, solar, storage and gas in each ReEDS zone must fit inside the zone's interconnection
headroom. Headroom is bought in steps (tranches) whose $/kW rises as the zone fills, estimated
from LBNL project-level network-upgrade costs. Retiring plants free headroom. This replaces
PowerGenome's fixed, distance-based reinforcement cost (`tx_capex`), which doesn't rise as a
zone fills. Method, data and first results: `interconnection_headroom/docs/project_doc.html`.

## Running it

1. **Build the curves** (any machine with Python; no PowerGenome needed):

   ```
   cd interconnection_headroom
   bash scripts/fetch_data.sh                # EIA-860M; LBNL workbooks go in data/raw/lbnl/
   python -m icsc.cli inspect-lbnl           # fix config.yaml lbnl.columns until nothing is MISSING
   python -m icsc.cli fit-weights            # pick proxy, tech weights and reuse share by fit
   python -m icsc.cli run --start-year 2026  # writes outputs/tranches_<scenario>.csv
   ```

   `outputs/` is gitignored. Copy it to energyVm1 with the branch, or rerun there.

2. **Turn it on** for a case set:
   - `pg/settings/interconnection_headroom.yml`: `enabled: true` and choose `scenario`.
   - `switch/modules.txt`: uncomment `study_modules.interconnection_headroom`.

3. **Generate inputs and solve as usual** (`setup_cases.sh`, then the scenario files).
   Each case folder gets `ic_tranches.csv`, `ic_weights.csv`, `ic_params.csv` and
   `ic_connect_cost_check.csv`. Each solve writes `ic_headroom.csv` (headroom used, freed and
   bought, with the dual), `ic_tranches_built.csv` and `ic_gen_weights.csv`.

## Scenarios

| `scenario` | Curve |
|---|---|
| `reference` | Empirical curve with each zone's planning-regime effect |
| `best_regime` | Every zone gets the lowest-cost regime effect; saturation slope unchanged |
| `gets` | Adds a GETs tranche (placeholder cost and size) |
| `reconductoring` | GETs plus an advanced-reconductoring tranche (placeholders) |
| `proactive_planning` | Empirical tranche costs × 0.6 (placeholder) |

## Checks on the first real run

- **Reinforcement cost removed once, and only once.** Open `ic_connect_cost_check.csv` in one
  case. `before − after` should equal `tx_capex`. Check that `interconnect_capex_mw` includes
  `tx_capex`. If it is spur plus substation only, `gen_connect_cost_per_mw` never contained
  reinforcement: set `exclude_network_reinforcement: false`. Also check whether `spur_capex` is
  counted twice in the existing formula (`spur_capex + interconnect_capex_mw` in
  `conversion_functions.gen_info_table`). That would be a pre-existing issue, separate from
  this change.
- **Weights.** `ic_gen_weights.csv` should show 0 for demand response, imports and distributed
  generation, and the configured weights for everything else.
- **Historical periods.** Network-upgrade tranches are available from the first period;
  GETs/reconductoring tranches only from their `available_year`.
- **Myopic chains.** Stage 2 should read `ic_tranches.chained.<case>.csv`, with lower
  `ic_tranche_max_mw` where stage 1 bought headroom.
- **Region scope.** With `st` or `interconnect` aggregation, tranches from each p-zone are
  pooled into the aggregate zone, which is the correct aggregate curve.

## Known limitations

- Placeholder policy-tranche costs; CPI table to verify against BLS.
- PJM and NYISO saturation looks too low with a 0.8 retirement reuse share; `fit-weights`
  tests lower shares.
- Phase 2 (a resource hub per zone with an hourly intra-zonal interface) is not built.
