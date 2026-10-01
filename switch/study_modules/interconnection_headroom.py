"""
Zonal interconnection (network-upgrade) headroom for Switch-USA-EDF.

Pipeline, method and data: interconnection_headroom/ (see its README and
docs/project_doc.html). Usage guide: Guides and documentation/interconnection_headroom.md.

New generation and storage in each load zone must fit inside the zone's
interconnection headroom. Each MW counts at a technology weight (the share of
the network one MW of nameplate occupies under interconnection-study
conditions), so gas uses headroom too. Headroom comes from three places:

  1. ic_initial_headroom_mw[z]           free headroom at the start (default 0:
                                          the first tranche is priced at today's
                                          marginal cost)
  2. retirement reuse                     ic_retirement_reuse_share x weighted
                                          existing capacity that has retired or
                                          been suspended by period p
  3. ICCapacity[z, tranche, p]           headroom bought from stepwise tranches
                                          with increasing $/MW (empirical network
                                          upgrades, plus policy tranches such as
                                          GETs or reconductoring)

Only *new* builds (NEW_GEN_BLD_YRS) count against headroom: existing and
predetermined capacity is already reflected in where each zone's curve starts.
Distributed generation (gen_is_distributed = 1) never counts.

The constraint applies only in "active" zones: zones with at least one tranche
or an ic_initial_headroom_mw value. With no ic_tranches.csv at all the module
does nothing, so it is safe to leave in modules.txt.

Myopic chains: prepare_next_stage.py writes ic_tranches.chained.<case>.csv with
each tranche's remaining MW, and pg_to_switch.py aliases it for later stages.

This replaces the distance-based "reinforcement" cost (and any flat intra-zone
adder) carried in gen_connect_cost_per_mw / capital costs. Remove those first,
or the cost is counted twice.

Inputs (all in inputs_dir):

  ic_tranches.csv (optional; module is inactive without it)
    IC_TRANCHE, ic_tranche_zone, ic_tranche_max_mw, ic_tranche_cost_per_mw,
    ic_tranche_available_year
  ic_weights.csv (optional)
    ic_key, ic_weight            matched against gen_tech first, then
                                 gen_energy_source. Unmatched storage gens use
                                 ic_storage_weight; anything else ic_default_weight.
  ic_params.csv (optional, one row)
    ic_retirement_reuse_share, ic_storage_weight, ic_default_weight,
    ic_asset_life_years
  ic_zone_params.csv (optional)
    LOAD_ZONE, ic_initial_headroom_mw

pg_to_switch.py writes ic_weights.csv keyed by every gen_tech in the case, so
keys always match. post_solve writes ic_gen_weights.csv to check what was applied.

Status: draft. Tested on the Switch 2.0.9 3zone_toy example; not yet run on a
full Switch-USA-PG case.
"""
import os

from pyomo.environ import (Constraint, Expression, NonNegativeReals, Param, Set,
                           Var, Any, value)

from switch_model.financials import capital_recovery_factor as crf

dependencies = (
    "switch_model.timescales",
    "switch_model.balancing.load_zones",
    "switch_model.financials",
    "switch_model.energy_sources.properties",
    "switch_model.generators.core.build",
)

def define_components(m):
    # ---- parameters -------------------------------------------------------
    m.ic_retirement_reuse_share = Param(within=NonNegativeReals, default=0.8)
    m.ic_storage_weight = Param(within=NonNegativeReals, default=0.5)
    m.ic_default_weight = Param(within=NonNegativeReals, default=1.0)
    m.ic_asset_life_years = Param(within=NonNegativeReals, default=40)
    m.ic_initial_headroom_mw = Param(m.LOAD_ZONES, within=NonNegativeReals, default=0.0)

    m.IC_TRANCHES = Set(dimen=1)
    m.ic_tranche_zone = Param(m.IC_TRANCHES, within=m.LOAD_ZONES)
    m.ic_tranche_max_mw = Param(m.IC_TRANCHES, within=NonNegativeReals)
    m.ic_tranche_cost_per_mw = Param(m.IC_TRANCHES, within=NonNegativeReals)
    m.ic_tranche_available_year = Param(m.IC_TRANCHES, within=NonNegativeReals, default=0)

    m.IC_KEYS = Set(dimen=1)
    m.ic_weight = Param(m.IC_KEYS, within=NonNegativeReals)

    def gen_ic_weight(m, g):
        if hasattr(m, "gen_is_distributed") and m.gen_is_distributed[g]:
            return 0.0
        if m.gen_tech[g] in m.IC_KEYS:
            return m.ic_weight[m.gen_tech[g]]
        if m.gen_energy_source[g] in m.IC_KEYS:
            return m.ic_weight[m.gen_energy_source[g]]
        if hasattr(m, "STORAGE_GENS") and g in m.STORAGE_GENS:
            return m.ic_storage_weight
        if m.gen_energy_source[g] == "Electricity":  # storage when the storage module isn't loaded
            return m.ic_storage_weight
        return m.ic_default_weight

    m.gen_ic_weight = Param(m.GENERATION_PROJECTS, within=NonNegativeReals,
                            initialize=gen_ic_weight)

    # ---- sets --------------------------------------------------------------
    m.IC_TRANCHES_IN_ZONE = Set(
        m.LOAD_ZONES, dimen=1,
        initialize=lambda m, z: [t for t in m.IC_TRANCHES if m.ic_tranche_zone[t] == z])
    m.IC_ZONES_WITH_INITIAL = Set(dimen=1, within=m.LOAD_ZONES)  # zones listed in ic_zone_params.csv
    m.IC_ACTIVE_ZONES = Set(
        dimen=1,
        initialize=lambda m: [z for z in m.LOAD_ZONES
                              if len(m.IC_TRANCHES_IN_ZONE[z]) > 0 or z in m.IC_ZONES_WITH_INITIAL])
    m.IC_TRANCHE_PERIODS = Set(
        dimen=2,
        initialize=lambda m: [(t, p) for t in m.IC_TRANCHES for p in m.PERIODS
                              if m.ic_tranche_available_year[t] <= m.period_start[p]])

    # existing (pre-horizon) vintages: their retirement or suspension frees headroom

    def existing_init(m):
        p0 = m.PERIODS.first()
        return [(g, y) for (g, y) in m.PREDETERMINED_GEN_BLD_YRS
                if m.gen_ic_weight[g] > 0 and y in m.BLD_YRS_FOR_GEN_PERIOD[g, p0]]

    m.IC_EXISTING_BLD_YRS = Set(dimen=2, initialize=existing_init)

    # ---- decisions --------------------------------------------------------
    m.ICBuild = Var(m.IC_TRANCHE_PERIODS, within=NonNegativeReals)

    m.ICCapacity = Expression(
        m.IC_TRANCHES, m.PERIODS,
        rule=lambda m, t, p: sum(m.ICBuild[t, pp] for pp in m.PERIODS
                                 if pp <= p and (t, pp) in m.IC_TRANCHE_PERIODS))

    m.IC_Tranche_Limit = Constraint(
        m.IC_TRANCHES, m.PERIODS,
        rule=lambda m, t, p: m.ICCapacity[t, p] <= m.ic_tranche_max_mw[t])

    # ---- headroom accounting ---------------------------------------------
    m.ICNewCapacityWeighted = Expression(
        m.LOAD_ZONES, m.PERIODS,
        rule=lambda m, z, p: sum(
            m.gen_ic_weight[g] * sum(
                m.BuildGen[g, y] - m.SuspendGen[g, y, p]
                for y in m.BLD_YRS_FOR_GEN_PERIOD[g, p] if (g, y) in m.NEW_GEN_BLD_YRS)
            for g in m.GENS_IN_ZONE[z] if m.gen_ic_weight[g] > 0))

    m.ICFreedHeadroom = Expression(
        m.LOAD_ZONES, m.PERIODS,
        rule=lambda m, z, p: sum(
            m.gen_ic_weight[g] * (
                m.build_gen_predetermined[g, y]
                - ((m.BuildGen[g, y] - m.SuspendGen[g, y, p])
                   if y in m.BLD_YRS_FOR_GEN_PERIOD[g, p] else 0))
            for (g, y) in m.IC_EXISTING_BLD_YRS if m.gen_load_zone[g] == z))

    m.IC_Headroom_Limit = Constraint(
        m.IC_ACTIVE_ZONES, m.PERIODS,
        rule=lambda m, z, p: (
            m.ICNewCapacityWeighted[z, p]
            <= m.ic_initial_headroom_mw[z]
            + m.ic_retirement_reuse_share * m.ICFreedHeadroom[z, p]
            + sum(m.ICCapacity[t, p] for t in m.IC_TRANCHES_IN_ZONE[z])))

    # ---- costs ------------------------------------------------------------
    m.ICAnnualCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.ICCapacity[t, p] * m.ic_tranche_cost_per_mw[t]
            * crf(m.interest_rate, m.ic_asset_life_years)
            for t in m.IC_TRANCHES))
    m.Cost_Components_Per_Period.append("ICAnnualCost")


def load_inputs(m, switch_data, inputs_dir):
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_tranches.csv"),
        optional=True,
        optional_params=["ic_tranche_available_year"],
        index=m.IC_TRANCHES,
        param=(m.ic_tranche_zone, m.ic_tranche_max_mw, m.ic_tranche_cost_per_mw,
               m.ic_tranche_available_year))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_weights.csv"),
        optional=True, index=m.IC_KEYS, param=(m.ic_weight,))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_params.csv"),
        optional=True,
        optional_params=["ic_retirement_reuse_share", "ic_storage_weight", "ic_default_weight",
                         "ic_asset_life_years"],
        param=(m.ic_retirement_reuse_share, m.ic_storage_weight, m.ic_default_weight,
               m.ic_asset_life_years))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_zone_params.csv"),
        optional=True, index=m.IC_ZONES_WITH_INITIAL, param=(m.ic_initial_headroom_mw,))


def post_solve(m, outdir):
    import pandas as pd
    if not len(m.IC_ACTIVE_ZONES):
        return
    rows = []
    for z in m.IC_ACTIVE_ZONES:
        for p in m.PERIODS:
            rows.append({
                "load_zone": z, "period": p,
                "new_capacity_mw_weighted": value(m.ICNewCapacityWeighted[z, p]),
                "freed_headroom_mw": value(m.ic_retirement_reuse_share * m.ICFreedHeadroom[z, p]),
                "tranche_mw": sum(value(m.ICCapacity[t, p]) for t in m.IC_TRANCHES_IN_ZONE[z]),
                "headroom_dual": (m.dual[m.IC_Headroom_Limit[z, p]]
                                  if hasattr(m, "dual") and m.IC_Headroom_Limit[z, p] in m.dual
                                  else None),
            })
    pd.DataFrame(rows).to_csv(os.path.join(outdir, "ic_headroom.csv"), index=False)
    pd.DataFrame([{"ic_tranche": t, "load_zone": m.ic_tranche_zone[t], "period": p,
                   "built_mw": value(m.ICCapacity[t, p]), "max_mw": value(m.ic_tranche_max_mw[t]),
                   "cost_per_mw": value(m.ic_tranche_cost_per_mw[t])}
                  for t in m.IC_TRANCHES for p in m.PERIODS]).to_csv(
        os.path.join(outdir, "ic_tranches_built.csv"), index=False)
    # weight actually applied to each project, so the key mapping can be checked
    pd.DataFrame([{"GENERATION_PROJECT": g, "gen_tech": m.gen_tech[g],
                   "gen_energy_source": m.gen_energy_source[g], "ic_weight": value(m.gen_ic_weight[g])}
                  for g in m.GENERATION_PROJECTS]).to_csv(
        os.path.join(outdir, "ic_gen_weights.csv"), index=False)
