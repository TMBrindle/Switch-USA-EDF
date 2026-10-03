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

Input (inputs_dir), optional (the module does nothing without it):
    retirement_rules.csv   gen_energy_source, rr_no_retirement_before
Output: retirement_rules_check.csv (MW suspended by source and period, for review).
"""
import os

from pyomo.environ import Any, Constraint, Param, Set, value

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


def load_inputs(m, switch_data, inputs_dir):
    switch_data.load_aug(filename=os.path.join(inputs_dir, "retirement_rules.csv"), optional=True,
                         index=m.RR_SOURCES, param=(m.rr_no_retirement_before,))


def post_solve(m, outdir):
    import pandas as pd

    if not len(m.RR_SOURCES):
        return
    rows = {}
    for (g, b, p) in m.GEN_BLD_SUSPEND_YRS:
        s = m.gen_energy_source[g]
        if s in m.RR_SOURCES:
            rows[(s, p)] = rows.get((s, p), 0.0) + value(m.SuspendGen[g, b, p])
    pd.DataFrame([{"gen_energy_source": s, "period": p, "suspended_mw": v,
                   "rule_year": int(value(m.rr_no_retirement_before[s])),
                   "blocked": p < int(value(m.rr_no_retirement_before[s]))} for (s, p), v in sorted(rows.items())]
                 ).to_csv(os.path.join(outdir, "retirement_rules_check.csv"), index=False)
