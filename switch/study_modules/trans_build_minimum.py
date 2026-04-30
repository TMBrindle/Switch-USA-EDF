"""
This module enforces minimum new transmission capacity builds by certain periods,
reflecting planned transmission projects.

For each (TRANSMISSION_LINE, PERIOD) entry in trans_build_minimum.csv, the
cumulative new capacity built on that path up to and including that period must
be at least trans_build_minimum_mw MW.  Existing capacity does not count toward
this minimum (the constraint is on new builds only).

Input file (optional):
    trans_build_minimum.csv
        TRANSMISSION_LINE, PERIOD, trans_build_minimum_mw
"""

import os
from pyomo.environ import Constraint, NonNegativeReals, Param


def define_components(m):
    m.trans_build_minimum_mw = Param(
        m.TRANSMISSION_LINES, m.PERIODS, within=NonNegativeReals, default=0
    )

    m.Enforce_Trans_Build_Minimum = Constraint(
        m.TRANSMISSION_LINES,
        m.PERIODS,
        rule=lambda m, tx, p: (
            Constraint.Skip
            if m.trans_build_minimum_mw[tx, p] == 0
            else (
                (m.TxCapacityNameplate[tx, p] - m.existing_trans_cap[tx])
                >= m.trans_build_minimum_mw[tx, p]
            )
        ),
    )


def load_inputs(m, switch_data, inputs_dir):
    """
    Import minimum new transmission build requirements.

    trans_build_minimum.csv
        TRANSMISSION_LINE, PERIOD, trans_build_minimum_mw
    """
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "trans_build_minimum.csv"),
        optional=True,
        param=(m.trans_build_minimum_mw,),
    )
