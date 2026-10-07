# Build-rate function: design state at cbbc907

**Branch:** `tom/s0-prod-scripts` · **State described:** commit `cbbc907` (CHANGES §84) · **Written:** 2026-10-07

**FROZEN:** no edits to `build_rate/`, the build-rate case writer (`build_rate/brc/switch_case.py`) or
`switch/study_modules/build_rate.py` until the separate design review finishes. This file is the only addition.

**Not this state:** `tom/s0-v3-scenarios`, where the S0 v3 scenario chains run. It has §74–75 only: v3's placeholder
post-2030 growth, no deliverability layer, no nuclear group, the placeholder ramp.

**Outputs aren't tracked:** `build_rate/outputs/` is untracked, so the tables are whatever `python -m brc.cli run`
last wrote on each machine. A v3.1 build needs a fresh run at this commit.

---

## 1. Where it lives

| Part | File | Role |
|---|---|---|
| Pipeline | `build_rate/brc/rates.py`, `data.py`, `cli.py` | `rates_<level>.csv` (R and ceilings by group, region and year, 2026–2050), `tiers_<level>.csv` |
| Config | `build_rate/config.yaml` | every parameter below |
| Case writer | `build_rate/brc/switch_case.py` (from `pg_to_switch.py`) | `build_rate_*.csv` per case: period window means, tiers, groups, regional ceilings, ramp columns |
| Switch module | `switch/study_modules/build_rate.py` | rate, ramp, tier, regional constraints; adders in the objective |
| Settings | `pg/settings/build_rate.yml`; S0: `pg/settings/s0_production.yml` (`settings.build_rate`), axis `build_rate`, `levels_by_period` / `level_overrides` | which level and groups a case uses |
| Gas-turbine cap | `build_rate/brc/turbine_cap.py` (`build_rate.gas_turbine_cap`) | separate cumulative cap on CC + CT; not the build rate itself |

Groups: wind_onshore, solar, storage (build rate); gas (opt-in, off in S0); nuclear (path group, §79, on in S0 v3.1).
S0 settings: `groups: [wind_onshore, solar, storage, nuclear]`, `regional_groups: [wind_onshore, solar]`, level
`central`. The legacy regression row pins `[wind_onshore, solar, storage]`.

## 2. Layers, in order of computation

### 2.1 Data rate R (queue/pace layer), national, MW/yr (`rates.rate_table`)
- **2026–2030:** `R = max(Q[y], R0)`.
  - **Q:** active LBNL Queued Up requests with an executed IA or under construction, each × its completion rate, in
    its expected COD year.
  - **Completion:** resolved IA-executed requests queued by 2018 (wind 46.4%, solar 65.0%, storage 86.9%);
    under construction 0.9 (PLACEHOLDER).
  - **COD:** max(proposed year, IA year + median IA-to-operation years).
  - **Overdue requests:** spread evenly over 2026–30 (PLACEHOLDER rule).
- **R0** from EIA-860M national additions 2021–25 (`r0_rule`):

  | Level of R0 | Rule | Wind / solar / storage, GW/yr |
  |---|---|---|
  | low | min of the 2023–25 and 2021–25 means | 5.98 / 21.14 / 8.48 |
  | central | max of the same | 8.25 / 26.87 / 11.56 |
  | high | best single year 2021–25 | 13.84 / 31.16 / 16.36 |

- **After 2030 (§76):** `R[y] = R[y−1] × (1 + g[y])`, with g from `growth_paths`, anchored to the first-round S0–S4
  caps (`scripts/round1_growth_paths.py`; round 1's script and data in `data/reference/round1/`).
  - **central (round 1 Quadratic Trend):** wind 2.4% → 1.8%, solar 7.2% → 3.6% over 2031–45.
  - **high (round 1 Implied Rate):** wind 8.27 / 7.33 / 6.24%, solar 14.84 / 10.78 / 8.47% by 5-year period.
  - **storage:** follows solar (PROPOSED, flagged).
  - **low and gas:** keep the scalar `growth` (PLACEHOLDER).
  - **After 2045:** the last value.
- **Storage module floor (§77):** storage R ≥ the deliverability central path in 2029 and 2030 (33.2 / 40.0 GW/yr),
  every level. Later years compound from it.

### 2.2 Tiers (`tier_sets`, `tier_set: central`)
- **central:** free to 1.3R, +15% of the group's capex to 1.75R, +50% to 2.0R (hard ceiling). Adders are marginal,
  annualised over `life_years`. The adders are UNDER REVIEW against the EPA 2025 Reference Case (+45–48% /
  +143–153%).
- **Other tier sets:** `reeds_exact` (+10% / +50%); `ipm2025` (free to 1.0R, +46% to 1.74R, +147% above, **no
  ceiling**: `upto: 1000`; storage one free unbounded band).
- **Nuclear** uses the central tiers in every level (`path_groups.nuclear.tier_set`).

### 2.3 Regional (transreg) ceilings, wind and solar in S0
- **Formula:** `max(share_r × regional_mult × 2.0 × R, floor_r)`.
  - share_r: EIA additions 2016–25.
  - regional_mult: 1.5 (PLACEHOLDER).
  - floor_r = `max(floor_min, k_stock × stock end-2025, k_peak × peak build 2010–25)` (PLACEHOLDER terms; wind 500 MW
    / 0.05 / 0.75, solar 1,000 / 0.05 / 0.75).
- **Storage and gas** are national-only by default.
- **Committed builds** above a regional ceiling raise it (case writer).

### 2.4 Derived levels (`benchmark_of`, `max_of`; `rates.derived_rate_table`)
- **reform_bp (§74):** a best-performing-state benchmark of queue completion and request-to-operation duration.
  - It mirrors round 1's trimmed top-quartile Implied Rate rule: units are states, min resolved 2,000 MW, ≥ 5
    completions, trim 10%, top 25%; storage uses solar's values.
  - Implied rate of the active queue I = Σ MW × completion / duration.
  - Regional R = (share_r + ΔI_r / I) × R_central; national R = R_central × I'/I (wind ×1.47, solar ×1.62, storage
    ×1.49).
  - Sensitivities: `scripts/reform_sensitivity.py`.
- **reform_bp_siting (§76):** reform_bp plus wind regional mult 3.0 and floor terms 1,500 MW / 0.10 / 1.5.
- **high_reform (§74):** per region and year the larger of high and reform_bp; national = the larger regional build-up.

### 2.5 Deliverability ceiling (§77–78)
- **Where it applies:** national, from `deliverability.first_year: 2029`; 2026–28 keep the module's pipeline-based
  ceilings.
- **D:** the brief's 2025 actuals (solar 27.2 GWac, wind 5.2, storage 15.8 GW), compounded annually at its growth
  table on the level's path (`level_path`: central or high). Values:

  | Path | Solar 2030 / 2045 | Wind 2030 / 2045 | Storage 2030 / 2045 |
  |---|---|---|---|
  | central | 49.9 / 74.7 | 15.0 / 25.0 | 40.0 / 65.0 |
  | high | 64.9 / 109.7 | 20.0 / 40.1 | 54.8 / 99.4 |

- **How it applies:**
  - factor = min(1, D / (2.0 × R_module));
  - **R and every regional ceiling (floors included) are multiplied by the factor**, so the national ceiling is
    2.0R = D with the tier shape kept (§78);
  - the module values stay in `module_r_data_mw_per_yr` and `module_ceiling_mw_per_yr`.
- **Not covered:** gas has no path.
- **Interconnection evidence:** D uses none (the brief's caution against double counting). Queue processing is the
  pace layer; headroom is the separate module.

### 2.6 Nuclear path group (§79)
- **Ceiling:** new nuclear (large and SMR) national ceiling from build year 2031 (the 2035 period's window):

  | Path | 2031–35 | 2036–40 | 2041+ |
  |---|---|---|---|
  | central | 0.8 | 2.5 | 4.0 GW/yr |
  | high | 2.0 | 6.0 | 10.0 GW/yr |

- **Mechanics:** R = ceiling / 2.0; no ramp (floor 1e6); life 60.
- **Earlier periods:** the case writer leaves nuclear out of periods ending before 2031. S0's `new_build_rule` still
  bans new nuclear before 2035.
- **Own level map:** high only for high and high_reform.
- **Old cap still present:** `MaxCapTag_NuclearGrowth` (looser) is untouched.

### 2.7 Ramp (Switch module; §84 evidence form)
- **Groups with evidence** (wind, solar, storage):
  - fixed base (myopic stage: the chained best rate so far): `R ≤ max(F_p, G_p × base) + committed/(2.0 × W_p)`;
  - variable base (previous period in the same solve, mode B): `R ≤ F_p + G_p × NewBuild[p−1]/W + committed term`.
  - F_p = the window mean of the brief's **low** path: wind 6.8 / 9.4 / 10.0 / 11.2 / 12.0, solar 30.1 / 34.2 / 38.0
    / 43.0 / 45.1, storage 19.0 / 23.9 / 27.9 / 32.9 / 34.9 GW/yr for 2028–45.
  - G_p = ∏(1 + g_y) on the level's deliverability path (central: wind 1.53 for 2029–30; high 1.71).
  - Written as `br_ramp_factor` and `br_ramp_floor_mw_per_yr` in `build_rate_periods.csv`.
- **Groups without evidence** (gas; nuclear off): `R ≤ (1+br_growth)^W × base + br_ramp_floor_mw + committed term`.
  - br_growth = the rate table's `growth` column: geometric mean of the growth path, or the scalar.
  - floor 1,000 MW/yr for gas (PLACEHOLDER).
- **No history:** no ramp in a chain's first stage.
- **Other module constraints:** `R ≤ data rate` (window mean); tiers `Tier_k ≤ width_k × R × W`; regional ceilings.

### 2.8 Case writer (per period, window W = period_end − period_start + 1)
- **Window means:** data rate = window mean of R; regional ceilings = window mean.
- **Committed builds:** predetermined builds dated in the window count in `NewBuild`; above 2.0 × R × W they raise R.
- **Checks:** MinCap programs are checked against the cumulative national ceilings (stop unless slack); an RPS
  warning (`rps_check`).

## 3. Levels

| Level | R (2026–30 / after) | Growth path | Deliverability path | Ramp growth path | Regional | Tiers | Nuclear |
|---|---|---|---|---|---|---|---|
| central (S0) | queue / central R0 | central | central | central | base mult and floors | central | central |
| low | queue / low R0 | scalar low (PH) | central | central | base | central | central |
| high | queue / high R0 | high | high | high | base | central | high |
| reform | as central | central | central | central | wind mult 3.0, floors 1,500 / 0.10 / 1.5 | central | central |
| reform_bp | central × queue benchmark | central | **high** | **high** | benchmark regional increments | central | **central** |
| reform_bp_siting | reform_bp | central | **high** | **high** | + wind mult 3.0 and floors | central | **central** |
| high_reform | max(high, reform_bp) by region | high | high | high | max of the two | central | high |
| high_ipm | EPA 2025 Table 4-13 Step 1 by build year; storage follows high | high (after 2036) | central | central | base | **ipm2025 (no ceiling)** | central |
| off | no build-rate files (`levels_by_period` / `level_overrides` "off"): no limit of any layer, including nuclear | — | — | — | — | — | — |

Off is used by S3, S5 and P from 2040. The gas-turbine cap is switched off separately (`gas_turbine_cap: off`).

## 4. Config keys (`build_rate/config.yaml`) and sources

| Key | What | Source / status |
|---|---|---|
| `paths` | EIA-860M Aug 2026, LBNL Queued Up, ReEDS county2zone, hierarchy | data |
| `history`, `r0_rule` | R0 windows and rules | EIA-860M; decision (b), Oct 2026 |
| `near_term` | queue cohorts, construction completion 0.9, overdue spread | Queued Up; 0.9 and overdue rule PLACEHOLDER |
| `growth_paths` | post-2030 growth, central / high | round-1 S0–S4 caps (§76); storage = solar PROPOSED |
| `growth` | scalar growth (low, gas; fallback) | PLACEHOLDER |
| `deliverability` (`first_year`, `actual_gw`, `cagr`, `level_path`, `module_floor`) | second layer, storage floor | brief "US Wind, Solar & Storage Build-Rate Limits to 2045" (6 Oct 2026), FOR REVIEW |
| `ramp` (`evidence`, `floor_path`) | ramp floor and growth | same brief (§84), FOR REVIEW |
| `path_groups.nuclear` | nuclear ceiling | NRC permits, Fermi COLA, Westinghouse deal, DOE Liftoff (upper bound), EIA history (§79), FOR REVIEW |
| `ramp_floor_mw` | groups without evidence (gas 1,000; nuclear 1e6) | PLACEHOLDER (gas) |
| `regional_mult`, `regional_floor`, `floor_basis` | transreg ceilings | PLACEHOLDER |
| `rps_check` | warning threshold and CFs | PLACEHOLDER |
| `life_years` | adder annualisation | PLACEHOLDER (nuclear 60) |
| `levels` | the table above | decisions §73–79 |
| `reform_benchmark` | reform_bp's rule | round-1 Implied Rate rule (§74), FOR REVIEW |
| `tier_set`, `tier_sets`, `group_tier_overrides`, `tier_adders_last_year`, `gas_tiers` | bands and adders | ReEDS / EPA Platform v6; central adders UNDER REVIEW vs EPA 2025 |
| `ipm` | EPA 2025 Table 4-13 | EPA 2025 Reference Case |
| `horizon_last_year` | 2050 | — |

## 5. Commits (003b062 onward)

| Commit | § | Build-rate content |
|---|---|---|
| 003b062 | 74 | reform_bp (best-performing-state queue benchmark; `reform_benchmark`, `queue_metrics`, `reform_uplift`, `derived_rate_table`, recursive `rate_tables`); high_reform redefined as max(high, reform_bp); queue state column; `reform_sensitivity.py` |
| 9e2045b | 75 | gas-turbine cap "off" (`drop_tag_rows`); S-set rows use build rate high / high_reform / off |
| b407692 | 76 | `growth_paths` from round 1; reform_bp_siting; derived levels' own floors; `round1_growth_paths.py` |
| fba5436 | 77 | deliverability layer, storage 2029–30 floor, `deliverability_report.py` (tier truncation, reverted in §78) |
| 7efe91a | 78 | deliverability from 2029; R scaled so D = 2.0R (tiers kept); case-writer truncation removed |
| 37c66ef, 0e16305 | 79 | nuclear path group; `switch_group` maps nuclear; case writer skips path groups before their first year; S0 groups + nuclear |
| cbbc907 | 84 | ramp from the brief (`ramp`, `ramp_window`, case-writer columns, Switch module optional per-period parameters); `ramp_report.py` |

Earlier: 291cf9e (§73) first defined high_reform and "off" by period.

## 6. Flags (for the design review)

**Inconsistent or likely wrong:**
1. **high_ipm × deliverability (bug, verified on the local tables).** ipm2025's top band is `upto: 1000`, so the
   module ceiling is 1000 × R, and the deliverability factor (≈ 0.001) scales R to about D/1000.
   - Example: wind 2030 drops from 16.5 GW/yr to 15 MW/yr.
   - The free band is almost zero, so nearly all build pays the +46% / +147% adders.
   - Every regional ceiling and floor is scaled by the same ≈ 0.001.
   - high_ipm is unusable with the deliverability layer as it stands.
2. **The ramp bounds the pace R, not built capacity.** Build can reach 2.0R with adders, so the effective ramp is up to
   2× the brief's form ("new build ≤ max(floor, (1+g) × previous build")).
3. **The ramp floor is a build quantity applied to R.** For central it exceeds the deliverability-scaled R in every
   period (wind 2030: floor 9.4 vs R 6.8), so the S0 ramp can never bind. Effectively inactive.
4. **Level-path mapping is mixed.**
   - reform_bp and reform_bp_siting use central R and growth but the high deliverability and ramp paths, and central
     for nuclear.
   - high_ipm uses high R but the central deliverability and ramp paths.
   - reform (old) uses central everywhere.
5. **Wherever D binds**, high, reform_bp and high_reform have the same national R and ceiling. reform_bp's queue
   benchmark then matters only regionally and in 2026–28.
6. **Mode B** uses the additive ramp `F + G × base` for a variable base, looser than the max by up to F.
7. **Regional floors are scaled** by the deliverability factor, so they can fall below `floor_min_mw`.
8. **The deliverability base year** uses the brief's 2025 actuals (27.2 / 5.2 / 15.8 GW), not the module's EIA-860M
   2025 additions (29.6 / 6.3 / 16.4).
9. **Ceilings step down from 2028 to 2029** (S0 wind 16.5 → 12.1, solar 66.8 → 44.2 GW/yr) where D starts.
10. **The storage 2029–30 floor** lifts storage's post-2030 module R by about 3.5×. That is invisible where D binds
    (every storage level) but enters the tier edges via the scaling.
11. **br_growth** (the growth column) is no longer used for wind, solar and storage when the ramp evidence is on;
    only for gas.
12. **MaxCapTag_NuclearGrowth** remains alongside the nuclear path group. It's looser, so harmless, but there are two
    mechanisms for one limit.

**Placeholders:**
- construction completion 0.9 and the overdue rule;
- scalar `growth` (low, gas);
- gas ramp floor 1,000 MW/yr;
- `regional_mult` 1.5 and all `regional_floor` terms;
- `rps_check`;
- `life_years`;
- central tier adders (under review vs EPA 2025).

**Proposals awaiting review:**
- storage growth following solar;
- reform_bp's storage proxy (solar's values) and every `reform_benchmark` choice;
- the deliverability and nuclear paths;
- the ramp evidence form.
