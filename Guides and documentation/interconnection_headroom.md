# Interconnection Headroom

**Branch:** `tom/interconnection-headroom` (off `edf-baseline`)
**Status:** draft. Pipeline tested on synthetic cost data; Switch module tested on the Switch
2.0.9 three-zone toy; not yet run on a Switch-USA-PG case.

---

## What it does

Each ReEDS BA has intra-zonal network capacity **H** (MW). How full the zone is, **s = used / H**,
sets the marginal cost of connecting more generation. That curve is estimated from LBNL
network-upgrade costs and replaces PowerGenome's fixed, distance-based reinforcement cost
(`tx_capex`), which doesn't rise as a zone fills.

The curve has four handles:

| Lever | What changes | How it's represented |
|---|---|---|
| Retirements | Position (s falls) | Retired capacity frees headroom (reuse share) |
| GETs, reconductoring | H: stretches the curve and moves the zone back down it | Uprate options Switch can build |
| New intra-zonal lines | Headroom added directly (host mode, default) | Uprate option at ReEDS reinforcement cost per MW of generation |
| Cost allocation, regime, queue process | Height | `regime_override: best`, `cost_multiplier` |
| Proactive or portfolio planning | Steepness | `slope_multiplier` (later: regime-specific slopes) |

Method, data and first results: `interconnection_headroom/docs/project_doc.html`.

## How Switch represents it

Per IC zone and period:

- Step k of the curve provides up to `width_k x H` MW of generation headroom at `cost_k` $/MW.
- `H = H0 + MW of stretch uprates built` (GETs, reconductoring). Each uprate option has a cap
  (share of H0), a $/MW and a first year.
- When H grows, existing use fills less of it, releasing up to `s0 x MW of stretch uprates` of
  headroom priced at the zone's starting marginal cost.
- New lines (`ic_uprate_mode: host`, pipeline `new_line_mode: host`, the default) add weighted MW of
  headroom directly at the zone's ReEDS reinforcement cost per MW of generation (median $319/kW),
  with no step or release cost on top, and leave H unchanged. ReEDS's cost already covers delivering
  the MW to the zone centre, so charging the LBNL step costs too would count upgrades twice.
  `new_line_mode: stretch` restores the old behaviour (a new line adds to H).

Per load zone (summing its IC zones): tech-weighted new capacity <= initial headroom + reuse x
retired capacity + steps + released headroom + hosted headroom. Everything is linear. The release term is exact for
small uprates and slightly conservative for large ones.

## Outputs

| File | Contents |
|---|---|
| `ic_spend.csv` | Overnight and annual $ by IC zone, period and type (`reactive_upgrades`, `gets`, `reconductor`, `new_line`), network MW added, generation MW enabled, and network MW per generation MW for reactive upgrades |
| `ic_network.csv` | Base capacity; MW and % added by GETs/reconductoring (`deliberate_mw_added`); estimated MW and % added by reactive upgrades; headroom from the curve and released; `hosted_mw` (new lines) and `new_line_network_mw_implied` = hosted MW / `curve_end_saturation` (s0 + step widths: weighted generation per MW of network at the end of the curve) |
| `ic_headroom.csv` | Per load zone: new weighted capacity, freed, bought, initial headroom, dual |
| `ic_gen_weights.csv` | Weight applied to each project |

Spend figures are cumulative to each period. Annual cost uses the model interest rate over
`ic_asset_life_years` (40).

**Reactive capacity is an estimate.** The empirical curve records what generators paid, not how
much network that bought. `reactive_mw_added_est` divides that spend by an engineering cost of
network capacity (`reactive_cost_per_kw_network` in the pipeline config, a placeholder). Check
`network_mw_per_generation_mw`: values well above 1-2 mean the curve's cost reflects more than
physical reinforcement (process, local constraints) or that the engineering cost is off. Uprate
MW are exact model decisions.

## Running it

1. **Build the curves** (any machine with Python; no PowerGenome needed):

   ```
   cd interconnection_headroom
   bash scripts/fetch_data.sh                # EIA-860M; LBNL workbooks go in data/raw/lbnl/
   python -m icsc.cli inspect-lbnl           # fix config.yaml lbnl.columns until nothing is MISSING
   python -m icsc.cli fit-weights            # pick proxy, tech weights and reuse share by fit
   python -m icsc.cli run --start-year 2026  # writes outputs/{zones,tranches,uprates}_<scenario>.csv
   ```

   `outputs/` is gitignored. Copy it to energyVm1 with the branch, or rerun there.

2. **Turn it on** for a case set:
   - `pg/settings/interconnection_headroom.yml`: `enabled: true` and choose `scenario`.
   - `switch/modules.txt`: uncomment `study_modules.interconnection_headroom`.

3. **Generate inputs and solve as usual** (`setup_cases.sh`, then the scenario files).

## Scenarios

| `scenario` | Curve |
|---|---|
| `reference` | Empirical curve with each zone's regime effect; no uprate options |
| `best_regime` | Lowest regime effect everywhere (height) |
| `gets` | Switch may add GETs up to 10% of H0 (placeholder cost) |
| `reconductoring` | GETs plus reconductoring up to 30% of H0 from 2028 (placeholders) |
| `proactive_planning` | All uprate options incl. new lines from 2032, and slope × 0.7 (placeholders) |

## Checks on the first real run

- **Reinforcement removed once, and only once.** In `ic_connect_cost_check.csv`, `before − after`
  should equal `tx_capex`. Check that `interconnect_capex_mw` includes `tx_capex`; if it is spur
  plus substation only, set `exclude_network_reinforcement: false`. Also check whether `spur_capex`
  is counted twice in `conversion_functions.gen_info_table` (`spur_capex + interconnect_capex_mw`),
  a pre-existing issue separate from this change.
- **Weights.** `ic_gen_weights.csv` should show 0 for demand response, imports and distributed
  generation.
- **Myopic chains.** Stage 2 should read the `.chained.<case>.csv` versions of `ic_zones`,
  `ic_tranches` and `ic_uprates`: higher base capacity where stage 1 built GETs or reconductoring,
  narrower steps where it bought headroom, and `ic_hosted_headroom_mw` with new-line headroom stage 1
  built but did not use (from `ic_hosted_built.csv`).
- **Region scope.** With `st` or `interconnect` aggregation, each aggregate load zone keeps its
  BAs as separate IC zones and pools their headroom.

## Known limitations

- Placeholder uprate costs and caps (should come from HIFLD line-km by voltage), reactive
  engineering cost, slope multiplier, and CPI table.
- PJM and NYISO saturation looks too low with a 0.8 retirement reuse share; `fit-weights` tests
  lower shares.
- Summing MW added across a zone over-counts upgrades on the same corridor; quote % of base too.
- Phase 2 (an hourly intra-zonal interface with curtailment) is not built.
