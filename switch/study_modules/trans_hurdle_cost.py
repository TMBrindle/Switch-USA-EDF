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
"""

import os
from pyomo.environ import Constraint, Expression, NonNegativeReals, Param, Set


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
        return sum(
            m.DispatchTx[a, b, tp] * m.trans_efficiency[m.trans_d_line[a, b]] * m.trans_import_cost_per_mwh[a, b, pp]
            for (a, b, pp) in m.TX_IMPORT_COST_DIRS
            if pp == p
        )

    m.TxImportCostPerTP = Expression(m.TIMEPOINTS, rule=import_cost)

    def TxHurdleCostPerTP_rule(m, tp):
        p = m.tp_period[tp]
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


def post_solve(m, outdir):
    """trans_import_cost.csv in, trans_import_cost_results.csv out: delivered MWh and cost by direction and period
    (annual: timepoint weights); nothing when there is no import cost."""
    if not len(m.TX_IMPORT_COST_DIRS):
        return
    import pandas as pd
    from pyomo.environ import value
    rows = []
    for (a, b, p) in m.TX_IMPORT_COST_DIRS:
        mwh = sum(value(m.DispatchTx[a, b, t]) * value(m.trans_efficiency[m.trans_d_line[a, b]])
                  * value(m.tp_weight_in_year[t]) for t in m.TPS_IN_PERIOD[p])
        rows.append({"trans_lz_from": a, "trans_lz_to": b, "PERIOD": p, "delivered_mwh_per_yr": mwh,
                     "cost_per_mwh": value(m.trans_import_cost_per_mwh[a, b, p]),
                     "annual_cost": mwh * value(m.trans_import_cost_per_mwh[a, b, p])})
    pd.DataFrame(rows).to_csv(os.path.join(outdir, "trans_import_cost_results.csv"), index=False)
