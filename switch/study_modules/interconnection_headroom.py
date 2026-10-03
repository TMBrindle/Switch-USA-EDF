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

Deliberate reinforcement comes in two modes (ic_uprate_mode):

  stretch (GETs and advanced-conductor reconductoring when the pipeline's atts_mode is stretch;
  conventional reinforcement when its reinforcement_mode is stretch) adds MW to H. That
  * stretches every step: the MW available in step k is w_k x H, and
  * moves the zone back down the curve: existing use U0 now fills less of H, releasing up
    to s0 x (MW added) of headroom priced at the zone's starting marginal cost.
  Both effects are linear in the MW added, so the model stays an LP. The approximation is exact
  for small uprates and slightly conservative for large ones.

  host (the pipeline default for GETs, advanced-conductor reconductoring and conventional
  reinforcement) adds weighted MW of headroom directly, at the uprate's own cost per MW of generation
  hosted (for conventional reinforcement, ReEDS's reinforcement cost: delivering a MW of new
  generation to the zone centre). It does not change H, stretch the steps or release headroom, and
  generation it hosts pays no empirical step or release cost on top.

Constraint per load zone and period (sums over the IC zones in the load zone):

  new capacity (tech-weighted)  <=  initial headroom
                                   + reuse share x weighted existing capacity retired
                                   + sum_k ICStep[k]            (ICStep[k] <= w_k x H)
                                   + ICRelease                  (ICRelease <= s0 x stretch-uprate MW)
                                   + hosted MW                  (host uprates + ic_hosted_headroom_mw)

Costs: generation-side reactive upgrades (sum c_k x ICStep + release cost) and deliberate
uprates (MW added x $/MW), both annualised over ic_asset_life_years.

Reporting (post_solve):
  ic_spend.csv      overnight and annual $ by zone, period and type
  ic_network.csv    network capacity: base; added by stretch uprates (deliberate_mw_added);
                    estimated added by reactive upgrades (reactive spend /
                    ic_reactive_cost_per_mw_network); headroom hosted by host uprates
                    (hosted_mw) and the network capacity it implies,
                    hosted_network_mw_implied = hosted MW / the zone's curve-end saturation (s0 + sum
                    of step widths: weighted generation per MW of network at the end of the
                    empirical curve); %
  ic_spend.csv rows are by type (reactive_upgrades and each uprate type, so conventional
                    reinforcement (conv_reinforcement) spend is its own row; for host uprates
                    generation_mw_enabled = hosted MW)
  ic_headroom.csv   headroom used, freed, bought, and the constraint dual, by load zone
  ic_gen_weights.csv  weight applied to each project

Only new builds (NEW_GEN_BLD_YRS) count; distributed generation never counts. Load zones with
no IC zone are unconstrained, and with no ic_zones.csv the module does nothing.

Inputs (inputs_dir)
-------------------
ic_zones.csv            IC_ZONE, ic_zone_load_zone, ic_base_capacity_mw, ic_start_saturation,
                        ic_release_cost_per_mw, ic_hosted_headroom_mw (optional: headroom on new
                        lines built in earlier myopic stages and not yet used; free)
ic_tranches.csv         IC_TRANCHE, ic_tranche_zone, ic_tranche_width, ic_tranche_cost_per_mw,
                        ic_tranche_available_year (optional)
ic_uprates.csv          IC_UPRATE, ic_uprate_zone, ic_uprate_type, ic_uprate_max_mw,
                        ic_uprate_cost_per_mw, ic_uprate_available_year (optional),
                        ic_uprate_mode (optional: stretch (default) | host)          [optional]
ic_weights.csv          ic_key, ic_weight (gen_tech first, then gen_energy_source) [optional]
ic_params.csv           ic_retirement_reuse_share, ic_storage_weight, ic_default_weight,
                        ic_asset_life_years, ic_reactive_cost_per_mw_network,
                        ic_slack_cost_per_mw (diagnostic slack on the headroom limit, $/MW;
                        absent or "." = hard constraint)                           [optional]
ic_zone_params.csv      LOAD_ZONE, ic_initial_headroom_mw                           [optional]

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


def ic_slack_cost(m):
    """$/MW cost of the diagnostic headroom slack, or None if slack is off (absent, "." or < 0)."""
    v = value(m.ic_slack_cost_per_mw)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def define_components(m):
    # ---- general parameters ----------------------------------------------
    m.ic_retirement_reuse_share = Param(within=NonNegativeReals, default=0.8)
    m.ic_storage_weight = Param(within=NonNegativeReals, default=0.5)
    m.ic_default_weight = Param(within=NonNegativeReals, default=1.0)
    m.ic_asset_life_years = Param(within=NonNegativeReals, default=40)
    # engineering cost of network capacity added by reactive upgrades; used only to *report*
    # an estimate of capacity added by the empirical (generator-paid) curve
    m.ic_reactive_cost_per_mw_network = Param(within=NonNegativeReals, default=0.0)
    # diagnostic slack on the headroom limit ($/MW overnight); absent or "." means no slack, i.e.
    # a hard constraint ("." reaches a scalar param as a literal string, hence within=Any)
    m.ic_slack_cost_per_mw = Param(within=Any, default=".")
    m.IC_ZONES_WITH_INITIAL = Set(dimen=1, within=m.LOAD_ZONES)
    m.ic_initial_headroom_mw = Param(m.LOAD_ZONES, within=NonNegativeReals, default=0.0)

    # ---- interconnection zones -------------------------------------------
    m.IC_ZONES = Set(dimen=1)
    m.ic_zone_load_zone = Param(m.IC_ZONES, within=m.LOAD_ZONES)
    m.ic_base_capacity_mw = Param(m.IC_ZONES, within=NonNegativeReals)
    m.ic_start_saturation = Param(m.IC_ZONES, within=NonNegativeReals)
    m.ic_release_cost_per_mw = Param(m.IC_ZONES, within=NonNegativeReals)
    m.ic_hosted_headroom_mw = Param(m.IC_ZONES, within=NonNegativeReals, default=0.0)

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
    m.ic_uprate_mode = Param(m.IC_UPRATES, within=Any, default="stretch",
                             validate=lambda m, v, u: v in ("stretch", "host"))

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
    m.IC_STRETCH_UPRATES_IN_ZONE = Set(
        m.IC_ZONES, dimen=1,
        initialize=lambda m, i: [u for u in m.IC_UPRATES_IN_ZONE[i] if m.ic_uprate_mode[u] == "stretch"])
    m.IC_HOST_UPRATES_IN_ZONE = Set(
        m.IC_ZONES, dimen=1,
        initialize=lambda m, i: [u for u in m.IC_UPRATES_IN_ZONE[i] if m.ic_uprate_mode[u] == "host"])
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
        rule=lambda m, i, p: sum(m.ICUprateCapacity[u, p] for u in m.IC_STRETCH_UPRATES_IN_ZONE[i]))
    # headroom (weighted MW) hosted directly by host-mode uprates, plus any carried over
    m.ICHostedHeadroom = Expression(
        m.IC_ZONES, m.PERIODS,
        rule=lambda m, i, p: m.ic_hosted_headroom_mw[i]
        + sum(m.ICUprateCapacity[u, p] for u in m.IC_HOST_UPRATES_IN_ZONE[i]))
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
            + m.ICHostedHeadroom[i, p]
            for i in m.IC_ZONES_IN_LOAD_ZONE[z]))

    # diagnostic slack: weighted MW admitted beyond the headroom available (fixed at 0 unless
    # ic_slack_cost_per_mw >= 0); costed in ICAnnualCost so it is used only as a last resort
    m.ICHeadroomSlack = Var(
        m.IC_ACTIVE_LOAD_ZONES, m.PERIODS, within=NonNegativeReals,
        bounds=lambda m, z, p: (0, None) if ic_slack_cost(m) is not None else (0, 0))

    m.IC_Headroom_Limit = Constraint(
        m.IC_ACTIVE_LOAD_ZONES, m.PERIODS,
        rule=lambda m, z, p: m.ICNewCapacityWeighted[z, p]
        <= m.ic_initial_headroom_mw[z]
        + m.ic_retirement_reuse_share * m.ICFreedHeadroom[z, p]
        + m.ICHeadroomBought[z, p]
        + m.ICHeadroomSlack[z, p])

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
    m.ICSlackOvernight = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(m.ICHeadroomSlack[z, p] for z in m.IC_ACTIVE_LOAD_ZONES)
        * (ic_slack_cost(m) or 0.0))
    m.ICAnnualCost = Expression(
        m.PERIODS,
        rule=lambda m, p: crf(m.interest_rate, m.ic_asset_life_years) * (sum(
            m.ICReactiveOvernight[i, p] + m.ICUprateOvernight[i, p] for i in m.IC_ZONES)
            + m.ICSlackOvernight[p]))
    m.Cost_Components_Per_Period.append("ICAnnualCost")


def load_inputs(m, switch_data, inputs_dir):
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_zones.csv"),
        optional=True, index=m.IC_ZONES,
        optional_params=["ic_hosted_headroom_mw"],
        param=(m.ic_zone_load_zone, m.ic_base_capacity_mw, m.ic_start_saturation,
               m.ic_release_cost_per_mw, m.ic_hosted_headroom_mw))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_tranches.csv"),
        optional=True, optional_params=["ic_tranche_available_year"], index=m.IC_TRANCHES,
        param=(m.ic_tranche_zone, m.ic_tranche_width, m.ic_tranche_cost_per_mw,
               m.ic_tranche_available_year))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_uprates.csv"),
        optional=True, optional_params=["ic_uprate_available_year", "ic_uprate_mode"], index=m.IC_UPRATES,
        param=(m.ic_uprate_zone, m.ic_uprate_type, m.ic_uprate_max_mw, m.ic_uprate_cost_per_mw,
               m.ic_uprate_available_year, m.ic_uprate_mode))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_weights.csv"),
        optional=True, index=m.IC_KEYS, param=(m.ic_weight,))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_params.csv"),
        optional=True,
        optional_params=["ic_retirement_reuse_share", "ic_storage_weight", "ic_default_weight",
                         "ic_asset_life_years", "ic_reactive_cost_per_mw_network",
                         "ic_slack_cost_per_mw"],
        param=(m.ic_retirement_reuse_share, m.ic_storage_weight, m.ic_default_weight,
               m.ic_asset_life_years, m.ic_reactive_cost_per_mw_network, m.ic_slack_cost_per_mw))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "ic_zone_params.csv"),
        optional=True, index=m.IC_ZONES_WITH_INITIAL, param=(m.ic_initial_headroom_mw,))


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
        # weighted generation per MW of network at the end of the empirical curve
        s_end = value(m.ic_start_saturation[i]) + sum(value(m.ic_tranche_width[t]) for t in m.IC_TRANCHES_IN_ZONE[i])
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
                d = by_type.setdefault((ty, m.ic_uprate_mode[u]), [0.0, 0.0])
                d[0] += c
                d[1] += mw
            for (ty, mode), (c, mw) in by_type.items():
                host = mode == "host"
                net_mw = (mw / s_end if s_end > 1e-9 else None) if host else mw
                spend.append({"ic_zone": i, "load_zone": z, "period": p, "type": ty,
                              "overnight_cost": c, "annual_cost": c * ann,
                              "network_mw_added": net_mw, "generation_mw_enabled": mw if host else None,
                              "network_mw_per_generation_mw": (1 / s_end if s_end > 1e-9 else None) if host else None})
            added = value(m.ICNetworkAdded[i, p])
            hosted = value(m.ICHostedHeadroom[i, p])
            implied = hosted / s_end if s_end > 1e-9 else None
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
                "hosted_mw": hosted,
                "curve_end_saturation": s_end,
                "hosted_network_mw_implied": implied,
            })
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
                "headroom_slack_mw": value(m.ICHeadroomSlack[z, p]),
                "headroom_dual": m.dual[c] if hasattr(m, "dual") and c in m.dual else None,
            })
    hr = pd.DataFrame(rows)
    hr.to_csv(os.path.join(outdir, "ic_headroom.csv"), index=False)
    used = hr[hr["headroom_slack_mw"] > 1e-6]
    if len(used):
        import logging
        logging.getLogger(__name__).warning(
            "interconnection_headroom: diagnostic slack used in %d load zone/period(s), %.1f weighted "
            "MW in total (%s). Something other than the headroom curve forces these builds; see "
            "ic_headroom.csv.", len(used), used["headroom_slack_mw"].sum(),
            ", ".join(f"{r.load_zone}/{r.period}: {r.headroom_slack_mw:.1f}" for r in used.itertuples()))
        print("WARNING: interconnection_headroom diagnostic slack used:",
              ", ".join(f"{r.load_zone}/{r.period} {r.headroom_slack_mw:.1f} MW" for r in used.itertuples()))

    # remaining state, for chaining myopic stages (prepare_next_stage.chain_ic_inputs): cumulative
    # values for every period, so a rolling-window stage can hand on its committed period
    pd.DataFrame([{"ic_tranche": t, "ic_zone": m.ic_tranche_zone[t], "period": p,
                   "used_mw": value(m.ICStep[t, p])} for p in m.PERIODS for t in m.IC_TRANCHES],
                 columns=["ic_tranche", "ic_zone", "period", "used_mw"]).to_csv(
        os.path.join(outdir, "ic_tranches_built.csv"), index=False)
    pd.DataFrame([{"ic_uprate": u, "ic_zone": m.ic_uprate_zone[u], "period": p,
                   "built_mw": value(m.ICUprateCapacity[u, p])} for p in m.PERIODS for u in m.IC_UPRATES],
                 columns=["ic_uprate", "ic_zone", "period", "built_mw"]).to_csv(
        os.path.join(outdir, "ic_uprates_built.csv"), index=False)
    pd.DataFrame([{"ic_zone": i, "period": p, "released_mw": value(m.ICRelease[i, p])}
                  for p in m.PERIODS for i in m.IC_ZONES]).to_csv(os.path.join(outdir, "ic_release_built.csv"),
                                                                   index=False)
    # hosted headroom (host uprates) not used by builds: the headroom constraint's slack in the load
    # zone, up to the hosted MW, shared across its IC zones in proportion to hosted MW
    hosted_rows = []
    for p in m.PERIODS:
        for z in m.IC_ACTIVE_LOAD_ZONES:
            con = m.IC_Headroom_Limit[z, p]
            slack = max(0.0, value(con.upper) - value(con.body)) if con.has_ub() else 0.0
            hz = {i: value(m.ICHostedHeadroom[i, p]) for i in m.IC_ZONES_IN_LOAD_ZONE[z]}
            tot = sum(hz.values())
            for i, h in hz.items():
                hosted_rows.append({"ic_zone": i, "period": p, "hosted_mw": h,
                                    "unused_mw": min(slack, tot) * h / tot if tot > 1e-9 else 0.0})
    pd.DataFrame(hosted_rows, columns=["ic_zone", "period", "hosted_mw", "unused_mw"]).to_csv(
        os.path.join(outdir, "ic_hosted_built.csv"), index=False)

    pd.DataFrame([{"GENERATION_PROJECT": g, "gen_tech": m.gen_tech[g],
                   "gen_energy_source": m.gen_energy_source[g], "ic_weight": value(m.gen_ic_weight[g])}
                  for g in m.GENERATION_PROJECTS]).to_csv(
        os.path.join(outdir, "ic_gen_weights.csv"), index=False)
