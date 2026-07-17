"""
Federal tax credit (PTC-style) revenue for qualifying generation.

Represents the per-MWh "production tax credit" family of federal incentives
-- 45Y/48E (technology-neutral clean electricity PTC/ITC, covers wind/solar/
geothermal/etc.), 45U (existing nuclear PTC), 45Q (carbon capture, translated
to an effective $/MWh credit for the capturing generator), and 45V (clean
hydrogen, translated to an effective $/MWh-equivalent credit where hydrogen
technologies are represented as generators) -- as a per-MWh dispatch credit,
rather than folding it into upfront CapEx the way the pre-existing blanket
30% `atb_modifiers` ITC treatment does for batteries/geothermal.

This mirrors the "credit the generation, not just the investment" approach
used for PTC-style credits in switch-model/Switch-USA-PG-style builds: a PTC
is earned per MWh actually generated (and can phase out/step down or expire
after a fixed number of years in service), so it belongs alongside variable
O&M as a per-timepoint dispatch cost adjustment, not as a capex multiplier.

IMPORTANT -- kept deliberately separate from opex reporting:
The credit is NOT netted into `gen_variable_om_by_period` /
`Gen_Variable_OM_by_Period` (see gen_om_by_period.py). If it were, opex
outputs would silently shrink (or go negative) for PTC-eligible techs and a
reviewer would have no clean line item to see how much credit money is
actually flowing to each technology/scenario. Instead:

  - `TaxCreditEarned[tp]` reports the POSITIVE dollar value of credits earned
    in each timepoint (gross, informational).
  - `Gen_Tax_Credit_Value_by_Period[tp]` is the same value NEGATED and
    registered as its own named entry in `Cost_Components_Per_TP`, separate
    from `Gen_Variable_OM_by_Period`, so it still correctly reduces total
    system cost in the objective (so the optimizer responds to the credit),
    while standard per-component cost reports (e.g. costs_by_tp /
    costs_by_period style outputs that iterate `Cost_Components_Per_*`) show
    it as its own labeled row rather than folding it into the variable O&M
    row.
  - `post_solve()` additionally writes a standalone `tax_credit_value.csv`
    report (GENERATION_PROJECT, PERIOD, technology, gen_ptc_value_per_mwh,
    dispatch_mwh, tax_credit_value_dollars) so total credit spend by
    scenario/technology/period can be read directly, without back-deriving
    it from opex figures.

Credit magnitudes ($/MWh, always >= 0) are supplied per generation project
and period in gen_tax_credits.csv, written by pg_to_switch.py from the
`tax_credit_values` scenario setting (see the `tax_credits` axis in
pg/settings/scenario_management.yml). Generators/periods not listed default
to 0 (no credit).
"""
import os
from pyomo.environ import *
from switch_model.reporting import write_table


def define_components(m):
    m.gen_ptc_value_per_mwh = Param(
        m.GEN_PERIODS, within=NonNegativeReals, default=0.0
    )

    # Gross credit earned, in dollars -- informational only, not a cost
    # component. Kept separate so it can be reported on its own.
    m.TaxCreditEarned = Expression(
        m.TIMEPOINTS,
        rule=lambda m, tp: sum(
            m.DispatchGen[g, tp]
            * m.gen_ptc_value_per_mwh[g, m.tp_period[tp]]
            for g in m.GENS_IN_PERIOD[m.tp_period[tp]]
        ),
    )

    # Same value, negated, registered as its OWN cost component (distinct
    # from Gen_Variable_OM_by_Period) so the optimizer sees the incentive
    # without it being merged into the opex line.
    m.Gen_Tax_Credit_Value_by_Period = Expression(
        m.TIMEPOINTS, rule=lambda m, tp: -1 * m.TaxCreditEarned[tp]
    )
    m.Cost_Components_Per_TP.append("Gen_Tax_Credit_Value_by_Period")


def load_inputs(m, switch_data, inputs_dir):
    """
    Import PTC-style per-MWh credit values. Optional; all generators default
    to a $0/MWh credit if not listed.

    gen_tax_credits.csv
        GENERATION_PROJECT,
        PERIOD,
        gen_ptc_value_per_mwh,
    """
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "gen_tax_credits.csv"),
        optional=True,
        param=(m.gen_ptc_value_per_mwh,),
    )


def post_solve(m, outdir):
    """
    Write a standalone tax-credit-value report, separate from opex/cost
    outputs, so total credit $ by generator/technology/period can be read
    directly (see module docstring).
    """
    rows = [
        (g, p)
        for (g, p) in m.GEN_PERIODS
        if value(m.gen_ptc_value_per_mwh[g, p]) != 0
    ]

    def dispatch_mwh(g, p):
        return sum(
            value(m.DispatchGen[g, tp]) * m.tp_weight_in_year[tp]
            for tp in m.TPS_IN_PERIOD[p]
        )

    write_table(
        m,
        rows,
        output_file=os.path.join(outdir, "tax_credit_value.csv"),
        headings=(
            "GENERATION_PROJECT",
            "PERIOD",
            "gen_tech",
            "gen_ptc_value_per_mwh",
            "dispatch_mwh",
            "tax_credit_value_dollars",
        ),
        values=lambda m, row: (
            row[0],
            row[1],
            m.gen_tech[row[0]],
            value(m.gen_ptc_value_per_mwh[row[0], row[1]]),
            dispatch_mwh(row[0], row[1]),
            value(m.gen_ptc_value_per_mwh[row[0], row[1]]) * dispatch_mwh(row[0], row[1]),
        ),
    )
