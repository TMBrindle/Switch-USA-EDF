# Tracked Demand Module — Design Decisions Register

This document records every design choice discussed during the module design
process, its resolution status, and the rationale. Items marked **DECIDED** are
reflected in the current spec. Items marked **OPEN** need your input before
coding begins.

---

## 1. How to Handle the Zonal Emissions Rate (Nonlinearity Problem)

**Context:** Attributing grid emissions to a tracked demand requires knowing the
emissions intensity of the zone's generation mix in each hour. This is the ratio
of two decision variables (total emissions ÷ total generation), making it
nonlinear if computed endogenously during the optimization.

| Option | Description | Status |
|--------|-------------|--------|
| **A. Exogenous grid emissions rate** | Use a pre-calculated parameter for grid emissions rate and grid clean fraction. Keeps the optimization fully linear. | **DECIDED — adopted for in-optimization use** |
| **B. Endogenous rate via linear reformulation** | Reformulate the ratio constraint to avoid division. Still introduces bilinear terms in most formulations. | Discarded — no clean linear reformulation exists for this |
| **C. Consequential/marginal emissions** | Use dual values from the energy balance constraint as marginal emissions. | Discarded as a constraint mechanism — but could be added as a **post-solve reporting** metric alongside average rates |
| **D. Post-solve endogenous calculation** | Compute actual emissions rates after the solve, when dispatch is fixed and the ratio is just arithmetic. | **DECIDED — adopted for post-solve accounting** |

**Resolution:** Two-layer approach. The optimization uses exogenous
`grid_clean_fraction[z, t]` as a parameter (Option A). The post-solve computes
the true endogenous rate from solved dispatch (Option D) for accurate reporting.

**OPEN:** How to source the initial `grid_clean_fraction` values. Options:
1. From a prior model run without CFE requirements (recommended)
2. From external data (Cambium, eGRID)
3. A conservative default (e.g., 0.5 everywhere)
4. Iterative: run → compute → feed back → re-run (most accurate but slower)

---

## 2. When to Enforce CFE Matching (Real-Time vs. End-of-Period)

**Context:** Should hourly clean energy matching be enforced as a hard constraint
during the optimization, or assessed after the fact?

| Option | Description | Status |
|--------|-------------|--------|
| **A. Hard hourly matching constraint** | Add `CleanEnergyAllocation[td, g, t]` variable; enforce exact hourly matching during solve. Adds millions of variables. | Discarded — too many variables, nonlinearity issues with emissions rate |
| **B. End-of-period compliance accounting** | The optimization makes dispatch/investment decisions; hourly compliance is checked post-solve, mirroring how PPA + REC procurement works in practice. | **DECIDED — adopted** |
| **C. Soft in-optimization target + post-solve accounting** | Time-block-level targets with penalty pricing in the optimization, full hourly accounting post-solve. | **DECIDED — adopted (this is the refinement of B)** |

**Resolution:** Option C. The optimization "steers" investment and dispatch toward
CFE compliance via penalty-priced time-block targets. The post-solve does precise
hourly accounting with endogenous emissions rates. This mirrors real-world
procurement: PPAs and investment decisions made upfront, REC/EAC compliance
settled after the fact.

---

## 3. How to Structure the Soft CFE Target

**Context:** A single annual target could let the model build all-solar and leave
nights unmatched. How to prevent this?

| Option | Description | Status |
|--------|-------------|--------|
| **A. Single annual target** | One clean energy fraction for the whole year. Simple but allows temporal mismatch. | Available as a configuration (set one block called "annual") but not the only option |
| **B. Time-block targets** | Separate targets for named blocks (summer_day, winter_night, etc.). Forces diverse procurement without full hourly granularity. | **DECIDED — adopted as the primary mechanism** |
| **C. Ramping penalty schedule** | Penalty price increases over periods, signaling tightening requirements. | **DECIDED — supported via period-indexed penalty in the input data** |
| **D. Dynamic inter-period adjustment** | Post-solve analysis identifies weak blocks and automatically tightens targets for next period. | Discarded for perfect foresight mode — not applicable when all periods solve simultaneously. Remains viable for myopic mode but not in current scope. |

**Resolution:** Time-block targets (Option B) with period-indexed penalties
(Option C). The user specifies targets and penalties per time block per period in
`tracked_demand_cfe_targets.csv`. This gives full control over the stringency
trajectory without requiring dynamic feedback.

**OPEN:** Should the module ship with a **default time-block definition** (e.g.,
4 blocks: summer day/night, winter day/night) or require the user to always
specify blocks? A default would lower the barrier to entry but might not match the
user's climate zones.

---

## 4. Demand Flexibility

**Context:** Should the tracked demand's load profile be fixed or should the model
optimize when it consumes electricity?

| Option | Description | Status |
|--------|-------------|--------|
| **A. Fixed load profile** | Demand is a parameter `td_demand_mw[td, t]`, exogenous. | Discarded as the sole option — too rigid |
| **B. Flexible dispatch within bounds** | Demand is a variable `TDDispatch[td, t]` with min/max power and an annual energy requirement. Model optimizes timing. | **DECIDED — adopted** |
| **C. Location-flexible** | Model also chooses which zone to site the demand in, following the MCET flexible_loads zone-set pattern. | **DECIDED — adopted as an option** (controlled by `td_location_fixed` flag) |

**Resolution:** Option B + optional C. The tracked demand has a `TDDispatch`
variable bounded by `td_min_power_mw` and `td_max_power_mw`, with an annual
energy requirement constraint. If `td_location_fixed = False`, the model also
optimizes siting across candidate zones.

A fixed-profile demand can still be specified by setting `td_min_power_mw =
td_max_power_mw` (forcing flat load) or by setting the annual energy requirement
to exactly match a desired profile with tight bounds.

**OPEN:** For **electrolyzers**, should the module also model hydrogen storage and
delivery schedules (production balance over time, buffer tank sizing)? This adds
realism but also complexity. Options:
1. Include H₂ storage as a separate storage type with its own cost parameters
2. Approximate H₂ storage by allowing the electrolyzer very wide min/max
   dispatch bounds and a loose annual energy requirement (conceptually similar)
3. Defer to a future extension or to Switch's existing hydrogen module

---

## 5. On-Site Storage: Colocated vs. Grid Asset

**Context:** Should on-site batteries be dedicated to the tracked demand or shared
with the grid?

| Option | Description | Status |
|--------|-------------|--------|
| **A. Dedicated colocated storage** | On-site storage is a separate investment that only serves the tracked demand. The grid doesn't see it. Simple to model, no double-counting issues. | **DECIDED — adopted for initial implementation** |
| **B. Shared storage with clean energy tracking** | Battery is a grid asset. Need to track clean vs. dirty charge state separately. Adds `CleanStorageLevel` variables. | Deferred — noted as a future enhancement |
| **C. Both options available** | Model can build dedicated TD storage and/or grid storage. They compete on cost. | **DECIDED — this is implicitly how it works**, since the system can still build grid-scale storage independently through Switch's normal storage module; the TD storage is simply an additional, dedicated option. |

**Resolution:** Option A for the tracked demand module, with Option C arising
naturally because Switch's existing storage module continues to operate
independently. The model can invest in both TD-dedicated storage and system-wide
storage, and the optimizer finds the right balance.

**OPEN:** Should dedicated TD storage be **behind the meter** (invisible to the
grid, doesn't contribute to Zone_Power_Injections) or **in front of the meter**
(can export surplus to the grid)? Behind-the-meter is simpler and avoids gaming
where TD storage arbitrages grid prices rather than serving CFE compliance. But
in-front-of-meter better represents some real configurations.

Current spec assumes behind-the-meter: storage only charges from and discharges to
the tracked demand's internal balance. It never injects into the grid.

---

## 6. On-Site Backup Generation

**Context:** Data centers may need diesel, gas, or fuel cell backup to meet grid
flexibility requirements.

| Option | Description | Status |
|--------|-------------|--------|
| **A. Generic on-site tech menu** | User specifies available technologies with costs, emissions, and runtime limits. Model invests and dispatches optimally. | **DECIDED — adopted** |
| **B. Pre-specified backup capacity** | Fixed amount of diesel backup as a parameter, not a decision variable. | Discarded — loses the interesting investment interaction |

**Resolution:** Option A. *(For fuel-burning techs, superseded 11 Oct 2026 by D3 below: they are ordinary
generators linked by `tracked_demand_onsite_gens.csv`. The tech menu remains for non-fuel plant.)* The
`tracked_demand_onsite_techs.csv` file defines
available technologies. The model decides how much of each to build. This captures
the key interaction: diesel is cheap for rare events, batteries are better when
CFE requirements exist, gas fills the middle.

> **Superseded 11 Oct 2026** (decisions D2, D3 below; CHANGES §108): fuel-burning on-site plant is an ordinary
> Switch generator linked to the tracked demand, and each carbon programme sets whether on-site emissions count,
> with its size threshold. The open question below is closed.

**OPEN:** Should on-site generators be subject to the system-wide carbon cap or
carbon cost from `carbon_policies.py`? Options:
1. **Yes** — on-site emissions are added to `AnnualEmissions` and face the same
   carbon price as grid generators. More realistic, creates proper incentive
   against dirty backup.
2. **No** — on-site emissions are only tracked in the TD's own accounting,
   affecting CFE score and 45V eligibility but not the system carbon cap. Simpler,
   avoids double-counting if the TD is already paying a CFE shortfall penalty.
3. **Configurable** — a flag determines whether on-site emissions enter the
   system carbon accounting.

---

## 7. Grid Flexibility Requirements

**Context:** Data centers may be required to reduce grid draw during stress events.

| Option | Description | Status |
|--------|-------------|--------|
| **A. Pre-specified flex events** | User identifies specific timepoints and max grid draw limits. Hard constraint. | **DECIDED — adopted** |
| **B. Endogenous reserve product** | TD offers curtailment capacity to the system and earns a capacity payment. Model decides how much to offer. | Deferred — elegant but adds complexity; could be a future extension |
| **C. Flex as coincident peak reduction** | TD must reduce grid draw during the N highest-demand hours. Requires identifying peak hours endogenously. | Deferred — similar issue to B; the peak hours depend on dispatch, creating a chicken-and-egg problem |

**Resolution:** Option A for now. Flex events are pre-specified in input data,
which is appropriate for modeling known reliability programs or interconnection
agreements. The user determines which timepoints are flex events (possibly using
the system peak hours from a prior run).

**OPEN:** Should there be a **cost associated with flex compliance**, or is it
purely a hard constraint? In reality, data centers face costs when they curtail
(lost revenue, SLA penalties). Options:
1. Hard constraint only (current spec) — the model always complies and finds the
   cheapest way to do so
2. Add a **flex non-compliance penalty** ($/MWh of excess grid draw during flex
   events) — allows the model to violate flex requirements if compliance is
   extremely expensive, which might reveal important cost thresholds

---

## 8. Perfect Foresight vs. Myopic Solve

**Context:** Switch-USA-PG-ReEDS primarily uses perfect foresight.

| Option | Description | Status |
|--------|-------------|--------|
| **A. Perfect foresight** | All periods solved simultaneously. CFE targets, penalties, and on-site investment trajectories specified upfront. No inter-period feedback needed. | **DECIDED — primary mode** |
| **B. Myopic with inter-period chaining** | Each period solved sequentially. Post-solve adjusts targets/penalties for next period based on observed performance. | Not in scope but the module design doesn't preclude it — the post-solve outputs could feed a chaining script |

**Resolution:** Designed for perfect foresight. All targets, penalties, and
technology costs are specified across all periods in the input data. The model
makes anticipatory investment decisions.

No open items here.

---

## 9. How Tracked Demand Interacts with Existing Zone Load

**Context:** Is the tracked demand part of `zone_demand_mw` or additional to it?

| Option | Description | Status |
|--------|-------------|--------|
| **A. Additional load** | TD grid draw is a new `Zone_Power_Withdrawal` on top of existing `zone_demand_mw`. The system must build/dispatch enough to serve both. | **DECIDED — adopted** |
| **B. Subset of existing load** | TD represents a portion of existing `zone_demand_mw` that is "tagged" for CFE tracking. Would require reducing `zone_demand_mw` to avoid double-counting. | Discarded — more complex, harder to configure, and the "additional load" framing better matches the use case of new facilities being added to the system |

**Resolution:** Option A. The tracked demand is new load added to the system. If
modeling an existing data center that's already in `zone_demand_mw`, the user
should reduce `zone_demand_mw` accordingly (e.g., via a `standard_load_addition =
-X` in the flexible loads module, or by adjusting the load input data).

No open items here.

---

## 10. Clean Energy Eligibility and Additionality

**Context:** Which generators count as "clean" for CFE purposes?

| Option | Description | Status |
|--------|-------------|--------|
| **A. All non-fuel-based generation** | Default to `NON_FUEL_ENERGY_SOURCES` — solar, wind, hydro, geothermal, nuclear, etc. | **DECIDED — adopted as default** |
| **B. User-specified eligible sources** | `tracked_demand_eligible_sources.csv` overrides the default. | **DECIDED — adopted as override** |
| **C. Generator-level eligibility with incrementality** | Specific generators are eligible based on online date (36-month rule), location, etc. | Deferred — this level of detail is better handled in post-solve accounting where specific generators can be evaluated against 45V rules. The in-optimization uses source-level eligibility; post-solve can filter by generator vintage. |

**Resolution:** Default to all non-fuel sources (Option A), with user override
(Option B). Generator-level 45V incrementality is a post-solve check (Option C
deferred to that layer).

**OPEN:** Should **nuclear** be eligible by default? Under 45V final rules,
existing nuclear has limited eligibility (capped at 200 MWh per reactor per
operating hour) but is not blanket excluded. For 24/7 CFE voluntary programs,
nuclear is typically included. Options:
1. Include nuclear in defaults, let user exclude via eligible sources file
2. Exclude nuclear from defaults, let user include
3. Include by default (current spec implies this since nuclear uses non-fuel
   energy source "Uranium")

---

## Summary of Open Items

| # | Question | Options | Recommendation |
|---|----------|---------|----------------|
| 1 | Source of initial `grid_clean_fraction` | Prior run / external data / default / iterative | Prior run with 0.5 as fallback default |
| 2 | Default time-block definition | Ship defaults / always user-specified | Ship a simple 4-block default (summer/winter × day/night) with option to override |
| 3 | Hydrogen storage modeling | Dedicated H₂ storage / wide dispatch bounds / defer | Wide dispatch bounds for v1; dedicated H₂ storage as future extension |
| 4 | TD storage metering (behind vs. in-front) | Behind-the-meter / in-front / configurable | Behind-the-meter for v1 |
| 5 | On-site emissions in system carbon accounting | Yes / No / configurable | Configurable with default = Yes. **Closed 11 Oct 2026 by D2:** configurable per programme, counted by default subject to the programme's threshold. |
| 6 | Flex compliance as hard vs. soft constraint | Hard only / add penalty option | Hard constraint for v1; penalty option as future extension |
| 7 | Nuclear eligibility default | Include / exclude | Include (consistent with Uranium being in NON_FUEL_ENERGY_SOURCES) |

---

## Decisions of 11 October 2026 (Tom)

Recorded on `tom/tracked-demands-v2` (CHANGES §108). They answer the step-1 audit (CHANGES §107) and take precedence
over the April text above and over `spec_v1.md` where they differ. "Supersedes" names the April item: register
sections (§n) and open items (OI n) above, and `spec_v1.md` §8 confirmed decisions (spec #n).

| # | Decision (11 Oct 2026) | Supersedes | Implemented |
|---|---|---|---|
| D1a | Stress-day behaviour is configurable per tracked demand: maximum (default), stated profile or minimum, plus a curtailable share (default 0) that combines with any of them. | — | step 2(d), CHANGES §108 |
| D1b | PRM requirement of a tracked demand = grid connection capacity, capped at its peak load, × (1 − curtailable share). It also enters the region peak. On-site plant gets no reserve credit. | — | step 2(d), CHANGES §108 (no on-site credit checked from 2(a)) |
| D2 | Carbon caps, per programme (RGGI, VA, CA, WA and any other in the model): whether on-site emissions count; a size threshold (RGGI/VA 25 MWe nameplate per generator; CA/WA about 25,000 tCO₂/yr per facility; current rules cited in the guide §15); a toggle for testing per unit or on total site capacity (needs a unit-size column per on-site tech); a limited-export exemption (RGGI-style ≤10% of gross generation to the grid), off by default, applying only with import-only or ≤10% export. On-site emissions are always reported, with whether each tonne is covered. | **spec #10** and spec §5.12 (on-site emissions in cap, default off; `td_onsite_emissions_in_system_cap`); **§6** open question; **OI 5** | step 2(a), CHANGES §108 |
| D3 | On-site fuel techs become ordinary Switch generators in the demand's zone, linked by `tracked_demand_onsite_gens.csv` (energy balance, zone withdrawal, optional no-export limit, not clean in matching). Reserve credit 0; turbines count in the gas-turbine build cap, reciprocating engines don't *(refined 12 Oct: membership follows the case's cap coverage, row below)*; headroom weight 0 (recorded as an assumption). | **spec #8** and **§6** resolution, for fuel-burning techs (private on-site dispatch); the tech menu stays for non-fuel plant | step 2(a), CHANGES §108 |
| D3 refined (12 Oct) | Gas-turbine cap membership of linked generators follows the case's own cap coverage, read per the case's form (`gtc_form` in `gas_turbine_cap_params.csv`): `allowance_coverage` under `cumulative_additions` (S0 v3.1 production cases: combined_cycle, combustion_turbine, aeroderivative; pg_to_switch writes this form for `s0_production.gas_turbine_cap.form: allowance`), `coverage` under `cumulative_in_service` and `new_additions_per_period` (combined_cycle, combustion_turbine), not "turbines count, reciprocating engines don't". Categories: frame CT → combustion_turbine; CC → combined_cycle; aero and small industrial CTs (`industrial_ct`, Titan 130 class) → aeroderivative; recips → reciprocating_engine; fuel cells and diesel never. A linked generator joins the cap only if its category is covered. | D3 (cap membership clause only) | CHANGES §108 (g), cap-coverage refinement; guide §15.1 |
| D4a | Grid connection cost: the zone network-upgrade $/kW by default, with an override per tracked demand (a multiplier on the zone cost or its own $/kW), in total system cost. | — | step 2(f), CHANGES §108 |
| D4b | Cost share for bill reporting: 100% paid by the demand by default, adjustable to 0%. Affects bills only, not the solve. | — | step 2(f), CHANGES §108 |
| D4c | Connection size: fixed input by default; optionally a decision variable. | — | step 2(f), CHANGES §108 |
| D4d | Optional headroom draw (off by default): the connection uses the zone's interconnection headroom, and the demand is charged the resulting increase in generator connection costs. Default measure: headroom shadow price × MW used; optional check by a counterfactual solve without the draw. Reported in the stage where the load connects and summed over later stages (headroom carries forward through the chained `ic_*` files). | — | step 2(f), CHANGES §108 |
| D5 | 45V stays post-solve (deferred). Later: in the objective once upstream methane from the gas work can feed lifecycle intensity. Current law: construction must begin before 1 January 2028 (OBBBA, P.L. 119-21, §70511, amending §45V(c)(3)(C)). | — | noted, guide §15 |
| D6 | No change to `chain_reuse` on this branch. Narrowing the code check to loaded modules goes into the reuse-digest work (HK-01) as a shared change. Re-record references only if a chain here needs reuse. | — | — |
| D7 | Merge the `s0-v3.1` tag (3160be1) into `tom/tracked-demands-v2` (merge, not rebase). | — | done (3d2b1ac) |
| D8 | Merge `hk/td-design-docs` when it exists; add these decisions to the register as a dated section, marking superseded April decisions. | — | done: merged (a36d9b2); this section |

## Decisions of 12 October 2026 (Tom): backup diesel

On the design proposal `diesel_backup_proposal.md` (written as §109; implemented as CHANGES §140, guide section 16).

| # | Decision (12 Oct 2026) | Implemented |
|---|---|---|
| E1 | Implement option B (stress grid limit `td_stress_grid_limit_share`, default 1 = D1b exactly) as an option; not option C. | §140 (5) |
| E2 | Stress-year basis `weather_year` by default; `all` as an option. | §140 (4) |
| E3 | Defaults: permit 36 full-load hours a year (sensitivities 24 and 42 documented); test 12 h (PLACEHOLDER pending the input research); config defaults to blackstart if the demand has gas-fired linked generators, else grid_backup, logged. | §140 (1), (2) |
| E4 | Non-emergency running only for tier 4 or `permitted_non_emergency`; the writer stops otherwise. The reading of 40 CFR 60.4211(f) / 63.6640(f) is to be confirmed by the input research. | §140 (6) |
| E5 | Potential to emit: add `td_onsite_pte_hours`; diesel at its permit hours. CA/WA treatment of emergency engines goes to the input research. | §140 (7) |
| E6 | Shortfall penalty: the case's lost-load cost if it defines one, else 10,000 $/MWh; strict off by default. | §140 (3) C6 |
| E3 revised (12 Oct, input research) | Test hours default 10 (filings 4–25 h; Laurelwood CA 10.5 h). Permit default 100 full-load hours as a PLACEHOLDER, with sensitivities 37 and 137 (VA fuel caps imply 37–137; MD 7–24; WA 45–100; sized under the major-source NOx threshold). Replaces E3's 36 h / 12 h. | §140 (12) |
| E7 | New `emergency_counts_toward_cap` (default 1): at 0 the permit cap leaves out emergency (stress-day) running. Federal rules put no time limit on emergency use; whether state permit caps count emergency hours is being checked. | §140 (12) |
| E8 | CA and WA never cover backup diesel at the facility (CA 17 CCR §95101(f)(2), supplier obligation §95852(d); WA WAC 173-446-040(3)(a)(ii), supplier's under (3)(c)(i)): such rows are not covered, with the citation, whatever their potential to emit. `td_onsite_pte_hours` stays for other fuels; RGGI/VA unit basis unchanged. Refines E5. | §140 (12) |
| E9 | Federal limits confirmed (40 CFR 60.4211(f), 63.6640(f)); emergency demand response removed from the 100 h (vacated 2015–16, removed from the CFR 2022); paid local-reliability dispatch only under the five conditions (NSPS and area-source NESHAP engines). Settles E4's "to be confirmed". | guide 16.2 |

