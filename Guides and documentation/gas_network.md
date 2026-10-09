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
python -m pytest -q             # 18 tests; the raw-data ones skip if data/raw is empty
python -m gasnet.cli pin        # only after a deliberate data refresh: rewrites data/SOURCES.yml
```

`fetch` needs www.eia.gov and raw.githubusercontent.com. `run` takes about 20 s.

## Outputs (`gas_network/outputs/`, committed)

| File | Contents |
|---|---|
| `zone_basin_shares.csv` | `zone, year, basin, share, method, source_vintage` for the 134 zones, 2024 and 2028/2030/2035/2040/2045. Shares sum to 1 per zone and year (rounded to 6 decimals). |
| `hub_basin_shares.csv` | The same by gas hub (state; TX_E/TX_N/TX_W; GOM), with `throughput_mmcf` |
| `region_crosscheck.csv` | Basin mix of each AEO Table 64 flow region three ways (below) |
| `table64_arcs_check.csv` | Interregional flows: EIA 2024, Table 64, and the projected bundles, Bcf |
| `texas_routing.csv` | Intra-Texas transfers by year, NGMM capacity, and the Texas disposition scale |
| `zone_hub_weights.csv`, `tx_county_hubs.csv` | Zone → hub weights; Texas county → district → hub and gas MW |
| `flags.csv`, `run_summary.json` | Every held, scaled or assigned item (below) and the run summary |

`method`: `trace_eia` (2024: proportional sharing on EIA state-to-state movements); `trace_aeo_indexed` (model
years: the same network with production and flows indexed to AEO2026). `source_vintage` codes:
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
| San Juan | New Mexico West |
| Rockies (other) | CO, WY, UT |
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

**Projections** (ASSUMPTION). AEO2026's tables start in 2025 and EIA's state flows end in 2024. So every quantity keeps
its 2024 base and moves with AEO2026's growth from 2025 to the model year:
- **Production** by basin, × AEO series(y)/AEO series(2025):
  - Table 60 plays for Appalachia (Marcellus/Utica), Haynesville, Permian, Eagle Ford, Barnett and Bakken;
  - Table 59 regions for Mid-Continent, Rocky Mountain (San Juan and Rockies), Gulf offshore and Lower 48 onshore
    (other).
  - The series is the nearest published one, not an exact match. For example, Appalachia's conventional gas moves
    with shale.
- **Canada imports** at each crossing: × its Table 64 Canada arc. WA and ID have their own rows; the others use their
  region's row.
- **LNG imports**: × Table 61 LNG imports. Mexican imports are held (AEO2026 shows none).
- **Interregional flows**: each bundle of 2024 state arcs between two Table 64 regions × Table 64(y)/Table 64(2025).
  - The ID → OR/WA arcs carry both Canadian gas through Idaho and Rockies gas. That bundle is indexed by the sum of
    Table 64's "Canada (through Idaho)" and "Rocky Mountains–Great Plains" rows into Oregon/Washington.
  - Arcs Table 64 does not list, or lists as 0 in 2025, are held at 2024 and flagged.
- **Intra-region arcs**: × the growth of the region's own supply plus interregional inflow.
- **GOM outflows**: × the Gulf offshore index.
- **Texas**: re-routed each year. Consumption × Table 62 West South Central by sector; Mexico and LNG exports × Table
  61 (ASSUMPTION: Texas keeps its 2024 share of U.S. LNG exports).

Indexing was chosen over Table 64's levels because NGMM's regional flows are model results. On several arcs they
differ from EIA's movements by more than 3× (flags `arc_level_mismatch_indexed`), e.g. New Mexico → South Central:
EIA 2024 220 Bcf, Table 64 2025 1,015 Bcf.

The restart file in the NEMS repo (pre-release run d011626a, not the published AEO2026 d021826b) is **not** used
anywhere in Stage 1.

## Results (example zones; % of delivered gas)

**2024**

| Zones | Appalachia | Haynesville | Permian | Eagle Ford | Barnett/other TX | Mid-Con/Anadarko | San Juan | Rockies (other) | Fed. GOM | W. Canada | E. Canada | LNG imports | other |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FL (p91, p101, p102) | 34.2 | 30.5 | 9.4 | 5.8 | 1.0 | 7.9 | – | 0.5 | 7.1 | 0.3 | – | – | 3.0 |
| VA (p99, p100, p118, p124) | 99.9 | – | – | – | – | – | – | – | – | – | – | 0.1 | – |
| MA (p131) | 81.8 | – | – | – | – | – | – | – | – | – | 15.5 | 2.6 | – |
| CA (p8–p11) | – | – | 32.8 | – | – | 1.1 | 4.8 | 26.9 | – | 28.6 | – | – | 5.7 |
| TX_W zones p48, p59, p62 | – | – | 90.3 | – | – | 8.8 | 0.4 | 0.4 | – | – | – | – | – |
| p61 (W 56/N 22/E 22) | – | 11.1 | 66.6 | 5.8 | 5.0 | 10.6 | 0.3 | 0.4 | 0.2 | – | – | – | – |
| p63 (N 79/E 21) | – | 26.8 | 34.3 | 5.6 | 15.5 | 17.0 | 0.2 | 0.5 | 0.2 | – | – | – | – |
| TX_E zones p57, p64–p67 | – | 23.0 | 38.7 | 26.4 | 4.7 | 5.9 | 0.2 | 0.2 | 0.8 | – | – | – | – |

**2045**

| Zones | Appalachia | Haynesville | Permian | Eagle Ford | Barnett/other TX | Mid-Con/Anadarko | San Juan | Rockies (other) | Fed. GOM | W. Canada | E. Canada | LNG imports | other |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FL | 65.9 | 10.3 | 7.0 | 4.9 | 0.2 | 5.3 | – | 0.2 | 3.6 | 0.3 | – | – | 2.0 |
| VA | 99.9 | – | – | – | – | – | – | – | – | – | – | 0.1 | – |
| MA | 89.5 | – | – | – | – | – | – | – | – | – | 2.9 | 7.6 | – |
| CA | – | – | 27.2 | – | – | 2.1 | 3.0 | 33.1 | – | 27.0 | – | – | 7.5 |
| TX_W zones | – | – | 88.7 | – | – | 10.4 | 0.2 | 0.6 | – | – | – | – | – |
| TX_E zones | – | 12.6 | 44.2 | 32.5 | 1.6 | 7.8 | 0.1 | 0.3 | 0.7 | – | – | – | – |

Florida turns Appalachian over time because Table 64's Mid-Atlantic → Eastern Midwest → South Central flows roughly
triple. New England loses most of its Canadian gas: Table 64's Canada → New England falls from 137 Bcf (2025) to
25 Bcf (2045).

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
2. **Table 64 vs EIA levels.** Five region pairs differ by more than 3× (flag `arc_level_mismatch_indexed`):
   NM→South Central, Eastern Midwest→Mid-Atlantic, Eastern Midwest→Southeast, Rockies→AZ/NM and South
   Central→Rockies.
   - Indexing keeps the EIA level but can amplify a small base. Rockies → AZ/NM grows 4.3× by 2045 (290 →
     1,237 Bcf).
   - Arcs Table 64 doesn't carry are held at 2024: TX→NM 390 Bcf, the Mid-Atlantic→South Central 224 Bcf, New
     England→Mid-Atlantic 123 Bcf, and six smaller ones (81 Bcf or less).
3. **Texas.**
   - Dispositions are scaled by 0.865 (2024) and 0.80–0.92 later.
   - Intra-Texas flows exceed NGMM's 2023 capacity in every year (above).
4. **Corrections to the Step 1 note's scratch numbers.** The scratch parser missed two items:
   - Mississippi's 134 Bcf of receipts from the federal Gulf ("Receipts from", lower case);
   - Arizona's 1.6 Bcf of Mexican imports (an "Imports + Intransit" column).

   With both, Florida 2024 is Appalachian 34.1% (not 34.6%) and federal GOM 7.1%. The test reproduces the scratch
   numbers with both removed and the corrected values with them.
5. **Basin labelling** (ASSUMPTION):
   - whole RRC districts are labelled by their main basin; in Table 9, shale is 59% of District 4 and 75% of
     District 6;
   - Colorado's San Juan-basin gas (La Plata, Archuleta) is in Rockies (other);
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
