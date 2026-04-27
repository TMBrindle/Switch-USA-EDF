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
"""

import os
from pyomo.environ import Constraint, Expression, NonNegativeReals, Param


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
        )

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
