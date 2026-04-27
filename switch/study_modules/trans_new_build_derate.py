"""
This module applies a separate derate factor to NEW transmission capacity built
between planning subregions (transgrp boundaries). This reflects the additional
reliability uncertainty of new inter-subregion lines relative to existing
infrastructure.

The effective available capacity for dispatch on each line becomes:

    existing_trans_cap[tx] * trans_derating_factor[tx]
    + sum(BuildTx[tx, bld_yr]) * trans_new_build_derate_factor[tx]

For cross-subregion lines, trans_new_build_derate_factor is set to 0.85
(15% derate on new builds). For intra-subregion lines, the default is 1.0
and the behavior is identical to the standard TxCapacityNameplateAvailable.

This module deactivates the core Maximum_DispatchTx constraint from
switch_model.transmission.transport.dispatch and replaces it with
Maximum_DispatchTx_Derated, which uses the expression above. It must
therefore be loaded AFTER switch_model.transmission.transport.dispatch
in modules.txt.

Input file (optional):
    trans_new_build_derate.csv
        TRANSMISSION_LINE, trans_new_build_derate_factor
"""

import os
from pyomo.environ import BuildAction, Constraint, Expression, NonNegativeReals, Param


dependencies = (
    "switch_model.timescales",
    "switch_model.transmission.transport.build",
    "switch_model.transmission.transport.dispatch",
)


def define_components(m):
    m.trans_new_build_derate_factor = Param(
        m.TRANSMISSION_LINES, within=NonNegativeReals, default=1.0
    )

    # Available capacity with separate derating for existing and new capacity
    m.TxCapacityNewBuildDerated = Expression(
        m.TRANSMISSION_LINES,
        m.PERIODS,
        rule=lambda m, tx, p: (
            m.existing_trans_cap[tx] * m.trans_derating_factor[tx]
            + sum(
                m.BuildTx[tx, bld_yr]
                for bld_yr in m.PERIODS
                if bld_yr <= p and (tx, bld_yr) in m.TRANS_BLD_YRS
            )
            * m.trans_new_build_derate_factor[tx]
        ),
    )

    # Deactivate the core symmetric dispatch limit so it is not double-applied
    m.Deactivate_Core_Dispatch_Limit = BuildAction(
        rule=lambda m: m.Maximum_DispatchTx.deactivate()
    )

    # Replacement dispatch limit using the split derate expression
    m.Maximum_DispatchTx_Derated = Constraint(
        m.TRANS_TIMEPOINTS,
        rule=lambda m, zone_from, zone_to, tp: (
            m.DispatchTx[zone_from, zone_to, tp]
            <= m.TxCapacityNewBuildDerated[
                m.trans_d_line[zone_from, zone_to], m.tp_period[tp]
            ]
        ),
    )


def load_inputs(m, switch_data, inputs_dir):
    """
    Import per-line new-build derate factors.

    trans_new_build_derate.csv
        TRANSMISSION_LINE, trans_new_build_derate_factor
    """
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "trans_new_build_derate.csv"),
        optional=True,
        param=(m.trans_new_build_derate_factor,),
    )
