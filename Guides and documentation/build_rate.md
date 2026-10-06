# Build-rate supply curves

A data-anchored, tiered limit on how fast new onshore wind, solar, storage (and, opt-in, gas) can
be built. It replaces the trend-based `MaxCapTag_WindGrowth` / `MaxCapTag_SolarGrowth` growth caps
(cumulative stock caps such as `growth_caps/S0.csv` on the fedpol branches) with:

* a national build rate R (MW/yr) per technology group and period, anchored to observed EIA-860M
  additions and the LBNL interconnection-queue pipeline;
* cost bands above R (marginal adders: only the MW inside a band pay it) and a hard ceiling;
* a ramp bound tying each period's rate to the previous period's build;
* regional (transreg) ceilings from recent regional shares, with a minimum per region, for the
  siting-limited groups only (wind and solar by default; storage and gas are national-only).

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
R[G,p]       ≤ R_data[G,p] = (1/W_p) · Σ_{y∈W_p} R_data[G,y]   window sum of the annual rates / W
R[G,p]       ≤ (1+growth_G)^W_p · NewBuild[G,p−1]/W_{p−1} + floor_G (+ committed/(top·W_p))
Σ_k Tier[G,p,k] = NewBuild[G,p]
Tier[G,p,k]  ≤ width_k · R[G,p] · W_p   (= width_k · Σ_{y∈W_p} R_data[G,y] at R = R_data)
Σ BuildGen in region r ≤ Σ_{y∈W_p} max(share_r · m_G · 2.0 · R_data[G,y], floor_G,r)
floor_G,r = max(floor_min_G, k_stock_G · existing MW_r end-2025, k_peak_G · peak annual build_r 2010–25)
cost = Σ_k Tier · adder_k  (overnight $), annualised with crf(r, life_G), charged while the vintage lives
```

* Cumulative limits are always the sum of the annual values over the build window, never R at the
  period year × W_p. A single 2035 period (2026–35) and a 2028 / 2030 / 2035 run get the same
  cumulative limits (central wind at 1.0R: 89.2 GW, not R[2035] × 10 = 105.3 GW). Tests:
  `test_toy_cumulative_limits_window_sum_single_vs_three_periods` (case writer on the 3-zone toy,
  national, ceiling and regional limits with floors) and `test_toy_solve_ceiling_is_window_sum`
  (Switch solve: the ceiling binds at 2.0 × the window sum).
* Marginal charging keeps the model an LP (IPM charges its adder on all capacity in a run year).
* R is a variable: the model takes the smaller of the data rate and the ramp bound.
* Ramp in perfect foresight uses the previous period only (a "best rate so far" ratchet would make
  the bound non-convex). In myopic runs, `prepare_next_stage.chain_build_rate_inputs` writes
  `build_rate_prev_build.chained.<case>.csv` with the best rate achieved so far, and the next stage's
  first period uses it, so the bound never falls below it.
* Regional ceilings use R_data (a parameter), not the variable R, to stay linear.
* Regional ceilings apply only to `regional_groups` (default `[wind_onshore, solar]`). Storage and gas
  are national-only: they are limited by supply chain, not local siting. On the VM, regional storage
  ceilings with 500 MW/yr floors held central to 36 GW of storage against an EIA pace of about 83 GW,
  because 2021–25 storage build was concentrated in CAISO, ERCOT and WestConnect, so share-based
  ceilings left little room elsewhere. Add a group to `regional_groups` to restore its ceilings.
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
| R0 ("sustained demonstrated rate") | base rates | low = min(mean 2023–25, mean 2021–25); central = max(mean 2023–25, mean 2021–25); high = best single year 2021–25. The pipeline stops unless low ≤ central ≤ high for every group |
| Near-term to 2030 | LBNL Queued Up (through 2025; read with `git show` from `origin/tom/interconnection-headroom`) | active MW with an executed IA or under construction × completion rate × phased by COD |
| Completion rates | same file | IA-executed requests queued ≤ 2018 and resolved: share of MW that reached operation (wind 0.46, solar 0.65, storage 0.87, gas 0.66). Construction: **PLACEHOLDER 0.9** (the file reclassifies completed projects as IA Executed, so it has no resolved Construction history) |
| COD phasing | same file | COD = max(proposed year, IA year + median IA-to-COD years of operational requests queued ≥ 2015: wind 1, gas 1, solar 2, storage 2). Proposed CODs in the file are revised as projects progress, so slip can't be measured from them. Overdue requests (COD already passed: 18 GW wind, 57 GW solar, 11 GW storage, 16 GW gas) are spread evenly over 2026–30 (**PLACEHOLDER rule**) |
| R_data | above | years ≤ 2030: max(queue-based, R0); later: R_data(2030) × (1+growth)^(y−2030) |

**Queue thinning.** The IA-based near-term rate falls after 2028 (national wind 5.8 → 2.9 GW, solar 33.4 → 12.9 GW, storage 20.8 → 8.6 GW, 2028 → 2030) because projects that will be built in 2029–30 have mostly not yet reached an executed IA or construction. It reflects the stage the queue file can see, not a forecast of falling build, so near-term R is max(queue-based, R0) and R0 sets the rate once the visible pipeline thins.

| Growth after 2030 | **PLACEHOLDER** low/central/high per group | candidate sources: NREL ATB / Standard Scenarios, ReEDS absolute limits, WoodMac/SEIA/ACP outlooks, LBNL completion trends |
| Regional shares | EIA 2016–25 | share of national additions by transreg |
| Regional floors | EIA-860M stock and additions; coefficients **PLACEHOLDER** | floor_r,G = max(floor_min, k_stock × capacity in service at end-2025 (Operating sheet plus units retired after 2025), k_peak × largest annual addition 2010–25), per transreg (`floor_basis.csv`). Wind 500 MW/yr / 0.05 / 0.75; solar 1,000 / 0.05 / 0.75; storage 500 / 0.10 / 0.75; gas flat 500. Reform, wind: 1,500 / 0.10 / 1.5 |

## Tiers and their sources

| Band (× R) | Adder (central) | ReEDS-exact (`tier_set: reeds_exact`) | Gas (opt-in) |
|---|---|---|---|
| 0 – 1.3 | 0 | 0 | 0 |
| 1.3 – 1.75 | +15% of capex | +10% | +44% |
| 1.75 – 2.0 (ceiling) | +50% | +50% | +140% |

`high_ipm` uses IPM's own shape (`tier_set: ipm2025`, set on the level):

| Band (× R, R = IPM Step 1 per build year) | Wind, solar | Gas | Storage |
|---|---|---|---|
| 0 – 1.0 | 0 | 0 | 0, unbounded (IPM has no storage row) |
| 1.0 – 1.74 (IPM Step 2 bound) | +46% | +44% | — |
| above 1.74, no hard ceiling | +147% | +140% | — |

No adders on build years after 2036 (end of the 2035 run year's build window; a period straddling
2036 pays the adder pro rata to its window years up to 2036). Charged marginally, which keeps the
model an LP; IPM's text (2023 Section 4.4.3) charges the Step 2 or Step 3 adder on **all** capacity
built in the run year once Step 1 is exceeded. With no top band edge, `high_ipm`'s regional ceilings
(share × m × top × R) do not bind; IPM has no regional build-rate limit either.

* Bands: ReEDS growth bins 1.3 / 1.75 / 2.0 (`inputs/growth_constraints/growth_bin_size_mult.csv`,
  ReEDS-2.0 commit 2f583ff5; penalties 0 / 0.1 / 0.5 / 1000 × capex in `growth_penalty.csv`).
  ReEDS's growth penalties are off by default (`GSw_GrowthPenalties = 0`) and are relative to the
  model's own previous build, per state.
* Central +15%: between ReEDS's +10% and EPA Platform v6 Post-IRA 2022 Step 2 (+16–25% of capex
  for wind and solar, 2028–2035). +50%: ReEDS, and the low end of v6 Step 3 (+52–78%). The current
  EPA 2025 Reference Case adders are about three times larger; they are used only by `high_ipm`.
* Gas: EPA 2025 Table 4-13 CC + CT Step 2 = +40–44%, Step 3 = +127–141% of Table 4-12 capex
  (multi-shaft CC / industrial-frame CT), unchanged from the 2023 case relative to CT.
* $/MW per case: adder fraction × the median new-build overnight cost of the group's projects in that
  case and period (`gen_build_costs.csv`).

### Comparison with IPM (EPA 2025 Reference Case)

**Sources.** EPA 2025 Reference Case incremental documentation: Table 4-13 (short-term capital cost
adders and bounds, **2022$**), Table 4-15 (renewable capex), Table 4-12 (conventional capex). EPA
2023 Reference Case documentation: Section 4.4.3 (method) and Table 2-1 (run years, p. 2-6). Method:
Step 1 is the new capacity a run year can add with no adder; above it the Step 2 or Step 3 adder
applies to all capacity built in that run year; no adders after 2035.

**Source caveat (Table 4-12).** The 2025 document captions Table 4-12 "EPA 2023 Reference Case",
but its values differ from the 2023 document's Table 4-12 (e.g. CC multi-shaft 2028 $732 vs
$989/kW, CT industrial frame $694 vs $717/kW). It is treated as the 2025 table. Only the gas adder
percentages depend on it.

**Build windows.** Table 2-1 maps calendar years to run years for dispatch and cost accounting
(2028 = 2028–29, 2030 = 2030–31, 2035 = 2032–37), but the Step 1 bounds scale with a different
set of build years. Across every 2025 row:

| Row | Step 1 2028/2030 | Step 1 2035/2030 |
|---|---|---|
| Coal steam, fuel cell | 2.000 | 2.500 |
| Biomass / CC+CT / onshore wind / landfill gas | 2.02 / 2.07 / 2.07 / 2.14 | 2.500 |
| Solar PV / solar thermal / nuclear / hydro / geothermal | 2.14 / 1.93 / 1.93 / 2.08 / 2.37 | 2.05 |

So the 2028 bound covers 4 build years (2026–29: the first run year absorbs new builds from the
start of the horizon), 2030 covers 2 (2030–31) and 2035 covers 5 (2032–36), not Table 2-1's 6.
`ipm.run_year_span: {2028: 4, 2030: 2, 2035: 5}` from 2026. The rows at 2.05 have a ~18% lower
per-year rate in 2035 over the same window. Per build year that gives near-flat rates:

| GW/yr | 2026–29 | 2030–31 | 2032–36 |
|---|---|---|---|
| Wind Step 1 | 17.1 | 16.5 | 16.5 |
| Solar PV Step 1 | 46.2 | 43.2 | 35.4 |
| CC+CT Step 1 | 22.9 | 22.1 | 22.1 |

**Adders (% of 2025 base capex).**

| | 2028 | 2030 | 2035 |
|---|---|---|---|
| Wind Step 2 / Step 3 (2025) | 48 / 153 | 47 / 148 | 46 / 145 |
| Solar Step 2 / Step 3 (2025) | 48 / 151 | 46 / 147 | 45 / 143 |
| CC+CT Step 2 / Step 3 vs CT frame (vs CC multi-shaft) | 44 / 141 (42 / 134) | 44 / 139 (41 / 131) | 44 / 139 (40 / 127) |
| Wind Step 2 / Step 3 (v6, historical) | 22 / 70 | 19 / 61 | 16 / 52 |
| Solar Step 2 / Step 3 (v6, historical) | 25 / 78 | 20 / 64 | 18 / 59 |

Step 2's bound is 1.74 × Step 1 in every 2030 and 2035 row (the `ipm2025` band edge).

**45X.** Section 4.4.3 (2023) widens renewable steps by 21% / 29% / 50% in 2028 / 2030 / 2035; the
2023 table already embeds it (wind and solar 2035/2030 = 2.907 = 2.5 × 1.50/1.29). The 2025 wind
row is at 2.500 and the document does not restate the scalars, so the published bounds are used.

**Central against IPM** (national, cumulative 2026–35, GW):

| | Central free (1.3R) | Central 1.75R | Central ceiling (2.0R) | IPM Step 1 | IPM Step 2 bound |
|---|---|---|---|---|---|
| Wind | 116 | 156 | 178 | 168 | 292 |
| Solar | 417 | 562 | 642 | 413 | 718 |
| Gas | 84 | 113 | 130 | 224 | 390 |

* **Wind:** central is tighter than IPM. Central's free band (10.7–13.7 GW/yr) is below IPM's Step
  1 (16.5–17.1) in every year, and central's hard ceiling (178 GW) is about IPM's no-adder band.
* **Gas:** central is much tighter (free band 84 vs 224 GW).
* **Solar:** central is **not** tighter. Its free band about equals IPM's Step 1 cumulatively (417
  vs 413 GW): above IPM in 2026–27 and from 2031 (e.g. 44.6 vs 35.4 GW/yr in 2035), below it in
  2028–30. Central's ceiling (642 GW) is below IPM's Step 2 bound, and IPM has no ceiling.
* **Price:** central's +15% / +50% are about a third of IPM 2025's adders, but central keeps its
  bands and ceiling in every period, while IPM has no adders after 2035.

Central is deliberately tighter than IPM on volume for wind and gas: it is anchored to observed
EIA-860M rates and the queue pipeline, because the S0 tests over-built wind against history. For
solar, the recent record (26.9 GW/yr mean 2023–25) already reaches IPM's no-adder band, so the two
agree on volume.

`high_ipm` sets R = Step 1 per build year (2026–27 take the 2028 window's rate; after 2036 R grows
at the `high` rate, and the bands are free). Storage has no Table 4-13 row, so it keeps `high`'s R
with one free unbounded band.

### Spur and access adders (IPM Tables 4-38 / 4-42): not adopted

They are spur-line / resource-access capital-cost adders by resource and cost class, and PowerGenome
already includes spur costs (`spur_capex`), so adding them would double count. They are evidence for
applying the siting lever to wind first: the median wind access adder is $103/kW (10% of the Platform v6
2028 base capex, from the Table 4-38 / 4-42 files supplied earlier; 90th percentile $915) against $11/kW for solar (1.3%; 90th percentile $344), about 9× at the
median. The `reform` level therefore relaxes wind's regional multiplier (1.5 → 3.0) and scales up
all three floor terms (floor_min 500 → 1,500 MW/yr, k_stock 0.05 → 0.10, k_peak 0.75 → 1.5).

## Levels (scenario axis `build_rate`)

| Level | R0 | Growth | Other |
|---|---|---|---|
| off | — | — | module inert |
| low | min(mean 2023–25, mean 2021–25) | low | |
| central | max(mean 2023–25, mean 2021–25) | central | |
| high | best year 2021–25 | high | |
| reform | as central | central | wind regional multiplier 3.0; wind floor terms 1,500 MW/yr / 0.10 / 1.5 (national limits = central's) |
| high_reform | as high | high | as reform: wind regional multiplier 3.0 and floor terms (national limits = high's; §73) |
| high_ipm | EPA 2025 Table 4-13 Step 1 per build year (implied windows) | high (after 2036; storage) | `ipm2025` tiers, no ceiling |

R0, GW/yr (low / central / high): wind 5.98 / 8.25 / 13.84, solar 21.14 / 26.87 / 31.16, storage
8.48 / 11.56 / 16.36, gas 5.38 / 6.02 / 9.65. Near-term years use the queue-based rate whenever it
is higher.

## Interactions

* **MaxCap growth caps.** With build_rate on, release `MaxCapTag_WindGrowth` / `SolarGrowth` for the
  same groups (e.g. `policies: S0_uncapped`); both limit the same builds otherwise.
* **Gas.** Opt-in (`groups: [..., gas]`) and off by default. The case build raises an error if gas is
  on while `MaxCapTag_GasTurbineSupply` is still in `max_cap_requirements.csv`. Nuclear
  (`MaxCapTag_NuclearGrowth`) and offshore wind (`MaxCapTag_Ban`, `offshore_wind_policy`) are unchanged.
* **MinCap.** The case build checks that each MinCap program whose generators are all in one group
  needs no more new build than the cumulative national ceilings allow; it stops with a message unless
  `ceiling_slack_cost` is set.
* **Local RPS / MinCap and regional ceilings.** Regional ceilings can make a requirement that must be
  met inside a few zones infeasible (a state RPS, or a MinCap program on one region's generators),
  because they limit new wind and solar there even when the national ceiling has room. With
  build_rate on, use the RPS ACP option (`rps_acp_per_mwh` in `rps_requirements.csv`, on
  `tom/ic-test-fedpol`), which turns an unmeetable target into a priced buyout. RPS targets are in
  energy, so the case build cannot test them exactly; it warns (`check_rps` in
  `build_rate/brc/switch_case.py`) when a program has no ACP, its share reaches `rps_check.warn_share`
  (30%, PLACEHOLDER), and a wind or solar ceiling in its transregs is set by the regional floor
  (little recent build). The message gives the window's allowed new wind + solar energy at rough
  capacity factors against the target (existing eligible generation is not counted). MinCap targets
  are checked against the national ceilings and stop the build.
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
  regional_groups: [wind_onshore, solar]   # transreg ceilings; storage and gas national-only
  ceiling_slack_cost: null  # $/kW
```

The `build_rate` axis touches only `build_rate.enabled` and `build_rate.level`, so it never shares a
flattened key with `policies` (carbon settings, `max_cap_req_fn`) in the same scenario row. It needs
a `build_rate` column in `scenario_inputs.csv` (not added on this branch; see the handoff below).

Commands (from `build_rate/`): `bash scripts/fetch_data.sh`, `python -m brc.cli run`, `pytest -q`.
The toy tests find Switch's `examples/3zone_toy` under `$SWITCH_SRC` (else `/opt/switch-src`, else
the installed `switch_model`'s source tree); on the VM run `SWITCH_SRC=<switch checkout> pytest -q`.

## Results (1 Oct 2026 inputs: EIA-860M Aug 2026, Queued Up through 2025)

National R (GW/yr), the free-band edge (1.3R; IPM shape 1.0R) and ceiling (2.0R; none for `high_ipm`), and cumulative 2026–y new build (GW):

| Level | Group | R 2028 / 2030 / 2035 | Free-band edge 2035 | Ceiling 2035 | Cum. R to 2028 / 2030 / 2035 | Cum. free band to 2028 / 2030 / 2035 | Cum. ceiling to 2035 | S0 new build (2028 / 2030 / 2035) |
|---|---|---|---|---|---|---|---|---|
| central | wind | 8.2 / 8.2 / 10.5 | 13.7 | 21.1 | 25 / 41 / 89 | 32 / 54 / 116 | 178 | 38.5 / 61.6 / 124.2 |
| central | solar | 33.4 / 26.9 / 34.3 | 44.6 | 68.6 | 111 / 165 / 321 | 145 / 215 / 417 | 642 | 90.8 / 168.6 / 413.7 |
| central | storage | 20.8 / 11.6 / 17.0 | 22.1 | 34.0 | 71 / 94 / 168 | 93 / 123 / 218 | 335 | — |
| central | gas | 8.4 / 6.0 / 6.7 | 8.6 | 13.3 | 21 / 33 / 65 | 27 / 43 / 84 | 130 | — |
| low | wind | 6.0 / 6.0 / 6.6 | 8.6 | 13.2 | 20 / 32 / 64 | 26 / 42 / 83 | 128 | 38.5 / 61.6 / 124.2 |
| low | solar | 33.4 / 21.1 / 23.3 | 30.3 | 46.7 | 111 / 154 / 266 | 145 / 200 / 345 | 531 | 90.8 / 168.6 / 413.7 |
| low | storage | 20.8 / 8.6 / 10.5 | 13.7 | 21.0 | 71 / 90 / 138 | 93 / 117 / 180 | 277 | — |
| low | gas | 8.4 / 5.4 / 5.4 | 7.0 | 10.8 | 20 / 31 / 58 | 26 / 40 / 75 | 116 | — |
| high | wind | 13.8 / 13.8 / 22.3 | 29.0 | 44.6 | 42 / 69 / 162 | 54 / 90 / 211 | 324 | 38.5 / 61.6 / 124.2 |
| high | solar | 33.4 / 31.2 / 50.2 | 65.2 | 100.4 | 111 / 174 / 383 | 145 / 226 / 498 | 766 | 90.8 / 168.6 / 413.7 |
| high | storage | 20.8 / 16.4 / 32.9 | 42.8 | 65.8 | 71 / 104 / 231 | 93 / 135 / 300 | 461 | — |
| high | gas | 9.7 / 9.7 / 12.3 | 16.0 | 24.6 | 29 / 48 / 104 | 38 / 63 / 136 | 208 | — |
| high_ipm | wind | 17.1 / 16.5 / 16.5 | 16.5 | none | 51 / 85 / 168 | 51 / 85 / 168 | none | 38.5 / 61.6 / 124.2 |
| high_ipm | solar | 46.2 / 43.2 / 35.4 | 35.4 | none | 139 / 228 / 413 | 139 / 228 / 413 | none | 90.8 / 168.6 / 413.7 |
| high_ipm | storage | 20.8 / 16.4 / 32.9 | unbounded (no adder) | none | 71 / 104 / 231 | unbounded | none | — |
| high_ipm | gas | 22.9 / 22.1 / 22.1 | 22.1 | none | 69 / 114 / 224 | 69 / 114 / 224 | none | — |

S0's caps are cumulative stock (wind 198.2 / 221.3 / 283.9 GW, solar 243.7 / 321.5 / 566.6 GW);
the new-build column subtracts the end-2025 EIA-860M operating stock (wind 159.7 GW incl. offshore,
which the WindGrowth tag also covers; solar PV 152.9 GW), ignoring retirements before 2035.

Regional check, onshore wind (GW; floors in GW/yr; cumulative ceilings sum the annual
max(share × m × 2.0 × R, floor) over 2026–35; the national ceiling, 178 GW central, binds on the
sum first; uncapped S0 builds are from the VM diagnostics):

| Transreg | Existing end-2025 | Peak annual build 2010–25 | Old floor (central / reform) | New floor central | New floor reform | Cum. ceiling central | Cum. ceiling reform | Observed 2021–25 | Uncapped S0 (VM) |
|---|---|---|---|---|---|---|---|---|---|
| SPP | 46.1 | 4.76 | 0.5 / 1.5 | 3.57 | 7.14 | 92.4 | 184.7 | 12.7 | |
| ERCOT | 35.8 | 3.85 | 0.5 / 1.5 | 2.89 | 5.78 | 67.1 | 134.1 | 12.1 | |
| MISO | 35.2 | 4.16 | 0.5 / 1.5 | 3.12 | 6.23 | 56.6 | 113.2 | 7.2 | 88 |
| WestConnect | 13.4 | 2.49 | 0.5 / 1.5 | 1.87 | 3.73 | 26.8 | 53.5 | 5.1 | |
| NorthernGrid | 11.4 | 2.06 | 0.5 / 1.5 | 1.55 (floor) | 3.09 (floor) | 15.5 | 30.9 | 1.6 | |
| CAISO | 6.5 | 1.97 | 0.5 / 1.5 | 1.48 (floor) | 2.95 (floor) | 14.8 | 29.5 | 0.7 | |
| PJM | 6.1 | 0.67 | 0.5 / 1.5 | 0.50 | 1.50 (floor) | 5.9 | 15.0 | 0.6 | 25 |
| NYISO | 2.7 | 0.56 | 0.5 / 1.5 | 0.50 (floor) | 1.50 (floor) | 5.0 | 15.0 | 0.8 | |
| ISONE | 1.7 | 0.36 | 0.5 / 1.5 | 0.50 (floor) | 1.50 (floor) | 5.0 | 15.0 | 0.2 | 10 |
| SERTP | 0.4 | 0.21 | 0.5 / 1.5 | 0.50 (floor) | 1.50 (floor) | 5.0 | 15.0 | 0.2 | 31 |
| FRCC | 0 | 0 | 0.5 / 1.5 | 0.50 (floor) | 1.50 (floor) | 5.0 | 15.0 | 0.0 | |

"(floor)" marks transregs where the floor sets the ceiling in every year of 2026–35. The new floor
binds only where history is thin relative to the stock: it raises NorthernGrid (8.6 → 15.5 GW) and
CAISO (5.0 → 14.8 GW). In the large wind regions the share term is far above the floor, and in
PJM, NYISO, ISO-NE, SERTP and FRCC stock and peak build are small, so floor_min still sets it. Against
the uncapped S0 builds, central still binds in MISO (56.6 vs 88), PJM (5.9 vs 25), SERTP (5.0 vs
31) and ISO-NE (5.0 vs 10); reform relieves MISO (113) and ISO-NE (15) but not PJM (15) or SERTP
(15). Regional ceilings sum to 294 GW central against the 178 GW national ceiling.

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

   **Existing cases (no rebuild).** To add build_rate to a case already built by pg_to_switch, patch
   its inputs folder. This writes all `build_rate_*.csv`, including the generator → group mapping
   (`build_rate_gens.csv`), from the case's own `gen_info.csv`, `periods.csv`, `gen_build_costs.csv`,
   `gen_build_predetermined.csv` and min/max cap files, and runs the same gas-cap and MinCap checks:

   ```bash
   cd build_rate
   python -m brc.cli patch-case <case>/inputs --level central            # default groups: wind, solar, storage
   #   --groups wind_onshore solar storage gas   (gas only with MaxCapTag_GasTurbineSupply released)
   #   --regional-groups wind_onshore solar     (default; storage and gas national-only)
   #   --no-regional | --zone-map zone_map.csv (columns ba, zone; only for aggregated zones)
   #   --ceiling-slack-cost 5000                 (diagnostic, $/kW)
   ```

   Then switch the module on. If the case runs with the merged `switch/modules.txt` it already lists
   `study_modules.build_rate` (inert without the files). Otherwise add
   `--include-module study_modules.build_rate` to the case's line in `scenarios.txt` (Switch accepts
   it with any `--module-list`). Patch an uncapped-S0 copy of the case (the WindGrowth/SolarGrowth
   caps released), not the S0 case itself. A patched case is solved in foresight; myopic chaining
   needs the pg_to_switch route (it adds the `build_rate_prev_build` alias).
3. Add a `build_rate` column to `pg/extra_inputs/scenario_inputs.csv`: `off` in every existing row.
   Then add these rows (they copy `s4x1_S0unc_2035_icoff` / `_icon` and set build_rate):

   ```
   s4x1_S0br_2035_icoff,2035,s4x1,firm,edf_epri_med,yes,S0_uncapped,RGGI10,none,hist5_high_gas,none,full,no,1,constrained,zero,yes,yes,yes,yes,none,no_wind_solar,none,none,blocked_2030_coal_gas,capped_2025,none,none,section232_2025,off,central
   s4x1_S0br_2035_icon,2035,s4x1,firm,edf_epri_med,yes,S0_uncapped,RGGI10,none,hist5_high_gas,none,full,no,1,constrained,zero,yes,yes,yes,yes,none,no_wind_solar,none,none,blocked_2030_coal_gas,capped_2025,none,none,section232_2025,on,central
   ```

   `S0_uncapped` is the `policies` preset on `tom/ic-test-fedpol` (S0 carbon settings with
   `max_cap_req_fn: growth_caps/uncapped.csv`), so this is "S0_buildrate": S0 with the wind/solar
   growth caps released and build_rate central. GasTurbineSupply and NuclearGrowth stay as they are.
4. Runs (2035, s4x1), each with the corrected wind profiles: `variable_capacity_factors.windloss.csv`
   (onshore wind profiles × 0.881, made on the VM in each case's inputs folder), passed as
   `variable_capacity_factors.csv=variable_capacity_factors.windloss.csv` (append it to the scenario's
   existing `--input-aliases` list in `scenarios.txt` if it has one):
   * `s4x1_S0br_2035_icoff`: uncapped S0 + build_rate central, headroom off;
   * `s4x1_S0br_2035_icon`: the same with interconnection headroom on;
   * reference: `s4x1_S0_2035_icoff` (S0 caps) and `s4x1_S0unc_2035_icoff` (uncapped).
5. Compare new onshore wind and solar by 2035 (target: wind share of new wind + solar near the 0.28
   EIA 2021–25 value), `build_rate_tiers_built.csv` (which bands fill), `build_rate_duals.csv` (ceiling
   price in $/kW), and `costs_itemized.csv` (BuildRateCosts).

## Gas-turbine supply cap (`build_rate.gas_turbine_cap`)

Moved here from `make_emission_policies.py`'s `MaxCapTag_GasTurbineSupply` (CHANGES §44,
SHARED_CHANGES.md). It is on by default and applies whether or not `build_rate.enabled`.
`build_rate/brc/turbine_cap.py` writes `gas_turbine_cap_gens.csv`, `gas_turbine_cap.csv` and
`gas_turbine_cap_params.csv`, and drops the MaxCapTag rows from `max_cap_*`.
`study_modules.build_rate` enforces it; output `gas_turbine_cap_results.csv`.

The options:
- annual additions by year (`annual_additions_mw`);
- coverage (combined cycle, combustion turbine, aeroderivative, reciprocating engine);
- CC at full plant MW or its turbine share;
- cumulative in-service vs new additions per period;
- whether economic retirements free room.

The defaults reproduce the old cap exactly: 451,444.2 MW in 2024 + 9,666.67 MW/yr (Wood Mackenzie
58 GW over 2025-30, extended linearly), cumulative in-service CC + CT, retirements not freeing room,
raised to the covered predetermined MW. This cap is separate from the opt-in `gas` tier group above.
Both may apply.
See `Guides and documentation/s0_production.md` for the table of settings.

## Placeholders (all marked in build_rate/config.yaml)

construction completion rate (0.9), overdue-pipeline rule (spread evenly 2026–30), growth rates
after 2030 (low/central/high), ramp floors, regional multipliers and floors, and amortisation lives.

## Future development: dynamic regional limits (not implemented)

**Today the regional limits are static.** Each region's floor is computed once from its end-2025
fleet and its peak annual build in 2010–25, and its share term scales with the exogenous national R.
Neither responds to what the model builds. The national ramp bound depends on the previous period's
build, but R = min(R_data, ramp), so it can only lower R, never raise it. A region that builds at
its limit through 2030 therefore gets the same 2031–35 limit as one that built nothing.

**Proposal (Tom Brindle, 2 Oct 2026).** Let a region that is deploying earn a higher limit
("capability grows with deployment"), using an opt-in switch `regional_dynamic` (default false):

- stock term on end-2025 stock **plus** the model's own new build in the region to date;
- peak term on max(historical peak 2010–25, the region's achieved annual rate in the previous
  period);
- a regional ramp term, (1 + g_reg_G)^W × the achieved rate, with g_reg a placeholder (wind ~0.20/yr;
  e.g. a region at 0.5 GW/yr through 2030 could reach ~1.2 GW/yr in 2031–35). With today's k_stock
  and k_peak the stock and peak terms alone barely move (+~125 MW/yr for 2.5 GW of extra stock), so
  the ramp term carries the effect.

The national ceiling stays exogenous, so dynamic regional limits only reallocate build between
regions within the national total. They cannot reintroduce the self-referencing national overbuild
that the data anchor is meant to prevent.

**Implementation notes:**

- *Myopic runs* (the usual fedpol setup): exact and simple. `chain_build_rate_inputs` recomputes
  each region's limits for the next stage from the solved stage's actual builds, using any formula
  including max().
- *Perfect foresight*: an upper bound equal to the max of terms that depend on variables is
  non-convex, so use an additive linear form instead (static limit_p + g × the region's NewBuild in
  p−1) and document it as an approximation of the myopic rule.
- *Single-period runs* (e.g. 2035-only tests) have one window and nothing to recompute, so they stay
  more conservative than multi-period runs. The test asserting identical limits for one and three
  periods would then hold only with `regional_dynamic: false`.
- *Tests to add*: a 2030+2035 myopic toy chain where a region at its 2030 limit gets a higher 2035
  limit; the additive form under perfect foresight; unchanged limits with the switch off.

Most relevant once fedpol runs go multi-period with myopic chaining.
