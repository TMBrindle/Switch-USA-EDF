# Build-rate supply curves

A data-anchored, tiered limit on how fast new onshore wind, solar, storage (and, opt-in, gas) can
be built. It replaces the trend-based `MaxCapTag_WindGrowth` / `MaxCapTag_SolarGrowth` growth caps
(cumulative stock caps such as `growth_caps/S0.csv` on the fedpol branches) with:

* a national build rate R (MW/yr) per technology group and period, anchored to observed EIA-860M
  additions and the LBNL interconnection-queue pipeline;
* cost bands above R (marginal adders: only the MW inside a band pay it) and a hard ceiling;
* a ramp bound tying each period's rate to the previous period's build;
* regional (transreg) ceilings from recent regional shares, with a minimum per region.

Why: in the 2035 S0 tests, removing the growth caps let the model build 270–360 GW of new onshore
wind against ~5–9 GW/yr of real build, and the wind share of new wind + solar came out ~0.50 against
0.28 in EIA 2021–25. Unsubsidised wind LCOE is at or below solar's, yet solar outbuilds wind ~5:1,
so the binding limits are non-cost (supply chain, siting, permitting, lead times). This module makes
them explicit, sourced and adjustable as policy levers ("permitting reform", "supply-chain shock").

Code: `build_rate/` (pipeline, pandas only), `switch/study_modules/build_rate.py` (Switch module),
`build_rate/brc/switch_case.py` (called by `pg_to_switch.py`), `pg/settings/build_rate.yml`,
`build_rate` axis in `pg/settings/scenario_management.yml`. Change log: CHANGES.md §39.

## Formulation

For group G (wind_onshore, solar, storage, gas) and investment period p with build window
W_p = period_end − period_start + 1 years:

```
NewBuild[G,p] = Σ BuildGen over the group's projects with a build year in the window
                (new builds in p + predetermined builds dated inside the window)
R[G,p]       ≤ R_data[G,p]                                 data rate (mean over the window)
R[G,p]       ≤ (1+growth_G)^W_p · NewBuild[G,p−1]/W_{p−1} + floor_G (+ committed/(top·W_p))
Σ_k Tier[G,p,k] = NewBuild[G,p]
Tier[G,p,k]  ≤ width_k · R[G,p] · W_p                      bands 1.3 / 0.45 / 0.25 → ceiling 2.0·R
Σ BuildGen in region r ≤ max(share_r · m_G · 2.0 · R_data, regional floor_G) · W_p
cost = Σ_k Tier · adder_k  (overnight $), annualised with crf(r, life_G), charged while the vintage lives
```

* Marginal charging keeps the model an LP (IPM charges its adder on all capacity in a run year).
* R is a variable: the model takes the smaller of the data rate and the ramp bound.
* Ramp in perfect foresight uses the previous period only (a "best rate so far" ratchet would make
  the bound non-convex). In myopic runs, `prepare_next_stage.chain_build_rate_inputs` writes
  `build_rate_prev_build.chained.<case>.csv` with the best rate achieved so far, and the next stage's
  first period uses it, so the bound never falls below it.
* Regional ceilings use R_data (a parameter), not the variable R, to stay linear.
* Committed (predetermined) builds count toward the rate. If they exceed a ceiling, the case writer
  raises that ceiling to the committed amount and warns.
* Optional `ceiling_slack_cost` ($/kW, off by default) allows builds above the ceiling at that cost,
  for diagnosing infeasibility.

Outputs: `build_rate_tiers_built.csv` (MW per band, whether full), `build_rate_duals.csv` (duals of
each band, the data rate, the ramp and regional limits, converted to overnight-equivalent $/kW), and
`build_rate_new_build.csv` and `build_rate_costs.csv`. `BuildRateCosts` appears in `costs_itemized.csv`.

## Data (build_rate/, `python -m brc.cli run`)

| Step | Source | Method |
|---|---|---|
| Base rates 2015–25 | EIA-860M (Aug 2026), Operating + Retired sheets | nameplate MW by operating year, group, transreg (county → ReEDS BA → transreg; 99.0% of MW mapped) |
| R0 | base rates | low = mean 2023–25, central = mean 2021–25, high = max 2021–25 |
| Near-term to 2030 | LBNL Queued Up (through 2025; read with `git show` from `origin/tom/interconnection-headroom`) | active MW with an executed IA or under construction × completion rate × phased by COD |
| Completion rates | same file | IA-executed requests queued ≤ 2018 and resolved: share of MW that reached operation (wind 0.46, solar 0.65, storage 0.87, gas 0.66). Construction: **PLACEHOLDER 0.9** (the file reclassifies completed projects as IA Executed, so it has no resolved Construction history) |
| COD phasing | same file | COD = max(proposed year, IA year + median IA-to-COD years of operational requests queued ≥ 2015: wind 1, gas 1, solar 2, storage 2). Proposed CODs in the file are revised as projects progress, so slip can't be measured from them. Overdue requests (COD already passed: 18 GW wind, 57 GW solar, 11 GW storage, 16 GW gas) are spread evenly over 2026–30 (**PLACEHOLDER rule**) |
| R_data | above | years ≤ 2030: max(queue-based, R0); later: R_data(2030) × (1+growth)^(y−2030) |
| Growth after 2030 | **PLACEHOLDER** low/central/high per group | candidate sources: NREL ATB / Standard Scenarios, ReEDS absolute limits, WoodMac/SEIA/ACP outlooks, LBNL completion trends |
| Regional shares | EIA 2016–25 | share of national additions by transreg |
| Regional floors | **PLACEHOLDER** | 500 MW/yr wind, 1,000 solar, 500 storage, 500 gas per transreg (reform: 1,500 wind) |

## Tiers and their sources

| Band (× R) | Adder (central) | ReEDS-exact (`tier_set: reeds_exact`) | Gas (opt-in) |
|---|---|---|---|
| 0 – 1.3 | 0 | 0 | 0 |
| 1.3 – 1.75 | +15% of capex | +10% | +44% |
| 1.75 – 2.0 (ceiling) | +50% | +50% | +140% |

* Bands: ReEDS growth bins 1.3 / 1.75 / 2.0 (`inputs/growth_constraints/growth_bin_size_mult.csv`,
  ReEDS-2.0 commit 2f583ff5; penalties 0 / 0.1 / 0.5 / 1000 × capex in `growth_penalty.csv`).
  ReEDS's growth penalties are off by default (`GSw_GrowthPenalties = 0`) and are relative to the
  model's own previous build, per state.
* +15%: between ReEDS's +10% and EPA Platform v6 Post-IRA 2022 Step 2 (+16–25% of capex for wind
  and solar, 2028–2035). +50%: ReEDS, and the low end of v6 Step 3 (+52–78%). **Under review:** the
  current EPA 2025 Reference Case adders are about three times larger (table below).
* Gas: EPA 2025 Table 4-13 CC + CT Step 2 = +40–44%, Step 3 = +127–141% of Table 4-12 capex
  (multi-shaft CC / industrial-frame CT), unchanged from the 2023 case relative to CT.
* $/MW per case: adder fraction × the median new-build overnight cost of the group's projects in that
  case and period (`gen_build_costs.csv`).

### Comparison with IPM (EPA 2025 Reference Case)

Sources: EPA 2025 Reference Case incremental documentation, Table 4-13 (short-term capital cost
adders, **2022$**), Table 4-15 (renewable capex) and Table 4-12 (conventional capex); EPA 2023
Reference Case documentation, Section 4.4.3 (method) and Table 2-1 (run years, p. 2-6). Method: Step 1
is the new capacity that can be built in a run year with no adder; above it, the Step 2 or Step 3
adder applies to **all** capacity built in that run year; adders stop after 2035. Run years 2028 /
2030 / 2035 represent 2028–29 / 2030–31 / 2032–37.

| | 2028 | 2030 | 2035 |
|---|---|---|---|
| Wind Step 1, GW per calendar year (2025) | 34.3 | 16.5 | 13.8 |
| Solar Step 1, GW per calendar year (2025) | 92.4 | 43.2 | 29.5 |
| CC+CT Step 1, GW per calendar year (2025) | 45.8 | 22.1 | 18.4 |
| Wind Step 2 / Step 3 adder, % of capex (2025) | 48 / 153 | 47 / 148 | 46 / 145 |
| Solar Step 2 / Step 3 adder, % of capex (2025) | 48 / 151 | 46 / 147 | 45 / 143 |
| CC+CT Step 2 / Step 3, % of CT capex (2025) | 44 / 141 | 44 / 139 | 44 / 139 |
| Wind Step 2 / Step 3, % of capex (v6, historical) | 22 / 70 | 19 / 61 | 16 / 52 |
| Solar Step 2 / Step 3, % of capex (v6, historical) | 25 / 78 | 20 / 64 | 18 / 59 |

Step 2's upper bound is 1.74 × Step 1 in every 2030/2035 row, close to our 1.75R band edge.

Against central (national, GW/yr; cumulative 2028–35 in brackets):

* **Wind:** central's free band (1.3R) is 10.7–13.7 against IPM Step 1's 34.3 / 16.5 / 13.8 (94 vs
  157 GW cumulative). Central's ceiling (145 GW cumulative) is below IPM's Step 1 alone, so central
  is tighter on volume through 2035.
* **Gas:** central is far tighter (free band 68 vs 210 GW cumulative).
* **Solar:** central is tighter through 2031. From 2032 central's free band (30.3 → 35.1 GW/yr)
  exceeds IPM's Step 1 (29.5), and by 2035 cumulative central's ceiling (397 GW) roughly equals
  IPM's Step 1 (389 GW).
* **Adders:** central's +15% / +50% are about a third of IPM 2025's +45–48% / +143–153%. They are
  marginal, while IPM's apply to the whole run year's build. IPM has no ceiling (Step 3 has no
  limit) and no adders after 2035; central keeps its bands and ceiling in every period.

So central is deliberately tighter than IPM on volume for wind and gas (anchored to observed
EIA rates and the queue pipeline, because the S0 tests over-built wind against history), but
looser on price inside its bands, and not tighter for solar after 2031.

The 45X step-width scalars (Section 4.4.3 of 2023: +21% / +29% / +50%) are already embedded in the 2023
Table 4-13: wind and solar 2035/2030 Step 1 ratio 2.907 = 2.5 × 1.50/1.29, against 2.5 for CC+CT.
In 2025 wind's ratio is 2.500, and the 2025 document does not restate the scalars, so `high_ipm`
uses the published 2025 bounds without extra scaling (`ipm.apply_45x_scalars: false`).

`high_ipm` sets R = Table 4-13 Step 1 per calendar year (Table 2-1 mapping; 2026–27, before IPM's
horizon, take the 2028 run year; after 2037 R grows at the `high` rate). Storage has no Table 4-13
row and follows `high`. With R = Step 1, our free band is 1.3 × IPM's and our 1.75R edge matches
IPM's Step 2 bound.

### Spur and access adders (IPM Tables 4-38 / 4-42): not adopted

They are spur-line / resource-access capital-cost adders by resource and cost class, and PowerGenome
already includes spur costs (`spur_capex`), so adding them would double count. They are evidence for
applying the siting lever to wind first: the median wind access adder is $103/kW (10% of the Platform v6
2028 base capex, from the Table 4-38 / 4-42 files supplied earlier; 90th percentile $915) against $11/kW for solar (1.3%; 90th percentile $344), about 9× at the
median. The `reform` level therefore relaxes wind's regional multiplier (1.5 → 3.0) and floor
(500 → 1,500 MW/yr).

## Levels (scenario axis `build_rate`)

| Level | R0 | Growth | Other |
|---|---|---|---|
| off | — | — | module inert |
| low | mean 2023–25 | low | |
| central | mean 2021–25 | central | |
| high | max 2021–25 | high | |
| reform | mean 2021–25 | central | wind regional multiplier 3.0, floor 1,500 MW/yr |
| high_ipm | EPA 2025 Table 4-13 Step 1 per calendar year (Table 2-1 mapping) | high (after 2037; storage) | |

Note: with the decision-(b) rules, `low` R0 is above `central` for solar and storage, whose largest
years are the most recent (solar 26.9 vs 21.1 GW/yr, storage 11.6 vs 8.5). Near-term years use the
queue-based rate whenever it is higher.

## Interactions

* **MaxCap growth caps.** With build_rate on, release `MaxCapTag_WindGrowth` / `SolarGrowth` for the
  same groups (e.g. `policies: S0_uncapped`); both limit the same builds otherwise.
* **Gas.** Opt-in (`groups: [..., gas]`) and off by default. The case build raises an error if gas is
  on while `MaxCapTag_GasTurbineSupply` is still in `max_cap_requirements.csv`. Nuclear
  (`MaxCapTag_NuclearGrowth`) and offshore wind (`MaxCapTag_Ban`, `offshore_wind_policy`) are unchanged.
* **MinCap.** The case build checks that each MinCap program whose generators are all in one group
  needs no more new build than the cumulative national ceilings allow; it stops with a message unless
  `ceiling_slack_cost` is set. The RPS ACP (on `tom/ic-test-fedpol`) is unaffected.
* **Interconnection headroom** (`tom/interconnection-headroom`). Both modules limit the same new
  builds: headroom prices network capacity by zone, build rate prices national development
  throughput. Neither cost term contains the other. Possible overlap: LBNL network-upgrade costs
  partly reflect queue congestion, which is itself a rate effect. To limit it, the build-rate adders
  come from IPM and ReEDS, never from LBNL interconnection costs. Both use Queued Up, but for
  different quantities (headroom: county location; build rate: stage and COD).

## Settings

`pg/settings/build_rate.yml`:

```yaml
build_rate:
  enabled: false
  level: central            # low | central | high | reform | high_ipm
  tables_dir: build_rate/outputs
  groups: [wind_onshore, solar, storage]   # add gas only with MaxCapTag_GasTurbineSupply released
  regional: true
  ceiling_slack_cost: null  # $/kW
```

The `build_rate` axis touches only `build_rate.enabled` and `build_rate.level`, so it never shares a
flattened key with `policies` (carbon settings, `max_cap_req_fn`) in the same scenario row. It needs
a `build_rate` column in `scenario_inputs.csv` (not added on this branch; see the handoff below).

Commands (from `build_rate/`): `bash scripts/fetch_data.sh`, `python -m brc.cli run`, `pytest -q`.

## Results (1 Oct 2026 inputs: EIA-860M Aug 2026, Queued Up through 2025)

National R and bands, GW/yr (ceiling = 2.0 × R), and cumulative 2026–y new build, GW:

| Level | Group | R 2028 / 2030 / 2035 | Ceiling 2035 | Cum. free band (1.3R) to 2028 / 2030 / 2035 | Cum. ceiling to 2035 | S0 new build since 2025 (2028 / 2030 / 2035) |
|---|---|---|---|---|---|---|
| central | wind_onshore | 8.2 / 8.2 / 10.5 | 21.1 | 32 / 54 / 116 | 178 | 38.5 / 61.6 / 124.2 |
| central | solar | 33.4 / 21.1 / 27.0 | 54.0 | 145 / 200 / 359 | 552 | 90.8 / 168.6 / 413.7 |
| central | storage | 20.8 / 8.6 / 12.7 | 25.4 | 93 / 117 / 188 | 289 | — |
| low | wind_onshore | 6.0 / 6.0 / 6.6 | 13.2 | 26 / 42 / 83 | 128 | |
| high | wind_onshore | 13.8 / 13.8 / 22.3 | 44.6 | 54 / 90 / 211 | 324 | |

S0's caps are cumulative stock (wind 198.2 / 221.3 / 283.9 GW, solar 243.7 / 321.5 / 566.6 GW);
the new-build column subtracts the end-2025 EIA-860M operating stock (wind 159.7 GW incl. offshore,
which the WindGrowth tag also covers; solar PV 152.9 GW), ignoring retirements before 2035.

## VM test handoff

Real-case tests run on the VM by merging this branch into `tom/ic-test-fedpol`, where S0,
`growth_caps/`, `max_cap_req_fn`, `S0_uncapped` and the RPS ACP exist.

1. Merge (on the VM, in the repo):

   ```bash
   git fetch origin tom/build-rate tom/ic-test-fedpol
   git checkout tom/ic-test-fedpol
   git merge --no-ff origin/tom/build-rate
   ```

   Expected conflicts are additive only: CHANGES.md (keep both; this is §39),
   `pg/settings/scenario_management.yml` (keep both axes), `switch/modules.txt` (keep both lines),
   `switch/study_modules/prepare_next_stage.py` (keep `chain_ic_inputs` and
   `chain_build_rate_inputs`; each is called once from `post_solve`), and `pg_to_switch.py` (the
   `br_case` import and two short blocks; keep the headroom lines too).
2. Build the tables: `cd build_rate && bash scripts/fetch_data.sh && python -m brc.cli run`.
3. Add a `build_rate` column to `pg/extra_inputs/scenario_inputs.csv`: `off` in every existing row.
   Then add these rows (they copy `s4x1_S0unc_2035_icoff` / `_icon` and set build_rate):

   ```
   s4x1_S0br_2035_icoff,2035,s4x1,firm,edf_epri_med,yes,S0_uncapped,RGGI10,none,hist5_high_gas,none,full,no,1,constrained,zero,yes,yes,yes,yes,none,no_wind_solar,none,none,blocked_2030_coal_gas,capped_2025,none,none,section232_2025,off,central
   s4x1_S0br_2035_icon,2035,s4x1,firm,edf_epri_med,yes,S0_uncapped,RGGI10,none,hist5_high_gas,none,full,no,1,constrained,zero,yes,yes,yes,yes,none,no_wind_solar,none,none,blocked_2030_coal_gas,capped_2025,none,none,section232_2025,on,central
   ```

   `S0_uncapped` is the `policies` preset on `tom/ic-test-fedpol` (S0 carbon settings with
   `max_cap_req_fn: growth_caps/uncapped.csv`), so this is "S0_buildrate": S0 with the wind/solar
   growth caps released and build_rate central. GasTurbineSupply and NuclearGrowth stay as they are.
4. Runs (2035, s4x1), each with the corrected wind profiles via the **windloss profile alias**
   (`<windloss alias: not on any branch; fill in from the VM>`):
   * `s4x1_S0br_2035_icoff`: uncapped S0 + build_rate central, headroom off;
   * `s4x1_S0br_2035_icon`: the same with interconnection headroom on;
   * reference: `s4x1_S0_2035_icoff` (S0 caps) and `s4x1_S0unc_2035_icoff` (uncapped).
5. Compare new onshore wind and solar by 2035 (target: wind share of new wind + solar near the 0.28
   EIA 2021–25 value), `build_rate_tiers_built.csv` (which bands fill), `build_rate_duals.csv` (ceiling
   price in $/kW), and `costs_itemized.csv` (BuildRateCosts).

## Placeholders (all marked in build_rate/config.yaml)

construction completion rate (0.9), overdue-pipeline rule (spread evenly 2026–30), growth rates
after 2030 (low/central/high), ramp floors, regional multipliers and floors, and amortisation lives. The central adders (+15% / +50%) are
under review against the EPA 2025 Reference Case adders.
