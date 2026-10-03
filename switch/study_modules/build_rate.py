"""
Build-rate supply curves for new wind, solar, storage (and optionally gas), for Switch-USA-EDF.

Pipeline and data: build_rate/ (config.yaml, Guides and documentation/build_rate.md).
Inputs are written by pg_to_switch (build_rate/brc/switch_case.py) when build_rate.enabled.

For each group G and investment period p, with build window W_p = period_end - period_start + 1:

  NewBuild[G,p] = sum of BuildGen over the group's projects with a build year in the window
                  (new builds in p plus predetermined builds dated inside the window)
  R[G,p]       <= br_rate_data_mw[G,p]                       (window sum of the annual data rates / W_p)
  R[G,p]       <= (1+growth)^W_p x NewBuild[G,p-1]/W_{p-1} + floor + committed[G,p]/(top x W_p)
                  (ramp; first period of a chained myopic stage uses br_prev_rate_mw_per_yr)
  sum_k Tier[G,p,k] (+ Slack) = NewBuild[G,p]
  Tier[G,p,k]  <= width_k x R[G,p] x W_p                      (the top band's edge is a hard ceiling)
  regional:    sum of BuildGen in region r's window <= br_region_max_mw_per_yr[G,r,p] x W_p

Tier adders ($/MW, marginal: only MW inside a band pay it) are annualised with
crf(interest_rate, br_life_years) and charged in each period the vintage is alive
(BuildRateCosts in Cost_Components_Per_Period). The optional slack (br_ceiling_slack_cost_per_mw)
lets builds exceed the ceiling at a penalty; it is off unless that value is given.

Outputs: build_rate_tiers_built.csv, build_rate_duals.csv ($/kW overnight-equivalent of the band,
rate and regional constraints), build_rate_new_build.csv. With no build_rate_periods.csv the module
does nothing.

Inputs (inputs_dir)
-------------------
build_rate_groups.csv   BR_GROUP, br_growth, br_ramp_floor_mw, br_life_years,
                        br_ceiling_slack_cost_per_mw (optional)
build_rate_gens.csv     GENERATION_PROJECT, br_gen_group
build_rate_periods.csv  BR_GROUP, PERIOD, br_rate_data_mw
build_rate_tiers.csv    BR_GROUP, PERIOD, BR_TIER, br_tier_width, br_tier_adder_per_mw
build_rate_zones.csv    LOAD_ZONE, br_zone_region                                      [optional]
build_rate_regions.csv  BR_GROUP, BR_REGION, PERIOD, br_region_max_mw_per_yr           [optional]
build_rate_prev_build.csv  BR_GROUP, br_prev_rate_mw_per_yr  (myopic chaining)         [optional]

Gas-turbine supply cap (configurable gas group; replaces MaxCapTag_GasTurbineSupply; written by
build_rate/brc/turbine_cap.py whenever build_rate.gas_turbine_cap.enabled, independently of the
groups above):

  cumulative_in_service:     sum over covered g of weight_g x (MW of g alive in p) <= gtc_max_mw[p]
                             (BuildGen of every vintage alive in p; net of SuspendGen only when
                             gtc_retirements_free_room = 1)
  new_additions_per_period:  sum over covered g of weight_g x (MW with a build year in p's window)
                             <= gtc_max_mw[p]
  cumulative_additions:      sum over covered g of weight_g x (MW with a build year from
                             gtc_since_year to p's end year) <= gtc_max_mw[p]  (retirements irrelevant)

gas_turbine_cap_gens.csv    GENERATION_PROJECT, gtc_class, gtc_weight                    [optional]
gas_turbine_cap.csv         PERIOD, gtc_max_mw                                           [optional]
gas_turbine_cap_params.csv  gtc_form, gtc_retirements_free_room, gtc_since_year          [optional]
Output: gas_turbine_cap_results.csv (covered MW, cap and dual per period).
"""
import os

from pyomo.environ import (Any, Constraint, Expression, NonNegativeReals, Param, Set, Var, value)

from switch_model.financials import capital_recovery_factor as crf

dependencies = (
    "switch_model.timescales",
    "switch_model.balancing.load_zones",
    "switch_model.financials",
    "switch_model.generators.core.build",
)


def define_components(m):
    m.BR_GROUPS = Set(dimen=1)
    m.br_growth = Param(m.BR_GROUPS, within=NonNegativeReals, default=0.0)
    m.br_ramp_floor_mw = Param(m.BR_GROUPS, within=NonNegativeReals, default=0.0)
    m.br_life_years = Param(m.BR_GROUPS, within=NonNegativeReals, default=30)
    # -1 = not given (slack off / no chained history)
    m.br_ceiling_slack_cost_per_mw = Param(m.BR_GROUPS, within=Any, default=-1)
    m.br_prev_rate_mw_per_yr = Param(m.BR_GROUPS, within=Any, default=-1)

    m.BR_GENS = Set(dimen=1, within=m.GENERATION_PROJECTS)
    m.br_gen_group = Param(m.BR_GENS, within=m.BR_GROUPS)

    m.BR_GROUP_PERIODS = Set(dimen=2, within=m.BR_GROUPS * m.PERIODS)
    m.br_rate_data_mw = Param(m.BR_GROUP_PERIODS, within=NonNegativeReals)

    m.BR_TIERS = Set(dimen=3)   # (group, period, tier)
    m.br_tier_width = Param(m.BR_TIERS, within=NonNegativeReals)
    m.br_tier_adder_per_mw = Param(m.BR_TIERS, within=NonNegativeReals)

    m.BR_ZONES = Set(dimen=1, within=m.LOAD_ZONES)
    m.br_zone_region = Param(m.BR_ZONES, within=Any)
    m.BR_REGION_PERIODS = Set(dimen=3)   # (group, region, period)
    m.br_region_max_mw_per_yr = Param(m.BR_REGION_PERIODS, within=NonNegativeReals)

    def window_years(m, p):
        return value(m.period_end[p]) - value(m.period_start[p]) + 1
    m.br_window_years = Param(m.PERIODS, initialize=window_years)

    def build_period(m, y):
        """Investment period whose build window contains build year y (None if none)."""
        if y in m.PERIODS:
            return y
        for p in m.PERIODS:
            if value(m.period_start[p]) <= y <= value(m.period_end[p]):
                return p
        return None

    def bld_init(m, grp, p):
        return [(g, y) for g in m.BR_GENS if m.br_gen_group[g] == grp
                for y in m.BLD_YRS_FOR_GEN[g] if build_period(m, y) == p]
    m.BR_BLD = Set(m.BR_GROUP_PERIODS, dimen=2, initialize=bld_init)

    m.BRNewBuild = Expression(m.BR_GROUP_PERIODS, rule=lambda m, grp, p: sum(
        m.BuildGen[g, y] for (g, y) in m.BR_BLD[grp, p]))

    def committed(m, grp, p):
        return sum(value(m.build_gen_predetermined[g, y]) for (g, y) in m.BR_BLD[grp, p]
                   if (g, y) in m.PREDETERMINED_GEN_BLD_YRS)
    m.br_committed_mw = Param(m.BR_GROUP_PERIODS, initialize=committed)

    m.BRRate = Var(m.BR_GROUP_PERIODS, within=NonNegativeReals)
    m.BR_Rate_Data = Constraint(m.BR_GROUP_PERIODS, rule=lambda m, grp, p:
                                m.BRRate[grp, p] <= m.br_rate_data_mw[grp, p])

    def top_band(m, grp, p):
        return sum(m.br_tier_width[t] for t in m.BR_TIERS if t[0] == grp and t[1] == p)

    def ramp_rule(m, grp, p):
        ps = [q for q in m.PERIODS if (grp, q) in m.BR_GROUP_PERIODS and q < p]
        w = value(m.br_window_years[p])
        top = top_band(m, grp, p)
        extra = m.br_ramp_floor_mw[grp] + (m.br_committed_mw[grp, p] / (top * w) if top > 0 else 0)
        g = (1 + m.br_growth[grp]) ** w
        if ps:
            q = ps[-1]
            return m.BRRate[grp, p] <= g * m.BRNewBuild[grp, q] / value(m.br_window_years[q]) + extra
        prev = value(m.br_prev_rate_mw_per_yr[grp])
        if prev is None or prev < 0:
            return Constraint.Skip   # first period, no chained history: data rate only
        return m.BRRate[grp, p] <= g * float(prev) + extra
    m.BR_Ramp = Constraint(m.BR_GROUP_PERIODS, rule=ramp_rule)

    m.BRTier = Var(m.BR_TIERS, within=NonNegativeReals)
    m.BR_SLACK_GROUP_PERIODS = Set(dimen=2, initialize=lambda m: [
        (grp, p) for (grp, p) in m.BR_GROUP_PERIODS if value(m.br_ceiling_slack_cost_per_mw[grp]) >= 0])
    m.BRSlack = Var(m.BR_SLACK_GROUP_PERIODS, within=NonNegativeReals)

    m.BR_Tier_Sum = Constraint(m.BR_GROUP_PERIODS, rule=lambda m, grp, p:
                               sum(m.BRTier[t] for t in m.BR_TIERS if t[0] == grp and t[1] == p)
                               + (m.BRSlack[grp, p] if (grp, p) in m.BR_SLACK_GROUP_PERIODS else 0)
                               == m.BRNewBuild[grp, p])
    m.BR_Tier_Width = Constraint(m.BR_TIERS, rule=lambda m, grp, p, k:
                                 m.BRTier[grp, p, k] <= m.br_tier_width[grp, p, k] * m.BRRate[grp, p]
                                 * m.br_window_years[p])

    def region_rule(m, grp, r, p):
        if (grp, p) not in m.BR_GROUP_PERIODS:
            return Constraint.Skip
        zones = {z for z in m.BR_ZONES if m.br_zone_region[z] == r}
        terms = [m.BuildGen[g, y] for (g, y) in m.BR_BLD[grp, p] if m.gen_load_zone[g] in zones]
        if not terms:
            return Constraint.Skip
        return sum(terms) <= m.br_region_max_mw_per_yr[grp, r, p] * m.br_window_years[p]
    m.BR_Region = Constraint(m.BR_REGION_PERIODS, rule=region_rule)

    # ---- gas-turbine supply cap ----------------------------------------------
    m.GTC_GENS = Set(dimen=1, within=m.GENERATION_PROJECTS)
    m.gtc_class = Param(m.GTC_GENS, within=Any, default="")
    m.gtc_weight = Param(m.GTC_GENS, within=NonNegativeReals, default=1.0)
    m.GTC_PERIODS = Set(dimen=1, within=m.PERIODS)
    m.gtc_max_mw = Param(m.GTC_PERIODS, within=NonNegativeReals)
    m.gtc_form = Param(within=Any, default="cumulative_in_service")
    m.gtc_retirements_free_room = Param(within=Any, default=0)
    m.gtc_since_year = Param(within=Any, default=2025)

    def gtc_mw(m, p):
        if value(m.gtc_form) == "cumulative_additions":
            first, last = int(value(m.gtc_since_year)), value(m.period_end[p])
            return sum(m.gtc_weight[g] * m.BuildGen[g, y] for g in m.GTC_GENS
                       for y in m.BLD_YRS_FOR_GEN[g] if first <= y <= last)
        if value(m.gtc_form) == "new_additions_per_period":
            return sum(m.gtc_weight[g] * m.BuildGen[g, y] for g in m.GTC_GENS
                       for y in m.BLD_YRS_FOR_GEN[g] if build_period(m, y) == p)
        net = str(value(m.gtc_retirements_free_room)).strip() in ("1", "1.0", "True", "true")
        return sum(m.gtc_weight[g] * (m.BuildGen[g, y]
                                      - (m.SuspendGen[g, y, p] if net and (g, y, p) in m.GEN_BLD_SUSPEND_YRS else 0))
                   for g in m.GTC_GENS for y in m.BLD_YRS_FOR_GEN_PERIOD[g, p])
    m.GTCCoveredMW = Expression(m.GTC_PERIODS, rule=gtc_mw)
    m.GTC_Cap = Constraint(m.GTC_PERIODS, rule=lambda m, p: m.GTCCoveredMW[p] <= m.gtc_max_mw[p]
                           if m.GTC_GENS else Constraint.Skip)

    # one-time adder cost per vintage (overnight $), annualised over the group's life
    m.BRAdderOvernight = Expression(m.BR_GROUP_PERIODS, rule=lambda m, grp, p: sum(
        m.BRTier[t] * m.br_tier_adder_per_mw[t] for t in m.BR_TIERS if t[0] == grp and t[1] == p)
        + (m.BRSlack[grp, p] * float(m.br_ceiling_slack_cost_per_mw[grp])
           if (grp, p) in m.BR_SLACK_GROUP_PERIODS else 0))

    def active(m, grp, v, p):
        """Vintage built in period v is still paying in period p."""
        return v <= p and value(m.period_start[p]) < value(m.period_start[v]) + value(m.br_life_years[grp])

    m.BuildRateCosts = Expression(m.PERIODS, rule=lambda m, p: sum(
        m.BRAdderOvernight[grp, v] * crf(m.interest_rate, m.br_life_years[grp])
        for (grp, v) in m.BR_GROUP_PERIODS if active(m, grp, v, p)))
    m.Cost_Components_Per_Period.append("BuildRateCosts")


def load_inputs(m, switch_data, inputs_dir):
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "gas_turbine_cap_gens.csv"), optional=True, index=m.GTC_GENS,
        param=(m.gtc_class, m.gtc_weight))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "gas_turbine_cap.csv"), optional=True, index=m.GTC_PERIODS,
        param=(m.gtc_max_mw,))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "gas_turbine_cap_params.csv"), optional=True,
        optional_params=["gtc_retirements_free_room", "gtc_since_year"],
        param=(m.gtc_form, m.gtc_retirements_free_room, m.gtc_since_year))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "build_rate_groups.csv"), optional=True, index=m.BR_GROUPS,
        optional_params=["br_growth", "br_ramp_floor_mw", "br_life_years", "br_ceiling_slack_cost_per_mw"],
        param=(m.br_growth, m.br_ramp_floor_mw, m.br_life_years, m.br_ceiling_slack_cost_per_mw))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "build_rate_gens.csv"), optional=True, index=m.BR_GENS,
        param=(m.br_gen_group,))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "build_rate_periods.csv"), optional=True,
        index=m.BR_GROUP_PERIODS, param=(m.br_rate_data_mw,))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "build_rate_tiers.csv"), optional=True, index=m.BR_TIERS,
        param=(m.br_tier_width, m.br_tier_adder_per_mw))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "build_rate_zones.csv"), optional=True, index=m.BR_ZONES,
        param=(m.br_zone_region,))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "build_rate_regions.csv"), optional=True,
        index=m.BR_REGION_PERIODS, param=(m.br_region_max_mw_per_yr,))
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "build_rate_prev_build.csv"), optional=True,
        param=(m.br_prev_rate_mw_per_yr,))


def pv_factor(m, grp, v):
    """Objective $ per overnight $ of a vintage-v adder (annuity x discounting over active periods)."""
    f = value(crf(m.interest_rate, m.br_life_years[grp]))
    return sum(f * value(m.bring_annual_costs_to_base_year[p]) for p in m.PERIODS
               if v <= p and value(m.period_start[p]) < value(m.period_start[v]) + value(m.br_life_years[grp]))


def post_solve(m, outdir):
    import pandas as pd

    dual = getattr(m, "dual", None)

    def d(c):
        return dual[c] if dual is not None and c in dual else None

    if len(m.GTC_PERIODS) and len(m.GTC_GENS):
        pd.DataFrame([{"period": p, "form": value(m.gtc_form), "covered_mw": value(m.GTCCoveredMW[p]),
                       "cap_mw": value(m.gtc_max_mw[p]),
                       "dual": d(m.GTC_Cap[p]) if p in m.GTC_Cap else None} for p in m.GTC_PERIODS]).to_csv(
            os.path.join(outdir, "gas_turbine_cap_results.csv"), index=False)
    if not len(m.BR_GROUP_PERIODS):
        return

    rows, drows, nrows = [], [], []
    for (grp, p) in m.BR_GROUP_PERIODS:
        w = value(m.br_window_years[p])
        rate = value(m.BRRate[grp, p])
        pv = pv_factor(m, grp, p)
        to_kw = (lambda x: None if x is None or pv == 0 else -x / pv / 1000)   # objective $/MW -> overnight $/kW
        upper = 0.0
        for t in sorted((t for t in m.BR_TIERS if t[0] == grp and t[1] == p), key=lambda t: value(m.br_tier_adder_per_mw[t])):
            width_mw = value(m.br_tier_width[t]) * rate * w
            upper += value(m.br_tier_width[t])
            built = value(m.BRTier[t])
            rows.append({"group": grp, "period": p, "tier": t[2], "band_upper_x_rate": upper,
                         "rate_mw_per_yr": rate, "window_years": w, "width_mw": width_mw,
                         "built_mw": built, "built_mw_per_yr": built / w,
                         "adder_per_kw": value(m.br_tier_adder_per_mw[t]) / 1000,
                         "full": built >= width_mw - 1e-6})
            drows.append({"group": grp, "period": p, "constraint": f"band_{t[2]}", "region": None,
                          "dual_overnight_per_kw": to_kw(d(m.BR_Tier_Width[t]))})
        drows.append({"group": grp, "period": p, "constraint": "rate_data", "region": None,
                      "dual_overnight_per_kw": to_kw(d(m.BR_Rate_Data[grp, p]))})
        if (grp, p) in m.BR_Ramp:
            drows.append({"group": grp, "period": p, "constraint": "ramp", "region": None,
                          "dual_overnight_per_kw": to_kw(d(m.BR_Ramp[grp, p]))})
        nrows.append({"group": grp, "period": p, "window_years": w, "new_build_mw": value(m.BRNewBuild[grp, p]),
                      "new_build_mw_per_yr": value(m.BRNewBuild[grp, p]) / w, "committed_mw": value(m.br_committed_mw[grp, p]),
                      "rate_mw_per_yr": rate, "rate_data_mw_per_yr": value(m.br_rate_data_mw[grp, p]),
                      "slack_mw": value(m.BRSlack[grp, p]) if (grp, p) in m.BR_SLACK_GROUP_PERIODS else 0.0,
                      "adder_cost_overnight": value(m.BRAdderOvernight[grp, p])})
    for (grp, r, p) in m.BR_REGION_PERIODS:
        if (grp, r, p) in m.BR_Region:
            pv = pv_factor(m, grp, p)
            x = d(m.BR_Region[grp, r, p])
            drows.append({"group": grp, "period": p, "constraint": "regional", "region": r,
                          "dual_overnight_per_kw": None if x is None or pv == 0 else -x / pv / 1000})
    pd.DataFrame(rows).to_csv(os.path.join(outdir, "build_rate_tiers_built.csv"), index=False)
    pd.DataFrame(drows).to_csv(os.path.join(outdir, "build_rate_duals.csv"), index=False)
    pd.DataFrame(nrows).to_csv(os.path.join(outdir, "build_rate_new_build.csv"), index=False)
    pd.DataFrame([{"period": p, "BuildRateCosts": value(m.BuildRateCosts[p])} for p in m.PERIODS]).to_csv(
        os.path.join(outdir, "build_rate_costs.csv"), index=False)
