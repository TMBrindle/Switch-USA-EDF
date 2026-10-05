"""
Per-period economic-retirement rule (S0 production; see s0_workflow/production.py).

Replaces the single gen_info flag (gen_can_retire_early, set from PowerGenome's Can_Retire tag) as the
way to block retirement before a given year. The flag is one value per generator for the whole case,
so a multi-period case or window starting before 2030 had coal and gas locked in for every period
(the "coal lock"): Can_Retire = 0 for the 2028 settings carried into 2030 and later. With this module
the case sets gen_can_retire_early = 1 for the covered generators and this constraint forbids
suspension/retirement only in periods before the rule's year:

    SuspendGen[g, build_year, p] = 0   for g with gen_energy_source in the rule and
                                       INVESTMENT_PERIOD p < rr_no_retirement_before

Periods are compared by their label (INVESTMENT_PERIOD, the last year of the period by this repo's
convention), so with the production spans 2028 (2026-28) is blocked and 2030 (2029-30) is open,
the same split as the retirement_policy axis's blocked_2030_* settings (Can_Retire 0 for model years
before 2030, 1 from 2030). Retirement in Switch is permanent once taken (gen_can_suspend = 0), so a
generator kept through 2028 can still retire in 2030 or later.

Retirement friction (ReEDS-style; S0 default 0.5, CHANGES §66): retiring an existing unit avoids only (1 - f)
of its fixed O&M. For existing (predetermined) capacity of a covered source, in periods from rf_from_period on,
the objective is charged

    RetirementFrictionCost[p] = sum f[s] x SuspendGen[g, b, p] x (gen_fixed_om[g, b] + gen_fixed_om_by_period[g, p])

(gen_fixed_om_by_period when study_modules.gen_om_by_period is loaded), so a unit retires economically only when
it recovers less than (1 - f) of its fixed costs. Fixed O&M includes PowerGenome's age-based capital additions for
existing units (no separate capex is charged on existing capacity). Retirements that are not SuspendGen (planned
dates, the lifetime backstop: build-year encoding) carry no friction; before rr_no_retirement_before there is no
economic retirement to charge.

Input (inputs_dir), optional (the module does nothing without them):
    retirement_rules.csv     gen_energy_source, rr_no_retirement_before
    retirement_friction.csv  gen_energy_source, rf_fraction, rf_from_period
Output: retirement_rules_check.csv (MW suspended by source and period, the friction charged, for review).
"""
import os

from pyomo.environ import Any, Constraint, Expression, NonNegativeReals, Param, Set, value

dependencies = ("switch_model.timescales", "switch_model.generators.core.build")


def define_components(m):
    m.RR_SOURCES = Set(dimen=1, within=Any)
    m.rr_no_retirement_before = Param(m.RR_SOURCES, within=Any)

    def blocked(m, g, b, p):
        s = m.gen_energy_source[g]
        return s in m.RR_SOURCES and p < int(value(m.rr_no_retirement_before[s]))

    m.RR_BLOCKED = Set(dimen=3, within=m.GEN_BLD_SUSPEND_YRS, initialize=lambda m: [
        (g, b, p) for (g, b, p) in m.GEN_BLD_SUSPEND_YRS if blocked(m, g, b, p)])
    m.RR_No_Early_Retirement = Constraint(m.RR_BLOCKED, rule=lambda m, g, b, p: m.SuspendGen[g, b, p] == 0)

    # retirement friction
    m.RF_SOURCES = Set(dimen=1, within=Any)
    m.rf_fraction = Param(m.RF_SOURCES, within=NonNegativeReals,
                          validate=lambda m, v, s: v <= 1)
    m.rf_from_period = Param(m.RF_SOURCES, within=Any)

    def rf_covered(m, g, b, p):
        s = m.gen_energy_source[g]
        return (s in m.RF_SOURCES and (g, b) in m.PREDETERMINED_GEN_BLD_YRS
                and p >= int(value(m.rf_from_period[s])) and value(m.rf_fraction[s]) > 0)

    m.RF_GEN_BLD_SUSPEND_YRS = Set(dimen=3, within=m.GEN_BLD_SUSPEND_YRS, initialize=lambda m: [
        (g, b, p) for (g, b, p) in m.GEN_BLD_SUSPEND_YRS if rf_covered(m, g, b, p)])

    def fom(m, g, b, p):
        by_p = m.gen_fixed_om_by_period[g, p] if hasattr(m, "gen_fixed_om_by_period") else 0.0
        return value(m.gen_fixed_om[g, b]) + value(by_p)

    m.RetirementFrictionCost = Expression(m.PERIODS, rule=lambda m, p: sum(
        m.rf_fraction[m.gen_energy_source[g]] * fom(m, g, b, pp) * m.SuspendGen[g, b, pp]
        for (g, b, pp) in m.RF_GEN_BLD_SUSPEND_YRS if pp == p))
    m.Cost_Components_Per_Period.append("RetirementFrictionCost")


def load_inputs(m, switch_data, inputs_dir):
    switch_data.load_aug(filename=os.path.join(inputs_dir, "retirement_rules.csv"), optional=True,
                         index=m.RR_SOURCES, param=(m.rr_no_retirement_before,))
    switch_data.load_aug(filename=os.path.join(inputs_dir, "retirement_friction.csv"), optional=True,
                         index=m.RF_SOURCES, param=(m.rf_fraction, m.rf_from_period))


def post_solve(m, outdir):
    import pandas as pd

    if not len(m.RR_SOURCES) and not len(m.RF_SOURCES):
        return
    rows, fric = {}, {}
    for (g, b, p) in m.GEN_BLD_SUSPEND_YRS:
        s = m.gen_energy_source[g]
        if s in m.RR_SOURCES or s in m.RF_SOURCES:
            rows[(s, p)] = rows.get((s, p), 0.0) + value(m.SuspendGen[g, b, p])
    for (g, b, p) in m.RF_GEN_BLD_SUSPEND_YRS:
        s = m.gen_energy_source[g]
        by_p = m.gen_fixed_om_by_period[g, p] if hasattr(m, "gen_fixed_om_by_period") else 0.0
        f = value(m.rf_fraction[s]) * (value(m.gen_fixed_om[g, b]) + value(by_p)) * value(m.SuspendGen[g, b, p])
        fric[(s, p)] = fric.get((s, p), 0.0) + f

    def rule_year(s):
        return int(value(m.rr_no_retirement_before[s])) if s in m.RR_SOURCES else float("nan")

    out = pd.DataFrame([{"gen_energy_source": s, "period": p, "suspended_mw": v,
                         "rule_year": rule_year(s),
                         "blocked": s in m.RR_SOURCES and p < rule_year(s),
                         "friction_fraction": value(m.rf_fraction[s]) if s in m.RF_SOURCES else 0.0,
                         "friction_cost_per_yr": fric.get((s, p), 0.0)} for (s, p), v in sorted(rows.items())])
    if not len(m.RF_SOURCES):                     # as before the friction (no retirement_friction.csv)
        out = out.drop(columns=["friction_fraction", "friction_cost_per_yr"], errors="ignore")
    out.to_csv(os.path.join(outdir, "retirement_rules_check.csv"), index=False)
