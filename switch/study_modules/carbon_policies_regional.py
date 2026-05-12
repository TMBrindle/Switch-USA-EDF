"""
Implement regional carbon caps with optional two-tier CCR (Cost Containment Reserve).

carbon_policies_regional.csv — base cap per program:
    CO2_PROGRAM: name of the program (there may be multiple)
    PERIOD: period when the cap applies
    LOAD_ZONE: zone participating in the program
    carbon_cap_tco2_per_yr: zone-level cap; carbon trades freely within a program
    carbon_cost_dollar_per_tco2: escape-valve price; "." or omitted = hard cap (inf)

carbon_policies_ccr.csv — optional two-tier CCR for any program:
    CO2_PROGRAM, PERIOD, ccr_tier (1 or 2),
    ccr_pool_tco2_per_yr, ccr_price_dollar_per_tco2

The CCR adds priced flexibility on top of a hard base cap. If total emissions exceed the
base cap the model can purchase Tier 1 allowances (up to the pool) at the Tier 1 price,
then Tier 2 allowances (up to the pool) at the Tier 2 price. Emissions beyond both pools
are infeasible (for programs with a hard base cap) or pay the escape-valve price.

For soft-cap programs (CA, WA — escape-valve cost is finite), AnnualCapViolation remains
the standard slack mechanism and CCR is not used.
"""

import os
import pandas as pd
from pyomo.environ import (
    Any,
    Constraint,
    Expression,
    NonNegativeReals,
    Param,
    Reals,
    Set,
    Suffix,
    Var,
    value,
)
from switch_model.utilities import apply_input_aliases, unique_list
import switch_model.reporting as reporting


def define_components(m):

    # ── Dual suffix (for clearing price extraction in post_solve) ─────────────
    # Declare only if not already present (another module may declare it first).
    if not hasattr(m, "dual"):
        m.dual = Suffix(direction=Suffix.IMPORT)

    # ══════════════════════════════════════════════════════════════════════════
    # BASE CAP (carbon_policies_regional.csv)
    # ══════════════════════════════════════════════════════════════════════════

    # indexing set: (program, period, zone)
    m.REGIONAL_CO2_RULES = Set(dimen=3, within=Any * m.PERIODS * m.LOAD_ZONES)

    m.carbon_cap_tco2_per_yr = Param(
        m.REGIONAL_CO2_RULES,
        within=Reals,
        default=float("inf"),
    )

    # escape-valve price; inf = hard cap (AnnualCapViolation bounded to 0)
    m.carbon_cost_dollar_per_tco2 = Param(
        m.REGIONAL_CO2_RULES,
        within=Reals,
        default=float("inf"),
    )

    # slack that relaxes the constraint at a cost; bounded to 0 for hard caps
    m.AnnualCapViolation = Var(
        m.REGIONAL_CO2_RULES,
        within=NonNegativeReals,
        bounds=lambda m, pr, pe, z: (
            0,
            0 if m.carbon_cost_dollar_per_tco2[pr, pe, z] == float("inf") else None,
        ),
    )

    # (program, period) pairs active in the base cap
    m.CO2_PROGRAM_PERIODS = Set(
        dimen=2,
        initialize=lambda m: unique_list(
            (pr, pe) for (pr, pe, lz) in m.REGIONAL_CO2_RULES
        ),
    )

    # zones in each (program, period)
    m.ZONES_IN_CO2_PROGRAM_PERIOD = Set(
        m.CO2_PROGRAM_PERIODS,
        within=m.LOAD_ZONES,
        initialize=lambda m, pr, pe: [
            z for (pr2, pe2, z) in m.REGIONAL_CO2_RULES if (pr2, pe2) == (pr, pe)
        ],
    )

    # ══════════════════════════════════════════════════════════════════════════
    # CCR TIERS (carbon_policies_ccr.csv)
    # ══════════════════════════════════════════════════════════════════════════

    # indexing set: (program, period, tier)
    m.CCR_RULES = Set(dimen=3, within=Any * m.PERIODS * Any)

    m.ccr_pool_tco2_per_yr = Param(m.CCR_RULES, within=NonNegativeReals)

    m.ccr_price_dollar_per_tco2 = Param(m.CCR_RULES, within=NonNegativeReals)

    # allowances purchased from each CCR tier; bounded by pool size
    m.CCRPurchases = Var(
        m.CCR_RULES,
        within=NonNegativeReals,
        bounds=lambda m, pr, pe, tier: (0, m.ccr_pool_tco2_per_yr[pr, pe, tier]),
    )

    # (program, period) pairs that have CCR tiers
    m.CCR_PROGRAM_PERIODS = Set(
        dimen=2,
        initialize=lambda m: unique_list(
            (pr, pe) for (pr, pe, tier) in m.CCR_RULES
        ),
    )

    # tiers available in each CCR (program, period)
    m.TIERS_IN_CCR_PROGRAM_PERIOD = Set(
        m.CCR_PROGRAM_PERIODS,
        initialize=lambda m, pr, pe: sorted(
            tier for (pr2, pe2, tier) in m.CCR_RULES if (pr2, pe2) == (pr, pe)
        ),
    )

    # ══════════════════════════════════════════════════════════════════════════
    # CONSTRAINT
    # ══════════════════════════════════════════════════════════════════════════

    def cap_rule(m, pr, pe):
        cap = sum(
            m.carbon_cap_tco2_per_yr[pr, pe, z]
            for z in m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, pe]
        )
        if cap == float("inf"):
            return Constraint.Skip

        exceedance = sum(
            m.AnnualCapViolation[pr, pe, z]
            for z in m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, pe]
        )

        # CCR tiers augment the effective cap for programs that have them
        ccr_slack = (
            sum(
                m.CCRPurchases[pr, pe, tier]
                for tier in m.TIERS_IN_CCR_PROGRAM_PERIOD[pr, pe]
            )
            if (pr, pe) in m.CCR_PROGRAM_PERIODS
            else 0
        )

        emissions = sum(
            m.DispatchEmissions[g, tp, f] * m.tp_weight_in_year[tp]
            for z in m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, pe]
            for g in m.GENS_IN_ZONE[z]
            if g in m.FUEL_BASED_GENS
            for tp in m.TPS_FOR_GEN_IN_PERIOD[g, pe]
            for f in m.FUELS_FOR_GEN[g]
        )

        # scale by 0.001 to improve numerical stability
        return emissions * 0.001 <= (cap + exceedance + ccr_slack) * 0.001

    m.Enforce_Regional_Carbon_Cap = Constraint(m.CO2_PROGRAM_PERIODS, rule=cap_rule)

    # ══════════════════════════════════════════════════════════════════════════
    # COST
    # ══════════════════════════════════════════════════════════════════════════

    m.EmissionsCost = Expression(
        m.PERIODS,
        rule=lambda m, pe: (
            # escape-valve costs for soft-cap programs (CA, WA)
            sum(
                m.AnnualCapViolation[pr, pe2, z]
                * m.carbon_cost_dollar_per_tco2[pr, pe2, z]
                for (pr, pe2) in m.CO2_PROGRAM_PERIODS
                if pe2 == pe
                for z in m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, pe2]
                if m.carbon_cost_dollar_per_tco2[pr, pe2, z] != float("inf")
            )
            # CCR purchase costs
            + sum(
                m.CCRPurchases[pr, pe2, tier]
                * m.ccr_price_dollar_per_tco2[pr, pe2, tier]
                for (pr, pe2, tier) in m.CCR_RULES
                if pe2 == pe
            )
        ),
        doc="Annual cost of CO2 cap violations and CCR allowance purchases.",
    )
    m.Cost_Components_Per_Period.append("EmissionsCost")


def load_inputs(m, switch_data, inputs_dir):
    """
    Load carbon_policies_regional.csv (base caps) and
    carbon_policies_ccr.csv (CCR tier specs, optional).
    """
    switch_data.load_aug(
        filename=apply_input_aliases(
            switch_data,
            os.path.join(inputs_dir, "carbon_policies_regional.csv"),
        ),
        optional=True,
        index=m.REGIONAL_CO2_RULES,
        param=(m.carbon_cap_tco2_per_yr, m.carbon_cost_dollar_per_tco2),
    )

    # Load CCR data manually (mixed-type 3D index not handled well by load_aug)
    ccr_path = apply_input_aliases(
        switch_data, os.path.join(inputs_dir, "carbon_policies_ccr.csv")
    )
    if os.path.isfile(ccr_path):
        ccr = pd.read_csv(ccr_path)
        if not ccr.empty:
            if None not in switch_data._data:
                switch_data._data[None] = {}
            switch_data._data[None]["CCR_RULES"] = {
                None: set(
                    (row.CO2_PROGRAM, int(row.PERIOD), int(row.ccr_tier))
                    for row in ccr.itertuples()
                )
            }
            switch_data._data[None]["ccr_pool_tco2_per_yr"] = {
                (row.CO2_PROGRAM, int(row.PERIOD), int(row.ccr_tier)): float(
                    row.ccr_pool_tco2_per_yr
                )
                for row in ccr.itertuples()
            }
            switch_data._data[None]["ccr_price_dollar_per_tco2"] = {
                (row.CO2_PROGRAM, int(row.PERIOD), int(row.ccr_tier)): float(
                    row.ccr_price_dollar_per_tco2
                )
                for row in ccr.itertuples()
            }


def post_solve(m, outputs_dir):
    """
    Write carbon_program_clearing_prices.csv with per-program results:
    actual emissions, cap, CCR usage, and allowance clearing price.

    Clearing price for hard-cap programs is extracted from the constraint dual.
    Sign convention: Pyomo minimisation duals are negative for <= constraints;
    we negate and divide by the 0.001 scaling factor to get $/tCO2.
    For soft-cap programs the clearing price equals the escape-valve price when
    AnnualCapViolation > 0, otherwise 0.
    For CCR: when Tier 1 purchases < pool, price = Tier 1 trigger; when Tier 1
    is exhausted and Tier 2 > 0, price = Tier 2 trigger.
    """
    rows = []
    for pr, pe in m.CO2_PROGRAM_PERIODS:
        constr = m.Enforce_Regional_Carbon_Cap[pr, pe]
        zones = list(m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, pe])

        cap = sum(
            value(m.carbon_cap_tco2_per_yr[pr, pe, z])
            for z in zones
            if value(m.carbon_cap_tco2_per_yr[pr, pe, z]) != float("inf")
        )

        emissions = sum(
            value(m.DispatchEmissions[g, tp, f]) * value(m.tp_weight_in_year[tp])
            for z in zones
            for g in m.GENS_IN_ZONE[z]
            if g in m.FUEL_BASED_GENS
            for tp in m.TPS_FOR_GEN_IN_PERIOD[g, pe]
            for f in m.FUELS_FOR_GEN[g]
        )

        violation = sum(value(m.AnnualCapViolation[pr, pe, z]) for z in zones)

        # CCR usage per tier
        ccr_usage = {}
        if (pr, pe) in m.CCR_PROGRAM_PERIODS:
            for tier in m.TIERS_IN_CCR_PROGRAM_PERIOD[pr, pe]:
                ccr_usage[tier] = value(m.CCRPurchases[pr, pe, tier])

        # Determine clearing price
        escape_cost = value(m.carbon_cost_dollar_per_tco2[pr, pe, zones[0]])
        is_hard_cap = escape_cost == float("inf")

        if is_hard_cap:
            # CCR-based price determination
            if ccr_usage:
                tiers = sorted(ccr_usage.keys())
                price = 0.0
                for tier in tiers:
                    pool = value(m.ccr_pool_tco2_per_yr[pr, pe, tier])
                    used = ccr_usage[tier]
                    if used > 1e-3:  # tier is active
                        price = value(m.ccr_price_dollar_per_tco2[pr, pe, tier])
                # if no CCR used, fall back to constraint dual
                if price == 0.0:
                    dual = m.dual.get(constr)
                    price = (-dual / 0.001) if dual is not None else 0.0
            else:
                dual = m.dual.get(constr)
                price = (-dual / 0.001) if dual is not None else ""
        else:
            # soft cap: price = escape-valve cost if constraint is binding
            price = escape_cost if violation > 1e-3 else 0.0

        row = {
            "CO2_PROGRAM": pr,
            "PERIOD": pe,
            "cap_tco2_per_yr": cap,
            "emissions_tco2_per_yr": round(emissions, 0),
            "surplus_tco2_per_yr": round(cap - emissions, 0),
            "violation_tco2_per_yr": round(violation, 0),
            "clearing_price_dollar_per_tco2": (
                round(price, 4) if isinstance(price, float) else price
            ),
        }
        # CCR tier columns
        for tier in sorted(ccr_usage.keys()):
            row[f"ccr_tier{tier}_purchases_tco2"] = round(ccr_usage[tier], 0)
            row[f"ccr_tier{tier}_pool_tco2"] = value(
                m.ccr_pool_tco2_per_yr[pr, pe, tier]
            )
            row[f"ccr_tier{tier}_price_dollar_per_tco2"] = value(
                m.ccr_price_dollar_per_tco2[pr, pe, tier]
            )
        rows.append(row)

    out_path = os.path.join(outputs_dir, "carbon_program_clearing_prices.csv")
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"carbon_policies_regional: wrote {out_path} ({len(rows)} program-period rows)")
