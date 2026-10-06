import os
from pyomo.environ import Any, Param, Constraint, Set, PercentFraction


def define_components(m):
    # For generators with `gen_max_annual_availability` set, constrain output
    # to this fraction times the availability they would otherwise have (e.g.,
    # to limit share of loads served by data center backup plants)
    m.gen_max_annual_availability = Param(
        m.GENERATION_PROJECTS, within=PercentFraction, default=1
    )

    # force non-operation of completely unavailable projects, even on 0-weight
    # (PRM) days (also shrinks Respect_Annual_Availability_Limit a bit)
    m.UNAVAILABLE_GENS = Set(
        dimen=1,
        within=m.GENERATION_PROJECTS,
        initialize=lambda m: [
            g for g in m.GENERATION_PROJECTS if m.gen_max_annual_availability[g] == 0
        ],
    )
    m.UNAVAILABLE_GEN_TPS = Set(
        dimen=2,
        within=m.GEN_TPS,
        initialize=lambda m: [
            (g, tp) for g in m.UNAVAILABLE_GENS for tp in m.TPS_FOR_GEN[g]
        ],
    )
    m.Force_Off_Unavailable_Gens = Constraint(
        m.UNAVAILABLE_GEN_TPS, rule=lambda m, g, tp: m.DispatchGen[g, tp] == 0
    )

    # optional per-period value (gen_max_annual_availability_by_period.csv), e.g. a zonal coal cap that
    # differs by period in a multi-period case; defaults to the generator's single value
    m.gen_max_annual_availability_by_period = Param(
        m.GEN_PERIODS, within=Any, default=None
    )

    def rule(m, g, p):
        # no constraint needed if completely available or completely unavailable
        limit_frac = m.gen_max_annual_availability_by_period[g, p]
        if limit_frac is None:
            limit_frac = m.gen_max_annual_availability[g]
        elif not 0 <= limit_frac <= 1:
            raise ValueError(f"gen_max_annual_availability_by_period[{g}, {p}] = {limit_frac} is outside [0, 1]")
        if limit_frac == 1 or g in m.UNAVAILABLE_GENS:
            return Constraint.Skip

        if hasattr(m, "CommitUpperLimit"):
            # with unit commitment; find the limit if it were committed but don't
            # require it to be committed to get credit
            if m.gen_is_variable[g]:
                limit = (
                    lambda g, t: m.CommitUpperLimit[g, t]
                    * m.gen_max_capacity_factor[g, t]
                )
            else:
                limit = lambda g, t: m.CommitUpperLimit[g, t]
        else:
            # no unit commitment
            limit = lambda g, t: m.DispatchUpperLimit[g, t]

        # operating timepoints only: light stress timepoints (CHANGES §69, zero weight) have no commitment, and
        # zero weight adds nothing to either sum
        light = getattr(m, "LIGHT_TPS", ())
        tps = [t for t in m.TPS_IN_PERIOD[p] if t not in light]
        max_production = sum(limit(g, t) * m.tp_weight[t] for t in tps)
        actual_production = sum(m.DispatchGen[g, t] * m.tp_weight[t] for t in tps)
        return actual_production <= limit_frac * max_production

    m.Respect_Annual_Availability_Limit = Constraint(m.GEN_PERIODS, rule=rule)


def load_inputs(m, switch_data, inputs_dir):
    """
    Files or columns marked with * are optional. See notes above on default
    values.

        gen_info.csv
        ..., gen_max_annual_availability*

        gen_max_annual_availability_by_period.csv*
        GENERATION_PROJECT, PERIOD, gen_max_annual_availability_by_period
    """
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "gen_info.csv"),
        param=(m.gen_max_annual_availability),
    )
    # optional: GENERATION_PROJECT, PERIOD, gen_max_annual_availability_by_period
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "gen_max_annual_availability_by_period.csv"),
        optional=True,
        param=(m.gen_max_annual_availability_by_period,),
    )
