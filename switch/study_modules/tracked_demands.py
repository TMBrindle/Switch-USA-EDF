"""
Tracked Demands module for Switch capacity expansion models.

Models specific electricity demands (data centers, electrolyzers) with clean
energy procurement requirements (24/7 CFE, 45V), grid flexibility obligations,
and access to on-site resources (storage, backup generation) to meet them.

Implementation stages:
  Stage 1  — Basic grid-only demand (current)
  Stage 2  — CFE time-block targets with shortfall penalty (current)
  Stage 3  — On-site generation (diesel, gas, CCGT+CCS, fuel cells)
  Stage 4  — Behind-the-meter battery storage
  Stage 5  — Grid flexibility events
  Stage 6  — Timepoint-indexed dispatch bounds (shaped load envelopes)
  Stage 7  — Location-flexible siting (TDZoneAllocation)
  Stage 8  — Hydrogen storage (electrolyzer TDs)
  Stage 9  — Per-source eligible energy and MW caps
  Stage 10 — Post-solve: grid_clean_fraction_computed, hourly emissions, computed
             CFE scores, 45V tier assessment
"""

import os
import tempfile

import pandas as pd
from pyomo.environ import (
    Any,
    Binary,
    Boolean,
    BuildAction,
    Constraint,
    Expression,
    NonNegativeIntegers,
    NonNegativeReals,
    Param,
    PercentFraction,
    Set,
    Var,
    value,
)

import switch_model.reporting as reporting
from switch_model.financials import capital_recovery_factor as crf
from switch_model.utilities import apply_input_aliases

dependencies = (
    "switch_model.timescales",
    "switch_model.balancing.load_zones",
    "switch_model.financials",
    "switch_model.generators.core.build",
    "switch_model.generators.core.dispatch",
)


def define_arguments(argparser):
    argparser.add_argument(
        "--td-headroom-draw",
        default="on",
        choices=("on", "off"),
        dest="td_headroom_draw",
        help=(
            "on (default): tracked demands with td_connection_uses_headroom draw interconnection headroom. "
            "off: no draw (the counterfactual for checking the attributed headroom cost; "
            "tracked_demands/scripts/headroom_counterfactual.py runs both)."
        ),
    )
    argparser.add_argument(
        "--td-cfe-region-col",
        default="h2ptcreg",
        dest="td_cfe_region_col",
        metavar="COLUMN",
        help=(
            "Column in hierarchy.csv to use for regional aggregation when computing "
            "grid_clean_fraction_computed.csv and related Stage 10 outputs. "
            "Default: h2ptcreg (45V deliverability regions). "
            "Use 'ba' to disable aggregation and compute at zone level."
        ),
    )


# ── Module-level helpers (cached on the model instance) ───────────────────────

def _td_zone(m, td):
    """Return the single candidate zone for a location-fixed TD."""
    if not hasattr(m, "_td_zone_dict"):
        m._td_zone_dict = {}
        for td2, z in m.TD_CANDIDATE_ZONES:
            m._td_zone_dict[td2] = z
    return m._td_zone_dict.get(td)


def _block_tps_in_period(m, td, blk, p):
    """Return list of timepoints in (td, blk) that fall in period p."""
    if not hasattr(m, "_block_tp_dict"):
        m._block_tp_dict = {}
        for td2, blk2, t in m.TD_BLOCK_TIMEPOINTS:
            key = (td2, blk2, m.tp_period[t])
            m._block_tp_dict.setdefault(key, []).append(t)
    return m._block_tp_dict.get((td, blk, p), [])


def _onsite_subsidy(m, tech, p):
    """Effective production subsidy ($/MWh) for tech in period p."""
    if (tech, p) in m.TD_TECH_SUBSIDY_PERIODS:
        return m.td_onsite_tech_subsidy_by_period[tech, p]
    return m.td_onsite_tech_subsidy[tech]


def _candidate_zones_for_td(m, td):
    """Return list of candidate zones for td (cached on model instance)."""
    if not hasattr(m, "_td_cand_zones"):
        m._td_cand_zones = {}
        for td2, z in m.TD_CANDIDATE_ZONES:
            m._td_cand_zones.setdefault(td2, []).append(z)
    return m._td_cand_zones.get(td, [])


def _onsite_gens(m, td):
    """Linked on-site generators of td (cached on model instance)."""
    if not hasattr(m, "_td_onsite_gens"):
        m._td_onsite_gens = {}
        for td2, g in m.TD_ONSITE_GENS:
            m._td_onsite_gens.setdefault(td2, []).append(g)
    return m._td_onsite_gens.get(td, [])


def _export(m, td, t):
    return m.TDGridExport[td, t] if (td, t) in m.TD_EXPORT_TIMEPOINTS else 0


def _float_or_none(v):
    """A "."-or-number param value as float, or None for "." / blank."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v in ("", "."):
            return None
    v = float(v)
    return None if v != v else v


def _export_share(m, td):
    """Export limit share of td: inf (no limit), 0 (no export) or a share."""
    v = _float_or_none(value(m.td_export_limit_share[td]))
    return float("inf") if v is None else v


def _connection(m, td):
    """The TD's grid connection (MW): the decision TDGridConnectionMW, or td_grid_interconnect_mw."""
    if td in m.TD_ENDOGENOUS_CONNECTION:
        return m.TDGridConnectionMW[td]
    return m.td_grid_interconnect_mw[td]


def _fixed_connection(m, td):
    if td in m.TD_ENDOGENOUS_CONNECTION:
        raise ValueError(
            f"tracked_demands: {td} is location-flexible, so its connection size can't be a decision "
            f"(td_connection_endogenous needs td_location_fixed = 1)"
        )
    return m.td_grid_interconnect_mw[td]


def _prm_requirement(m, td, p):
    """min(s x connection, peak) x (1 - curtailable share), s the stress grid limit share (§140; 1 by default); with
    an endogenous connection (bounded above by td_grid_interconnect_mw, which must not exceed the peak) it is
    s x connection x (1 - share)."""
    if td in m.TD_ENDOGENOUS_CONNECTION:
        peak = max(value(m.td_max_power_mw[td, t]) for t in m.TPS_IN_PERIOD[p])
        if value(m.td_grid_interconnect_mw[td]) > peak + 1e-9:
            raise ValueError(
                f"tracked_demands: {td} has an endogenous connection with upper bound td_grid_interconnect_mw "
                f"{value(m.td_grid_interconnect_mw[td]):g} MW above its peak load {peak:g} MW in {p}; set the bound "
                f"at or below the peak so the PRM requirement min(connection, peak) stays linear"
            )
        s = value(m.td_stress_grid_limit_share[td])
        conn = m.TDGridConnectionMW[td] if s >= 1 else s * m.TDGridConnectionMW[td]
        return conn * (1 - m.td_stress_curtailable_share[td])
    return m.td_prm_requirement_mw[td, p]


def _connection_cost_per_kw(m, td):
    """(overnight $/kW, source) of td's connection: its own cost, else the zone's upgrade cost x multiplier."""
    own = _float_or_none(value(m.td_connection_cost_per_kw[td]))
    if own is not None:
        return own, "own"
    z = _td_zone(m, td)
    zone = _float_or_none(value(m.td_zone_upgrade_cost_per_kw[z])) if z is not None else None
    if zone is None:
        return 0.0, "none"
    mult = value(m.td_connection_cost_multiplier[td])
    return zone * mult, ("zone" if mult == 1 else "zone_x_multiplier")


def _existing_connection(m, td):
    return value(m.td_connection_existing_mw[td]) if td in m.TD_CONNECTION_EXISTING else 0.0


def _new_connection(m, td):
    """Connection added in this stage (MW)."""
    return _connection(m, td) - _existing_connection(m, td)


def _has_stress_backup(m, td):
    """TD with a backup diesel fleet or a stress grid limit (§140): its stress-day shortfall is a variable."""
    return value(m.td_stress_grid_limit_share[td]) < 1 or any(d == td for d, _ in m.TD_DIESEL_GENS)


def _shortfall(m, td, t):
    return m.TDStressShortfall[td, t] if (td, t) in m.TD_SHORTFALL_TIMEPOINTS else 0


def _stress_group(m, td, t):
    """The weather-year group of stress timepoint t for td (§140): its weather year, or "all"."""
    ts = m.tp_ts[t]
    if str(value(m.td_stress_year_basis[td])).strip() == "weather_year" and ts in m.TD_STRESS_YEAR_TS:
        return str(value(m.td_stress_weather_year[ts]))
    return "all"


def _stress_groups(m, td, p):
    """Weather-year groups of period p's stress days for td; ["all"] when p has none."""
    gs = sorted({_stress_group(m, td, t) for t in m.TPS_IN_PERIOD[p] if t in m.TD_STRESS_TPS})
    return gs or ["all"]


def _n_groups(m, td, p):
    return len(_stress_groups(m, td, p))


def _stress_tps_in_group(m, td, p, w):
    return [t for t in m.TPS_IN_PERIOD[p] if t in m.TD_STRESS_TPS and _stress_group(m, td, t) == w]


def _diesel_normal_flh_mwh(m, g, p):
    """Non-emergency running of diesel g in period p, MWh per year (outside stress days)."""
    return sum(m.DispatchGen[g, t] * m.tp_weight_in_year[t]
               for t in m.TPS_IN_PERIOD[p] if t not in m.TD_STRESS_TPS and (g, t) in m.GEN_TPS)


def _diesel_variable_cost(m, g, p):
    """$/MWh of diesel g in period p: variable O&M + heat rate x the zone's fuel cost: fuel_cost.csv (simple fuel
    costs), or the regional fuel market's cheapest supply tier plus the zone's adder (markets); 0 if neither."""
    fc = 0.0
    if g in m.FUEL_BASED_GENS:
        z = m.gen_load_zone[g]
        costs = []
        for f in m.FUELS_FOR_GEN[g]:
            if hasattr(m, "fuel_cost") and (z, f, p) in m.fuel_cost:
                costs.append(value(m.fuel_cost[z, f, p]))
            elif hasattr(m, "zone_fuel_rfm") and (z, f) in m.ZONE_FUELS:
                tiers = m.SUPPLY_TIERS_FOR_RFM_PERIOD[m.zone_fuel_rfm[z, f], p]
                if len(tiers):
                    costs.append(min(value(m.rfm_supply_tier_cost[k]) for k in tiers)
                                 + value(m.zone_fuel_cost_adder[z, f, p]))
        fc = (min(costs) if costs else 0.0) * value(m.gen_full_load_heat_rate[g])
    return value(m.gen_variable_om[g]) + fc


def _stress_load(m, td, t):
    """The TD's stress-day load (MW) at t before curtailment: max, stated profile or min (§108 (d))."""
    mode = str(value(m.td_stress_mode[td])).strip()
    if mode == "max":
        return m.td_max_power_mw[td, t]
    if mode == "min":
        return m.td_min_power_mw[td, t]
    if (td, t) not in m.TD_STRESS_PROFILE:
        raise ValueError(
            f"tracked_demands: {td} has td_stress_mode profile but tracked_demand_stress_profile.csv has no row "
            f"for stress timepoint {t}"
        )
    return m.td_stress_load_mw[td, t]


def _onsite_clean(m, td, t):
    """On-site clean output of td's private techs at t (MW)."""
    return sum(
        m.DispatchOnsiteTech[td, tech, t]
        for tech in m.TD_ONSITE_TECHS
        if m.td_onsite_tech_is_clean[tech]
    )


def _onsite_clean_served(m, td, t):
    """On-site clean power credited to td's consumption at t: direct use plus clean discharge, less export debit.

    Works on model components and, through value(), on a solved model."""
    out = _onsite_clean(m, td, t)
    if (td, t) in m.TD_CFE_STORAGE_TPS:
        out = out - m.TDCleanChargeOnsite[td, t] + m.TDCleanDischarge[td, t]
    if (td, t) in m.TD_EXPORT_TIMEPOINTS:
        z = _td_zone(m, td)
        out = out - m.grid_clean_fraction[z, t] * m.TDGridExport[td, t]
    return out


def _clean_served(m, td, t):
    """All clean power credited to td's consumption at t (MW), before RECs; export debit excluded (it only lowers
    the credit, so the hourly cap is tested on the gross)."""
    out = _onsite_clean(m, td, t) + m.TDGridCleanToLoad[td, t]
    if (td, t) in m.TD_CFE_STORAGE_TPS:
        out = out - m.TDCleanChargeOnsite[td, t] + m.TDCleanDischarge[td, t]
    return out


def _expand_flex_events(inputs_dir, switch_data):
    """
    Read tracked_demand_flex_events.csv and expand time_block references.

    Supports two formats:
      - time_block column: joined with tracked_demand_time_blocks.csv
      - TIMEPOINT column: used directly (no join needed)

    Returns a DataFrame with columns (TRACKED_DEMAND, TIMEPOINT,
    td_flex_max_grid_draw_mw [, td_flex_noncompliance_penalty]), or None if
    tracked_demand_flex_events.csv is absent.
    """
    flex_path = apply_input_aliases(
        switch_data,
        os.path.join(inputs_dir, "tracked_demand_flex_events.csv"),
    )
    if not os.path.exists(flex_path):
        return None

    flex_df = pd.read_csv(flex_path)

    if "time_block" in flex_df.columns:
        blocks_path = apply_input_aliases(
            switch_data,
            os.path.join(inputs_dir, "tracked_demand_time_blocks.csv"),
        )
        blocks_df = pd.read_csv(blocks_path)[["TRACKED_DEMAND", "time_block", "TIMEPOINT"]]
        flex_df = flex_df.merge(
            blocks_df,
            on=["TRACKED_DEMAND", "time_block"],
            how="left",
        ).drop(columns=["time_block"])

    flex_df = flex_df.dropna(subset=["TIMEPOINT"]).reset_index(drop=True)
    # load_aug uses the first dimen=2 columns as the index, so TRACKED_DEMAND
    # and TIMEPOINT must come before any param columns.
    param_cols = [c for c in flex_df.columns if c not in ("TRACKED_DEMAND", "TIMEPOINT")]
    return flex_df[["TRACKED_DEMAND", "TIMEPOINT"] + param_cols]


# ══════════════════════════════════════════════════════════════════════════════
# Stage 1: Basic tracked demand with grid draw only
# ══════════════════════════════════════════════════════════════════════════════


def define_components(m):
    """
    Define sets, parameters, variables, and constraints for tracked demands.

    Stage 1 covers the core grid-only demand: each TD draws from the grid,
    subject to power bounds and an annual energy service requirement. The grid
    draw is registered as Zone_Power_Withdrawals so the system must build or
    dispatch enough to serve it.

    Stage 2 adds optional CFE time-block targets enforced via a soft penalty.
    The optimization uses an exogenous grid_clean_fraction parameter to keep
    the problem linear. Accurate hourly CFE accounting is done in post_solve
    (Stage 10).

    Inputs (Stage 1):
        tracked_demands.csv
            TRACKED_DEMAND, td_type, td_energy_requirement_mwh_per_year,
            td_default_max_power_mw [, td_default_min_power_mw,
            td_location_fixed, td_grid_interconnect_mw,
            td_onsite_emissions_in_system_cap]

        tracked_demand_candidate_zones.csv
            TRACKED_DEMAND, LOAD_ZONE
            One row per TD (location-fixed). Multi-row support added in Stage 7.

    Inputs (Stage 2, all optional — module functions without them):
        tracked_demand_time_blocks.csv
            TRACKED_DEMAND, time_block, TIMEPOINT

        tracked_demand_cfe_targets.csv
            TRACKED_DEMAND, PERIOD, time_block, cfe_target [, cfe_shortfall_penalty]

        grid_clean_fraction.csv
            LOAD_ZONE, TIMEPOINT, grid_clean_fraction
            First run: use eGRID values or omit (defaults to 0.5 everywhere).
            Later runs: use grid_clean_fraction_computed.csv from prior solve.
    """

    # ── Sets ──────────────────────────────────────────────────────────────────

    m.TRACKED_DEMANDS = Set(dimen=1, within=Any)

    # (td, zone) pairs defining where each TD could be sited.
    # Stage 1: one row per TD (location-fixed). Stage 7 adds multi-zone support.
    m.TD_CANDIDATE_ZONES = Set(dimen=2, within=Any)

    # All (td, t) combinations. Stage 1: all TDs active in all periods.
    m.TD_TIMEPOINTS = Set(
        dimen=2,
        initialize=lambda m: [
            (td, t) for td in m.TRACKED_DEMANDS for t in m.TIMEPOINTS
        ],
    )

    # ── Parameters ────────────────────────────────────────────────────────────

    m.td_type = Param(m.TRACKED_DEMANDS, within=Any)

    m.td_energy_requirement_mwh_per_year = Param(
        m.TRACKED_DEMANDS,
        within=NonNegativeReals,
        doc="Annual electricity consumption requirement (MWh/yr); enforced each period.",
    )

    m.td_default_min_power_mw = Param(
        m.TRACKED_DEMANDS,
        within=NonNegativeReals,
        default=0.0,
        doc="Default minimum instantaneous consumption (MW).",
    )

    m.td_default_max_power_mw = Param(
        m.TRACKED_DEMANDS,
        within=NonNegativeReals,
        doc="Default maximum instantaneous consumption (MW).",
    )

    # Stage 1: flag stored but not yet enforced (location flexibility is Stage 7)
    m.td_location_fixed = Param(
        m.TRACKED_DEMANDS,
        within=Boolean,
        default=True,
    )

    # Defaults to td_default_max_power_mw when not specified.
    # In Stage 1 this is always binding via the dispatch upper bound anyway;
    # becomes a meaningful separate limit in Stage 3 when on-site resources exist.
    m.td_grid_interconnect_mw = Param(
        m.TRACKED_DEMANDS,
        within=NonNegativeReals,
        default=lambda m, td: m.td_default_max_power_mw[td],
    )

    # Stage 1: flag stored; system carbon cap integration active in Stage 3.
    m.td_onsite_emissions_in_system_cap = Param(
        m.TRACKED_DEMANDS,
        within=Boolean,
        default=False,
    )

    # Stress days (zero-weight timepoints; CHANGES §108 (d)): the TD's load is
    # its maximum (default), a stated profile, or its minimum, and may be cut
    # by up to the curtailable share. The same share reduces its PRM
    # requirement.
    m.td_stress_mode = Param(
        m.TRACKED_DEMANDS, within=Any, default="max",
        validate=lambda m, v, td: str(v).strip() in ("max", "profile", "min"),
    )
    m.td_stress_curtailable_share = Param(m.TRACKED_DEMANDS, within=PercentFraction, default=0.0)
    # Stress grid limit (CHANGES §140, option B): on stress days the TD's grid
    # draw is at most this share of its connection (mandatory curtailment of
    # large loads in emergencies; backup generation carries the rest). 1 = no
    # limit (exactly §108's rule). The PRM requirement uses share x connection.
    m.td_stress_grid_limit_share = Param(m.TRACKED_DEMANDS, within=PercentFraction, default=1.0)
    # How stress days are grouped into years for the diesel permit cap and the
    # expected cost of stress-day running: weather_year (each weather year's
    # stress days are one year; tracked_demand_stress_years.csv) or all (all of
    # a period's stress days are one year)
    m.td_stress_year_basis = Param(
        m.TRACKED_DEMANDS, within=Any, default="weather_year",
        validate=lambda m, v, td: str(v).strip() in ("weather_year", "all"),
    )
    # 1: no stress-day shortfall allowed (a gap makes the run infeasible)
    m.td_stress_strict = Param(m.TRACKED_DEMANDS, within=Boolean, default=False)
    m.TD_STRESS_YEAR_TS = Set(dimen=1, within=Any)
    m.td_stress_weather_year = Param(m.TD_STRESS_YEAR_TS, within=Any)
    # $/MWh of stress-day shortfall: "." = the case's lost-load cost
    # (unserved_load_penalty_per_mwh) if it has one, else 10,000
    m.td_stress_shortfall_penalty = Param(within=Any, default=".")

    # ── Stage 6 prerequisites ─────────────────────────────────────────────────
    # td_min_power_mw / td_max_power_mw are timepoint-indexed overrides of the
    # scalar defaults. Declared here because TD_Dispatch_Lower/Upper_Bound
    # rules reference them directly. Callable defaults fall back to the scalar
    # params so no data file is needed for the base case.

    m.td_min_power_mw = Param(
        m.TD_TIMEPOINTS,
        within=NonNegativeReals,
        default=lambda m, td, t: m.td_default_min_power_mw[td],
        doc="Min power (MW) this timepoint; defaults to td_default_min_power_mw.",
    )

    m.td_max_power_mw = Param(
        m.TD_TIMEPOINTS,
        within=NonNegativeReals,
        default=lambda m, td, t: m.td_default_max_power_mw[td],
        doc="Max power (MW) this timepoint; defaults to td_default_max_power_mw.",
    )

    # ── Stage 3 prerequisites ─────────────────────────────────────────────────
    # TD_ONSITE_TECHS is iterated inside TD_Energy_Balance and TD_CFE_Constraint
    # rules; DispatchOnsiteTech is accessed in those same rules. Both must be
    # constructed before those constraints are. Remaining Stage 3 components
    # (other params, BuildOnsiteTech, constraints, costs) are defined at the end
    # of this function after Stage 2.

    m.TD_ONSITE_TECHS = Set(dimen=1, within=Any)

    m.td_onsite_tech_is_clean = Param(
        m.TD_ONSITE_TECHS,
        within=Boolean,
        default=False,
        doc="If True, dispatch counts toward CFE targets.",
    )

    m.TD_TECH_TIMEPOINTS = Set(
        dimen=3,
        initialize=lambda m: [
            (td, tech, t)
            for td in m.TRACKED_DEMANDS
            for tech in m.TD_ONSITE_TECHS
            for t in m.TIMEPOINTS
        ],
    )

    m.DispatchOnsiteTech = Var(
        m.TD_TECH_TIMEPOINTS,
        within=NonNegativeReals,
        doc="On-site generation dispatched (MW).",
    )

    # ── Stage 12 prerequisites (CHANGES §108): on-site generators ─────────────
    # Fuel-burning on-site plant is modelled as ordinary Switch generators in the
    # TD's zone (gen_info.csv etc.), linked to their TD by
    # tracked_demand_onsite_gens.csv. Their fuel use, fuel cost and emissions go
    # through Switch's standard accounting (GenFuelUseRate, DispatchEmissions);
    # the TD energy balance and zone withdrawal below net their output out.

    m.TD_ONSITE_GENS = Set(dimen=2, within=m.TRACKED_DEMANDS * m.GENERATION_PROJECTS)

    # turbine | recip | fuel_cell | boiler | other
    m.td_onsite_prime_mover = Param(m.TD_ONSITE_GENS, within=Any, default="other")

    # gas-turbine cap category (build_rate's classes: combined_cycle |
    # combustion_turbine | aeroderivative | reciprocating_engine, or none).
    # A linked generator is in the cap exactly when its category is in the
    # case's cap coverage (tracked_demand_gtc_coverage.csv, written by the case
    # writer from build_rate.gas_turbine_cap; checked in pre_solve). "" (not
    # given): reciprocating_engine for a recip, none for a fuel cell, boiler or
    # other; a turbine must name it.
    m.td_onsite_gtc_class = Param(m.TD_ONSITE_GENS, within=Any, default="")
    m.TD_GTC_COVERAGE = Set(dimen=1, within=Any)

    # hours per year of potential to emit for carbon-cap applicability tests
    # (CHANGES §140): "." = all year, or the permit hours of a backup diesel
    m.td_onsite_pte_hours = Param(m.TD_ONSITE_GENS, within=Any, default=".")

    # ── Backup diesel (CHANGES §140) ─────────────────────────────────────────
    # Existing reliability fleet (predetermined capacity at zero capex), linked
    # like any on-site generator; tracked_demand_diesel.csv marks it. Emergency
    # running on stress days only, unless non-emergency hours are allowed
    # (tier 4 or permitted engines); a permit cap on full-load-equivalent hours
    # per year counts stress hours at their real duration.
    m.TD_DIESEL_GENS = Set(dimen=2, within=m.TD_ONSITE_GENS)
    # permit 100 h/yr: PLACEHOLDER (VA permits' annual fuel caps imply 37-137 full-load hours, MD 7-24, WA 45-100;
    # sized to stay under the major-source NOx threshold); test 10 h/yr (filings 4-25 h; Laurelwood CA 10.5 h)
    m.td_diesel_permit_flh = Param(m.TD_DIESEL_GENS, within=NonNegativeReals, default=100.0)
    m.td_diesel_test_flh = Param(m.TD_DIESEL_GENS, within=NonNegativeReals, default=10.0)
    # 1: emergency (stress-day) running counts toward the permit cap; 0: the cap
    # limits only testing and non-emergency hours (federal rules put no time
    # limit on emergency use; whether state permit caps count it is being checked)
    m.td_diesel_emergency_counts_toward_cap = Param(m.TD_DIESEL_GENS, within=Boolean, default=True)
    m.td_diesel_non_emergency_flh = Param(m.TD_DIESEL_GENS, within=NonNegativeReals, default=0.0)
    m.td_diesel_tier = Param(m.TD_DIESEL_GENS, within=Any, default=2)
    m.td_diesel_permitted_non_emergency = Param(m.TD_DIESEL_GENS, within=Boolean, default=False)
    m.td_diesel_forced_outage_rate = Param(m.TD_DIESEL_GENS, within=PercentFraction, default=0.0)

    # unit nameplate (MW) for per-unit applicability tests; "." -> gen_unit_size,
    # else the rated MW
    m.td_onsite_unit_mw = Param(m.TD_ONSITE_GENS, within=Any, default=".")

    # rated MW of the generator for site-level tests and potential to emit;
    # "." -> gen_capacity_limit_mw
    m.td_onsite_rated_mw = Param(m.TD_ONSITE_GENS, within=Any, default=".")

    # annual export to the grid as a share of the linked generators' gross
    # generation; "." = no limit, 0 = no export
    m.td_export_limit_share = Param(m.TRACKED_DEMANDS, within=Any, default=".")

    m.TD_EXPORT_TIMEPOINTS = Set(
        dimen=2,
        initialize=lambda m: [
            (td, t)
            for td in m.TRACKED_DEMANDS
            if _onsite_gens(m, td) and _export_share(m, td) != 0
            for t in m.TIMEPOINTS
        ],
    )

    m.TDGridExport = Var(
        m.TD_EXPORT_TIMEPOINTS,
        within=NonNegativeReals,
        doc="Power exported to the grid by the TD's site (MW); only TDs with linked generators.",
    )

    m.TDOnsiteGenMW = Expression(
        m.TD_TIMEPOINTS,
        rule=lambda m, td, t: sum(
            m.DispatchGen[g, t] for g in _onsite_gens(m, td) if (g, t) in m.GEN_TPS
        ),
        doc="Output of the TD's linked on-site generators (MW).",
    )

    # ── Stage 4 prerequisites ─────────────────────────────────────────────────
    # TDStorageCharge and TDStorageDischarge appear in TD_Energy_Balance and
    # TD_CFE_Constraint rules, so they must be constructed before those
    # constraints. Remaining Stage 4 components are defined after Stage 3.

    m.TD_STORAGE = Set(dimen=1, within=Any)

    m.TD_STORAGE_TIMEPOINTS = Set(
        dimen=2,
        initialize=lambda m: [
            (td, t)
            for td in m.TD_STORAGE
            for t in m.TIMEPOINTS
        ],
    )

    m.TDStorageCharge = Var(
        m.TD_STORAGE_TIMEPOINTS,
        within=NonNegativeReals,
        doc="Battery charge rate (MW).",
    )

    m.TDStorageDischarge = Var(
        m.TD_STORAGE_TIMEPOINTS,
        within=NonNegativeReals,
        doc="Battery discharge rate (MW).",
    )

    # ── Stage 9 prerequisites ─────────────────────────────────────────────────
    # TDBlockGridCleanMWh appears in _cfe_constraint_rule (Stage 2). The CFE
    # constraint uses it instead of computing grid_draw × clean_fraction inline,
    # so Stage 9 can cap it without modifying the Stage 2 rule. When no Stage 9
    # caps apply the optimizer drives it to the full earned amount.
    # TD_CFE_TARGET_PERIODS must also be declared here so this Var can use it.

    m.TD_CFE_TARGET_PERIODS = Set(dimen=3, within=Any)

    m.TDBlockGridCleanMWh = Var(
        m.TD_CFE_TARGET_PERIODS,
        within=NonNegativeReals,
        doc="Grid clean MWh credited toward CFE in this block; bounded above by "
            "earned draw (Stage 2) and optionally by source cap (Stage 9).",
    )

    # ── Stage 11 prerequisite ─────────────────────────────────────────────────
    # TDBlockRECPurchase appears in _cfe_constraint_rule (Stage 2).
    # Declared here so it exists when that rule is built. Stage 11 adds the
    # supply cap constraint, cost, and load_inputs entry.

    m.TD_REC_SUPPLY = Set(dimen=3, within=Any)

    m.TDBlockRECPurchase = Var(
        m.TD_REC_SUPPLY,
        within=NonNegativeReals,
        doc="RECs purchased for this (td, period, block); counted toward CFE.",
    )

    # ── Decision variables ────────────────────────────────────────────────────

    m.TDDispatch = Var(
        m.TD_TIMEPOINTS,
        within=NonNegativeReals,
        doc="Total facility consumption (MW).",
    )

    m.TDGridDraw = Var(
        m.TD_TIMEPOINTS,
        within=NonNegativeReals,
        doc="Power drawn from the grid (MW).",
    )

    # ── Constraints ───────────────────────────────────────────────────────────

    # Facility energy balance. On-site and storage terms are zero when the
    # respective feature sets are empty (graceful degradation to Stage 1).
    m.TD_Energy_Balance = Constraint(
        m.TD_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDDispatch[td, t]
            == m.TDGridDraw[td, t]
            - _export(m, td, t)
            + m.TDOnsiteGenMW[td, t]
            + sum(m.DispatchOnsiteTech[td, tech, t] for tech in m.TD_ONSITE_TECHS)
            + (m.TDStorageDischarge[td, t] - m.TDStorageCharge[td, t]
               if td in m.TD_STORAGE else 0)
        ),
    )

    # Dispatch bounds — use timepoint-indexed params (Stage 6). When no
    # dispatch_bounds.csv is provided the callable defaults return the scalar
    # values, so behaviour is identical to the previous Stage 1 constraints.
    # Stress-day shortfall (CHANGES §140, C6): for TDs with a backup diesel
    # fleet or a stress grid limit, unserved TD load on stress days, penalised
    # (TDStressShortfallCost) so a gap shows instead of an infeasible run;
    # fixed at 0 with td_stress_strict.
    m.TD_SHORTFALL_TIMEPOINTS = Set(
        dimen=2,
        initialize=lambda m: [
            (td, t) for td in m.TRACKED_DEMANDS if _has_stress_backup(m, td)
            for t in m.TIMEPOINTS if value(m.tp_weight_in_year[t]) == 0
        ],
    )
    m.TDStressShortfall = Var(
        m.TD_SHORTFALL_TIMEPOINTS, within=NonNegativeReals,
        bounds=lambda m, td, t: (0.0, 0.0 if value(m.td_stress_strict[td]) else None),
        doc="TD load not served on a stress day (MW).",
    )

    m.TD_Dispatch_Lower_Bound = Constraint(
        m.TD_TIMEPOINTS,
        rule=lambda m, td, t: m.TDDispatch[td, t] + _shortfall(m, td, t) >= m.td_min_power_mw[td, t],
    )

    m.TD_Dispatch_Upper_Bound = Constraint(
        m.TD_TIMEPOINTS,
        rule=lambda m, td, t: m.TDDispatch[td, t] <= m.td_max_power_mw[td, t],
    )

    # Annual service requirement: meet minimum MWh in every period.
    m.TD_Annual_Service_Requirement = Constraint(
        m.TRACKED_DEMANDS,
        m.PERIODS,
        rule=lambda m, td, p: (
            sum(
                m.TDDispatch[td, t] * m.tp_weight_in_year[t]
                for t in m.TPS_IN_PERIOD[p]
            )
            >= m.td_energy_requirement_mwh_per_year[td]
        ),
    )

    # ── Grid connection (CHANGES §108 (f)) ────────────────────────────────────
    # Size: td_grid_interconnect_mw (fixed, default) or, with
    # td_connection_endogenous = 1, a decision TDGridConnectionMW between the
    # connection carried from earlier stages and td_grid_interconnect_mw (an
    # upper bound, default the TD's maximum power, so min(connection, peak) is
    # the connection). Cost: overnight $/kW = td_connection_cost_per_kw, else
    # the zone's network-upgrade cost (tracked_demand_zone_upgrade_costs.csv)
    # x td_connection_cost_multiplier, annualised over td_connection_life_years
    # at the model's interest rate, on the whole connection in every period
    # (like on-site plant). td_connection_cost_share_demand splits it for bill
    # reporting only. Optional headroom draw (td_connection_uses_headroom): the
    # connection added this stage x td_headroom_weight is added to
    # interconnection_headroom's headroom use in the TD's zone.
    m.td_connection_endogenous = Param(m.TRACKED_DEMANDS, within=Boolean, default=False)
    m.td_connection_cost_per_kw = Param(m.TRACKED_DEMANDS, within=Any, default=".")
    m.td_connection_cost_multiplier = Param(m.TRACKED_DEMANDS, within=NonNegativeReals, default=1.0)
    m.td_connection_cost_share_demand = Param(m.TRACKED_DEMANDS, within=PercentFraction, default=1.0)
    m.td_connection_uses_headroom = Param(m.TRACKED_DEMANDS, within=Boolean, default=False)
    m.td_headroom_weight = Param(m.TRACKED_DEMANDS, within=NonNegativeReals, default=1.0)
    m.td_zone_upgrade_cost_per_kw = Param(m.LOAD_ZONES, within=Any, default=".")
    m.td_connection_life_years = Param(within=NonNegativeReals, default=40.0)
    # carried from earlier stages (chain_inputs)
    m.TD_CONNECTION_EXISTING = Set(dimen=1, within=m.TRACKED_DEMANDS)
    m.td_connection_existing_mw = Param(m.TD_CONNECTION_EXISTING, within=NonNegativeReals, default=0.0)
    m.td_headroom_draw_existing_mw = Param(m.TD_CONNECTION_EXISTING, within=NonNegativeReals, default=0.0)

    m.TD_ENDOGENOUS_CONNECTION = Set(
        dimen=1,
        initialize=lambda m: [td for td in m.TRACKED_DEMANDS if value(m.td_connection_endogenous[td])],
    )
    m.TDGridConnectionMW = Var(
        m.TD_ENDOGENOUS_CONNECTION,
        within=NonNegativeReals,
        bounds=lambda m, td: (
            value(m.td_connection_existing_mw[td]) if td in m.TD_CONNECTION_EXISTING else 0.0,
            value(m.td_grid_interconnect_mw[td]),
        ),
        doc="Grid connection built for the TD (MW), when its size is a decision.",
    )

    # Grid interconnection capacity limit.
    m.TD_Grid_Interconnect_Limit = Constraint(
        m.TD_TIMEPOINTS,
        rule=lambda m, td, t: m.TDGridDraw[td, t] <= _connection(m, td),
    )

    # ── Stress days (§108 (d)) ────────────────────────────────────────────────
    # prm_regional's region peak (a parameter, built in its define_components) reads TDPrmFixedLoadMW, so this
    # module must come first (its served load is built in define_dynamic_components, so that part works either way)
    if hasattr(m, "prm_region_peak_mw"):
        raise RuntimeError(
            "tracked_demands must be loaded before prm_regional (prm_regional reads the tracked demands' reserve "
            "requirement): list it earlier on the scenario line"
        )

    m.TD_STRESS_TPS = Set(
        dimen=1,
        initialize=lambda m: [t for t in m.TIMEPOINTS if value(m.tp_weight_in_year[t]) == 0],
        doc="Zero-weight (stress-day) timepoints.",
    )
    m.TD_STRESS_PROFILE = Set(dimen=2, within=m.TRACKED_DEMANDS * m.TIMEPOINTS)
    m.td_stress_load_mw = Param(m.TD_STRESS_PROFILE, within=NonNegativeReals)
    m.TD_STRESS_TIMEPOINTS = Set(
        dimen=2,
        initialize=lambda m: [(td, t) for td in m.TRACKED_DEMANDS for t in m.TD_STRESS_TPS],
    )
    m.TD_Stress_Load_Max = Constraint(
        m.TD_STRESS_TIMEPOINTS,
        rule=lambda m, td, t: m.TDDispatch[td, t] <= _stress_load(m, td, t),
    )
    m.TD_Stress_Load_Min = Constraint(
        m.TD_STRESS_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDDispatch[td, t] + _shortfall(m, td, t)
            >= (1 - m.td_stress_curtailable_share[td]) * _stress_load(m, td, t)
        ),
    )
    # stress grid limit (§140, option B)
    m.TD_Stress_Grid_Limit = Constraint(
        m.TD_STRESS_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDGridDraw[td, t] <= m.td_stress_grid_limit_share[td] * _connection(m, td)
            if value(m.td_stress_grid_limit_share[td]) < 1 else Constraint.Skip
        ),
    )

    # ── PRM requirement (§108 (d); read by prm_regional) ──────────────────────
    # min(grid connection, peak load) x (1 - curtailable share), in every stress
    # hour of the period, at the TD's zone (shared by siting for a location-
    # flexible TD). On-site plant gets no reserve credit (pre_solve).
    m.td_prm_requirement_mw = Param(
        m.TRACKED_DEMANDS, m.PERIODS,
        initialize=lambda m, td, p: min(
            value(m.td_grid_interconnect_mw[td]) * value(m.td_stress_grid_limit_share[td]),
            max(value(m.td_max_power_mw[td, t]) for t in m.TPS_IN_PERIOD[p]),
        ) * (1 - value(m.td_stress_curtailable_share[td])),
        doc="Tracked demand's planning-reserve requirement (MW) in each stress hour of the period.",
    )

    # Export through the same connection (§108).
    m.TD_Grid_Export_Limit = Constraint(
        m.TD_EXPORT_TIMEPOINTS,
        rule=lambda m, td, t: m.TDGridExport[td, t] <= _connection(m, td),
    )

    # Annual export <= share x gross generation of the linked generators (§108;
    # RGGI-style limited export is share 0.1).
    m.TD_Export_Share_Limit = Constraint(
        m.TRACKED_DEMANDS,
        m.PERIODS,
        rule=lambda m, td, p: (
            sum(m.TDGridExport[td, t] * m.tp_weight_in_year[t] for t in m.TPS_IN_PERIOD[p])
            <= _export_share(m, td)
            * sum(m.TDOnsiteGenMW[td, t] * m.tp_weight_in_year[t] for t in m.TPS_IN_PERIOD[p])
            if (td, m.TPS_IN_PERIOD[p].first()) in m.TD_EXPORT_TIMEPOINTS
            and _export_share(m, td) != float("inf")
            else Constraint.Skip
        ),
    )

    # ── Backup diesel constraints and stress-day costs (CHANGES §140) ────────
    # C1: emergency only (no running outside stress days) unless non-emergency
    # hours are allowed; then at most those hours per year
    m.TD_DIESEL_NORMAL_TPS = Set(
        dimen=3,
        initialize=lambda m: [
            (td, g, t) for td, g in m.TD_DIESEL_GENS for t in m.TPS_FOR_GEN[g] if t not in m.TD_STRESS_TPS
        ],
    )
    m.TD_Diesel_Emergency_Only = Constraint(
        m.TD_DIESEL_NORMAL_TPS,
        rule=lambda m, td, g, t: (
            m.DispatchGen[g, t] == 0 if value(m.td_diesel_non_emergency_flh[td, g]) == 0 else Constraint.Skip
        ),
    )
    m.TD_Diesel_Non_Emergency_Limit = Constraint(
        m.TD_DIESEL_GENS, m.PERIODS,
        rule=lambda m, td, g, p: (
            _diesel_normal_flh_mwh(m, g, p) <= m.td_diesel_non_emergency_flh[td, g] * m.GenCapacity[g, p]
            if value(m.td_diesel_non_emergency_flh[td, g]) > 0 and (g, p) in m.GEN_PERIODS else Constraint.Skip
        ),
    )
    # C2: permit cap on full-load-equivalent hours per year, in each weather-year
    # group of stress days: stress running at real duration (tp_duration_hrs,
    # each stress day once a year) + non-emergency running per year (tp_weight_in_year)
    # + test hours <= permit hours
    m.TD_DIESEL_GROUPS = Set(
        dimen=4,
        initialize=lambda m: [
            (td, g, p, w) for td, g in m.TD_DIESEL_GENS for p in m.PERIODS if (g, p) in m.GEN_PERIODS
            for w in _stress_groups(m, td, p)
        ],
    )
    m.TD_Diesel_Permit_Cap = Constraint(
        m.TD_DIESEL_GROUPS,
        rule=lambda m, td, g, p, w: (
            (sum(m.DispatchGen[g, t] * m.tp_duration_hrs[t] for t in _stress_tps_in_group(m, td, p, w)
                 if (g, t) in m.GEN_TPS) if value(m.td_diesel_emergency_counts_toward_cap[td, g]) else 0)
            + _diesel_normal_flh_mwh(m, g, p)
            + m.td_diesel_test_flh[td, g] * m.GenCapacity[g, p]
            <= m.td_diesel_permit_flh[td, g] * m.GenCapacity[g, p]
        ),
    )
    # C3: emergency engines don't supply the grid: export only from the TD's
    # other linked generators
    m.TD_Diesel_No_Export = Constraint(
        m.TD_EXPORT_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDGridExport[td, t] <= sum(
                m.DispatchGen[g, t] for g in _onsite_gens(m, td)
                if (td, g) not in m.TD_DIESEL_GENS and (g, t) in m.GEN_TPS)
            if any((td, g) in m.TD_DIESEL_GENS for g in _onsite_gens(m, td)) else Constraint.Skip
        ),
    )
    # C4: availability on stress days
    m.TD_DIESEL_STRESS_TPS = Set(
        dimen=3,
        initialize=lambda m: [
            (td, g, t) for td, g in m.TD_DIESEL_GENS for t in m.TPS_FOR_GEN[g] if t in m.TD_STRESS_TPS
        ],
    )
    m.TD_Diesel_Availability = Constraint(
        m.TD_DIESEL_STRESS_TPS,
        rule=lambda m, td, g, t: (
            m.DispatchGen[g, t]
            <= m.GenCapacity[g, m.tp_period[t]] * (1 - m.td_diesel_forced_outage_rate[td, g])
        ),
    )
    # C5: expected cost of stress-day running per year (fuel x heat rate +
    # variable O&M, at real duration, averaged over the period's weather-year
    # groups): stress days carry no other cost, so this keeps diesel off unless
    # it relieves something
    m.TDDieselStressCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.DispatchGen[g, t] * m.tp_duration_hrs[t] * _diesel_variable_cost(m, g, p) / _n_groups(m, td, p)
            for td, g, t in m.TD_DIESEL_STRESS_TPS if m.tp_period[t] == p
        ),
    )
    m.Cost_Components_Per_Period.append("TDDieselStressCost")
    # C6: shortfall penalty, per year likewise
    m.td_stress_shortfall_penalty_resolved = Param(
        within=NonNegativeReals,
        initialize=lambda m: (
            float(_float_or_none(value(m.td_stress_shortfall_penalty)))
            if _float_or_none(value(m.td_stress_shortfall_penalty)) is not None
            else float(value(m.unserved_load_penalty_per_mwh)) if hasattr(m, "unserved_load_penalty_per_mwh")
            else 10000.0
        ),
    )
    m.TDStressShortfallCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.TDStressShortfall[td, t] * m.tp_duration_hrs[t] * m.td_stress_shortfall_penalty_resolved
            / _n_groups(m, td, p)
            for td, t in m.TD_SHORTFALL_TIMEPOINTS if m.tp_period[t] == p
        ),
    )
    m.Cost_Components_Per_Period.append("TDStressShortfallCost")

    # ── Grid connection cost (§108 (f)) ───────────────────────────────────────
    m.TDConnectionCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            _connection(m, td) * 1000.0 * _connection_cost_per_kw(m, td)[0]
            * crf(m.interest_rate, m.td_connection_life_years)
            for td in m.TRACKED_DEMANDS
            if _connection_cost_per_kw(m, td)[0] > 0
        ),
        doc="Annualised cost of the tracked demands' grid connections ($/yr).",
    )
    m.Cost_Components_Per_Period.append("TDConnectionCost")

    # headroom draw: connection added this stage x weight, in the TD's zone, every period
    m.TD_HEADROOM_TDS = Set(
        dimen=1,
        initialize=lambda m: [
            td for td in m.TRACKED_DEMANDS
            if value(m.td_connection_uses_headroom[td]) and _headroom_draw_on(m)
        ],
    )
    m.TDHeadroomDrawMW = Expression(
        m.TD_HEADROOM_TDS,
        rule=lambda m, td: m.td_headroom_weight[td] * _new_connection(m, td),
        doc="Weighted MW of interconnection headroom drawn by the TD's connection added this stage.",
    )

    # ── Soft grid cap (Stage 1 extension) ────────────────────────────────────
    # Optional penalty-based cap, activated when tracked_demand_grid_soft_cap.csv
    # is present.  For TDs in TD_SOFT_CAP, grid draw above td_grid_soft_cap_mw
    # is allowed but incurs td_grid_soft_cap_penalty $/MWh.  The existing hard
    # limit (TD_Grid_Interconnect_Limit) remains as a physical ceiling.
    #
    # Use cases:
    #   - Policy/tariff cap with economic penalty (soft cap, penalty > 0)
    #   - Approximate hard cap: set penalty >> expected LMP (e.g. 1e6 $/MWh)
    #   - Reporting only: set penalty = 0 to track excess without cost impact
    #
    # Input: tracked_demand_grid_soft_cap.csv (optional)
    #   TRACKED_DEMAND, td_grid_soft_cap_mw [, td_grid_soft_cap_penalty]

    m.TD_SOFT_CAP = Set(dimen=1, within=m.TRACKED_DEMANDS)

    m.td_grid_soft_cap_mw = Param(
        m.TD_SOFT_CAP,
        within=NonNegativeReals,
        doc="Soft grid connection cap (MW); excess above this incurs penalty.",
    )

    m.td_grid_soft_cap_penalty = Param(
        m.TD_SOFT_CAP,
        within=NonNegativeReals,
        default=0.0,
        doc="Penalty ($/MWh) for grid draw above td_grid_soft_cap_mw. "
            "Set large (e.g. 1e6) to approximate a hard cap with penalty.",
    )

    m.TD_SOFT_CAP_TIMEPOINTS = Set(
        dimen=2,
        initialize=lambda m: [
            (td, t) for td in m.TD_SOFT_CAP for t in m.TIMEPOINTS
        ],
    )

    m.TDGridSoftExcess = Var(
        m.TD_SOFT_CAP_TIMEPOINTS,
        within=NonNegativeReals,
        doc="Grid draw above soft cap (MW); penalised in objective.",
    )

    m.TD_Grid_Soft_Cap = Constraint(
        m.TD_SOFT_CAP_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDGridDraw[td, t]
            <= m.td_grid_soft_cap_mw[td] + m.TDGridSoftExcess[td, t]
        ),
    )

    m.TDGridSoftExcessCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.TDGridSoftExcess[td, t]
            * m.td_grid_soft_cap_penalty[td]
            * m.tp_weight_in_year[t]
            for td, t in m.TD_SOFT_CAP_TIMEPOINTS
            if m.tp_period[t] == p
        ),
    )
    m.Cost_Components_Per_Period.append("TDGridSoftExcessCost")

    # ── Stage 7 prerequisites ─────────────────────────────────────────────────
    # TDGridDrawInZone appears in TDGridDrawByZone below; must be constructed
    # first. Remaining Stage 7 components (TDZoneAllocation, constraints) are
    # defined after Stage 4 once all other variables exist.

    m.TD_LOCATION_FLEX = Set(
        dimen=1,
        within=m.TRACKED_DEMANDS,
        initialize=lambda m: [
            td for td in m.TRACKED_DEMANDS
            if not value(m.td_location_fixed[td])
        ],
    )

    # (td, z, t) for flexible TDs × their candidate zones × all timepoints.
    m.TD_FLEX_ZONE_TIMEPOINTS = Set(
        dimen=3,
        initialize=lambda m: [
            (td, z, t)
            for td, z in m.TD_CANDIDATE_ZONES
            if td in m.TD_LOCATION_FLEX
            for t in m.TIMEPOINTS
        ],
    )

    m.TDGridDrawInZone = Var(
        m.TD_FLEX_ZONE_TIMEPOINTS,
        within=NonNegativeReals,
        doc="Grid draw from zone z for location-flexible TD (MW).",
    )

    # ── Zone energy balance integration ──────────────────────────────────────
    # Location-fixed TDs: all grid draw attributed to their single candidate
    # zone directly via TDGridDraw.
    # Location-flexible TDs: grid draw split across candidate zones via
    # TDGridDrawInZone (Stage 7). Both paths are summed here.

    def _td_grid_draw_by_zone_rule(m, z, t):
        if not hasattr(m, "_td_fixed_in_zone"):
            m._td_fixed_in_zone = {}
            m._td_flex_in_zone = {}
            for td2, z2 in m.TD_CANDIDATE_ZONES:
                if value(m.td_location_fixed[td2]):
                    m._td_fixed_in_zone.setdefault(z2, []).append(td2)
                else:
                    m._td_flex_in_zone.setdefault(z2, []).append(td2)
        # A location-fixed TD withdraws its grid draw net of export, plus the
        # output of its linked generators, which inject through the standard
        # dispatch module (so the zone nets to draw - export; §108).
        fixed = sum(
            m.TDGridDraw[td, t] - _export(m, td, t) + m.TDOnsiteGenMW[td, t]
            for td in m._td_fixed_in_zone.get(z, [])
        )
        flex = sum(m.TDGridDrawInZone[td, z, t] for td in m._td_flex_in_zone.get(z, []))
        return fixed + flex

    m.TDGridDrawByZone = Expression(
        m.LOAD_ZONES,
        m.TIMEPOINTS,
        rule=_td_grid_draw_by_zone_rule,
    )
    m.Zone_Power_Withdrawals.append("TDGridDrawByZone")

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 2: CFE time-block targets with shortfall penalty
    # ══════════════════════════════════════════════════════════════════════════

    # ── Sets ─────────────────────────────────────────────────────────────────

    # (td, time_block, t) triples; loaded from tracked_demand_time_blocks.csv.
    # Empty when file is absent — all Stage 2 constraints are then vacuous.
    m.TD_BLOCK_TIMEPOINTS = Set(dimen=3, within=Any)

    # (td, time_block) pairs; derived from TD_BLOCK_TIMEPOINTS.
    m.TD_TIME_BLOCKS = Set(
        dimen=2,
        initialize=lambda m: set(
            (td, blk) for td, blk, t in m.TD_BLOCK_TIMEPOINTS
        ),
    )

    # ── Parameters ───────────────────────────────────────────────────────────

    m.td_cfe_target = Param(
        m.TD_CFE_TARGET_PERIODS,
        within=PercentFraction,
        default=0.0,
        doc="Required clean energy fraction for this TD, period, and time block.",
    )

    m.td_cfe_shortfall_penalty = Param(
        m.TD_CFE_TARGET_PERIODS,
        within=NonNegativeReals,
        default=0.0,
        doc="Penalty ($/MWh) for each MWh of CFE shortfall in this block.",
    )

    # Exogenous clean fraction of zonal grid generation.
    # Default 0.5 (conservative first-run value). Replace with
    # grid_clean_fraction_computed.csv from a prior solve for accuracy.
    m.grid_clean_fraction = Param(
        m.LOAD_ZONES,
        m.TIMEPOINTS,
        within=PercentFraction,
        default=0.5,
    )

    # ── Decision variable ─────────────────────────────────────────────────────

    m.TDCFEShortfall = Var(
        m.TD_CFE_TARGET_PERIODS,
        within=NonNegativeReals,
        doc="CFE shortfall (MWh/yr) in this block; non-zero incurs penalty cost.",
    )

    # ── Clean-energy ledger (CHANGES §108 (b)) ────────────────────────────────
    # Hourly clean credit counts each clean MWh once and never more than the
    # TD consumes in that hour. For each TD with CFE targets and timepoint t:
    #   GridCleanToLoad + CleanChargeGrid  <= gcf x grid draw
    #   CleanChargeOnsite                  <= on-site clean output
    #   clean served = on-site clean - CleanChargeOnsite + CleanDischarge
    #                  + GridCleanToLoad  <= TDDispatch          (hourly cap)
    # and, for TDs with a battery, a clean sub-ledger of its state of charge
    # (TD_Clean_Storage_* below): clean charge in, clean discharge out, at the
    # battery's efficiency, never above the battery's level. Clean that goes
    # into the battery counts when it comes out, not when it goes in. Export
    # (§108 (a)) is debited at the grid clean fraction (conservative: it
    # removes any gain from drawing and exporting in the same hour).

    m.TD_CFE_TDS = Set(
        dimen=1,
        initialize=lambda m: sorted({td for td, p, blk in m.TD_CFE_TARGET_PERIODS}, key=str),
    )
    m.TD_CFE_TPS = Set(
        dimen=2,
        initialize=lambda m: [(td, t) for td in m.TD_CFE_TDS for t in m.TIMEPOINTS],
    )
    m.TD_CFE_STORAGE_TPS = Set(
        dimen=2,
        initialize=lambda m: [(td, t) for td, t in m.TD_CFE_TPS if td in m.TD_STORAGE],
    )
    m.TDGridCleanToLoad = Var(
        m.TD_CFE_TPS, within=NonNegativeReals,
        doc="Grid clean power credited to the TD's own consumption (MW).",
    )
    m.TDCleanChargeOnsite = Var(m.TD_CFE_STORAGE_TPS, within=NonNegativeReals,
                                doc="On-site clean power charged into the battery (MW).")
    m.TDCleanChargeGrid = Var(m.TD_CFE_STORAGE_TPS, within=NonNegativeReals,
                              doc="Grid clean power charged into the battery (MW).")
    m.TDCleanDischarge = Var(m.TD_CFE_STORAGE_TPS, within=NonNegativeReals,
                             doc="Battery discharge credited as clean (MW).")
    m.TDCleanLevel = Var(m.TD_CFE_STORAGE_TPS, within=NonNegativeReals,
                         doc="Clean part of the battery's state of charge (MWh).")

    m.TD_Grid_Clean_Available = Constraint(
        m.TD_CFE_TPS,
        rule=lambda m, td, t: (
            Constraint.Skip if _td_zone(m, td) is None else
            m.TDGridCleanToLoad[td, t]
            + (m.TDCleanChargeGrid[td, t] if (td, t) in m.TD_CFE_STORAGE_TPS else 0)
            <= m.grid_clean_fraction[_td_zone(m, td), t] * m.TDGridDraw[td, t]
        ),
    )
    m.TD_Clean_Served_Cap = Constraint(
        m.TD_CFE_TPS,
        rule=lambda m, td, t: _clean_served(m, td, t) <= m.TDDispatch[td, t],
    )

    # ── CFE soft constraint ───────────────────────────────────────────────────

    def _cfe_constraint_rule(m, td, p, blk):
        tps = _block_tps_in_period(m, td, blk, p)
        if not tps:
            return Constraint.Skip
        z = _td_zone(m, td)
        if z is None:
            return Constraint.Skip

        # Grid clean credit comes from TDBlockGridCleanMWh (Stage 9 prerequisite):
        # bounded above by the grid clean power credited to the TD's own
        # consumption (TD_CFE_Grid_Earned) and optionally by a per-block source
        # cap (TD_Grid_Clean_Cap, Stage 9). RECs (Stage 11) and on-site clean
        # served (through the clean ledger) are added directly.
        rec_mwh = (
            m.TDBlockRECPurchase[td, p, blk]
            if (td, p, blk) in m.TD_REC_SUPPLY else 0
        )
        estimated_clean_mwh = (
            m.TDBlockGridCleanMWh[td, p, blk]
            + rec_mwh
            + sum(_onsite_clean_served(m, td, t) * m.tp_weight_in_year[t] for t in tps)
        )
        total_consumption_mwh = sum(
            m.TDDispatch[td, t] * m.tp_weight_in_year[t] for t in tps
        )
        return (
            estimated_clean_mwh + m.TDCFEShortfall[td, p, blk]
            >= m.td_cfe_target[td, p, blk] * total_consumption_mwh
        )

    m.TD_CFE_Constraint = Constraint(
        m.TD_CFE_TARGET_PERIODS,
        rule=_cfe_constraint_rule,
    )

    # ── CFE shortfall cost (added to objective) ───────────────────────────────

    # Per-period cost in $/yr: shortfall (MWh/yr) × penalty ($/MWh).
    # Multiplied by bring_annual_costs_to_base_year[p] by financials module.
    m.TDCFEShortfallCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.TDCFEShortfall[td, p2, blk] * m.td_cfe_shortfall_penalty[td, p2, blk]
            for td, p2, blk in m.TD_CFE_TARGET_PERIODS
            if p2 == p
        ),
    )
    m.Cost_Components_Per_Period.append("TDCFEShortfallCost")

    # TDBlockGridCleanMWh cannot exceed the actual grid clean energy earned in
    # this block. The optimizer maximises it (to reduce shortfall penalty), so
    # without a Stage 9 cap it reaches the earned amount automatically.
    def _td_cfe_grid_earned_rule(m, td, p, blk):
        tps = _block_tps_in_period(m, td, blk, p)
        z = _td_zone(m, td)
        if not tps or z is None:
            return Constraint.Skip
        return m.TDBlockGridCleanMWh[td, p, blk] <= sum(
            m.TDGridCleanToLoad[td, t] * m.tp_weight_in_year[t] for t in tps
        )

    m.TD_CFE_Grid_Earned = Constraint(m.TD_CFE_TARGET_PERIODS, rule=_td_cfe_grid_earned_rule)

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 3: On-site generation
    # ══════════════════════════════════════════════════════════════════════════

    # ── Sets ─────────────────────────────────────────────────────────────────
    # TD_ONSITE_TECHS, TD_TECH_TIMEPOINTS, and DispatchOnsiteTech are declared
    # earlier as Stage 3 prerequisites (before Stage 1 constraint rules).

    # All (td, tech) build pairs — all TDs can access all available techs.
    m.TD_TECH_BUILDS = Set(
        dimen=2,
        initialize=lambda m: [
            (td, tech)
            for td in m.TRACKED_DEMANDS
            for tech in m.TD_ONSITE_TECHS
        ],
    )

    # (tech, period) pairs with period-specific subsidy overrides.
    m.TD_TECH_SUBSIDY_PERIODS = Set(dimen=2, within=Any)

    # ── Parameters ───────────────────────────────────────────────────────────

    m.td_onsite_tech_capital_cost = Param(
        m.TD_ONSITE_TECHS,
        within=NonNegativeReals,
        doc="Annualized capital cost ($/MW-yr).",
    )

    m.td_onsite_tech_variable_om = Param(
        m.TD_ONSITE_TECHS,
        within=NonNegativeReals,
        default=0.0,
        doc="Variable O&M ($/MWh).",
    )

    m.td_onsite_tech_fuel_cost = Param(
        m.TD_ONSITE_TECHS,
        within=NonNegativeReals,
        default=0.0,
        doc="Fuel cost ($/MWh of output).",
    )

    m.td_onsite_tech_emissions = Param(
        m.TD_ONSITE_TECHS,
        within=NonNegativeReals,
        default=0.0,
        doc="Gross emissions rate (tCO2/MWh).",
    )

    m.td_onsite_tech_capture_rate = Param(
        m.TD_ONSITE_TECHS,
        within=PercentFraction,
        default=0.0,
        doc="CO2 capture rate [0-1]. Net emissions = emissions * (1 - capture_rate).",
    )

    m.td_onsite_tech_max_annual_hours = Param(
        m.TD_ONSITE_TECHS,
        within=NonNegativeReals,
        default=8760.0,
        doc="Maximum annual operating hours per MW built.",
    )

    # td_onsite_tech_is_clean is declared earlier as a Stage 3 prerequisite.

    m.td_onsite_tech_min_stable_mw_fraction = Param(
        m.TD_ONSITE_TECHS,
        within=PercentFraction,
        default=0.0,
        doc="Minimum stable output as fraction of built capacity (LP relaxation).",
    )

    m.td_onsite_tech_subsidy = Param(
        m.TD_ONSITE_TECHS,
        within=NonNegativeReals,
        default=0.0,
        doc="Default production subsidy ($/MWh); overridden per period by "
            "td_onsite_tech_subsidy_by_period.",
    )

    m.td_onsite_tech_subsidy_by_period = Param(
        m.TD_TECH_SUBSIDY_PERIODS,
        within=NonNegativeReals,
        doc="Period-specific production subsidy ($/MWh).",
    )

    # Minimum built capacity if any is built (default 0 = continuous LP).
    # When > 0, a binary flag enforces the semi-continuous lower bound.
    m.td_onsite_tech_min_build_mw = Param(
        m.TD_ONSITE_TECHS,
        within=NonNegativeReals,
        default=0.0,
        doc="Minimum capacity if built (MW). 0 = continuous (LP).",
    )

    # Capacity increment above minimum (default 0 = continuous above minimum).
    # When > 0, an integer variable enforces discrete step sizes.
    m.td_onsite_tech_build_increment_mw = Param(
        m.TD_ONSITE_TECHS,
        within=NonNegativeReals,
        default=0.0,
        doc="Capacity increment above min_build (MW). 0 = continuous above min.",
    )

    m.td_onsite_tech_cf_source = Param(
        m.TD_ONSITE_TECHS,
        within=Any,
        default="",
        doc=(
            "gen_energy_source whose zone-averaged capacity factor constrains dispatch. "
            "Empty = fully dispatchable (diesel, gas CCS, nuclear, geothermal). "
            "Set to 'solar' or 'wind' to inherit the zone's grid CF profile."
        ),
    )

    # (td, tech) pairs with fixed predetermined capacity (not optimised).
    m.TD_PREDETERMINED_BUILDS = Set(dimen=2, within=Any)

    m.td_onsite_predetermined_mw = Param(
        m.TD_PREDETERMINED_BUILDS,
        within=NonNegativeReals,
        doc="Fixed on-site capacity (MW); solver dispatches but does not size.",
    )

    # Subset of TD_TECH_BUILDS that require MIP variables (min_build or increment
    # nonzero, excluding predetermined entries whose size is already fixed).
    m.TD_TECH_BUILDS_FLAG = Set(
        dimen=2,
        within=m.TD_TECH_BUILDS,
        initialize=lambda m: [
            (td, tech)
            for td, tech in m.TD_TECH_BUILDS
            if (td, tech) not in m.TD_PREDETERMINED_BUILDS
            and (m.td_onsite_tech_min_build_mw[tech] > 0
                 or m.td_onsite_tech_build_increment_mw[tech] > 0)
        ],
    )

    # Subset requiring an integer units variable (increment > 0).
    m.TD_TECH_BUILDS_UNITS = Set(
        dimen=2,
        within=m.TD_TECH_BUILDS_FLAG,
        initialize=lambda m: [
            (td, tech)
            for td, tech in m.TD_TECH_BUILDS_FLAG
            if m.td_onsite_tech_build_increment_mw[tech] > 0
        ],
    )

    # ── Decision variables ────────────────────────────────────────────────────

    m.BuildOnsiteTech = Var(
        m.TD_TECH_BUILDS,
        within=NonNegativeReals,
        doc="On-site generation capacity built (MW); single persistent build.",
    )
    # DispatchOnsiteTech is declared earlier as a Stage 3 prerequisite.

    # Binary: 1 if any capacity is built for this (td, tech) pair.
    # Only created for entries in TD_TECH_BUILDS_FLAG (MIP techs only).
    m.BuildOnsiteTechFlag = Var(
        m.TD_TECH_BUILDS_FLAG,
        within=Binary,
        doc="1 if on-site tech is built; enforces semi-continuous lower bound.",
    )

    # Integer: additional capacity increments above min_build.
    # Only created for entries in TD_TECH_BUILDS_UNITS (increment > 0).
    m.BuildOnsiteTechUnits = Var(
        m.TD_TECH_BUILDS_UNITS,
        within=NonNegativeIntegers,
        doc="Additional capacity increments above td_onsite_tech_min_build_mw.",
    )

    # ── Constraints ───────────────────────────────────────────────────────────

    m.TD_OnsiteTech_Dispatch_Max = Constraint(
        m.TD_TECH_TIMEPOINTS,
        rule=lambda m, td, tech, t: (
            m.DispatchOnsiteTech[td, tech, t] <= m.BuildOnsiteTech[td, tech]
        ),
    )

    # ── CF profile constraint for variable onsite techs (solar, wind) ─────────
    # BuildAction precomputes the cache before the constraint rules are called.
    # Keys: (zone, energy_source_lowercase, timepoint) → average CF.
    # Only (zone, source) pairs with entries in gen_capacity_factor are cached;
    # baseload techs (nuclear, geothermal, gas CCS) have no CF data → skipped.

    def _build_td_onsite_cf_cache(m):
        from collections import defaultdict
        cache = {}
        if not hasattr(m, 'gen_max_capacity_factor'):
            m._td_onsite_cf_cache = cache
            return
        by_zone_src = defaultdict(list)
        for g in m.GENERATION_PROJECTS:
            src = value(m.gen_energy_source[g]).lower()
            zone = value(m.gen_load_zone[g])
            by_zone_src[(zone, src)].append(g)
        gen_cf = m.gen_max_capacity_factor
        for (zone, src), gens in by_zone_src.items():
            for tp in m.TIMEPOINTS:
                vals = [value(gen_cf[g, tp]) for g in gens if (g, tp) in gen_cf]
                if vals:
                    cache[(zone, src, tp)] = sum(vals) / len(vals)
        m._td_onsite_cf_cache = cache

    m.TDOnsiteCFCacheBuilder = BuildAction(rule=_build_td_onsite_cf_cache)

    def _onsite_cf_limit_rule(m, td, tech, tp):
        src = value(m.td_onsite_tech_cf_source[tech])
        if not src or src == ".":
            return Constraint.Skip
        zone = _td_zone(m, td)
        if zone is None:
            return Constraint.Skip
        cf = getattr(m, '_td_onsite_cf_cache', {}).get((zone, src.lower(), tp))
        if cf is None:
            return Constraint.Skip
        return m.DispatchOnsiteTech[td, tech, tp] <= m.BuildOnsiteTech[td, tech] * cf

    m.TDOnsiteDispatchCFLimit = Constraint(
        m.TD_TECH_TIMEPOINTS,
        rule=_onsite_cf_limit_rule,
    )

    # LP relaxation of min stable output. When Build = 0 this is Dispatch >= 0.
    m.TD_OnsiteTech_Dispatch_Min = Constraint(
        m.TD_TECH_TIMEPOINTS,
        rule=lambda m, td, tech, t: (
            m.DispatchOnsiteTech[td, tech, t]
            >= m.BuildOnsiteTech[td, tech]
            * m.td_onsite_tech_min_stable_mw_fraction[tech]
        ),
    )

    # Annual runtime cap: e.g. 200 hours for backup diesel.
    m.TD_OnsiteTech_Annual_Hours = Constraint(
        m.TD_TECH_BUILDS,
        m.PERIODS,
        rule=lambda m, td, tech, p: (
            sum(
                m.DispatchOnsiteTech[td, tech, t] * m.tp_weight_in_year[t]
                for t in m.TPS_IN_PERIOD[p]
            )
            <= m.BuildOnsiteTech[td, tech] * m.td_onsite_tech_max_annual_hours[tech]
        ),
    )

    # MIP build sizing: fixes BuildOnsiteTech to min_build * Flag + increment * Units.
    # When increment = 0 the expression is just min_build * Flag (exactly min or 0).
    # Entries not in TD_TECH_BUILDS_FLAG are unconstrained continuous LP variables.
    def _mip_build_rule(m, td, tech):
        min_b = m.td_onsite_tech_min_build_mw[tech]
        incr = m.td_onsite_tech_build_increment_mw[tech]
        rhs = min_b * m.BuildOnsiteTechFlag[td, tech]
        if incr > 0:
            rhs = rhs + incr * m.BuildOnsiteTechUnits[td, tech]
        return m.BuildOnsiteTech[td, tech] == rhs

    m.TD_OnsiteTech_MIP_Build = Constraint(
        m.TD_TECH_BUILDS_FLAG,
        rule=_mip_build_rule,
    )

    # Predetermined builds: fix capacity to the specified value.
    m.TD_OnsiteTech_Predetermined = Constraint(
        m.TD_PREDETERMINED_BUILDS,
        rule=lambda m, td, tech: (
            m.BuildOnsiteTech[td, tech] == m.td_onsite_predetermined_mw[td, tech]
        ),
    )

    # ── Objective function components ─────────────────────────────────────────

    # Capital cost is the same every period (build is persistent, not re-paid).
    m.TDOnsiteTechFixedCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.BuildOnsiteTech[td, tech] * m.td_onsite_tech_capital_cost[tech]
            for td, tech in m.TD_TECH_BUILDS
        ),
    )
    m.Cost_Components_Per_Period.append("TDOnsiteTechFixedCost")

    # Net variable cost: VOM + fuel - effective subsidy.
    m.TDOnsiteTechVariableCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.DispatchOnsiteTech[td, tech, t]
            * m.tp_weight_in_year[t]
            * (m.td_onsite_tech_variable_om[tech]
               + m.td_onsite_tech_fuel_cost[tech]
               - _onsite_subsidy(m, tech, p))
            for td, tech in m.TD_TECH_BUILDS
            for t in m.TPS_IN_PERIOD[p]
        ),
    )
    m.Cost_Components_Per_Period.append("TDOnsiteTechVariableCost")

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 4: Behind-the-meter battery storage
    # ══════════════════════════════════════════════════════════════════════════
    # TD_STORAGE, TD_STORAGE_TIMEPOINTS, TDStorageCharge, TDStorageDischarge
    # are declared earlier as Stage 4 prerequisites.

    # ── Parameters ───────────────────────────────────────────────────────────

    m.td_storage_power_cost = Param(
        m.TD_STORAGE,
        within=NonNegativeReals,
        doc="Annualized inverter/power cost ($/MW-yr).",
    )

    m.td_storage_energy_cost = Param(
        m.TD_STORAGE,
        within=NonNegativeReals,
        doc="Annualized cell/energy cost ($/MWh-yr).",
    )

    m.td_storage_max_hours = Param(
        m.TD_STORAGE,
        within=NonNegativeReals,
        default=8.0,
        doc="Maximum energy-to-power ratio (hours).",
    )

    m.td_storage_min_hours = Param(
        m.TD_STORAGE,
        within=NonNegativeReals,
        default=0.0,
        doc="Minimum energy-to-power ratio (hours).",
    )

    m.td_storage_roundtrip_eff = Param(
        m.TD_STORAGE,
        within=PercentFraction,
        default=0.85,
        doc="Round-trip efficiency [0-1]. Applied symmetrically as √rte per leg.",
    )

    # ── Decision variables ────────────────────────────────────────────────────

    m.TDStoragePowerCapacity = Var(
        m.TD_STORAGE,
        within=NonNegativeReals,
        doc="Battery power capacity built (MW).",
    )

    m.TDStorageEnergyCapacity = Var(
        m.TD_STORAGE,
        within=NonNegativeReals,
        doc="Battery energy capacity built (MWh).",
    )

    m.TDStorageLevel = Var(
        m.TD_STORAGE_TIMEPOINTS,
        within=NonNegativeReals,
        doc="Battery state of charge at end of timepoint (MWh).",
    )

    # ── Constraints ───────────────────────────────────────────────────────────

    m.TD_Storage_Charge_Limit = Constraint(
        m.TD_STORAGE_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDStorageCharge[td, t] <= m.TDStoragePowerCapacity[td]
        ),
    )

    m.TD_Storage_Discharge_Limit = Constraint(
        m.TD_STORAGE_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDStorageDischarge[td, t] <= m.TDStoragePowerCapacity[td]
        ),
    )

    m.TD_Storage_Level_Limit = Constraint(
        m.TD_STORAGE_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDStorageLevel[td, t] <= m.TDStorageEnergyCapacity[td]
        ),
    )

    m.TD_Storage_Energy_Min = Constraint(
        m.TD_STORAGE,
        rule=lambda m, td: (
            m.TDStorageEnergyCapacity[td]
            >= m.td_storage_min_hours[td] * m.TDStoragePowerCapacity[td]
        ),
    )

    m.TD_Storage_Energy_Max = Constraint(
        m.TD_STORAGE,
        rule=lambda m, td: (
            m.TDStorageEnergyCapacity[td]
            <= m.td_storage_max_hours[td] * m.TDStoragePowerCapacity[td]
        ),
    )

    # SoC tracking: cyclic within each timeseries via tp_previous.
    # Efficiency loss split symmetrically: √rte applied to each leg.
    def _storage_soc_rule(m, td, t):
        sqrt_eff = m.td_storage_roundtrip_eff[td] ** 0.5
        dt = m.tp_duration_hrs[t]
        return (
            m.TDStorageLevel[td, t]
            == m.TDStorageLevel[td, m.tp_previous[t]]
            + m.TDStorageCharge[td, t] * sqrt_eff * dt
            - m.TDStorageDischarge[td, t] / sqrt_eff * dt
        )

    m.TD_Storage_Track_Level = Constraint(
        m.TD_STORAGE_TIMEPOINTS,
        rule=_storage_soc_rule,
    )

    # ── Objective function component ──────────────────────────────────────────

    # ── Clean sub-ledger of the battery (§108 (b)) ────────────────────────────
    # Only for TDs with CFE targets. Clean charge comes from on-site clean
    # output or the grid's clean share; the rest of the charge must come from
    # non-clean sources (grid's non-clean share, non-clean on-site output,
    # linked generators), so clean can't be relabelled. Clean discharge is
    # bounded by the clean level, which tracks the battery's own losses.

    m.TD_Clean_Charge_Onsite_Limit = Constraint(
        m.TD_CFE_STORAGE_TPS,
        rule=lambda m, td, t: m.TDCleanChargeOnsite[td, t] <= _onsite_clean(m, td, t),
    )
    m.TD_Clean_Charge_Limit = Constraint(
        m.TD_CFE_STORAGE_TPS,
        rule=lambda m, td, t: (
            m.TDCleanChargeOnsite[td, t] + m.TDCleanChargeGrid[td, t] <= m.TDStorageCharge[td, t]
        ),
    )

    def _nonclean_charge_rule(m, td, t):
        z = _td_zone(m, td)
        nonclean_onsite = sum(
            m.DispatchOnsiteTech[td, tech, t]
            for tech in m.TD_ONSITE_TECHS
            if not m.td_onsite_tech_is_clean[tech]
        )
        return (
            m.TDStorageCharge[td, t] - m.TDCleanChargeOnsite[td, t] - m.TDCleanChargeGrid[td, t]
            <= (1 - m.grid_clean_fraction[z, t]) * m.TDGridDraw[td, t]
            + nonclean_onsite
            + m.TDOnsiteGenMW[td, t]
        )

    m.TD_NonClean_Charge_Sources = Constraint(m.TD_CFE_STORAGE_TPS, rule=_nonclean_charge_rule)
    m.TD_Clean_Discharge_Limit = Constraint(
        m.TD_CFE_STORAGE_TPS,
        rule=lambda m, td, t: m.TDCleanDischarge[td, t] <= m.TDStorageDischarge[td, t],
    )
    m.TD_Clean_Level_Limit = Constraint(
        m.TD_CFE_STORAGE_TPS,
        rule=lambda m, td, t: m.TDCleanLevel[td, t] <= m.TDStorageLevel[td, t],
    )

    def _clean_level_rule(m, td, t):
        sqrt_eff = m.td_storage_roundtrip_eff[td] ** 0.5
        dt = m.tp_duration_hrs[t]
        return (
            m.TDCleanLevel[td, t]
            == m.TDCleanLevel[td, m.tp_previous[t]]
            + (m.TDCleanChargeOnsite[td, t] + m.TDCleanChargeGrid[td, t]) * sqrt_eff * dt
            - m.TDCleanDischarge[td, t] / sqrt_eff * dt
        )

    m.TD_Clean_Storage_Track_Level = Constraint(m.TD_CFE_STORAGE_TPS, rule=_clean_level_rule)

    # Same capital cost each period (persistent build, not re-paid).
    m.TDStorageFixedCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.TDStoragePowerCapacity[td] * m.td_storage_power_cost[td]
            + m.TDStorageEnergyCapacity[td] * m.td_storage_energy_cost[td]
            for td in m.TD_STORAGE
        ),
    )
    m.Cost_Components_Per_Period.append("TDStorageFixedCost")

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 7: Location-flexible siting
    # ══════════════════════════════════════════════════════════════════════════
    # TD_LOCATION_FLEX, TD_FLEX_ZONE_TIMEPOINTS, and TDGridDrawInZone are
    # declared as Stage 7 prerequisites above (before TDGridDrawByZone).
    # This section adds the allocation variable and the three constraints that
    # enforce a valid, capacity-consistent zone allocation.
    #
    # For location-fixed TDs (the common case) all three new sets are empty
    # and no variables or constraints are added — zero overhead.

    # (td, z, p) for flexible TDs × candidate zones × periods.
    m.TD_FLEX_ZONE_PERIODS = Set(
        dimen=3,
        initialize=lambda m: [
            (td, z, p)
            for td, z in m.TD_CANDIDATE_ZONES
            if td in m.TD_LOCATION_FLEX
            for p in m.PERIODS
        ],
    )

    # Share of TD capacity (and therefore interconnect) allocated to zone z
    # in period p. Continuous [0,1]; sums to 1 across candidate zones.
    m.TDZoneAllocation = Var(
        m.TD_FLEX_ZONE_PERIODS,
        within=PercentFraction,
        doc="Fraction of TD grid interconnect capacity allocated to zone z.",
    )

    # Allocation shares must sum to 1 per (td, period).
    m.TD_ZoneAllocation_Sum = Constraint(
        m.TD_LOCATION_FLEX,
        m.PERIODS,
        rule=lambda m, td, p: (
            sum(m.TDZoneAllocation[td, z, p] for z in _candidate_zones_for_td(m, td))
            == 1
        ),
    )

    # Grid draw in zone z bounded by allocated share of interconnect capacity.
    # This linearises the otherwise-bilinear siting × dispatch product by
    # fixing the capacity rather than the instantaneous draw.
    m.TD_ZoneAllocation_Capacity = Constraint(
        m.TD_FLEX_ZONE_TIMEPOINTS,
        rule=lambda m, td, z, t: (
            m.TDGridDrawInZone[td, z, t]
            <= m.TDZoneAllocation[td, z, m.tp_period[t]] * _fixed_connection(m, td)
        ),
    )

    # Total zonal draws must equal the TD's total grid draw each timepoint.
    m.TD_ZoneAllocation_GridDraw = Constraint(
        m.TD_LOCATION_FLEX,
        m.TIMEPOINTS,
        rule=lambda m, td, t: (
            sum(m.TDGridDrawInZone[td, z, t] for z in _candidate_zones_for_td(m, td))
            == m.TDGridDraw[td, t]
        ),
    )

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 5: Grid flexibility events
    # ══════════════════════════════════════════════════════════════════════════
    # Optional soft constraint on grid draw at specific (td, t) pairs.
    # Events are specified per time_block (or direct TIMEPOINT) and expanded
    # to individual timepoints in load_inputs via _expand_flex_events.
    # Excess above the limit is penalised at td_flex_noncompliance_penalty.

    m.TD_FLEX_TIMEPOINTS = Set(dimen=2, within=Any)

    m.td_flex_max_grid_draw_mw = Param(
        m.TD_FLEX_TIMEPOINTS,
        within=NonNegativeReals,
        doc="Maximum grid draw during this flex event (MW).",
    )

    m.td_flex_noncompliance_penalty = Param(
        m.TD_FLEX_TIMEPOINTS,
        within=NonNegativeReals,
        default=500.0,
        doc="Penalty for exceeding grid draw limit ($/MWh of excess).",
    )

    m.TDFlexExcess = Var(
        m.TD_FLEX_TIMEPOINTS,
        within=NonNegativeReals,
        doc="Grid draw above flex event limit (MW); penalised in objective.",
    )

    m.TD_Flex_Grid_Limit = Constraint(
        m.TD_FLEX_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDGridDraw[td, t]
            <= m.td_flex_max_grid_draw_mw[td, t] + m.TDFlexExcess[td, t]
        ),
    )

    m.TDFlexNoncomplianceCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.TDFlexExcess[td, t]
            * m.td_flex_noncompliance_penalty[td, t]
            * m.tp_weight_in_year[t]
            for td, t in m.TD_FLEX_TIMEPOINTS
            if m.tp_period[t] == p
        ),
    )
    m.Cost_Components_Per_Period.append("TDFlexNoncomplianceCost")

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 8: Hydrogen storage
    # ══════════════════════════════════════════════════════════════════════════
    # For electrolyzer TDs: electricity → H2 production → H2 tank → delivery.
    # TDDispatch is the electrical load; TDH2Production is derived from it via
    # efficiency. The SoC constraint is cyclic (same pattern as Stage 4).

    m.TD_H2_STORAGE = Set(dimen=1, within=Any)

    m.TD_H2_STORAGE_TIMEPOINTS = Set(
        dimen=2,
        initialize=lambda m: [
            (td, t) for td in m.TD_H2_STORAGE for t in m.TIMEPOINTS
        ],
    )

    # ── Parameters ────────────────────────────────────────────────────────────

    m.td_electrolyzer_efficiency_kwh_per_kg = Param(
        m.TD_H2_STORAGE,
        within=NonNegativeReals,
        default=50.0,
        doc="Electricity consumed per kg H2 produced (kWh/kg).",
    )

    m.td_h2_storage_cost_per_kg_yr = Param(
        m.TD_H2_STORAGE,
        within=NonNegativeReals,
        doc="Annualized tank capital cost ($/kg-yr).",
    )

    # Default 1e9 ≈ unconstrained for any realistic electrolyzer size.
    m.td_h2_storage_max_kg = Param(
        m.TD_H2_STORAGE,
        within=NonNegativeReals,
        default=1e9,
        doc="Maximum tank capacity (kg).",
    )

    m.td_h2_min_delivery_kg_per_hr = Param(
        m.TD_H2_STORAGE,
        within=NonNegativeReals,
        default=0.0,
        doc="Minimum delivery rate (kg/hr). 0 = no minimum.",
    )

    m.td_h2_max_delivery_kg_per_hr = Param(
        m.TD_H2_STORAGE,
        within=NonNegativeReals,
        default=1e9,
        doc="Maximum delivery rate (kg/hr).",
    )

    # ── Decision variables ────────────────────────────────────────────────────

    m.TDH2Production = Var(
        m.TD_H2_STORAGE_TIMEPOINTS,
        within=NonNegativeReals,
        doc="H2 produced (kg/hr).",
    )

    m.TDH2StorageCapacity = Var(
        m.TD_H2_STORAGE,
        within=NonNegativeReals,
        doc="H2 tank capacity (kg).",
    )

    m.TDH2StorageLevel = Var(
        m.TD_H2_STORAGE_TIMEPOINTS,
        within=NonNegativeReals,
        doc="H2 inventory at end of timepoint (kg).",
    )

    m.TDH2Delivery = Var(
        m.TD_H2_STORAGE_TIMEPOINTS,
        within=NonNegativeReals,
        doc="H2 delivered to offtake (kg/hr).",
    )

    # ── Constraints ───────────────────────────────────────────────────────────

    # TDH2Production[kg/hr] × efficiency[kWh/kg] / 1000 = TDDispatch[MW]
    m.TD_H2_Production_Link = Constraint(
        m.TD_H2_STORAGE_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDH2Production[td, t]
            * (m.td_electrolyzer_efficiency_kwh_per_kg[td] / 1000.0)
            == m.TDDispatch[td, t]
        ),
    )

    # SoC tracking: cyclic within timeseries via tp_previous.
    m.TD_H2_Track_Level = Constraint(
        m.TD_H2_STORAGE_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDH2StorageLevel[td, t]
            == m.TDH2StorageLevel[td, m.tp_previous[t]]
            + (m.TDH2Production[td, t] - m.TDH2Delivery[td, t])
            * m.tp_duration_hrs[t]
        ),
    )

    m.TD_H2_Level_Cap = Constraint(
        m.TD_H2_STORAGE_TIMEPOINTS,
        rule=lambda m, td, t: m.TDH2StorageLevel[td, t] <= m.TDH2StorageCapacity[td],
    )

    m.TD_H2_Storage_Cap = Constraint(
        m.TD_H2_STORAGE,
        rule=lambda m, td: m.TDH2StorageCapacity[td] <= m.td_h2_storage_max_kg[td],
    )

    # Skip min-delivery constraint when default 0 (TDH2Delivery >= 0 from Var domain).
    m.TD_H2_Delivery_Min = Constraint(
        m.TD_H2_STORAGE_TIMEPOINTS,
        rule=lambda m, td, t: (
            m.TDH2Delivery[td, t] >= m.td_h2_min_delivery_kg_per_hr[td]
            if value(m.td_h2_min_delivery_kg_per_hr[td]) > 0
            else Constraint.Skip
        ),
    )

    m.TD_H2_Delivery_Max = Constraint(
        m.TD_H2_STORAGE_TIMEPOINTS,
        rule=lambda m, td, t: m.TDH2Delivery[td, t] <= m.td_h2_max_delivery_kg_per_hr[td],
    )

    # ── Objective function component ──────────────────────────────────────────

    m.TDH2StorageFixedCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.TDH2StorageCapacity[td] * m.td_h2_storage_cost_per_kg_yr[td]
            for td in m.TD_H2_STORAGE
        ),
    )
    m.Cost_Components_Per_Period.append("TDH2StorageFixedCost")

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 9: Per-source eligible energy and MW caps
    # ══════════════════════════════════════════════════════════════════════════
    # Three independent optional mechanisms; each is activated only when its
    # CSV is present.
    #
    #   9A  On-site build caps: per-(TD, tech) MW ceiling on BuildOnsiteTech.
    #   9B  Annual grid draw caps: per-(TD, period) floor/ceiling on total
    #       grid MWh drawn.
    #   9C  Grid clean credit caps: per-(TD, period, block) ceiling on
    #       TDBlockGridCleanMWh. Limits CFE credit from the grid, forcing
    #       the remaining target to come from on-site clean resources or be
    #       paid as shortfall penalty.

    m.TD_ONSITE_BUILD_CAPS = Set(dimen=2, within=Any)   # (td, tech)
    m.TD_ANNUAL_GRID_CAPS = Set(dimen=2, within=Any)    # (td, period)
    m.TD_GRID_CLEAN_CAPS = Set(dimen=3, within=Any)     # (td, period, time_block)

    m.td_onsite_build_mw_cap = Param(
        m.TD_ONSITE_BUILD_CAPS,
        within=NonNegativeReals,
        doc="Maximum on-site tech capacity (MW) for this (TD, tech) pair.",
    )

    m.td_annual_grid_max_mwh = Param(
        m.TD_ANNUAL_GRID_CAPS,
        within=NonNegativeReals,
        default=1e12,
        doc="Annual grid draw ceiling (MWh/yr).",
    )

    m.td_annual_grid_min_mwh = Param(
        m.TD_ANNUAL_GRID_CAPS,
        within=NonNegativeReals,
        default=0.0,
        doc="Annual grid draw floor (MWh/yr).",
    )

    m.td_grid_clean_max_mwh = Param(
        m.TD_GRID_CLEAN_CAPS,
        within=NonNegativeReals,
        doc="Max grid clean MWh credited toward CFE for this (TD, period, block).",
    )

    m.TD_Onsite_Build_Cap = Constraint(
        m.TD_ONSITE_BUILD_CAPS,
        rule=lambda m, td, tech: m.BuildOnsiteTech[td, tech] <= m.td_onsite_build_mw_cap[td, tech],
    )

    m.TD_Annual_Grid_Max = Constraint(
        m.TD_ANNUAL_GRID_CAPS,
        rule=lambda m, td, p: (
            sum(m.TDGridDraw[td, t] * m.tp_weight_in_year[t] for t in m.TPS_IN_PERIOD[p])
            <= m.td_annual_grid_max_mwh[td, p]
        ),
    )

    m.TD_Annual_Grid_Min = Constraint(
        m.TD_ANNUAL_GRID_CAPS,
        rule=lambda m, td, p: (
            sum(m.TDGridDraw[td, t] * m.tp_weight_in_year[t] for t in m.TPS_IN_PERIOD[p])
            >= m.td_annual_grid_min_mwh[td, p]
        ),
    )

    m.TD_Grid_Clean_Cap = Constraint(
        m.TD_GRID_CLEAN_CAPS,
        rule=lambda m, td, p, blk: (
            m.TDBlockGridCleanMWh[td, p, blk] <= m.td_grid_clean_max_mwh[td, p, blk]
        ),
    )

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 11: REC purchasing
    # ══════════════════════════════════════════════════════════════════════════
    # TD_REC_SUPPLY and TDBlockRECPurchase are declared earlier (Stage 11
    # prerequisites) and already wired into _cfe_constraint_rule (Stage 2).
    # This section defines params, the supply cap, and cost.
    #
    # A REC purchase claims clean-energy attribution from regional new clean
    # generators beyond the TD's proportionate grid-draw share. Subject to:
    #   - Hourly matching: same time block as the CFE target
    #   - Regional deliverability: supply cap derived from the same h2ptcreg
    #     region as the TD's load zone
    #   - Additionality: td_rec_supply_mwh should reflect only generation from
    #     newly built regional clean capacity (derive from a prior dispatch.csv
    #     run using _compute_new_clean_gen_by_block in post_solve)
    #
    # Input: tracked_demand_rec_supply.csv (optional)
    #   TRACKED_DEMAND, PERIOD, time_block, td_rec_supply_mwh, td_rec_cost_per_mwh

    m.td_rec_supply_mwh = Param(
        m.TD_REC_SUPPLY,
        within=NonNegativeReals,
        doc="Maximum RECs purchasable for this (td, period, block) in MWh/yr.",
    )

    m.td_rec_cost_per_mwh = Param(
        m.TD_REC_SUPPLY,
        within=NonNegativeReals,
        default=0.0,
        doc="REC cost ($/MWh). Added to objective per MWh purchased.",
    )

    m.TD_REC_Supply_Cap = Constraint(
        m.TD_REC_SUPPLY,
        rule=lambda m, td, p, blk: (
            m.TDBlockRECPurchase[td, p, blk] <= m.td_rec_supply_mwh[td, p, blk]
        ),
    )

    m.TDRECCost = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.TDBlockRECPurchase[td, p2, blk] * m.td_rec_cost_per_mwh[td, p2, blk]
            for td, p2, blk in m.TD_REC_SUPPLY
            if p2 == p
        ),
    )
    m.Cost_Components_Per_Period.append("TDRECCost")

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 12: carbon-cap coverage of on-site generators (CHANGES §108)
    # ══════════════════════════════════════════════════════════════════════════
    # Linked on-site generators are ordinary generators, so
    # carbon_policies_regional counts their emissions in every programme whose
    # zones they sit in. tracked_demand_cap_coverage.csv sets, per programme,
    # whether on-site emissions count and the programme's applicability test:
    #   td_cap_counts_onsite              1 (default) / 0
    #   td_cap_threshold_mw               nameplate threshold (RGGI, VA: 25 MWe)
    #   td_cap_threshold_tco2             emissions threshold (CA, WA: 25,000 t/yr)
    #   td_cap_threshold_basis            unit (default) / site
    #   td_cap_limited_export_exemption   1: exempt a site whose export limit
    #                                     share is <= 0.1 (RGGI-style permit)
    # A generator below a threshold is exempt from that programme: its emissions
    # come out of the cap row (TDCapCoverage below). Programmes not listed: on-site
    # emissions count, no threshold. Applicability is fixed before the solve from
    # declared sizes (unit / rated MW, potential to emit at rated output);
    # post_solve checks actual emissions against tCO2 thresholds.

    m.TD_CAP_PROGRAMS = Set(dimen=1, within=Any)
    m.td_cap_counts_onsite = Param(m.TD_CAP_PROGRAMS, within=Any, default=1)
    m.td_cap_threshold_mw = Param(m.TD_CAP_PROGRAMS, within=Any, default=".")
    m.td_cap_threshold_tco2 = Param(m.TD_CAP_PROGRAMS, within=Any, default=".")
    m.td_cap_threshold_basis = Param(
        m.TD_CAP_PROGRAMS, within=Any, default="unit",
        validate=lambda m, v, pr: str(v).strip() in ("unit", "site"),
    )
    m.td_cap_limited_export_exemption = Param(m.TD_CAP_PROGRAMS, within=Any, default=0)
    # §140: a programme that never covers backup diesel at the facility (CA, WA)
    # names its rule here; such a fleet is not covered and stays out of the
    # programme's site-basis sums, whatever its potential to emit
    m.td_cap_backup_diesel_rule = Param(m.TD_CAP_PROGRAMS, within=Any, default="")

    # Per-state MW threshold overrides within a programme
    # (tracked_demand_cap_coverage_states.csv: CO2_PROGRAM, STATE,
    # td_cap_state_threshold_mw, td_cap_state_condition). Condition `always`,
    # or `at_budget_source_or_two_units` (New York, 6 NYCRR 242-1.4(a)(2): a
    # unit of >= the state threshold is covered at a site that already has a
    # unit at or above the programme's threshold, or that has two or more units
    # at or above the state threshold). Zone -> state:
    # tracked_demand_zone_states.csv (LOAD_ZONE, STATE), else hierarchy.csv
    # (ba -> st) found upward from the inputs folder.
    m.TD_CAP_STATE_RULES = Set(dimen=2, within=Any)
    m.td_cap_state_threshold_mw = Param(m.TD_CAP_STATE_RULES, within=NonNegativeReals)
    m.td_cap_state_condition = Param(
        m.TD_CAP_STATE_RULES, within=Any, default="always",
        validate=lambda m, v, pr, st: str(v).strip() in ("always", "at_budget_source_or_two_units"),
    )
    m.td_zone_state = Param(m.LOAD_ZONES, within=Any, default="")

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 13: carry-forward between chain stages (CHANGES §108 (c))
    # ══════════════════════════════════════════════════════════════════════════
    # Written by chain_inputs() (called from prepare_next_stage) as
    # <file>.chained.<case>.csv in the next stage's inputs folder; load_inputs
    # reads the chained version when present (an explicit alias wins).
    #   tracked_demand_onsite_existing.csv   on-site MW by (TD, tech)
    #   tracked_demand_storage_existing.csv  battery MW and MWh by TD
    #   tracked_demand_h2_existing.csv       H2 tank kg by TD
    #   tracked_demand_siting_fixed.csv      allocation of a location-flexible TD
    #   tracked_demand_cap_coverage_fixed.csv  cap coverage by (TD, generator, programme)
    # Existing capacity is a lower bound on the stage's capacity (expandable)
    # and keeps paying its annualised cost, like predetermined generators.

    m.TD_ONSITE_EXISTING = Set(dimen=2, within=m.TD_TECH_BUILDS)
    m.td_onsite_existing_mw = Param(m.TD_ONSITE_EXISTING, within=NonNegativeReals)
    m.TD_OnsiteTech_Existing = Constraint(
        m.TD_ONSITE_EXISTING,
        rule=lambda m, td, tech: m.BuildOnsiteTech[td, tech] >= m.td_onsite_existing_mw[td, tech],
    )

    m.TD_STORAGE_EXISTING = Set(dimen=1, within=m.TD_STORAGE)
    m.td_storage_existing_mw = Param(m.TD_STORAGE_EXISTING, within=NonNegativeReals, default=0.0)
    m.td_storage_existing_mwh = Param(m.TD_STORAGE_EXISTING, within=NonNegativeReals, default=0.0)
    m.TD_Storage_Existing_MW = Constraint(
        m.TD_STORAGE_EXISTING,
        rule=lambda m, td: m.TDStoragePowerCapacity[td] >= m.td_storage_existing_mw[td],
    )
    m.TD_Storage_Existing_MWh = Constraint(
        m.TD_STORAGE_EXISTING,
        rule=lambda m, td: m.TDStorageEnergyCapacity[td] >= m.td_storage_existing_mwh[td],
    )

    m.TD_H2_EXISTING = Set(dimen=1, within=m.TD_H2_STORAGE)
    m.td_h2_existing_kg = Param(m.TD_H2_EXISTING, within=NonNegativeReals)
    m.TD_H2_Existing = Constraint(
        m.TD_H2_EXISTING,
        rule=lambda m, td: m.TDH2StorageCapacity[td] >= m.td_h2_existing_kg[td],
    )

    # siting of a location-flexible TD, fixed after the first stage (zones not
    # listed get 0)
    m.TD_SITING_FIXED = Set(dimen=2, within=m.TD_CANDIDATE_ZONES)
    m.td_fixed_allocation = Param(m.TD_SITING_FIXED, within=PercentFraction)
    def _siting_fixed_rule(m, td, z, p):
        shares = {z2: value(m.td_fixed_allocation[td2, z2]) for td2, z2 in m.TD_SITING_FIXED if td2 == td}
        if not shares:
            return Constraint.Skip
        total = sum(shares.values())
        if total <= 0:
            raise ValueError(f"tracked_demands: tracked_demand_siting_fixed.csv gives {td} no allocation")
        # normalised, so rounded carried shares still sum to exactly 1 (TD_ZoneAllocation_Sum)
        return m.TDZoneAllocation[td, z, p] == shares.get(z, 0.0) / total

    m.TD_Siting_Fixed = Constraint(m.TD_FLEX_ZONE_PERIODS, rule=_siting_fixed_rule)

    # ── PRM load by zone (§108 (d)) ───────────────────────────────────────────
    # TDPrmLoad[z, p]: added to prm_regional's served load in every stress hour
    # of p. TDPrmFixedLoadMW[z, p]: the part known before the solve (location-
    # fixed TDs, and flexible TDs whose siting is fixed), added to the region
    # peak (prm_region_peak_mw, the import-cap basis), which is a parameter.

    def _prm_load_rule(m, z, p):
        out = 0
        for td in m.TRACKED_DEMANDS:
            if value(m.td_location_fixed[td]):
                if _td_zone(m, td) == z:
                    out = out + _prm_requirement(m, td, p)
            elif (td, z, p) in m.TD_FLEX_ZONE_PERIODS:
                out = out + m.TDZoneAllocation[td, z, p] * m.td_prm_requirement_mw[td, p]
        return out

    m.TDPrmLoad = Expression(m.LOAD_ZONES, m.PERIODS, rule=_prm_load_rule)

    def _prm_fixed_load_rule(m, z, p):
        out = 0.0
        for td in m.TRACKED_DEMANDS:
            if td in m.TD_ENDOGENOUS_CONNECTION:
                continue    # a decision, so not in the region peak (a parameter); see the guide, 15.7
            if value(m.td_location_fixed[td]):
                if _td_zone(m, td) == z:
                    out += value(m.td_prm_requirement_mw[td, p])
            elif (td, z) in m.TD_SITING_FIXED:
                shares = {z2: value(m.td_fixed_allocation[td2, z2]) for td2, z2 in m.TD_SITING_FIXED if td2 == td}
                out += shares[z] / sum(shares.values()) * value(m.td_prm_requirement_mw[td, p])
        return out

    m.TDPrmFixedLoadMW = Param(m.LOAD_ZONES, m.PERIODS, within=NonNegativeReals, initialize=_prm_fixed_load_rule)

    # cap coverage of linked generators carried from the stage where it was
    # first decided (used instead of the applicability tests)
    m.TD_CAP_COVERAGE_FIXED = Set(dimen=3, within=Any)
    m.td_cap_covered = Param(m.TD_CAP_COVERAGE_FIXED, within=Any)

    m.TDCapCoverage = BuildAction(rule=_apply_cap_coverage)

    # gen_zone_ratio's load side (§108 (e)): fill its placeholder with each
    # zone's tracked-demand withdrawal, net of export and including linked
    # generators (which gen_zone_ratio counts as zone generation).
    m.TDGenRatioLoad = BuildAction(rule=_fill_gen_ratio_load)

    # interconnection_headroom's headroom use (§108 (f)): fill its placeholder
    # with the tracked demands' headroom draw in each zone
    m.TDHeadroomDraw = BuildAction(rule=_fill_headroom_draw)


# ── Stage 12 helpers: cap coverage ────────────────────────────────────────────

LIMITED_EXPORT_SHARE = 0.1   # RGGI Model Rule / 9VAC5-140-7040 B: <= 10% of gross generation to the grid


def _flag(v):
    return str(v).strip() in ("1", "1.0", "True", "true", "yes")


def _rated_mw(m, td, g):
    v = _float_or_none(value(m.td_onsite_rated_mw[td, g]))
    if v is None and g in m.CAPACITY_LIMITED_GENS:
        v = value(m.gen_capacity_limit_mw[g])
    return v


def _unit_mw(m, td, g):
    v = _float_or_none(value(m.td_onsite_unit_mw[td, g]))
    if v is None and g in m.DISCRETELY_SIZED_GENS:
        v = value(m.gen_unit_size[g])
    return _rated_mw(m, td, g) if v is None else v


def _pte_hours(m, td, g):
    """Hours per year of potential to emit: td_onsite_pte_hours, else a backup diesel's permit hours, else all year
    (8766)."""
    v = _float_or_none(value(m.td_onsite_pte_hours[td, g])) if td is not None else None
    if v is None and (td, g) in m.TD_DIESEL_GENS:
        v = value(m.td_diesel_permit_flh[td, g])
    return 8766 if v is None else v


def _pte_tco2(m, g, mw, td=None):
    """Potential to emit (tCO2/yr) of mw of generator g at full load for its potential-to-emit hours (all year
    unless td_onsite_pte_hours or a diesel permit says otherwise; §140)."""
    rate = max(
        value(m.f_co2_intensity[f])
        * ((1 - value(m.gen_ccs_capture_efficiency[g])) if g in m.CCS_EQUIPPED_GENS else 1)
        for f in m.FUELS_FOR_GEN[g]
    )
    return mw * _pte_hours(m, td, g) * value(m.gen_full_load_heat_rate[g]) * rate


def _cap_programs_of_zone(m):
    out = {}
    for pr, pe in getattr(m, "CO2_PROGRAM_PERIODS", ()):
        for z in m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, pe]:
            out.setdefault(z, set()).add(pr)
    return out


def _cap_coverage(m):
    """{(td, g, programme): (covered, reason)} for linked fuel-based generators in a programme's zones."""
    if hasattr(m, "_td_cap_coverage"):
        return m._td_cap_coverage
    progs = _cap_programs_of_zone(m)
    cov = {}
    for td in m.TRACKED_DEMANDS:
        gens = [g for g in _onsite_gens(m, td) if g in m.FUEL_BASED_GENS]
        for g in gens:
            for pr in sorted(progs.get(m.gen_load_zone[g], ()), key=str):
                if (td, g, pr) in m.TD_CAP_COVERAGE_FIXED:
                    cov[td, g, pr] = (_flag(m.td_cap_covered[td, g, pr]), "carried_from_earlier_stage")
                    continue
                listed = pr in m.TD_CAP_PROGRAMS
                if listed and not _flag(m.td_cap_counts_onsite[pr]):
                    cov[td, g, pr] = (False, "programme_excludes_onsite")
                    continue
                bd_rule = str(value(m.td_cap_backup_diesel_rule[pr])).strip() if listed else ""
                if bd_rule in (".", "nan"):
                    bd_rule = ""
                if bd_rule and (td, g) in m.TD_DIESEL_GENS:
                    cov[td, g, pr] = (False, f"backup_diesel_not_covered ({bd_rule})")
                    continue
                if (listed and _flag(m.td_cap_limited_export_exemption[pr])
                        and _export_share(m, td) <= LIMITED_EXPORT_SHARE):
                    cov[td, g, pr] = (False, "limited_export_exemption")
                    continue
                th_mw = _float_or_none(value(m.td_cap_threshold_mw[pr])) if listed else None
                th_t = _float_or_none(value(m.td_cap_threshold_tco2[pr])) if listed else None
                if th_mw is None and th_t is None:
                    cov[td, g, pr] = (True, "covered")
                    continue
                th_mw, rule = _state_mw_threshold(m, td, g, pr, gens, th_mw)
                site = listed and str(value(m.td_cap_threshold_basis[pr])).strip() == "site"
                if site:
                    members = [h for h in gens if m.gen_load_zone[h] == m.gen_load_zone[g]
                               and not (bd_rule and (td, h) in m.TD_DIESEL_GENS)]
                    sizes = {h: _rated_mw(m, td, h) for h in members}
                    what = "rated MW (td_onsite_rated_mw or gen_capacity_limit_mw)"
                else:
                    members = [g]
                    sizes = {g: _unit_mw(m, td, g)}
                    what = "unit MW (td_onsite_unit_mw, gen_unit_size or a rated MW)"
                missing = [h for h, v in sizes.items() if v is None]
                if missing:
                    raise ValueError(
                        f"tracked_demands: programme {pr} tests on-site applicability on a {'site' if site else 'unit'} "
                        f"basis, but {', '.join(map(str, missing))} (tracked demand {td}) has no {what}."
                    )
                mw = sum(sizes.values())
                pte = sum(_pte_tco2(m, h, v, td) for h, v in sizes.items())
                if th_mw is not None and mw < th_mw:
                    cov[td, g, pr] = (False, f"below_mw_threshold ({mw:g} < {th_mw:g} MW{rule})")
                elif th_t is not None and pte < th_t:
                    cov[td, g, pr] = (False, f"below_tco2_threshold (potential {pte:.0f} < {th_t:g} t/yr)")
                else:
                    cov[td, g, pr] = (True, "covered")
    m._td_cap_coverage = cov
    return cov


def _zone_state(m, z):
    st = str(value(m.td_zone_state[z])).strip()
    if st:
        return st
    if not hasattr(m, "_td_hier_states"):
        m._td_hier_states = {}
        path = _find_hierarchy_file(m.options.inputs_dir) if getattr(m.options, "inputs_dir", None) else None
        if path is not None:
            h = pd.read_csv(path, dtype=str)
            if {"ba", "st"} <= set(h.columns):
                m._td_hier_states = dict(zip(h["ba"], h["st"]))
    return m._td_hier_states.get(z, "")


def _site_units(m, td, gens, z):
    """[(unit MW, count)] of td's linked fuel generators in zone z (count = rated / unit, at least 1)."""
    out = []
    for h in gens:
        if m.gen_load_zone[h] != z:
            continue
        u, r = _unit_mw(m, td, h), _rated_mw(m, td, h)
        if u is None:
            continue
        n = max(1, int(round(r / u))) if (r is not None and u > 0 and r != float("inf")) else 1
        out.append((u, n))
    return out


def _state_mw_threshold(m, td, g, pr, gens, th_mw):
    """The MW threshold for generator g in programme pr after any state override, and a note for the reason."""
    z = m.gen_load_zone[g]
    st = _zone_state(m, z) if len(m.TD_CAP_STATE_RULES) else ""
    if not st or (pr, st) not in m.TD_CAP_STATE_RULES:
        return th_mw, ""
    over = value(m.td_cap_state_threshold_mw[pr, st])
    cond = str(value(m.td_cap_state_condition[pr, st])).strip()
    if cond == "always":
        return over, f", {st} rule"
    units = _site_units(m, td, gens, z)
    at_source = th_mw is not None and any(u >= th_mw for u, _ in units)
    two_units = sum(n for u, n in units if u >= over) >= 2
    if at_source or two_units:
        return over, f", {st} rule: {'site is a budget source' if at_source else 'two or more units'}"
    return th_mw, f", {st} rule not met"


def _headroom_draw_on(m):
    return str(getattr(m.options, "td_headroom_draw", "on")).strip() != "off"


def _fill_headroom_draw(m):
    if not len(m.TD_HEADROOM_TDS):
        return
    if not hasattr(m, "ICTrackedDemandDrawMW"):
        raise ValueError(
            "tracked_demands: td_connection_uses_headroom is set but interconnection_headroom isn't loaded"
        )
    for td in m.TD_HEADROOM_TDS:
        if not value(m.td_location_fixed[td]):
            raise ValueError(f"tracked_demands: {td} draws headroom, so it must be location-fixed")
        z = _td_zone(m, td)
        if z not in m.IC_ACTIVE_LOAD_ZONES:
            print(f"tracked_demands: {td} draws headroom in {z}, which has no headroom limit; the draw does nothing")
    for z in m.LOAD_ZONES:
        draw = [m.TDHeadroomDrawMW[td] for td in m.TD_HEADROOM_TDS if _td_zone(m, td) == z]
        if draw:
            for p in m.PERIODS:
                m.ICTrackedDemandDrawMW[z, p].set_value(sum(draw))


def _fill_gen_ratio_load(m):
    if not hasattr(m, "GenRatioTrackedLoadMW"):
        return
    for z in m.LOAD_ZONES:
        for t in m.TIMEPOINTS:
            m.GenRatioTrackedLoadMW[z, t].set_value(m.TDGridDrawByZone[z, t])


def _apply_cap_coverage(m):
    """Take exempt on-site emissions out of carbon_policies_regional's cap rows.

    A hook from this module: the cap row is built before this module's components, so it can't read them. Each row
    of Enforce_Regional_Carbon_Cap is `emissions x 0.001 <= allowances x 0.001` (carbon_policies_regional.cap_rule);
    exempt emissions are subtracted on the left in the same scaling. carbon_policies_regional's post_solve reads
    m.td_cap_exempt_emissions so its reported emissions match the row.
    """
    m.td_cap_exempt_emissions = {}
    if not hasattr(m, "Enforce_Regional_Carbon_Cap"):
        return
    cap = m.Enforce_Regional_Carbon_Cap
    if not cap.is_constructed():
        raise RuntimeError("tracked_demands must be loaded after carbon_policies_regional")
    exempt = {pr: set() for pr in m.TD_CAP_PROGRAMS}
    for (td, g, pr), (covered, _) in _cap_coverage(m).items():
        if not covered:
            exempt.setdefault(pr, set()).add(g)
    light = getattr(m, "LIGHT_TPS", ())
    for pr, pe in m.CO2_PROGRAM_PERIODS:
        gens = exempt.get(pr)
        if not gens or (pr, pe) not in cap:
            continue
        zones = set(m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, pe])
        terms = [
            m.DispatchEmissions[g, tp, f] * m.tp_weight_in_year[tp]
            for g in sorted(gens, key=str)
            if m.gen_load_zone[g] in zones
            for tp in m.TPS_FOR_GEN_IN_PERIOD[g, pe]
            if tp not in light
            for f in m.FUELS_FOR_GEN[g]
            if (g, tp, f) in m.GEN_TP_FUELS
        ]
        if not terms:
            continue
        ex = sum(terms)
        c = cap[pr, pe]
        expr = c.expr
        if type(expr).__name__ != "InequalityExpression" or expr.nargs() != 2:
            raise RuntimeError(
                "tracked_demands: Enforce_Regional_Carbon_Cap no longer has the form `emissions x 0.001 <= "
                "allowances x 0.001`; update _apply_cap_coverage to match carbon_policies_regional.cap_rule"
            )
        lhs, rhs = expr.args
        c.set_value(lhs - ex * 0.001 <= rhs)
        m.td_cap_exempt_emissions[pr, pe] = ex


def pre_solve(m):
    """Checks before the solve: stress-day loads the grid connection alone can't serve (§108 (d)), and linked
    on-site generators against the other modules' settings (§108 (a))."""
    for td in m.TRACKED_DEMANDS:
        if (str(value(m.td_stress_mode[td])).strip() != "max" or not len(m.TD_STRESS_TPS)
                or td in m.TD_ENDOGENOUS_CONNECTION):
            continue
        has_onsite = bool(_onsite_gens(m, td)) or len(m.TD_ONSITE_TECHS) > 0 or td in m.TD_STORAGE
        need = (1 - value(m.td_stress_curtailable_share[td])) * max(
            value(m.td_max_power_mw[td, t]) for t in m.TD_STRESS_TPS)
        if not has_onsite and need > value(m.td_grid_interconnect_mw[td]) + 1e-9:
            raise ValueError(
                f"tracked_demands: {td} must draw {need:g} MW on stress days (stress mode max, curtailable share "
                f"{value(m.td_stress_curtailable_share[td]):g}) but its grid connection is "
                f"{value(m.td_grid_interconnect_mw[td]):g} MW and it has no on-site supply: set td_stress_mode, "
                f"a curtailable share, or on-site plant"
            )
    _check_diesel(m)
    _check_stress_supply(m)
    if not len(m.TD_ONSITE_GENS):
        return
    owner = {}
    for td, g in m.TD_ONSITE_GENS:
        if g in owner:
            raise ValueError(f"tracked_demands: generator {g} is linked to both {owner[g]} and {td}")
        owner[g] = td
        if not value(m.td_location_fixed[td]):
            raise ValueError(f"tracked_demands: {td} has linked on-site generators, so it must be location-fixed")
        z = _td_zone(m, td)
        if m.gen_load_zone[g] != z:
            raise ValueError(
                f"tracked_demands: on-site generator {g} is in {m.gen_load_zone[g]}, but {td} is in {z}"
            )
        # no reserve credit for on-site plant (Tom, 11 Oct 2026)
        if hasattr(m, "prm_credit") and m.prm_credit[g] != "none":
            raise ValueError(
                f"tracked_demands: on-site generator {g} has prm_credit '{m.prm_credit[g]}'; on-site plant gets no "
                f"reserve credit: list it in prm_gen_credit.csv with prm_credit none"
            )
        # behind the meter: no interconnection headroom (assumption recorded in CHANGES §108)
        if hasattr(m, "gen_ic_weight") and value(m.gen_ic_weight[g]) != 0:
            raise ValueError(
                f"tracked_demands: on-site generator {g} has interconnection weight {value(m.gen_ic_weight[g])}; "
                f"give its gen_tech an ic weight of 0 (on-site plant uses no headroom)"
            )
        # gas-turbine build cap: a linked generator is in it exactly when its category is in the case's coverage
        # (Tom, 12 Oct 2026, refining D3)
        if hasattr(m, "GTC_GENS") and len(m.GTC_PERIODS) and len(m.GTC_GENS):
            cat = gtc_category(m, td, g)
            cov = gtc_coverage(m)
            if cat in cov and g not in m.GTC_GENS:
                raise ValueError(
                    f"tracked_demands: on-site generator {g} ({cat}) must be in gas_turbine_cap_gens.csv: the case's "
                    f"gas-turbine cap covers {sorted(cov)}"
                )
            if cat not in cov and g in m.GTC_GENS:
                raise ValueError(
                    f"tracked_demands: on-site generator {g} ({cat}) is in gas_turbine_cap_gens.csv, but the case's "
                    f"gas-turbine cap covers only {sorted(cov)}"
                )
            if g in m.GTC_GENS and str(m.gtc_class[g]).strip() != cat:
                raise ValueError(
                    f"tracked_demands: on-site generator {g} has gtc_class {m.gtc_class[g]!r} in "
                    f"gas_turbine_cap_gens.csv but category {cat!r} in tracked_demand_onsite_gens.csv"
                )
    for tech in m.TD_ONSITE_TECHS:
        if value(m.td_onsite_tech_fuel_cost[tech]) > 0 or value(m.td_onsite_tech_emissions[tech]) > 0:
            print(
                f"tracked_demands: on-site tech {tech} has a fuel cost or emissions; fuel-burning plant should be a "
                f"linked generator (tracked_demand_onsite_gens.csv) so its fuel and emissions go through Switch's "
                f"accounting (CHANGES §108)"
            )


CARRY_FILES = (
    "tracked_demand_connection_existing.csv",
    "tracked_demand_headroom_ledger.csv",
    "tracked_demand_onsite_existing.csv",
    "tracked_demand_storage_existing.csv",
    "tracked_demand_h2_existing.csv",
    "tracked_demand_siting_fixed.csv",
    "tracked_demand_cap_coverage_fixed.csv",
)


def _carried_file(m, switch_data, inputs_dir, name):
    """The file to read for a carry-forward input: an explicit --input-alias for `name` wins; else
    <name>.chained.<case>.csv when prepare_next_stage wrote one (case = the outputs folder's name, as
    prepare_next_stage names it); else `name` (usually absent: the first stage)."""
    path = os.path.join(inputs_dir, name)
    aliased = apply_input_aliases(switch_data, path)
    if aliased != path:
        return aliased
    case = os.path.basename(os.path.normpath(getattr(m.options, "outputs_dir", "") or ""))
    if case:
        chained = os.path.join(inputs_dir, f"{name[:-4]}.chained.{case}.csv")
        if os.path.exists(chained):
            return chained
    return path


GTC_CLASSES = ("combined_cycle", "combustion_turbine", "aeroderivative", "reciprocating_engine")


def gtc_category(m, td, g):
    """Gas-turbine cap category of a linked generator: td_onsite_gtc_class, else from its prime mover."""
    cat = str(value(m.td_onsite_gtc_class[td, g])).strip()
    if cat in ("", "."):
        pm = str(value(m.td_onsite_prime_mover[td, g])).strip()
        if pm == "turbine":
            raise ValueError(
                f"tracked_demands: on-site turbine {g} has no td_onsite_gtc_class in tracked_demand_onsite_gens.csv "
                f"(combined_cycle, combustion_turbine or aeroderivative); the gas-turbine cap needs it"
            )
        cat = "reciprocating_engine" if pm == "recip" else "none"
    if cat not in GTC_CLASSES + ("none",):
        raise ValueError(f"tracked_demands: on-site generator {g}: td_onsite_gtc_class {cat!r} is not one of "
                         f"{GTC_CLASSES + ('none',)}")
    return cat


def gtc_coverage(m):
    """The case's gas-turbine cap coverage: tracked_demand_gtc_coverage.csv, else the classes of the case's own
    (not linked) generators in gas_turbine_cap_gens.csv."""
    if len(m.TD_GTC_COVERAGE):
        return set(m.TD_GTC_COVERAGE)
    linked = {g for _, g in m.TD_ONSITE_GENS}
    return {str(m.gtc_class[g]).strip() for g in m.GTC_GENS if g not in linked}


def chain_inputs(in_path, out_path, next_in_path, case_name, commit=None):
    """Write the next stage's carry-forward files from this stage's outputs (called by prepare_next_stage; it works
    from written outputs, so stage reuse can run it on copied outputs too).

    commit: the period this stage commits (None = its last). On-site, battery and H2 capacity are single builds
    for the whole stage, so the stage's build is carried (it exists from the stage's first period). Siting and cap
    coverage are carried from the earlier stage when it already fixed them, else taken at `commit`.
    """
    from pathlib import Path
    in_path, out_path, next_in_path = Path(in_path), Path(out_path), Path(next_in_path)

    def chained(name):
        return next_in_path / f"{name[:-4]}.chained.{case_name}.csv"

    def earlier(name):
        p = in_path / f"{name[:-4]}.chained.{case_name}.csv"
        return pd.read_csv(p) if p.exists() else None

    def out(name):
        p = out_path / name
        return pd.read_csv(p) if p.exists() else None

    b = out("tracked_demand_onsite_build.csv")
    if b is not None and len(b):
        b = b.rename(columns={"tracked_demand": "TRACKED_DEMAND", "tech": "td_onsite_tech",
                              "built_MW": "td_onsite_existing_mw"})
        b["td_onsite_existing_mw"] = b["td_onsite_existing_mw"].clip(lower=0).round(6)
        b[["TRACKED_DEMAND", "td_onsite_tech", "td_onsite_existing_mw"]].to_csv(
            chained("tracked_demand_onsite_existing.csv"), index=False)

    s = out("tracked_demand_storage_build.csv")
    if s is not None and len(s):
        s = s.rename(columns={"tracked_demand": "TRACKED_DEMAND", "built_power_MW": "td_storage_existing_mw",
                              "built_energy_MWh": "td_storage_existing_mwh"})
        for c in ("td_storage_existing_mw", "td_storage_existing_mwh"):
            s[c] = s[c].clip(lower=0).round(6)
        s[["TRACKED_DEMAND", "td_storage_existing_mw", "td_storage_existing_mwh"]].to_csv(
            chained("tracked_demand_storage_existing.csv"), index=False)

    h = out("tracked_demand_h2_build.csv")
    if h is not None and len(h):
        h = h.rename(columns={"tracked_demand": "TRACKED_DEMAND", "h2_tank_capacity_kg": "td_h2_existing_kg"})
        h["td_h2_existing_kg"] = h["td_h2_existing_kg"].clip(lower=0).round(6)
        h[["TRACKED_DEMAND", "td_h2_existing_kg"]].to_csv(chained("tracked_demand_h2_existing.csv"), index=False)

    fixed = earlier("tracked_demand_siting_fixed.csv")
    a = out("tracked_demand_zone_allocation.csv")
    if fixed is not None:
        fixed.to_csv(chained("tracked_demand_siting_fixed.csv"), index=False)
    elif a is not None and len(a):
        p = commit if commit is not None else a["period"].max()
        a = a[a["period"] == p].rename(columns={"tracked_demand": "TRACKED_DEMAND", "zone": "LOAD_ZONE",
                                                "allocation_fraction": "td_fixed_allocation"})
        a["td_fixed_allocation"] = a["td_fixed_allocation"].clip(0, 1).round(9)
        a[["TRACKED_DEMAND", "LOAD_ZONE", "td_fixed_allocation"]].to_csv(
            chained("tracked_demand_siting_fixed.csv"), index=False)

    c = out("tracked_demand_connection_costs.csv")
    if c is not None and len(c):
        p = commit if commit is not None else c["period"].max()
        c = c[c["period"] == p]
        pd.DataFrame({
            "TRACKED_DEMAND": c["tracked_demand"],
            "td_connection_existing_mw": c["connection_mw"].clip(lower=0).round(6),
            "td_headroom_draw_existing_mw": (c["headroom_draw_mw"] + c["carried_headroom_draw_mw"]).clip(
                lower=0).round(6),
        }).to_csv(chained("tracked_demand_connection_existing.csv"), index=False)
    led = out("tracked_demand_headroom_ledger.csv")
    if led is not None and len(led):
        led.to_csv(chained("tracked_demand_headroom_ledger.csv"), index=False)

    cov = earlier("tracked_demand_cap_coverage_fixed.csv")
    e = out("tracked_demand_onsite_emissions.csv")
    if e is not None and len(e):
        p = commit if commit is not None else e["period"].max()
        e = e[(e["period"] == p) & e["co2_program"].notna() & (e["co2_program"].astype(str) != "")]
        e = e.rename(columns={"tracked_demand": "TRACKED_DEMAND", "generation_project": "GENERATION_PROJECT",
                              "co2_program": "CO2_PROGRAM", "covered_by_cap": "td_cap_covered"})
        e = e[["TRACKED_DEMAND", "GENERATION_PROJECT", "CO2_PROGRAM", "td_cap_covered"]]
        if cov is not None:
            keys = ["TRACKED_DEMAND", "GENERATION_PROJECT", "CO2_PROGRAM"]
            e = pd.concat([cov, e]).drop_duplicates(subset=keys, keep="first")
        cov = e
    if cov is not None and len(cov):
        cov.to_csv(chained("tracked_demand_cap_coverage_fixed.csv"), index=False)


def load_inputs(m, switch_data, inputs_dir):
    """
    Load tracked demand input data.

    The module is a no-op if tracked_demands.csv is absent.

    tracked_demands.csv columns:
        Required: TRACKED_DEMAND, td_type,
                  td_energy_requirement_mwh_per_year, td_default_max_power_mw
        Optional: td_default_min_power_mw (default 0),
                  td_location_fixed (default 1/True),
                  td_grid_interconnect_mw (default = td_default_max_power_mw),
                  td_onsite_emissions_in_system_cap (default 0/False)
        Boolean columns: use 0/1 values in the CSV.

    tracked_demand_candidate_zones.csv columns:
        TRACKED_DEMAND, LOAD_ZONE
        One row per TD for location-fixed TDs.

    tracked_demand_time_blocks.csv (optional):
        TRACKED_DEMAND, time_block, TIMEPOINT

    tracked_demand_cfe_targets.csv (optional):
        TRACKED_DEMAND, PERIOD, time_block, td_cfe_target [, td_cfe_shortfall_penalty]

    grid_clean_fraction.csv (optional, defaults to 0.5 everywhere):
        LOAD_ZONE, TIMEPOINT, grid_clean_fraction

    tracked_demand_onsite_techs.csv (optional, Stage 3):
        td_onsite_tech (index), td_onsite_tech_capital_cost,
        [td_onsite_tech_variable_om, td_onsite_tech_fuel_cost,
         td_onsite_tech_emissions, td_onsite_tech_capture_rate,
         td_onsite_tech_max_annual_hours, td_onsite_tech_is_clean,
         td_onsite_tech_min_stable_mw_fraction, td_onsite_tech_subsidy,
         td_onsite_tech_min_build_mw, td_onsite_tech_build_increment_mw]
        Omitting min_build_mw and build_increment_mw (or leaving them 0) keeps
        the solve as LP. Setting either triggers MIP for that tech.

    tracked_demand_onsite_tech_subsidies.csv (optional, Stage 3):
        td_onsite_tech, PERIOD, td_onsite_tech_subsidy_by_period

    tracked_demand_onsite_predetermined.csv (optional, Stage 3):
        TRACKED_DEMAND, td_onsite_tech, td_onsite_predetermined_mw
        Fixes BuildOnsiteTech to specified MW; excludes entry from MIP sizing.

    tracked_demand_storage.csv (optional, Stage 4):
        TRACKED_DEMAND (index), td_storage_power_cost, td_storage_energy_cost
        [, td_storage_max_hours=8, td_storage_min_hours=0,
           td_storage_roundtrip_eff=0.85]
        One row per TD that has behind-the-meter storage.

    tracked_demand_dispatch_bounds.csv (optional, Stage 6):
        TRACKED_DEMAND, TIMEPOINT, td_min_power_mw [, td_max_power_mw]
        Overrides scalar dispatch bounds for specific timepoints.
        Absent timepoints use td_default_min/max_power_mw from tracked_demands.csv.

    tracked_demand_flex_events.csv (optional, Stage 5):
        TRACKED_DEMAND, time_block (or TIMEPOINT), td_flex_max_grid_draw_mw
        [, td_flex_noncompliance_penalty=500]
        Rows with time_block are expanded using tracked_demand_time_blocks.csv.
        Rows with TIMEPOINT are used directly.

    tracked_demand_h2_storage.csv (optional, Stage 8):
        TRACKED_DEMAND (index), td_h2_storage_cost_per_kg_yr
        [, td_electrolyzer_efficiency_kwh_per_kg=50,
           td_h2_storage_max_kg=1e9,
           td_h2_min_delivery_kg_per_hr=0,
           td_h2_max_delivery_kg_per_hr=1e9]
        One row per electrolyzer TD with H2 tank storage.

    tracked_demand_onsite_build_caps.csv (optional, Stage 9A):
        TRACKED_DEMAND, td_onsite_tech, td_onsite_build_mw_cap
        Per-(TD, tech) upper bound on BuildOnsiteTech MW.

    tracked_demand_grid_caps.csv (optional, Stage 9B):
        TRACKED_DEMAND, PERIOD [, td_annual_grid_max_mwh, td_annual_grid_min_mwh]
        Per-(TD, period) annual grid draw ceiling and/or floor (MWh/yr).
        Both params are optional (defaults: max=1e12, min=0).

    tracked_demand_grid_clean_caps.csv (optional, Stage 9C):
        TRACKED_DEMAND, PERIOD, time_block, td_grid_clean_max_mwh
        Per-(TD, period, block) ceiling on grid clean MWh credited toward CFE.
        Forces remaining CFE gap to on-site clean resources or shortfall penalty.
    """
    # ── Stage 1 ───────────────────────────────────────────────────────────────
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demands.csv"),
        optional=True,
        index=m.TRACKED_DEMANDS,
        param=(
            m.td_type,
            m.td_energy_requirement_mwh_per_year,
            m.td_default_min_power_mw,
            m.td_default_max_power_mw,
            m.td_location_fixed,
            m.td_grid_interconnect_mw,
            m.td_onsite_emissions_in_system_cap,
            m.td_export_limit_share,
            m.td_stress_mode,
            m.td_stress_curtailable_share,
            m.td_connection_endogenous,
            m.td_connection_cost_per_kw,
            m.td_connection_cost_multiplier,
            m.td_connection_cost_share_demand,
            m.td_connection_uses_headroom,
            m.td_headroom_weight,
            m.td_stress_grid_limit_share,
            m.td_stress_year_basis,
            m.td_stress_strict,
        ),
        optional_params=(
            m.td_default_min_power_mw,
            m.td_location_fixed,
            m.td_grid_interconnect_mw,
            m.td_onsite_emissions_in_system_cap,
            m.td_export_limit_share,
            m.td_stress_mode,
            m.td_stress_curtailable_share,
            m.td_connection_endogenous,
            m.td_connection_cost_per_kw,
            m.td_connection_cost_multiplier,
            m.td_connection_cost_share_demand,
            m.td_connection_uses_headroom,
            m.td_headroom_weight,
            m.td_stress_grid_limit_share,
            m.td_stress_year_basis,
            m.td_stress_strict,
        ),
    )
    # §108 (f): zone network-upgrade cost ($/kW overnight), connection life
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_zone_upgrade_costs.csv"),
        optional=True, param=(m.td_zone_upgrade_cost_per_kw,),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_params.csv"),
        optional=True, param=(m.td_connection_life_years, m.td_stress_shortfall_penalty),
        optional_params=(m.td_connection_life_years, m.td_stress_shortfall_penalty),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_stress_profile.csv"),
        optional=True,
        index=m.TD_STRESS_PROFILE,
        param=(m.td_stress_load_mw,),
    )
    # set= + explicit select= required for parameter-free multi-column set files.
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_candidate_zones.csv"),
        optional=True,
        set=m.TD_CANDIDATE_ZONES,
        select=("TRACKED_DEMAND", "LOAD_ZONE"),
    )

    # ── Stage 2 ───────────────────────────────────────────────────────────────
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_time_blocks.csv"),
        optional=True,
        set=m.TD_BLOCK_TIMEPOINTS,
        select=("TRACKED_DEMAND", "time_block", "TIMEPOINT"),
    )
    # Column names in the CSV must match param names (td_cfe_target,
    # td_cfe_shortfall_penalty) so load_aug auto-select correctly populates
    # the index set. See flexible_loads.py for the same pattern.
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_cfe_targets.csv"),
        optional=True,
        index=m.TD_CFE_TARGET_PERIODS,
        param=(m.td_cfe_target, m.td_cfe_shortfall_penalty),
        optional_params=(m.td_cfe_shortfall_penalty,),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "grid_clean_fraction.csv"),
        optional=True,
        param=(m.grid_clean_fraction,),
        optional_params=(m.grid_clean_fraction,),
        select=("LOAD_ZONE", "TIMEPOINT", "grid_clean_fraction"),
    )

    # ── Stage 3 ───────────────────────────────────────────────────────────────
    # Column names must match param names exactly (no explicit select=).
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_onsite_techs.csv"),
        optional=True,
        index=m.TD_ONSITE_TECHS,
        param=(
            m.td_onsite_tech_capital_cost,
            m.td_onsite_tech_variable_om,
            m.td_onsite_tech_fuel_cost,
            m.td_onsite_tech_emissions,
            m.td_onsite_tech_capture_rate,
            m.td_onsite_tech_max_annual_hours,
            m.td_onsite_tech_is_clean,
            m.td_onsite_tech_min_stable_mw_fraction,
            m.td_onsite_tech_subsidy,
            m.td_onsite_tech_min_build_mw,
            m.td_onsite_tech_build_increment_mw,
            m.td_onsite_tech_cf_source,
        ),
        optional_params=(
            m.td_onsite_tech_variable_om,
            m.td_onsite_tech_fuel_cost,
            m.td_onsite_tech_emissions,
            m.td_onsite_tech_capture_rate,
            m.td_onsite_tech_max_annual_hours,
            m.td_onsite_tech_is_clean,
            m.td_onsite_tech_min_stable_mw_fraction,
            m.td_onsite_tech_subsidy,
            m.td_onsite_tech_min_build_mw,
            m.td_onsite_tech_build_increment_mw,
            m.td_onsite_tech_cf_source,
        ),
    )
    switch_data.load_aug(
        filename=os.path.join(
            inputs_dir, "tracked_demand_onsite_tech_subsidies.csv"
        ),
        optional=True,
        index=m.TD_TECH_SUBSIDY_PERIODS,
        param=(m.td_onsite_tech_subsidy_by_period,),
    )
    switch_data.load_aug(
        filename=os.path.join(
            inputs_dir, "tracked_demand_onsite_predetermined.csv"
        ),
        optional=True,
        index=m.TD_PREDETERMINED_BUILDS,
        param=(m.td_onsite_predetermined_mw,),
    )

    # ── Stage 4 ───────────────────────────────────────────────────────────────
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_storage.csv"),
        optional=True,
        index=m.TD_STORAGE,
        param=(
            m.td_storage_power_cost,
            m.td_storage_energy_cost,
            m.td_storage_max_hours,
            m.td_storage_min_hours,
            m.td_storage_roundtrip_eff,
        ),
        optional_params=(
            m.td_storage_max_hours,
            m.td_storage_min_hours,
            m.td_storage_roundtrip_eff,
        ),
    )

    # ── Stage 6 ───────────────────────────────────────────────────────────────
    # Each param loaded separately with explicit select= (same pattern as
    # grid_clean_fraction). Both columns are optional — absent columns leave
    # the param at its callable default (= scalar from tracked_demands.csv).
    _bounds_file = os.path.join(inputs_dir, "tracked_demand_dispatch_bounds.csv")
    switch_data.load_aug(
        filename=_bounds_file,
        optional=True,
        param=(m.td_min_power_mw,),
        optional_params=(m.td_min_power_mw,),
        select=("TRACKED_DEMAND", "TIMEPOINT", "td_min_power_mw"),
    )
    switch_data.load_aug(
        filename=_bounds_file,
        optional=True,
        param=(m.td_max_power_mw,),
        optional_params=(m.td_max_power_mw,),
        select=("TRACKED_DEMAND", "TIMEPOINT", "td_max_power_mw"),
    )

    # ── Stage 8 ───────────────────────────────────────────────────────────────
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_h2_storage.csv"),
        optional=True,
        index=m.TD_H2_STORAGE,
        param=(
            m.td_h2_storage_cost_per_kg_yr,
            m.td_electrolyzer_efficiency_kwh_per_kg,
            m.td_h2_storage_max_kg,
            m.td_h2_min_delivery_kg_per_hr,
            m.td_h2_max_delivery_kg_per_hr,
        ),
        optional_params=(
            m.td_electrolyzer_efficiency_kwh_per_kg,
            m.td_h2_storage_max_kg,
            m.td_h2_min_delivery_kg_per_hr,
            m.td_h2_max_delivery_kg_per_hr,
        ),
    )

    # ── Stage 9 ───────────────────────────────────────────────────────────────
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_onsite_build_caps.csv"),
        optional=True,
        index=m.TD_ONSITE_BUILD_CAPS,
        param=(m.td_onsite_build_mw_cap,),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_grid_caps.csv"),
        optional=True,
        index=m.TD_ANNUAL_GRID_CAPS,
        param=(m.td_annual_grid_max_mwh, m.td_annual_grid_min_mwh),
        optional_params=(m.td_annual_grid_max_mwh, m.td_annual_grid_min_mwh),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_grid_clean_caps.csv"),
        optional=True,
        index=m.TD_GRID_CLEAN_CAPS,
        param=(m.td_grid_clean_max_mwh,),
    )

    # ── Stage 11 ──────────────────────────────────────────────────────────────
    switch_data.load_aug(
        filename=apply_input_aliases(
            switch_data,
            os.path.join(inputs_dir, "tracked_demand_rec_supply.csv"),
        ),
        optional=True,
        index=m.TD_REC_SUPPLY,
        param=(m.td_rec_supply_mwh, m.td_rec_cost_per_mwh),
        optional_params=(m.td_rec_cost_per_mwh,),
    )

    # ── Soft grid cap (Stage 1 extension) ─────────────────────────────────────
    switch_data.load_aug(
        filename=apply_input_aliases(
            switch_data,
            os.path.join(inputs_dir, "tracked_demand_grid_soft_cap.csv"),
        ),
        optional=True,
        index=m.TD_SOFT_CAP,
        param=(m.td_grid_soft_cap_mw, m.td_grid_soft_cap_penalty),
        optional_params=(m.td_grid_soft_cap_penalty,),
    )

    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_cap_coverage_states.csv"),
        optional=True, index=m.TD_CAP_STATE_RULES,
        param=(m.td_cap_state_threshold_mw, m.td_cap_state_condition),
        optional_params=(m.td_cap_state_condition,),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_zone_states.csv"),
        optional=True, param=(m.td_zone_state,), select=("LOAD_ZONE", "STATE"),
    )

    # ── Stage 13 (§108 (c)): carry-forward files ─────────────────────────────
    def carry(name):
        return _carried_file(m, switch_data, inputs_dir, name)

    switch_data.load_aug(
        filename=carry("tracked_demand_onsite_existing.csv"),
        optional=True, index=m.TD_ONSITE_EXISTING, param=(m.td_onsite_existing_mw,),
    )
    switch_data.load_aug(
        filename=carry("tracked_demand_storage_existing.csv"),
        optional=True, index=m.TD_STORAGE_EXISTING,
        param=(m.td_storage_existing_mw, m.td_storage_existing_mwh),
        optional_params=(m.td_storage_existing_mw, m.td_storage_existing_mwh),
    )
    switch_data.load_aug(
        filename=carry("tracked_demand_h2_existing.csv"),
        optional=True, index=m.TD_H2_EXISTING, param=(m.td_h2_existing_kg,),
    )
    switch_data.load_aug(
        filename=carry("tracked_demand_siting_fixed.csv"),
        optional=True, index=m.TD_SITING_FIXED, param=(m.td_fixed_allocation,),
    )
    switch_data.load_aug(
        filename=carry("tracked_demand_connection_existing.csv"),
        optional=True, index=m.TD_CONNECTION_EXISTING,
        param=(m.td_connection_existing_mw, m.td_headroom_draw_existing_mw),
        optional_params=(m.td_connection_existing_mw, m.td_headroom_draw_existing_mw),
    )
    switch_data.load_aug(
        filename=carry("tracked_demand_cap_coverage_fixed.csv"),
        optional=True, index=m.TD_CAP_COVERAGE_FIXED, param=(m.td_cap_covered,),
    )

    # ── Stage 12 (§108) ───────────────────────────────────────────────────────
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_onsite_gens.csv"),
        optional=True,
        index=m.TD_ONSITE_GENS,
        param=(m.td_onsite_prime_mover, m.td_onsite_unit_mw, m.td_onsite_rated_mw, m.td_onsite_gtc_class,
               m.td_onsite_pte_hours),
        optional_params=(m.td_onsite_prime_mover, m.td_onsite_unit_mw, m.td_onsite_rated_mw,
                         m.td_onsite_gtc_class, m.td_onsite_pte_hours),
    )
    # §140: backup diesel fleets and the stress days' weather years
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_diesel.csv"),
        optional=True,
        index=m.TD_DIESEL_GENS,
        param=(m.td_diesel_permit_flh, m.td_diesel_test_flh, m.td_diesel_non_emergency_flh, m.td_diesel_tier,
               m.td_diesel_permitted_non_emergency, m.td_diesel_forced_outage_rate,
               m.td_diesel_emergency_counts_toward_cap),
        optional_params=(m.td_diesel_permit_flh, m.td_diesel_test_flh, m.td_diesel_non_emergency_flh,
                         m.td_diesel_tier, m.td_diesel_permitted_non_emergency, m.td_diesel_forced_outage_rate,
                         m.td_diesel_emergency_counts_toward_cap),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_stress_years.csv"),
        optional=True,
        index=m.TD_STRESS_YEAR_TS,
        param=(m.td_stress_weather_year,),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_gtc_coverage.csv"),
        optional=True, set=m.TD_GTC_COVERAGE,
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tracked_demand_cap_coverage.csv"),
        optional=True,
        index=m.TD_CAP_PROGRAMS,
        param=(
            m.td_cap_counts_onsite,
            m.td_cap_threshold_mw,
            m.td_cap_threshold_tco2,
            m.td_cap_threshold_basis,
            m.td_cap_limited_export_exemption,
            m.td_cap_backup_diesel_rule,
        ),
        optional_params=(
            m.td_cap_counts_onsite,
            m.td_cap_threshold_mw,
            m.td_cap_threshold_tco2,
            m.td_cap_threshold_basis,
            m.td_cap_limited_export_exemption,
            m.td_cap_backup_diesel_rule,
        ),
    )

    # ── Stage 5 ───────────────────────────────────────────────────────────────
    # Expand time_block rows to individual timepoints using pandas, then load
    # via a temp file so load_aug can populate TD_FLEX_TIMEPOINTS correctly.
    _flex_df = _expand_flex_events(inputs_dir, switch_data)
    if _flex_df is not None and len(_flex_df) > 0:
        _tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline=""
        )
        _flex_df.to_csv(_tmp, index=False)
        _tmp.flush()
        _tmp.close()
        try:
            switch_data.load_aug(
                filename=_tmp.name,
                index=m.TD_FLEX_TIMEPOINTS,
                param=(
                    m.td_flex_max_grid_draw_mw,
                    m.td_flex_noncompliance_penalty,
                ),
                optional_params=(m.td_flex_noncompliance_penalty,),
            )
        finally:
            os.unlink(_tmp.name)


def post_solve(m, outdir):
    """
    Write output files after solve.

    Stage 1:  tracked_demand_dispatch.csv, tracked_demand_annual.csv
    Stage 1+: tracked_demand_grid_cap_summary.csv (soft-cap TDs only)
    Stage 2:  tracked_demand_cfe_detail.csv (CFE block scores vs exogenous clean fraction)
    Stage 10: grid_clean_fraction_computed.csv, grid_co2_intensity_computed.csv
              tracked_demand_hourly_emissions.csv
              tracked_demand_cfe_computed.csv (post-hoc CFE using actual dispatch)
              tracked_demand_45v_assessment.csv (H2 TDs only)

    Stage 10 outputs are computed from the solved model (§108 (b); they no longer need dispatch.csv).
    """
    # Switch's reporting module (a core module, so its post_solve runs first) writes every Var to <name>.csv, empty
    # ones as a header only. The on-site tech menu's variables are empty when the menu is off (and the MIP sizing
    # ones whenever no tech uses it): drop those header-only files (§140, VM test).
    for name, index in (("BuildOnsiteTech", m.TD_TECH_BUILDS), ("BuildOnsiteTechFlag", m.TD_TECH_BUILDS_FLAG),
                        ("BuildOnsiteTechUnits", m.TD_TECH_BUILDS_UNITS),
                        ("DispatchOnsiteTech", m.TD_TECH_TIMEPOINTS)):
        f = os.path.join(outdir, f"{name}.csv")
        if not len(index) and os.path.exists(f):
            os.remove(f)
    if not m.TRACKED_DEMANDS:
        return

    # ── Stage 1 outputs ───────────────────────────────────────────────────────

    backup = len(m.TD_DIESEL_GENS) > 0 or len(m.TD_SHORTFALL_TIMEPOINTS) > 0
    reporting.write_table(
        m,
        m.TD_TIMEPOINTS,
        output_file=os.path.join(outdir, "tracked_demand_dispatch.csv"),
        headings=("tracked_demand", "timepoint", "timestamp",
                  "TDDispatch_MW", "TDGridDraw_MW", "TDGridExport_MW", "TDOnsiteGen_MW")
        + (("TDDiesel_MW", "TDStressShortfall_MW") if backup else ()),
        values=lambda m, td, t: (
            td,
            t,
            m.tp_timestamp[t],
            m.TDDispatch[td, t],
            m.TDGridDraw[td, t],
            value(_export(m, td, t)),
            value(m.TDOnsiteGenMW[td, t]),
        ) + ((
            sum(value(m.DispatchGen[g, t]) for d, g in m.TD_DIESEL_GENS if d == td and (g, t) in m.GEN_TPS),
            value(_shortfall(m, td, t)),
        ) if backup else ()),
    )
    if backup:
        _write_diesel_outputs(m, outdir)

    # ── Grid connection outputs (§108 (f)) ────────────────────────────────────

    _write_connection_outputs(m, outdir)

    # ── Stage 12 outputs (§108) ───────────────────────────────────────────────

    if m.TD_ONSITE_GENS:
        _write_onsite_gen_outputs(m, outdir)

    reporting.write_table(
        m,
        [(td, p) for td in m.TRACKED_DEMANDS for p in m.PERIODS],
        output_file=os.path.join(outdir, "tracked_demand_annual.csv"),
        headings=("tracked_demand", "period",
                  "annual_dispatch_MWh", "annual_grid_draw_MWh",
                  "energy_requirement_MWh"),
        values=lambda m, td, p: (
            td,
            p,
            sum(
                m.TDDispatch[td, t] * m.tp_weight_in_year[t]
                for t in m.TPS_IN_PERIOD[p]
            ),
            sum(
                m.TDGridDraw[td, t] * m.tp_weight_in_year[t]
                for t in m.TPS_IN_PERIOD[p]
            ),
            m.td_energy_requirement_mwh_per_year[td],
        ),
    )

    # ── Soft grid cap summary ─────────────────────────────────────────────────

    if m.TD_SOFT_CAP:
        _write_soft_cap_summary(m, outdir)

    # ── Stage 2 output ────────────────────────────────────────────────────────

    if m.TD_CFE_TARGET_PERIODS:
        _write_cfe_detail(m, outdir)
        _write_clean_ledger(m, outdir)

    # ── Stage 3 output ────────────────────────────────────────────────────────

    if m.TD_TECH_BUILDS:
        _write_onsite_outputs(m, outdir)

    # ── Stage 4 output ────────────────────────────────────────────────────────

    if m.TD_STORAGE:
        _write_storage_outputs(m, outdir)

    # ── Stage 5 output ────────────────────────────────────────────────────────

    if m.TD_FLEX_TIMEPOINTS:
        _write_flex_outputs(m, outdir)

    # ── Stage 7 output ────────────────────────────────────────────────────────

    if m.TD_LOCATION_FLEX:
        _write_zone_allocation_outputs(m, outdir)

    # ── Stage 8 output ────────────────────────────────────────────────────────

    if m.TD_H2_STORAGE:
        _write_h2_outputs(m, outdir)

    # ── Stage 10 outputs ──────────────────────────────────────────────────────

    region_col = getattr(m.options, "td_cfe_region_col", "h2ptcreg")
    grid_df = _compute_grid_metrics(m, m.options.inputs_dir, region_col)
    if grid_df is not None:
        _write_grid_metrics_computed(m, outdir, grid_df)
        _write_td_emissions(m, outdir, grid_df)
        if m.TD_CFE_TARGET_PERIODS:
            _write_cfe_computed(m, outdir, grid_df)
        if m.TD_H2_STORAGE:
            _write_45v_assessment(m, outdir, grid_df)


# ── Backup diesel checks and outputs (CHANGES §140) ─────────────────────────
DIESEL_NOX_G_PER_KWH = {2: 6.4, 4: 0.67}   # certification limits, >560 / >900 kW gensets (40 CFR 89.112, 1039.101)


def _diesel_mw(m, g, p):
    """Fleet MW of diesel g in p, known before the solve: its predetermined capacity in service in p."""
    return sum(
        value(m.build_gen_predetermined[g2, y]) for g2, y in m.PREDETERMINED_GEN_BLD_YRS
        if g2 == g and (g, y) in m.GEN_BLD_YRS and p in m.PERIODS_FOR_GEN_BLD_YR[g, y]
    )


def _check_diesel(m):
    """Backup diesel rules (§140): existing capacity only; non-emergency running only for tier 4 or engines permitted
    as non-emergency (40 CFR 60.4211(f) / 63.6640(f), rule text checked by the input research); test plus
    non-emergency hours within the permit."""
    for td, g in m.TD_DIESEL_GENS:
        new = [y for g2, y in m.NEW_GEN_BLD_YRS if g2 == g]
        if new:
            raise ValueError(f"tracked_demands: backup diesel {g} ({td}) has new-build years {new}; the fleet is "
                             f"existing capacity (gen_build_predetermined only)")
        ne, permit, test = (value(m.td_diesel_non_emergency_flh[td, g]), value(m.td_diesel_permit_flh[td, g]),
                            value(m.td_diesel_test_flh[td, g]))
        tier = int(float(value(m.td_diesel_tier[td, g])))
        if ne > 0 and tier != 4 and not value(m.td_diesel_permitted_non_emergency[td, g]):
            raise ValueError(f"tracked_demands: backup diesel {g} ({td}) has {ne:g} non-emergency hours but is tier "
                             f"{tier} and not permitted as non-emergency; emergency engines may not run for peak "
                             f"shaving (40 CFR 60.4211(f), 63.6640(f))")
        if test + ne > permit + 1e-9:
            raise ValueError(f"tracked_demands: backup diesel {g} ({td}): test {test:g} + non-emergency {ne:g} hours "
                             f"exceed the permit's {permit:g} hours")


def _check_stress_supply(m):
    """Necessary-condition check (§140): for each TD with a backup fleet or a stress grid limit, period and
    weather-year group, the stress-day energy beyond s x connection against what the fleet (within its permit) and
    bounded on-site supply can give. A gap warns (an error with td_stress_strict); unbounded on-site supply skips it."""
    for td in m.TRACKED_DEMANDS:
        if not _has_stress_backup(m, td):
            continue
        s = value(m.td_stress_grid_limit_share[td])
        conn = value(m.td_grid_interconnect_mw[td])
        c = value(m.td_stress_curtailable_share[td])
        diesel = [g for d, g in m.TD_DIESEL_GENS if d == td]
        others = [g for g in _onsite_gens(m, td) if g not in diesel]
        if (len(m.TD_ONSITE_TECHS) or td in m.TD_STORAGE
                or any(g not in m.CAPACITY_LIMITED_GENS for g in others)):
            continue                                        # unbounded on-site supply: can't conclude
        for p in m.PERIODS:
            for w in _stress_groups(m, td, p):
                tps = _stress_tps_in_group(m, td, p, w)
                if not tps:
                    continue
                need_t = {t: max(0.0, max((1 - c) * value(_stress_load(m, td, t)), value(m.td_min_power_mw[td, t]))
                                 - s * conn) for t in tps}
                need = sum(need_t[t] * value(m.tp_duration_hrs[t]) for t in tps)
                if need <= 1e-9:
                    continue
                supply = sum(value(m.gen_capacity_limit_mw[g]) * value(m.tp_duration_hrs[t])
                             for g in others for t in tps)
                for g in diesel:
                    mw = _diesel_mw(m, g, p) * (1 - value(m.td_diesel_forced_outage_rate[td, g]))
                    hours = max(0.0, value(m.td_diesel_permit_flh[td, g]) - value(m.td_diesel_test_flh[td, g]))
                    by_hour = sum(min(need_t[t], mw) * value(m.tp_duration_hrs[t]) for t in tps)
                    supply += (min(by_hour, mw * hours) if value(m.td_diesel_emergency_counts_toward_cap[td, g])
                               else by_hour)
                if need > supply + 1e-6:
                    msg = (f"tracked_demands: {td} needs {need:,.0f} MWh beyond its stress grid limit "
                           f"({s:g} x {conn:g} MW) on period {p}'s stress days (weather year {w}), but its backup "
                           f"fleet (its size and, where emergency hours count, its permit) and on-site plant can give "
                           f"at most {supply:,.0f} MWh; "
                           f"the rest is stress-day shortfall")
                    if value(m.td_stress_strict[td]):
                        raise ValueError(msg + " (td_stress_strict: the run would be infeasible)")
                    print("WARNING: " + msg)


def _write_diesel_outputs(m, outdir):
    """tracked_demand_diesel.csv (per fleet, period and weather-year group) and tracked_demand_stress_summary.csv (per
    TD and period)."""
    duals = getattr(m, "dual", None)
    rows = []
    for td, g, p, w in m.TD_DIESEL_GROUPS:
        cap = value(m.GenCapacity[g, p])
        stress = [t for t in _stress_tps_in_group(m, td, p, w) if (g, t) in m.GEN_TPS]
        stress_mwh = sum(value(m.DispatchGen[g, t]) * value(m.tp_duration_hrs[t]) for t in stress)
        normal = [t for t in m.TPS_IN_PERIOD[p] if t not in m.TD_STRESS_TPS and (g, t) in m.GEN_TPS]
        ne_mwh = sum(value(m.DispatchGen[g, t]) * value(m.tp_weight_in_year[t]) for t in normal)
        hr = value(m.gen_full_load_heat_rate[g]) if g in m.FUEL_BASED_GENS else 0.0
        co2 = max((value(m.f_co2_intensity[f]) for f in m.FUELS_FOR_GEN[g]), default=0.0) \
            if g in m.FUEL_BASED_GENS else 0.0
        ne_fuel = sum(value(m.GenFuelUseRate[g, t, f]) * value(m.tp_weight_in_year[t])
                      for t in normal for f in m.FUELS_FOR_GEN[g] if (g, t, f) in m.GEN_TP_FUELS) \
            if g in m.FUEL_BASED_GENS else 0.0
        ne_co2 = sum(value(m.DispatchEmissions[g, t, f]) * value(m.tp_weight_in_year[t])
                     for t in normal for f in m.FUELS_FOR_GEN[g] if (g, t, f) in m.GEN_TP_FUELS) \
            if g in m.FUEL_BASED_GENS else 0.0
        test = value(m.td_diesel_test_flh[td, g])
        test_mwh = test * cap
        flh = lambda mwh: mwh / cap if cap > 0 else 0.0
        counted = bool(value(m.td_diesel_emergency_counts_toward_cap[td, g]))
        total = (flh(stress_mwh) if counted else 0.0) + flh(ne_mwh) + test
        permit = value(m.td_diesel_permit_flh[td, g])
        c = m.TD_Diesel_Permit_Cap[td, g, p, w]
        dual = abs(duals[c]) / value(m.bring_annual_costs_to_base_year[p]) if duals is not None and c in duals \
            else float("nan")
        short = sum(value(m.TDStressShortfall[td, t]) * value(m.tp_duration_hrs[t])
                    for t in _stress_tps_in_group(m, td, p, w) if (td, t) in m.TD_SHORTFALL_TIMEPOINTS)
        tier = int(float(value(m.td_diesel_tier[td, g])))
        rows.append(dict(
            tracked_demand=td, generation_project=g, period=p, weather_year=w, diesel_mw=cap, permit_flh=permit,
            stress_flh=flh(stress_mwh), emergency_counts_toward_cap=int(counted), non_emergency_flh=flh(ne_mwh),
            test_flh=test, total_flh=total,
            flh_remaining=permit - total, cap_dual_usd_per_mwh=dual, stress_mwh=stress_mwh,
            non_emergency_mwh=ne_mwh, test_mwh=test_mwh,
            fuel_mmbtu_stress=stress_mwh * hr, fuel_mmbtu_non_emergency=ne_fuel, fuel_mmbtu_test=test_mwh * hr,
            co2_t_stress=stress_mwh * hr * co2, co2_t_non_emergency=ne_co2, co2_t_test=test_mwh * hr * co2,
            nox_t=(stress_mwh + ne_mwh + test_mwh) * DIESEL_NOX_G_PER_KWH.get(tier, float("nan")) / 1000.0,
            shortfall_mwh=short,
            note="stress and test tonnes are outside the model's cap rows (zero-weight hours)",
        ))
    pd.DataFrame(rows).to_csv(os.path.join(outdir, "tracked_demand_diesel.csv"), index=False)

    summ = []
    for td in m.TRACKED_DEMANDS:
        if not _has_stress_backup(m, td):
            continue
        for p in m.PERIODS:
            mine = [r for r in rows if r["tracked_demand"] == td and r["period"] == p]
            worst = min(mine, key=lambda r: r["flh_remaining"]) if mine else None
            short = sum(value(m.TDStressShortfall[td, t]) * value(m.tp_duration_hrs[t])
                        for t in m.TPS_IN_PERIOD[p] if (td, t) in m.TD_SHORTFALL_TIMEPOINTS)
            summ.append(dict(
                tracked_demand=td, period=p, stress_grid_limit_share=value(m.td_stress_grid_limit_share[td]),
                n_weather_year_groups=_n_groups(m, td, p),
                binding_weather_year=worst["weather_year"] if worst else "",
                flh_remaining_min=worst["flh_remaining"] if worst else float("nan"),
                shortfall_mwh_total=short,
                shortfall_mwh_per_year=short / _n_groups(m, td, p),
            ))
            if short > 1e-6:
                print(f"WARNING: tracked_demands: {td} has {short:,.1f} MWh of stress-day shortfall in {p} "
                      f"(tracked_demand_stress_summary.csv)")
    pd.DataFrame(summ).to_csv(os.path.join(outdir, "tracked_demand_stress_summary.csv"), index=False)


def _headroom_value(m, z, p):
    """interconnection_headroom's headroom value in zone z, period p ($/kW-yr; its dual / NPV weight / 1000), or None."""
    if not hasattr(m, "IC_Headroom_Limit") or (z, p) not in m.IC_Headroom_Limit or not hasattr(m, "dual"):
        return None
    c = m.IC_Headroom_Limit[z, p]
    if c not in m.dual:
        return None
    return abs(m.dual[c]) / value(m.bring_annual_costs_to_base_year[p]) / 1000.0


def _stage_name(m):
    parts = os.path.normpath(str(getattr(m.options, "inputs_dir", "") or "")).split(os.sep)
    return parts[-2] if len(parts) >= 2 else ""


def _write_connection_outputs(m, outdir):
    """tracked_demand_connection_costs.csv (cost, bill split, headroom charge by TD and period) and
    tracked_demand_headroom_ledger.csv (the headroom charge of every stage so far, §108 (f))."""
    rows, ledger = [], []
    stage = _stage_name(m)
    for td in m.TRACKED_DEMANDS:
        z = _td_zone(m, td)
        conn = value(_connection(m, td))
        existing = _existing_connection(m, td)
        per_kw, source = _connection_cost_per_kw(m, td)
        annual = conn * 1000.0 * per_kw * crf(value(m.interest_rate), value(m.td_connection_life_years))
        share = value(m.td_connection_cost_share_demand[td])
        new_draw = value(m.TDHeadroomDrawMW[td]) if td in m.TD_HEADROOM_TDS else 0.0
        carried = value(m.td_headroom_draw_existing_mw[td]) if td in m.TD_CONNECTION_EXISTING else 0.0
        for p in m.PERIODS:
            hv = _headroom_value(m, z, p) if z is not None else None
            new_cost = new_draw * hv * 1000.0 if hv is not None else None
            carried_cost = carried * hv * 1000.0 if hv is not None else None
            rows.append({
                "tracked_demand": td, "load_zone": z, "period": p, "stage": stage,
                "connection_mw": conn, "existing_connection_mw": existing, "new_connection_mw": conn - existing,
                "endogenous": int(td in m.TD_ENDOGENOUS_CONNECTION),
                "cost_per_kw_overnight": per_kw, "cost_source": source,
                "annual_cost_usd": annual, "cost_share_demand": share,
                "paid_by_demand_usd": annual * share, "paid_by_other_customers_usd": annual * (1 - share),
                "headroom_draw_mw": new_draw, "headroom_value_usd_per_kw_yr": hv,
                "headroom_charge_usd": new_cost,
                # in objective units (NPV, base year), comparable with a counterfactual's objective difference
                "headroom_charge_npv_usd": (new_cost * value(m.bring_annual_costs_to_base_year[p])
                                            if new_cost is not None else None),
                "carried_headroom_draw_mw": carried, "carried_headroom_charge_usd": carried_cost,
            })
            if td in m.TD_HEADROOM_TDS or carried > 0:
                ledger.append({
                    "tracked_demand": td, "load_zone": z, "stage": stage, "period": p,
                    "years": value(m.period_length_years[p]),
                    "draw_mw_connected_this_stage": new_draw, "draw_mw_carried": carried,
                    "headroom_value_usd_per_kw_yr": hv,
                    "charge_usd_per_yr_connect_stage": new_cost, "charge_usd_per_yr_carried": carried_cost,
                })
    pd.DataFrame(rows).to_csv(os.path.join(outdir, "tracked_demand_connection_costs.csv"), index=False)

    # the ledger: earlier stages' rows (carried in) then this stage's
    case = os.path.basename(os.path.normpath(str(getattr(m.options, "outputs_dir", "") or outdir)))
    prev = os.path.join(m.options.inputs_dir, f"tracked_demand_headroom_ledger.chained.{case}.csv")
    frames = [pd.read_csv(prev)] if os.path.exists(prev) else []
    if ledger:
        frames.append(pd.DataFrame(ledger))
    if frames:
        led = pd.concat(frames, ignore_index=True)
        led.to_csv(os.path.join(outdir, "tracked_demand_headroom_ledger.csv"), index=False)
        # totals over the period's years, undiscounted: the stage where each TD connected, and all later stages
        led = led.assign(
            connect=led["charge_usd_per_yr_connect_stage"].fillna(0) * led["years"],
            later=led["charge_usd_per_yr_carried"].fillna(0) * led["years"],
        )
        tot = led.groupby("tracked_demand")[["connect", "later"]].sum().rename(columns={
            "connect": "headroom_charge_connect_stage_usd", "later": "headroom_charge_later_stages_usd"})
        tot["headroom_charge_total_usd"] = tot.sum(axis=1)
        tot.reset_index().to_csv(os.path.join(outdir, "tracked_demand_headroom_summary.csv"), index=False)


def _write_onsite_gen_outputs(m, outdir):
    """tracked_demand_onsite_fuel_use.csv (the gas-network interface) and tracked_demand_onsite_emissions.csv."""
    light = getattr(m, "LIGHT_TPS", ())
    fuel_rows = []
    for td, g in m.TD_ONSITE_GENS:
        if g not in m.FUEL_BASED_GENS:
            continue
        for t in m.TPS_FOR_GEN[g]:
            for f in m.FUELS_FOR_GEN[g]:
                if (g, t, f) not in m.GEN_TP_FUELS:
                    continue
                fuel_rows.append((td, g, m.gen_load_zone[g], t, m.tp_timestamp[t], m.tp_period[t], f,
                                  value(m.GenFuelUseRate[g, t, f]), value(m.tp_weight_in_year[t]),
                                  value(m.DispatchEmissions[g, t, f])))
    pd.DataFrame(fuel_rows, columns=[
        "tracked_demand", "generation_project", "load_zone", "timepoint", "timestamp", "period", "fuel",
        "fuel_use_mmbtu_per_h", "tp_weight_in_year", "emissions_tco2_per_h",
    ]).to_csv(os.path.join(outdir, "tracked_demand_onsite_fuel_use.csv"), index=False)

    cov = _cap_coverage(m)
    progs = _cap_programs_of_zone(m)
    rows = []
    for td, g in m.TD_ONSITE_GENS:
        if g not in m.FUEL_BASED_GENS:
            continue
        for p in m.PERIODS:
            t_co2 = sum(
                value(m.DispatchEmissions[g, t, f]) * value(m.tp_weight_in_year[t])
                for t in m.TPS_FOR_GEN_IN_PERIOD[g, p] if t not in light
                for f in m.FUELS_FOR_GEN[g] if (g, t, f) in m.GEN_TP_FUELS
            ) if (g, p) in m.GEN_PERIODS else 0.0
            in_prog = [
                pr for pr in sorted(progs.get(m.gen_load_zone[g], ()), key=str)
                if (pr, p) in m.CO2_PROGRAM_PERIODS and m.gen_load_zone[g] in m.ZONES_IN_CO2_PROGRAM_PERIOD[pr, p]
            ] if hasattr(m, "CO2_PROGRAM_PERIODS") else []
            if not in_prog:
                rows.append((td, g, m.gen_load_zone[g], p, "", t_co2, 0, "no_programme", ""))
            for pr in in_prog:
                covered, reason = cov[td, g, pr]
                th_t = _float_or_none(value(m.td_cap_threshold_tco2[pr])) if pr in m.TD_CAP_PROGRAMS else None
                # applicability was fixed from potential to emit; flag a site whose actual emissions disagree
                check = ""
                if th_t is not None:
                    site_t = sum(
                        value(m.DispatchEmissions[h, t, f]) * value(m.tp_weight_in_year[t])
                        for h in _onsite_gens(m, td) if h in m.FUEL_BASED_GENS and (h, p) in m.GEN_PERIODS
                        for t in m.TPS_FOR_GEN_IN_PERIOD[h, p] if t not in light
                        for f in m.FUELS_FOR_GEN[h] if (h, t, f) in m.GEN_TP_FUELS
                    )
                    if not covered and reason.startswith("below_tco2") and site_t >= th_t:
                        check = f"actual site emissions {site_t:.0f} t/yr >= threshold: re-run covered"
                rows.append((td, g, m.gen_load_zone[g], p, pr, t_co2, int(covered), reason, check))
    df = pd.DataFrame(rows, columns=[
        "tracked_demand", "generation_project", "load_zone", "period", "co2_program", "tco2_per_yr",
        "covered_by_cap", "coverage_reason", "threshold_check",
    ])
    if len(m.TD_DIESEL_GENS):
        # §140: a backup fleet's stress-day (emergency) tonnes per year, averaged over the weather-year groups;
        # tco2_per_yr above is its non-emergency running (Switch's weighted accounting, in the cap rows)
        emerg = {}
        for td, g, p, w in m.TD_DIESEL_GROUPS:
            hr = value(m.gen_full_load_heat_rate[g])
            co2 = max(value(m.f_co2_intensity[f]) for f in m.FUELS_FOR_GEN[g])
            mwh = sum(value(m.DispatchGen[g, t]) * value(m.tp_duration_hrs[t])
                      for t in _stress_tps_in_group(m, td, p, w) if (g, t) in m.GEN_TPS)
            emerg[td, g, p] = emerg.get((td, g, p), 0.0) + mwh * hr * co2 / _n_groups(m, td, p)
        keys = list(zip(df["tracked_demand"], df["generation_project"], df["period"]))
        df["tco2_emergency_per_yr"] = [emerg.get(k, 0.0) for k in keys]
        df["emergency_note"] = ["stress days: outside the model's cap rows" if k in emerg else "" for k in keys]
    df.to_csv(os.path.join(outdir, "tracked_demand_onsite_emissions.csv"), index=False)


def _write_clean_ledger(m, outdir):
    """tracked_demand_clean_ledger.csv: the hourly clean ledger (§108 (b)) for each TD with CFE targets."""
    def g(var, td, t):
        return value(var[td, t]) if (td, t) in var else 0.0
    rows = []
    for td, t in m.TD_CFE_TPS:
        rows.append((
            td, t, m.tp_timestamp[t],
            value(m.TDDispatch[td, t]),
            value(m.TDGridCleanToLoad[td, t]),
            value(_onsite_clean(m, td, t)),
            g(m.TDCleanChargeOnsite, td, t),
            g(m.TDCleanChargeGrid, td, t),
            g(m.TDCleanDischarge, td, t),
            g(m.TDCleanLevel, td, t),
            value(_clean_served(m, td, t)),
        ))
    pd.DataFrame(rows, columns=[
        "tracked_demand", "timepoint", "timestamp", "consumption_MW", "grid_clean_to_load_MW", "onsite_clean_MW",
        "clean_charge_onsite_MW", "clean_charge_grid_MW", "clean_discharge_MW", "clean_level_MWh",
        "clean_served_MW",
    ]).to_csv(os.path.join(outdir, "tracked_demand_clean_ledger.csv"), index=False)


def _write_cfe_detail(m, outdir):

    def _cfe_detail_row(m, td, p, blk):
        tps = _block_tps_in_period(m, td, blk, p)
        z = _td_zone(m, td)
        if not tps or z is None:
            return (td, p, blk, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        total_mwh = sum(
            value(m.TDDispatch[td, t]) * value(m.tp_weight_in_year[t])
            for t in tps
        )
        rec_mwh = (
            value(m.TDBlockRECPurchase[td, p, blk])
            if (td, p, blk) in m.TD_REC_SUPPLY else 0.0
        )
        # the same terms as TD_CFE_Constraint (clean ledger, §108 (b))
        clean_mwh = (
            value(m.TDBlockGridCleanMWh[td, p, blk])
            + rec_mwh
            + sum(
                value(_onsite_clean_served(m, td, t)) * value(m.tp_weight_in_year[t])
                for t in tps
            )
        )
        shortfall = value(m.TDCFEShortfall[td, p, blk])
        achieved_frac = (clean_mwh / total_mwh) if total_mwh > 0 else 0.0
        return (
            td, p, blk,
            value(m.td_cfe_target[td, p, blk]),
            achieved_frac,
            clean_mwh,
            total_mwh,
            shortfall,
            rec_mwh,
        )

    reporting.write_table(
        m,
        list(m.TD_CFE_TARGET_PERIODS),
        output_file=os.path.join(outdir, "tracked_demand_cfe_detail.csv"),
        headings=("tracked_demand", "period", "time_block",
                  "cfe_target", "cfe_achieved_fraction",
                  "clean_MWh", "total_MWh", "shortfall_MWh", "rec_MWh"),
        values=_cfe_detail_row,
    )


def _write_onsite_outputs(m, outdir):
    reporting.write_table(
        m,
        list(m.TD_TECH_BUILDS),
        output_file=os.path.join(outdir, "tracked_demand_onsite_build.csv"),
        headings=("tracked_demand", "tech", "built_MW"),
        values=lambda m, td, tech: (
            td,
            tech,
            value(m.BuildOnsiteTech[td, tech]),
        ),
    )

    reporting.write_table(
        m,
        [(td, tech, p)
         for td, tech in m.TD_TECH_BUILDS
         for p in m.PERIODS],
        output_file=os.path.join(outdir, "tracked_demand_onsite_annual.csv"),
        headings=("tracked_demand", "tech", "period",
                  "annual_dispatch_MWh",
                  "annual_net_variable_cost",
                  "annual_net_emissions_tco2"),
        values=lambda m, td, tech, p: (
            td,
            tech,
            p,
            sum(
                value(m.DispatchOnsiteTech[td, tech, t])
                * value(m.tp_weight_in_year[t])
                for t in m.TPS_IN_PERIOD[p]
            ),
            sum(
                value(m.DispatchOnsiteTech[td, tech, t])
                * value(m.tp_weight_in_year[t])
                * (value(m.td_onsite_tech_variable_om[tech])
                   + value(m.td_onsite_tech_fuel_cost[tech])
                   - _onsite_subsidy(m, tech, p))
                for t in m.TPS_IN_PERIOD[p]
            ),
            sum(
                value(m.DispatchOnsiteTech[td, tech, t])
                * value(m.tp_weight_in_year[t])
                * value(m.td_onsite_tech_emissions[tech])
                * (1.0 - value(m.td_onsite_tech_capture_rate[tech]))
                for t in m.TPS_IN_PERIOD[p]
            ),
        ),
    )

    reporting.write_table(
        m,
        list(m.TD_TECH_TIMEPOINTS),
        output_file=os.path.join(outdir, "tracked_demand_onsite_dispatch.csv"),
        headings=("tracked_demand", "tech", "timepoint", "timestamp",
                  "dispatch_MW"),
        values=lambda m, td, tech, t: (
            td,
            tech,
            t,
            m.tp_timestamp[t],
            value(m.DispatchOnsiteTech[td, tech, t]),
        ),
    )


def _write_storage_outputs(m, outdir):
    reporting.write_table(
        m,
        list(m.TD_STORAGE),
        output_file=os.path.join(outdir, "tracked_demand_storage_build.csv"),
        headings=("tracked_demand", "built_power_MW", "built_energy_MWh"),
        values=lambda m, td: (
            td,
            value(m.TDStoragePowerCapacity[td]),
            value(m.TDStorageEnergyCapacity[td]),
        ),
    )

    reporting.write_table(
        m,
        list(m.TD_STORAGE_TIMEPOINTS),
        output_file=os.path.join(outdir, "tracked_demand_storage_dispatch.csv"),
        headings=("tracked_demand", "timepoint", "timestamp",
                  "charge_MW", "discharge_MW", "level_MWh"),
        values=lambda m, td, t: (
            td,
            t,
            m.tp_timestamp[t],
            value(m.TDStorageCharge[td, t]),
            value(m.TDStorageDischarge[td, t]),
            value(m.TDStorageLevel[td, t]),
        ),
    )


def _write_flex_outputs(m, outdir):
    reporting.write_table(
        m,
        list(m.TD_FLEX_TIMEPOINTS),
        output_file=os.path.join(outdir, "tracked_demand_flex_detail.csv"),
        headings=("tracked_demand", "timepoint", "timestamp",
                  "flex_limit_MW", "grid_draw_MW", "excess_MW"),
        values=lambda m, td, t: (
            td,
            t,
            m.tp_timestamp[t],
            value(m.td_flex_max_grid_draw_mw[td, t]),
            value(m.TDGridDraw[td, t]),
            value(m.TDFlexExcess[td, t]),
        ),
    )


def _write_zone_allocation_outputs(m, outdir):
    reporting.write_table(
        m,
        list(m.TD_FLEX_ZONE_PERIODS),
        output_file=os.path.join(outdir, "tracked_demand_zone_allocation.csv"),
        headings=("tracked_demand", "zone", "period", "allocation_fraction"),
        values=lambda m, td, z, p: (
            td,
            z,
            p,
            value(m.TDZoneAllocation[td, z, p]),
        ),
    )


# ── Stage 10 helpers ──────────────────────────────────────────────────────────

# Clean energy sources: zero operational CO2. Storage is excluded so that it
# doesn't inflate the clean total (its output is attributed to its source).
_CLEAN_ENERGY_SOURCES = frozenset({
    # generic / EIA names
    "solar", "csp", "sun",
    "wind", "offshore_wind", "offshorewind",
    "geothermal",
    "nuclear", "uranium",
    "water", "hydro", "hydroelectric",
})


def _find_hierarchy_file(start_dir):
    """hierarchy.csv: searched upward (up to 6 levels) from start_dir (a case's own copy wins), then from the working
    directory, then from this module's folder, which reaches the repo root (<repo>/switch/study_modules). A run starts
    in switch/ with the case folder often outside the repo (§140 VM test), so the inputs-dir search alone missed it."""
    def upward(start):
        path = os.path.abspath(start)
        for _ in range(6):
            candidate = os.path.join(path, "hierarchy.csv")
            if os.path.exists(candidate):
                return candidate
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
        return None
    for start in (start_dir, os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        if start is not None:
            found = upward(start)
            if found is not None:
                return found
    return None


def _compute_grid_metrics(m, inputs_dir=None, region_col="h2ptcreg"):
    """
    Per-(zone, timepoint) grid metrics from the solved model (§108 (b): was read from dispatch.csv).

    Returns a DataFrame indexed by [LOAD_ZONE, TIMEPOINT] (timepoint ids, as grid_clean_fraction.csv uses) with:
        timestamp            tp_timestamp, for reference
        grid_clean_fraction  share of dispatched generation from clean sources, aggregated over region_col
        co2_tonne_per_mwh    tCO2 (DispatchEmissions) per MWh of dispatched generation in the region

    Rates come from DispatchGen and DispatchEmissions per hour, unweighted, so zero-weight stress hours get their
    own values. Light stress timepoints (§69) have no fuel use or emissions in the model, so their intensity is 0.
    Left out: storage (it passes energy through) and every tracked demand's linked on-site generators (they serve
    the demand behind the meter; counting them would charge the demand twice for its own plant).
    Regions: column region_col of hierarchy.csv (searched upward from inputs_dir); zone level when it is absent or
    region_col is 'ba'. Returns None when the model has no generators.
    """
    storage = set(getattr(m, "STORAGE_GENS", ()))
    onsite = {g for _, g in m.TD_ONSITE_GENS}
    light = getattr(m, "LIGHT_TPS", ())
    rows = []
    for g, t in m.GEN_TPS:
        if g in storage or g in onsite:
            continue
        src = str(value(m.gen_energy_source[g])).lower()
        gen = value(m.DispatchGen[g, t])
        co2 = 0.0
        if g in m.FUEL_BASED_GENS and t not in light:
            co2 = sum(
                value(m.DispatchEmissions[g, t, f])
                for f in m.FUELS_FOR_GEN[g] if (g, t, f) in m.GEN_TP_FUELS
            )
        rows.append((m.gen_load_zone[g], t, gen, gen if src in _CLEAN_ENERGY_SOURCES else 0.0, co2))
    if not rows:
        return None
    df_gen = pd.DataFrame(rows, columns=["gen_load_zone", "TIMEPOINT", "gen_mw", "clean_gen_mw", "co2_per_hr"])

    # ── Try to load hierarchy for regional aggregation ────────────────────────
    region_map = None
    if inputs_dir is not None and region_col != "ba":
        hier_path = _find_hierarchy_file(inputs_dir)
        if hier_path is None:
            print(
                f"tracked_demands Stage 10: hierarchy.csv not found; "
                f"grid clean fraction computed at zone level."
            )
        else:
            hier_df = pd.read_csv(hier_path, dtype=str)
            if "ba" not in hier_df.columns or region_col not in hier_df.columns:
                print(
                    f"tracked_demands Stage 10: column '{region_col}' not in "
                    f"hierarchy.csv; grid clean fraction computed at zone level."
                )
            else:
                region_map = (
                    hier_df[["ba", region_col]]
                    .rename(columns={"ba": "gen_load_zone"})
                    .drop_duplicates()
                )
                print(
                    f"tracked_demands Stage 10: grid clean fraction aggregated "
                    f"at '{region_col}' level ({region_map[region_col].nunique()} regions)."
                )

    if region_map is not None:
        df_gen = df_gen.merge(region_map, on="gen_load_zone", how="left")
        # Zones missing from hierarchy fall back to their own zone name.
        df_gen[region_col] = df_gen[region_col].fillna(df_gen["gen_load_zone"])
        key = region_col
    else:
        df_gen["_region"] = df_gen["gen_load_zone"]
        key = "_region"

    agg = df_gen.groupby([key, "TIMEPOINT"], sort=False).agg(
        total_gen_mw=("gen_mw", "sum"),
        clean_gen_mw=("clean_gen_mw", "sum"),
        co2_tonne_per_hr=("co2_per_hr", "sum"),
    )
    safe_total = agg["total_gen_mw"].clip(lower=1e-9)
    agg["grid_clean_fraction"] = (agg["clean_gen_mw"] / safe_total).clip(0.0, 1.0)
    agg["co2_tonne_per_mwh"] = (agg["co2_tonne_per_hr"] / safe_total).clip(lower=0.0)

    # Map regional metrics back to every (zone, timepoint) of the zones in each region.
    zone_tp = (
        df_gen[["gen_load_zone", key, "TIMEPOINT"]]
        .drop_duplicates()
        .merge(agg[["grid_clean_fraction", "co2_tonne_per_mwh"]].reset_index(), on=[key, "TIMEPOINT"])
        .rename(columns={"gen_load_zone": "LOAD_ZONE"})
    )
    zone_tp["timestamp"] = zone_tp["TIMEPOINT"].map(lambda t: m.tp_timestamp[t])
    zone_tp = zone_tp.sort_values(["LOAD_ZONE", "TIMEPOINT"], key=lambda c: c.map(str) if c.name == "LOAD_ZONE" else c)
    return zone_tp.set_index(["LOAD_ZONE", "TIMEPOINT"])[["timestamp", "grid_clean_fraction", "co2_tonne_per_mwh"]]


def _write_grid_metrics_computed(m, outdir, grid_df):
    """Write grid_clean_fraction_computed.csv and grid_co2_intensity_computed.csv, keyed by timepoint id, so the
    first can be used as the next pass's grid_clean_fraction.csv (§108 (b))."""
    base = grid_df.reset_index()
    base[["LOAD_ZONE", "TIMEPOINT", "grid_clean_fraction", "timestamp"]].to_csv(
        os.path.join(outdir, "grid_clean_fraction_computed.csv"),
        index=False,
        float_format="%.6f",
    )
    base[["LOAD_ZONE", "TIMEPOINT", "co2_tonne_per_mwh", "timestamp"]].to_csv(
        os.path.join(outdir, "grid_co2_intensity_computed.csv"),
        index=False,
        float_format="%.8f",
    )


def _onsite_co2_per_hr(m, td, t):
    """tCO2/h from td's private on-site techs and its linked generators at t."""
    private = sum(
        value(m.DispatchOnsiteTech[td, tech, t])
        * value(m.td_onsite_tech_emissions[tech])
        * (1.0 - value(m.td_onsite_tech_capture_rate[tech]))
        for tech in m.TD_ONSITE_TECHS
    )
    linked = sum(
        value(m.DispatchEmissions[g, t, f])
        for g in _onsite_gens(m, td) if g in m.FUEL_BASED_GENS
        for f in m.FUELS_FOR_GEN[g] if (g, t, f) in m.GEN_TP_FUELS
    )
    return private + linked


def _write_td_emissions(m, outdir, grid_df):
    """
    Write tracked_demand_hourly_emissions.csv.

    Attributes CO2 to each TD from:
      - Grid draw:  TDGridDraw[td, t] × zone_co2_intensity[z, t]
      - On-site:    private techs' net emissions rate × dispatch, plus the linked generators' DispatchEmissions

    Columns: tracked_demand, timepoint, timestamp,
             grid_co2_tonne_per_hr, onsite_co2_tonne_per_hr, total_co2_tonne_per_hr
    """
    co2_lookup = grid_df["co2_tonne_per_mwh"].to_dict()   # (zone, timepoint) -> tCO2/MWh

    rows = []
    for td in m.TRACKED_DEMANDS:
        z = _td_zone(m, td)
        for t in m.TIMEPOINTS:
            co2_intensity = co2_lookup.get((z, t), 0.0) if z is not None else 0.0
            grid_co2 = value(m.TDGridDraw[td, t]) * co2_intensity
            onsite_co2 = _onsite_co2_per_hr(m, td, t)
            rows.append((
                td, t, str(m.tp_timestamp[t]),
                round(grid_co2, 8),
                round(onsite_co2, 8),
                round(grid_co2 + onsite_co2, 8),
            ))

    pd.DataFrame(
        rows,
        columns=[
            "tracked_demand", "timepoint", "timestamp",
            "grid_co2_tonne_per_hr", "onsite_co2_tonne_per_hr", "total_co2_tonne_per_hr",
        ],
    ).to_csv(
        os.path.join(outdir, "tracked_demand_hourly_emissions.csv"),
        index=False,
    )


def _write_cfe_computed(m, outdir, grid_df):
    """
    Write tracked_demand_cfe_computed.csv.

    Re-computes CFE fractions with the dispatch-derived clean fractions (grid_df) instead of the exogenous
    grid_clean_fraction parameter, with the same ledger as the model (§108 (b)): per hour, on-site clean served
    (direct use + clean discharge) plus the grid's clean share of the draw not used for clean charging, capped at
    consumption, less export at the clean fraction.

    Columns: tracked_demand, period, time_block,
             cfe_target, cfe_computed_fraction, computed_clean_MWh,
             total_MWh, computed_shortfall_MWh, rec_MWh
    """
    cf_lookup = grid_df["grid_clean_fraction"].to_dict()   # (zone, timepoint) -> fraction

    def clean_at(td, z, t):
        cf = cf_lookup.get((z, t), 0.0)
        onsite = value(_onsite_clean(m, td, t))
        charge_grid = 0.0
        if (td, t) in m.TD_CFE_STORAGE_TPS:
            onsite += value(m.TDCleanDischarge[td, t]) - value(m.TDCleanChargeOnsite[td, t])
            charge_grid = value(m.TDCleanChargeGrid[td, t])
        grid = max(0.0, value(m.TDGridDraw[td, t]) * cf - charge_grid)
        served = min(value(m.TDDispatch[td, t]), onsite + grid)
        if (td, t) in m.TD_EXPORT_TIMEPOINTS:
            served -= cf * value(m.TDGridExport[td, t])
        return served

    rows = []
    for td, p, blk in m.TD_CFE_TARGET_PERIODS:
        tps = _block_tps_in_period(m, td, blk, p)
        z = _td_zone(m, td)
        if not tps or z is None:
            continue

        total_mwh = sum(
            value(m.TDDispatch[td, t]) * value(m.tp_weight_in_year[t])
            for t in tps
        )
        rec_mwh = (
            value(m.TDBlockRECPurchase[td, p, blk])
            if (td, p, blk) in m.TD_REC_SUPPLY else 0.0
        )
        computed_clean_mwh = rec_mwh + sum(
            clean_at(td, z, t) * value(m.tp_weight_in_year[t]) for t in tps
        )

        target = value(m.td_cfe_target[td, p, blk])
        shortfall = max(0.0, target * total_mwh - computed_clean_mwh)
        achieved = computed_clean_mwh / total_mwh if total_mwh > 0 else 0.0

        rows.append((
            td, p, blk,
            target,
            round(achieved, 6),
            round(computed_clean_mwh, 2),
            round(total_mwh, 2),
            round(shortfall, 2),
            round(rec_mwh, 2),
        ))

    pd.DataFrame(
        rows,
        columns=[
            "tracked_demand", "period", "time_block",
            "cfe_target", "cfe_computed_fraction",
            "computed_clean_MWh", "total_MWh", "computed_shortfall_MWh",
            "rec_MWh",
        ],
    ).to_csv(
        os.path.join(outdir, "tracked_demand_cfe_computed.csv"),
        index=False,
    )


# 45V credit tiers: lifecycle GHG (kg CO2e per kg H2) thresholds and $/kg credit.
# Values reflect prevailing-wage-adjusted rates from IRA § 45V.
_45V_TIERS = [
    (0.45, 1, "$3.00/kg", 3.00),
    (1.5,  2, "$1.00/kg", 1.00),
    (2.5,  3, "$0.75/kg", 0.75),
    (4.0,  4, "$0.60/kg", 0.60),
]


def _45v_credit(co2_kg_per_kg_h2):
    for threshold, tier, label, credit in _45V_TIERS:
        if co2_kg_per_kg_h2 <= threshold:
            return tier, label, credit
    return None, "no_credit", 0.0


def _write_45v_assessment(m, outdir, grid_df):
    """
    Write tracked_demand_45v_assessment.csv for electrolyzer TDs.

    Computes annual lifecycle CO2 per kg H2 using the actual grid CO2 intensity
    at each timepoint weighted by the TD's grid draw. Assigns the IRA 45V credit
    tier and indicative $/kg credit value.

    Columns: tracked_demand, period, total_h2_kg, grid_electricity_mwh,
             attributed_co2_tonne, lifecycle_co2_kg_per_kg_h2,
             tier, credit_dollar_per_kg, total_credit_dollar
    """
    co2_lookup = grid_df["co2_tonne_per_mwh"].to_dict()

    rows = []
    for td in m.TD_H2_STORAGE:
        z = _td_zone(m, td)
        for p in m.PERIODS:
            tps = list(m.TPS_IN_PERIOD[p])

            total_h2_kg = sum(
                value(m.TDH2Production[td, t]) * value(m.tp_weight_in_year[t])
                for t in tps
            )

            grid_mwh = sum(
                value(m.TDGridDraw[td, t]) * value(m.tp_weight_in_year[t])
                for t in tps
            )

            grid_co2 = sum(
                value(m.TDGridDraw[td, t])
                * co2_lookup.get((z, t), 0.0)
                * value(m.tp_weight_in_year[t])
                for t in tps
            ) if z else 0.0

            onsite_co2 = sum(
                _onsite_co2_per_hr(m, td, t) * value(m.tp_weight_in_year[t]) for t in tps
            )

            total_co2 = grid_co2 + onsite_co2

            # Convert tCO2 → kg CO2, then divide by kg H2 for intensity
            lc_co2 = (total_co2 * 1000.0 / total_h2_kg) if total_h2_kg > 0 else 0.0

            tier, label, credit_per_kg = _45v_credit(lc_co2)
            total_credit = credit_per_kg * total_h2_kg

            rows.append((
                td, p,
                round(total_h2_kg, 1),
                round(grid_mwh, 1),
                round(total_co2, 4),
                round(lc_co2, 6),
                tier if tier else "none",
                label,
                round(total_credit, 2),
            ))

    pd.DataFrame(
        rows,
        columns=[
            "tracked_demand", "period",
            "total_h2_kg", "grid_electricity_mwh",
            "attributed_co2_tonne", "lifecycle_co2_kg_per_kg_h2",
            "tier", "credit_dollar_per_kg", "total_credit_dollar",
        ],
    ).to_csv(
        os.path.join(outdir, "tracked_demand_45v_assessment.csv"),
        index=False,
    )


def _write_h2_outputs(m, outdir):
    reporting.write_table(
        m,
        list(m.TD_H2_STORAGE),
        output_file=os.path.join(outdir, "tracked_demand_h2_build.csv"),
        headings=("tracked_demand", "h2_tank_capacity_kg"),
        values=lambda m, td: (
            td,
            value(m.TDH2StorageCapacity[td]),
        ),
    )

    reporting.write_table(
        m,
        list(m.TD_H2_STORAGE_TIMEPOINTS),
        output_file=os.path.join(outdir, "tracked_demand_h2_dispatch.csv"),
        headings=("tracked_demand", "timepoint", "timestamp",
                  "production_kg_hr", "delivery_kg_hr", "level_kg"),
        values=lambda m, td, t: (
            td,
            t,
            m.tp_timestamp[t],
            value(m.TDH2Production[td, t]),
            value(m.TDH2Delivery[td, t]),
            value(m.TDH2StorageLevel[td, t]),
        ),
    )


def _write_soft_cap_summary(m, outdir):
    rows = []
    for td in m.TD_SOFT_CAP:
        for p in m.PERIODS:
            tps_in_p = list(m.TPS_IN_PERIOD[p])
            cap_mw = value(m.td_grid_soft_cap_mw[td])
            penalty = value(m.td_grid_soft_cap_penalty[td])
            annual_excess_mwh = sum(
                value(m.TDGridSoftExcess[td, t]) * value(m.tp_weight_in_year[t])
                for t in tps_in_p
            )
            hours_binding = sum(
                value(m.tp_weight_in_year[t])
                for t in tps_in_p
                if value(m.TDGridSoftExcess[td, t]) > 1e-4
            )
            penalty_cost = annual_excess_mwh * penalty
            annual_dispatch_mwh = sum(
                value(m.TDDispatch[td, t]) * value(m.tp_weight_in_year[t])
                for t in tps_in_p
            )
            mean_penalty_per_mwh = (
                penalty_cost / annual_dispatch_mwh
                if annual_dispatch_mwh > 0 else 0.0
            )
            rows.append((
                td, p, cap_mw, penalty,
                annual_excess_mwh, hours_binding,
                penalty_cost, mean_penalty_per_mwh,
            ))

    import csv
    out_path = os.path.join(outdir, "tracked_demand_grid_cap_summary.csv")
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "tracked_demand", "period",
            "soft_cap_mw", "penalty_per_mwh",
            "annual_excess_mwh", "hours_binding",
            "penalty_cost_per_yr", "mean_penalty_per_mwh_dc",
        ])
        writer.writerows(rows)
