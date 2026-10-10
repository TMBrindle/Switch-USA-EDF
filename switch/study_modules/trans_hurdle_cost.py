"""
This module adds a per-MWh hurdle cost for transmission flows across hurdle
region boundaries. These costs represent market friction, regulatory charges,
or policy penalties for inter-regional energy transfers.

Hurdle costs apply in both directions on each affected line: a flow from A to B
and a flow from B to A both incur the same per-MWh cost if A and B belong to
different hurdle regions.

Input file (optional):
    trans_hurdle_cost.csv
        TRANSMISSION_LINE, PERIOD, trans_hurdle_cost_per_mwh

Directional import cost (optional; S0 CA-WA carbon, CHANGES §65): a per-MWh charge on power DELIVERED in one direction
only (DispatchTx x trans_efficiency), e.g. the carbon cost of unspecified imports into California / Washington from
outside the linked market. It is added to the hurdle above, not instead of it.
    trans_import_cost.csv
        trans_lz_from, trans_lz_to, PERIOD, trans_import_cost_per_mwh

Two-tranche import charge (optional; S0 ca_wa_carbon.import_charge: two_tranche, CHANGES §92): per period, delivered
imports over the listed directions (annual MWh: timepoint weights) up to a free tranche pay nothing, and every MWh
above it pays one rate:
    TrancheExcessImports[p] >= sum_t w_t x sum_dirs DispatchTx x trans_efficiency - tranche_free_mwh_per_yr[p]
    TrancheExcessImports[p] >= 0;   cost[p] = tranche_cost_per_mwh[p] x TrancheExcessImports[p]
Linear and convex (the marginal import pays the full rate once imports exceed the tranche). A separate cost component
(TxImportTrancheCost; 0 and one more row in costs_itemized.csv for cases without the files, whose model is otherwise
unchanged).
    trans_import_tranche.csv        PERIOD, tranche_free_mwh_per_yr, tranche_cost_per_mwh
    trans_import_tranche_dirs.csv   trans_lz_from, trans_lz_to
    trans_import_tranche_info.csv   (optional; any columns: mode, tranche size, source) copied to the outputs
"""

import os
from pyomo.environ import Constraint, Expression, NonNegativeReals, Param, Set, Var


dependencies = (
    "switch_model.timescales",
    "switch_model.financials",
    "switch_model.transmission.transport.build",
    "switch_model.transmission.transport.dispatch",
)


def define_components(m):
    m.trans_hurdle_cost_per_mwh = Param(
        m.TRANSMISSION_LINES, m.PERIODS, within=NonNegativeReals, default=0
    )

    # directional import cost on delivered MWh (trans_import_cost.csv; empty for every case without it)
    m.TX_IMPORT_COST_DIRS = Set(dimen=3, within=m.DIRECTIONAL_TX * m.PERIODS)
    m.trans_import_cost_per_mwh = Param(m.TX_IMPORT_COST_DIRS, within=NonNegativeReals)

    def import_cost(m, tp):
        p = m.tp_period[tp]
        if tp in getattr(m, "LIGHT_TPS", ()):                 # light stress timepoints: no per-timepoint cost terms (CHANGES §69)
            return 0.0
        return sum(
            m.DispatchTx[a, b, tp] * m.trans_efficiency[m.trans_d_line[a, b]] * m.trans_import_cost_per_mwh[a, b, pp]
            for (a, b, pp) in m.TX_IMPORT_COST_DIRS
            if pp == p
        )

    m.TxImportCostPerTP = Expression(m.TIMEPOINTS, rule=import_cost)

    def TxHurdleCostPerTP_rule(m, tp):
        p = m.tp_period[tp]
        if tp in getattr(m, "LIGHT_TPS", ()):
            return 0.0
        return sum(
            (
                m.DispatchTx[m.trans_lz1[tx], m.trans_lz2[tx], tp]
                + m.DispatchTx[m.trans_lz2[tx], m.trans_lz1[tx], tp]
            )
            * m.trans_hurdle_cost_per_mwh[tx, p]
            for tx in m.TRANSMISSION_LINES
            if m.trans_hurdle_cost_per_mwh[tx, p] > 0
        ) + m.TxImportCostPerTP[tp]

    # one cost component (hurdles + import cost), so cases without trans_import_cost.csv report exactly as before
    m.TxHurdleCostPerTP = Expression(m.TIMEPOINTS, rule=TxHurdleCostPerTP_rule)
    m.Cost_Components_Per_TP.append("TxHurdleCostPerTP")

    # two-tranche import charge (trans_import_tranche*.csv; empty for every case without them)
    m.TRANCHE_PERIODS = Set(dimen=1, within=m.PERIODS)
    m.tranche_free_mwh_per_yr = Param(m.TRANCHE_PERIODS, within=NonNegativeReals)
    m.tranche_cost_per_mwh = Param(m.TRANCHE_PERIODS, within=NonNegativeReals)
    m.TRANCHE_DIRS = Set(dimen=2, within=m.DIRECTIONAL_TX)

    def tranche_imports(m, p):
        return sum(
            m.DispatchTx[a, b, t] * m.trans_efficiency[m.trans_d_line[a, b]] * m.tp_weight_in_year[t]
            for t in m.TPS_IN_PERIOD[p]
            if t not in getattr(m, "LIGHT_TPS", ())
            for (a, b) in m.TRANCHE_DIRS
        )

    m.TrancheImports = Expression(m.TRANCHE_PERIODS, rule=tranche_imports)
    m.TrancheExcessImports = Var(m.TRANCHE_PERIODS, within=NonNegativeReals)
    m.Tranche_Excess_Def = Constraint(
        m.TRANCHE_PERIODS,
        rule=lambda m, p: m.TrancheExcessImports[p] >= m.TrancheImports[p] - m.tranche_free_mwh_per_yr[p],
    )
    m.TxImportTrancheCost = Expression(
        m.PERIODS,
        rule=lambda m, p: m.tranche_cost_per_mwh[p] * m.TrancheExcessImports[p] if p in m.TRANCHE_PERIODS else 0.0,
    )
    m.Cost_Components_Per_Period.append("TxImportTrancheCost")


def load_inputs(m, switch_data, inputs_dir):
    """
    Import per-MWh hurdle costs for cross-region transmission flows.

    trans_hurdle_cost.csv
        TRANSMISSION_LINE, PERIOD, trans_hurdle_cost_per_mwh
    """
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "trans_hurdle_cost.csv"),
        optional=True,
        param=(m.trans_hurdle_cost_per_mwh,),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "trans_import_cost.csv"),
        optional=True,
        index=m.TX_IMPORT_COST_DIRS,
        param=(m.trans_import_cost_per_mwh,),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "trans_import_tranche.csv"),
        optional=True,
        index=m.TRANCHE_PERIODS,
        param=(m.tranche_free_mwh_per_yr, m.tranche_cost_per_mwh),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "trans_import_tranche_dirs.csv"),
        optional=True,
        set=m.TRANCHE_DIRS,
    )


def post_solve(m, outdir):
    """trans_import_cost.csv in, trans_import_cost_results.csv out: delivered MWh and cost by direction and period
    (annual: timepoint weights); nothing when there is no import cost. Two-tranche charge: trans_import_tranche_results.csv
    (period: delivered, free, excess MWh/yr, rate, annual cost) and the info file copied."""
    import shutil

    import pandas as pd
    from pyomo.environ import value
    if len(m.TRANCHE_PERIODS):
        pd.DataFrame([{"PERIOD": p, "delivered_mwh_per_yr": value(m.TrancheImports[p]),
                       "free_mwh_per_yr": value(m.tranche_free_mwh_per_yr[p]),
                       "excess_mwh_per_yr": value(m.TrancheExcessImports[p]),
                       "cost_per_mwh": value(m.tranche_cost_per_mwh[p]),
                       "annual_cost": value(m.TxImportTrancheCost[p])} for p in m.TRANCHE_PERIODS]).to_csv(
            os.path.join(outdir, "trans_import_tranche_results.csv"), index=False)
        info = os.path.join(m.options.inputs_dir, "trans_import_tranche_info.csv")
        if os.path.exists(info):
            shutil.copy(info, os.path.join(outdir, "trans_import_tranche_info.csv"))
    if not len(m.TX_IMPORT_COST_DIRS):
        return
    rows = []
    for (a, b, p) in m.TX_IMPORT_COST_DIRS:
        mwh = sum(value(m.DispatchTx[a, b, t]) * value(m.trans_efficiency[m.trans_d_line[a, b]])
                  * value(m.tp_weight_in_year[t]) for t in m.TPS_IN_PERIOD[p])
        rows.append({"trans_lz_from": a, "trans_lz_to": b, "PERIOD": p, "delivered_mwh_per_yr": mwh,
                     "cost_per_mwh": value(m.trans_import_cost_per_mwh[a, b, p]),
                     "annual_cost": mwh * value(m.trans_import_cost_per_mwh[a, b, p])})
    pd.DataFrame(rows).to_csv(os.path.join(outdir, "trans_import_cost_results.csv"), index=False)
