"""
Constrain in-zone annual generation as a fraction of annual load,
and/or in-zone generation as a fraction of contemporaneous load at every
timepoint (hourly/peak share).

Supports two levels of constraint:
  1. Zone-level — applied to individual model zones (LOAD_ZONES)
  2. Group-level — applied to the summed gen and load across a named group
                   of model zones (e.g., all BAs in a state or transreg)

All constraints are optional per zone/period: omit a row or leave a cell
blank to leave that constraint inactive.

──────────────────────────────────────────────────────────────────────────────
Input files (all optional):
──────────────────────────────────────────────────────────────────────────────

gen_zone_load_ratio.csv
  Per-zone constraints. LOAD_ZONE must be a model zone (in LOAD_ZONES).
  Extra 'historical_*' reference columns are ignored.
  Columns used:
    LOAD_ZONE, PERIOD,
    min_annual_ratio, max_annual_ratio,
    min_peak_share, max_peak_share

gen_zone_groups.csv
  Maps group names to their constituent model zones. Generated automatically
  by make_zone_ratios.py --agg-by <COLUMN>.
    GROUP_NAME  - group identifier (e.g. 'LA', 'SERTP', 'MISO')
    LOAD_ZONE   - model zone belonging to this group

gen_group_load_ratio.csv
  Same column format as gen_zone_load_ratio.csv, but GROUP_NAME instead of
  LOAD_ZONE. Each row constrains the *sum* of gen and load across all model
  zones in that group. GROUP_NAME must appear in gen_zone_groups.csv.
    GROUP_NAME, PERIOD,
    min_annual_ratio, max_annual_ratio,
    min_peak_share, max_peak_share

──────────────────────────────────────────────────────────────────────────────
Output file: gen_zone_ratio_summary.csv
──────────────────────────────────────────────────────────────────────────────
Written to the outputs directory after each solve. Contains one row per
zone/group per period with actual values and the applied constraint bounds.
"""

import os
import pandas as pd
from switch_model.utilities import apply_input_aliases
from pyomo.environ import (
    Any,
    Constraint,
    Expression,
    NonNegativeReals,
    Param,
    Set,
    value,
)


def define_components(m):
    # ══════════════════════════════════════════════════════════════════════════
    # A) ZONE-LEVEL CONSTRAINTS
    # ══════════════════════════════════════════════════════════════════════════

    # ── Params (defaults = no-constraint sentinels) ──────────────────────────
    m.gen_zone_min_annual_ratio = Param(
        m.LOAD_ZONES, m.PERIODS,
        within=NonNegativeReals,
        default=0.0,
        doc="Minimum annual in-zone gen / annual in-zone load. 0 = unconstrained.",
    )
    m.gen_zone_max_annual_ratio = Param(
        m.LOAD_ZONES, m.PERIODS,
        within=NonNegativeReals,
        default=float("inf"),
        doc="Maximum annual in-zone gen / annual in-zone load. inf = unconstrained.",
    )
    m.gen_zone_min_peak_share = Param(
        m.LOAD_ZONES, m.PERIODS,
        within=NonNegativeReals,
        default=0.0,
        doc="Min in-zone gen share of contemporaneous load at every timepoint.",
    )
    m.gen_zone_max_peak_share = Param(
        m.LOAD_ZONES, m.PERIODS,
        within=NonNegativeReals,
        default=float("inf"),
        doc="Max in-zone gen share of contemporaneous load at every timepoint.",
    )

    # ── Expressions ──────────────────────────────────────────────────────────
    m.ZoneAnnualGenMWh = Expression(
        m.LOAD_ZONES, m.PERIODS,
        rule=lambda m, z, p: sum(
            m.DispatchGen[g, t] * m.tp_weight_in_year[t]
            for g in m.GENS_IN_ZONE[z]
            for t in m.TPS_FOR_GEN_IN_PERIOD[g, p]
        ),
        doc="Annual generation (MWh/yr) by all generators in a load zone in a period.",
    )
    m.ZoneAnnualLoadMWh = Expression(
        m.LOAD_ZONES, m.PERIODS,
        rule=lambda m, z, p: sum(
            m.zone_demand_mw[z, t] * m.tp_weight_in_year[t]
            for t in m.TPS_IN_PERIOD[p]
        ),
        doc="Annual load (MWh/yr) in a load zone in a period.",
    )
    # Per-timepoint in-zone generation (reused in hourly constraints & post_solve)
    m.ZoneTimeGenMW = Expression(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=lambda m, z, t: sum(
            m.DispatchGen[g, t]
            for g in m.GENS_IN_ZONE[z]
            if (g, t) in m.GEN_TPS
        ),
        doc="Total in-zone generation (MW) at each timepoint.",
    )

    # ── Annual ratio constraints ──────────────────────────────────────────────
    def zone_min_annual_rule(m, z, p):
        if m.gen_zone_min_annual_ratio[z, p] == 0.0:
            return Constraint.Skip
        return (
            m.ZoneAnnualGenMWh[z, p]
            >= m.gen_zone_min_annual_ratio[z, p] * m.ZoneAnnualLoadMWh[z, p]
        )

    m.Zone_Min_Annual_Gen_Ratio = Constraint(
        m.LOAD_ZONES, m.PERIODS,
        rule=zone_min_annual_rule,
        doc="Annual in-zone gen must be at least min_annual_ratio × annual load.",
    )

    def zone_max_annual_rule(m, z, p):
        if m.gen_zone_max_annual_ratio[z, p] == float("inf"):
            return Constraint.Skip
        return (
            m.ZoneAnnualGenMWh[z, p]
            <= m.gen_zone_max_annual_ratio[z, p] * m.ZoneAnnualLoadMWh[z, p]
        )

    m.Zone_Max_Annual_Gen_Ratio = Constraint(
        m.LOAD_ZONES, m.PERIODS,
        rule=zone_max_annual_rule,
        doc="Annual in-zone gen must be at most max_annual_ratio × annual load.",
    )

    # ── Hourly (peak share) constraints ──────────────────────────────────────
    def zone_min_peak_rule(m, z, t):
        if m.gen_zone_min_peak_share[z, m.tp_period[t]] == 0.0:
            return Constraint.Skip
        return (
            m.ZoneTimeGenMW[z, t]
            >= m.gen_zone_min_peak_share[z, m.tp_period[t]] * m.zone_demand_mw[z, t]
        )

    m.Zone_Min_Peak_Share = Constraint(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=zone_min_peak_rule,
        doc="In-zone gen must cover at least min_peak_share of load at every timepoint.",
    )

    def zone_max_peak_rule(m, z, t):
        if m.gen_zone_max_peak_share[z, m.tp_period[t]] == float("inf"):
            return Constraint.Skip
        return (
            m.ZoneTimeGenMW[z, t]
            <= m.gen_zone_max_peak_share[z, m.tp_period[t]] * m.zone_demand_mw[z, t]
        )

    m.Zone_Max_Peak_Share = Constraint(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=zone_max_peak_rule,
        doc="In-zone gen must not exceed max_peak_share of load at any timepoint.",
    )

    # ══════════════════════════════════════════════════════════════════════════
    # B) GROUP-LEVEL CONSTRAINTS
    # ══════════════════════════════════════════════════════════════════════════

    # Set of (group, zone) pairs loaded from gen_zone_groups.csv
    m.GEN_RATIO_GROUP_ZONES = Set(
        dimen=2,
        within=Any * m.LOAD_ZONES,
        doc="(GROUP_NAME, LOAD_ZONE) pairs defining zone groups for group constraints.",
    )
    # Group names derived from GEN_RATIO_GROUP_ZONES
    m.GEN_RATIO_GROUPS = Set(
        dimen=1,
        within=Any,
        initialize=lambda m: list({g for (g, _z) in m.GEN_RATIO_GROUP_ZONES}),
        doc="Set of all zone group names.",
    )
    # Zones belonging to each group
    m.ZONES_IN_GEN_RATIO_GROUP = Set(
        m.GEN_RATIO_GROUPS,
        within=m.LOAD_ZONES,
        initialize=lambda m, g: [
            z for (gg, z) in m.GEN_RATIO_GROUP_ZONES if gg == g
        ],
        doc="Model zones belonging to each constraint group.",
    )

    # ── Group params ─────────────────────────────────────────────────────────
    m.group_min_annual_ratio = Param(
        m.GEN_RATIO_GROUPS, m.PERIODS,
        within=NonNegativeReals,
        default=0.0,
        doc="Minimum annual gen/load ratio for the group. 0 = unconstrained.",
    )
    m.group_max_annual_ratio = Param(
        m.GEN_RATIO_GROUPS, m.PERIODS,
        within=NonNegativeReals,
        default=float("inf"),
        doc="Maximum annual gen/load ratio for the group. inf = unconstrained.",
    )
    m.group_min_peak_share = Param(
        m.GEN_RATIO_GROUPS, m.PERIODS,
        within=NonNegativeReals,
        default=0.0,
        doc="Min group gen share of contemporaneous load at every timepoint.",
    )
    m.group_max_peak_share = Param(
        m.GEN_RATIO_GROUPS, m.PERIODS,
        within=NonNegativeReals,
        default=float("inf"),
        doc="Max group gen share of contemporaneous load at every timepoint.",
    )

    # ── Group expressions ─────────────────────────────────────────────────────
    m.GroupAnnualGenMWh = Expression(
        m.GEN_RATIO_GROUPS, m.PERIODS,
        rule=lambda m, g, p: sum(
            m.ZoneAnnualGenMWh[z, p] for z in m.ZONES_IN_GEN_RATIO_GROUP[g]
        ),
        doc="Annual generation (MWh/yr) summed across all zones in a group.",
    )
    m.GroupAnnualLoadMWh = Expression(
        m.GEN_RATIO_GROUPS, m.PERIODS,
        rule=lambda m, g, p: sum(
            m.ZoneAnnualLoadMWh[z, p] for z in m.ZONES_IN_GEN_RATIO_GROUP[g]
        ),
        doc="Annual load (MWh/yr) summed across all zones in a group.",
    )
    m.GroupTimeGenMW = Expression(
        m.GEN_RATIO_GROUPS, m.TIMEPOINTS,
        rule=lambda m, g, t: sum(
            m.ZoneTimeGenMW[z, t] for z in m.ZONES_IN_GEN_RATIO_GROUP[g]
        ),
        doc="Total generation (MW) summed across all zones in a group at each timepoint.",
    )
    m.GroupTimeLoadMW = Expression(
        m.GEN_RATIO_GROUPS, m.TIMEPOINTS,
        rule=lambda m, g, t: sum(
            m.zone_demand_mw[z, t] for z in m.ZONES_IN_GEN_RATIO_GROUP[g]
        ),
        doc="Total load (MW) summed across all zones in a group at each timepoint.",
    )

    # ── Group annual ratio constraints ────────────────────────────────────────
    def group_min_annual_rule(m, g, p):
        if m.group_min_annual_ratio[g, p] == 0.0:
            return Constraint.Skip
        return (
            m.GroupAnnualGenMWh[g, p]
            >= m.group_min_annual_ratio[g, p] * m.GroupAnnualLoadMWh[g, p]
        )

    m.Group_Min_Annual_Gen_Ratio = Constraint(
        m.GEN_RATIO_GROUPS, m.PERIODS,
        rule=group_min_annual_rule,
        doc="Group annual gen must be at least min_annual_ratio × group annual load.",
    )

    def group_max_annual_rule(m, g, p):
        if m.group_max_annual_ratio[g, p] == float("inf"):
            return Constraint.Skip
        return (
            m.GroupAnnualGenMWh[g, p]
            <= m.group_max_annual_ratio[g, p] * m.GroupAnnualLoadMWh[g, p]
        )

    m.Group_Max_Annual_Gen_Ratio = Constraint(
        m.GEN_RATIO_GROUPS, m.PERIODS,
        rule=group_max_annual_rule,
        doc="Group annual gen must be at most max_annual_ratio × group annual load.",
    )

    # ── Group hourly (peak share) constraints ─────────────────────────────────
    def group_min_peak_rule(m, g, t):
        if m.group_min_peak_share[g, m.tp_period[t]] == 0.0:
            return Constraint.Skip
        return (
            m.GroupTimeGenMW[g, t]
            >= m.group_min_peak_share[g, m.tp_period[t]] * m.GroupTimeLoadMW[g, t]
        )

    m.Group_Min_Peak_Share = Constraint(
        m.GEN_RATIO_GROUPS, m.TIMEPOINTS,
        rule=group_min_peak_rule,
        doc="Group gen must cover at least min_peak_share of group load at every timepoint.",
    )

    def group_max_peak_rule(m, g, t):
        if m.group_max_peak_share[g, m.tp_period[t]] == float("inf"):
            return Constraint.Skip
        return (
            m.GroupTimeGenMW[g, t]
            <= m.group_max_peak_share[g, m.tp_period[t]] * m.GroupTimeLoadMW[g, t]
        )

    m.Group_Max_Peak_Share = Constraint(
        m.GEN_RATIO_GROUPS, m.TIMEPOINTS,
        rule=group_max_peak_rule,
        doc="Group gen must not exceed max_peak_share of group load at any timepoint.",
    )


def _load_constraint_csv(switch_data, path, param_map):
    """
    Read a constraint CSV (with possible blank cells) and populate switch_data
    for each constraint param listed in param_map.

    param_map: list of (csv_column_name, param_name) pairs
    """
    if not os.path.isfile(path):
        return
    df = pd.read_csv(path)
    if df.empty:
        return

    if None not in switch_data._data:
        switch_data._data[None] = {}

    # First two columns are always the index (LOAD_ZONE/GROUP_NAME, PERIOD)
    index_cols = df.columns[:2].tolist()

    for csv_col, param_name in param_map:
        if csv_col not in df.columns:
            continue
        sub = df[index_cols + [csv_col]].copy()
        sub = sub.dropna(subset=[csv_col])
        sub = sub[sub[csv_col].astype(str).str.strip() != ""]
        if sub.empty:
            continue
        switch_data._data[None][param_name] = {
            (row[0], int(row[1])): float(row[2])
            for row in sub.itertuples(index=False)
        }


def load_inputs(m, switch_data, inputs_dir):
    """
    Load zone-level constraints from gen_zone_load_ratio.csv,
    zone group definitions from gen_zone_groups.csv, and
    group-level constraints from gen_group_load_ratio.csv.
    All files are optional.
    """
    zone_constraint_params = [
        ("min_annual_ratio", "gen_zone_min_annual_ratio"),
        ("max_annual_ratio", "gen_zone_max_annual_ratio"),
        ("min_peak_share",   "gen_zone_min_peak_share"),
        ("max_peak_share",   "gen_zone_max_peak_share"),
    ]
    group_constraint_params = [
        ("min_annual_ratio", "group_min_annual_ratio"),
        ("max_annual_ratio", "group_max_annual_ratio"),
        ("min_peak_share",   "group_min_peak_share"),
        ("max_peak_share",   "group_max_peak_share"),
    ]

    # Zone-level constraints
    _load_constraint_csv(
        switch_data,
        apply_input_aliases(switch_data, os.path.join(inputs_dir, "gen_zone_load_ratio.csv")),
        zone_constraint_params,
    )

    # Zone group membership (GROUP_NAME, LOAD_ZONE pairs → GEN_RATIO_GROUP_ZONES)
    groups_path = apply_input_aliases(switch_data, os.path.join(inputs_dir, "gen_zone_groups.csv"))
    if os.path.isfile(groups_path):
        gdf = pd.read_csv(groups_path)
        if not gdf.empty:
            # Expect columns: GROUP_NAME, LOAD_ZONE
            pairs = list(zip(gdf.iloc[:, 0], gdf.iloc[:, 1]))
            if None not in switch_data._data:
                switch_data._data[None] = {}
            switch_data._data[None]["GEN_RATIO_GROUP_ZONES"] = {
                None: set(pairs)
            }

    # Group-level constraints
    _load_constraint_csv(
        switch_data,
        apply_input_aliases(switch_data, os.path.join(inputs_dir, "gen_group_load_ratio.csv")),
        group_constraint_params,
    )


def post_solve(m, outputs_dir):
    """
    Write gen_zone_ratio_summary.csv with actual and constrained values
    for both individual zones and zone groups.
    """
    rows = []

    # ── Zone-level rows ───────────────────────────────────────────────────────
    for z in m.LOAD_ZONES:
        for p in m.PERIODS:
            gen_mwh = value(m.ZoneAnnualGenMWh[z, p])
            load_mwh = value(m.ZoneAnnualLoadMWh[z, p])
            ratio = gen_mwh / load_mwh if load_mwh else None

            min_ar = value(m.gen_zone_min_annual_ratio[z, p])
            max_ar = value(m.gen_zone_max_annual_ratio[z, p])
            min_ps = value(m.gen_zone_min_peak_share[z, p])
            max_ps = value(m.gen_zone_max_peak_share[z, p])

            shares = [
                value(m.ZoneTimeGenMW[z, t]) / value(m.zone_demand_mw[z, t])
                if value(m.zone_demand_mw[z, t]) else None
                for t in m.TPS_IN_PERIOD[p]
            ]
            shares_valid = [s for s in shares if s is not None]

            rows.append({
                "TYPE": "zone",
                "LOAD_ZONE": z,
                "PERIOD": p,
                "actual_annual_gen_twh":   round(gen_mwh / 1e6, 4) if gen_mwh is not None else "",
                "actual_annual_load_twh":  round(load_mwh / 1e6, 4) if load_mwh is not None else "",
                "actual_annual_ratio":     round(ratio, 4) if ratio is not None else "",
                "min_annual_ratio":        "" if min_ar == 0.0 else round(min_ar, 4),
                "max_annual_ratio":        "" if max_ar == float("inf") else round(max_ar, 4),
                "actual_min_hourly_gen_share": round(min(shares_valid), 4) if shares_valid else "",
                "actual_max_hourly_gen_share": round(max(shares_valid), 4) if shares_valid else "",
                "min_peak_share": "" if min_ps == 0.0 else round(min_ps, 4),
                "max_peak_share": "" if max_ps == float("inf") else round(max_ps, 4),
            })

    # ── Group-level rows ──────────────────────────────────────────────────────
    for g in m.GEN_RATIO_GROUPS:
        for p in m.PERIODS:
            gen_mwh = value(m.GroupAnnualGenMWh[g, p])
            load_mwh = value(m.GroupAnnualLoadMWh[g, p])
            ratio = gen_mwh / load_mwh if load_mwh else None

            min_ar = value(m.group_min_annual_ratio[g, p])
            max_ar = value(m.group_max_annual_ratio[g, p])
            min_ps = value(m.group_min_peak_share[g, p])
            max_ps = value(m.group_max_peak_share[g, p])

            shares = [
                value(m.GroupTimeGenMW[g, t]) / value(m.GroupTimeLoadMW[g, t])
                if value(m.GroupTimeLoadMW[g, t]) else None
                for t in m.TPS_IN_PERIOD[p]
            ]
            shares_valid = [s for s in shares if s is not None]

            rows.append({
                "TYPE": "group",
                "LOAD_ZONE": g,
                "PERIOD": p,
                "actual_annual_gen_twh":   round(gen_mwh / 1e6, 4) if gen_mwh is not None else "",
                "actual_annual_load_twh":  round(load_mwh / 1e6, 4) if load_mwh is not None else "",
                "actual_annual_ratio":     round(ratio, 4) if ratio is not None else "",
                "min_annual_ratio":        "" if min_ar == 0.0 else round(min_ar, 4),
                "max_annual_ratio":        "" if max_ar == float("inf") else round(max_ar, 4),
                "actual_min_hourly_gen_share": round(min(shares_valid), 4) if shares_valid else "",
                "actual_max_hourly_gen_share": round(max(shares_valid), 4) if shares_valid else "",
                "min_peak_share": "" if min_ps == 0.0 else round(min_ps, 4),
                "max_peak_share": "" if max_ps == float("inf") else round(max_ps, 4),
            })

    out = pd.DataFrame(rows, columns=[
        "TYPE", "LOAD_ZONE", "PERIOD",
        "actual_annual_gen_twh", "actual_annual_load_twh", "actual_annual_ratio",
        "min_annual_ratio", "max_annual_ratio",
        "actual_min_hourly_gen_share", "actual_max_hourly_gen_share",
        "min_peak_share", "max_peak_share",
    ])
    out_path = os.path.join(outputs_dir, "gen_zone_ratio_summary.csv")
    out.to_csv(out_path, index=False)
    print(f"gen_zone_ratio: wrote {out_path} ({len(out)} rows: "
          f"{(out.TYPE=='zone').sum()} zones, {(out.TYPE=='group').sum()} groups)")
