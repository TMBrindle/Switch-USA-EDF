# Gas network for Switch-USA-EDF: Step 1 design note

Branch `tom/gas-network` (from `tom/s0-v3.1` at 9d68751). Design only, no code. For Tom's approval.
Everything below was read from the files and pages listed under Sources, opened 2026-10-09.

## Summary

- **NEMS gas is now state-level and monthly, not 12 regions.** Since the NGTDM was replaced, the NEMS Natural Gas Market
  Module (NGMM, AIMMS/CPLEX) solves one QP per year over 12 months on a network of **state hubs** (Texas split into
  TX_E/TX_N/TX_W) plus 2 Canadian and 5 Mexican regions and 16 border crossings. It maximises consumer plus producer
  surplus minus transport cost. Supply comes from state and sub-state curves, and capacities are state-to-state. The
  AEO2026 source and inputs are on GitHub (EIAgov/NEMS, `models/ngas/`), mostly as plain-text AIMMS data.
- **Mapping is simpler than expected.** Every one of the 134 ReEDS zones lies inside one state (`hierarchy.csv`), so
  zone → NGMM state hub needs no EMM step. Only Texas zones need a county split. EMM/NGEMM only matter for comparing
  prices with S0 and AEO.
- **Stage 1 can be done now on published data**: proportional sharing ("flow tracing") over EIA's 2024
  state-to-state movements, rolled forward with AEO2026 Table 64 (flows between NGMM regions) and Tables 59/60
  (production by region and play). Worked numbers are below.
- **Stage 2** fits Switch as an LP: about 75 nodes, about 214 arcs, 3 seasons per period, with NGMM's own supply and
  tariff curves as step tranches. It adds well under 10k rows per period. A markup calibration makes the
  endogenous delivered price reproduce the S0 STEO→AEO path whenever Switch burns AEO's gas.

## a. The NEMS gas module as published

**Edition.** The latest is AEO2026: run `cb2026.d021826b`, released April 2026, "Counterfactual Baseline". This is the
same edition as S0's fuel prices (`s0_workflow/data/fuel/SOURCES.yml`). Sources:
- EIAgov/NEMS at commit 1bfbb2a (26 Jun 2026; `scedes.cb2026` and the other AEO2026 cases).
- *Assumptions to AEO2026: NGMM* (April 2026).
- *NGMM Model Documentation 2025* (July 2025). No AEO2026 edition of the full documentation was found.

| Element | NGMM representation | Machine-readable source |
|---|---|---|
| Nodes | State hubs (`States_`: 48 states + DC, TX_E/TX_N/TX_W), CN_E/CN_W, 5 Mexico regions, 16 border-crossing nodes (`BorderCrossings_`). Alaska is solved separately. | `models/ngas/input/ngsetmap.txt` |
| Supply nodes | One per state, plus sub-state nodes (TX RRC districts 1–10, NM_E/W, LA_N/S, AL_N/S, MS_N/S), state and federal offshore, and GOM. HSM passes expected production by **84 oil and gas districts**. | `ngsetmap.txt`; HSM outputs in the restart file (below) |
| Arcs and capacity | 214 directional state-to-state arcs, capacity in MMcf/d by year (history to 2023, some to 2025). This is EIA's State-to-State Capacity data. Planned projects come online in November of their in-service year. | `models/ngas/data/ngcapacity.txt` (text). The `EIA_StatetoStateCapacity.xlsx` in the repo is a Git LFS stub (the real file isn't checked out). |
| Tariffs | A 5-step variable tariff curve per arc (utilisation 0–1.4 of capacity → $/Mcf adder), plus a fixed arc fee and gathering and storage fees | `data/NGVarTarCurve.txt`, `input/ngvartar.txt` |
| Fuel use | Loss factors on each arc (IN/OUT, about 0.03–1%). Storage fuel is 0.4% of gross storage flows. Distribution loss is 0.3–6.2% depending on the state. Lease and plant fuel use historical ratios. | `data/NGPipelineFuelFactors.txt`, `input/ngassumptions.txt` |
| Supply curves | Non-associated gas: 5-segment piecewise-linear curve around (HSM expected quantity, previous-year price). Option 2 has segments at ±3%/±9% and slopes 0.8/0.7/0.5/0.3/0.2. Associated gas is fixed. Mexico production is set in NGMM; Canada comes from HSM. | Assumptions to AEO2026, NGMM, Table 1. Quantities are HSM outputs. |
| Non-power demand | Fixed in the QP. Census-division annual demand by sector (RDM/CDM/IDM/TDM) is split to state × month using 5-year historical shares. Canada and Mexico demand follow IEO2023. | History: `input/ngeia.txt` (state × month × sector, from 1990). Projections: AEO Table 62. |
| Power demand | EMM passes gas burn and receives prices by **17 NGEMM regions × 3 seasons** (peak Dec–Mar, offpeak Jun–Sep, shoulder the remaining months). NGMM splits these to state × month. | State → NGEMM map: `ngsetmap.txt map_State_NNGEMM`. Default gas region for new builds by EMM region: `input/emm/emm_db/NEMS_INPUT.db`, `V_EMM_ECPDAT_REG.EPNGRG`. Existing plants use their own state. |
| LNG and exports | LNG export demand curves per terminal state, using netbacks to Europe and Asia. Pipeline trade with Canada and Mexico. | AEO2026 assumptions Tables 2–7; `input/nglngexp.txt` |
| Storage | Exogenous: state × month injections and withdrawals at their 5-year historical average, scaled to zero net over the year | `ngeia.txt` history |
| Time | Monthly within each projection year. Capacity expansion is a separate QP solved for January and August, with design weather (res/com × state factor of about 1.1–2.1; industrial and power × 1.1) and additions capped at 40% of existing capacity | `ngassumptions.txt WeatherFactor1/2` |
| Interregional flows | The QP solution (hub shadow prices are the spot prices; delivered price = spot + markup) | — |

Notes on what's in the repo:
- `output/aeo2026/cb2026/d011626a/restart.npz` contains NEMS global data, including:
  - `NGRPT/NGFLOWS` and `NGCAPS` (12×6 region arcs × year), the source of Table 64;
  - `UEFDOUT/SQNGELGR` (power burn by NGEMM region × season);
  - `NGTDMOUT/SPNGELGR` (seasonal power prices);
  - `OGSMOUT/OGRNAGPRD` and `OGADGPRD` (production by 84 districts × gas type).
- **However, it is run d011626a, not the published d021826b.** Its district production also double-counts against
  Table 59 (a district sum gives 78 Tcf in 2025 against 38.8 Tcf published), so subtotal rows need the OGDIST
  dictionary, which isn't in the repo.
- The NGMM itself can't be run here: it needs AIMMS and CPLEX, both commercial. We re-implement its structure; we don't
  run it.

## b. AEO outputs for stage 1

All are AEO2026 supplemental tables (`https://www.eia.gov/outlooks/aeo/supplement/excel/suptab_NN.xlsx`), run d021826b,
covering 2025–2050:
- **Table 64, "Primary Natural Gas Flows Entering NGMM Region from Neighboring Regions"** (browser id 94-AEO2026).
  - Gross directional annual flows (Bcf) on about 40 arcs among 11 flow regions plus Canada.
  - The 11 regions: New England, Mid-Atlantic & Ohio, Eastern Midwest, Southeast, Florida, South Central,
    Rockies–Great Plains, Northern Great Plains, Arizona/New Mexico, California, Oregon/Washington.
  - State membership is in the table footnotes and in `map_hub_Region_Flow`.
  - Anomaly: "Into Oregon and Washington from Rockies" equals "Canada (into Washington)" in every year. This looks
    like a publication error; ask EIA.
- **Table 59** (id 81): Lower 48 dry production by HSM supply region (East, Gulf Coast, Midcontinent, Southwest,
  Rocky Mountain, Northern Great Plains, West Coast, plus three offshore regions).
- **Table 60** (id 76): shale gas by play (Marcellus/Utica, Haynesville, Permian, Eagle Ford, Barnett, Bakken, Other).
- **Table 61**: imports and exports, national only (pipeline Canada/Mexico, LNG).
- **Table 62**: consumption by sector and census division.
- **Table 13**: national supply, disposition and Henry Hub.
- Supplement `sup_elec` 54.1–54.25: power-sector gas price and consumption by EMM region (S0 already uses these).

Not published: state-level flows, production by state, LNG exports by state, and NGEMM seasonal power burn. Those exist
only inside NGMM or in the unpublished restart run.

## c. Mapping chain

**zone → state hub** (direct). In `hierarchy.csv` (`st`) each ReEDS zone lies in exactly one state, so no shares
are needed.
- Texas has 11 zones, which must go to TX_E (RRC districts 1, 2, 3, 4, 6), TX_N (5, 7B, 9) or TX_W (7C, 8, 8A, 10).
- Method: county → ReEDS zone (ReEDS `county2zone`), then county → RRC district (Texas RRC). Zones that straddle
  districts take the load-share (or, for gas burn, the gas-capacity-share) weighted mix.

**state hub → NGEMM** uses `map_State_NNGEMM`. It is only needed to compare with NEMS seasonal power burn and prices.

**zone → EMM** (`zone_emm.csv`, from crosswalk_v7) stays the link to S0's price path.
- The five split zones (p7, p8, p21, p24, p89) don't matter for gas, because each lies in one state.
- **The Virginia/Dominion issue doesn't touch the gas mapping.** p99, p100, p118 and p124 all go to the VA hub
  whatever their EMM region. It only affects which EMM price each VA zone's calibrated markup is anchored to.
- Your "p13" is Nevada in `hierarchy.csv`. Did you mean EMM region 13 (PJMD, which is p99/p100)?

**Regions split across gas regions.** Below the state level this can only happen in Texas (handled above). EMM
regions that span several states are irrelevant once the mapping goes via state.

## d. Stage 1 method: proportional sharing

**Method.** At each node n, gas from all sources is assumed fully mixed (the Bialek/Kirschen proportional-sharing
rule from electricity flow tracing). Let:
- T_n = P_n + Σ_m F_mn be the node's throughput (own supply plus all receipts);
- P_n,b be supply at n from basin b (own production, GOM, Canada, LNG imports);
- F_mn be the gross directional flow from node m to node n.

Then the basin shares s_n,b solve the linear system

  T_n s_n,b = P_n,b + Σ_m F_mn s_m,b   ⇔   (diag(T) − Fᵀ) S = P.

The system is non-singular whenever every node has throughput. Every outflow and every local burn at n carries s_n.
Only production by basin and gross flows are needed; consumption, exports and storage drop out. A zone's share is its
hub's share. For a Texas zone, use the share of its TX sub-hub.

**Base year 2024** (EIA Natural Gas Annual data, released 2026-09-30): 209 state-pair receipt flows, state dry
production, federal GOM as its own node, and international receipts. Results from a scratch run (not committed), as %
of each hub's gas:

| Zones (hub) | Through-put | Appalachia | LA (Haynesville + Gulf) | Texas (all) | Mid-Con | Fed. GOM | NM (Permian/San Juan) | Rockies | Canada/LNG imports |
|---|---|---|---|---|---|---|---|---|---|
| p91, p101, p102 (FL) | 1,732 Bcf | 34.6 | 27.0 | 21.7 | 8.0 | 4.8 | – | – | – |
| p99, p100, p118, p124 (VA) | 1,535 | 99.9 | – | – | – | – | – | – | – |
| p131 (MA) | 489 | 81.8 | – | – | – | – | – | – | 18.2 |
| p8–p11 (CA) | 2,162 | – | – | 3.8 | 0.9 | – | 34.0 | 26.9 | 28.6 (+5.7 CA own) |

Sensible: Florida (receipts AL 1,356, GA 375 Bcf) mixes Appalachian and Gulf gas; Virginia is Appalachian; New
England adds Canada; California mixes Permian/San Juan (via AZ), Rockies (via NV) and Canada (via OR).

**Projections.** Keep the state network, but for each AEO year:
1. Scale each state-pair arc so that each region pair's total matches Table 64 (an RAS-type fit over the arcs inside
   each region-pair bundle).
2. Set state production by basin from Table 59/60, split to states with 2024 state shares.

Table 64 shows why this matters. Flows from Eastern Midwest into South Central rise from 1,994 Bcf (2025) to
6,167 Bcf (2040), because Appalachian gas moves south to the Gulf LNG terminals. So Florida's Appalachian share will
rise. A region-level run (11 regions + Canada) is reported alongside as a check. Stage 2's solved flows replace this
whole step once they exist.

**Hook.** The output is a table `zone, year, basin, share` and, if a model run is supplied, `basin_gas_mmbtu`
(= share × the zone's gas burn from Switch output). A `basin_intensity.csv` (basin, year, kg CH₄/MMBtu, source)
from the separate research task gives upstream methane; until it exists the column stays empty.

**Limits.**
- (i) Full mixing ignores contract paths and displacement. Gas tracing is accounting, not physics.
- (ii) Annual flows hide winter swings; state × month flows exist only inside NGMM, so seasonal shares wait for
  Stage 2.
- (iii) EIA reports Texas as one state. Splitting Permian, Haynesville-TX, Eagle Ford and Barnett needs RRC district
  production (public) and an assumed routing inside Texas. Until then "Texas (all)" is a single basin.
- (iv) New Mexico combines Permian and San Juan. EIA's county or play data, or the HSM districts, can split them.
- (v) Projection arcs are scaled, not solved, so their pattern inside a region is held at 2024.

## e. Stage 2 formulation sketch (Pyomo, opt-in `study_modules/gas_network.py`)

**Sets.**
- `GAS_HUBS` (state hubs + TX sub-hubs + CN/MX + border nodes, about 75).
- `GAS_ARCS ⊂ hubs²` (about 214).
- `GAS_SUPPLY_NODES` (state and sub-state nodes, GOM, CN), with `SUPPLY_STEPS` (5 + a backstop) and `TARIFF_STEPS` (5).
- `GAS_SEASONS` = {peak, shoulder, offpeak}. Each Switch timeseries maps to a season by its month.
- `ZONE_HUB[z]`, shared for Texas zones.

**Variables** (per period p and season s), all non-negative:
- `GasSupply[n,k,p,s]` ≤ step width;
- `GasFlow[a,k,p,s]` ≤ step width × capacity × days(s);
- `GasSlack[n,p,s]` with a high penalty, as NGMM's `SLackVar`.

**Constraints.**
- Hub balance: Σ supply + Σ_in flow × (1 − loss_a) + storage_net_withdrawal[h,s] (exogenous)
  = Σ_out flow + NonPowerDemand[h,p,s] (res/com/ind/transport, lease/plant fuel, distribution loss)
  + exports[h,p,s] (LNG, Mexico) + PowerBurn[h,p,s].
- `PowerBurn[h,p,s]` = Σ_{z∈h} Σ_{t∈TPS(p,s)} Σ_g `GenFuelUseRate[g,t,'Naturalgas']` × `tp_weight_in_year[t]` / heat
  content (1.036 MMBtu/Mcf, AEO). Over s this sums to the period's gas burn. Stress-day timepoints have weight 0, so
  they drop out.

**Objective.** Add Σ step costs × quantities (supply + tariff + fixed fees + gathering) + markup × power burn + slack
penalty. Gas in `fuel_cost.csv` is dropped for the covered zones when the module is on.
- Non-power demand is fixed, so its supply cost enters the objective as an almost-constant term. It is reported
  separately so total costs stay comparable with existing cases.
- The step curves are NGMM's own (supply Table 1 option 2; tariff curves per arc), so the problem stays an LP.

**Delivered price** to zone z in season s = dual of the hub balance + `markup[z,p,s]`.

**Calibration** ("calibrated to AEO before use"), year by year:
1. Fix power burn at AEO (`sup_elec` consumption by EMM region, spread to hubs and seasons with EIA 5-year shares).
2. Anchor the supply curves at AEO quantities and prices (Tables 59/60, 13), and non-power demand and exports at
   Tables 61/62.
3. Solve the gas network alone, then:
   - check regional flows against Table 64 (target: within 10% on the major arcs; report the misfit);
   - set `markup[z,p,s]` = S0 path price(z, p) × seasonal factor(s) − the hub dual.

Then a Switch run that burns exactly AEO's gas reproduces the S0 STEO→AEO price for every zone (seasonal factors from
EIA monthly power-sector prices by state). Any deviation in burn moves the price along NGMM-shaped curves. S0's
exogenous path stays the default; the module replaces it only when switched on.

**Capacity.**
- Existing plus planned arcs come from `ngcapacity.txt`. AEO2026 adds capacity endogenously later, but its arc
  additions aren't published.
- Until Stage 4: capacity = max(existing + planned, AEO-implied), where AEO-implied = Table 64 flow ÷ season
  days ÷ a utilisation cap, allocated to the region pair's arcs (assumption, marked).

**Time resolution.** NGMM's 3 seasons, chosen over months because S0's 24 days give about 2 days per month. With a
seasonal balance, one season's gas cost depends only on that season's dispatch.

**Size.** About 75 hubs × 3 seasons ≈ 225 balances. About 214 arcs × 5 steps × 3 ≈ 3,200 flow variables. Supply
≈ 2,000 variables. 51 × 3 linking rows. All per period: under 10k rows and columns, against S0 cases of millions.
Mode A (myopic single years) suits NGMM's one-year short-term curves. Mode B solves 2 periods.

**Off by default.** The module goes into `modules.txt` only through a new settings switch, and `pg_to_switch` writes
`gas_*.csv` only when the switch is on. Unloaded, every case builds byte-identically. Tests will run a toy
two-hub/two-zone HiGHS solve on pandas 3.x and 1.4.4.

**Stage 3 (later).** On each S0 stress day d (12 days, winter at the cold end), for each hub or corridor:
daily gas burn ≤ deliverable inflow capacity + local production + max storage withdrawal − design-day non-power
demand.
- Design-day non-power demand = monthly demand ÷ days × NGMM `WeatherFactor1` (extreme ÷ average January) × a daily
  peak factor.
- The daily peak factor is a **gap** (see g).

**Stage 4 (later).** Arc expansion variables with a cost per MMcf/d-mile (to be sourced: FERC or INGAA cost data).
NGMM's 40%-of-existing cap becomes an option. Upstream methane = Σ basin share × burn × intensity, priced
optionally under the carbon policy modules.

## f. Published gas–power co-optimisation designs

- **NEMS NGMM / EMM**: iterative coupling (seasonal NGEMM prices ↔ burn). We embed the gas LP in the power LP
  instead: no iteration, but seasons rather than months. Structure borrowed directly.
- **ReEDS (NREL)**: no network; census-division AEO price–demand setpoints with regional and national elasticities
  regressed on AEO scenarios, plus a seasonal adjustor (`GSw_GasCurve`). Cheap and AEO-consistent: the natural
  **fallback ("stage 2-lite")** for the same zones, but no flows, deliverability or attribution.
- **PyPSA-USA**: state gas buses on EIA state-to-state capacities, import/export points, storage limits, methane
  tracking with configurable leakage; hourly, co-optimised, not AEO-calibrated. Same network as ours.
- **JPoNG** (Khorramfar, Mallapragada, Amin, *Applied Energy* 2024; Khorramfar et al., *Cell Reports
  Sustainability* 2025): New England joint planning; daily gas balance all year, hourly power on 24 representative
  days, pipeline/storage investment. Template for Stages 3 and 4.
- **EIPC Gas–Electric Interface Study** (Target 2, 2014–15): design-day pipeline capacity left for generators after
  firm LDC load. Stage 3's concept, there as an ex-post check.

## g. Data gaps, assumptions, questions

**Gaps** (none blocks Stage 1):
1. Texas RRC district production and county → district mapping, for the TX split.
2. HSM OGDIST dictionary, to use the restart's 84-district production. It isn't in the repo.
3. A design-day non-power demand factor for Stage 3. EIA has no state × day consumption; possible sources are LDC IRP
   design-day filings, FERC Form 2, or EIA-176 monthly with an assumed peak.
4. Arc expansion costs for Stage 4.
5. LNG exports by state in projections. AEO is national only; the allocation would use the AEO2026 project list
   (assumptions Table 3) and EIA terminal history.

**Assumptions to mark:**
- Full mixing at hubs.
- Storage net withdrawals exogenous at the 5-year average (as NGMM).
- 3 seasons.
- AEO-implied capacity before Stage 4.
- Canada and Mexico fixed (IEO2023, as NGMM).
- Heat content 1.036 MMBtu/Mcf.

**Questions for Tom:**
1. **Basin list for the methane hook.** Proposed: Appalachia, Haynesville (LA + TX_E), Permian (TX_W + NM_E),
   Eagle Ford, Mid-Continent/Anadarko, Rockies (incl. San Juan), Bakken, Fed. GOM, W. Canada, E. Canada, LNG imports,
   other. Does the methane research task use the same list?
2. **Restart run.** May we use the restart file from EIA's repo (run d011626a, not the published d021826b) for
   detail AEO doesn't publish (NGEMM seasons, district production, region capacities), clearly labelled? Or should we
   stay strictly on published tables?
3. **Calibration.** Is reproducing the S0 price path at AEO burn (via the markups) the right target for Stage 2?
4. **Accounting.** Should total cost include the (near-constant) cost of non-power gas, reported separately?
5. **Fallback.** Do you want the ReEDS-style supply curve as a fallback option inside the same module?
6. **"p13".** Which zone or region did you mean?

## Sources (opened 2026-10-09)

- EIAgov/NEMS, commit 1bfbb2a (2026-06-26): https://github.com/EIAgov/NEMS. Files used: `models/ngas/input/{ngsetmap,ngassumptions,ngvartar,ngcapacity,ngeia}.txt`, `models/ngas/data/{ngcapacity,NGVarTarCurve,NGPipelineFuelFactors,EMM_NGMM_report}.txt`, `models/ngas/mainproject/natgas.ams`, `input/emm/emm_db/NEMS_INPUT.db`, `input/dict.txt`, `output/aeo2026/cb2026/d011626a/restart.npz`
- EIA, *Assumptions to the AEO2026: Natural Gas Market Module*, April 2026: https://www.eia.gov/outlooks/aeo/assumptions/pdf/NGMM_Assumptions.pdf
- EIA, *Natural Gas Market Module of NEMS: Model Documentation 2025*, July 2025: https://www.eia.gov/outlooks/aeo/nems/documentation/ngmm/pdf/NGMM_AEO2025.pdf
- EIA, AEO2026 reference tables list: https://www.eia.gov/outlooks/aeo/tables_ref.php. Tables 59, 60, 61, 62, 64: https://www.eia.gov/outlooks/aeo/supplement/excel/suptab_{59,60,61,62,64}.xlsx (run cb2026.d021826b, April 2026)
- EIA Natural Gas Annual data via dnav (release 2026-09-30): dry production by state https://www.eia.gov/dnav/ng/xls/NG_PROD_SUM_A_EPG0_FPD_MMCF_A.xls; interstate movements by state https://www.eia.gov/dnav/ng/xls/NG_MOVE_IST_A2DCU_S{XX}_A.xls (49 files)
- NREL ReEDS model documentation (natural gas fuel prices): https://github.com/NREL/ReEDS-2.0 `docs/source/model_documentation.md`
- PyPSA-USA natural gas sector docs: https://github.com/PyPSA/pypsa-usa `docs/source/data-naturalgas.md`
- Khorramfar et al., "Cost-effective Planning of Decarbonized Power-Gas Infrastructure to Meet the Challenges of Heating Electrification": https://arxiv.org/pdf/2308.16814. Khorramfar, Mallapragada, Amin, "Electric-Gas Infrastructure Planning for Deep Decarbonization of Energy Systems": https://arxiv.org/html/2212.13655v2
- EIPC Gas–Electric study overview (NYISO posting): https://www.nyiso.com/documents/20142/1405188/EIPC%20G%20E%20Study.pdf/a5a9278d-dcd3-e656-58af-65702c6e425f
- Repo: `hierarchy.csv`, `s0_workflow/specs/fuel/zone_emm.csv`, `s0_workflow/fuel_prices.py`, `s0_workflow/data/fuel/SOURCES.yml`, `growth_rates/crosswalk_v7.csv` (ollie/edf-baseline 43fb0ec)
