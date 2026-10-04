# Coal specification: zonal CF caps and fleet refresh (for the cloud build)

**Version:** final, 2026-10-03 (rev. 2: stage-specific holds and S0 horizon), with addendum rev. 2.1 (2026-10-04, at the end); not committed here. The cloud implements it from a copy committed to its branch, so this file is self-contained. Validation tables are listed in §5.

**Scope:** the S0 production build (`pg/settings/s0_production.yml`), stages / periods **2028** (2026–28), **2030** (2029–30), **2035** (2031–35), **2040** (2036–40) and **2045** (2041–45).
* Mode A runs the stages as a myopic chain.
* Mode B runs rolling two-period windows: 2028–30, 2030–35, 2035–40, 2040–45.
* Everything below is defined per **period**, so it applies identically in both modes.

**Decisions** (Tom, 2026-10-03):
1. Coverage-based cap rule and a targeted fleet refresh: approved.
2. Edwardsport: **no change** until the 860M codes it consistently (§2.5).
3. Petroleum coke and IGCC (1.9 GW) **stay in the coal cluster with the coal cap**; to be split out in the full fleet refresh (§2.5).
4. **Held units (S0):** Centralia 2, Campbell 1 / 2 / 3, Schahfer 17 / 18, Culley 2 and Craig 1 are held **only in the 2028 stage**, capped at their actual CF since the order (minimum 0.001).
   * From the 2030 stage, each follows its utility's plan: the latest 860M planned retirement date if there is one; otherwise it is retired from 2030 if the order was the only thing keeping it open.
   * Centralia 2 is retired as coal from 2030; its gas conversion isn't modelled until sourced.
   * Stanton 1 isn't held (it stays **keep online**, §2). (§3)
5. **Sensitivity `holds_persist`** (not in S0): the same eight units held through 2045 at the same caps, plus Intermountain 1 / 2 at cap 0.001. (§3.4)

**Rule for data vintage:** use the **latest** EIA-860M month at build time.
* This spec's numbers use **August 2026**: `https://www.eia.gov/electricity/data/eia860m/xls/august_generator2026.xlsx`, released 2026-09-24; the next release is due 2026-10-23. The September–December 2026 links on the EIA page were placeholders that redirect (checked 2026-10-03).
* **If a later month is used,** apply the same rules; differences from §5's tables should then come only from changed 860M records.

**Sources** (all public):

| data | file | used for |
|---|---|---|
| EIA-860M (latest month) | `<month>_generator<year>.xlsx`, sheets `Operating` and `Retired`, header on row 3 | status, technology, fuel, winter capacity, planned / actual retirement, state, county, lat / lon |
| EIA-860 annual 2021–24 | `https://www.eia.gov/electricity/data/eia860/archive/xls/eia860<year>.zip` (or `.../xls/`) → `3_1_Generator_Y<year>.xlsx`, sheets `Operable` and `Retired and Canceled` | each year's winter capacity; first operating year |
| EIA-923 annual 2021–24 | `https://www.eia.gov/electricity/data/eia923/archive/xls/f923_<year>.zip` (or `.../xls/`) → Schedules 2–5, sheet `Page 4 Generator Data` | monthly net generation by generator (CF history) |
| EIA-923 2025 final and 2026 year-to-date | `.../eia923/xls/f923_2025.zip` (`M_12_2025_Final`); `f923_2026.zip` (`M_07_2026`, released 2026-09-21) | output since an order (§3) |
| EIA-923 Page 1 (plant × prime mover × fuel), or PUDL `generation_fuel_eia923` | same files | heat rate of converted units |
| plant → zone | `pg/extra_inputs/reeds_plant_map.csv` (PowerGenome `plant_region_map_fn`) | zone, the same map the model fleet uses |
| county → zone (fallback) | `interconnection_headroom/data/reference/county2zone.csv` | zone for plants not in the plant map |

The Page 4 header row isn't fixed: it's row 6 in the 2025 file and row 5 in the 2026 file. Locate it as the row whose first cell is "Plant Id".

## 1. Coal-cap table

### 1.1 Unit inclusion (the "cap unit set")

A generator is in the cap unit set if, in the **latest 860M `Operating` sheet**:
1. `Technology` is exactly **"Conventional Steam Coal"**. This excludes "Coal Integrated Gasification Combined Cycle" (IGCC), "Petroleum Coke", "Natural Gas Steam Turbine" and everything else.
2. The `Status` code (two letters in brackets) is **OP**, **SB** or **OA**. **OS** (out of service, not expected to return) is excluded. Units in the `Retired` sheet are excluded by construction.
3. `Planned Retirement Year` is **blank or ≥ p**, where p is the stage's model year (2028, 2030, 2035, 2040, 2045). The cap unit set is therefore **stage-specific**.
4. It is **not a held unit** in either S0 or `holds_persist` (§3). Held units have their own unit-level cap whenever they're in service, and are left out of every stage's zone values.

Fuel coding in earlier years doesn't affect inclusion; only the latest 860M technology does.

### 1.2 Generator-ID normalisation (860M ↔ EIA-860 ↔ EIA-923)

* **Unit key** = (`Plant ID` as integer, normalised `Generator ID`).
* **Normalisation:** strip whitespace, then strip leading zeros; if nothing is left, "0". Examples: "0001", "001", "01" → "1"; "ST4", "GEN5", "S01" are unchanged. Apply it identically to every file before any join.
* **Collision check, on coal-group units only** (Conventional Steam Coal, IGCC, Petroleum Coke): if two raw IDs in the same plant normalise to the same key in any one file, stop and report it.
  * None occur in the August 2026 data.
  * One collision exists elsewhere in that 860M: Equus Freeport Power (56032, gas CTs "0001" and "001"). A file-wide check would stop on it.

### 1.3 Annual CF 2021–24 and unit maximum

* **Unit-year CF** = Σ of the 12 monthly `Net Generation` values (EIA-923 Page 4) ÷ (that year's EIA-860 `Winter Capacity (MW)` × hours). Hours are 8,784 in 2024 and 8,760 otherwise.
* **A unit-year is valid** if:
  * all 12 monthly values are present;
  * the year isn't the unit's `Operating Year`;
  * the year isn't its retirement year;
  * winter capacity > 0.
* **Dual-fuel years count**, whatever that year's fuel code. EIA-923 Page 4 generation isn't split by fuel, and inclusion is decided by the latest 860M.
* **Unit maximum** = the highest valid CF, clipped to [0, 1]. A unit with no valid year has no history. It still counts toward its zone's model MW (§1.5), but not toward history MW.

### 1.4 Zone placement

1. **Plant map:** `pg/extra_inputs/reeds_plant_map.csv` (`plant_id_eia` → `region`).
2. **County fallback:** the 860M `Plant State` + `County` → `county2zone.csv`.
   * County normalisation: lower-case; "saint " → "st "; "st. " → "st "; "ste. " → "ste ". Drop a trailing " county", " parish", " borough", " census area", " city and borough", " municipality", " municipio" or " city". Remove non-alphanumerics.
   * Normalise `county2zone.csv`'s `county_name` the same way, and match on (upper-case state, normalised county).
3. **Lat / lon fallback:** a point-in-county spatial join on the 860M `Latitude` / `Longitude` (Census county shapes), then `county2zone.csv`.
4. Report any plant still unmapped. Don't use PUDL's plant county anywhere; it's missing for some plants.

August 2026: all 323 lower-48 cap units map (269 by step 1, 54 by step 2). 14 Alaska units are outside the model.

### 1.5 Zone values and the coverage rule

All quantities are computed **per stage p**:
* **H_z(p)** = Σ latest-860M `Net Winter Capacity (MW)` over stage-p cap units in zone z with history.
* **own_z(p)** = Σ (unit max × winter MW) ÷ H_z(p).
* **National fallback N(p)** = Σ (unit max × winter MW) ÷ Σ winter MW over **all** mapped stage-p cap units with history.
  * August 2026 values: **N(2028) = 0.5806, N(2030) = 0.5926, N(2035) = 0.5998, N(2040) = 0.6001, N(2045) = 0.6001**.
  * For reference, 2035: 0.6003 if the held units were included; 0.5927 with the February 2026 860M.
* **M_z(p)** = the zone's model coal MW in service in period p after the overrides in §2, in winter MW.
  * "In service in p" means the unit's encoded retirement year is ≥ p, or it has none (the pg_to_switch rule).
  * It includes every unit in PowerGenome's coal tech group (Conventional Steam Coal, IGCC and Petroleum Coke; §2.5).
  * It excludes held units (§3) and converted units.
* **Same in S0 and `holds_persist`.** Held units are excluded from H, M and N in both, so the zone caps are identical in the two.
* **Cap:**
  * M_z = 0: no coal clusters, so no cap needed.
  * Else if H_z = 0: **cap_z = N**.
  * Else **coverage_z = H_z ÷ M_z**:
    * coverage_z **≥ 0.5** → **cap_z = own_z**;
    * otherwise → **cap_z = (H_z × own_z + 500 × N) ÷ (H_z + 500)**, with 500 in MW.
* **Switch input:** each coal cluster in zone z gets `gen_max_annual_availability = min(1, cap_z ÷ (1 − gen_forced_outage_rate))`.
  * Aggregated load zones: H-weighted mean of the member zones' own_z, with H and M summed, and the rule applied to the aggregate.
* **Report** cap_z to 4 decimals and H_z, M_z to 0.1 MW.

## 2. Fleet overrides for the model's coal units

### 2.1 The current model fleet (what is being overridden)

PowerGenome builds existing units from PUDL `pudl.2025_08` report year 2024 (`pg/settings/resources.yml`: `eia_data_years: [2024]`; `eia_860m_fn: july_generator2025.xlsx`).
* **Retirement dates:** the 2035 cases encode **PUDL-2024 planned retirement dates** in `gen_build_predetermined.csv` (build year = retirement year − 500; e.g. Comanche 2 → 2025, Harrington 2 / 3 → 2038 / 2040). They don't use the July 2025 860M's planned dates, which `update_coal_closures.py` had edited with Global Energy Monitor data.
* **Out-of-service units** (2024 status OS) aren't in the case fleet.
* **Reconstruction:** this logic gives 135.66 GW against the case's 136.64 GW. The differences are in p101 (−453 MW, matching Stanton 1, which the case keeps), p107 (−481 MW) and p99 (−43 MW).
* **Validate end states**, not this reconstruction.

### 2.2 Override actions

* **Keys:** overrides are unit-level, keyed by the normalised (plant ID, generator ID), and applied to PowerGenome's existing-unit table **before clustering**.
* **Authority:** the latest 860M is authoritative; GEM-only dates that aren't in it are not applied.
* **Horizon:** retirement dates are compared over the whole S0 horizon, to 2045. A date after 2045 counts as "no retirement".
* **Applies to:** every stage. The overrides define each unit's encoded retirement year, and pg_to_switch puts a unit in service in period p iff that year is ≥ p (or blank).
* **Precedence** (first match wins): **hold list (§3)** > `Retired` sheet > status OS > conversion > 860M planned retirement date (earlier or later than the model basis) > keep online.

| action | condition (latest 860M unless stated) | status code / field | what to set | PowerGenome / Switch mapping |
|---|---|---|---|---|
| **hold online** | unit on the hold list for the scenario (§3) | per §3 | in service while held; unit-level max annual CF; then the post-hold action (§3.2) | §3.5 |
| **retire** | in the `Retired` sheet, retirement year later than the model basis's | RE, `Retirement Year` | retirement year = `Retirement Year` | `resources.yml` `additional_retirements: [plant_id_eia, generator_id, year]`; pg_to_switch encodes build year − 500 as now |
| **retire** / **retire later than model basis** | `Operating`, coal-group technology, `Planned Retirement Year` ≤ 2045 and different from the model basis's year | OP / SB / OA + `Planned Retirement Year` | retirement year = the 860M value (earlier or later than the model's) | as above |
| **remove** | `Operating`, status **OS** | OS | drop from the existing fleet (retire in 2026) | `additional_retirements`, year 2026 |
| **convert to gas** | `Operating`, `Technology` = "Natural Gas Steam Turbine" **and** `Energy Source Code` = NG, while the model unit is in the coal group | OP + Technology / Energy Source | §2.3 | edit the unit row before clustering, as `update_coal_closures.py` does: `technology_description` → "Natural Gas Steam Turbine" (tech group `Other_peaker`), fuel, capacity, heat rate, planned retirement. The unit leaves `p{zone}_conventional_steam_coal_*` and joins `p{zone}_other_peaker_1` |
| **keep online** | model basis retires the unit by 2045, but the 860M lists it `Operating` (OP / SB / OA) in the coal group with `Planned Retirement Year` blank or > 2045 | OP + blank `Planned Retirement Year` | no retirement in the horizon | override the PUDL-2024 planned date (pg_to_switch `predetermined_retirement_override`, or a corrected `additional_retirements` entry) |
| **no change** | coal-group units covered by §2.5 | — | none | — |
| **review** | technology changed to something other than NG steam (e.g. "All Other") | — | none; report | — |

Cluster names `p{zone}_conventional_steam_coal_1/2` are reassigned when PowerGenome re-clusters, so validate by unit and zone, not by cluster name.

### 2.3 Converted units

* **Fuel and technology:** natural gas, "Natural Gas Steam Turbine" (PowerGenome tech group `Other_peaker`).
* **Capacity:** the latest 860M `Net Winter Capacity (MW)`.
* **Full-load heat rate:** the plant's EIA-923 fuel for electricity ÷ net generation for prime mover **ST** and energy source **NG**, in the most recent year with **≥ 10 GWh** of ST / NG generation (partial years allowed).
  * Plant-level, because EIA doesn't report fuel by generator; converted units at the same plant share it.
  * If no year qualifies, keep the unit's 2024 coal heat rate and flag it.
* **O&M, min load, ramp, outages:** PowerGenome defaults for "Natural Gas Steam Turbine" / `Other_peaker`.
* **Planned retirement:** the 860M value.
* **Conversion year:** the first year of the unit's latest unbroken run of NG coding, in PUDL annual 2020–25 followed by the latest 860M (2026 if PUDL's latest year still codes coal). In the 2035 model year every listed conversion is gas.
* **No double counting:** none of the conversions below is in the case's gas fleet; their PUDL 2024 technology is Conventional Steam Coal. Harrington 1 (NG from 2024) is already a gas steam turbine in the case and isn't listed.

| plant (EIA ID) | gen | zone | winter MW | heat rate (MMBtu/MWh) | heat-rate source | conversion year | planned retirement |
|---|---|---|---|---|---|---|---|
| North Valmy (8224) | 1 | p12 | 254 | 13.98 | no gas history: 2024 coal heat rate (flag) | 2026 | — |
| North Valmy (8224) | 2 | p12 | 268 | 13.98 | same (flag) | 2026 | — |
| TalenEnergy Montour (3149) | 1 | p122 | 752 | 10.12 | ST/NG 2025, 5 months, 1,699 GWh | 2026 | — |
| Pawnee (6248) | 1 | p33 | 505 | 12.97 | ST/NG 2024, 12 GWh (thin; flag) | 2026 | — |
| Harrington (6193) | 2 | p48 | 339 | 11.10 | ST/NG 2025, 5 months, 909 GWh (shared with unit 3) | 2025 | 2038 |
| Harrington (6193) | 3 | p48 | 340 | 11.10 | same | 2026 | 2040 |
| James E. Rogers (2721) | 5 | p97 | 546 | 9.80 | ST/NG 2025, 5 months, 1,116 GWh (shared with unit 6) | 2026 | — |
| James E. Rogers (2721) | 6 | p97 | 849 | 9.80 | same | 2026 | — |

Rogers 5 / 6 are coded NG in 2022–23, BIT in 2024 and NG from 2025, so their conversion year is 2026 (the start of the latest run, per the 860M).

### 2.4 The August 2026 override list (model winter MW)

73 rows in `coal_spec_overrides.csv`:

| action | units | MW | units (zone): 860M date vs model basis |
|---|---|---|---|
| hold online (S0: 2028 stage only) | 8 | 3,238 | §3.2 |
| hold online (`holds_persist` only) | 2 | 1,800 | Intermountain 1 / 2 (p25); §3.4 |
| retire | 15 | 9,040 | Crystal River 4 / 5 2034 (p101); Merrimack 1 2027 (p130); Comanche 3 2030 (p34); **Sherburne County 3 2030 (model 2034, p43)**; La Cygne 1 2032, **La Cygne 2 2039**, Lawrence 4 / 5 2032, Jeffrey 3 2030, **Jeffrey 1 2039** (p53); **Iatan 1 2039** (p54); Marshall 4 2033, **Marshall 2 2028 (model 2034)** (p97); Mayo 1 2031 (p98) |
| retire later than model basis | 9 | 3,938 | Brandon Shores 1 / 2 2029 (model 2025, p123); Comanche 2 2026 (2025, p34); South Oak Creek 7 / 8 2027 (2025, p79); Cumberland 2 2034 (2026, p92); Kingston 7 / 8 / 9 2034 (2026, p92) |
| convert to gas | 8 | 3,853 | §2.3 |
| remove (OS) | 5 | 1,969 | Sandy Creek S01 (p63, 933 MW); Big Cajun 2-1 (p58); Merrimack 2 (p130); Warrick 2 (p107); Biron Mill GEN5 (p76) |
| keep online | 5 | 2,249 | Stanton 1 (p101); Mill Creek 2 (p109); Miami Fort 7 / 8 (p114); GREC 2 (p51) |
| no change: petroleum coke / IGCC in the coal cluster | 18 | 1,897 | §2.5 |
| no change: Edwardsport CT1 / CT2 | 2 | — (no winter MW in PUDL) | §2.5 |
| review | 1 | 7.6 | Seadrift Coke GEN1 (p65): now "All Other" (waste heat) |

The entries in **bold**, and the whole "retire later than model basis" row, are new in rev. 2. They come from extending the date comparison from "before 2035" to the full 2045 horizon. The later-date rows matter only for the 2028 and 2030 stages.

**Model coal in the zone clusters after overrides** (GW, model basis in brackets):

| stage | after overrides | model basis |
|---|---|---|
| 2028 | **157.3** | 158.0 |
| 2030 | 142.1 | 144.4 |
| 2035 | 126.4 | 135.7 |
| 2040 | 124.3 | 135.3 |
| 2045 | 124.3 | 135.0 |

**Held units, as separate projects:**
* **S0:** 3.24 GW in the 2028 stage only.
* **`holds_persist`:** 5.04 GW in every stage.

### 2.5 Units left unchanged by decision

* **Petroleum coke and IGCC stay in the coal cluster with the coal cap** (Tom, 2026-10-03).
  * **What's in the cluster:** PowerGenome's `pg/settings/resources.yml` `tech_groups` defines `Conventional Steam Coal: [Conventional Steam Coal, Coal Integrated Gasification Combined Cycle, Petroleum Coke]`. The Petroleum Coke line carries the comment **`# TODO: treat as separate fuel`**.
  * **Affected units:** 17 petroleum-coke units (1,319 MW: p58 845, p111 143, p94 85, p75 58, p20 55, p65 54, p103 47, p70 32) and Edwardsport IGCC (ST, 578 MW, p107).
  * **How they're treated:** they count in M_z and receive cap_z. cap_z is computed from conventional steam coal history only (§1.1).
  * **To be split out in the full fleet refresh.**
* **Edwardsport (plant 1004): no change** (Tom, 2026-10-03). The August 2026 860M codes CT1 / CT2 as "Natural Gas Fired Combined Cycle" but the ST as "Coal Integrated Gasification Combined Cycle" (SGC).
  * Leave all three generators as they are, in the coal cluster, until a later 860M codes the plant consistently.
  * Then apply §2.2 (conversion if all three are NG combined cycle).

## 3. Units held open by orders

### 3.1 The hold action

* **What a held unit is:** in service and available while held, counting for adequacy. Its output is limited by a **unit-level maximum annual CF** (the hold cap).
* **Hold cap** = **max(actual CF since the order, 0.001)**, or **0.01** if there's no usable data (unit absent from EIA-923 Page 4, or fewer than 3 months with values).
  * **Actual CF since the order** = Σ monthly EIA-923 Page 4 net generation ÷ (latest-860M winter MW × hours).
  * **Window:** from the first full month after the unit's original planned retirement date, or the first full month of the first order if later, to the latest EIA-923 month. If the first order is less than 3 months old, the window starts at the month after the original planned retirement.
  * Negative totals count as 0.
  * The values below use EIA-923 2025 final (`f923_2025.zip`, `M_12_2025_Final`) and 2026 year-to-date (`f923_2026.zip`, `EIA923_Schedules_2_3_4_5_M_07_2026_21SEP2026.xlsx`, through July 2026).
* **Why the 0.001 floor** (an implementation necessity, not a change to Tom's rule):
  * In Switch's `study_modules/gen_annual_availability_limits.py`, a generator with `gen_max_annual_availability == 0` goes into `UNAVAILABLE_GENS`, and `Force_Off_Unavailable_Gens` sets its dispatch to 0 in **every** timepoint, including the zero-weight planning-reserve day. It would no longer count for adequacy.
  * Any positive value goes through the weighted annual-energy constraint instead, which excludes zero-weight timepoints.

### 3.2 S0 holds: the 2028 stage only (applied by default in S0)

* **When the hold applies:** in **period 2028** (2026–28) only. In mode B, that's the first period of the 2028–30 window; the 2030 period of the same window is after the hold.
* **Post-hold rule** (from the 2030 stage):
  * if the latest 860M has a **planned retirement year ≥ 2030**, retire then;
  * if it has a planned year **before 2030** (already passed when the hold ends), or **no** planned year and the order was the only thing keeping the unit open, the unit is **retired from the 2030 stage**.
  * **Encoding:** retirement year **2029**, which pg_to_switch keeps in service in period 2028 (2029 ≥ 2028) and retires from period 2030 (2029 < 2030).
* **Currently all eight units retire from the 2030 stage.** If a later 860M gives one a planned year ≥ 2030, its hold project (§3.5) carries the hold cap through that year. Review it then: a single project can't switch from the hold cap to the zone cap mid-horizon.

| unit (plant, gen) | zone | winter MW (860M Aug 2026) | order basis, current order (effective period) | latest 860M | window | net MWh | actual CF | **hold cap** | post-hold (S0) | encoded retirement year | in current case? |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Centralia 2 (3845, 2) | p2 | 670 | DOE FPA §202(c): 202-25-11 (2025-12-16) → 202-26-18 → 202-26-28 → **202-26-44** (2026-09-13 to 2026-12-11); Ninth Circuit petition filed 2026-03-02 | OP, no planned retirement | 2026-01 to 2026-07 | 0 | 0.000 | **0.001** (floor) | retired from 2030 (order was the only thing keeping it open). **The gas conversion is not modelled until sourced** (Tom) | 2029 | no (model retires it 2025) |
| Campbell 1 (1710, 1) | p103 | 260 | 202(c): first order 202-25-3 (2025-05-23) **vacated by the D.C. Circuit** (*Michigan v. DOE*, 2026-09-11); current **202-26-39** (2026-08-17 to 2026-11-14) in force | OP, planned retirement 2026 | 2025-06 to 2026-07 (14 months) | 1,508,385 | 0.567 | **0.5674** | retired from 2030 (planned 2026 has passed) | 2029 | no |
| Campbell 2 (1710, 2) | p103 | 280 | as above | OP, planned 2026 | same | 107,492 | 0.038 | **0.0375** | same | 2029 | no |
| Campbell 3 (1710, 3) | p103 | 789.3 | as above | OP, planned 2026 | same | 4,470,074 | 0.554 | **0.5539** | same | 2029 | no |
| Schahfer 17 (6085, 17) | p105 | 361 | 202(c): 202-25-12 (2025-12-23) → … → **202-26-46** (2026-09-20 to 2026-12-18) | OP, planned 2026 | 2026-01 to 2026-07 | 313,763 | 0.171 | **0.1708** | same | 2029 | no |
| Schahfer 18 (6085, 18) | p105 | 361 | as above | OP, planned 2026 | same | −12,794 | 0.000 | **0.001** (floor) | same | 2029 | no |
| Culley 2 (1012, 2) | p107 | 90 | 202(c): 202-25-13 (2025-12-23) → … → **202-26-47** (2026-09-20 to 2026-12-18) | OP, planned 2026 | same | 48,413 | 0.106 | **0.1057** | same | 2029 | no |
| Craig 1 (6021, 1) | p33 | 427 | 202(c): 202-25-14 (2025-12-30) → … → **202-26-49** (2026-09-27 to 2026-12-25); challenged by Colorado and others 2026-01-28 | OP, planned 2026 | same | 77,260 | 0.036 | **0.0356** | same | 2029 | no |

**Total held in the 2028 stage: 3,238 MW.**
* None of these units is in the current case's fleet in 2028; the model basis (PUDL 2024 planned dates) retires all of them in 2025. The S0 hold therefore **adds** them for the 2028 stage.
* **Order chains:** DOE's 2026 §202(c) order list (`https://www.energy.gov/ceser/2026-doe-202c-orders`) and the per-order pages.
* **Campbell ruling:** D.C. Circuit decision of 2026-09-11 (Michigan Attorney General release; Utility Dive).

### 3.3 Not held

* **Stanton Energy Center 1** (564, 1; p101; 453.4 MW). It's under §202(c) orders 202-26-26 / **202-26-42** (2026-09-02 to 2026-11-30). Its actual CF since the order is 0.402 (2026-01 to 2026-07; window extended because the first order is less than 3 months old).
  * **Not a hold** (Tom): it stays **keep online** (§2.4). The latest 860M lists no planned retirement, so it's in service in all stages in the zone cluster with the zone cap.
* **Eddystone 3 / 4** (PA) and **Wagner 4** (MD): under §202(c) retirement-deferral orders but oil / gas steam, so out of scope.
* **Short-duration §202(c) orders** (weather events: PJM, Duke, SPP, ERCOT, others) aren't unit retirement deferrals and are excluded.

### 3.4 Sensitivity `holds_persist` (not in S0)

* **Units:** the eight §3.2 units **plus Intermountain 1 and 2** (6481, 1 / 2; p25; 900 MW each).
* **Duration:** held in **every stage through 2045** (periods 2028–2045); encoded retirement year: none in the horizon.
* **Caps:** the same as §3.2. **Intermountain 1 / 2 have cap 0.001**: they had 0 MWh in 2025-12 to 2026-07 (6 months), floored.
* **Intermountain basis:** Utah state law (HB 70, 2025) keeps the coal units operable while a buyer is sought. They stopped operating in November 2025 when the IPP Renewed gas units entered service, and IPA issued an acquisition RFP on 2026-08-05.
  * The **latest 860M lists them as Retired (2025)**. The sensitivity adds them back on the state-law basis only.
* **Total held in every stage: 5,038 MW.**
* **Zone caps are unchanged:** held units are excluded from H, M and N (§1.5). So `coal_spec_expected_caps_by_stage.csv` applies to both S0 and `holds_persist`.

**How to switch it on** (cloud implementation; names proposed, to keep the existing pattern):
* **Setting:** in `pg/settings/s0_production.yml` add `coal_holds: {enabled: true, scenario: s0, table: s0_workflow/data/coal_holds.csv}`, with `scenario: s0 | holds_persist`.
  * `s0` holds the units flagged `in_S0` through the 2028 stage and then applies the post-hold rule.
  * `holds_persist` holds the units flagged `in_holds_persist` through 2045.
  * Inert unless `s0_production.enabled`.
* **Scenario axis:** in `pg/settings/scenario_management.yml` add axis `coal_holds` with values `s0` (default) → `{s0_production: {coal_holds: {scenario: s0}}}` and `holds_persist` → `{s0_production: {coal_holds: {scenario: holds_persist}}}`.
* **Case selection:** a `coal_holds` column in `pg/extra_inputs/scenario_inputs.csv`; blank = `s0`.
* **Data:** `s0_workflow/data/coal_holds.csv`, one row per unit, with columns `plant_id_eia, generator_id, zone, winter_mw, hold_cap, in_S0, in_holds_persist, S0_last_stage (2028), S0_encoded_retirement_year (2029), persist_last_stage (2045), source`. Use the values in the tables above.

### 3.5 How a held unit is represented in PowerGenome / Switch

* **Own project.** The held unit is **not clustered**: it becomes a single-unit project (e.g. `p2_coal_hold_3845_2`) in its zone, and its capacity is removed from the zone's coal clusters.
  * Technology parameters (heat rate, O&M, outages, commitment) are the unit's own, from PowerGenome's unit data (EIA-860 2024 / 923).
  * Fuel is coal.
  * The unit is added even where the model basis has already retired it, which is the case for all of §3.2 and §3.4 except Stanton.
* **Retirement:** the encoded retirement year from §3.2 (S0) or none (`holds_persist`); `gen_can_retire_early = 0` while held.
  * S0's retirement rule (no economic retirement of coal or gas before 2030) is consistent with this.
* **Switch input:** `gen_max_annual_availability = min(1, hold_cap ÷ (1 − gen_forced_outage_rate))`. The cap is a per-project parameter; it applies in every period in which the project is in service.
* **Adequacy:** the unit counts in the planning-reserve constraints like any dispatchable unit.
* **Zone values:** held units are excluded from H_z, M_z and N in every stage (§1.5).

## 4. Validation summary (August 2026)

Expected zonal caps by stage (`coal_spec_expected_caps_by_stage.csv`; the same in S0 and `holds_persist`):

| stage | N(p) | zones | model coal after overrides (GW) | model-MW-weighted cap | own history / national / blend / no coal left |
|---|---|---|---|---|---|
| 2028 | 0.5806 | 75 | 157.28 | 0.5802 | 71 / 2 / 1 / 1 |
| 2030 | 0.5926 | 74 | 142.06 | 0.5922 | 67 / 3 / 1 / 3 |
| 2035 | 0.5998 | 73 | 126.40 | 0.5994 | 64 / 3 / 1 / 5 (p130, p33, p34, p48, p97) |
| 2040 | 0.6001 | 73 | 124.31 | 0.5998 | 64 / 3 / 1 / 5 |
| 2045 | 0.6001 | 72 | 124.31 | 0.5998 | 64 / 3 / 1 / 4 |

* **The blend zone** is p111 in every stage: H = 47 MW against 190 MW of model coal, own 0.002, so cap 0.531 (2028), 0.542 (2030), 0.548 (2035) and 0.549 (2040–45).
* **Held projects** come on top of these: S0 3.24 GW in 2028 only; `holds_persist` 5.04 GW in every stage. Per unit and stage: `coal_spec_hold_by_stage.csv`.

## 5. Validation tables

Location: `switch/out_ictest/2035/report_tables/emissions/` in the Switch repo (VM).

| file | content | tolerance for the cloud's output |
|---|---|---|
| `coal_spec_expected_caps_by_stage.csv` | per stage and zone: model MW before / after overrides, H_z(p), units, own_z(p), coverage, rule, **expected_cap**, N(p) | cap ±0.001; H, M ±1 MW; same rule label |
| `coal_spec_expected_caps.csv` | the 2035 stage of the above (unchanged from rev. 1) | same |
| `coal_spec_overrides.csv` | per unit (73 rows): plant, generator, zone, model winter MW and technology, model-basis retirement year, **action**, **effective_year**, **source**, latest 860M fields, conversion fields; for holds: hold cap and its source, `hold_last_stage_S0`, `post_hold_action_S0`, `encoded_retirement_year_S0`, `hold_last_stage_persist` | same (plant, generator, action) set; effective year exact; MW ±0.1 |
| `coal_spec_hold_online.csv` (+ `_monthly.csv`) | all order-held units: orders, window, months, MWh, actual CF, **hold cap**, `in_S0`, `in_sensitivity_holds_persist`, `S0_hold_last_stage` (2028), `persist_hold_last_stage` (2045), `S0_post_hold_action`, `S0_encoded_retirement_year` (2029), latest 860M planned retirement, decision | CF ±0.0005; cap exact; flags and years exact |
| `coal_spec_hold_by_stage.csv` | per held unit and stage: S0 status (held / retired), `holds_persist` status, cap while held, MW | exact |
| `coal_spec_converted_gas_units.csv` | the converted gas units (§2.3) | heat rate ±0.01; MW ±0.1 |
| `coal_cap_units_all.csv` | all current Conventional Steam Coal units (OP / SB / OA) with history, any planned retirement: the source of every stage's cap unit set (427 units, 166.1 GW incl. Alaska and held units) | same set; unit max ±0.0005 |
| `coal_cap_units_adjudicated.csv` | the 2035-stage cap unit set (337 units) | same |
| `coal_spec_model_units_2035.csv` | reconstruction of the current case fleet, 2035 (reference only) | — |

Producing scripts (VM, `ic_test_fedpol_work/`): `coalcap_adjudicate.py` (cap unit sets), `coal_holdopen.py` (§3), `coal_spec_build.py` (§1.5, §2, §4). Run them in that order.


## Addendum: revision 2.1 (2026-10-04)

Tom's decisions of 2026-10-04 on the cloud implementation of rev. 2. Implemented on `tom/s0-prod-scripts`
(`s0_workflow/coal_spec.py`, `coal_fleet.py`; `pg/settings/s0_production.yml`). Where this addendum differs
from the text above, it takes precedence.

### A1. Cap unit set: OP and SB only

The cap unit set (§1.1 rule 2) is status **OP or SB**. This overrides the §1.1 text, which also lists OA.
- **Effect:** the two OA units are left out: Biron Mill GEN1 (10234, 15.3 MW, p76) and WE Soda 5 (57915,
  10 MW, p21). Including them would raise p21's cap by 0.002 in every stage.
- **Tables:** the §5 validation tables were built this way.
- **Setting:** `coal_spec.cap_statuses: [OP, SB]`.

### A2. Converted units: heat rates from the latest EIA-923

The full-load heat rate of a converted unit (§2.3) comes from the **latest EIA-923**: 2025 final and 2026 through
July (`f923_2026.zip`, `M_07_2026`), not PUDL 2025_08 (data through May 2025). The rule is unchanged: the
plant's ST/NG fuel ÷ net generation in the most recent year with ≥ 10 GWh. Every converted plant now qualifies
in 2026, so North Valmy no longer falls back to its coal heat rate.
`coal_spec_converted_gas_units.csv` and the conversion rows of `coal_spec_overrides.csv` now carry these values:

| plant (EIA ID) | gens | heat rate rev. 2 (MMBtu/MWh) | **rev. 2.1** | source (rev. 2.1) |
|---|---|---|---|---|
| North Valmy (8224) | 1, 2 | 13.98 (coal heat rate, flag) | **11.42** | ST/NG 2026, 7 months, 926 GWh |
| TalenEnergy Montour (3149) | 1 | 10.12 | **10.11** | ST/NG 2026, 7 months, 3,031 GWh |
| Pawnee (6248) | 1 | 12.97 | **10.96** | ST/NG 2026, 7 months, 1,294 GWh |
| Harrington (6193) | 2, 3 | 11.10 | **10.83** | ST/NG 2026, 7 months, 1,640 GWh |
| James E. Rogers (2721) | 5, 6 | 9.80 | **10.55** | ST/NG 2026, 7 months, 1,032 GWh |

`coal_spec.heat_rate_data_through: "2025-05"` reproduces the rev. 2 values.

**Conversion year (§2.3):** the 2025 record is the **June 2025** 860M, the vintage of PUDL 2025_08's 2025 data.
It gives every listed conversion year, and is accepted.

### A3. Retirements before 2030: three options

Setting: `s0_production.retirements_pre2030`; axis `retirements_pre2030`; a `scenario_inputs.csv` column of
the same name. They apply to the S0 new-defaults cases (`s4x1_S0prod_2035_new`, `S0prod_A`, `S0prod_B`).
The regression case is `legacy`, which leaves its settings as they were. fedpol and every other case are
unchanged.

| option | dated retirements before 2030 (coal and gas) | economic retirements before 2030 |
|---|---|---|
| **block_all** (S0 default, current policy) | none: fedpol's `blocked_2030_coal_gas` predetermined override, for coal and gas | none (§ retirement rule) |
| planned_only | as scheduled: the 860M and this spec | none |
| unrestricted | as scheduled | allowed from the first stage |

**block_all, stage by stage.**
- **Which units:** a unit with a retirement year in 2026-29 is moved to 2030 (fedpol's rule: window 2026-29
  inclusive, target 2030). This covers coal and gas, and the coal side includes:
  - this spec's dated retirements and later-than-basis dates (Merrimack 1 2027, Marshall 2 2028, Brandon
    Shores 1/2 2029, Comanche 2 2026, South Oak Creek 7/8 2027);
  - the S0 holds' encoded 2029.
- **Not pushed: the OS removals** (Tom, 2026-10-04). Sandy Creek S01, Big Cajun 2-1, Merrimack 2, Warrick 2 and
  Biron Mill GEN5 (1,969 MW, encoded 2026) are out-of-service status, not retirement decisions, so they are
  removed in every option, block_all included.
  - **How (2026-10-04, after the VM build at c4a19f8):** the case build deletes them from PowerGenome's unit
    tables before clustering, in every option. Encoding them 2026 was not enough: fedpol's own push runs on the
    clusters afterwards and moved them to 2030. They are deleted from PowerGenome's EIA-860 units and from the 860M
    generators PowerGenome adds for Operating-sheet units missing from those units (all five are OP / OA in the
    July 2025 860M). Biron Mill (plant 10234) has no `reeds_plant_map.csv` region, so it enters only that second way
    and has no derived override row. The build logs `coal removals: 5 removed` and stops on any other count.
- **Which stages:** Switch runs with `--retire early`, so a unit with retirement year Y is in service in the stage
  whose period ends at p iff Y ≥ p. A unit dated 2026-29 is therefore **in service in the 2028 and 2030 stages
  and first disappears from the 2035 stage**. With planned_only or unrestricted it is in the 2028 stage only if
  dated 2028 or 2029, and gone from 2030.
- **Encoding:** in the S0 new-defaults cases the case build encodes these units, **coal, held and gas**, as
  **2031**, not 2030, in PowerGenome's unit table before clustering (Tom, 2026-10-04, for gas).
  - **Why 2031:** PowerGenome counts a unit in model year M only if Y > M. With 2030, a cluster whose units are
    all pushed would have no capacity in the 2030 stage and would be dropped (each hold project is such a
    cluster, and so can a gas cluster be). 2031 gives the same stages in Switch.
  - **Which units:** those whose cluster technology after PowerGenome's grouping matches the rule, as fedpol's
    function matches it ("coal" or "natural gas"). Gas units grouped into `Other_peaker`, including the converted
    units, don't match and keep their years, as under fedpol.
  - **fedpol's function** in `pg_to_switch.py` is unchanged and leaves 2031 alone (outside its window). Every
    other case keeps fedpol's 2030 encoding.
- **Held units:** with block_all the eight S0 holds are held in the 2028 **and 2030** stages, each with its own
  unit cap from §3.2, and gone from 2035. holds_persist is unchanged.
- **Cap unit set:** for consistency with the fleet, block_all also treats a cap unit's planned retirement year in
  2026-29 as in service through 2030 (§1.1 rule 3). Its 2021-24 history therefore counts in H and N in the 2028
  and 2030 stages.

**Model basis for these tables.** A public reconstruction of PowerGenome's coal basis
(`s0_workflow/data/coal_model_basis_860er2024.csv`):
- EIA-860 2024 **early release**, the vintage PUDL 2025_08 used: it has Brandon Shores 2025 and Stanton 2025;
- PowerGenome's July 2025 860M Retired sheet;
- plants in `reeds_plant_map.csv` only, as PowerGenome's EIA-860 units (A4);
- winter MW, or the nameplate where EIA-860 has none (Edwardsport CT1 / CT2, 240.6 MW each; A4).

It reproduces the rev. 2 caps and the rev. 2 rule labels, except zones left with no model coal (A4). Its model MW
differs from rev. 2's exactly by the unmapped plants and Edwardsport CT1 / CT2 (A4).

**Validation tables by option** (`s0_workflow/specs/coal/by_option/`, from
`s0_workflow/scripts/build_coal_option_tables.py`; the case build checks the table of the case's option):
- `coal_spec_expected_caps_by_stage.<option>.csv`;
- `coal_spec_hold_by_stage.<option>.csv`;
- `coal_spec_stage_summary.<option>.csv`.

planned_only and unrestricted have the same tables: economic retirement is a solve outcome, not part of the
predetermined fleet. block_all differs only in the 2028 and 2030 stages.

**Per-stage figures (August 2026 860M):**

| option | stage | N(p) | zones | zones with model coal | model coal before / after overrides (GW) | model-MW-weighted cap | own / national / blend / no coal left | S0 held (GW) |
|---|---|---|---|---|---|---|---|---|
| block_all | 2028 | 0.5785 | 74 | 72 | 166.71 / 163.59 | 0.5779 | 71 / 0 / 1 / 2 | 3.24 |
| block_all | 2030 | 0.5785 | 74 | 72 | 166.71 / 163.59 | 0.5779 | 71 / 0 / 1 / 2 | 3.24 |
| block_all | 2035 | 0.5998 | 70 | 61 | 134.72 / 125.48 | 0.5994 | 60 / 0 / 1 / 9 | 0.00 |
| block_all | 2040 | 0.6001 | 70 | 61 | 134.38 / 123.38 | 0.5998 | 60 / 0 / 1 / 9 | 0.00 |
| block_all | 2045 | 0.6001 | 69 | 61 | 134.04 / 123.38 | 0.5998 | 60 / 0 / 1 / 8 | 0.00 |
| planned_only | 2028 | 0.5806 | 73 | 69 | 157.03 / 156.35 | 0.5801 | 68 / 0 / 1 / 4 | 3.24 |
| planned_only | 2030 | 0.5926 | 71 | 65 | 143.47 / 141.14 | 0.5925 | 64 / 0 / 1 / 6 | 0.00 |
| planned_only | 2035 | 0.5998 | 70 | 61 | 134.72 / 125.48 | 0.5994 | 60 / 0 / 1 / 9 | 0.00 |
| planned_only | 2040 | 0.6001 | 70 | 61 | 134.38 / 123.38 | 0.5998 | 60 / 0 / 1 / 9 | 0.00 |
| planned_only | 2045 | 0.6001 | 69 | 61 | 134.04 / 123.38 | 0.5998 | 60 / 0 / 1 / 8 | 0.00 |
| unrestricted | 2028 | 0.5806 | 73 | 69 | 157.03 / 156.35 | 0.5801 | 68 / 0 / 1 / 4 | 3.24 |
| unrestricted | 2030 | 0.5926 | 71 | 65 | 143.47 / 141.14 | 0.5925 | 64 / 0 / 1 / 6 | 0.00 |
| unrestricted | 2035 | 0.5998 | 70 | 61 | 134.72 / 125.48 | 0.5994 | 60 / 0 / 1 / 9 | 0.00 |
| unrestricted | 2040 | 0.6001 | 70 | 61 | 134.38 / 123.38 | 0.5998 | 60 / 0 / 1 / 9 | 0.00 |
| unrestricted | 2045 | 0.6001 | 69 | 61 | 134.04 / 123.38 | 0.5998 | 60 / 0 / 1 / 8 | 0.00 |

Model coal in the zone clusters, 2028 / 2030 stages (GW; A4 basis):
- **block_all:** 163.59 / 163.59 (164.56 at 88e6b30, before A4; 166.53 at 479f213, before the OS removals were
  taken out of the push);
- **planned_only:** 156.35 / 141.14 (157.32 / 142.11 before A4);
- **unrestricted:** 156.35 / 141.14 before any economic retirements, which the model chooses.

Held projects (S0) come on top: 3.24 GW in 2028, and in 2030 with block_all.

**Zones whose cap or rule changes with block_all** (against planned_only; 2035 and later are unchanged):

| stage | zone | model coal after overrides (MW): planned_only → block_all | H (MW) | rule | cap |
|---|---|---|---|---|---|
| 2028 | p111 | 184 → 184 | 47 → 47 | blend → blend | 0.5309 → 0.5290 |
| 2028 | p130 | 0 → 108 | — → 108 | national (no coal) → own | 0.5806 → 0.1280 |
| 2028 | p24 | 2,235 → 2,455 | 2,235 → 2,455 | own → own | 0.6796 → 0.6699 |
| 2028 | p29 | 2,000 → 2,381 | 2,000 → 2,381 | own → own | 0.5899 → 0.5867 |
| 2028 | p33 | 1,317 → 1,579 | 1,317 → 1,579 | own → own | 0.7223 → 0.7266 |
| 2028 | p34 | 961 → 1,291 | 961 → 1,296 | own → own | 0.6910 → 0.6991 |
| 2028 | p43 | 2,324 → 3,004 | 2,324 → 3,004 | own → own | 0.5003 → 0.5105 |
| 2028 | p79 | 1,682 → 2,304 | 1,678 → 2,300 | own → own | 0.7314 → 0.6953 |
| 2028 | p81 | 1,750 → 2,935 | 1,753 → 2,938 | own → own | 0.8755 → 0.7899 |
| 2028 | p83 | 0 → 1,727 | 335 → 2,062 | own (no coal) → own | 0.4118 → 0.4936 |
| 2028 | p87 | — → 1,004 | — → 1,004 | — → own | — → 0.3948 |
| 2028 | p92 | 3,966 → 4,685 | 4,076 → 4,796 | own → own | 0.5123 → 0.4898 |
| 2030 | p103 | 1,601 → 4,416 | 1,602 → 4,417 | own → own | 0.6527 → 0.6414 |
| 2030 | p105 | 1,005 → 1,460 | 1,005 → 1,460 | own → own | 0.5753 → 0.5121 |
| 2030 | p107 | 8,447 → 11,045 | 7,389 → 9,987 | own → own | 0.5201 → 0.4541 |
| 2030 | p111 | 184 → 184 | 47 → 47 | blend → blend | 0.5419 → 0.5290 |
| 2030 | p112 | 4,969 → 5,589 | 4,969 → 5,589 | own → own | 0.6349 → 0.6427 |
| 2030 | p123 | — → 1,273 | — → 1,273 | — → own | — → 0.2238 |
| 2030 | p130 | 0 → 108 | — → 108 | national (no coal) → own | 0.5926 → 0.1280 |
| 2030 | p24 | 2,235 → 2,455 | 2,235 → 2,455 | own → own | 0.6796 → 0.6699 |
| 2030 | p29 | 2,000 → 2,381 | 2,000 → 2,381 | own → own | 0.5899 → 0.5867 |
| 2030 | p33 | 0 → 1,579 | — → 1,579 | national (no coal) → own | 0.5926 → 0.7266 |
| 2030 | p34 | 766 → 1,291 | 766 → 1,296 | own → own | 0.7024 → 0.6991 |
| 2030 | p43 | 1,813 → 3,004 | 1,813 → 3,004 | own → own | 0.5596 → 0.5105 |
| 2030 | p48 | 0 → 1,067 | — → 1,067 | national (no coal) → own | 0.5926 → 0.3132 |
| 2030 | p65 | 1,831 → 2,391 | 1,831 → 2,391 | own → own | 0.6514 → 0.6483 |
| 2030 | p72 | 3,857 → 4,851 | 3,857 → 4,851 | own → own | 0.8180 → 0.7419 |
| 2030 | p76 | — → 1,165 | — → 1,127 | — → own | — → 0.5795 |
| 2030 | p79 | 1,682 → 2,304 | 1,678 → 2,300 | own → own | 0.7314 → 0.6953 |
| 2030 | p81 | 1,750 → 2,935 | 1,753 → 2,938 | own → own | 0.8755 → 0.7899 |
| 2030 | p83 | 0 → 1,727 | 335 → 2,062 | own (no coal) → own | 0.4118 → 0.4936 |
| 2030 | p87 | — → 1,004 | — → 1,004 | — → own | — → 0.3948 |
| 2030 | p92 | 2,701 → 4,685 | 2,812 → 4,796 | own → own | 0.5207 → 0.4898 |
| 2030 | p97 | 660 → 1,040 | 660 → 1,040 | own → own | 0.5889 → 0.4930 |

### A4. Spec-side corrections after the VM build at 88e6b30 (2026-10-04)

The VM build at 88e6b30 passed the removal fix but failed the coal check in the 2028 stage. A read-only
investigation found three causes, all on the spec side. Tom's decisions:

1. **Overrides already satisfied by the build pass.**
   - **The case:** PowerGenome's fleet can already have the date or status an override wants: Brandon Shores 1 / 2
     dated 2029, Stanton 1 online with no date. The build then derives no override.
   - **The check:** a spec row of action retire, retire later or keep online with no override in the build passes
     as `ok (already satisfied: model <year | no date>)` when PowerGenome's unit table (before the edits) has the
     spec's year, or no date for keep online. "Only in spec" otherwise still fails.
   - **Why the public basis differs:** it is the EIA-860 2024 early release, which has Brandon Shores and Stanton
     at 2025; PowerGenome's fleet on the VM doesn't. The end state is the same.
2. **Plants not in `reeds_plant_map.csv` are not in the model.**
   - **Why:** PowerGenome's EIA-860 unit table keeps only plants with a model region. With no
     `region_aggregations` (`model_definition.yml`), only `reeds_plant_map.csv` gives one. §1.4's county step
     therefore doesn't apply to the model fleet. Biron Mill is the exception: PowerGenome adds it back from the 860M
     new-generator rows, and the build deletes it as an OS removal.
   - **Expected tables:** the model basis leaves these plants out. The `by_option` tables are regenerated for all
     three options. Spec override rows for them pass as `not in model: plant not in reeds_plant_map.csv` (12 rows:
     Seadrift Coke, Alpena Cement ×5, Toledo Refinery, Valero Corpus Christi ×2, Roquette, Savannah River Mill ×2).
     They are logged under the same label.
   - **Cap unit set unchanged:** H, own and N still come from every mapped 860M cap unit (§1.1-1.5; county
     placement included), so the caps and N are unchanged. Zones whose only model coal was unmapped now have
     "(no model coal after overrides)" and no coal clusters: p10 and p44 in every stage; p83 from 2035 (planned_only
     and unrestricted: from 2028); p92 from 2035. p42 and p74 drop out of the tables, and so does p76 from 2035
     (planned_only and unrestricted: from 2030).
   - **Known gap for the fleet refresh:** accepted as absent for S0 (Tom).
     - **Lower 48:** 68 coal-group units, 1,470.4 MW, listed in `coal_spec_not_in_model.csv` with their county zone
       (Biron Mill's 35.3 MW included).
     - **Alaska:** five more plants, 159.5 MW, have no zone at all: Healy 75, Chena 25.1, FWA Central Heat 22.9,
       Eielson AFB 19.5, University of Alaska Fairbanks 17.

     | county zone | plants (MW) |
     |---|---|
     | p70 | ADM Clinton (180), ADM Cedar Rapids (260), Roquette America (pet coke, 32) |
     | p83 | ADM Decatur (335) |
     | p92 | Tennessee Eastman (110.5) |
     | p94 | Savannah River Mill (pet coke, 84.6) |
     | p21 | WE Soda (41), General Chemical (30) |
     | p40 | ADM Columbus (61) |
     | p65 | Valero Corpus Christi West (pet coke, 53.8), Seadrift Coke (pet coke, 7.6) |
     | p10 | Argus Cogen (50) |
     | p103 | Alpena Cement (pet coke, 47.2), MSC Sebewaing (1.4) |
     | p99 | Covington (43) |
     | p122 | Pixelle Spring Grove (36.1) |
     | p76 | Biron Mill (35.3, incl. the OS removal GEN5) |
     | p37 | American Crystal Sugar Hillsboro (13.3), Drayton (6) |
     | p42 | American Crystal Sugar Moorhead (5), Crookston (6.5), East Grand Forks (7.5) |
     | p44 | Southern Minnesota Beet Sugar (7.5) |
     | p111 | Toledo Refinery (pet coke, 6) |
     | p74 | Neenah Paper Munising (5.8) |
     | p81 | SIUC (2.8) |
     | p20 | Western Sugar Billings (1.5) |

   - **p99:** Covington's 43 MW was in the 88e6b30 tables by the county step. Rev. 2's model MW didn't have it.
   - **What to watch:** PowerGenome adds 860M Operating units that aren't among its EIA-860 units back as new
     generators, placed by location (`import_new_generators`). That is how Biron Mill came back, and it could
     bring these plants into the case as well. The model MW of the caps counts PowerGenome's EIA-860 units only,
     so such units wouldn't appear there. The build therefore logs any coal-group unit in those rows: `coal: <n>
     coal-group units PowerGenome added from the 860M ...` (reported, not failed).
3. **Edwardsport CT1 / CT2 (p107).** EIA-860 2024 has no winter MW for them, and PowerGenome's unit table counts
   2 × 240.6 MW (their nameplate) in the coal group.
   - **Expected tables:** the basis uses the nameplate where winter MW is blank: +481.2 MW in p107 in every
     stage. `coal_spec_overrides.csv` gives CT1 / CT2 `model_winter_MW` 240.6.
   - **No other effect:** they remain "no change" (§2.5), and the p107 rule and cap don't change.

**Zone MW tolerance.** The build reports a zone whose model MW after the overrides differs from the expected
table by more than **0.1 MW** (was 1 MW; §1.5 reports M to 0.1 MW). The report doesn't fail the build. With A4 no
differences are expected. On the public basis there are none in any option or stage (test). A difference on the VM
means PowerGenome's fleet differs from the basis: report it.

**Figures after A4** (all options; the table above):
- **N and the caps:** unchanged.
- **Model coal after overrides:** 0.97 GW lower in every stage (−1.45 GW unmapped, +0.48 GW Edwardsport).
- **Zones with model coal:** block_all 72 in 2028 and 2030 (was 76); planned_only 69 / 65 (was 74 / 71); 61 from
  2035 (was 68).

**GEM dates are not used.** `update_coal_closures.py` (Global Energy Monitor closure dates written into the 860M)
is not applied in the S0 build: the build reads the unedited July 2025 860M (VM, 88e6b30), and nothing in
`pg_to_switch.py` runs it. This corrects §2.1's remark that the July 2025 860M "had been edited". The spec uses
860M dates only, by design.
