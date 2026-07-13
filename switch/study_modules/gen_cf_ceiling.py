"""
Constrains the annual average capacity factor of selected generators.

Add a gen_annual_cf_ceiling column to gen_info.csv for any generator that should
have a ceiling on its annual capacity factor. SWITCH will ensure those generators
produce no more than ceiling × installed_capacity × hours_per_year of energy in
each period.

This is a ceiling, not a fixed target, so the optimizer can still ramp a plant
down below the ceiling (e.g., ahead of retirement). Generators without a value
set (or with a value >= 1.0) face no constraint.

pg_to_switch.py / conversion_functions.py populates this column automatically:
  - Existing fossil/nuclear: set to the PowerGenome regional historical average CF
  - New gas builds: set to 0.80
  - All other generators: left blank (no ceiling)
"""
import os

from pyomo.environ import Constraint, NonNegativeReals, Param


def define_components(m):
    # Per-generator ceiling on annual capacity factor.  Default 1.0 = no constraint.
    m.gen_annual_cf_ceiling = Param(
        m.GENERATION_PROJECTS, within=NonNegativeReals, default=1.0
    )

    def rule(m, g, p):
        if m.gen_annual_cf_ceiling[g] >= 1.0:
            return Constraint.Skip
        total_hours = sum(m.tp_weight[t] for t in m.TPS_IN_PERIOD[p])
        actual_mwh = sum(
            m.DispatchGen[g, t] * m.tp_weight[t] for t in m.TPS_IN_PERIOD[p]
        )
        return actual_mwh <= m.gen_annual_cf_ceiling[g] * m.GenCapacity[g, p] * total_hours

    m.Enforce_Annual_CF_Ceiling = Constraint(m.GEN_PERIODS, rule=rule)


def load_inputs(m, switch_data, inputs_dir):
    """
    Optional column in gen_info.csv:
        gen_annual_cf_ceiling   fraction in [0, 1); blank or missing = no ceiling
    """
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "gen_info.csv"),
        optional=True,
        param=(m.gen_annual_cf_ceiling,),
    )
