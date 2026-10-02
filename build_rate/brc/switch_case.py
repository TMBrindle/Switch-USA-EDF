"""Write build-rate inputs into a Switch-USA-EDF case folder (called from pg_to_switch.py).

Reads the case's own periods.csv, gen_info.csv, gen_build_costs.csv, gen_build_predetermined.csv,
min_cap_*.csv and max_cap_requirements.csv, plus the pipeline tables
(<tables_dir>/rates_<level>.csv, tiers.csv). Only pandas and a YAML reader are needed.

Settings (pg/settings/build_rate.yml):

    build_rate:
      enabled: false
      level: central                 # low | central | high | reform | high_ipm
      tables_dir: build_rate/outputs
      groups: [wind_onshore, solar, storage]   # add gas only with MaxCapTag_GasTurbineSupply released
      regional: true
      regional_groups: [wind_onshore, solar]   # groups with transreg ceilings; others national-only
      ceiling_slack_cost: null       # $/kW; diagnostic slack above the ceiling (off when null)
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .groups import switch_group

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_CONFIG = REPO_ROOT / "build_rate" / "config.yaml"
GAS_CAP_TAG = "MaxCapTag_GasTurbineSupply"
# Regional (transreg) ceilings by default only for groups limited by local siting. Storage and gas are
# limited by supply chain and stay national-only: 2021-25 storage build was concentrated in CAISO,
# ERCOT and WestConnect, so share-based regional ceilings held storage far below its national pace.
DEFAULT_REGIONAL_GROUPS = ("wind_onshore", "solar")


def br_settings(settings: dict) -> dict | None:
    s = settings.get("build_rate") or {}
    return s if s.get("enabled") else None


def _read(folder: Path, name: str) -> pd.DataFrame | None:
    p = Path(folder) / name
    if not p.exists():
        return None
    df = pd.read_csv(p, na_values=["."])
    # Switch reads the index columns by position, so PowerGenome's BUILD_YEAR (written as 2016.0) is as
    # valid as build_year; normalise it here
    if "BUILD_YEAR" in df and "build_year" not in df:
        df = df.rename(columns={"BUILD_YEAR": "build_year"})
    if "build_year" in df:
        df["build_year"] = df["build_year"].astype(int)
    return df


def _window_sum(table: pd.Series, start: int, end: int) -> float:
    """Sum of an annual series over the build window [start, end] (years outside the table take the
    nearest year). Cumulative limits are always this sum, never the period-year value x W."""
    yrs = np.clip(np.arange(start, end + 1), table.index.min(), table.index.max())
    return float(table.reindex(yrs).sum())


def _window_mean(table: pd.Series, start: int, end: int) -> float:
    """Window sum / W: the per-year rate the module multiplies back by W (so width x rate x W =
    width x sum over the window of R[y])."""
    return _window_sum(table, start, end) / (end - start + 1)


def check_gas_cap(groups, max_cap_req: pd.DataFrame | None):
    if "gas" in groups and max_cap_req is not None and len(max_cap_req) and \
            (max_cap_req["MAX_CAP_PROGRAM"] == GAS_CAP_TAG).any():
        raise ValueError(
            f"build_rate group 'gas' is on but {GAS_CAP_TAG} is still active in max_cap_requirements.csv. "
            f"Release {GAS_CAP_TAG} (remove its MaxCapReq entries) or drop 'gas' from build_rate.groups; "
            "both would limit new gas turbines.")


def check_min_cap(gens: pd.DataFrame, periods: pd.DataFrame, ceilings: pd.DataFrame,
                  min_req: pd.DataFrame | None, min_gens: pd.DataFrame | None,
                  predet: pd.DataFrame | None, slack: bool) -> list[str]:
    """MinCap programs whose generators all belong to one build-rate group: the new build they need by
    each period must fit under the cumulative national ceilings. Raises unless slack is on."""
    problems = []
    if min_req is None or min_gens is None or not len(min_req):
        return problems
    grp = gens.set_index("GENERATION_PROJECT")["br_gen_group"]
    for prog, pg in min_gens.groupby("MIN_CAP_PROGRAM"):
        g = pg["MIN_CAP_GEN"].map(grp)
        if g.isna().any() or g.nunique() != 1:
            continue
        group = g.iat[0]
        for _, r in min_req[min_req["MIN_CAP_PROGRAM"] == prog].iterrows():
            p = r["PERIOD"]
            have = 0.0
            if predet is not None:
                d = predet[predet["GENERATION_PROJECT"].isin(pg["MIN_CAP_GEN"]) & (predet["build_year"] <= p)]
                have = float(d["build_gen_predetermined"].sum())
            need = float(r["min_cap_mw"]) - have
            allowed = float(ceilings[(ceilings["group"] == group) & (ceilings["period"] <= p)]["ceiling_mw"].sum())
            if need > allowed + 1e-6:
                problems.append(f"{prog} needs {need:,.0f} MW of new {group} by {p} but the build-rate "
                                f"ceilings allow {allowed:,.0f} MW")
    if problems and not slack:
        raise ValueError("MinCap targets exceed the build-rate ceiling (set build_rate.ceiling_slack_cost to "
                         "diagnose, or relax the level): " + "; ".join(problems))
    for msg in problems:
        logger.warning("build_rate: %s (slack on)", msg)
    return problems


def check_rps(out_folder: Path, zones: pd.DataFrame, rates: pd.DataFrame, groups, periods: pd.DataFrame,
              cfg: dict) -> list[str]:
    """Warn (never raise) when an RPS program with no ACP has a high share in transregs whose wind or
    solar regional ceilings are set by the floor. Returns the warning messages."""
    req = _read(out_folder, "rps_requirements.csv")
    if req is None or not len(req) or not len(zones):
        return []
    rc = cfg.get("rps_check") or {}
    warn_share, cf = float(rc.get("warn_share", 0.3)), rc.get("cf") or {}
    zr = zones.set_index("LOAD_ZONE")["br_zone_region"]
    acp = pd.to_numeric(req["rps_acp_per_mwh"], errors="coerce") if "rps_acp_per_mwh" in req else pd.Series(np.nan, index=req.index)
    req = req.assign(_acp=acp.fillna(-1) >= 0, _tr=req["LOAD_ZONE"].map(zr))
    loads = _read(out_folder, "loads.csv")
    msgs = []
    for (prog, p), d in req.groupby(["RPS_PROGRAM", "PERIOD"]):
        share = float(d["rps_share"].max())
        if d["_acp"].all() or share < warn_share:
            continue
        pr = periods.set_index("INVESTMENT_PERIOD").loc[p]
        s, e = int(pr["period_start"]), int(pr["period_end"])
        trs = sorted(t for t in d["_tr"].dropna().unique())
        win = rates[rates["region"].isin(trs) & rates["year"].between(s, e) & rates["group"].isin(
            [g for g in ("wind_onshore", "solar") if g in groups])]
        floored = win[win["floor_applied"]]
        if not len(floored):
            continue
        cum = win.groupby("group")["ceiling_mw_per_yr"].sum()      # window sum, MW
        twh = sum(cum.get(g, 0.0) * cf.get(g, 0.0) * 8.76e-3 for g in cum.index) / (e - s + 1)
        need = ""
        if loads is not None:
            z = loads[loads["LOAD_ZONE"].isin(d["LOAD_ZONE"])]
            if len(z):
                load_twh = z.groupby("LOAD_ZONE")["zone_demand_mw"].mean().sum() * 8.76e-3
                need = f"; target {share:.0%} of ~{load_twh:,.0f} TWh/yr = ~{share * load_twh:,.0f} TWh/yr"
        msgs.append(f"RPS {prog} {p}: share {share:.0%}, no ACP; {', '.join(sorted(floored['group'].unique()))} "
                    f"ceilings in {', '.join(trs)} are set by the regional floor (little recent build). New "
                    f"wind+solar allowed there over {s}-{e} averages ~{twh:,.0f} TWh/yr at rough CFs{need} "
                    "(existing eligible generation not counted). Consider the RPS ACP (rps_acp_per_mwh).")
    for m_ in msgs:
        logger.warning("build_rate: %s", m_)
    return msgs


def write_case_inputs(out_folder: Path, settings: dict, pipeline_cfg: dict | None = None) -> list[str]:
    """Write build_rate_*.csv into out_folder. Returns the files written ([] when disabled)."""
    br = br_settings(settings)
    if br is None:
        return []
    out_folder = Path(out_folder)
    cfg = pipeline_cfg or yaml.safe_load(open(PIPELINE_CONFIG))
    level = br.get("level", "central")
    groups = list(br.get("groups") or ["wind_onshore", "solar", "storage"])
    tdir = REPO_ROOT / br.get("tables_dir", "build_rate/outputs")
    rates_path = tdir / f"rates_{level}.csv"
    if not rates_path.exists():
        raise FileNotFoundError(f"build_rate is enabled but {rates_path} is missing. Run `python -m brc.cli run` "
                                "in build_rate/ (level high_ipm also needs ipm.run_year_span), or set enabled: false.")
    rates = pd.read_csv(rates_path)
    tiers_path = tdir / f"tiers_{level}.csv"
    tiers = pd.read_csv(tiers_path if tiers_path.exists() else tdir / "tiers.csv")
    check_gas_cap(groups, _read(out_folder, "max_cap_requirements.csv"))

    periods = _read(out_folder, "periods.csv")
    gi = _read(out_folder, "gen_info.csv")
    costs = _read(out_folder, "gen_build_costs.csv")
    predet = _read(out_folder, "gen_build_predetermined.csv")
    dist = gi["gen_is_distributed"] if "gen_is_distributed" in gi else pd.Series(0, index=gi.index)
    gi = gi.assign(br_gen_group=[switch_group(t, e, d) for t, e, d in
                                 zip(gi["gen_tech"], gi["gen_energy_source"], dist)])
    gens = gi[gi["br_gen_group"].isin(groups)][["GENERATION_PROJECT", "br_gen_group", "gen_load_zone"]]

    nat = rates[rates["region"] == "national"]
    rows_p, rows_t, ceil = [], [], []
    for g in groups:
        rd = nat[nat["group"] == g].set_index("year")["r_data_mw_per_yr"]
        tg = tiers[tiers["group"] == g]
        top = float(tg["width"].sum())
        gproj = gens.loc[gens["br_gen_group"] == g, "GENERATION_PROJECT"]
        for _, pr in periods.iterrows():
            p, s, e = int(pr["INVESTMENT_PERIOD"]), int(pr["period_start"]), int(pr["period_end"])
            w = e - s + 1
            r = _window_mean(rd, s, e)        # = sum of R[y] over the window / W
            committed = 0.0
            if predet is not None:
                d = predet[predet["GENERATION_PROJECT"].isin(gproj)
                           & (predet["build_year"].between(s, e) | (predet["build_year"] == p))]
                committed = float(d["build_gen_predetermined"].sum())
            if committed > top * r * w + 1e-6:
                logger.warning("build_rate: committed %s build in period %s (%.0f MW) exceeds the ceiling; "
                               "raising the rate to %.0f MW/yr", g, p, committed, committed / (top * w))
                r = committed / (top * w)
            rows_p.append({"BR_GROUP": g, "PERIOD": p, "br_rate_data_mw": round(r, 3)})
            ceil.append({"group": g, "period": p, "ceiling_mw": top * r * w})
            c = costs[costs["GENERATION_PROJECT"].isin(gproj)] if costs is not None else None
            capex = np.nan
            if c is not None and len(c):
                at_p = c[c["build_year"] == p]["gen_overnight_cost"]
                capex = float((at_p if len(at_p) else c["gen_overnight_cost"]).median())
            for _, t in tg.iterrows():
                # adders that stop after a calendar year (IPM: none after 2035) apply to the share
                # of the period's build window up to that year
                last = t.get("adders_last_year", np.nan)
                frac = 1.0 if pd.isna(last) else min(max(int(last) - s + 1, 0), w) / w
                rows_t.append({"BR_GROUP": g, "PERIOD": p, "BR_TIER": t["tier"], "br_tier_width": t["width"],
                               "br_tier_adder_per_mw": round(float(t["adder"]) * frac * (0 if np.isnan(capex) else capex), 2)})
    slack_kw = br.get("ceiling_slack_cost")
    grp_rows = [{"BR_GROUP": g, "br_growth": float(nat[nat["group"] == g]["growth"].iat[0]),
                 "br_ramp_floor_mw": cfg["ramp_floor_mw"][g], "br_life_years": cfg["life_years"][g],
                 "br_ceiling_slack_cost_per_mw": float(slack_kw) * 1000 if slack_kw is not None else -1}
                for g in groups]
    gens_out = gens[["GENERATION_PROJECT", "br_gen_group"]]
    check_min_cap(gens_out, periods, pd.DataFrame(ceil), _read(out_folder, "min_cap_requirements.csv"),
                  _read(out_folder, "min_cap_generators.csv"), predet, slack_kw is not None)

    files = {"build_rate_groups.csv": pd.DataFrame(grp_rows), "build_rate_gens.csv": gens_out,
             "build_rate_periods.csv": pd.DataFrame(rows_p), "build_rate_tiers.csv": pd.DataFrame(rows_t)}
    reg_groups = [g for g in groups if g in (br.get("regional_groups") or DEFAULT_REGIONAL_GROUPS)]
    if br.get("regional", True) and reg_groups:
        zones, regions = regional_frames(gens, periods, rates, reg_groups, settings, predet)
        if len(zones):
            files["build_rate_zones.csv"] = zones
            files["build_rate_regions.csv"] = regions
            check_rps(out_folder, zones, rates[rates["region"] != "national"], reg_groups, periods, cfg)
    for name, df in files.items():
        df.to_csv(out_folder / name, index=False)
    logger.info("build_rate: level %s, groups %s -> %s", level, groups, ", ".join(files))
    return list(files)


def regional_frames(gens, periods, rates, groups, settings, predet):
    """Load zone -> transreg (zones that are ReEDS BAs, or aggregates inside one transreg) and the
    regional ceilings averaged over each period's window, raised to cover committed builds."""
    h = pd.read_csv(REPO_ROOT / "hierarchy.csv")[["ba", "transreg"]].set_index("ba")["transreg"]
    zone_map = settings.get("_zone_map") or {}
    members = {}
    for ba, z in zone_map.items():
        members.setdefault(z, set()).add(ba)
    rows = []
    for z in sorted(gens["gen_load_zone"].unique()):
        bas = members.get(z, {z})
        trs = {h.get(b) for b in bas}
        if len(trs) == 1 and None not in trs:
            rows.append({"LOAD_ZONE": z, "br_zone_region": trs.pop()})
    zones = pd.DataFrame(rows, columns=["LOAD_ZONE", "br_zone_region"])
    zr = zones.set_index("LOAD_ZONE")["br_zone_region"]
    out = []
    for g in groups:
        rg = rates[(rates["group"] == g) & (rates["region"] != "national")]
        for reg, tab in rg.groupby("region"):
            ser = tab.set_index("year")["ceiling_mw_per_yr"]
            proj = gens[(gens["br_gen_group"] == g) & gens["gen_load_zone"].map(zr).eq(reg)]["GENERATION_PROJECT"]
            if not len(proj):
                continue
            for _, pr in periods.iterrows():
                p, s, e = int(pr["INVESTMENT_PERIOD"]), int(pr["period_start"]), int(pr["period_end"])
                w = e - s + 1
                cap = _window_mean(ser, s, e)  # sum over the window of max(share x m x top x R[y], floor) / W
                if predet is not None:
                    d = predet[predet["GENERATION_PROJECT"].isin(proj)
                               & (predet["build_year"].between(s, e) | (predet["build_year"] == p))]
                    cap = max(cap, float(d["build_gen_predetermined"].sum()) / w)
                out.append({"BR_GROUP": g, "BR_REGION": reg, "PERIOD": p, "br_region_max_mw_per_yr": round(cap, 3)})
    return zones, pd.DataFrame(out, columns=["BR_GROUP", "BR_REGION", "PERIOD", "br_region_max_mw_per_yr"])
