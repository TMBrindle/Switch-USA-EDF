from __future__ import division
import os
from pyomo.environ import *
from switch_model.financials import capital_recovery_factor as crf
from switch_model.reporting import write_table

dependencies = 'switch_model.timescales', 'hydrogen_supply.hydrogen_nodes'

def define_arguments(argparser):
    argparser.add_argument('--no-blue-hydrogen', action='store_true', default=False,
        help="Don't allow construction of any hydrogen infrastructure."
    )

def define_components(m):
    if not m.options.no_hydrogen:
        define_blue_hydrogen_components(m)

def define_blue_hydrogen_components(m):

    #Sets of blue hydrogen projects
    m.BLUE_HYDROGEN_PROJECTS = Set(dimen=1)

    # Blue Hydrogen Process details
    m.blue_hydrogen_load_zone = Param(m.BLUE_HYDROGEN_PROJECTS, within=Any)
    m.blue_hydrogen_infrastructure_capital_cost_per_kg_per_hr = Param(m.BLUE_HYDROGEN_PROJECTS, within=NonNegativeReals)
    m.blue_hydrogen_infrastructure_fixed_cost_per_kg_year = Param(m.BLUE_HYDROGEN_PROJECTS,default=0.0)
    m.blue_hydrogen_infrastructure_variable_cost_per_kg = Param(m.BLUE_HYDROGEN_PROJECTS,default=0.0)  # assumed to include any refurbishment needed
    m.blue_hydrogen_infrastructure_mwh_per_kg = Param(m.BLUE_HYDROGEN_PROJECTS, within=NonNegativeReals) # assumed to deliver H2 at enough pressure for liquifier and daily buffering
    m.blue_hydrogen_fuel_source = Param(
        m.BLUE_HYDROGEN_PROJECTS,validate=lambda m, val, g: val in m.ENERGY_SOURCES or val == "multiple",
        within=Any)
    m.blue_hydrogen_infrastructure_fuel_consumption_mmbtu_per_kg = Param(m.BLUE_HYDROGEN_PROJECTS, within=NonNegativeReals)
    m.blue_hydrogen_infrastructure_life_years = Param(m.BLUE_HYDROGEN_PROJECTS, within=NonNegativeReals)
    m.blue_hydrogen_emissions_tonCO2_per_kg = Param(m.BLUE_HYDROGEN_PROJECTS, within=NonNegativeReals)
    m.blue_hydrogen_CCS_efficiency = Param(m.BLUE_HYDROGEN_PROJECTS, default=0.0)
    m.BuildBlueHydrogenInfrastructureKg = Var(m.BLUE_HYDROGEN_PROJECTS, m.PERIODS, within=NonNegativeReals)
    m.BlueHydrogenInfrastructureCapacityKg = Expression(m.BLUE_HYDROGEN_PROJECTS, m.PERIODS, rule=lambda m, h, p:
        sum(m.BuildBlueHydrogenInfrastructureKg[h, p_] for p_ in m.CURRENT_AND_PRIOR_PERIODS_FOR_PERIOD[p]))
    m.BlueHydrogenInfrastructureProductionKg = Var(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, within=NonNegativeReals)
    m.BlueHydrogenInfrastructureProductionSupplyKg = Var(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, within=NonNegativeReals)
    m.BlueHydrogenInfrastructureProductionLiquifyKg = Var(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, within=NonNegativeReals)

    #Blue Hydrogen Infrastructure Relation
    m.Blue_Hydrogen_Infrastructure_Relation = Constraint(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, rule=lambda m, h, tp:
        m.BlueHydrogenInfrastructureProductionKg[h, tp] ==  m.BlueHydrogenInfrastructureProductionSupplyKg[h,tp] +
         m.BlueHydrogenInfrastructureProductionLiquifyKg[h, tp]
    )
    #Blue Hydrogen Production maximum production
    m.Max_Production_Blue_Hydrogen = Constraint(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, rule=lambda m, h, tp:
        m.BlueHydrogenInfrastructureProductionKg[h, tp] <= m.BlueHydrogenInfrastructureCapacityKg[h, m.tp_period[tp]]
    )


    # note: we assume there is a gaseous hydrogen storage tank that is big enough to buffer
    # daily production, storage and withdrawals of hydrogen, but we don't include a cost
    # for this (because it will be negligible compared to the rest of the costs)
    # This allows the system to do some intra-day arbitrage without going all the way to liquification

    # liquifier details
    m.blue_hydrogen_liquifier_capital_cost_per_kg_per_hour = Param(m.BLUE_HYDROGEN_PROJECTS)
    m.blue_hydrogen_liquifier_fixed_cost_per_kg_hour_year = Param(m.BLUE_HYDROGEN_PROJECTS,default=0.0)
    m.blue_hydrogen_liquifier_variable_cost_per_kg = Param(m.BLUE_HYDROGEN_PROJECTS,default=0.0)
    m.blue_hydrogen_liquifier_mwh_per_kg = Param(m.BLUE_HYDROGEN_PROJECTS)
    m.blue_hydrogen_liquifier_life_years = Param(m.BLUE_HYDROGEN_PROJECTS)
    m.BuildBlueLiquifierKgPerHour = Var(m.BLUE_HYDROGEN_PROJECTS, m.PERIODS, within=NonNegativeReals) 
    m.BlueLiquifierCapacityKgPerHour = Expression(m.BLUE_HYDROGEN_PROJECTS, m.PERIODS, rule=lambda m, h, p:
        sum(m.BuildBlueLiquifierKgPerHour[h, p_] for p_ in m.CURRENT_AND_PRIOR_PERIODS_FOR_PERIOD[p]))
    m.BlueHydrogenProductionKgPerHour = Var(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, within=NonNegativeReals)
    m.BlueHydrogenProductionKgPerHourToSupply = Var(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, within=NonNegativeReals)
    m.LiquifyBlueHydrogenKgPerHourToStorage = Var(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, within=NonNegativeReals)
    m.LiquifyBlueHydrogenKgPerHourToSupply = Var(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, within=NonNegativeReals)

    m.BlueHydrogenProductioRelation = Constraint(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, rule=lambda m, h, t:
        m.BlueHydrogenProductionKgPerHour[h, t] == m.BlueHydrogenInfrastructureProductionLiquifyKg[h,t] + m.BlueHydrogenProductionKgPerHourToSupply[h,t]
    )
    m.LiquifyBlueHydrogenRelation = Constraint(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, rule=lambda m, h, t:
        m.BlueHydrogenInfrastructureProductionLiquifyKg[h, t] == m.LiquifyBlueHydrogenKgPerHourToStorage[h,t] + m.LiquifyBlueHydrogenKgPerHourToSupply[h,t]
    )


    # storage tank details
    m.liquid_blue_hydrogen_tank_capital_cost_per_kg = Param(m.BLUE_HYDROGEN_PROJECTS)
    m.liquid_blue_hydrogen_tank_minimum_size_kg = Param(m.BLUE_HYDROGEN_PROJECTS, default=0.0)
    m.liquid_blue_hydrogen_tank_life_years = Param(m.BLUE_HYDROGEN_PROJECTS)
    m.BuildLiquidBlueHydrogenTankKg = Var(m.BLUE_HYDROGEN_PROJECTS, m.PERIODS, within=NonNegativeReals) # in kg
    m.LiquidBlueHydrogenTankCapacityKg = Expression(m.BLUE_HYDROGEN_PROJECTS, m.PERIODS, rule=lambda m, h, p:
        sum(m.BuildLiquidBlueHydrogenTankKg[h, p_] for p_ in m.CURRENT_AND_PRIOR_PERIODS_FOR_PERIOD[p]))
    m.StoredLiquidBlueHydrogenKg = Var(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, within=NonNegativeReals)
    m.WithdrawLiquidBlueHydrogenKg = Var(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, within=NonNegativeReals)
    #Border conditions of the tank: first tp of period should be empty
    m.Blue_Hydrogen_Tank_Initial_Condition = Constraint(m.BLUE_HYDROGEN_PROJECTS,  m.TIMEPOINTS, rule=lambda m, h, tp:
        m.StoredLiquidBlueHydrogenKg[h,  m.TPS_IN_PERIOD[m.tp_period[tp]].first()] ==  0
    )
    # note: we assume the system will be large enough to neglect boil-off

    # hydrogen mass balances
    m.Blue_Hydrogen_Conservation_of_Mass_Hourly = Constraint(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, rule=lambda m, h, tp:
        m.StoredLiquidBlueHydrogenKg[h, tp] ==  m.StoredLiquidBlueHydrogenKg[h, m.tp_previous[tp]] + m.LiquifyBlueHydrogenKgPerHourToStorage[h, tp] - m.WithdrawLiquidBlueHydrogenKg[h, tp]
    )

    #Total hydrogen production details: Gaseous and Liquid
    m.LiquidBlueHydrogenProductionKgPerHour = Expression(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=lambda m, z, t: (
            sum(
                m.WithdrawLiquidBlueHydrogenKg[h, t] + m.LiquifyBlueHydrogenKgPerHourToSupply[h,t]
                for h in m.BLUE_HYDROGEN_PROJECTS if m.blue_hydrogen_load_zone[h] == z
            )
        )
    )
    m.GaseousBlueHydrogenProductionKgPerHour = Expression(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=lambda m, z, t: (
            sum(
                m.BlueHydrogenInfrastructureProductionSupplyKg[h, t]
                for h in m.BLUE_HYDROGEN_PROJECTS if m.blue_hydrogen_load_zone[h] == z
            )
        )
    )
    
    #We add the total hydrogen production to the injections
    m.Liquid_Hydrogen_Zone_Injections.append('LiquidBlueHydrogenProductionKgPerHour')
    m.Gaseous_Hydrogen_Zone_Injections.append('GaseousBlueHydrogenProductionKgPerHour')

    # limits on equipment
    m.Max_Run_Liquifier_Blue = Constraint(m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS, rule=lambda m, h, t:
        m.BlueHydrogenInfrastructureProductionLiquifyKg[h, t] <= m.BlueLiquifierCapacityKgPerHour[h, m.tp_period[t]])

    # add electricity consumption and production to the zonal energy balance
    m.EnergyConsumtionBlueHydrogenMW = Expression(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=lambda m, z, t: (
            sum(
                m.BlueHydrogenInfrastructureProductionKg[h, t] * m.blue_hydrogen_infrastructure_mwh_per_kg[h]
                + m.LiquifyBlueHydrogenKgPerHourToStorage[h,t] * m.blue_hydrogen_liquifier_mwh_per_kg[h]
                for h in m.BLUE_HYDROGEN_PROJECTS if m.blue_hydrogen_load_zone[h] == z
            )
        )
    )
    m.Zone_Power_Withdrawals.append('EnergyConsumtionBlueHydrogenMW')

    #Fuel consumption for the blue hydrogen process
    m.BlueHydrogenFuelConsumptionMMBTU = Expression(
        m.BLUE_HYDROGEN_PROJECTS, m.TIMEPOINTS,
        rule=lambda m, h, t: (
            m.BlueHydrogenInfrastructureProductionKg[h, t] * m.blue_hydrogen_infrastructure_fuel_consumption_mmbtu_per_kg[h]
        )
    )

    # add costs to the model
    m.BlueHydrogenVariableCost = Expression(m.TIMEPOINTS, rule=lambda m, t:
        sum(
            m.BlueHydrogenInfrastructureProductionKg[h, t] * m.blue_hydrogen_infrastructure_variable_cost_per_kg[h]
            + m.BlueHydrogenInfrastructureProductionLiquifyKg[h, t] * m.blue_hydrogen_liquifier_variable_cost_per_kg[h]
            + m.BlueHydrogenFuelConsumptionMMBTU[h,t] * m.fuel_cost[m.blue_hydrogen_load_zone[h], m.blue_hydrogen_fuel_source[h], m.tp_period[t]]
            for h in m.BLUE_HYDROGEN_PROJECTS
        )
    )
    m.BlueHydrogenFixedCostAnnual = Expression(m.PERIODS, rule=lambda m, p:
        sum(
            m.BlueHydrogenInfrastructureCapacityKg[h, p] * (
                m.blue_hydrogen_infrastructure_capital_cost_per_kg_per_hr[h] * crf(m.interest_rate, m.blue_hydrogen_infrastructure_life_years[h])
                + m.blue_hydrogen_infrastructure_fixed_cost_per_kg_year[h])
            + m.BlueLiquifierCapacityKgPerHour[h, p] * (
                m.blue_hydrogen_liquifier_capital_cost_per_kg_per_hour[h] * crf(m.interest_rate, m.blue_hydrogen_liquifier_life_years[h])
                + m.blue_hydrogen_liquifier_fixed_cost_per_kg_hour_year[h])
            + m.LiquidBlueHydrogenTankCapacityKg[h, p] * (
                m.liquid_blue_hydrogen_tank_capital_cost_per_kg[h] * crf(m.interest_rate, m.liquid_blue_hydrogen_tank_life_years[h]))
            for h in m.BLUE_HYDROGEN_PROJECTS
        )
    )
    m.Cost_Components_Per_TP.append('BlueHydrogenVariableCost')
    m.Cost_Components_Per_Period.append('BlueHydrogenFixedCostAnnual')

    # Blue Hydrogen annual emissions (currently only used for reporting)
    # TODO: Implement emissions contraints
    m.Blue_Hydrogen_Annual_Amissions = Expression(
        m.PERIODS, rule= lambda m, p: sum(
            m.BlueHydrogenInfrastructureProductionKg[h,t]
            * m.blue_hydrogen_emissions_tonCO2_per_kg[h] *
            (
                1 - m.blue_hydrogen_CCS_efficiency[h]

            )
            for t in m.TPS_IN_PERIOD[p]
            for h in m.BLUE_HYDROGEN_PROJECTS
        ),
    )


def load_inputs(m, switch_data, inputs_dir):
    """
    Import hydrogen data from a .csv file.
    TODO: change this to allow multiple storage technologies.
    """
    if not m.options.no_hydrogen:
        switch_data.load_aug(
            filename=os.path.join(inputs_dir, 'blue_hydrogen_infrastructure.csv'),
            optional=False, auto_select=True,
             index=m.BLUE_HYDROGEN_PROJECTS,
            param=(
                m.blue_hydrogen_load_zone,
                m.blue_hydrogen_infrastructure_capital_cost_per_kg_per_hr,
                m.blue_hydrogen_infrastructure_fixed_cost_per_kg_year,
                m.blue_hydrogen_infrastructure_variable_cost_per_kg,
                m.blue_hydrogen_infrastructure_mwh_per_kg,
                m.blue_hydrogen_fuel_source,
                m.blue_hydrogen_infrastructure_fuel_consumption_mmbtu_per_kg,
                m.blue_hydrogen_infrastructure_life_years,
                m.blue_hydrogen_emissions_tonCO2_per_kg,
                m.blue_hydrogen_CCS_efficiency,
                m.blue_hydrogen_liquifier_capital_cost_per_kg_per_hour,
                m.blue_hydrogen_liquifier_fixed_cost_per_kg_hour_year,
                m.blue_hydrogen_liquifier_variable_cost_per_kg,
                m.blue_hydrogen_liquifier_mwh_per_kg,
                m.blue_hydrogen_liquifier_life_years,
                m.liquid_blue_hydrogen_tank_capital_cost_per_kg,
                m.liquid_blue_hydrogen_tank_minimum_size_kg,
                m.liquid_blue_hydrogen_tank_life_years,
            )
        )


def post_solve(model, outdir):
    def get_row(model, period):
        row = [period, model.AnnualEmissions[period],
               model.Blue_Hydrogen_Annual_Amissions[period],
               model.AnnualEmissions[period] + model.Blue_Hydrogen_Annual_Amissions[period]]
        return row

    write_table(
        model, model.PERIODS,
        output_file=os.path.join(outdir, "blue_h2_emissions.csv"),
        headings=("PERIOD", "AnnualEmissions_tCO2_per_yr", "Blue_Hydrogen_Annual_Amissions_tCO2_per_yr", 
                  "TotalEmissions_tCO2_per_year"),
        values=get_row)