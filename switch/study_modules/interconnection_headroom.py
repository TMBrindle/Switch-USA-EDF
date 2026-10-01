"""
Zonal interconnection headroom with explicit network capacity, for Switch-USA-EDF.

Pipeline, method and data: interconnection_headroom/ (README, docs/project_doc.html).
Usage guide: Guides and documentation/interconnection_headroom.md.

Picture
-------
Each interconnection zone (IC zone; one per ReEDS BA, mapped to a Switch load zone) has
network capacity H (MW of intra-zonal transfer capability, starting from the NARIS 2024
proxy H0). How full the zone is, s = used / H, sets the marginal cost of connecting more
generation. The empirical cost curve (from LBNL network-upgrade costs) is a set of steps in
units of s: step k is w_k wide and costs c_k per MW of generation connected.

Deliberate reinforcement (GETs, reconductoring, new intra-zonal lines) adds MW to H. That
  * stretches every step: the MW available in step k is w_k x H, and
  * moves the zone back down the curve: existing use U0 now fills less of H, releasing up
    to s0 x (MW added) of headroom priced at the zone's starting marginal cost.
Both effects are linear in the MW added, so the model stays an LP. The approximation is exact
for small uprates and slightly conservative for large ones.

Constraint per load zone and period (sums over the IC zones in the load zone):

  new capacity (tech-weighted)  <=  initial headroom
                                   + reuse share x weighted existing capacity retired
                                   + sum_k ICStep[k]            (ICStep[k] <= w_k x H)
                                   + ICRelease                  (ICRelease <= s0 x uprate MW)

Costs: generation-side reactive upgrades (sum c_k x ICStep + release cost) and deliberate
uprates (MW added x $/MW), both annualised over ic_asset_life_years.

Reporting (post_solve):
  ic_spend.csv      overnight and annual $ by zone, period and type: reactive_upgrades and
                    uprate types per IC zone; spur and poi per load zone (new builds x the
                    ic_connect_components.csv parts of gen_connect_cost_per_mw, annualised like
                    the generator's capital cost; accounting only, already in the objective)
  ic_network.csv    network capacity: base, added by deliberate uprates, estimated added by
                    reactive upgrades (reactive spend / ic_reactive_cost_per_mw_network), %
  ic_headroom.csv   headroom used, freed, bought, and the constraint dual, by load zone
  ic_gen_weights.csv  weight applied to each project

Only new builds (NEW_GEN_BLD_YRS) count; distributed generation never counts. Load zones with
no IC zone are unconstrained, and with no ic_zones.csv the module does nothing.

Inputs (inputs_dir)
-------------------
ic_zones.csv            IC_ZONE, ic_zone_load_zone, ic_base_capacity_mw, ic_start_saturation,
                        ic_release_cost_per_mw
ic_tranches.csv         IC_TRANCHE, ic_tranche_zone, ic_tranche_width, ic_tranche_cost_per_mw,
                        ic_tranche_available_year (optional)
ic_uprates.csv          IC_UPRATE, ic_uprate_zone, ic_uprate_type, ic_uprate_max_mw,
                        ic_uprate_cost_per_mw, ic_uprate_available_year (optional)   [optional]
ic_weights.csv          ic_key, ic_weight (gen_tech first, then gen_energy_source) [optional]
ic_params.csv           ic_retirement_reuse_share, ic_storage_weight, ic_default_weight,
                        ic_asset_life_years, ic_reactive_cost_per_mw_network        [optional]
ic_zone_params.csv      LOAD_ZONE, ic_initial_headroom_mw                           [optional]
ic_connect_components.csv  GENERATION_PROJECT, spur_cost_per_mw, poi_cost_per_mw
                        (+ co2_pipeline_cost_per_mw): the parts of gen_connect_cost_per_mw, for
                        the spend report only (pg_to_switch, split_connect_costs)    [optional]

Status: draft. Tested on the Switch 2.0.9 3zone_toy example; not yet run on a full case.
"""
import os

from pyomo.environ import (Constraint, Expression, NonNegativeReals, Param, Set, Var, Any,
                           value)

from switch_model.financials import capital_recovery_factor as crf

dependencies = (
    "switch_model.timescales",
    "switch_model.balancing.load_zones",
    "switch_model.financials",
    "switch_model.energy_sources.properties",
    "switch_model.generators.core.build",
)


def define_components(m):
    # ---- general parameters ----------------------------------------------
    m.ic_retirement_reuse_share = Param(within=NonNegativeReals, default=0.8)
    m.ic_storage_weight = Param(within=NonNegativeReals, default=0.5)
    m.ic_default_weight = Param(within=NonNegativeReals, default=1.0)
    m.ic_asset_life_years = Param(within=NonNegativeReals, default=40)
    # engineering cost of network capacity added by reactive upgrades; used only to *report*
    # an estimate of capacity added by the empirical (generator-paid) curve
    m.ic_reactive_cost_per_mw_network = Param(within=NonNegativeReals, default=0.0)
    m.IC_ZONES_WITH_INITIAL = Set(dimen=1, within=m.LOAD_ZONES)
    # parts of gen_connect_cost_per_mw, reporting only
    m.IC_CONNECT_GENS = Set(dimen=1, within=m.GENERATION_PROJECTS)
    m.gen_ic_spur_cost_per_mw = Param(m.IC_CONNECT_GENS, within=NonNegativeReals, default=0.0)
    m.gen_ic_poi_cost_per_mw = Param(m.IC_CONNECT_GENS, within=NonNegativeReals, default=0.0)
    m.ic_initial_headroom_mw = Param(m.LOAD_ZONES, within=NonNegativeReals, default=0.0)

    # ---- interconnection zones -------------------------------------------
    m.IC_ZONES = Set(dimen=1)
    m.ic_zone_load_zone = Param(m.IC_ZONES, within=m.LOAD_ZONES)
    m.ic_base_capacity_mw = Param(m.IC_ZONES, within=NonNegativeReals)
    m.ic_start_saturation = Param(m.IC_ZONES, within=NonNegativeReals)
    m.ic_release_cost_per_mw = Param(m.IC_ZONES, within=NonNegativeReals)

    m.IC_TRANCHES = Set(dimen=1)
    m.ic_tranche_zone = Param(m.IC_TRANCHES, within=m.IC_ZONES)
    m.ic_tranche_width = Param(m.IC_TRANCHES, within=NonNegativeReals)  # saturation units
    m.ic_tranche_cost_per_mw = Param(m.IC_TRANCHES, within=NonNegativeReals)
    m.ic_tranche_available_year = Param(m.IC_TRANCHES, within=NonNegativeReals, default=0)

    m.IC_UPRATES = Set(dimen=1)
    m.ic_uprate_zone = Param(m.IC_UPRATES, within=m.IC_ZONES)
    m.ic_uprate_type = Param(m.IC_UPRATES, within=Any)
    m.ic_uprate_max_mw = Param(m.IC_UPRATES, within=NonNegativeReals)
    m.ic_uprate_cost_per_mw = Param(m.IC_UPRATES, within=NonNegativeReals)
    m.ic_uprate_available_year = Param(m.IC_UPRATES, within=NonNegativeReals, default=0)

    # ---- technology weights ----------------------------------------------
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

    # ---- index sets ----------------------------------------------------------
    m.IC_ZONES_IN_LOAD_ZONE = Set(
        m.LOAD_ZONES, dimen=1,
        initialize=lambda m, z: [i for i in m.IC_ZONES if m.ic_zone_load_zone[i] == z])
    m.IC_TRANCHES_IN_ZONE = Set(
        m.IC_ZONES, dimen=1,
        initialize=lambda m, i: [t for t in m.IC_TRANCHES if m.ic_tranche_zone[t] == i])
    m.IC_UPRATES_IN_ZONE = Set(
        m.IC_ZONES, dimen=1,
        initialize=lambda m, i: [u for u in m.IC_UPRATES if m.ic_uprate_zone[u] == i])
    m.IC_ACTIVE_LOAD_ZONES = Set(
        dimen=1,
        initialize=lambda m: [z for z in m.LOAD_ZONES
                              if len(m.IC_ZONES_IN_LOAD_ZONE[z]) > 0 or z in m.IC_ZONES_WITH_INITIAL])
    m.IC_TRANCHE_PERIODS = Set(
        dimen=2,
        initialize=lambda m: [(t, p) for t in m.IC_TRANCHES for p in m.PERIODS
                              if m.ic_tranche_available_year[t] <= m.period_start[p]])
    m.IC_UPRATE_PERIODS = Set(
        dimen=2,
        initialize=lambda m: [(u, p) for u in m.IC_UPRATES for p in m.PERIODS
                              if m.ic_uprate_available_year[u] <= m.period_start[p]])

    def existing_init(m):
        p0 = m.PERIODS.first()
        return [(g, y) for (g, y) in m.PREDETERMINED_GEN_BLD_YRS
                if m.gen_ic_weight[g] > 0 and y in m.BLD_YRS_FOR_GEN_PERIOD[g, p0]]

    m.IC_EXISTING_BLD_YRS = Set(dimen=2, initialize=existing_init)

    # ---- decisions: deliberate network uprates ----------------------------
    m.ICBuildUprate = Var(m.IC_UPRATE_PERIODS, within=NonNegativeReals)
    m.ICUprateCapacity = Expression(
        m.IC_UPRATES, m.PERIODS,
        rule=lambda m, u, p: sum(m.ICBuildUprate[u, pp] for pp in m.PERIODS
                                 if pp <= p and (u, pp) in m.IC_UPRATE_PERIODS))
    m.IC_Uprate_Limit = Constraint(
        m.IC_UPRATES, m.PERIODS,
        rule=lambda m, u, p: m.ICUprateCapacity[u, p] <= m.ic_uprate_max_mw[u])

    m.ICNetworkAdded = Expression(
        m.IC_ZONES, m.PERIODS,
        rule=lambda m, i, p: sum(m.ICUprateCapacity[u, p] for u in m.IC_UPRATES_IN_ZONE[i]))
    m.ICNetworkCapacity = Expression(
        m.IC_ZONES, m.PERIODS,
        rule=lambda m, i, p: m.ic_base_capacity_mw[i] + m.ICNetworkAdded[i, p])

    # ---- decisions: headroom bought along the cost curve ---------------------
    m.ICBuildStep = Var(m.IC_TRANCHE_PERIODS, within=NonNegativeReals)
    m.ICStep = Expression(
        m.IC_TRANCHES, m.PERIODS,
        rule=lambda m, t, p: sum(m.ICBuildStep[t, pp] for pp in m.PERIODS
                                 if pp <= p and (t, pp) in m.IC_TRANCHE_PERIODS))
    # step widths stretch with network capacity
    m.IC_Step_Width = Constraint(
        m.IC_TRANCHES, m.PERIODS,
        rule=lambda m, t, p: m.ICStep[t, p]
        <= m.ic_tranche_width[t] * m.ICNetworkCapacity[m.ic_tranche_zone[t], p])

    # headroom released because existing use fills less of a bigger network
    m.ICBuildRelease = Var(m.IC_ZONES, m.PERIODS, within=NonNegativeReals)
    m.ICRelease = Expression(
        m.IC_ZONES, m.PERIODS,
        rule=lambda m, i, p: sum(m.ICBuildRelease[i, pp] for pp in m.PERIODS if pp <= p))
    m.IC_Release_Limit = Constraint(
        m.IC_ZONES, m.PERIODS,
        rule=lambda m, i, p: m.ICRelease[i, p]
        <= m.ic_start_saturation[i] * m.ICNetworkAdded[i, p])

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

    m.ICHeadroomBought = Expression(
        m.LOAD_ZONES, m.PERIODS,
        rule=lambda m, z, p: sum(
            sum(m.ICStep[t, p] for t in m.IC_TRANCHES_IN_ZONE[i]) + m.ICRelease[i, p]
            for i in m.IC_ZONES_IN_LOAD_ZONE[z]))

    m.IC_Headroom_Limit = Constraint(
        m.IC_ACTIVE_LOAD_ZONES, m.PERIODS,
        rule=lambda m, z, p: m.ICNewCapacityWeighted[z, p]
        <= m.ic_initial_headroom_mw[z]
        + m.ic_retirement_reuse_share * m.ICFreedHeadroom[z, p]
        + m.ICHeadroomBought[z, p])

    # ---- costs (overnight, cumulative to period p) -------------------------
    m.ICReactiveOvernight = Expression(
        m.IC_ZONES, m.PERIODS,
        rule=lambda m, i, p: sum(m.ICStep[t, p] * m.ic_tranche_cost_per_mw[t]
                                 for t in m.IC_TRANCHES_IN_ZONE[i])
        + m.ICRelease[i, p] * m.ic_release_cost_per_mw[i])
    m.ICUprateOvernight = Expression(
        m.IC_ZONES, m.PERIODS,
        rule=lambda m, i, p: sum(m.ICUprateCapacity[u, p] * m.ic_uprate_cost_per_mw[u]
                                 for u in m.IC_UPRATES_IN_ZONE[i]))
    m.ICAnnualCost = Expression(
        m.PERIODS,
        rule=lambda m, p: crf(m.interest_rate, m.ic_asset_life_years) * sum(
            m.ICReactiveOvernight[i, p] + m.ICUprateOvernight[i, p] for i in m.IC_ZONES))
    m.Cost_Components_Per_Period.append("ICAnnualCost")


def load_inputs(m, switch_data, inputs_dir):
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_zones.csv"),
        optional=True, index=m.IC_ZONES,
        param=(m.ic_zone_load_zone, m.ic_base_capacity_mw, m.ic_start_saturation,
               m.ic_release_cost_per_mw))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_tranches.csv"),
        optional=True, optional_params=["ic_tranche_available_year"], index=m.IC_TRANCHES,
        param=(m.ic_tranche_zone, m.ic_tranche_width, m.ic_tranche_cost_per_mw,
               m.ic_tranche_available_year))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_uprates.csv"),
        optional=True, optional_params=["ic_uprate_available_year"], index=m.IC_UPRATES,
        param=(m.ic_uprate_zone, m.ic_uprate_type, m.ic_uprate_max_mw, m.ic_uprate_cost_per_mw,
               m.ic_uprate_available_year))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_weights.csv"),
        optional=True, index=m.IC_KEYS, param=(m.ic_weight,))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_params.csv"),
        optional=True,
        optional_params=["ic_retirement_reuse_share", "ic_storage_weight", "ic_default_weight",
                         "ic_asset_life_years", "ic_reactive_cost_per_mw_network"],
        param=(m.ic_retirement_reuse_share, m.ic_storage_weight, m.ic_default_weight,
               m.ic_asset_life_years, m.ic_reactive_cost_per_mw_network))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_connect_components.csv"),
        optional=True, index=m.IC_CONNECT_GENS, select=("GENERATION_PROJECT", "spur_cost_per_mw", "poi_cost_per_mw"),
        param=(m.gen_ic_spur_cost_per_mw, m.gen_ic_poi_cost_per_mw))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_zone_params.csv"),
        optional=True, index=m.IC_ZONES_WITH_INITIAL, param=(m.ic_initial_headroom_mw,))


def connect_spend(m):
    """spur and poi rows for ic_spend.csv: new builds x the connection-cost parts, by load zone and
    build period. Annualised over gen_max_age, as gen_capital_cost_annual does."""
    rows = {}
    life = m.gen_max_age
    for g, y in m.NEW_GEN_BLD_YRS:
        if g not in m.IC_CONNECT_GENS:
            continue
        mw = value(m.BuildGen[g, y])
        if mw <= 1e-9:
            continue
        f = value(crf(m.interest_rate, life[g]))
        for kind, cost in (("spur", m.gen_ic_spur_cost_per_mw[g]), ("poi", m.gen_ic_poi_cost_per_mw[g])):
            r = rows.setdefault((m.gen_load_zone[g], y, kind), [0.0, 0.0, 0.0])
            r[0] += mw * value(cost)
            r[1] += mw * value(cost) * f
            r[2] += mw
    return [{"ic_zone": None, "load_zone": z, "period": y, "type": kind, "overnight_cost": c,
             "annual_cost": a, "network_mw_added": None, "generation_mw_enabled": None,
             "generation_mw_connected": mw, "network_mw_per_generation_mw": None}
            for (z, y, kind), (c, a, mw) in sorted(rows.items())]


def post_solve(m, outdir):
    import pandas as pd

    if not len(m.IC_ZONES) and not len(m.IC_ZONES_WITH_INITIAL):
        return
    ann = value(crf(m.interest_rate, m.ic_asset_life_years))
    k_react = value(m.ic_reactive_cost_per_mw_network)

    spend, network = [], []
    for i in m.IC_ZONES:
        z = m.ic_zone_load_zone[i]
        h0 = value(m.ic_base_capacity_mw[i])
        for p in m.PERIODS:
            react = value(m.ICReactiveOvernight[i, p])
            steps = sum(value(m.ICStep[t, p]) for t in m.IC_TRANCHES_IN_ZONE[i])
            rel = value(m.ICRelease[i, p])
            react_mw = (react / k_react) if k_react > 0 else None
            spend.append({"ic_zone": i, "load_zone": z, "period": p, "type": "reactive_upgrades",
                          "overnight_cost": react, "annual_cost": react * ann,
                          "network_mw_added": react_mw,
                          "generation_mw_enabled": steps + rel,
                          # sanity check on the estimate: network MW implied per MW of generation
                          # enabled. Values well above ~1-2 mean the curve's cost reflects more
                          # than steel (process, local constraints) or is mis-calibrated.
                          "network_mw_per_generation_mw": (react_mw / (steps + rel))
                          if react_mw is not None and steps + rel > 1e-9 else None})
            by_type = {}
            for u in m.IC_UPRATES_IN_ZONE[i]:
                ty = m.ic_uprate_type[u]
                mw = value(m.ICUprateCapacity[u, p])
                c = mw * value(m.ic_uprate_cost_per_mw[u])
                d = by_type.setdefault(ty, [0.0, 0.0])
                d[0] += c
                d[1] += mw
            for ty, (c, mw) in by_type.items():
                spend.append({"ic_zone": i, "load_zone": z, "period": p, "type": ty,
                              "overnight_cost": c, "annual_cost": c * ann,
                              "network_mw_added": mw, "generation_mw_enabled": None,
                              "network_mw_per_generation_mw": None})
            added = value(m.ICNetworkAdded[i, p])
            network.append({
                "ic_zone": i, "load_zone": z, "period": p,
                "base_capacity_mw": h0,
                "deliberate_mw_added": added,
                "reactive_mw_added_est": react_mw,
                "total_mw_added_est": added + (react_mw or 0.0),
                "pct_increase_deliberate": 100 * added / h0 if h0 else None,
                "pct_increase_total_est": 100 * (added + (react_mw or 0.0)) / h0 if h0 else None,
                "headroom_from_curve_mw": steps,
                "headroom_released_mw": rel,
            })
    spend += connect_spend(m)
    pd.DataFrame(spend).to_csv(os.path.join(outdir, "ic_spend.csv"), index=False)
    pd.DataFrame(network).to_csv(os.path.join(outdir, "ic_network.csv"), index=False)

    rows = []
    for z in m.IC_ACTIVE_LOAD_ZONES:
        for p in m.PERIODS:
            c = m.IC_Headroom_Limit[z, p]
            rows.append({
                "load_zone": z, "period": p,
                "new_capacity_mw_weighted": value(m.ICNewCapacityWeighted[z, p]),
                "freed_headroom_mw": value(m.ic_retirement_reuse_share * m.ICFreedHeadroom[z, p]),
                "headroom_bought_mw": value(m.ICHeadroomBought[z, p]),
                "initial_headroom_mw": value(m.ic_initial_headroom_mw[z]),
                "headroom_dual": m.dual[c] if hasattr(m, "dual") and c in m.dual else None,
            })
    pd.DataFrame(rows).to_csv(os.path.join(outdir, "ic_headroom.csv"), index=False)

    # remaining state, for chaining myopic stages (prepare_next_stage.chain_ic_inputs)
    last = m.PERIODS.last()
    pd.DataFrame([{"ic_tranche": t, "ic_zone": m.ic_tranche_zone[t], "period": last,
                   "used_mw": value(m.ICStep[t, last])} for t in m.IC_TRANCHES]).to_csv(
        os.path.join(outdir, "ic_tranches_built.csv"), index=False)
    pd.DataFrame([{"ic_uprate": u, "ic_zone": m.ic_uprate_zone[u], "period": last,
                   "built_mw": value(m.ICUprateCapacity[u, last])} for u in m.IC_UPRATES]).to_csv(
        os.path.join(outdir, "ic_uprates_built.csv"), index=False)
    pd.DataFrame([{"ic_zone": i, "period": last, "released_mw": value(m.ICRelease[i, last])}
                  for i in m.IC_ZONES]).to_csv(os.path.join(outdir, "ic_release_built.csv"), index=False)

    pd.DataFrame([{"GENERATION_PROJECT": g, "gen_tech": m.gen_tech[g],
                   "gen_energy_source": m.gen_energy_source[g], "ic_weight": value(m.gen_ic_weight[g])}
                  for g in m.GENERATION_PROJECTS]).to_csv(
        os.path.join(outdir, "ic_gen_weights.csv"), index=False)
