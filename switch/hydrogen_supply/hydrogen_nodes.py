
"""
Defines hydrogen supply nodes parameters for the Switch model.
"""

import os
from pyomo.environ import *
from switch_model.reporting import write_table

dependencies = 'switch_model.timescales'

def define_arguments(argparser):
    argparser.add_argument(
        "--hydrogen-liquid-demand-portion",
        type=float,
        default=0.0,
        help="Portion of the hydrogen demand that has to be supplied by liquid hydrogen (values between 0 and 1).",
    )

def define_dynamic_lists(mod):
    """
    Liquid_Hydrogen_Zone_Injections, Liquid_Hydrogen_Zone_Withdrawals,
    Gaseous_Hydrogen_Zone_Injections and Gaseous_Hydrogen_Zone_Withdrawals are lists of
    components that contribute to zone level hydrogen supply balance equations.
    sum(Liquid_Hydrogen_Zone_Injections[z,t]) == sum(Liquid_Hydrogen_Zone_Withdrawals[z,t])
        for all z,t
    sum(Gaseous_Hydrogen_Zone_Injections[z,t]) == sum(Gaseous_Hydrogen_Zone_Withdrawals[z,t])
        for all z,t
    Other modules may append to either list, as long as the components they
    add are indexed by [zone, timepoint] and have units of kg. Other modules
    often include Expressions to summarize decision variables on a zonal basis.
    """
    mod.Liquid_Hydrogen_Zone_Injections = []
    mod.Liquid_Hydrogen_Zone_Withdrawals = []
    mod.Gaseous_Hydrogen_Zone_Injections = []
    mod.Gaseous_Hydrogen_Zone_Withdrawals = []


def define_components(m):
    """
    Augments a Pyomo abstract model object with sets and parameters that
    describe water zones and associated desalinated water balance equations. Unless
    otherwise stated, each set and parameter is mandatory.

    LOAD_ZONES_TIMEPOINTS is the cross product of load zones and timepoints, used for indexing. 
    It is assumed tht the Hydroen projects are connected to the same defien load zones and the withrawls
    of H2 are also done in this zones.

    zone_hydrogen_demand_kg[z,t] describes the hydrogen demand from
    each zone z and timepoint t. This will either go into the 
    LOAD_ZONES_Withdrawals hydrogen balance equations

    zone_liquid_hydrogen_demand_kg[z,t] describes the liquid hydrogen demand from
    each zone z and timepoint t. It is constructed based on the --hydrogen-liquid-demand-portion
    flag.

    zone_gaseous_hydrogen_demand_kg[z,t] describes the gaseous hydrogen demand from
    each zone z and timepoint t. It is constructed based on the --hydrogen-liquid-demand-portion
    flag.

    """
    m.LOAD_ZONES_TIMEPOINTS = Set(dimen=2,
        initialize=lambda m: m.LOAD_ZONES * m.TIMEPOINTS,
        doc="The cross product of water zones and timepoints, used for indexing.")
    
    liquid_hydrogen_demand_portion = m.options.hydrogen_liquid_demand_portion
    m.zone_hydrogen_demand_kg = Param(
        m.LOAD_ZONES_TIMEPOINTS,
        within=NonNegativeReals
    )
    m.min_data_check('zone_hydrogen_demand_kg')
    m.zone_liquid_hydrogen_demand_kg = Param(
        m.LOAD_ZONES_TIMEPOINTS,
        within=NonNegativeReals,
        initialize=lambda m,z,t: (
            m.zone_hydrogen_demand_kg[z, t] * liquid_hydrogen_demand_portion
        ) #if liquid_hydrogen_demand_portion > 0 else 0.0
    )
    m.zone_gaseous_hydrogen_demand_kg = Param(
        m.LOAD_ZONES_TIMEPOINTS,
        within=NonNegativeReals,
        initialize=lambda m,z,t: (
            m.zone_hydrogen_demand_kg[z, t] * (1-liquid_hydrogen_demand_portion)
        ) #if liquid_hydrogen_demand_portion > 0 else 0.0
    )
    
    m.Liquid_Hydrogen_Zone_Withdrawals.append('zone_liquid_hydrogen_demand_kg')
    m.Gaseous_Hydrogen_Zone_Withdrawals.append('zone_gaseous_hydrogen_demand_kg')
    
def define_dynamic_components(mod):
    """
    Zone_Liquid_Hydrogen_Balance[load_zone, timepoint] is a constraint that mandates
    conservation of mass in every load zone and timepoint. This constraint
    sums the model components in the lists Liquid_Hydrogen_Zone_Injections and
    Liquid_Hydrogen_Zone_Withdrawals - each of which is indexed by (z, t) and
    has units of kg - and ensures they are equal. The term tp_duration_hrs
    is factored out of the equation for brevity.

    Zone_Gaseous_Hydrogen_Balance[load_zone, timepoint] is a constraint that mandates
    conservation of mass in every load zone and timepoint. This constraint
    sums the model components in the lists Zone_Gaseous_Hydrogen_Injections and
    Zone_Gaseous_Hydrogen_Withdrawals - each of which is indexed by (z, t) and
    has units of kg - and ensures they are equal. The term tp_duration_hrs
    is factored out of the equation for brevity.
    """

    mod.Zone_Liquid_Hydrogen_Balance = Constraint(
        mod.LOAD_ZONES_TIMEPOINTS,
        rule=lambda m, z, t: (
            sum(
                getattr(m, component)[z, t]
                for component in m.Liquid_Hydrogen_Zone_Injections
            ) >= sum(
                getattr(m, component)[z, t]
                for component in m.Liquid_Hydrogen_Zone_Withdrawals)))
    
    mod.Zone_Gaseous_Hydrogen_Balance = Constraint(
        mod.LOAD_ZONES_TIMEPOINTS,
        rule=lambda m, z, t: (
            sum(
                getattr(m, component)[z, t]
                for component in m.Gaseous_Hydrogen_Zone_Injections
            ) >= sum(
                getattr(m, component)[z, t]
                for component in m.Gaseous_Hydrogen_Zone_Withdrawals)))


def load_inputs(m, switch_data, inputs_dir):
    """
    The following csv file is expected in the input directory. 
    Their index columns need to be on the left, but the data columns can
    be in any order. Extra columns will be ignored during import, and 
    optional columns can be dropped.

    hydrogen_demand.csv
        LOAD_ZONES,TIMEPOINT,zone_hydrogen_demand_kg

    """
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, 'hydrogen_demand.csv'),
        auto_select=True,
        param=(m.zone_hydrogen_demand_kg))


def post_solve(instance, outdir):
    """
    Export results.

    gaseous_hydrogen_demand_balance.csv and liquid_hydrogen_demand_balance.csv
    are a wide table of hydrogen balance components for every
    zone and timepoint. Each component registered with
    Liquid_Hydrogen_Zone_Injections, Liquid_Hydrogen_Zone_Withdrawals,
    Gaseous_Hydrogen_Zone_Injections and Gaseous_Hydrogen_Zone_Withdrawals
    will become a column.

    """
    write_table(
        instance, instance.LOAD_ZONES, instance.TIMEPOINTS,
        output_file=os.path.join(outdir, "gaseous_hydrogen_demand_balance.csv"),
        headings=("LOAD_ZONES", "timestamp",) + tuple(
            instance.Gaseous_Hydrogen_Zone_Injections +
            instance.Gaseous_Hydrogen_Zone_Withdrawals),
        values=lambda m, z, t: (z, m.tp_timestamp[t],) + tuple(
            getattr(m, component)[z, t]
            for component in (
                m.Gaseous_Hydrogen_Zone_Injections +
                m.Gaseous_Hydrogen_Zone_Withdrawals)))
    
    write_table(
        instance, instance.LOAD_ZONES, instance.TIMEPOINTS,
        output_file=os.path.join(outdir, "liquid_hydrogen_demand_balance.csv"),
        headings=("LOAD_ZONES", "timestamp",) + tuple(
            instance.Liquid_Hydrogen_Zone_Injections +
            instance.Liquid_Hydrogen_Zone_Withdrawals),
        values=lambda m, z, t: (z, m.tp_timestamp[t],) + tuple(
            getattr(m, component)[z, t]
            for component in (
                m.Liquid_Hydrogen_Zone_Injections +
                m.Liquid_Hydrogen_Zone_Withdrawals)))
