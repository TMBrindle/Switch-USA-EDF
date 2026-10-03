"""
Per-period rule on new builds (S0 production; see s0_workflow/production.py, write_build_rules).

Forbids NEW capacity of the listed energy sources in periods before a given year:

    BuildGen[g, p] = 0   for (g, p) in NEW_GEN_BLD_YRS with gen_energy_source in the rule and
                         p < br_no_new_build_before

Used for nuclear in the S0 new-defaults cases: no new nuclear before the 2035 stage (periods 2028 and
2030 blocked, 2035 open). This is separate from the nuclear growth cap (MaxCapTag_NuclearGrowth), which
counts existing nuclear too and so can leave room for new units in 2028/2030 if existing plants retire.
Existing and planned (predetermined) units are not affected. Periods are compared by their label
(INVESTMENT_PERIOD, the last year of the period by this repo's convention), as in retirement_rules.

Input (inputs_dir), optional (the module does nothing without it):
    build_rules.csv   gen_energy_source, br_no_new_build_before
Output: build_rules_check.csv (new MW by source and period, for review).
"""
import os

from pyomo.environ import Any, Constraint, Param, Set, value

dependencies = ("switch_model.timescales", "switch_model.generators.core.build")


def define_components(m):
    m.BR_SOURCES = Set(dimen=1, within=Any)
    m.br_no_new_build_before = Param(m.BR_SOURCES, within=Any)

    def blocked(m, g, p):
        s = m.gen_energy_source[g]
        return s in m.BR_SOURCES and p < int(value(m.br_no_new_build_before[s]))

    m.BR_BLOCKED = Set(dimen=2, within=m.NEW_GEN_BLD_YRS, initialize=lambda m: [
        (g, p) for (g, p) in m.NEW_GEN_BLD_YRS if blocked(m, g, p)])
    m.BR_No_New_Build = Constraint(m.BR_BLOCKED, rule=lambda m, g, p: m.BuildGen[g, p] == 0)


def load_inputs(m, switch_data, inputs_dir):
    switch_data.load_aug(filename=os.path.join(inputs_dir, "build_rules.csv"), optional=True,
                         index=m.BR_SOURCES, param=(m.br_no_new_build_before,))


def post_solve(m, outdir):
    import pandas as pd

    if not len(m.BR_SOURCES):
        return
    rows = {}
    for (g, p) in m.NEW_GEN_BLD_YRS:
        s = m.gen_energy_source[g]
        if s in m.BR_SOURCES:
            rows[(s, p)] = rows.get((s, p), 0.0) + value(m.BuildGen[g, p])
    pd.DataFrame([{"gen_energy_source": s, "period": p, "new_mw": v,
                   "rule_year": int(value(m.br_no_new_build_before[s])),
                   "blocked": p < int(value(m.br_no_new_build_before[s]))} for (s, p), v in sorted(rows.items())],
                 columns=["gen_energy_source", "period", "new_mw", "rule_year", "blocked"]
                 ).to_csv(os.path.join(outdir, "build_rules_check.csv"), index=False)
