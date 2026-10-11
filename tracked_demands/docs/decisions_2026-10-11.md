# Tracked demands: decisions of 11 October 2026 (Tom)

**Staged here until the decisions register arrives** on `hk/td-design-docs` (the April design documents). When that
branch is merged, append this section to the register as a dated entry and delete this file. Where the April
register numbers a decision this one supersedes, write the number in the "Supersedes" column. The numbers aren't
known on this branch yet.

| # | Decision (11 Oct 2026) | Supersedes | Implemented |
|---|---|---|---|
| D1a | Stress-day behaviour is configurable per tracked demand: maximum (default), stated profile or minimum, plus a curtailable share (default 0) that combines with any of them. | — | step 2(d) |
| D1b | PRM requirement of a tracked demand = grid connection capacity, capped at its peak load, × (1 − curtailable share). It also enters the region peak. On-site plant gets no reserve credit. | — | step 2(d); no credit checked from 2(a) |
| D2 | Carbon caps, per programme (RGGI, VA, CA, WA and any other in the model): whether on-site emissions count; a size threshold (RGGI/VA 25 MWe nameplate per generator; CA/WA about 25,000 tCO₂/yr per facility; current rules cited in the guide §15); a toggle for testing per unit or on total site capacity (needs a unit-size column per on-site tech); a limited-export exemption (RGGI-style ≤10% of gross generation to the grid), off by default, applying only with import-only or ≤10% export. On-site emissions are always reported, with whether each tonne is covered. | **April default "on-site emissions off"** (`td_onsite_emissions_in_system_cap` = 0) | step 2(a), CHANGES §108 |
| D3 | On-site fuel techs become ordinary Switch generators in the demand's zone, linked by `tracked_demand_onsite_gens.csv` (energy balance, zone withdrawal, optional no-export limit, not clean in matching). Reserve credit 0; turbines count in the gas-turbine build cap, reciprocating engines don't; headroom weight 0 (recorded as an assumption). | April design's private on-site dispatch for fuel techs | step 2(a), CHANGES §108 |
| D4a | Grid connection cost: the zone network-upgrade $/kW by default, with an override per tracked demand (a multiplier on the zone cost or its own $/kW), in total system cost. | — | step 2(f) |
| D4b | Cost share for bill reporting: 100% paid by the demand by default, adjustable to 0%. Affects bills only, not the solve. | — | step 2(f) |
| D4c | Connection size: fixed input by default; optionally a decision variable. | — | step 2(f) |
| D4d | Optional headroom draw (off by default): the connection uses the zone's interconnection headroom, and the demand is charged the resulting increase in generator connection costs. Default measure: headroom shadow price × MW used; optional check by a counterfactual solve without the draw. Reported in the stage where the load connects and summed over later stages (headroom carries forward through the chained `ic_*` files). | — | step 2(f) |
| D5 | 45V stays post-solve (deferred). Later: in the objective once upstream methane from the gas work can feed lifecycle intensity. Current law: construction must begin before 1 January 2028 (OBBBA, P.L. 119-21, §70511, amending §45V(c)(3)(C)). | — | noted, guide §15 |
| D6 | No change to `chain_reuse` on this branch. Narrowing the code check to loaded modules goes into the reuse-digest work (HK-01) as a shared change. Re-record references only if a chain here needs reuse. | — | — |
| D7 | Merge the `s0-v3.1` tag (3160be1) into `tom/tracked-demands-v2` (merge, not rebase). | — | done (3d2b1ac) |
| D8 | Merge `hk/td-design-docs` when it exists; add these decisions to the register as a dated section, marking superseded April decisions. | — | pending: branch not yet pushed (checked 11 Oct) |
