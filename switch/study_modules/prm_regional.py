"""
Regional planning reserve requirement on stress days (S0 production, CHANGES §55).

Replaces, for the cases that use it (s0_production.prm.design: regional), the per-zone
planning_reserves + planning_reserves_extreme_days pair. The case's scenario line excludes those two
modules and includes this one.

Reserve regions (PRM_REGIONS) pool load zones (ReEDS nercr; WECC_NW by transgrp). The requirement is
checked in every timepoint of the stress timeseries (PRM_TIMESERIES: real weather days, zero weight),
and only there.

For each zone z of region r and stress timepoint t of period p:

    sum_g credit[g, t] + sum_in PrmFlow * trans_efficiency - sum_out PrmFlow + PrmZoneShortfall[z, p]
        >= (1 + prm_margin[r, p]) * served_load[z, t]

  credit       capacity class: GenCapacity * prm_avail_frac[g, t] (thermal: 1 - FOR at the stress-day
               temperature; written by the case build), variable: GenCapacity * min(1, max CF at t),
               dispatch (hydro): DispatchGen[g, t] (bounded by the hydro module's water / energy limits
               for that day), storage: DispatchGen - ChargeStorage (state of charge tracked through the
               stress day by the storage module, so charging counts against the requirement), none: 0.
               Distributed generators are grossed up for avoided local T&D losses.
  served_load  the energy balance's load at the central node: every Distributed_Power_Withdrawals
               component (zone_demand_mw and any DER-side loads) / (1 - local_td_loss_rate) when
               local_td is used, else zone_demand_mw.
  PrmFlow      reserve (capacity) transfers over each direction of each transmission line, separate
               from energy dispatch: delivered MW = PrmFlow * trans_efficiency (the energy balance's
               line losses); limited by existing capacity + (1 - prm_new_tx_derate) x new builds (in a
               chain, new lines of earlier stages too: trans_built_to_date.csv), times
               trans_derating_factor, and by trans_directional_cap_mw where that module sets one.
               Within a region this is the deliverability check (each zone's requirement is met by
               local capacity plus net inflows over internal lines); flows across a region boundary
               are imports.

Region-level import cap, in EVERY stress timepoint (also hours when storage charges, so storage can't
"launder" imports into local capacity):

    PrmNetImport[r, t] = sum of delivered inflows from other regions - outflows to them
        <= prm_import_share[r, p] * prm_region_peak_mw[r, p]

prm_region_peak_mw: the region's highest coincident zone_demand_mw in the period's timepoints (the
stress days include each region's peak days). A blank share means no cap.

Flow-based credit for new transmission (prm_new_tx_flow_split = 1; CHANGES §87, the S0 case writer's setting for every
regional case): on each line crossing a region boundary, the reserve flow is split in two.
  * PrmFlow[a, b, t], the existing-capacity part: limited by (existing - built in earlier stages) x
    trans_derating_factor per direction, and the only part in PrmNetImport, so the only part under the import cap.
  * PrmFlowNew[a, b, t], the new-capacity part: one limit per line and hour across BOTH directions,
        PrmFlowNew[a, b, t] + PrmFlowNew[b, a, t] <= (1 - prm_new_tx_derate) x new capacity x trans_derating_factor
    (new = this stage's BuildTx to date plus earlier stages' trans_built_to_date_mw), exempt from the import cap.
New capacity then counts by physics: in an hour it carries reserve one way, to one end, once. When both ends are
stressed in the same hour it can't serve both; when their stresses don't coincide each end gets the full amount in its
own hours. The trans_asymmetric_capacity directional cap limits the sum of the two parts. Lines inside a region keep
the single PrmFlow. prm_new_tx_flow_split = 0 (the module default, so case folders built before §87 solve as they did):
one PrmFlow per direction up to existing + (1 - derate) x new, all of it under the import cap (+ the retired
prm_import_new_tx_allowance; it can't be combined with the split).
No transit netting (v3.1 review): letting new-line exports offset existing-tie imports in the cap reopens double
counting through opposite flows on the same new line in one hour (CHANGES §89), and netting on net new exports
instead needs max(0, .) on the wrong side of the cap (non-convex). Reserve passing through a region over an existing
tie counts against that region's cap.

Shortfall: PrmZoneShortfall[z, p] (MW, applies to every stress timepoint of the period) at
prm_shortfall_cost_per_mw_yr in each year of the period; PrmShortfall[r, p] sums a region's zones.

Outputs (post_solve): prm_shortfall.csv, prm_zone_hours.csv (with hourly duals), prm_zone_prices.csv
(reserve price by zone: the sum of its stress-hour duals, $/kW-yr, <= the penalty), prm_region_hours.csv
(margin achieved, import use, import-cap duals), prm_summary.csv and prm_capacity_credit.csv
(implied capacity credit by class and region against the benchmarks in prm_benchmarks.csv), prm_tx_reserve_value.csv
(v3.1: the reserve value of 1 MW more new capacity on each buildable line, $/kW-yr, including lines with none built
yet). Duals
need the `dual` suffix (switch/modules.txt's write_dual_costs declares it for every run) and an LP solution with duals (Gurobi barrier
gives them with or without crossover; a MIP gives none, and the files then say so).

Inputs:
    prm_zones.csv               LOAD_ZONE, PRM_REGION
    prm_region_periods.csv      PRM_REGION, PERIOD, prm_margin, prm_import_share*
    prm_timeseries.csv          TIMESERIES
    prm_gen_credit.csv*         GENERATION_PROJECT, prm_credit*, prm_class*
    prm_gen_availability.csv*   GENERATION_PROJECT, TIMEPOINT, prm_avail_frac
    prm_params.csv*             prm_new_tx_derate, prm_shortfall_cost_per_mw_yr, prm_import_cap_all_hours,
                                prm_new_tx_flow_split* (1: flow-based credit for new boundary capacity, above),
                                prm_import_new_tx_allowance* (retired by §87; folders built before it: + allowance x
                                new boundary capacity on the cap),
                                prm_compact_capacity* (1: compact reserve rows, below)

Compact reserve rows (prm_compact_capacity = 1; CHANGES §69). The hourly rows above put every capacity-credit unit's
capacity (GenCapacity: one BuildGen / SuspendGen column per vintage) and the zone's shortfall column into every stress
hour of the zone: dense columns that drive factorisation fill-in. Compact form, exact:
  * the zone's stress hours fall into derate groups k: hours whose capacity-credit units and prm_avail_frac values are
    identical (with the seasonal derate, the stress days' seasons: at most 3 per zone and period);
  * PrmAccCap[z, p, k] == sum over capacity-credit units of GenCapacity x prm_avail_frac (x the T&D gross-up),
    defined once per group (Prm_Acc_Cap_Def);
  * each hourly row uses PrmAccCap[z, p, k(t)] instead of the units' capacity terms; variable (wind, solar), dispatch
    (hydro, imports) and storage credits keep their hourly terms;
  * shortfall: PrmGroupShortfall[z, p, k] in the hourly rows, PrmZoneShortfall[z, p] >= PrmGroupShortfall[z, p, k]
    once per group (Prm_Group_Shortfall_Link); the penalty stays on PrmZoneShortfall, so the optimum is the same.
A capacity column then appears in at most one row per group instead of every stress hour.
    trans_built_to_date.csv*    TRANSMISSION_LINE, trans_built_to_date_mw (chained stages; prepare_next_stage)
    prm_benchmarks.csv*         PRM_REGION, prm_class, benchmark, benchmark_value
"""

import os

import pandas as pd
from pyomo.environ import (
    Any,
    Constraint,
    Expression,
    NonNegativeReals,
    Param,
    PercentFraction,
    Reals,
    Set,
    Var,
    value,
)

dependencies = (
    "switch_model.timescales",
    "switch_model.financials",
    "switch_model.balancing.load_zones",
    "switch_model.generators.core.build",
    "switch_model.generators.core.dispatch",
    "switch_model.transmission.transport.build",
)
optional_dependencies = (
    "switch_model.transmission.local_td",
    "switch_model.generators.storage",
    "study_modules.generators_extensions_storage",
    "study_modules.trans_asymmetric_capacity",
)

CREDIT_TYPES = ("capacity", "variable", "dispatch", "storage", "none")


def define_components(m):
    m.PRM_ZONES = Set(dimen=2)  # (zone, region)
    m.PRM_REGIONS = Set(
        dimen=1, ordered=True, initialize=lambda m: list(dict.fromkeys(r for (z, r) in m.PRM_ZONES))
    )
    # "" for a zone in no reserve region (no requirement there)
    m.prm_region_of_zone = Param(
        m.LOAD_ZONES,
        within=Any,
        initialize=lambda m, z: next((r for (zz, r) in m.PRM_ZONES if zz == z), ""),
    )
    m.ZONES_IN_PRM_REGION = Set(
        m.PRM_REGIONS,
        initialize=lambda m, r: [z for (z, rr) in m.PRM_ZONES if rr == r],
    )
    m.PRM_REGION_PERIODS = Set(dimen=2, within=m.PRM_REGIONS * m.PERIODS)
    m.prm_margin = Param(m.PRM_REGION_PERIODS, within=Reals)
    m.prm_import_share = Param(
        m.PRM_REGION_PERIODS, within=NonNegativeReals, default=float("inf")
    )

    m.PRM_TIMESERIES = Set(dimen=1, within=m.TIMESERIES)
    m.PRM_TPS = Set(
        dimen=1,
        ordered=True,
        initialize=lambda m: [t for ts in m.PRM_TIMESERIES for t in m.TPS_IN_TS[ts]],
    )

    # parameters
    m.prm_new_tx_derate = Param(within=PercentFraction, default=0.15)
    m.prm_shortfall_cost_per_mw_yr = Param(within=NonNegativeReals, default=0.0)
    # 1 (default): import cap in every stress timepoint; 0: only at each region's peak-load stress
    # timepoint (diagnostic only: shows the storage "laundering" loophole the default closes)
    m.prm_import_cap_all_hours = Param(within=NonNegativeReals, default=1)
    # retired (§87): bill cases' reserve imports could also use this share of new interregional capacity built into
    # the region. Kept so folders built before §87 load; the case writer no longer writes it
    m.prm_import_new_tx_allowance = Param(within=NonNegativeReals, default=0.0)
    # 1: flow-based credit for new capacity on region-boundary lines (§87; module docstring); 0 (default): as before
    m.prm_new_tx_flow_split = Param(within=NonNegativeReals, default=0)
    # 1: compact reserve rows (CHANGES §69): capacity-credit units enter through one accredited-capacity variable per
    # zone, period and derate group, and the shortfall through one variable per group; 0 (default): hourly terms
    m.prm_compact_capacity = Param(within=NonNegativeReals, default=0)

    # generator credit
    def credit_default(m, g):
        if g in getattr(m, "STORAGE_GENS", ()):
            return "storage"
        if m.gen_is_variable[g]:
            return "variable"
        return "capacity"

    m.prm_credit = Param(
        m.GENERATION_PROJECTS,
        within=Any,
        default=credit_default,
        validate=lambda m, v, g: v in CREDIT_TYPES,
    )
    m.prm_class = Param(m.GENERATION_PROJECTS, within=Any, default="other")
    m.PRM_GEN_AVAIL_TPS = Set(dimen=2)
    m.prm_avail_frac = Param(m.PRM_GEN_AVAIL_TPS, within=PercentFraction)

    m.prm_td_multiplier = Param(
        m.LOAD_ZONES,
        initialize=lambda m, z: (
            1 / (1 - m.local_td_loss_rate[z]) if hasattr(m, "local_td_loss_rate") else 1.0
        ),
    )

    # reserve transfers over the network
    m.PRM_TX_TPS = Set(
        dimen=3,
        initialize=lambda m: [
            (zf, zt, t)
            for (zf, zt) in m.DIRECTIONAL_TX
            for t in m.PRM_TPS
            if m.prm_region_of_zone[zf] != "" and m.prm_region_of_zone[zt] != ""
        ],
    )
    m.PrmFlow = Var(m.PRM_TX_TPS, within=NonNegativeReals)

    # new transmission earlier stages of a chain built (carried into existing_trans_cap by
    # prepare_next_stage; trans_built_to_date.csv): still derated as new
    m.trans_built_to_date_mw = Param(m.TRANSMISSION_LINES, within=NonNegativeReals, default=0.0)

    def tx_cap(m, tx, p):
        new = sum(
            m.BuildTx[tx, b] for b in m.PERIODS if b <= p and (tx, b) in m.TRANS_BLD_YRS
        )
        before = min(value(m.trans_built_to_date_mw[tx]), value(m.existing_trans_cap[tx]))
        return (
            m.existing_trans_cap[tx] - before + (1 - m.prm_new_tx_derate) * (before + new)
        ) * m.trans_derating_factor[tx]

    m.PrmTxCapacity = Expression(m.TRANSMISSION_LINES, m.PERIODS, rule=tx_cap)

    # flow-based credit for new capacity on region-boundary lines (prm_new_tx_flow_split; §87)
    def split(m):
        return value(m.prm_new_tx_flow_split) >= 1

    def is_boundary(m, tx):
        r1, r2 = m.prm_region_of_zone[m.trans_lz1[tx]], m.prm_region_of_zone[m.trans_lz2[tx]]
        return r1 != "" and r2 != "" and r1 != r2

    m.PRM_SPLIT_LINES = Set(
        dimen=1, initialize=lambda m: [tx for tx in m.TRANSMISSION_LINES if split(m) and is_boundary(m, tx)]
    )

    def can_have_new(m, tx, p):
        return value(m.trans_built_to_date_mw[tx]) > 0 or any(
            (tx, b) in m.TRANS_BLD_YRS for b in m.PERIODS if b <= p)

    # (line, stress timepoint) pairs where the line may carry new capacity: the new-part rows and columns
    m.PRM_NEW_TX_LINE_TPS = Set(
        dimen=2,
        initialize=lambda m: [(tx, t) for tx in m.PRM_SPLIT_LINES for t in m.PRM_TPS
                              if can_have_new(m, tx, m.tp_period[t])],
    )
    m.PRM_NEW_TX_TPS = Set(
        dimen=3,
        initialize=lambda m: [(zf, zt, t) for (tx, t) in m.PRM_NEW_TX_LINE_TPS
                              for (zf, zt) in ((m.trans_lz1[tx], m.trans_lz2[tx]), (m.trans_lz2[tx], m.trans_lz1[tx]))],
    )
    m.PrmFlowNew = Var(m.PRM_NEW_TX_TPS, within=NonNegativeReals)

    def tx_cap_existing(m, tx):
        before = min(value(m.trans_built_to_date_mw[tx]), value(m.existing_trans_cap[tx]))
        return (m.existing_trans_cap[tx] - before) * m.trans_derating_factor[tx]

    def tx_cap_new(m, tx, p):
        new = sum(m.BuildTx[tx, b] for b in m.PERIODS if b <= p and (tx, b) in m.TRANS_BLD_YRS)
        before = min(value(m.trans_built_to_date_mw[tx]), value(m.existing_trans_cap[tx]))
        return (1 - m.prm_new_tx_derate) * (before + new) * m.trans_derating_factor[tx]

    def flow_limit(m, zf, zt, t):
        tx = m.trans_d_line[zf, zt]
        if tx in m.PRM_SPLIT_LINES:                    # boundary line, split: the existing-capacity part
            return m.PrmFlow[zf, zt, t] <= tx_cap_existing(m, tx)
        return m.PrmFlow[zf, zt, t] <= m.PrmTxCapacity[tx, m.tp_period[t]]

    m.Prm_Flow_Limit = Constraint(m.PRM_TX_TPS, rule=flow_limit)
    # the new-capacity part: both directions share the line's new capacity in each hour
    m.Prm_New_Flow_Limit = Constraint(
        m.PRM_NEW_TX_LINE_TPS,
        rule=lambda m, tx, t: m.PrmFlowNew[m.trans_lz1[tx], m.trans_lz2[tx], t]
        + m.PrmFlowNew[m.trans_lz2[tx], m.trans_lz1[tx], t]
        <= tx_cap_new(m, tx, m.tp_period[t]),
    )

    def new_flow(m, zf, zt, t):
        return m.PrmFlowNew[zf, zt, t] if (zf, zt, t) in m.PRM_NEW_TX_TPS else 0.0

    def directional_rule(m, zf, zt, t):
        cap = m.trans_directional_cap_mw[zf, zt, m.tp_period[t]]
        if cap == float("inf"):
            return Constraint.Skip
        return m.PrmFlow[zf, zt, t] + new_flow(m, zf, zt, t) <= cap

    if hasattr(m, "trans_directional_cap_mw"):
        m.Prm_Flow_Directional_Limit = Constraint(m.PRM_TX_TPS, rule=directional_rule)

    m.PrmNetInflow = Expression(
        m.LOAD_ZONES,
        m.PRM_TPS,
        rule=lambda m, z, t: sum(
            (m.PrmFlow[zf, z, t] + new_flow(m, zf, z, t)) * m.trans_efficiency[m.trans_d_line[zf, z]]
            for zf in m.TX_CONNECTIONS_TO_ZONE[z]
            if (zf, z, t) in m.PRM_TX_TPS
        )
        - sum(m.PrmFlow[z, zt, t] + new_flow(m, z, zt, t)
              for zt in m.TX_CONNECTIONS_TO_ZONE[z] if (z, zt, t) in m.PRM_TX_TPS),
    )

    def net_import(m, r, t):
        zones = set(m.ZONES_IN_PRM_REGION[r])
        inflow = sum(
            m.PrmFlow[zf, zt, t] * m.trans_efficiency[m.trans_d_line[zf, zt]]
            for (zf, zt) in m.DIRECTIONAL_TX
            if zt in zones and zf not in zones and (zf, zt, t) in m.PRM_TX_TPS
        )
        outflow = sum(
            m.PrmFlow[zf, zt, t]
            for (zf, zt) in m.DIRECTIONAL_TX
            if zf in zones and zt not in zones and (zf, zt, t) in m.PRM_TX_TPS
        )
        return inflow - outflow

    # the import cap's quantity: with the split, PrmFlow on boundary lines is the existing-capacity part only
    m.PrmNetImport = Expression(m.PRM_REGIONS, m.PRM_TPS, rule=net_import)

    def new_import(m, r, t):
        zones = set(m.ZONES_IN_PRM_REGION[r])
        inflow = sum(
            m.PrmFlowNew[zf, zt, t] * m.trans_efficiency[m.trans_d_line[zf, zt]]
            for (zf, zt) in m.DIRECTIONAL_TX
            if zt in zones and zf not in zones and (zf, zt, t) in m.PRM_NEW_TX_TPS
        )
        outflow = sum(
            m.PrmFlowNew[zf, zt, t]
            for (zf, zt) in m.DIRECTIONAL_TX
            if zf in zones and zt not in zones and (zf, zt, t) in m.PRM_NEW_TX_TPS
        )
        return inflow - outflow

    # net reserve import over new boundary capacity (exempt from the cap; 0 without the split)
    m.PrmNewImport = Expression(m.PRM_REGIONS, m.PRM_TPS, rule=new_import)

    m.prm_region_peak_mw = Param(
        m.PRM_REGION_PERIODS,
        initialize=lambda m, r, p: max(
            [sum(m.zone_demand_mw[z, t] for z in m.ZONES_IN_PRM_REGION[r]) for t in m.TPS_IN_PERIOD[p]]
            or [0.0]
        ),
    )

    # shortfall
    m.PRM_ZONE_PERIODS = Set(
        dimen=2,
        initialize=lambda m: [(z, p) for (r, p) in m.PRM_REGION_PERIODS for z in m.ZONES_IN_PRM_REGION[r]],
    )
    m.PrmZoneShortfall = Var(m.PRM_ZONE_PERIODS, within=NonNegativeReals)
    m.PrmShortfall = Expression(
        m.PRM_REGION_PERIODS,
        rule=lambda m, r, p: sum(m.PrmZoneShortfall[z, p] for z in m.ZONES_IN_PRM_REGION[r]),
    )
    m.PrmShortfallCost = Expression(
        m.PERIODS,
        rule=lambda m, p: m.prm_shortfall_cost_per_mw_yr
        * sum(m.PrmShortfall[r, pp] for (r, pp) in m.PRM_REGION_PERIODS if pp == p),
    )
    m.Cost_Components_Per_Period.append("PrmShortfallCost")


def define_dynamic_components(m):
    # generator credit: built after every module (storage may be loaded after this one)
    def credit_rule(m, g, t):
        c = m.prm_credit[g]
        if c == "none":
            return 0.0
        if c == "capacity":
            frac = m.prm_avail_frac[g, t] if (g, t) in m.PRM_GEN_AVAIL_TPS else 1.0
            e = m.GenCapacityInTP[g, t] * frac
        elif c == "variable":
            e = m.GenCapacityInTP[g, t] * min(1.0, value(m.gen_max_capacity_factor[g, t]))
        elif c == "dispatch":
            e = m.DispatchGen[g, t]
        else:  # storage: net scheduled delivery, backed by the storage module's state of charge
            e = m.DispatchGen[g, t] - m.ChargeStorage[g, t]
        if m.gen_is_distributed[g]:
            e = e * m.prm_td_multiplier[m.gen_load_zone[g]]
        return e

    m.PRM_GEN_TPS = Set(
        dimen=2,
        initialize=lambda m: [
            (g, t)
            for t in m.PRM_TPS
            for g in m.GENS_IN_PERIOD[m.tp_period[t]]
            if (g, t) in m.GEN_TPS and m.prm_region_of_zone[m.gen_load_zone[g]] != ""
        ],
    )
    m.PrmGenCredit = Expression(m.PRM_GEN_TPS, rule=credit_rule)

    # light stress days (CHANGES §69): with no commitment there, thermal dispatch is held to the same derated
    # capacity the reserve credit uses (nameplate x prm_avail_frac, 1 - FOR at the stress-day temperature)
    m.PRM_LIGHT_THERMAL_TPS = Set(
        dimen=2,
        initialize=lambda m: [(g, t) for (g, t) in m.PRM_GEN_AVAIL_TPS
                              if t in getattr(m, "LIGHT_TPS", ()) and (g, t) in m.GEN_TPS],
    )
    m.Prm_Light_Thermal_Limit = Constraint(
        m.PRM_LIGHT_THERMAL_TPS,
        rule=lambda m, g, t: m.DispatchGen[g, t] <= m.GenCapacityInTP[g, t] * m.prm_avail_frac[g, t],
    )

    def gens_in_zone_tp(m, z, t):
        return [g for g in m.GENS_IN_ZONE[z] if (g, t) in m.PRM_GEN_TPS]

    # compact reserve rows (prm_compact_capacity): derate groups of stress hours per zone and period
    def acc_groups(m):
        m._prm_acc_of, m._prm_acc_rep, keys, out = {}, {}, {}, []
        if value(m.prm_compact_capacity) < 1:
            return out
        for t in m.PRM_TPS:
            p = m.tp_period[t]
            for z in m.LOAD_ZONES:
                r = m.prm_region_of_zone[z]
                if r == "" or (r, p) not in m.PRM_REGION_PERIODS:
                    continue
                gens = tuple(g for g in gens_in_zone_tp(m, z, t) if m.prm_credit[g] == "capacity")
                frac = tuple(round(value(m.prm_avail_frac[g, t]), 9) if (g, t) in m.PRM_GEN_AVAIL_TPS else 1.0
                             for g in gens)
                k = keys.setdefault((z, p, gens, frac), sum(1 for kk in keys if kk[:2] == (z, p)))
                m._prm_acc_of[z, t] = k
                if (z, p, k) not in m._prm_acc_rep:
                    m._prm_acc_rep[z, p, k] = (t, gens)
                    out.append((z, p, k))
        return out

    m.PRM_ACC_GROUPS = Set(dimen=3, initialize=acc_groups)
    m.PrmAccCap = Var(m.PRM_ACC_GROUPS, within=Reals)
    m.Prm_Acc_Cap_Def = Constraint(
        m.PRM_ACC_GROUPS,
        rule=lambda m, z, p, k: m.PrmAccCap[z, p, k]
        == sum(m.PrmGenCredit[g, m._prm_acc_rep[z, p, k][0]] for g in m._prm_acc_rep[z, p, k][1]),
    )

    def local_credit(m, z, t):
        if (z, t) in getattr(m, "_prm_acc_of", {}):
            p = m.tp_period[t]
            return m.PrmAccCap[z, p, m._prm_acc_of[z, t]] + sum(
                m.PrmGenCredit[g, t] for g in gens_in_zone_tp(m, z, t) if m.prm_credit[g] != "capacity")
        return sum(m.PrmGenCredit[g, t] for g in gens_in_zone_tp(m, z, t))

    m.PrmLocalCredit = Expression(m.LOAD_ZONES, m.PRM_TPS, rule=local_credit)

    def served_load(m, z, t):
        if hasattr(m, "Distributed_Power_Withdrawals") and hasattr(m, "local_td_loss_rate"):
            return m.prm_td_multiplier[z] * sum(
                getattr(m, c)[z, t] for c in m.Distributed_Power_Withdrawals
            )
        # without local_td: demand plus demand-response shifting (demand_response_investment registers ShiftDemand
        # at the zone's central node then), so DR counts at its dispatch in either case
        return m.zone_demand_mw[z, t] + (m.ShiftDemand[z, t] if hasattr(m, "ShiftDemand") else 0)

    m.PrmServedLoad = Expression(m.LOAD_ZONES, m.PRM_TPS, rule=served_load)

    m.PRM_ZONE_TPS = Set(
        dimen=2,
        initialize=lambda m: [
            (z, t)
            for t in m.PRM_TPS
            for z in m.LOAD_ZONES
            if m.prm_region_of_zone[z] != ""
            and (m.prm_region_of_zone[z], m.tp_period[t]) in m.PRM_REGION_PERIODS
        ],
    )
    m.PrmRequirement = Expression(
        m.PRM_ZONE_TPS,
        rule=lambda m, z, t: (1 + m.prm_margin[m.prm_region_of_zone[z], m.tp_period[t]])
        * m.PrmServedLoad[z, t],
    )
    m.PrmGroupShortfall = Var(m.PRM_ACC_GROUPS, within=NonNegativeReals)
    m.Prm_Group_Shortfall_Link = Constraint(
        m.PRM_ACC_GROUPS, rule=lambda m, z, p, k: m.PrmZoneShortfall[z, p] >= m.PrmGroupShortfall[z, p, k])

    def zone_shortfall(m, z, t):
        if (z, t) in getattr(m, "_prm_acc_of", {}):
            return m.PrmGroupShortfall[z, m.tp_period[t], m._prm_acc_of[z, t]]
        return m.PrmZoneShortfall[z, m.tp_period[t]]

    m.Prm_Zone_Requirement = Constraint(
        m.PRM_ZONE_TPS,
        rule=lambda m, z, t: m.PrmLocalCredit[z, t]
        + m.PrmNetInflow[z, t]
        + zone_shortfall(m, z, t)
        >= m.PrmRequirement[z, t],
    )

    def capped(m, r, t):
        p = m.tp_period[t]
        if (r, p) not in m.PRM_REGION_PERIODS or m.prm_import_share[r, p] == float("inf"):
            return False
        if value(m.prm_import_cap_all_hours) >= 1:
            return True
        # diagnostic variant: only the region's peak-load stress timepoint of the period
        tps = [tt for tt in m.PRM_TPS if m.tp_period[tt] == p]
        load = lambda tt: sum(m.zone_demand_mw[z, tt] for z in m.ZONES_IN_PRM_REGION[r])  # noqa: E731
        return t == max(tps, key=load)

    m.PRM_IMPORT_CAP_TPS = Set(
        dimen=2,
        initialize=lambda m: [(r, t) for r in m.PRM_REGIONS for t in m.PRM_TPS if capped(m, r, t)],
    )
    # import allowance for new interregional transmission (transmission-bill cases, CHANGES §60): the cap rises by
    # prm_import_new_tx_allowance x the new capacity (nameplate MW) on lines crossing the region's boundary, built
    # to date (this stage's BuildTx plus earlier stages' trans_built_to_date_mw)
    def boundary_lines(m, r):
        return [
            tx for tx in m.TRANSMISSION_LINES
            if m.prm_region_of_zone[m.trans_lz1[tx]] != m.prm_region_of_zone[m.trans_lz2[tx]]
            and r in (m.prm_region_of_zone[m.trans_lz1[tx]], m.prm_region_of_zone[m.trans_lz2[tx]])
        ]

    m.PrmNewInterCapacity = Expression(
        m.PRM_REGIONS, m.PERIODS,
        rule=lambda m, r, p: sum(
            sum(m.BuildTx[tx, b] for b in m.PERIODS if b <= p and (tx, b) in m.TRANS_BLD_YRS)
            + m.trans_built_to_date_mw[tx]
            for tx in boundary_lines(m, r)
        ),
    )

    def import_cap_rule(m, r, t):
        p = m.tp_period[t]
        cap = m.prm_import_share[r, p] * m.prm_region_peak_mw[r, p]
        if value(m.prm_import_new_tx_allowance) > 0 and value(m.prm_new_tx_flow_split) >= 1:
            raise ValueError("prm_params.csv: prm_import_new_tx_allowance (retired, §87) can't be combined with "
                             "prm_new_tx_flow_split = 1 (new capacity is credited by flow)")
        if value(m.prm_import_new_tx_allowance) > 0:
            cap = cap + m.prm_import_new_tx_allowance * m.PrmNewInterCapacity[r, p]
        return m.PrmNetImport[r, t] <= cap

    m.Prm_Import_Cap = Constraint(m.PRM_IMPORT_CAP_TPS, rule=import_cap_rule)


def load_inputs(m, switch_data, inputs_dir):
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "prm_zones.csv"),
        optional=True,
        set=m.PRM_ZONES,
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "prm_region_periods.csv"),
        optional=True,
        index=m.PRM_REGION_PERIODS,
        param=(m.prm_margin, m.prm_import_share),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "prm_timeseries.csv"),
        optional=True,
        set=m.PRM_TIMESERIES,
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "prm_gen_credit.csv"),
        optional=True,
        param=(m.prm_credit, m.prm_class),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "prm_gen_availability.csv"),
        optional=True,
        index=m.PRM_GEN_AVAIL_TPS,
        param=(m.prm_avail_frac,),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "trans_built_to_date.csv"),
        optional=True,
        param=(m.trans_built_to_date_mw,),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "prm_params.csv"),
        optional=True,
        param=(m.prm_new_tx_derate, m.prm_shortfall_cost_per_mw_yr, m.prm_import_cap_all_hours,
               m.prm_import_new_tx_allowance, m.prm_compact_capacity, m.prm_new_tx_flow_split),
    )


# ---------------------------------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------------------------------
def _dual(m, con, idx):
    d = getattr(m, "dual", None)
    if d is None:
        return float("nan")
    try:
        return float(d.get(con[idx], float("nan")))
    except (KeyError, TypeError, ValueError):
        return float("nan")


def _region_price(zp, r, p):
    x = zp[(zp.PRM_REGION == r) & (zp.PERIOD == p)] if len(zp) else zp
    if not len(x) or x.peak_served_load_mw.sum() <= 0:
        return {"reserve_price_usd_per_kw_yr": float("nan"), "max_zone_price_usd_per_kw_yr": float("nan"),
                "max_price_zone": ""}
    top = x.loc[x.price_usd_per_kw_yr.idxmax()]
    return {"reserve_price_usd_per_kw_yr": (x.price_usd_per_kw_yr * x.peak_served_load_mw).sum()
            / x.peak_served_load_mw.sum(),
            "max_zone_price_usd_per_kw_yr": top.price_usd_per_kw_yr, "max_price_zone": top.LOAD_ZONE}


def post_solve(m, outputs_dir):
    to_kw_yr = lambda dual, p: dual / value(m.bring_annual_costs_to_base_year[p]) / 1000.0  # noqa: E731
    has_duals = hasattr(m, "dual") and len(m.dual) > 0
    # shortfall
    rows = []
    for (r, p) in m.PRM_REGION_PERIODS:
        rows.append(
            {
                "PRM_REGION": r,
                "PERIOD": p,
                "shortfall_mw": value(m.PrmShortfall[r, p]),
                "cost_per_mw_yr": value(m.prm_shortfall_cost_per_mw_yr),
                "annual_cost": value(m.PrmShortfall[r, p]) * value(m.prm_shortfall_cost_per_mw_yr),
            }
        )
    pd.DataFrame(rows).to_csv(os.path.join(outputs_dir, "prm_shortfall.csv"), index=False)

    # zone hours with duals
    zrows = []
    for (z, t) in m.PRM_ZONE_TPS:
        p = m.tp_period[t]
        dual = _dual(m, m.Prm_Zone_Requirement, (z, t))
        zrows.append(
            {
                "LOAD_ZONE": z,
                "PRM_REGION": m.prm_region_of_zone[z],
                "TIMEPOINT": t,
                "TIMESERIES": m.tp_ts[t],
                "PERIOD": p,
                "served_load_mw": value(m.PrmServedLoad[z, t]),
                "requirement_mw": value(m.PrmRequirement[z, t]),
                "local_credit_mw": value(m.PrmLocalCredit[z, t]),
                "net_inflow_mw": value(m.PrmNetInflow[z, t]),
                "shortfall_mw": value(m.PrmZoneShortfall[z, p]),
                "dual": dual,
                # reserve price: the dual's magnitude (solvers differ in the sign they report for >=)
                "price_usd_per_kw_yr": abs(to_kw_yr(dual, p)),
            }
        )
    zh = pd.DataFrame(zrows)
    zh.to_csv(os.path.join(outputs_dir, "prm_zone_hours.csv"), index=False)

    # reserve price by zone and period, $/kW-yr: the sum over the period's stress hours of the zone requirement's
    # dual. The zone's shortfall slack (MW, one per period) appears in every stress-hour constraint and costs
    # prm_shortfall_cost_per_mw_yr once a year, so this sum never exceeds the penalty, and equals it when the zone
    # is short. (An hourly dual alone is the value of 1 MW more in that hour only.)
    prow = []
    for (z, p) in m.PRM_ZONE_PERIODS:
        x = zh[(zh.LOAD_ZONE == z) & (zh.PERIOD == p)] if len(zh) else zh
        if not len(x):
            continue
        prow.append({"LOAD_ZONE": z, "PRM_REGION": m.prm_region_of_zone[z], "PERIOD": p,
                     "peak_served_load_mw": x.served_load_mw.max(), "shortfall_mw": value(m.PrmZoneShortfall[z, p]),
                     "price_usd_per_kw_yr": to_kw_yr(x.dual.abs().sum(), p)})
    zp = pd.DataFrame(prow, columns=["LOAD_ZONE", "PRM_REGION", "PERIOD", "peak_served_load_mw", "shortfall_mw",
                                     "price_usd_per_kw_yr"])
    zp.to_csv(os.path.join(outputs_dir, "prm_zone_prices.csv"), index=False)

    # region hours: margin achieved and import use
    rrows = []
    for r in m.PRM_REGIONS:
        for t in m.PRM_TPS:
            p = m.tp_period[t]
            if (r, p) not in m.PRM_REGION_PERIODS:
                continue
            zones = m.ZONES_IN_PRM_REGION[r]
            load = sum(value(m.PrmServedLoad[z, t]) for z in zones)
            local = sum(value(m.PrmLocalCredit[z, t]) for z in zones)
            imp = value(m.PrmNetImport[r, t])
            new_imp = value(m.PrmNewImport[r, t])
            share = value(m.prm_import_share[r, p])
            cap = share * value(m.prm_region_peak_mw[r, p]) if share != float("inf") else float("inf")
            idual = (
                _dual(m, m.Prm_Import_Cap, (r, t)) if (r, t) in m.PRM_IMPORT_CAP_TPS else float("nan")
            )
            rrows.append(
                {
                    "PRM_REGION": r,
                    "PERIOD": p,
                    "TIMEPOINT": t,
                    "TIMESERIES": m.tp_ts[t],
                    "served_load_mw": load,
                    "target_margin": value(m.prm_margin[r, p]),
                    "local_credit_mw": local,
                    "net_import_mw": imp,
                    "import_cap_mw": cap,
                    "import_use": imp / cap if cap not in (0, float("inf")) else float("nan"),
                    # §87: net reserve import over new boundary capacity (exempt from the cap; 0 without the split)
                    "new_tx_net_import_mw": new_imp,
                    "margin_achieved": (local + imp + new_imp) / load - 1 if load > 0 else float("nan"),
                    "reserve_dual_sum": zh.loc[(zh.PRM_REGION == r) & (zh.TIMEPOINT == t), "dual"].abs().sum()
                    if len(zh)
                    else float("nan"),
                    "import_cap_dual": idual,
                    "import_cap_price_usd_per_kw_yr": abs(to_kw_yr(idual, p)),
                }
            )
    rh = pd.DataFrame(rrows)
    rh.to_csv(os.path.join(outputs_dir, "prm_region_hours.csv"), index=False)

    # summary by region and period
    srows = []
    for (r, p) in m.PRM_REGION_PERIODS:
        x = rh[(rh.PRM_REGION == r) & (rh.PERIOD == p)] if len(rh) else rh
        if not len(x):
            continue
        worst = x.loc[x.margin_achieved.idxmin()]
        srows.append(
            {
                "PRM_REGION": r,
                "PERIOD": p,
                "target_margin": value(m.prm_margin[r, p]),
                "min_margin_achieved": worst.margin_achieved,
                "at_timepoint": worst.TIMEPOINT,
                "max_net_import_mw": x.net_import_mw.max(),
                "max_new_tx_net_import_mw": x.new_tx_net_import_mw.max(),
                "import_cap_mw": x.import_cap_mw.iloc[0],
                "max_import_use": x.import_use.max(),
                "peak_mw": value(m.prm_region_peak_mw[r, p]),
                "shortfall_mw": value(m.PrmShortfall[r, p]),
                # zone prices (prm_zone_prices.csv) weighted by zone peak load: <= the penalty; the regional
                # sum of hourly duals over all its zones (the old figure) is not a price
                **_region_price(zp, r, p),
                "duals_available": has_duals,
            }
        )
    pd.DataFrame(srows).to_csv(os.path.join(outputs_dir, "prm_summary.csv"), index=False)

    # reserve value of new transmission (v3.1): for every line that can carry new capacity in a period, the value of
    # 1 MW more of it to the reserve requirement, from the duals of the flow limits it relaxes. Lines with no new
    # capacity yet are included (dual_costs.csv drops their rows: dual x bound is 0 there).
    #   split boundary lines: (1 - derate) x derating x sum over stress hours of |dual of Prm_New_Flow_Limit|
    #   other lines: (1 - derate) x derating x sum over stress hours and both directions of |dual of Prm_Flow_Limit|
    # in $/kW-yr (NPV duals / bring_annual_costs_to_base_year / 1000), the units of prm_zone_prices.csv
    vrows = []
    for tx in m.TRANSMISSION_LINES:
        for p in m.PERIODS:
            if not (any((tx, b) in m.TRANS_BLD_YRS for b in m.PERIODS if b <= p)
                    or value(m.trans_built_to_date_mw[tx]) > 0):
                continue
            tps = [t for t in m.PRM_TPS if m.tp_period[t] == p]
            a, b = m.trans_lz1[tx], m.trans_lz2[tx]
            per_mw = (1 - value(m.prm_new_tx_derate)) * value(m.trans_derating_factor[tx])
            if tx in m.PRM_SPLIT_LINES:
                form = "split_new_part"
                duals = [_dual(m, m.Prm_New_Flow_Limit, (tx, t)) for t in tps if (tx, t) in m.PRM_NEW_TX_LINE_TPS]
            else:
                form = "per_direction"
                duals = [_dual(m, m.Prm_Flow_Limit, (zf, zt, t)) for t in tps for (zf, zt) in ((a, b), (b, a))
                         if (zf, zt, t) in m.PRM_TX_TPS]
            if not duals:
                continue
            tot = sum(abs(x) for x in duals)
            new_mw = sum(value(m.BuildTx[tx, bb]) for bb in m.PERIODS if bb <= p and (tx, bb) in m.TRANS_BLD_YRS) \
                + min(value(m.trans_built_to_date_mw[tx]), value(m.existing_trans_cap[tx]))
            vrows.append({"TRANSMISSION_LINE": tx, "trans_lz1": a, "trans_lz2": b,
                          "region1": m.prm_region_of_zone[a], "region2": m.prm_region_of_zone[b], "PERIOD": p,
                          "boundary": m.prm_region_of_zone[a] != m.prm_region_of_zone[b], "formulation": form,
                          "new_capacity_mw_to_date": new_mw,
                          "binding_stress_hours": sum(1 for x in duals if abs(x) > 1e-9),
                          "reserve_value_usd_per_kw_yr": tot * per_mw / value(m.bring_annual_costs_to_base_year[p])
                          / 1000.0 if tot == tot else float("nan")})
    pd.DataFrame(vrows, columns=["TRANSMISSION_LINE", "trans_lz1", "trans_lz2", "region1", "region2", "PERIOD",
                                 "boundary", "formulation", "new_capacity_mw_to_date", "binding_stress_hours",
                                 "reserve_value_usd_per_kw_yr"]).to_csv(
        os.path.join(outputs_dir, "prm_tx_reserve_value.csv"), index=False)

    # implied capacity credit: dual-weighted credited MW / capacity, by class and region
    crows = []
    for (r, p) in m.PRM_REGION_PERIODS:
        tps = [t for t in m.PRM_TPS if m.tp_period[t] == p]
        w = {t: zh.loc[(zh.PRM_REGION == r) & (zh.TIMEPOINT == t), "dual"].abs().sum() if len(zh) else 0.0
             for t in tps}
        wsum = sum(w.values())
        by_class = {}
        for z in m.ZONES_IN_PRM_REGION[r]:
            for g in m.GENS_IN_ZONE[z]:
                if g not in m.GENS_IN_PERIOD[p]:
                    continue
                cap = value(m.GenCapacity[g, p])
                if cap <= 1e-6:
                    continue
                c = by_class.setdefault(m.prm_class[g], {"cap": 0.0, "wcredit": 0.0, "credit": 0.0})
                c["cap"] += cap
                for t in tps:
                    if (g, t) in m.PRM_GEN_TPS:
                        v = value(m.PrmGenCredit[g, t])
                        c["credit"] += v / len(tps)
                        if wsum > 0:
                            c["wcredit"] += w[t] * v / wsum
        # demand response (study_modules.demand_response_investment): deployed MW = DeployDRShare x the largest
        # shift-down limit in the period's stress hours; credit = the load reduction (-ShiftDemand) in each hour
        if hasattr(m, "ShiftDemand"):
            c = {"cap": 0.0, "wcredit": 0.0, "credit": 0.0}
            for z in m.ZONES_IN_PRM_REGION[r]:
                lim = max((value(m.dr_shift_down_limit[z, t]) for t in tps), default=0.0)
                c["cap"] += value(m.DeployDRShare[z, p]) * lim
                for t in tps:
                    v = -value(m.ShiftDemand[z, t]) * value(m.prm_td_multiplier[z])
                    c["credit"] += v / len(tps)
                    if wsum > 0:
                        c["wcredit"] += w[t] * v / wsum
            if c["cap"] > 1e-6:
                by_class["dr"] = c
        for cls, c in by_class.items():
            crows.append(
                {
                    "PRM_REGION": r,
                    "PERIOD": p,
                    "prm_class": cls,
                    "capacity_mw": c["cap"],
                    "implied_capacity_credit": c["wcredit"] / c["cap"] if wsum > 0 else float("nan"),
                    "mean_stress_credit": c["credit"] / c["cap"],
                    "binding_weight": wsum,
                }
            )
    cc = pd.DataFrame(crows)
    bfile = os.path.join(m.options.inputs_dir, "prm_benchmarks.csv")
    if len(cc) and os.path.exists(bfile):
        b = pd.read_csv(bfile)
        b = b.pivot_table(index=["PRM_REGION", "prm_class"], columns="benchmark", values="benchmark_value")
        cc = cc.merge(b.reset_index(), on=["PRM_REGION", "prm_class"], how="left")
    cc.to_csv(os.path.join(outputs_dir, "prm_capacity_credit.csv"), index=False)
