# Gas network, Stage 1: where each zone's gas comes from

`gas_network/` (CHANGES §93) estimates, for every Switch load zone and model year, the share of its delivered natural
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
python -m pytest -q             # 24 tests; the raw-data ones skip if data/raw is empty
python -m gasnet.cli pin        # only after a deliberate data refresh: rewrites data/SOURCES.yml
```

`fetch` needs www.eia.gov and raw.githubusercontent.com. `run` takes about 20 s.

## Outputs (`gas_network/outputs/`, committed)

| File | Contents |
|---|---|
| `zone_basin_shares.csv` | `zone, year, basin, share, method, source_vintage` for the 134 zones, 2024 and 2028/2030/2035/2040/2045, default method (`trace_aeo_additive_balanced`). Shares sum to 1 per zone and year (rounded to 6 decimals). |
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
(model years, default) and `trace_aeo_ratio_balanced` (sensitivity), below. `source_vintage` codes:
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

**Colorado San Juan split** (§94).
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

**Projections** (ASSUMPTION; §94). AEO2026's tables start in 2025 and EIA's state flows end in 2024. Each quantity
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
