from __future__ import division
import os
from pyomo.environ import *
from switch_model.financials import capital_recovery_factor as crf

dependencies = 'switch_model.timescales', 'hydrogen_supply.hydrogen_nodes'

def define_arguments(argparser):
    argparser.add_argument('--no-hydrogen', action='store_true', default=False,
        help="Don't allow construction of any hydrogen infrastructure."
    )

def define_components(m):
    if not m.options.no_hydrogen:
        define_green_hydrogen_components(m)

def define_green_hydrogen_components(m):

    # electrolyzer details
    m.hydrogen_electrolyzer_capital_cost_per_mw = Param()
    m.hydrogen_electrolyzer_fixed_cost_per_mw_year = Param(default=0.0)
    m.hydrogen_electrolyzer_variable_cost_per_kg = Param(default=0.0)  # assumed to include any refurbishment needed
    m.hydrogen_electrolyzer_kg_per_mwh = Param() # assumed to deliver H2 at enough pressure for liquifier and daily buffering
    m.hydrogen_electrolyzer_life_years = Param()
    m.BuildElectrolyzerMW = Var(m.LOAD_ZONES, m.PERIODS, within=NonNegativeReals)
    m.ElectrolyzerCapacityMW = Expression(m.LOAD_ZONES, m.PERIODS, rule=lambda m, z, p:
        sum(m.BuildElectrolyzerMW[z, p_] for p_ in m.CURRENT_AND_PRIOR_PERIODS_FOR_PERIOD[p]))
    m.RunElectrolyzerMW = Var(m.LOAD_ZONES, m.TIMEPOINTS, within=NonNegativeReals)
    m.ProduceHydrogenKgPerHour = Expression(m.LOAD_ZONES, m.TIMEPOINTS, rule=lambda m, z, t:
        m.RunElectrolyzerMW[z, t] * m.hydrogen_electrolyzer_kg_per_mwh)
    m.ElectrolyzerGreenHydrogenKgPerHour = Var(m.LOAD_ZONES, m.TIMEPOINTS, within=NonNegativeReals)
    

    # note: we assume there is a gaseous hydrogen storage tank that is big enough to buffer
    # daily production, storage and withdrawals of hydrogen, but we don't include a cost
    # for this (because it will be negligible compared to the rest of the costs)
    # This allows the system to do some intra-day arbitrage without going all the way to liquification

    # liquifier details
    m.hydrogen_liquifier_capital_cost_per_kg_per_hour = Param()
    m.hydrogen_liquifier_fixed_cost_per_kg_hour_year = Param(default=0.0)
    m.hydrogen_liquifier_variable_cost_per_kg = Param(default=0.0)
    m.hydrogen_liquifier_mwh_per_kg = Param()
    m.hydrogen_liquifier_life_years = Param()
    m.BuildLiquifierKgPerHour = Var(m.LOAD_ZONES, m.PERIODS, within=NonNegativeReals)  # capacity to build, measured in kg/hour of throughput
    m.LiquifierCapacityKgPerHour = Expression(m.LOAD_ZONES, m.PERIODS, rule=lambda m, z, p:
        sum(m.BuildLiquifierKgPerHour[z, p_] for p_ in m.CURRENT_AND_PRIOR_PERIODS_FOR_PERIOD[p]))
    m.LiquifyHydrogenKgPerHour = Var(m.LOAD_ZONES, m.TIMEPOINTS, within=NonNegativeReals)
    m.LiquifyHydrogenMW = Expression(m.LOAD_ZONES, m.TIMEPOINTS, rule=lambda m, z, t:
        m.LiquifyHydrogenKgPerHour[z, t] * m.hydrogen_liquifier_mwh_per_kg
    )
    m.LiquifyHydrogenKgPerHourToStorage = Var(m.LOAD_ZONES, m.TIMEPOINTS, within=NonNegativeReals)
    m.LiquifyHydrogenKgPerHourToSupply = Var(m.LOAD_ZONES, m.TIMEPOINTS, within=NonNegativeReals)


    #Contraint that relate the amount of H2 produced that is delivered and the one that is liquified
    m.ElectrolyzerGreenHydrogenKgPerHour_Constraint = Constraint(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=lambda m, z, t: (
            m.ElectrolyzerGreenHydrogenKgPerHour[z, t] == m.ProduceHydrogenKgPerHour[z, t] - m.LiquifyHydrogenKgPerHour[z, t])
    )

    #Contraint that relate the amount of H2 produced that is delivered and the one that is liquified
    m.LiquifyGreenHydrogenKgPerHour_Constraint = Constraint(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=lambda m, z, t: (
            m.LiquifyHydrogenKgPerHour[z, t] == m.LiquifyHydrogenKgPerHourToStorage[z,t] + m.LiquifyHydrogenKgPerHourToSupply[z,t])
    )

    # storage tank details
    m.liquid_hydrogen_tank_capital_cost_per_kg = Param()
    m.liquid_hydrogen_tank_minimum_size_kg = Param(default=0.0)
    m.liquid_hydrogen_tank_life_years = Param()
    m.BuildLiquidHydrogenTankKg = Var(m.LOAD_ZONES, m.PERIODS, within=NonNegativeReals) # in kg
    m.LiquidHydrogenTankCapacityKg = Expression(m.LOAD_ZONES, m.PERIODS, rule=lambda m, z, p:
        sum(m.BuildLiquidHydrogenTankKg[z, p_] for p_ in m.CURRENT_AND_PRIOR_PERIODS_FOR_PERIOD[p]))
    m.StoredLiquidHydrogenKg = Var(m.LOAD_ZONES, m.TIMEPOINTS, within=NonNegativeReals)
    m.WithdrawLiquidHydrogenKg = Var(m.LOAD_ZONES, m.TIMEPOINTS, within=NonNegativeReals)
    #Border conditions of the tank: first tp of period should be empty
    m.Tank_Initial_Condition = Constraint(m.LOAD_ZONES,  m.TIMEPOINTS, rule=lambda m, z, tp:
        m.StoredLiquidHydrogenKg[z,  m.TPS_IN_PERIOD[m.tp_period[tp]].first()] ==  0
    )
    # note: we assume the system will be large enough to neglect boil-off

    # hydrogen mass balances
    m.Hydrogen_Conservation_of_Mass_Hourly = Constraint(m.LOAD_ZONES, m.TIMEPOINTS, rule=lambda m, z, tp:
        m.StoredLiquidHydrogenKg[z, tp] ==  m.StoredLiquidHydrogenKg[z, m.tp_previous[tp]] + m.LiquifyHydrogenKgPerHourToStorage[z, tp] - m.WithdrawLiquidHydrogenKg[z, tp]
    )

    #Total hydrogen production details
    m.LiquidGreenHydrogenProductionKgPerHour = Expression(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=lambda m, z, t: (
            m.WithdrawLiquidHydrogenKg[z, t] + m.LiquifyHydrogenKgPerHourToSupply[z,t])
    )
    #We add the total hydrogen production to the injections: gaseous and Liquid
    m.Liquid_Hydrogen_Zone_Injections.append('LiquidGreenHydrogenProductionKgPerHour')
    m.Gaseous_Hydrogen_Zone_Injections.append('ElectrolyzerGreenHydrogenKgPerHour')


    # limits on equipment
    m.Max_Run_Electrolyzer = Constraint(m.LOAD_ZONES, m.TIMEPOINTS, rule=lambda m, z, t:
        m.RunElectrolyzerMW[z, t] <= m.ElectrolyzerCapacityMW[z, m.tp_period[t]])
    m.Max_Run_Liquifier = Constraint(m.LOAD_ZONES, m.TIMEPOINTS, rule=lambda m, z, t:
        m.LiquifyHydrogenKgPerHour[z, t] <= m.LiquifierCapacityKgPerHour[z, m.tp_period[t]])

    # Enforce minimum size for hydrogen tank if specified. We only define these
    # variables and constraints if needed, to avoid warnings about variables
    # with no values assigned.
    def action(m):
        if m.liquid_hydrogen_tank_minimum_size_kg != 0.0:
            m.BuildAnyLiquidHydrogenTank = Var(
                m.LOAD_ZONES, m.PERIODS, within=Binary
            )
            m.Set_BuildAnyLiquidHydrogenTank_Flag = Constraint(
                m.LOAD_ZONES, m.PERIODS,
                rule=lambda m, z, p:
                    m.BuildLiquidHydrogenTankKg[z, p]
                    <=
                    1000 * m.BuildAnyLiquidHydrogenTank[z, p]
                    * m.liquid_hydrogen_tank_minimum_size_kg
            )
            m.Build_Minimum_Liquid_Hydrogen_Tank = Constraint(
                m.LOAD_ZONES, m.PERIODS,
                rule=lambda m, z, p:
                    m.BuildLiquidHydrogenTankKg[z, p]
                    >=
                    m.BuildAnyLiquidHydrogenTank[z, p]
                    * m.liquid_hydrogen_tank_minimum_size_kg
            )
    m.Apply_liquid_hydrogen_tank_minimum_size = BuildAction(rule=action)


    # add electricity consumption and production to the zonal energy balance
    m.Zone_Power_Withdrawals.append('RunElectrolyzerMW')
    m.Zone_Power_Withdrawals.append('LiquifyHydrogenMW')

    # add costs to the model
    m.HydrogenVariableCost = Expression(m.TIMEPOINTS, rule=lambda m, t:
        sum(
            m.ProduceHydrogenKgPerHour[z, t] * m.hydrogen_electrolyzer_variable_cost_per_kg
            + m.LiquifyHydrogenKgPerHour[z, t] * m.hydrogen_liquifier_variable_cost_per_kg
            for z in m.LOAD_ZONES
        )
    )
    m.HydrogenFixedCostAnnual = Expression(m.PERIODS, rule=lambda m, p:
        sum(
            m.ElectrolyzerCapacityMW[z, p] * (
                m.hydrogen_electrolyzer_capital_cost_per_mw * crf(m.interest_rate, m.hydrogen_electrolyzer_life_years)
                + m.hydrogen_electrolyzer_fixed_cost_per_mw_year)
            + m.LiquifierCapacityKgPerHour[z, p] * (
                m.hydrogen_liquifier_capital_cost_per_kg_per_hour * crf(m.interest_rate, m.hydrogen_liquifier_life_years)
                + m.hydrogen_liquifier_fixed_cost_per_kg_hour_year)
            + m.LiquidHydrogenTankCapacityKg[z, p] * (
                m.liquid_hydrogen_tank_capital_cost_per_kg * crf(m.interest_rate, m.liquid_hydrogen_tank_life_years))
            for z in m.LOAD_ZONES
        )
    )
    m.Cost_Components_Per_TP.append('HydrogenVariableCost')
    m.Cost_Components_Per_Period.append('HydrogenFixedCostAnnual')



def load_inputs(m, switch_data, inputs_dir):
    """
    Import hydrogen data from a .csv file.
    TODO: change this to allow multiple storage technologies.
    """
    if not m.options.no_hydrogen:
        switch_data.load_aug(
            filename=os.path.join(inputs_dir, 'green_hydrogen.csv'),
            optional=False, auto_select=True,
            param=(
                m.hydrogen_electrolyzer_capital_cost_per_mw,
                m.hydrogen_electrolyzer_fixed_cost_per_mw_year,
                m.hydrogen_electrolyzer_kg_per_mwh,
                m.hydrogen_electrolyzer_life_years,
                m.hydrogen_electrolyzer_variable_cost_per_kg,
                m.hydrogen_liquifier_capital_cost_per_kg_per_hour,
                m.hydrogen_liquifier_fixed_cost_per_kg_hour_year,
                m.hydrogen_liquifier_life_years,
                m.hydrogen_liquifier_mwh_per_kg,
                m.hydrogen_liquifier_variable_cost_per_kg,
                m.liquid_hydrogen_tank_capital_cost_per_kg,
                m.liquid_hydrogen_tank_life_years,
                m.liquid_hydrogen_tank_minimum_size_kg,
            )
        )
