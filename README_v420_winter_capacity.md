# V4-20: winter-capacity double count in S0 v3.1 existing generators (12 Oct 2026)

This analysis was read-only.

**Inputs:**
- the S0 v3.1 2028 stage inputs (`s0v31_tt/2028/S0prod_A`, mirror copy);
- PowerGenome's own PUDL database (`pg_data/pudl.2025_08.sqlite`, EIA-860 2024, existing generators).

## Cause
- **S0 v3.1 settings:** existing capacity is built with `resources.yml` `capacity_col: winter_capacity_mw`.
- **PowerGenome version:** the build ran a PowerGenome without upstream commit **a8dd566**. That copy of `generators.supplement_generator_860_data` fills a blank winter capacity with nameplate for each `unit_id_pg`. In this code `unit_id_pg` is plant + generator, so every generator with a blank winter capacity gets its nameplate.
- **What EIA-860 does:** it often puts the whole plant's winter capacity on one generator and leaves the others blank. The blank generators therefore add their nameplate on top of the plant total.
- **The fix in a8dd566:** nameplate only where the whole plant is blank, and 0 otherwise.
- **Overcount per generator:** the old fill minus the fixed fill.

## Result
**10.2 GW in total, 9.3 GW of it combined cycle.** CC accounts for 2.9% of the model's 320 GW of existing CC capacity, matching the commit author's estimate of about 10 GW.

**Overcount by technology:**

| Technology | Overcount (MW) | Plants | Model existing (MW) |
|---|---|---|---|
| Natural gas combined cycle | 9,273 | 25 | 320,310 |
| Coal IGCC (Edwardsport) | 481 | 1 | (not checked) |
| Natural gas steam turbine | 294 | 2 | (not checked) |
| Conventional hydro | 133 | 5 | 71,561 |
| Petroleum liquids | 57 | 1 | 26,917 |
| Natural gas combustion turbine | 7 | 1 | 148,715 |

**Overcount by PRM region** (MW: all technologies, with combined cycle in brackets):
- MISO 5,480 (4,904);
- SPP 1,496 (1,196);
- PJM 746 (746);
- SERC_C 604 (604);
- NPCC_NY 564;
- NPCC_NE 514;
- SERC_SE 406;
- WestConnect_North 338;
- NorthernGrid_East 91 (hydro);
- WECC_CA 4.

**Largest plants:**

| Plant | State | Zone | Region | Overcount (MW) | Old fill (MW) | Reported winter (MW) |
|---|---|---|---|---|---|---|
| Union Power Partners | AR | p85 | MISO | 1,408 | 3,726 | 2,318 |
| Redbud | OK | p50 | SPP | 796 | 2,010 | 1,214 |
| Victor J Daniel Jr | MS | p87 | MISO | 761 | 2,949 | 2,188 |
| Acadia | LA | p58 | MISO | 695 | 1,244 | 550 |
| Kemper County | MS | p88 | SERC_C | 605 | 1,335 | 730 |

**The model carries it.** In all 14 zones with affected CC plants, the model's existing CC capacity (`gen_build_predetermined`, build years ≤ 2024) equals the old-fill total to the MW. It differs from the fixed fill by exactly the overcount. Only 74 MW of the overcount sits at plants with a planned retirement before 2028.

## What it reaches in S0 v3.1
It reaches **both the reliability constraint and dispatch**, because the inflated MW is the capacity itself (`GenCapacity`):
- **Planning reserve:** `prm_regional.py` credits `GenCapacityInTP × prm_avail_frac` for thermal units (line 376). At CC availability of about 0.93, that is roughly 8.6 GW of phantom firm credit, 4.6 GW of it in MISO.
- **Dispatch:** Switch's dispatch limit is `GenCapacity` × availability, so the phantom capacity can also generate in every hour. On the stress days, dispatch is capped at `GenCapacityInTP × prm_avail_frac` (line 407).
- **Costs and retirements:** the phantom MW carry existing fixed O&M, and they are available to the retirement decisions.

Whether the reserve constraint binds in the affected regions, and so how much the overcount changes builds and emissions, is not assessed here.

## Files
- `v420_overcount_by_tech.csv`
- `v420_overcount_by_prm_region.csv`
- `v420_plants_affected.csv`: one row per affected plant.
- `v420_cc_zone_check.csv`: model CC capacity against both fills, by zone.
