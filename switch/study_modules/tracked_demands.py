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
            + sum(m.DispatchOnsiteTech[td, tech, t] for tech in m.TD_ONSITE_TECHS)
            + (m.TDStorageDischarge[td, t] - m.TDStorageCharge[td, t]
               if td in m.TD_STORAGE else 0)
        ),
    )

    # Dispatch bounds — use timepoint-indexed params (Stage 6). When no
    # dispatch_bounds.csv is provided the callable defaults return the scalar
    # values, so behaviour is identical to the previous Stage 1 constraints.
    m.TD_Dispatch_Lower_Bound = Constraint(
        m.TD_TIMEPOINTS,
        rule=lambda m, td, t: m.TDDispatch[td, t] >= m.td_min_power_mw[td, t],
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

    # Grid interconnection capacity limit.
    m.TD_Grid_Interconnect_Limit = Constraint(
        m.TD_TIMEPOINTS,
        rule=lambda m, td, t: m.TDGridDraw[td, t] <= m.td_grid_interconnect_mw[td],
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
        fixed = sum(m.TDGridDraw[td, t] for td in m._td_fixed_in_zone.get(z, []))
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

    # ── CFE soft constraint ───────────────────────────────────────────────────

    def _cfe_constraint_rule(m, td, p, blk):
        tps = _block_tps_in_period(m, td, blk, p)
        if not tps:
            return Constraint.Skip
        z = _td_zone(m, td)
        if z is None:
            return Constraint.Skip

        # Grid clean credit comes from TDBlockGridCleanMWh (Stage 9 prerequisite):
        # bounded above by actual earned grid clean MWh (TD_CFE_Grid_Earned) and
        # optionally by a per-block source cap (TD_Grid_Clean_Cap, Stage 9).
        # RECs (Stage 11), on-site clean, and clean storage discharge added directly.
        rec_mwh = (
            m.TDBlockRECPurchase[td, p, blk]
            if (td, p, blk) in m.TD_REC_SUPPLY else 0
        )
        estimated_clean_mwh = (
            m.TDBlockGridCleanMWh[td, p, blk]
            + rec_mwh
            + sum(
                (sum(
                     m.DispatchOnsiteTech[td, tech, t]
                     for tech in m.TD_ONSITE_TECHS
                     if m.td_onsite_tech_is_clean[tech]
                 )
                 + (m.TDStorageDischarge[td, t] * m.grid_clean_fraction[z, t]
                    if td in m.TD_STORAGE else 0))
                * m.tp_weight_in_year[t]
                for t in tps
            )
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
            m.TDGridDraw[td, t] * m.grid_clean_fraction[z, t] * m.tp_weight_in_year[t]
            for t in tps
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
            <= m.TDZoneAllocation[td, z, m.tp_period[t]] * m.td_grid_interconnect_mw[td]
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
        ),
        optional_params=(
            m.td_default_min_power_mw,
            m.td_location_fixed,
            m.td_grid_interconnect_mw,
            m.td_onsite_emissions_in_system_cap,
        ),
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

    Stage 10 outputs require dispatch.csv written earlier by generators_core_dispatch.
    If that file is absent (e.g., --skip-output-file dispatch.csv), Stage 10 is skipped.
    """
    if not m.TRACKED_DEMANDS:
        return

    # ── Stage 1 outputs ───────────────────────────────────────────────────────

    reporting.write_table(
        m,
        m.TD_TIMEPOINTS,
        output_file=os.path.join(outdir, "tracked_demand_dispatch.csv"),
        headings=("tracked_demand", "timepoint", "timestamp",
                  "TDDispatch_MW", "TDGridDraw_MW"),
        values=lambda m, td, t: (
            td,
            t,
            m.tp_timestamp[t],
            m.TDDispatch[td, t],
            m.TDGridDraw[td, t],
        ),
    )

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
    grid_df = _compute_grid_metrics(outdir, m.options.inputs_dir, region_col)
    if grid_df is not None:
        _write_grid_metrics_computed(m, outdir, grid_df)
        _write_td_emissions(m, outdir, grid_df)
        if m.TD_CFE_TARGET_PERIODS:
            _write_cfe_computed(m, outdir, grid_df)
        if m.TD_H2_STORAGE:
            _write_45v_assessment(m, outdir, grid_df)


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
        clean_mwh = (
            value(m.TDBlockGridCleanMWh[td, p, blk])
            + rec_mwh
            + sum(
                (sum(
                     value(m.DispatchOnsiteTech[td, tech, t])
                     for tech in m.TD_ONSITE_TECHS
                     if m.td_onsite_tech_is_clean[tech]
                 )
                 + (value(m.TDStorageDischarge[td, t])
                    * value(m.grid_clean_fraction[z, t])
                    if td in m.TD_STORAGE else 0.0))
                * value(m.tp_weight_in_year[t])
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
    """Search upward from start_dir (up to 6 levels) for hierarchy.csv."""
    path = os.path.abspath(start_dir)
    for _ in range(6):
        candidate = os.path.join(path, "hierarchy.csv")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


def _compute_grid_metrics(outdir, inputs_dir=None, region_col="h2ptcreg"):
    """
    Read dispatch.csv from outdir and compute per-(zone, timepoint) grid metrics,
    aggregated at the region level specified by region_col in hierarchy.csv.

    Returns a DataFrame indexed by ["gen_load_zone", "timestamp"] with columns:
        grid_clean_fraction — fraction of dispatched non-storage generation from
                              clean sources, aggregated over region_col
        co2_tonne_per_mwh  — tCO2/MWh of dispatched generation in the region

    The region-level values are assigned back to each zone in that region so that
    zone-level lookups in TD emissions and CFE computations use the wider grid mix.

    Falls back to zone-level aggregation if hierarchy.csv is not found or region_col
    is absent, or when region_col == 'ba'.

    Returns None if dispatch.csv is absent (Stage 10 is silently skipped).
    """
    dispatch_csv = os.path.join(outdir, "dispatch.csv")
    if not os.path.exists(dispatch_csv):
        return None

    df = pd.read_csv(dispatch_csv, dtype={"timestamp": str})

    # Exclude storage rows: pass-through devices don't generate clean energy.
    df_gen = df[~df["is_storage"].astype(bool)].copy()

    # Per-timepoint CO2 rate (tCO2/hr) from the annually-weighted dispatch column.
    df_gen["co2_per_hr"] = (
        df_gen["DispatchEmissions_tCO2_per_typical_yr"]
        / df_gen["tp_weight_in_year_hrs"].clip(lower=1e-9)
    )

    # Clean classification by energy source (case-insensitive).
    df_gen["is_clean"] = df_gen["gen_energy_source"].str.lower().isin(_CLEAN_ENERGY_SOURCES)
    df_gen["clean_gen_mw"] = df_gen["DispatchGen_MW"] * df_gen["is_clean"].astype(float)

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

    # ── Aggregate ─────────────────────────────────────────────────────────────
    if region_map is not None:
        df_gen = df_gen.merge(region_map, on="gen_load_zone", how="left")
        # Zones missing from hierarchy fall back to their own zone name.
        df_gen[region_col] = df_gen[region_col].fillna(df_gen["gen_load_zone"])

        # Sum dispatch at region × timestamp level.
        reg_agg = df_gen.groupby([region_col, "timestamp"], sort=False).agg(
            total_gen_mw=("DispatchGen_MW", "sum"),
            clean_gen_mw=("clean_gen_mw", "sum"),
            co2_tonne_per_hr=("co2_per_hr", "sum"),
        )
        safe_total = reg_agg["total_gen_mw"].clip(lower=1e-9)
        reg_agg["grid_clean_fraction"] = (reg_agg["clean_gen_mw"] / safe_total).clip(0.0, 1.0)
        reg_agg["co2_tonne_per_mwh"] = (reg_agg["co2_tonne_per_hr"] / safe_total).clip(lower=0.0)

        # Map regional metrics back to (zone, timestamp) index for zone-level lookup.
        zone_ts = (
            df_gen[["gen_load_zone", region_col, "timestamp"]]
            .drop_duplicates()
            .merge(
                reg_agg[["grid_clean_fraction", "co2_tonne_per_mwh"]].reset_index(),
                on=[region_col, "timestamp"],
            )
            .set_index(["gen_load_zone", "timestamp"])
        )
        return zone_ts[["grid_clean_fraction", "co2_tonne_per_mwh"]]

    else:
        # Zone-level fallback.
        agg = df_gen.groupby(["gen_load_zone", "timestamp"], sort=False).agg(
            total_gen_mw=("DispatchGen_MW", "sum"),
            clean_gen_mw=("clean_gen_mw", "sum"),
            co2_tonne_per_hr=("co2_per_hr", "sum"),
        )
        safe_total = agg["total_gen_mw"].clip(lower=1e-9)
        agg["grid_clean_fraction"] = (agg["clean_gen_mw"] / safe_total).clip(0.0, 1.0)
        agg["co2_tonne_per_mwh"] = (agg["co2_tonne_per_hr"] / safe_total).clip(lower=0.0)
        return agg[["grid_clean_fraction", "co2_tonne_per_mwh"]]


def _write_grid_metrics_computed(m, outdir, grid_df):
    """Write grid_clean_fraction_computed.csv and grid_co2_intensity_computed.csv."""
    base = grid_df.reset_index()[
        ["gen_load_zone", "timestamp", "grid_clean_fraction", "co2_tonne_per_mwh"]
    ].rename(columns={"gen_load_zone": "LOAD_ZONE", "timestamp": "TIMEPOINT"})

    base[["LOAD_ZONE", "TIMEPOINT", "grid_clean_fraction"]].to_csv(
        os.path.join(outdir, "grid_clean_fraction_computed.csv"),
        index=False,
        float_format="%.6f",
    )
    base[["LOAD_ZONE", "TIMEPOINT", "co2_tonne_per_mwh"]].to_csv(
        os.path.join(outdir, "grid_co2_intensity_computed.csv"),
        index=False,
        float_format="%.8f",
    )


def _write_td_emissions(m, outdir, grid_df):
    """
    Write tracked_demand_hourly_emissions.csv.

    Attributes CO2 to each TD from:
      - Grid draw:  TDGridDraw[td, t] × zone_co2_intensity[z, t]
      - Onsite gen: DispatchOnsiteTech[td, tech, t] × net_emissions_rate[tech]

    Columns: tracked_demand, timepoint, timestamp,
             grid_co2_tonne_per_hr, onsite_co2_tonne_per_hr, total_co2_tonne_per_hr
    """
    co2_lookup = grid_df["co2_tonne_per_mwh"].to_dict()   # (zone, timestamp) -> tCO2/MWh

    rows = []
    for td in m.TRACKED_DEMANDS:
        z = _td_zone(m, td)
        for t in m.TIMEPOINTS:
            ts = str(m.tp_timestamp[t])
            key = (z, ts) if z is not None else None
            co2_intensity = co2_lookup.get(key, 0.0) if key else 0.0

            grid_co2 = value(m.TDGridDraw[td, t]) * co2_intensity

            onsite_co2 = sum(
                value(m.DispatchOnsiteTech[td, tech, t])
                * value(m.td_onsite_tech_emissions[tech])
                * (1.0 - value(m.td_onsite_tech_capture_rate[tech]))
                for tech in m.TD_ONSITE_TECHS
            ) if m.TD_ONSITE_TECHS else 0.0

            rows.append((
                td, t, ts,
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

    Re-computes CFE fractions using actual dispatch-derived clean fractions
    (from grid_df) rather than the exogenous grid_clean_fraction parameter.
    Provides a post-hoc check on how well the optimized plan performs given
    the real grid mix.

    Columns: tracked_demand, period, time_block,
             cfe_target, cfe_computed_fraction, computed_clean_MWh,
             total_MWh, computed_shortfall_MWh
    """
    cf_lookup = grid_df["grid_clean_fraction"].to_dict()   # (zone, timestamp) -> fraction

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
            (
                value(m.TDGridDraw[td, t])
                * cf_lookup.get((z, str(m.tp_timestamp[t])), 0.0)
                + (sum(
                    value(m.DispatchOnsiteTech[td, tech, t])
                    for tech in m.TD_ONSITE_TECHS
                    if m.td_onsite_tech_is_clean[tech]
                ) if m.TD_ONSITE_TECHS else 0.0)
                + (value(m.TDStorageDischarge[td, t])
                   * cf_lookup.get((z, str(m.tp_timestamp[t])), 0.0)
                   if td in m.TD_STORAGE else 0.0)
            ) * value(m.tp_weight_in_year[t])
            for t in tps
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
                * co2_lookup.get((z, str(m.tp_timestamp[t])), 0.0)
                * value(m.tp_weight_in_year[t])
                for t in tps
            ) if z else 0.0

            onsite_co2 = sum(
                value(m.DispatchOnsiteTech[td, tech, t])
                * value(m.td_onsite_tech_emissions[tech])
                * (1.0 - value(m.td_onsite_tech_capture_rate[tech]))
                * value(m.tp_weight_in_year[t])
                for tech in m.TD_ONSITE_TECHS
                for t in tps
            ) if m.TD_ONSITE_TECHS else 0.0

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
