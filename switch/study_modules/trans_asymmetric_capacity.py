"""
This module enforces directional (asymmetric) capacity limits on transmission
flows. It adds a supplementary constraint on DispatchTx for each direction of
each transmission line that has an entry in trans_directional_limits.csv.

The constraint is additive on top of Maximum_DispatchTx from
switch_model.transmission.transport.dispatch — the tighter of the two limits
applies. For lines not in the input file, this module has no effect.

Directional limits represent the existing infrastructure limits from NARIS2024
AC and nonAC capacity data. New builds (BuildTx) add to the symmetric nameplate
capacity tracked by Switch's core model; the directional constraint here applies
only to the total dispatch, so new capacity may relax the directional limit if
it is built on an asymmetric corridor.

Input file (optional):
    trans_directional_limits.csv
        zone_from, zone_to, PERIOD, trans_directional_cap_mw
"""

import os
from pyomo.environ import Constraint, NonNegativeReals, Param


dependencies = (
    "switch_model.timescales",
    "switch_model.balancing.load_zones",
    "switch_model.transmission.transport.build",
    "switch_model.transmission.transport.dispatch",
)


def define_components(m):
    m.trans_directional_cap_mw = Param(
        m.LOAD_ZONES,
        m.LOAD_ZONES,
        m.PERIODS,
        within=NonNegativeReals,
        default=float("inf"),
    )

    m.Maximum_DispatchTx_Directional = Constraint(
        m.TRANS_TIMEPOINTS,
        rule=lambda m, zone_from, zone_to, tp: (
            Constraint.Skip
            if m.trans_directional_cap_mw[zone_from, zone_to, m.tp_period[tp]]
            == float("inf")
            else (
                m.DispatchTx[zone_from, zone_to, tp]
                <= m.trans_directional_cap_mw[zone_from, zone_to, m.tp_period[tp]]
            )
        ),
    )


def load_inputs(m, switch_data, inputs_dir):
    """
    Import directional capacity limits for asymmetric transmission lines.

    trans_directional_limits.csv
        zone_from, zone_to, PERIOD, trans_directional_cap_mw
    """
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "trans_directional_limits.csv"),
        optional=True,
        param=(m.trans_directional_cap_mw,),
    )
