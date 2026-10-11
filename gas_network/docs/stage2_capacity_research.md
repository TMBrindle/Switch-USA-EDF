# Stage 2 prerequisite: pipeline capacity since NGMM's 2023 file (research only)

Opened 2026-10-11. No code; nothing here feeds Stage 1 results.

## Sources

| File | URL | Release | sha256 |
|---|---|---|---|
| EIA U.S. State-to-State Capacity, January 2026 (capacity by pipeline, state and county pair, 1990–2025, MMcf/d) | https://www.eia.gov/naturalgas/pipelines/EIA-StatetoStateCapacity_Jan2026.xlsx | sheets updated 2026-01-31 | 2cea0fc5b4cbe4f82a36cf22598c00a68612cec26bf0cccfa55ba4f74dd5ed31 |
| EIA Natural Gas Pipeline Projects tracker, August 2026 (projects from 2025, plus completed 1996–2025) | https://www.eia.gov/naturalgas/pipelines/EIA-NaturalGasPipelineProjectsAug2026.xlsx | quarterly; rows updated to 2026-07 | 5c278179984027f380dfa6e9bdb739db628889dc25e6003f933b33f362ec469d |
| NGMM capacity in the AEO2026 code | EIAgov/NEMS 1bfbb2a `models/ngas/data/ngcapacity.txt`, `models/ngas/input/texas_capacity.txt` | 2026-06-26 | pinned in `data/SOURCES.yml` (texas_capacity) |

Both EIA files are listed on https://www.eia.gov/naturalgas/data.php.

## 1. Interstate (state-to-state) capacity, 2023 → 2025

EIA's January 2026 file runs to 2025. Two points about its layout:
- The NGMM-format sheet `State2StateAIMMS` has **no Texas outflows**. NGMM keeps Texas in `texas_capacity.txt`.
- The full sheet `Pipeline State2State Capacity` has county from and county to, so Texas border arcs can be put on
  NGMM's sub-hubs with the Stage 1 county → RRC district map.

Changes of ≥100 MMcf/d between 2023 and 2025, all states:

| Arc | 2023 → 2025 change (MMcf/d) | Note |
|---|---|---|
| Mississippi → Louisiana | +1,100 | Southern Natural Gas, new row in 2025 |
| North Carolina → Virginia | +423 | |
| Kentucky → Indiana | +220 | |
| British Columbia → Idaho; Idaho → Washington | +150 each | |
| TX_E → Louisiana | +364 | Transco "Texas to Louisiana Energy Pathway" (2025) |
| Colorado → Wyoming; Colorado → Kansas | −180; −463 | |
| Alabama → Tennessee | −2,308 | **Data gap, not a real change.** Texas Eastern's row is missing in 2025 (and Tennessee Gas's in 2024). A capacity table must carry the last value forward. |

Texas and New Mexico interstate arcs are otherwise unchanged 2023 → 2025. That holds for Oklahoma ↔ TX_N/TX_W,
New Mexico ↔ TX_W, TX_E/TX_W → Mexico and Arkansas ↔ TX_E. The Permian takeaway growth is all intrastate, so it
appears only in the project tracker.

## 2. Texas intrastate and Permian takeaway (project tracker)

Each project is mapped to an NGMM arc from the tracker's description; the tracker has no county fields for
intrastate projects. "Status" is the tracker's.

**TX_W → TX_E (Permian → Gulf Coast; NGMM 2023 capacity 8,380 MMcf/d)**

| Project | MMcf/d | In service | Status |
|---|---|---|---|
| Permian Highway expansion | 550 | 2023 | Completed (probably already in NGMM's 2023 value) |
| Whistler expansion | 500 | 2023 | Completed (same) |
| Matterhorn Express (Waha → Katy) | 2,500 | 2024 | Completed |
| Gulf Coast Express expansion (Waha → Agua Dulce, compression) | 570 | 2026 | Completed |
| Blackcomb (Midland Basin → Agua Dulce) | 2,500 | 2026 | Construction |
| Eiger Express (Permian → Katy, upsized to 48-inch) | 3,700 | 2028 | Construction |
| Apex (Midland → Port Arthur) | 2,000 | 2027 | On hold |

**TX_W → TX_N (NGMM 2023: 1,560 MMcf/d; Stage 1 routing runs it full every year)**

| Project | MMcf/d | In service | Status |
|---|---|---|---|
| Hugh Brinson (Waha → Maypearl, south of Dallas–Fort Worth) | 2,200 (1,500 Phase I) | 2026 | Part completed |

**TX_W → Mexico**

| Project | MMcf/d | In service | Status |
|---|---|---|---|
| Saguaro Connector (Waha → Hudspeth County border) | 2,800 | 2028 | Approved |
| Waha to Mexico (Wahalajara expansion) | 300 | 2027 | Construction |
| Nueva Era Dos | 1,000 | 2027 | Applied |

**New Mexico → TX_W, and New Mexico onward**

| Project | MMcf/d | In service | Status |
|---|---|---|---|
| Forza (Lea County NM → Winkler County TX) | 750 | 2028 | Applied |
| ECCC (Eddy NM → Culberson TX) | 150 | 2026 | Construction |
| Transwestern Desert Southwest expansion (NM → AZ) | 2,300 | 2029 | Pre-applied |
| Green Chile (in NM) | 400 | 2026 | Applied |
| Dude lateral (in NM) | 400 | 2026 | Applied |

**Gulf Coast header and LNG feed (inside TX_E: not NGMM arcs, but they decide where TX_E gas can reach)**
- Completed: ADCC 1,700 (2024), Verde 1,000 (2024), Eagle Ford project 2,000 (2023), Spears 1,000 (2023), South
  Texas to Houston 781 (2025).
- Construction: Traverse 2,400 (Agua Dulce ↔ Katy, bidirectional, 2027); Trident 2,000 (Katy → Port Arthur, 2027);
  Blackfin 3,500 (2027); Rio Bravo 4,500 (Rio Grande LNG feed, 2026); Corpus Christi Stage III 1,530 (2027).
- Approved: Golden Pass lateral 2,600 (2027), Mustang Express 2,500 (Katy → Port Arthur, 2029), TTC 300.
- Pre-applied: CCPL Stage IV 3,000 (2030).

**TX_E ↔ Louisiana**

| Project | Direction | MMcf/d | In service | Status |
|---|---|---|---|---|
| Golden Pass bidirectional | LA → TX | 2,500 | 2025 | Completed |
| Louisiana Connector – Port Arthur | LA → TX | 2,000 | 2026 | Part completed |
| CP Express Phase I + II | TX → LA | 1,800 + 2,200 | 2027 | Construction |
| NGPL Texas–Louisiana expansion | TX → LA | 467 | 2028 | "Completed" (the status and date disagree) |
| Texas Access | TX → LA | 1,300 | 2028 | Applied |
| Texas Gateway (Gulf South) | TX → LA | 1,450 | 2029 | Applied |
| DeLa Express | TX → LA | 2,000 | 2028 | Pre-applied |
| Sabine Crossing | TX → LA | 2,500 | 2031 | Pre-applied |
| South Mississippi (ET) | TX → MS | 2,000 | 2028 | Announced |

**What this does to Stage 1's flag.** Stage 1 routing (before balancing) needs TX_W → TX_E of 8.6 Bcf/d in 2024,
9.8 in 2028, 10.9 in 2030 and 12.1 in 2045. NGMM's 2023 capacity is 8.38. Completed projects plus those under
construction bring it to about 13.95 Bcf/d by 2026 (Matterhorn + GCX expansion + Blackcomb), and 17.65 with Eiger
Express in 2028.
- So the `texas_intra_capacity_exceeded` flag reflects the stale capacity file, not a real constraint.
- TX_W → TX_N with Hugh Brinson rises from 1.56 to about 3.8 Bcf/d. That would let more Permian gas reach North
  Texas zones (p61, p63) than Stage 1's capacity-limited routing gives.
- Open question: whether NGMM's 8,380 MMcf/d already includes the 2023 PHP/Whistler expansions. NGMM brings
  projects online in November of their in-service year.

## 3. What a current capacity table needs (for Stage 2)

1. **NGMM topology.** State hubs, TX_E/TX_N/TX_W, GOM, CN_E/CN_W, five Mexico regions and the 16 border-crossing
   nodes; directional arcs in MMcf/d by year, 2023 onward.
2. **Interstate base.** EIA State-to-State Capacity (latest edition; January 2026 covers to 2025), taken from the
   `Pipeline State2State Capacity` sheet so county pairs exist:
   - Texas endpoints go to sub-hubs through county → RRC district (Stage 1 `tx_county_hubs.csv`); Mexico and Canada
     endpoints go to NGMM's crossings.
   - Missing pipeline-years are carried forward and flagged (Alabama → Tennessee 2024/2025).
3. **Intra-Texas base.** NGMM `texas_capacity.txt` (2023), plus a hand-built, sourced table of intrastate projects
   from the tracker. Each row needs an arc, MMcf/d, an in-service date, status and the tracker row id.
   - Intrastate projects carry no county fields, so each arc assignment cites the tracker's description (e.g.
     "Waha → Katy").
   - Bidirectional projects get two arcs.
   - Header and LNG-feed projects inside a hub are not arcs. Keep them in a separate list for the LNG/export side.
4. **Status rule** (an ASSUMPTION for Tom to choose):
   - Completed, Part completed and Construction come online in November of their in-service year (NGMM's
     convention).
   - Approved is a sensitivity.
   - Applied, Pre-applied, Announced and On hold are excluded unless the AEO case's own expansion reaches them.
5. **AEO2026 consistency.** AEO2026 adds capacity endogenously after 2027. Its arc additions aren't published; the
   pre-release restart run (d011626a) has region-level `NGCAPS`, usable for shape only under Tom's rule.
   - Stage 2 either takes the table as exogenous capacity plus Stage 4 expansion, or adds Table 64-implied minimums
     where AEO flows exceed it.
   - Stage 1's arc check (`table64_arcs_check.csv`) already lists the region pairs where that bites.
6. **Tariffs and fuel.** Every new arc needs a variable tariff curve and a fuel-loss factor. Existing arcs use NGMM's
   (`NGVarTarCurve.txt`, `NGPipelineFuelFactors.txt`). New arcs take the curve of the parallel NGMM arc or the
   generic curve (`ngvartar.txt`); ASSUMPTION, flagged.
7. **Design-day versus nameplate.** These are nameplate figures. Stage 3's winter test also needs storage
   deliverability and design-day non-power demand (gaps listed in the Step 1 note).
8. **Pins.** Every file goes into `data/SOURCES.yml`, and the hand-mapped project table is committed with its
   tracker row ids, so a new quarterly tracker can be diffed.
