# Gas network, Stage 1: where each zone's gas comes from

`gas_network/` (CHANGES §101) estimates, for every Switch load zone and model year, the share of its delivered natural
gas that comes from each production basin. It changes nothing in the model. Its use is upstream-methane reporting:
basin shares × the zone's gas burn × basin methane intensity. The intensities come from a separate research task and
go into `gas_network/data/basin_intensity.csv` (a template, empty for now). Later stages (a seasonal gas network in
the optimisation, winter deliverability, pipeline expansion) are in `gas_network/docs/design_note_step1.md`.

## Commands

Run from `gas_network/`. Tested on pandas 3.0.6 (Python 3.12) and pandas 1.4.4 (Python 3.10, numpy 1.23.5).

```
python -m gasnet.cli fetch      # download the pinned inputs into data/raw/ (git-ignored) and check their sha256
python -m gasnet.cli run        # writes outputs/ (refuses if a raw file differs from its pin)
python -m gasnet.cli report <switch outputs dir> [--inputs-dir <inputs dir>]   # writes basin_gas_mmbtu.csv there
python -m gasnet.cli stage2a    # Stage 2a annual network LP -> outputs/stage2a/ (§103)
python -m gasnet.cli nemscap    # NGMM capacity (AEO2026 restart file) vs Stage 2a min expansion (§104)
python -m gasnet.cli upstream <switch outputs dir> [options]   # post-solve upstream CH4 (§105; below)
python -m pytest -q             # 39 tests; the raw-data ones skip if data/raw is empty
python -m gasnet.cli pin        # only after a deliberate data refresh: rewrites data/SOURCES.yml
```

`fetch` needs www.eia.gov and raw.githubusercontent.com. `run` takes about 20 s.

## Outputs (`gas_network/outputs/`, committed)

| File | Contents |
|---|---|
| `zone_basin_shares.csv` | `zone, year, basin, share, method, source_vintage, fallback` for the 134 zones, 2024 and 2028/2030/2035/2040/2045, default method (`trace_aeo_additive_balanced`), with the §104 share fallback applied (`method` `fallback_2024`, `fallback` = reason; empty otherwise). Shares sum to 1 per zone and year (rounded to 6 decimals). |
| `zone_basin_shares_constrained_pipe.csv` | Share path `constrained_pipe` (§105): as `zone_basin_shares.csv` to 2030, then each zone's 2030 shares (`method` `constrained_pipe_hold_2030`) |
| `zone_basin_shares_no_fallback.csv`, `share_fallback.csv`, `balance_degeneracy_by_zone.csv` | The shares before the fallback (identical to the §103 file); the zone-years that fall back and why; the degeneracy (pp) of every zone and model year (§104) |
| `zone_basin_shares.meta.json` | Method description and the known limitations, quantified (district labelling, Colorado San Juan, mixing, routing) |
| `zone_basin_shares_sensitivity_ratio.csv` | Model years under the ratio method (`trace_aeo_ratio_balanced`) |
| `method_comparison.csv` | Example zones under all four projection variants (additive/ratio × balanced/unbalanced) |
| `projection_reconciliation.csv` | Per variant and year: overdraw before/after balancing, volume moved, unmet floors, census-division disposition error |
| `basin_production_vs_aeo.csv` | Basin totals (node supply and traced) against AEO2026 Tables 59/60 |
| `district_basin_mixing.csv` | Non-shale share of each production area (reserves report Tables 8/9) and the gas that would move between basins |
| `hub_basin_shares.csv` | The same by gas hub (state; TX_E/TX_N/TX_W; GOM), with `throughput_mmcf` |
| `region_crosscheck.csv` | Basin mix of each AEO Table 64 flow region three ways (below) |
| `table64_arcs_check.csv` | Interregional flows: EIA 2024, Table 64, and the projected bundles, Bcf |
| `texas_routing.csv` | Intra-Texas transfers by year, NGMM capacity, and the Texas disposition scale |
| `zone_hub_weights.csv`, `tx_county_hubs.csv` | Zone → hub weights; Texas county → district → hub and gas MW |
| `flags.csv`, `run_summary.json` | Every held, scaled or assigned item (below) and the run summary |

`method`: `trace_eia` (2024: proportional sharing on EIA state-to-state movements); `trace_aeo_additive_balanced`
(model years, default) and `trace_aeo_ratio_balanced` (sensitivity), below; `fallback_2024` (a model year that
takes the zone's 2024 shares, §104). `source_vintage` codes:
- `NGA2024@<release>` – EIA Natural Gas Annual dnav files;
- `ARR2024@<release>` – EIA proved-reserves report, year-end 2024;
- `NEMS@1bfbb2a` – EIAgov/NEMS inputs;
- `AEO2026@cb2026.d021826b` – AEO2026 supplemental tables.

Full entries (URL, release date, sha256) are in `gas_network/data/SOURCES.yml`.

### Reporting a Switch run

`report` reads two files from a Switch outputs folder:
- `GenFuelUseRate.csv` (Switch's generic output: generator, timepoint, fuel, MMBtu/h);
- `dispatch.csv` (zone, period, `tp_weight_in_year_hrs`).

Timepoint ids equal timestamps in this repo's cases (`conversion_functions.py`). Without `dispatch.csv`, pass
`--inputs-dir` (`gen_info`, `timepoints`, `timeseries`, `periods`).

It writes `basin_gas_mmbtu.csv` (zone, period, share_year, basin, MMBtu per typical year of the period). Stress-day
timepoints carry zero weight and drop out. A period without its own share year uses the latest earlier one.

**Gas fuels** (`config.yaml gas_fuels`): `naturalgas`, `naturalgas_ccs90`, `naturalgas_ccs100`. These are every
natural-gas fuel in the S0 v3.1 settings (`pg/settings/fuels.yml` `tech_fuel_map` and `ccs_fuel_map`):
- gas CC, CT, steam, gas peaker and Other_peaker are `naturalgas`;
- NaturalGas_CCCCS is `naturalgas_ccs90`; NaturalGas_CCS100 is `naturalgas_ccs100`;
- coal-to-gas conversions (`coal_fleet.py`) burn `naturalgas`.

No on-site or backup generator burns gas. The data-centre "backup" resources are the fuel-less `load_growth` virtual
generators. As a guard, `report` stops if a fuel with "gas" in its name appears that is not in the list.

## Method

**Network.** 52 hubs:
- 47 states + DC;
- Texas as NGMM's three hubs, TX_E (RRC districts 1, 2, 3, 4, 6), TX_N (5, 7B, 9) and TX_W (7C, 8, 8A, 10);
- the federal Gulf of Mexico (GOM).

Flows are EIA's 2024 gross directional state-to-state receipts (163 pairs, 67.0 Tcf) plus GOM → state receipts.
Supply is:
- dry production (EIA, 2024);
- international receipts: Canada by crossing state, LNG by country of origin, Mexico.

**Tracing** (proportional sharing, `tracing.py`). Gas is fully mixed at each hub, so every delivery and every burn at a
hub carries its mix: (diag(T) − Fᵀ) S = P, where T = own supply + receipts. Consumption, exports and storage don't
enter. A zone takes its hub's mix. Every ReEDS zone lies in one state (`hierarchy.csv`), including p13 (Las Vegas),
which goes to NV for gas whatever its EMM region. Texas zones are weighted over the three hubs (below).

**Basins** (Tom's list):

| Basin | Production areas (`data/reference/production_areas.csv`) |
|---|---|
| Appalachia | PA, WV, OH, NY, VA, KY, TN, MD |
| Haynesville | Louisiana North; Texas RRC districts 5 and 6 |
| Permian | Texas districts 7C, 8, 8A; New Mexico East |
| Eagle Ford | Texas districts 1, 2, 4 |
| Barnett/other TX | Texas districts 3, 7B, 9, Texas state offshore |
| Mid-Continent/Anadarko | OK, KS, AR; Texas district 10 (Panhandle) |
| San Juan | New Mexico West; Colorado San Juan Basin (27.5% of Colorado, below) |
| Rockies (other) | Rest of Colorado; WY, UT |
| Bakken | ND, MT |
| Fed. GOM | Federal offshore Gulf of America |
| W. / E. Canada | Pipeline imports by crossing, as NGMM: W = WA, ID, MT, ND, MN; E = ME, NH, VT, NY, MI (CA and CT, not NGMM crossings, by region) |
| LNG imports | International receipts from any other country (Everett MA, Cove Point MD, Sabine LA) |
| other | Every other state; Louisiana South Onshore and State Offshore; Mexican imports |

**Texas and New Mexico splits.** EIA's state dry production is split in proportion to 2024 "estimated production" in
EIA's *U.S. Crude Oil and Natural Gas Proved Reserves, Year-end 2024* (released 2026-04-07), Table 8. That table gives
the RRC districts, NM East/West and Louisiana North/South Onshore/State Offshore.
- New Mexico: East 3,079 Bcf (85.9%, Permian), West 506 Bcf (14.1%, San Juan).
- Texas: District 8 has 40% of Texas production, District 6 16%.
- The shares are of wet gas after lease separation, applied to dry gas (ASSUMPTION: the same dry/wet ratio in every
  district).
- RRC's own site (rrc.texas.gov) is blocked from cloud sessions. These EIA figures are RRC district production as
  compiled by EIA.

**Texas routing** (ASSUMPTION, stated as Tom asked). Interstate arcs are assigned to sub-hubs by NGMM's border mapping
(`ngtexas.txt`):
- LA, AR and GOM to TX_E; NM to TX_W;
- receipts from OK split TX_N 48.8% / TX_W 51.2% (NGMM 2023 history); deliveries to OK from TX_W;
- the few tiny pairs NGMM does not list go by geography.

Each sub-hub's disposition is:
- its share of Texas residential, commercial, industrial and electric consumption (NGMM `TexasConsShares`);
- lease and plant fuel in proportion to its production, pipeline and distribution use in proportion to its supply;
- its interstate deliveries;
- Mexico exports (TX_E 69.5%, TX_W 30.5%, NGMM 2024);
- all LNG exports at TX_E.

Dispositions are scaled so Texas balances. In 2024 dry production + receipts are 11.17 Tcf against 12.96 Tcf of
dispositions, so the scale is 0.865. The gap is extraction loss, storage and EIA's balancing item.

Intra-Texas transfers are the least total flow that balances the three hubs (direct before two-hop) within NGMM's
2023 intra-Texas capacities (`texas_capacity.txt`). TX_W → TX_E exceeds that capacity: 73 Bcf in 2024 and
0.5–1.3 Tcf/yr in 2028–2045. Those capacities predate Matterhorn and later Permian pipelines. The excess goes on the
same direct path and is flagged.

**Texas zones** are weighted over hubs by operating gas-fired nameplate MW per county (EIA-860M, August 2026, Energy
Source Code NG). The chain is:
- county → zone: ReEDS `county2zone.csv` (ReEDS 2f583ff, in `interconnection_headroom/data/reference/`);
- county → RRC district: the most frequent district among the AEO2026 HSM onshore project files (EIAgov/NEMS
  `models/hsm/input/onshore/projects`, columns `state, cnty_fips, rrc`).
  - The HSM code 73 is District 7B and 72 is 7C. All 19 of the counties in RRC's published District 7B list that
    appear in the HSM files carry 73.
  - 235 of Texas's 254 counties are covered.
  - Four gas-plant counties have no district: El Paso, Lamar, Llano and Cameron. Each takes the hub of the nearest
    generator in a mapped county (860M coordinates; flagged).

Resulting weights:
- p48, p59, p62: all TX_W;
- p60: TX_W 99.6%;
- p61: TX_W 56%, TX_N 22%, TX_E 22%;
- p63: TX_N 79%, TX_E 21%;
- p57, p64–p67: all TX_E.

**Colorado San Juan split** (§102).
- **Source.** Observed county production is not reachable from cloud sessions: ECMC (ecmc.state.co.us,
  ecmc.colorado.gov, cogcc.state.co.us), the Colorado State Publications Library copies of its annual county tables
  (spl.cde.state.co.us), data.colorado.gov and ONRR are all refused by the egress policy. The split therefore uses
  **EIA's AEO2026 HSM onshore producing-project files** (EIAgov/NEMS 1bfbb2a).
  - Each project carries county, USGS play (province = play // 100) and GP1, its first projection-year gas from wells
    already producing (MMcf).
  - San Juan = province 22 projects in La Plata, Archuleta, Montezuma or San Juan counties: 614 Bcf of 2,232 Bcf, so
    **27.5% of Colorado** (La Plata 576, Archuleta 37). A 1.6 Bcf province-22 record in Moffat County is left in
    Rockies (other) and flagged.
- **Validation.**
  - The same method gives New Mexico's San Juan share as 15.4%, against 14.1% in the reserves report (NM West
    506 / 3,585 Bcf).
  - Garfield + La Plata + Mesa + Rio Blanco come to 45% of Colorado, against EIA's "almost 44%" for 2019 (Today in
    Energy, 2020-07-23, from COGCC data).
- **Limit.** GP1 is a model volume, not observed production. If Tom can supply ECMC's county production file, the
  share is one number (`production_areas.csv`, `hsm_san_juan`).
- **Utah and Arizona.** No HSM production is in province 22. San Juan County, UT is Paradox Basin (province 21,
  6 Bcf), so no split is needed.

**Projections** (ASSUMPTION; §102). AEO2026's tables start in 2025 and EIA's state flows end in 2024. Each quantity
keeps its 2024 base and moves with AEO2026 from 2025 to the model year.
- **Production** by basin, × AEO series(y)/AEO series(2025):
  - Table 60 plays for Appalachia (Marcellus/Utica), Haynesville, Permian, Eagle Ford, Barnett and Bakken;
  - Table 59 regions for Mid-Continent, Rocky Mountain (San Juan and Rockies), Gulf offshore and Lower 48 onshore
    (other).
  - The series is the nearest published one, not an exact match.
- **Interregional flows and Canada imports**: each bundle of 2024 state arcs between two Table 64 regions (and each
  Table 64 Canada arc's crossings) moves by one of two rules:
  - **additive (default)**: 2024 + (Table 64 level(y) − level(2025)), spread over the bundle's arcs in proportion to
    their 2024 flows, floored at 0;
  - **ratio (sensitivity)**: 2024 × Table 64(y)/Table 64(2025).
  - The ID → OR/WA bundle uses Table 64's "Canada (through Idaho)" + "Rocky Mountains–Great Plains" rows.
  - Arcs Table 64 doesn't list are held at 2024. Under ratio, arcs at 0 in 2025 are also held.
- **LNG imports**: × Table 61. Mexican imports are held.
- **Intra-region arcs** × the region's supply-plus-inflow growth; **GOM outflows** × the Gulf offshore index.
- **Texas** is re-routed each year (Table 62 West South Central consumption; Table 61 Mexico and LNG exports).
- **Balancing** (default on; `gasnet/balance.py`). Arcs scaled one by one don't conserve gas at through-hubs.
  - Unbalanced, by 2045 TN, MO, OH, WV, MN, NH, OR and GOM ship 7.2 Tcf more than they receive and produce
    (additive; ratio 7.6). That "overdraw" carries the hub's mix downstream: it inflates Florida's Appalachian share,
    for example.
  - The repair keeps every Table 64 bundle as projected. It moves only intra-region arcs, GOM outflows and intra-Texas
    arcs, by the least total volume (HiGHS LP). Every hub keeps at least its 2024 local disposition × AEO2026
    Table 62 consumption growth of its census division; West South Central adds Table 61 LNG and Mexico exports.
  - Where existing arcs can't reach a floor, the shortfall is reported as unmet (flag `balance_unmet_floor`).

**Checking the methods against AEO2026** (`basin_production_vs_aeo.csv`, `projection_reconciliation.csv`).
- **The production check can't separate the methods.** The basin totals implied by the traced shares,
  Σₕ (throughput − outflow)ₕ · sₕ,ᵦ, equal node supply of basin b exactly, under any flows. This is an identity of
  proportional sharing (tested). So against Tables 59/60 every method shows the same gaps.
- **Those gaps are definitional and constant over the years**, because production is indexed:

| Basin (AEO series) | 2024 base vs AEO 2025 |
|---|---|
| Appalachia (T60 Marcellus/Utica) | +2.4% |
| Haynesville (T60) | +25.3% (Texas Districts 5 and 6 conventional and all North Louisiana are in our label) |
| Permian (T60) | +6.5% |
| Eagle Ford (T60) | +38.1% (525 Bcf of conventional Gulf Coast gas is under the label; see Limits) |
| Barnett/other TX vs T60 Barnett | −11.8% |
| Bakken (T60) | +1.2% |
| Mid-Continent (T59) | +10.8% |
| San Juan + Rockies (T59 Rocky Mountain) | −10.4% |
| Fed. GOM (T59 Gulf offshore) | −23.9% (dry federal production vs AEO's offshore Gulf) |
| Lower 48 total (T59) | −3.6% in 2024 vs 2025; −1.9% by 2045 |

- **What separates the methods is network consistency:**

| | 2028 | 2030 | 2035 | 2040 | 2045 |
|---|---|---|---|---|---|
| Overdraw before balancing, Tcf: additive / ratio | 0.16 / 0.15 | 1.02 / 0.94 | 3.55 / 3.91 | 5.81 / 6.25 | 7.24 / 7.65 |
| Volume moved by balancing, Tcf: additive / ratio | 3.6 / 3.9 | 8.2 / 8.1 | 14.9 / 15.5 | 20.4 / 20.7 | 23.6 / 24.2 |
| Unmet floors after balancing, Tcf: additive / ratio | 0.36 / 0.35 | 0.49 / 0.76 | 0.74 / 0.78 | 1.18 / 1.49 | 1.55 / 1.70 |
| Census-division disposition error, balanced: additive / ratio | 7.0% / 6.4% | 7.5% / 8.4% | 7.4% / 8.9% | 8.3% / 9.3% | 6.5% / 7.0% |
| The same, unbalanced: additive / ratio | 8.3% / 7.3% | 15.2% / 16.1% | 26.5% / 28.1% | 33.6% / 35.1% | 36.0% / 37.0% |

**Choice: additive + balancing (default).**
- Balancing matters most: it cuts the disposition error from 26–37% to 6.5–9% from 2035 on and removes all
  overdraw.
- Between the rules, additive reconciles better in 2035–2045 on every measure and in 2030 on unmet floors and
  disposition error. Ratio is marginally better in 2028 (overdraw, error, unmet floors) and in 2030 on overdraw
  and volume moved.
- Additive also avoids the ratio rule's amplification of small 2024 bases (Rockies → AZ/NM 290 → 1,237 Bcf under
  ratio).
- Ratio stays as `zone_basin_shares_sensitivity_ratio.csv`.

**Florida and the Texas E zones under both methods** (% of delivered gas; `method_comparison.csv`):

| Zones, year | Variant | Appalachia | Haynesville | Permian | Eagle Ford | Mid-Con | Fed. GOM |
|---|---|---|---|---|---|---|---|
| FL 2030 | additive balanced (default) | 49.3 | 27.5 | 8.0 | 4.2 | 2.4 | 5.0 |
| FL 2030 | ratio balanced | 48.3 | 27.7 | 8.1 | 4.2 | 3.0 | 5.1 |
| FL 2030 | additive / ratio unbalanced | 46.0 / 45.7 | 26.5 / 26.9 | 8.7 / 8.7 | 4.0 / 4.1 | 5.4 / 5.4 | 5.1 / 5.1 |
| FL 2045 | additive balanced (default) | 77.1 | 5.2 | 3.3 | 2.2 | 6.1 | 3.0 |
| FL 2045 | ratio balanced | 77.2 | 5.5 | 3.4 | 2.4 | 5.9 | 3.1 |
| FL 2045 | additive / ratio unbalanced | 66.8 / 65.9 | 10.0 / 10.3 | 6.9 / 7.0 | 4.7 / 4.9 | 5.1 / 5.3 | 3.5 / 3.6 |
| TX_E 2030 | additive balanced (default) | 0.0 | 23.9 | 43.2 | 22.6 | 4.4 | 0.7 |
| TX_E 2030 | ratio balanced | 0.0 | 24.1 | 42.6 | 22.7 | 4.7 | 0.7 |
| TX_E 2045 | additive balanced (default) | 12.7 | 11.1 | 37.4 | 28.5 | 7.2 | 1.1 |
| TX_E 2045 | ratio balanced | 13.1 | 11.1 | 37.1 | 28.4 | 7.3 | 1.2 |
| TX_E 2045 | additive / ratio unbalanced | 0.0 / 0.0 | 12.6 / 12.6 | 44.4 / 44.2 | 32.5 / 32.5 | 7.8 / 7.8 | 0.7 / 0.7 |

By 2045, balancing sends Appalachian gas arriving in Louisiana on to TX_E: about 13% of TX_E's mix. Table 64's
Eastern Midwest → South Central flow reaches 7.2 Tcf and Louisiana can't absorb it at its AEO-grown floor. The
Louisiana → Texas pipes in the tracker point the same way: Golden Pass bidirectional 2.5 Bcf/d (2025) and the
Louisiana Connector 2.0 Bcf/d (2026).

The restart file in the NEMS repo (pre-release run d011626a, not the published AEO2026 d021826b) is **not** used
anywhere in Stage 1.

## Results (example zones; % of delivered gas)

Default method. `method_comparison.csv` has the other variants.

**2024**

| Zones | Appalachia | Haynesville | Permian | Eagle Ford | Barnett/other TX | Mid-Con/Anadarko | San Juan | Rockies (other) | Bakken | Fed. GOM | W. Canada | E. Canada | LNG imports | other |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FL (p91, p101, p102) | 34.2 | 30.5 | 9.4 | 5.8 | 1.0 | 7.9 | 0.1 | 0.4 | 0.2 | 7.1 | 0.3 | – | – | 3.0 |
| VA (p99, p100, p118, p124) | 99.9 | – | – | – | – | – | – | – | – | – | – | – | 0.1 | – |
| MA (p131) | 81.8 | – | – | – | – | – | – | – | – | – | – | 15.5 | 2.6 | – |
| CA (p8–p11) | – | – | 32.8 | – | – | 1.1 | 7.7 | 24.0 | 0.2 | – | 28.6 | – | – | 5.7 |
| CO (p33, p34) | – | – | 1.3 | – | – | 5.5 | 22.3 | 70.3 | 0.2 | – | 0.5 | – | – | – |
| TX_W zones p48, p59, p62 | – | – | 90.3 | – | – | 8.8 | 0.5 | 0.3 | – | – | – | – | – | – |
| p61 | – | 11.1 | 66.6 | 5.8 | 5.0 | 10.6 | 0.4 | 0.3 | – | 0.2 | – | – | – | – |
| p63 | – | 26.8 | 34.3 | 5.6 | 15.5 | 17.0 | 0.3 | 0.4 | – | 0.2 | – | – | – | – |
| TX_E zones p57, p64–p67 | – | 23.0 | 38.7 | 26.4 | 4.7 | 5.9 | 0.2 | 0.2 | – | 0.8 | – | – | – | – |

**2045**

| Zones | Appalachia | Haynesville | Permian | Eagle Ford | Barnett/other TX | Mid-Con/Anadarko | San Juan | Rockies (other) | Bakken | Fed. GOM | W. Canada | E. Canada | LNG imports | other |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FL | 77.1 | 5.2 | 3.3 | 2.2 | 0.1 | 6.1 | – | 0.2 | 0.5 | 3.0 | 0.4 | – | – | 1.8 |
| VA | 99.8 | – | – | – | – | – | – | – | – | – | – | – | 0.2 | – |
| MA | 82.7 | – | – | – | – | – | – | – | – | – | – | 9.7 | 7.6 | – |
| CA | – | – | 31.0 | – | – | 1.9 | 6.2 | 22.1 | – | – | 31.4 | – | – | 7.4 |
| CO | – | – | 1.2 | – | – | 11.5 | 21.4 | 65.8 | – | – | – | – | – | – |
| TX_W zones | – | – | 88.5 | – | – | 11.0 | 0.3 | 0.2 | – | – | – | – | – | – |
| TX_E zones | 12.7 | 11.1 | 37.4 | 28.5 | 1.1 | 7.2 | 0.2 | 0.1 | 0.1 | 1.1 | 0.1 | – | – | 0.3 |

The Colorado split changes the 2024 Western mixes: California's San Juan share rises from 4.8% to 7.7% and Rockies
(other) falls from 26.9% to 24.0%. Florida's Appalachian share keeps rising because Table 64's Mid-Atlantic →
Eastern Midwest → South Central flows roughly triple.

**Region cross-check** (`region_crosscheck.csv`). For each Table 64 region, the basin mix is computed three ways:
- `state_model`: the throughput-weighted mean of its hubs;
- `region_same_flows`: a region-level trace on the same flows aggregated to regions;
- `region_table64`: a region-level trace with Table 64's own interregional levels (2025 for the base year).

The state model and the two region traces agree within about 8 points (total variation) in the Mid-Atlantic, Northern
Great Plains and South Central. They differ most where one big region hides different supply paths:
- Florida: 34% Appalachian at state level, 11–13% at region level. Alabama's receipts arrive from the north, not from
  the Texas/Louisiana pool.
- Oregon/Washington: Idaho's Canadian imports versus Wyoming/Colorado gas.
- New England: Maine and New Hampshire's Canadian imports.

The difference between the two region traces measures indexing against Table 64 levels. Use the state model; read the
region columns as sensitivity.

## Things that did not reconcile

1. **AEO2026 Table 64 anomaly (question for EIA).** "Into Oregon and Washington from Rocky Mountains–Great Plains"
   equals "Canada (into Washington)" in every year (632.0 Bcf in 2025 … 397.0 in 2045). It looks like a copy error in
   the published table.
   - EIA 2024 movements from Rockies–Great Plains states into OR/WA total 1,030 Bcf, most of it Canadian gas through
     Idaho.
   - Question for EIA: what is the correct Rockies → Oregon/Washington row in AEO2026 Supplemental Table 64
     (cb2026.d021826b)?
   - Our projection indexes that bundle by Rockies + Canada-through-Idaho as published. `tests/test_pipeline.py`
     asserts the two rows are identical, so a corrected table will show up.
2. **Table 64 vs EIA levels.** Five region pairs differ by more than 3×: NM→South Central, Eastern
   Midwest→Mid-Atlantic, Eastern Midwest→Southeast, Rockies→AZ/NM and South Central→Rockies.
   - The ratio rule amplifies a small base (Rockies → AZ/NM: 290 → 1,237 Bcf by 2045, flag
     `arc_level_mismatch_indexed`); the additive default adds Table 64's change instead.
   - Arcs Table 64 doesn't carry are held at 2024: TX→NM 390 Bcf, the Mid-Atlantic→South Central 224 Bcf, New
     England→Mid-Atlantic 123 Bcf, and six smaller ones (81 Bcf or less).
3. **Texas.**
   - Dispositions are scaled by 0.865 (2024) and 0.80–0.92 later.
   - Intra-Texas flows exceed NGMM's 2023 capacity in every year. Capacity added since (Matterhorn, the GCX
     expansion, Blackcomb, Eiger Express, Hugh Brinson) removes that; see `gas_network/docs/stage2_capacity_research.md`.
4. **Unmet floors after balancing** (flag `balance_unmet_floor`, 0.36–1.55 Tcf). Where Table 64's fixed bundles
   bring less gas into a region than AEO-grown local use needs (FL, GA, TN, CA, OR, WA), the shortfall is reported.
   Those hubs keep their full traced mix.
5. **Corrections to the Step 1 note's scratch numbers.** The scratch parser missed two items:
   - Mississippi's 134 Bcf of receipts from the federal Gulf ("Receipts from", lower case);
   - Arizona's 1.6 Bcf of Mexican imports (an "Imports + Intransit" column).

   With both, Florida 2024 is Appalachian 34.1% (not 34.6%) and federal GOM 7.1%. The test reproduces the scratch
   numbers with both removed and the corrected values with them.
6. **Basin labelling** (ASSUMPTION; known limitation, in `zone_basin_shares.meta.json`). Each production area is
   labelled whole with its main basin. Reserves report YE2024 Table 9 (shale) against Table 8 (total) shows:
   - **Over 30% non-shale:** RRC District 3 (96%), 4 (41%), 7B (32%), 8A (88%), 10 (100%); also NM West, LA South
     and Texas state offshore (100%, but labelled by basin).
   - **Only Districts 1, 2 and 4 move gas between basins.** Their conventional Gulf Coast gas (Vicksburg, Frio,
     Wilcox, Lobo) is labelled Eagle Ford instead of Barnett/other TX: 2024 dry D1 73, D2 77, **D4 375 Bcf; 525 Bcf
     in all, 24% of the Eagle Ford label**.
   - The other high non-shale areas hold gas from the same geological basin as the label: Permian conventional in
     8A, Anadarko Granite Wash in 10, Upper Gulf Coast in 3 (already "other TX"), and 7B.
   - Montana is all Bakken; Kentucky is all Appalachia.

## Stage 1 status, unmet local use and balancing degeneracy (§103)

**Status** (Tom, 2026-10-11): Stage 1 is accepted at ddac626. **2024 shares are the reference; projected years are
indicative.** `zone_basin_shares.meta.json` carries `year_status` and the two tables below.

**Unmet local use after balancing** (default variant; `outputs/unmet_local_use.csv`). These states end below their
AEO-grown floor of local use (consumption + exports). Every one has gas-fired generation in Switch zones; capacity is
EIA-860M August 2026 operating NG nameplate (Switch's existing gas fleet is built from EIA-860 via PowerGenome).

| Year | State (Switch zones) | Shortfall, Bcf | Floor, Bcf | Shortfall % | Gas MW |
|---|---|---|---|---|---|
| 2028 | OR (p5–p7) | 218.9 | 275.2 | 79.5 | 4,348 |
| 2028 | WA (p1–p4) | 124.0 | 480.9 | 25.8 | 4,045 |
| 2028 | CA (p8–p11) | 10.2 | 1,918.5 | 0.5 | 40,278 |
| 2028 | IA (p45, p69, p70) | 5.3 | 453.5 | 1.2 | 4,350 |
| 2030 | OR | 262.9 | 277.9 | 94.6 | 4,348 |
| 2030 | WA | 136.7 | 485.5 | 28.2 | 4,045 |
| 2030 | GA (p94) | 85.3 | 837.2 | 10.2 | 18,594 |
| 2030 | FL (p91, p101, p102) | 5.8 | 1,645.5 | 0.4 | 55,325 |
| 2035 | TN (p92) | 237.2 | 418.4 | 56.7 | 7,388 |
| 2035 | GA | 157.7 | 858.4 | 18.4 | 18,594 |
| 2035 | CA | 101.2 | 1,828.3 | 5.5 | 40,278 |
| 2035 | FL | 95.8 | 1,687.2 | 5.7 | 55,325 |
| 2035 | OR / WA | 75.1 / 75.1 | 262.3 / 458.2 | 28.7 / 16.4 | 4,348 / 4,045 |
| 2040 | IA | 439.3 | 484.4 | 90.7 | 4,350 |
| 2040 | FL | 256.9 | 1,864.5 | 13.8 | 55,325 |
| 2040 | OR / WA | 204.1 / 137.8 | 285.5 / 498.9 | 71.5 / 27.6 | 4,348 / 4,045 |
| 2040 | CA / GA | 90.3 / 56.1 | 1,990.4 / 948.6 | 4.5 / 5.9 | 40,278 / 18,594 |
| 2045 | IA | 397.1 | 511.3 | 77.7 | 4,350 |
| 2045 | WA / OR | 367.2 / 306.3 | 535.1 / 306.3 | 68.6 / 100.0 | 4,045 / 4,348 |
| 2045 | FL | 256.6 | 2,091.6 | 12.3 | 55,325 |
| 2045 | CA / TN | 124.8 / 101.5 | 2,134.9 / 468.1 | 5.8 / 21.7 | 40,278 / 7,388 |

Reading it:
- **Pacific Northwest.** Table 64's Canada → WA and through-Idaho imports fall, and the fixed bundles can't refill
  OR/WA. Oregon's local use is fully unmet by 2045. These hubs keep their traced mix, but their volumes don't add up.
- **Iowa/Tennessee (Eastern Midwest) and Florida/Georgia** sit at the ends of Table 64 bundles that don't grow with
  AEO consumption.
- Which state inside a region takes a shortfall can be a tie between equal optima (Iowa vs Tennessee in 2035).
  Regional totals are the robust number.

**Balancing degeneracy** (`outputs/balance_degeneracy.csv`). The balancing LP has tied optima. A second optimum
(sorted arcs, tiny tie-break costs) moves zone shares by the total-variation distances below:
- the mean over all 134 zones is 1.9–3.4 pp;
- the example zones are under 1.2 pp through 2035, except MA at 2–3.6 pp; in 2040–45, FL is at 3.9–4.6 pp and the
  TX_E/TX_W zones at 1.7–3.6 pp;
- the worst are Missouri's six zones (24–27 pp, a through-hub) and NH/VT (10–12 pp); about 20 zones exceed 5 pp.

The accepted ddac626 formulation stays the default, and is one of the optima. This is part of why the projected years
are indicative.

## Stage 2a: standalone annual gas network LP (§103)

`python -m gasnet.cli stage2a` → `outputs/stage2a/`. No Switch link, and no model code changed.

**Network.**
- **Nodes:** the 52 Stage 1 hubs plus 14 basin supply nodes. Gathering arcs from each basin to its hubs are fixed at
  the hub's share (production areas; crossings for Canada; terminals for LNG imports), so supply by basin is exact.
- **Arcs:** `gasnet/capacity.py`.
  - EIA State-to-State Capacity January 2026, per-pipeline 2025 values (2024 where a 2025 row is missing). Texas
    endpoints go to sub-hubs by county; two counties without a district (Fannin, Parmer) take their zone's hub.
    Gulf of America rows map to GOM.
  - NGMM intra-Texas 2024, which includes Matterhorn.
  - Projects from `data/reference/capacity_projects.csv` (EIA tracker, August 2026, hand-mapped to hub arcs) under
    the **status rule**: Completed, Part Completed and Construction count from November of the in-service year (2/12
    of the year, then full); Approved only in the `with_approved` sensitivity; everything else excluded.
  - Two 2025 completions missing from EIA's 2025 capacity are added: Golden Pass bidirectional LA→TX_E 2,500 and
    Wisconsin Reliability 144.
  - TX_W→TX_E reaches 17,650 MMcf/d by 2030 (NGMM 2024 10,880 + GCX expansion + Blackcomb + Eiger Express). TX_W→TX_N
    reaches 3,760 (with Hugh Brinson 2,200). NGMM's own planned row (Warrior 3,700 + Apex 2,000) differs; Apex is on
    hold and excluded.
  - Canadian and Mexican crossings are not arcs: imports are supply at the crossing state, exports demand there.

**Supply.**
- 2024: EIA observed (Stage 1 base).
- 2030, 2035, 2045: Stage 1's AEO2026-indexed basin supply (Tables 59/60; Canada additive Table 64; LNG Table 61).

**Demand.**
- 2024: EIA consumption by state and end use (49 state files) plus exports by state (Mexico, Canada, LNG).
- Projected years:
  - end uses × AEO2026 Table 62 growth (census division × sector);
  - exports × Table 61;
  - lease and plant fuel × state production growth;
  - pipeline and distribution use × delivered-use growth.
- Texas is split as in Stage 1.
- Demand is scaled to total supply: ×1.031 in 2024, 1.07–1.10 later.
- State movement files give LNG exports of about 3.6 Tcf in 2024, against EIA's national ~4.3 Tcf; the scaling
  absorbs the gap.

**Objective.**
- Minimise Σ great-circle km × flow between hub centroids, plus 10⁶ per MMcf unserved and per MMcf spilled.
  - Centroids are the mean location of each hub's gas generators in EIA-860M; Vermont uses all its generators; GOM is
    28.5N 90.5W.
  - With fixed supply and demand only relative distances matter, so the $/km scale is irrelevant to flows.
- **Calibration** on 2024 (`calibration_grid.csv`): annual utilisation cap U ∈ {0.6 … 1.0} × per-arc hurdle ∈
  {0, 100, 300, 1000} km. The chosen setting has the lowest 2024 arc error among those that serve all 2024 demand
  (within 0.1%): **U = 0.9, hurdle 0**. Below 0.9, 2024 demand can't be met (0.6 leaves 2.8 Tcf unserved).

**Calibration result (2024).**
- State-arc weighted absolute error 0.61 (net of direction 0.57), R² 0.55.
- The LP moves 47.8 Tcf on interstate arcs against 67.0 Tcf of observed gross receipts. EIA's movements include
  cross-hauls (MS↔LA, AL↔MS, LA↔TX) that a min-cost LP never makes; the largest misses are those (MS→AL −2.1 Tcf,
  LA→MS −1.9 Tcf).
- Observed 2024 flows on arcs with no EIA capacity row (NH↔VT 152 Bcf each way, OH→LA 224 Bcf) and above listed
  capacity (PA→OH 1,533 Bcf vs 869 Bcf/yr) are flagged.
- Against Table 64 2025 at region level the error is 0.27.

**Projected years.**

| Year | Unserved (Bcf) | Where | Spilled | Binding arcs | Region error vs Table 64 |
|---|---|---|---|---|---|
| 2030 | 6 | VT | ID, NM | 23 | 0.33 |
| 2035 | 1,892 | FL 1,816, GA 69, VT 7 | PA 1,892 | 23 | 0.49 |
| 2045 | 4,871 | FL 2,315, GA 1,063, LA 685, CA 626, WI 145, SC 29, VT 7 | PA 4,871 | 25 | 0.62 |

- **Approved projects change nothing material** (same unserved; one more binding arc).
- **The binding constraint is Appalachian takeaway.** AEO2026's Marcellus/Utica growth (+72% 2025→2045) can't leave
  Pennsylvania on existing plus under-construction capacity.
- Arcs binding in every year: PA→OH, PA→NY, PA→MD, WV→KY, TN→GA, OK→AR, OK→MO, NM→AZ, ND→MN, ID→OR, NY→MA, CT→MA,
  DE→MD, MD→DC. From 2030 also OH→IN, OH→MI, WV→MD and WV→VA. TX_W→TX_E binds in 2024 (Matterhorn only 2/12),
  2030 and 2045.
- Which demand goes unserved is then arbitrary: the LP drops the most remote, so Florida's traced mix in 2035/2045 is
  meaningless.
- **Diagnostic variant `status_rule_min_expansion`** (not Stage 4). Extra capacity on existing arcs at 10,000 km +
  distance per MMcf, far above routing and far below unserved. It finds the least expansion that serves all demand:
  - 2035 needs 6.7 Bcf/d: WV→KY 3.1, PA→MD 2.2, OH→MI 1.1, VA→NC 0.3;
  - 2045 needs 22.3 Bcf/d: PA→OH 5.0, WV→KY 4.9, OH→MI 4.1, OH→IN 3.1, PA→MD 2.8, KY→TN 1.0, VA→NC 0.75, AL→FL 0.5,
    VA→TN 0.2.
  - Only Vermont stays unserved (7 Bcf, no arc). Region error vs Table 64 falls to 0.35 in both years.

**Traced basin shares vs Stage 1** (% of delivered gas; "distance" = total-variation distance from Stage 1 additive
balanced, pp; full table `zone_shares_vs_stage1.csv`):

**FL (p91, p101, p102)**

| Year | Source | App | Hay | Perm | EF | Barn | MidC | GOM | SJ | Rock | WCan | ECan | LNG | oth | Distance from Stage 1 (pp) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | Stage 1 | 34.2 | 30.5 | 9.4 | 5.8 | 1.0 | 7.9 | 7.1 | 0.1 | 0.4 | 0.3 | – | – | 3.0 |  |
| 2024 | 2a | 34.9 | 18.2 | 7.4 | 2.1 | 0.4 | 14.7 | 17.9 | 0.4 | 0.8 | – | – | – | 3.2 | 19.3 |
| 2030 | Stage 1 | 49.3 | 27.5 | 8.0 | 4.2 | 0.9 | 2.4 | 5.0 | – | – | – | – | – | 2.6 |  |
| 2030 | 2a | 31.5 | 23.5 | 7.7 | 2.9 | 0.5 | 12.7 | 17.5 | 0.1 | – | – | – | – | 3.6 | 23.9 |
| 2035 | Stage 1 | 62.8 | 18.5 | 4.9 | 3.3 | 0.5 | 3.3 | 4.4 | – | – | – | – | – | 2.2 |  |
| 2035 | 2a | – | – | – | – | – | – | – | – | – | – | – | – | 100.0 | 97.8 |
| 2035 | 2a + min. expansion | 64.7 | – | – | – | – | 12.6 | 19.4 | – | – | – | – | – | 3.3 | 27.3 |
| 2045 | Stage 1 | 77.1 | 5.2 | 3.3 | 2.2 | 0.1 | 6.1 | 3.0 | – | 0.2 | 0.4 | – | – | 1.8 |  |
| 2045 | 2a | – | – | – | – | – | – | – | – | – | – | – | – | 100.0 | 98.2 |
| 2045 | 2a + min. expansion | 80.8 | – | – | – | – | 16.3 | – | – | – | – | – | – | 2.9 | 15.0 |

**east TX (p57, p64-p67)**

| Year | Source | App | Hay | Perm | EF | Barn | MidC | GOM | SJ | Rock | WCan | ECan | LNG | oth | Distance from Stage 1 (pp) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | Stage 1 | – | 23.0 | 38.7 | 26.4 | 4.7 | 5.9 | 0.8 | 0.2 | 0.2 | – | – | – | – |  |
| 2024 | 2a | – | 22.0 | 38.9 | 25.6 | 4.4 | 8.3 | – | 0.8 | – | – | – | – | – | 3.1 |
| 2030 | Stage 1 | – | 23.9 | 43.2 | 22.6 | 4.8 | 4.4 | 0.7 | 0.3 | 0.2 | – | – | – | – |  |
| 2030 | 2a | – | 20.1 | 54.1 | 20.3 | 3.5 | 1.1 | – | 1.0 | – | – | – | – | – | 11.7 |
| 2035 | Stage 1 | 8.2 | 23.3 | 34.1 | 23.8 | 3.6 | 5.5 | 1.3 | – | – | – | – | – | 0.3 |  |
| 2035 | 2a | – | 18.5 | 53.4 | 23.5 | 2.2 | 1.6 | – | 0.8 | – | – | – | – | – | 20.1 |
| 2035 | 2a + min. expansion | – | 18.3 | 52.9 | 23.3 | 2.1 | 2.5 | – | 0.8 | – | – | – | – | – | 19.6 |
| 2045 | Stage 1 | 12.7 | 11.1 | 37.4 | 28.5 | 1.1 | 7.2 | 1.1 | 0.2 | 0.1 | 0.1 | – | – | 0.3 |  |
| 2045 | 2a | – | 10.3 | 50.2 | 27.6 | 1.3 | 9.6 | – | 1.0 | – | – | – | – | – | 16.2 |
| 2045 | 2a + min. expansion | – | 10.2 | 47.4 | 26.0 | 1.4 | 14.1 | – | 1.0 | – | – | – | – | – | 17.9 |

**VA (p99, p100, p118, p124)**

| Year | Source | App | Hay | Perm | EF | Barn | MidC | GOM | SJ | Rock | WCan | ECan | LNG | oth | Distance from Stage 1 (pp) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | Stage 1 | 99.9 | – | – | – | – | – | – | – | – | – | – | 0.1 | – |  |
| 2024 | 2a | 100.0 | – | – | – | – | – | – | – | – | – | – | – | – | 0.1 |
| 2030 | Stage 1 | 99.8 | – | – | – | – | – | – | – | – | – | – | 0.2 | – |  |
| 2030 | 2a | 99.9 | – | – | – | – | – | – | – | – | – | – | 0.1 | – | 0.1 |
| 2035 | Stage 1 | 99.8 | – | – | – | – | – | – | – | – | – | – | 0.2 | – |  |
| 2035 | 2a | 99.9 | – | – | – | – | – | – | – | – | – | – | 0.1 | – | 0.1 |
| 2035 | 2a + min. expansion | 99.9 | – | – | – | – | – | – | – | – | – | – | 0.1 | – | 0.1 |
| 2045 | Stage 1 | 99.8 | – | – | – | – | – | – | – | – | – | – | 0.2 | – |  |
| 2045 | 2a | 99.9 | – | – | – | – | – | – | – | – | – | – | 0.1 | – | 0.1 |
| 2045 | 2a + min. expansion | 99.9 | – | – | – | – | – | – | – | – | – | – | 0.1 | – | 0.1 |

**MA (p131)**

| Year | Source | App | Hay | Perm | EF | Barn | MidC | GOM | SJ | Rock | WCan | ECan | LNG | oth | Distance from Stage 1 (pp) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | Stage 1 | 81.8 | – | – | – | – | – | – | – | – | – | 15.5 | 2.6 | – |  |
| 2024 | 2a | 88.4 | – | – | – | – | – | – | – | – | – | 9.1 | 2.5 | – | 6.6 |
| 2030 | Stage 1 | 82.9 | – | – | – | – | – | – | – | – | – | 9.5 | 7.7 | – |  |
| 2030 | 2a | 84.8 | – | – | – | – | – | – | – | – | – | 8.5 | 6.7 | – | 2.0 |
| 2035 | Stage 1 | 83.4 | – | – | – | – | – | – | – | – | – | 9.4 | 7.3 | – |  |
| 2035 | 2a | 85.7 | – | – | – | – | – | – | – | – | – | 8.2 | 6.1 | – | 2.4 |
| 2035 | 2a + min. expansion | 85.7 | – | – | – | – | – | – | – | – | – | 8.2 | 6.1 | – | 2.4 |
| 2045 | Stage 1 | 82.7 | – | – | – | – | – | – | – | – | – | 9.7 | 7.6 | – |  |
| 2045 | 2a | 85.5 | – | – | – | – | – | – | – | – | – | 8.2 | 6.4 | – | 2.8 |
| 2045 | 2a + min. expansion | 85.5 | – | – | – | – | – | – | – | – | – | 8.2 | 6.4 | – | 2.8 |

**CA (p8-p11)**

| Year | Source | App | Hay | Perm | EF | Barn | MidC | GOM | SJ | Rock | WCan | ECan | LNG | oth | Distance from Stage 1 (pp) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | Stage 1 | – | – | 32.8 | – | – | 1.1 | – | 7.7 | 24.0 | 28.6 | – | – | 5.7 |  |
| 2024 | 2a | – | – | 56.2 | – | – | – | – | 9.2 | 7.0 | 21.9 | – | – | 5.6 | 24.9 |
| 2030 | Stage 1 | – | – | 36.2 | – | – | 0.9 | – | 5.8 | 19.5 | 31.1 | – | – | 6.3 |  |
| 2030 | 2a | – | – | 58.3 | – | – | – | – | 6.2 | 18.1 | 11.3 | – | – | 5.7 | 22.7 |
| 2035 | Stage 1 | – | – | 38.0 | – | – | 1.7 | – | 5.6 | 18.7 | 28.1 | – | – | 7.7 |  |
| 2035 | 2a | – | – | 59.6 | – | – | – | – | 6.7 | 14.8 | 12.3 | – | – | 6.5 | 22.8 |
| 2035 | 2a + min. expansion | – | – | 59.6 | – | – | – | – | 6.7 | 14.8 | 12.3 | – | – | 6.5 | 22.8 |
| 2045 | Stage 1 | – | – | 31.0 | – | – | 1.9 | – | 6.2 | 22.1 | 31.4 | – | – | 7.4 |  |
| 2045 | 2a | – | – | 62.0 | – | – | – | – | 8.5 | 19.7 | 1.1 | – | – | 8.8 | 34.6 |
| 2045 | 2a + min. expansion | – | – | 46.1 | – | – | – | – | 9.1 | 28.7 | 9.6 | – | – | 6.5 | 24.6 |

What the differences mean:
- **East TX, VA and MA** agree with Stage 1 within about 3 pp in 2024 (MA 6.6). From 2030 east TX differs by 12–20
  pp, because the LP sends more Permian gas through the new TX_W→TX_E pipes and no Appalachian gas reaches Texas.
- **Florida and California** differ by about 19–25 pp even in 2024. That is the min-cost routing, not the data:
  - Florida draws on the nearer Gulf/Mid-Continent pipes in place of Haynesville;
  - California takes more Permian via Arizona and less Rockies/Canada gas, against observed AZ→CA 898 and OR→CA
    686 Bcf.
- So Stage 1 (traced on observed flows) stays the reference for 2024. Stage 2a's flows are a feasibility and capacity
  check: what the network can carry under AEO's production, and where it needs pipe.

**Limits (Stage 2a).**
- Annual: no seasonal or peak-day constraint (Stage 2b/3).
- No cross-hauling, contract paths, tariffs or fuel losses.
- Supply fixed by basin.
- Crossings not capacity-limited.
- Distances are between generator-weighted centroids.
- Utilisation cap uniform.

## Stage 1 share fallback, NGMM capacity comparison (§104)

**Share fallback** (Tom, 2026-10-11; `config.yaml share_fallback`, `gasnet/limitations.share_fallback`). A projected
zone-year takes the zone's 2024 shares (`method` `fallback_2024`, 2024 `source_vintage`, `fallback` = reason) when
- a hub the zone draws on (`zone_hub_weights` weight > 0) has unmet local use above 25% of its floor
  (`unmet_local_use.csv`), or
- the zone's shares differ by more than 10 pp between the two equally good balancing solutions
  (`balance_degeneracy_by_zone.csv`, total-variation distance).

89 zone-years in 24 zones switch (`share_fallback.csv`):

| Zones | State | Years | Reason |
|---|---|---|---|
| p1–p4 | WA | 2028, 2030, 2040, 2045 | unmet local use 26–69% |
| p5–p7 | OR | all | unmet local use 29–100% |
| p45, p69, p70 | IA | 2040, 2045 | unmet local use 78–91% |
| p92 | TN | 2035 | unmet local use 57% (TN 2045 is 22%, below the threshold) |
| p54, p55, p71–p73, p84 | MO | all | degeneracy 15–27 pp |
| p80–p83 | IL | 2030, 2035 | degeneracy 11–13 pp |
| p130 | NH | all | degeneracy 10–13 pp |
| p129, p134 | VT, ME | 2030–2045 | degeneracy 11–13 pp |

- The change in these zone-years is 3.6–47 pp (total-variation distance from the balanced shares); largest in
  Missouri (47 pp) and Illinois (43 pp), smallest in WA/OR (3.6–4.6 pp), whose 2024 and projected mixes are close.
- **The example zones (FL, east TX p57/p64–p67, VA, MA, CA) do not switch in any year**; their shares are unchanged.
- No other output changes: `zone_basin_shares_no_fallback.csv` is byte-identical to the §103 `zone_basin_shares.csv`,
  and Stage 2a's comparison zones are unaffected.
- Fallback years are flagged rather than solved; a zone can switch in one year and not the next (e.g. VT 2028).

**NGMM pipeline capacity vs Stage 2a** (`gasnet/nems_capacity.py`, `python -m gasnet.cli nemscap` →
`outputs/stage2a/nems_capacity_vs_stage2a.csv`, `nems_ngmm_capacity_d011626a.csv`).
- **Source.** EIAgov/NEMS publishes one reference-case restart file, `output/aeo2026/cb2026/d011626a/restart.npz`
  (pinned). It is **run d011626a, not the published d021826b**, whose restart file is not in the repo. NGMM writes
  common block NGRPT to it: `NGCAPS(12,6,year)` (capacity entering each flow region, by arc) and `NGFLOWS` (Bcf/yr).
  The arcs are identified by matching NGFLOWS to Table 64; across all matched arcs and years 2025–2050 the d011626a
  flows are within 1.2% (weighted) of the published d021826b Table 64, so the run is a close proxy.
- **Endogenous builds.** NGMM allows builds after NoBuildYear 2027 (`ngassumptions.txt`). Its planned list
  (`ngcapacity.txt` PlannedCapacity, 2025–2028) has nothing across the Mid-Atlantic/Ohio → Eastern Midwest boundary.
- **AEO assumes new Appalachian takeaway, a lot of it:**

| Arc (NGMM regions) | 2025 capacity, Bcf/d | Added by 2035 | Added by 2045 | Stage 2a min expansion 2035 | 2045 |
|---|---|---|---|---|---|
| Mid-Atlantic/Ohio → Eastern Midwest | 19.2 | +11.1 | +22.5 | +4.2 (WV→KY 3.1, OH→MI 1.1) | +12.2 (WV→KY 4.9, OH→MI 4.1, OH→IN 3.1, VA→TN 0.2) |
| Eastern Midwest → South Central | 11.9 | +1.5 | +11.4 | 0 | 0 |
| Eastern Midwest → Southeast | 0.2 | +0.4 | +0.8 | 0 | 0 |
| Mid-Atlantic/Ohio → Southeast | 3.4 | 0 | +0.7 | +0.3 (VA→NC) | +0.8 (VA→NC) |
| South Central → Florida | 4.9 | 0 | +1.0 | 0 | +0.5 (AL→FL) |
| Northern Great Plains → Eastern Midwest | 4.3 | +0.6 | +0.6 | 0 | 0 |
| Within Mid-Atlantic/Ohio (not an NGMM arc) | – | – | – | +2.2 (PA→MD) | +7.8 (PA→OH 5.0, PA→MD 2.8) |
| Within Eastern Midwest (not an NGMM arc) | – | – | – | 0 | +1.0 (KY→TN) |

- NGMM's Mid-Atlantic → Eastern Midwest capacity goes 7,001 → 11,035 → 15,217 Bcf/yr (2025/2035/2045), with
  flows 4,297 → 9,455 → 12,408 Bcf. Much of it continues south: Eastern Midwest → South Central flow reaches 7,179 Bcf
  in 2045 (Table 64: 7,200), i.e. Appalachian gas to the Gulf through the Midwest.
- Stage 2a figures are nameplate Bcf/d (the annual expansion ÷ the 0.9 utilisation cap), comparable with NGCAPS.
- **Stage 2a needs less than NGMM builds** across the boundary: 4.2 vs 11.1 Bcf/d by 2035, 12.2 vs 22.5 by 2045
  (6.7 and 22.3 Bcf/d in all, including arcs inside the Mid-Atlantic region that NGMM does not represent). Stage 2a
  serves demand at least cost and has no reason to send Appalachian gas to the Gulf; NGMM does (Eastern Midwest →
  South Central +11.4 Bcf/d by 2045). The two start from the same boundary capacity: Stage 2a's EIA-based 2025
  nameplate is 19.1 Bcf/d, NGMM's 19.2.
- Two NGCAPS rows/columns carry flow but match no Table 64 arc (row 12 and some extra columns, e.g. into FL and
  AZ_NM); they are kept as `unidentified` in the full file and do not enter the comparison.
- **The file that would have d021826b:** `output/aeo2026/cb2026/d021826b/restart.npz` (or `restart.rlx`) in
  EIAgov/NEMS; it is not published. NGMM's own capacity report pages (`models/ngas/Pages/Capacity Additions.xml`)
  are AIMMS pages stored as Git LFS pointers and hold no data.

## Share paths and the post-solve upstream methane tool (§105)

**Two share paths for projected years.**
- `aeo` = `zone_basin_shares.csv` (Stage 1 as accepted, with the §104 fallback). It carries AEO2026's production on
  NGMM's network, which builds +11.1 / +22.5 Bcf/d of Mid-Atlantic/Ohio → Eastern Midwest takeaway by 2035/2045 (§104).
- `constrained_pipe` = `zone_basin_shares_constrained_pipe.csv`: no interregional capacity beyond what exists or is
  under construction (status rule). **Method: each zone holds its 2030 shares for 2035, 2040 and 2045**
  (`config.yaml constrained_pipe_hold_year`). Why 2030 and why a hold:
  - 2030 is the last model year Stage 2a serves on status-rule capacity (all demand but Vermont's 6 Bcf); 2035 and
    2045 are infeasible (1.9 / 4.9 Tcf stranded in PA).
  - A Stage 2a re-route does not give a better answer. With capacity fixed, demand can only be served if production
    shifts away from Appalachia, and which basins replace it is a supply-and-price question that Stage 2a (supply
    fixed by basin) cannot answer. Stage 2a's least-cost routing also differs from observed flows by 19–25 pp in FL and
    CA even in 2024, larger than the effect being measured.
  - **Caveat (hold year):** Stage 1's 2030 shares already include NGMM's 2028–2030 endogenous builds (+6.1 Bcf/d
    across the Mid-Atlantic/Ohio → Eastern Midwest boundary by 2030; 2028 already carries part of them), which
    Stage 2a shows are not needed to serve 2030 demand. A 2030 hold is therefore somewhat more Appalachian than a
    strict no-build network would be. The hold year is a setting: `config.yaml constrained_pipe_hold_year` (2030
    default; 2028 or 2024 allowed, the stricter choices), `python -m gasnet.cli run --hold-year 2028` to rewrite
    `zone_basin_shares_constrained_pipe.csv`, or `upstream --hold-year 2024|2028|2030` to build the path on the fly
    from `zone_basin_shares.csv` without re-running Stage 1. Any other year stops with an error.

Shares under the two paths (%, basins ≥ 0.5%; 2024–2030 identical):

| Zone | 2035 aeo | 2035/2045 constrained_pipe (= 2030) | 2045 aeo |
|---|---|---|---|
| FL p101 | App 62.8, Hay 18.5, Perm 4.9, GOM 4.4, EF 3.3, MidC 3.3, other 2.2 | App 49.3, Hay 27.5, Perm 8.0, GOM 5.0, EF 4.2, other 2.6, MidC 2.4, Barn 0.9 | App 77.1, MidC 6.1, Hay 5.2, Perm 3.3, GOM 3.0, EF 2.2, other 1.8, Bakken 0.5 |
| east TX (p57, p64–p67, mean) | Perm 34.1, EF 23.8, Hay 23.3, App 8.2, MidC 5.5, Barn 3.6, GOM 1.3 | Perm 43.2, Hay 23.9, EF 22.6, Barn 4.8, MidC 4.4, GOM 0.7 | Perm 37.4, EF 28.5, App 12.7, Hay 11.1, MidC 7.2, GOM 1.1, Barn 1.1 |
| VA p99 | App 99.8 | App 99.8 | App 99.8 |
| MA p131 | App 83.4, E. Can 9.4, LNG 7.3 | App 82.9, E. Can 9.5, LNG 7.7 | App 82.7, E. Can 9.7, LNG 7.6 |
| CA p10 | Perm 38.0, W. Can 28.1, Rock 18.7, other 7.7, SJ 5.6, MidC 1.7 | Perm 36.2, W. Can 31.1, Rock 19.5, other 6.3, SJ 5.8, MidC 0.9 | W. Can 31.4, Perm 31.0, Rock 22.1, other 7.4, SJ 6.2, MidC 1.9 |

**Headline difference** (kg CH4 per MWh of gas generation, measured central, 7,754 Btu/kWh, upstream + T&S, each
year's own shares and intensities):

| Zone | 2035 aeo → constrained | 2045 aeo → constrained | Same with no_further_reductions (2035; 2045) |
|---|---|---|---|
| FL p101 | 1.18 → 1.28 (+0.10, +8%) | 1.11 → 1.28 (+0.16, +15%) | 1.56 → 1.69 (+8%); 1.49 → 1.69 (+14%) |
| east TX p64 | 1.80 → 1.94 (+0.14, +8%) | 1.87 → 1.94 (+0.08, +4%) | 2.58 → 2.81 (+9%); 2.68 → 2.81 (+5%) |
| VA p99 | 0.83 → 0.83 (0) | 0.83 → 0.83 (0) | 1.12 → 1.12 (0) |
| MA p131 | 0.94 → 0.94 (+1%) | 0.94 → 0.94 (0) | 1.22 → 1.23 (0) |
| CA p10 | 2.19 → 2.17 (−1%) | 2.16 → 2.17 (0) | 3.35 → 3.32 (−1%); 3.32 → 3.32 (0) |

Without AEO's new Appalachian pipe, FL and east TX keep more Haynesville/Permian/Gulf gas, so their upstream methane
is 4–15% higher; VA and MA are Appalachian either way and CA is not on the Appalachian path. The share path matters less
than the methane path (current_rules vs no_further_reductions: −24 to −35% in 2045) or the measured/inventory basis
(3–4×). (These use projected shares; the research note's worked example holds the 2024 shares.)

**Post-solve upstream CH4 tool** (`gasnet/upstream.py`; no model change). On the VM, from `gas_network/`:

```
python -m gasnet.cli upstream /path/to/case/outputs                       # all options, writes outputs/upstream_ch4/
python -m gasnet.cli upstream /path/to/case/outputs --out-dir /path/to/dir \
    --share-path aeo constrained_pipe --basis measured --methane-path current_rules [--hold-year 2028] \
    [--gas-fuels NaturalGas ...]
```

- **Reads** from the Switch outputs folder: `GenFuelUseRate.csv` (generator, timepoint, fuel, MMBtu/h) and
  `dispatch.csv` (zone, period, `tp_weight_in_year_hrs`, `Energy_GWh_typical_yr`,
  `DispatchEmissions_tCO2_per_typical_yr`); timepoint ids equal timestamps, as for `report`. When present,
  `tracked_demand_onsite_fuel_use.csv` (tracked-demands module, `tom/tracked-demands-v2`). On-site generators are
  ordinary Switch generators, so that burn is already in `GenFuelUseRate.csv`: the file labels it
  (`onsite_gas_mmbtu`) and is not added again; a row missing from `GenFuelUseRate.csv` is added with no MWh and
  flagged.
- **Gas fuels:** `config.yaml gas_fuels`, case-insensitive (`--gas-fuels` overrides); a fuel with "gas" in its name
  that is not listed stops the run. A generator burning gas with another fuel gets the gas fraction of its MWh and
  stack CO2.
- **Method:** zone gas MMBtu (typical year of the period) × Σ basin share × basin intensity + gas MMBtu × transmission
  & storage (0.0056 / 0.0376 / 0.0483 kg/MMBtu). Distribution is excluded. Shares: the latest share year not after
  the period. Intensities (`data/basin_methane_intensity.csv`):
  - `measured` (IDEA-01 default): `upstream_total` kg/MMBtu rows by year and methane path, low/central/high;
  - `inventory`: GHGRP RY2023 prod + G&B rates (W. Canada: the `inventory_derived` NIR-level row, 0.81%; E. Canada and
    LNG imports keep measured values), moved over time by the measured path's multipliers; one value for all levels.
- **Writes** `upstream_ch4_by_zone.csv`, `_by_state.csv`, `_by_period.csv`: one row per zone/state/period ×
  share path × basis × methane path × level (with `share_year` and `share_method`, e.g.
  `constrained_pipe_hold_2028`), with gas MMBtu, on-site gas MMBtu, gas MWh, stack tCO2 (from the same
  run), upstream and T&S t CH4, total t CH4, t CO2e at GWP100 (29.8) and GWP20 (82.5), kg CH4/MWh, and upstream
  CO2e as % of stack CO2.
- **Tests** on toy Switch outputs: known answer, gas/hydrogen blend, coal excluded, on-site gas labelled not doubled,
  an on-site row only in the tracked-demand file, an unlisted gas-like fuel, the inventory multipliers, and the hold
  year (2028 path, on-the-fly 2024 path, invalid year).

## Limits

- **Full mixing** at each hub is accounting, not physics. Contract paths and displacement are ignored.
- **Annual flows** hide winter routing. State × month flows exist only inside NGMM, so seasonal shares wait for
  Stage 2.
- **Projected arcs are scaled, not solved.** Their pattern inside each region is held at 2024; Stage 2's solved
  flows replace them.
- **Shares are by delivery hub, not plant.** A Texas zone's shares are a gas-MW-weighted mix of its sub-hubs.

## Later options (not in Stage 1)

- A ReEDS-style AEO price–demand supply curve by region as a fallback to the full network (Tom, Step 1 decisions).
- The Stage 2 calibration target: the S0 path price at AEO burn, via markups.
- Stage 2 objective: non-power gas included in the supply cost, reported separately, with the objective also reported
  net of a fixed non-power baseline.
