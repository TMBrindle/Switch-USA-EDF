"""
Implement regional carbon caps with optional two-tier CCR (Cost Containment Reserve)
and optional allowance banking across periods.

carbon_policies_regional.csv — base cap per program:
    CO2_PROGRAM: name of the program (there may be multiple)
    PERIOD: period when the cap applies
    LOAD_ZONE: zone participating in the program
    carbon_cap_tco2_per_yr: zone-level cap; carbon trades freely within a program
    carbon_cost_dollar_per_tco2: escape-valve price; "." or omitted = hard cap (inf)
    carbon_floor_price_dollar_per_tco2: MRP floor; "." or omitted = 0 (no floor)

carbon_policies_ccr.csv — optional two-tier CCR for any program:
    CO2_PROGRAM, PERIOD, ccr_tier (1 or 2),
    ccr_pool_tco2_per_yr, ccr_price_dollar_per_tco2

carbon_policies_banking.csv — optional allowance banking:
    CO2_PROGRAM, initial_bank_tco2
    One row per program. If absent, banking is disabled for all programs.

The CCR adds priced flexibility on top of a hard base cap. If total emissions exceed the
base cap the model can purchase Tier 1 allowances (up to the pool) at the Tier 1 price,
then Tier 2 allowances (up to the pool) at the Tier 2 price. Emissions beyond both pools
are infeasible (for programs with a hard base cap) or pay the escape-valve price.

When banking is active, generators can purchase CCR allowances in excess of current
compliance need (CCR_Banked) and carry the balance forward for use in future periods
(BankDraw). The bank balance is cumulative: allowances purchased in any period are
available in all future periods. An exogenous initial bank (initial_bank_tco2) represents
the existing RGGI bank entering the first model period.

Banking works in both myopic (single-period) and foresight (multi-period) solves:
- Myopic: initial_bank acts as a compliance buffer; CCR_Banked is naturally zero
  (no future period to benefit); pass_bank_balance.py forwards the end balance.
- Foresight: the optimizer can bank CCR purchases in cheaper early periods for use
  when the cap tightens in later periods.

For soft-cap programs (CA, WA — escape-valve cost is finite), AnnualCapViolation remains
the standard slack mechanism and CCR/banking are not used.
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

    # minimum reserve price: sets a floor on the allowance clearing price by
    # restricting supply (see FloorAllowances). When cap is non-binding, the floor
    # is the full clearing price. When CCR is triggered, the CCR price dominates.
    m.carbon_floor_price_dollar_per_tco2 = Param(
        m.REGIONAL_CO2_RULES,
        within=NonNegativeReals,
        default=0.0,
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

    # sum of zone-level caps for each (program, period)
    m.ProgramCap_tco2 = Param(
        m.CO2_PROGRAM_PERIODS,
        initialize=lambda m, pr, pe: sum(
            m.carbon_cap_tco2_per_yr[pr, pe, z]
            for z in m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, pe]
        ),
        within=Reals,
    )

    # program-level floor price (all zones share the same value; take the min zone)
    m.carbon_floor_price_dollar_per_tco2_program = Param(
        m.CO2_PROGRAM_PERIODS,
        initialize=lambda m, pr, pe: m.carbon_floor_price_dollar_per_tco2[
            pr, pe, min(m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, pe])
        ],
        within=NonNegativeReals,
        default=0.0,
    )

    # ── Floor allowances (supply-restriction mechanism) ────────────────────────
    # The state withholds (cap - FloorAllowances) allowances when the market price
    # would fall below the floor. FloorAllowances replaces the fixed cap in the
    # emissions constraint:
    #   non-binding cap  → FloorAllowances = emissions, dual = floor_price
    #   CCR active       → FloorAllowances = cap, dual = CCR_trigger (floor irrelevant)
    #   binding, no CCR  → FloorAllowances = cap, dual ∈ [floor, CCR_trigger]
    # When floor_price = 0: model freely sets FloorAllowances = cap → no change.
    m.FloorAllowances = Var(
        m.CO2_PROGRAM_PERIODS,
        within=NonNegativeReals,
        bounds=lambda m, pr, pe: (
            0,
            None if value(m.ProgramCap_tco2[pr, pe]) == float("inf")
            else value(m.ProgramCap_tco2[pr, pe]),
        ),
    )

    # ══════════════════════════════════════════════════════════════════════════
    # ALLOWANCE BANKING (carbon_policies_banking.csv — optional)
    # ══════════════════════════════════════════════════════════════════════════

    # Programs with banking active. Empty set if carbon_policies_banking.csv absent.
    m.BANKING_PROGRAMS = Set(within=Any)

    # Exogenous bank balance (tCO2) entering the first model period.
    # Represents the existing RGGI bank at the start of the study horizon.
    m.initial_bank_tco2 = Param(m.BANKING_PROGRAMS, within=NonNegativeReals, default=0.0)

    # ══════════════════════════════════════════════════════════════════════════
    # CCR TIERS (carbon_policies_ccr.csv)
    # ══════════════════════════════════════════════════════════════════════════

    # indexing set: (program, period, tier)
    m.CCR_RULES = Set(dimen=3, within=Any * m.PERIODS * Any)

    m.ccr_pool_tco2_per_yr = Param(m.CCR_RULES, within=NonNegativeReals)

    m.ccr_price_dollar_per_tco2 = Param(m.CCR_RULES, within=NonNegativeReals)

    # allowances purchased from each CCR tier for current-period compliance;
    # bounded by pool size (for banking programs, CCR_Banked further shares the pool)
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
    # BANKING VARIABLES AND CONSTRAINTS
    # ══════════════════════════════════════════════════════════════════════════

    # (program, period) pairs where banking is active
    m.BANKING_CO2_PROGRAM_PERIODS = Set(
        dimen=2,
        initialize=lambda m: [
            (pr, pe) for (pr, pe) in m.CO2_PROGRAM_PERIODS
            if pr in m.BANKING_PROGRAMS
        ],
    )

    # (program, period, tier) CCR rules for banking programs
    m.CCR_BANKING_RULES = Set(
        dimen=3,
        initialize=lambda m: [
            (pr, pe, tier) for (pr, pe, tier) in m.CCR_RULES
            if pr in m.BANKING_PROGRAMS
        ],
    )

    # Bank balance at END of each period; non-negative (can't owe allowances)
    m.AllowanceBank = Var(m.BANKING_CO2_PROGRAM_PERIODS, within=NonNegativeReals)

    # CCR allowances purchased above current-period compliance need, for banking
    m.CCR_Banked = Var(m.CCR_BANKING_RULES, within=NonNegativeReals)

    # Allowances drawn from the bank to supplement current-period compliance
    m.BankDraw = Var(m.BANKING_CO2_PROGRAM_PERIODS, within=NonNegativeReals)

    def _prev_period(m, pr, pe):
        """Previous period for program pr before pe, or None if pe is the first."""
        periods = sorted(pe2 for (pr2, pe2) in m.CO2_PROGRAM_PERIODS if pr2 == pr)
        idx = periods.index(pe)
        return periods[idx - 1] if idx > 0 else None

    def _bank_start(m, pr, pe):
        """Bank balance available at the start of period pe for program pr."""
        prev = _prev_period(m, pr, pe)
        return m.initial_bank_tco2[pr] if prev is None else m.AllowanceBank[pr, prev]

    # Joint CCR pool constraint for banking programs:
    # compliance purchases + banked purchases <= pool per tier
    m.CCR_Joint_Pool = Constraint(
        m.CCR_BANKING_RULES,
        rule=lambda m, pr, pe, tier: (
            m.CCRPurchases[pr, pe, tier] + m.CCR_Banked[pr, pe, tier]
            <= m.ccr_pool_tco2_per_yr[pr, pe, tier]
        ),
    )

    # Bank balance evolution: end_balance = start + CCR_banked_this_period - drawn
    def bank_evolution_rule(m, pr, pe):
        ccr_banked = (
            sum(
                m.CCR_Banked[pr, pe, tier]
                for tier in m.TIERS_IN_CCR_PROGRAM_PERIOD[pr, pe]
            )
            if (pr, pe) in m.CCR_PROGRAM_PERIODS
            else 0
        )
        return (
            m.AllowanceBank[pr, pe]
            == _bank_start(m, pr, pe) + ccr_banked - m.BankDraw[pr, pe]
        )

    m.AllowanceBank_Evolution = Constraint(
        m.BANKING_CO2_PROGRAM_PERIODS, rule=bank_evolution_rule
    )

    # BankDraw cannot exceed the balance available at the start of the period
    m.BankDraw_Limit = Constraint(
        m.BANKING_CO2_PROGRAM_PERIODS,
        rule=lambda m, pr, pe: m.BankDraw[pr, pe] <= _bank_start(m, pr, pe),
    )

    # ══════════════════════════════════════════════════════════════════════════
    # CONSTRAINT
    # ══════════════════════════════════════════════════════════════════════════

    def cap_rule(m, pr, pe):
        if m.ProgramCap_tco2[pr, pe] == float("inf"):
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

        # Allowances drawn from the bank provide additional compliance headroom
        bank_draw = m.BankDraw[pr, pe] if pr in m.BANKING_PROGRAMS else 0

        emissions = sum(
            m.DispatchEmissions[g, tp, f] * m.tp_weight_in_year[tp]
            for z in m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, pe]
            for g in m.GENS_IN_ZONE[z]
            if g in m.FUEL_BASED_GENS
            for tp in m.TPS_FOR_GEN_IN_PERIOD[g, pe]
            for f in m.FUELS_FOR_GEN[g]
        )

        # FloorAllowances replaces the fixed cap; the state withholds
        # (cap - FloorAllowances) allowances to enforce the price floor.
        # BankDraw supplements the auctioned supply from the bank.
        # scale by 0.001 to improve numerical stability
        return (
            emissions * 0.001
            <= (m.FloorAllowances[pr, pe] + exceedance + ccr_slack + bank_draw) * 0.001
        )

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
            # CCR compliance purchase costs
            + sum(
                m.CCRPurchases[pr, pe2, tier]
                * m.ccr_price_dollar_per_tco2[pr, pe2, tier]
                for (pr, pe2, tier) in m.CCR_RULES
                if pe2 == pe
            )
            # Floor allowance cost: floor_price × FloorAllowances (supply-restriction
            # mechanism). When cap is non-binding, FloorAllowances = emissions so
            # cost = floor × emissions. When CCR is active, FloorAllowances = cap
            # (flat cost) and the dual already captures the full clearing price.
            + sum(
                m.carbon_floor_price_dollar_per_tco2_program[pr, pe2]
                * m.FloorAllowances[pr, pe2]
                for (pr, pe2) in m.CO2_PROGRAM_PERIODS
                if pe2 == pe
            )
            # CCR banking costs: allowances purchased for future-period compliance
            # are paid at the CCR price in the period they are purchased
            + sum(
                m.CCR_Banked[pr, pe2, tier]
                * m.ccr_price_dollar_per_tco2[pr, pe2, tier]
                for (pr, pe2, tier) in m.CCR_BANKING_RULES
                if pe2 == pe
            )
        ),
        doc="Annual cost of CO2 cap violations, CCR purchases, floor, and banking.",
    )
    m.Cost_Components_Per_Period.append("EmissionsCost")


def load_inputs(m, switch_data, inputs_dir):
    """
    Load carbon_policies_regional.csv (base caps),
    carbon_policies_ccr.csv (CCR tier specs, optional), and
    carbon_policies_banking.csv (allowance banking, optional).
    """
    switch_data.load_aug(
        filename=apply_input_aliases(
            switch_data,
            os.path.join(inputs_dir, "carbon_policies_regional.csv"),
        ),
        optional=True,
        index=m.REGIONAL_CO2_RULES,
        param=(
            m.carbon_cap_tco2_per_yr,
            m.carbon_cost_dollar_per_tco2,
            m.carbon_floor_price_dollar_per_tco2,
        ),
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

    # Load banking configuration (optional; absent = no banking)
    banking_path = apply_input_aliases(
        switch_data, os.path.join(inputs_dir, "carbon_policies_banking.csv")
    )
    if os.path.isfile(banking_path):
        banking = pd.read_csv(banking_path)
        if not banking.empty:
            if None not in switch_data._data:
                switch_data._data[None] = {}
            switch_data._data[None]["BANKING_PROGRAMS"] = {
                None: set(banking["CO2_PROGRAM"].tolist())
            }
            switch_data._data[None]["initial_bank_tco2"] = {
                row.CO2_PROGRAM: float(row.initial_bank_tco2)
                for row in banking.itertuples()
            }


def post_solve(m, outputs_dir):
    """
    Write carbon_program_clearing_prices.csv with per-program results:
    actual emissions, cap, CCR usage, bank draws, and allowance clearing price.

    Also writes allowance_bank_balance.csv when banking is active, for use
    by pass_bank_balance.py to chain the end-of-period balance into the next
    myopic solve's initial bank.

    Clearing price for hard-cap programs is extracted from the constraint dual.
    Sign convention: Pyomo minimisation duals are negative for <= constraints;
    we negate and divide by the 0.001 scaling factor to get $/tCO2.
    For soft-cap programs the clearing price equals the escape-valve price when
    AnnualCapViolation > 0, otherwise 0.
    For CCR: when Tier 1 purchases < pool, price = Tier 1 trigger; when Tier 1
    is exhausted and Tier 2 > 0, price = Tier 2 trigger.
    """

    def _prev_period(pr, pe):
        periods = sorted(pe2 for (pr2, pe2) in m.CO2_PROGRAM_PERIODS if pr2 == pr)
        idx = periods.index(pe)
        return periods[idx - 1] if idx > 0 else None

    rows = []
    bank_rows = []

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

        # Banking quantities
        banking_active = pr in m.BANKING_PROGRAMS
        if banking_active:
            prev = _prev_period(pr, pe)
            bank_start_val = (
                value(m.initial_bank_tco2[pr]) if prev is None
                else value(m.AllowanceBank[pr, prev])
            )
            bank_draw_val = value(m.BankDraw[pr, pe])
            ccr_banked_val = (
                sum(value(m.CCR_Banked[pr, pe, tier])
                    for tier in m.TIERS_IN_CCR_PROGRAM_PERIOD[pr, pe])
                if (pr, pe) in m.CCR_PROGRAM_PERIODS else 0
            )
            bank_end_val = value(m.AllowanceBank[pr, pe])
            bank_rows.append({
                "CO2_PROGRAM": pr,
                "PERIOD": pe,
                "bank_start_tco2": round(bank_start_val, 0),
                "ccr_banked_tco2": round(ccr_banked_val, 0),
                "bank_draw_tco2": round(bank_draw_val, 0),
                "bank_end_tco2": round(bank_end_val, 0),
            })

        # Determine clearing price (floor_price + scarcity premium from cap dual)
        escape_cost = value(m.carbon_cost_dollar_per_tco2[pr, pe, zones[0]])
        is_hard_cap = escape_cost == float("inf")
        floor_price = value(m.carbon_floor_price_dollar_per_tco2[pr, pe, zones[0]])

        if is_hard_cap:
            # Clearing price comes from the constraint dual. With the
            # supply-restriction floor mechanism, the dual already encodes:
            #   non-binding cap  → dual = floor_price
            #   CCR active       → dual = CCR_trigger (floor irrelevant)
            #   binding, no CCR  → dual = scarcity ∈ [floor, CCR_trigger]
            # Dual-to-price conversion: objective weights annual costs by
            # bring_annual_costs_to_base_year[pe], so the dual of the 0.001-scaled
            # emission constraint is in NPV units.
            # Annualised $/tCO2 = -dual * 0.001 / bring_annual_costs_to_base_year.
            # Note: barrier solver without crossover gives unreliable duals;
            # with crossover=1 these are true LP duals (see I-19).
            if ccr_usage:
                tiers = sorted(ccr_usage.keys())
                price = 0.0
                for tier in tiers:
                    if ccr_usage[tier] > 1e-3:
                        price = value(m.ccr_price_dollar_per_tco2[pr, pe, tier])
                if price == 0.0:
                    dual = m.dual.get(constr)
                    npv_weight = value(m.bring_annual_costs_to_base_year[pe])
                    price = (-dual * 0.001 / npv_weight) if dual is not None else ""
            else:
                dual = m.dual.get(constr)
                npv_weight = value(m.bring_annual_costs_to_base_year[pe])
                price = (-dual * 0.001 / npv_weight) if dual is not None else ""
        else:
            # soft cap: price = escape-valve cost if constraint is binding
            price = escape_cost if violation > 1e-3 else floor_price

        row = {
            "CO2_PROGRAM": pr,
            "PERIOD": pe,
            "cap_tco2_per_yr": cap,
            "emissions_tco2_per_yr": round(emissions, 0),
            "surplus_tco2_per_yr": round(cap - emissions, 0),
            "violation_tco2_per_yr": round(violation, 0),
            "floor_price_dollar_per_tco2": round(floor_price, 4),
            "clearing_price_dollar_per_tco2": (
                round(price, 4) if isinstance(price, float) else price
            ),
            "auction_revenue_dollar_per_yr": (
                round(price * emissions, 0) if isinstance(price, float) else ""
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
        # Banking columns (present only when banking is active for this program)
        if banking_active:
            row["bank_start_tco2"] = round(bank_start_val, 0)
            row["ccr_banked_tco2"] = round(ccr_banked_val, 0)
            row["bank_draw_tco2"] = round(bank_draw_val, 0)
            row["bank_end_tco2"] = round(bank_end_val, 0)

        rows.append(row)

    out_path = os.path.join(outputs_dir, "carbon_program_clearing_prices.csv")
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"carbon_policies_regional: wrote {out_path} ({len(rows)} program-period rows)")

    if bank_rows:
        bank_path = os.path.join(outputs_dir, "allowance_bank_balance.csv")
        pd.DataFrame(bank_rows).to_csv(bank_path, index=False)
        print(f"carbon_policies_regional: wrote {bank_path} ({len(bank_rows)} rows)")
