"""Turn the fitted cost model into per-zone curves in saturation units, plus network uprate options.

Outputs per scenario (see cli.cmd_run):
  zones_<scenario>.csv     ba, base_capacity_mw (H0), start_saturation (s0), release_cost_per_kw
  tranches_<scenario>.csv  ba, tranche, width (saturation units), cost_per_kw, sat_from, sat_to,
                           extrapolated, available_year
  uprates_<scenario>.csv   ba, uprate, type, max_mw (network MW), cost_per_kw (per kW of network
                           capacity), available_year

In Switch, step k provides width x H MW of generation headroom, where H = H0 + uprates built, so
uprates stretch the curve; and up to s0 x (uprate MW) of headroom is released at release_cost.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .estimate import CostModel


def zone_regime_labels(hierarchy: pd.DataFrame, cfg: dict) -> pd.Series:
    """Regime label per zone: a utility (regimes.hurdlereg_to_regime, by the zone's hurdlereg) where
    one is listed, else the zone's transreg (through regimes.transreg_to_regime)."""
    rc = cfg.get("regimes", {})
    by_util = rc.get("hurdlereg_to_regime") or {}
    by_reg = rc.get("transreg_to_regime") or {}
    h = hierarchy.set_index("ba")
    lab = h["transreg"].map(lambda t: by_reg.get(t, t))
    util = h["hurdlereg"].map(by_util)
    return util.fillna(lab).rename("regime")


def priced_status_effect(model: CostModel, regime: str, status: str, cfg: dict) -> float:
    """Status effect (vs active) used to price a zone, per tranches.status_pricing:
    own (as estimated), capped (within status_cap_ratio x the pooled effect, multiplicatively) or pooled."""
    tc = cfg["tranches"]
    how = tc.get("status_pricing", "own")
    own = model.status_effect(regime, status)
    pooled = model.pooled_status_effect(status)
    if how == "own":
        return own
    if how == "pooled":
        return pooled
    if how == "capped":
        lo, hi = tc.get("status_cap_ratio", [0.5, 2.0])
        return float(np.clip(own, pooled + np.log(lo), pooled + np.log(hi)))
    raise ValueError(f"tranches.status_pricing must be own, capped or pooled, not {how!r}")


def trend_year(model: CostModel, regime: str, cfg: dict) -> int:
    """Queue year the trend is evaluated at for a zone (tranches.trend_freeze)."""
    tc = cfg["tranches"]
    ref = int(tc.get("reference_year", cfg["dollar_year"]))
    freeze = tc.get("trend_freeze", "none")
    if freeze == "none":
        return ref
    if freeze == "per_regime":
        last = model.last_queue_year or {}
        return min(ref, int(last.get(regime, last.get("_all", ref))))
    raise ValueError(f"tranches.trend_freeze must be per_regime or none, not {freeze!r}")


def zone_regimes(hierarchy: pd.DataFrame, model: CostModel, cfg: dict) -> pd.DataFrame:
    """Assign each zone a planning regime, its effect at the reference status (the regime fixed effect
    plus the priced status effect vs active, see priced_status_effect) and the queue year its trend
    is evaluated at (trend_year). Zones whose regime is not in the sample get the mean regime effect,
    the pooled status effect and the sample's last queue year."""
    z = hierarchy[["ba", "transreg", "hurdlereg"]].copy()
    z["regime"] = zone_regime_labels(hierarchy, cfg).reindex(z["ba"]).values
    eff = model.regime_effects()
    z["regime_in_sample"] = z["regime"].isin(eff.index)
    st = cfg["tranches"].get("reference_status", "completed")
    z["status_effect_estimated"] = [model.status_effect(r if r in eff.index else "", st) for r in z["regime"]]
    z["status_effect"] = [priced_status_effect(model, r if r in eff.index else "", st, cfg) for r in z["regime"]]
    z["regime_effect"] = [eff.get(r, eff.mean()) for r in z["regime"]] + z["status_effect"]
    z["trend_year"] = [trend_year(model, r if r in eff.index else "_all", cfg) for r in z["regime"]]
    return z.set_index("ba")


def _predict_with_effect(model: CostModel, sat, effect: float, cfg: dict, year: int | None = None) -> np.ndarray:
    """Predict at the reference project with an explicit total effect (base-regime, active design +
    offset; the offset carries the regime and its status effect, see zone_regimes), with the trend
    evaluated at `year` (default tranches.reference_year)."""
    tc = cfg["tranches"]
    yr = tc.get("reference_year", cfg["dollar_year"]) if year is None else year
    yhat = model.log_pred(sat, tc["reference_tech"], tc["reference_service"], tc["reference_capacity_mw"],
                          yr, model.base_regime, "active") + effect
    return model.to_cost(yhat)


def build_reference(model: CostModel, panel: pd.DataFrame, regimes: pd.DataFrame, cfg: dict,
                    start_year: int, regime_override: str | None = None
                    ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (zones, tranches) for every zone, starting from its saturation in start_year.

    The empirical curve runs from the zone's start saturation s0 to the sample's support edge
    (model.sat_support, the support_quantile of sample saturation) in equal steps of about
    tranches.step_width. A zone already at or beyond the edge gets one step of edge_step_width
    priced at the edge. Beyond that, headroom comes only from uprates (new_line is the backstop in
    every scenario). Costs are made non-decreasing (cumulative max) so the LP fills them in order.
    """
    tc = cfg["tranches"]
    edge = float(model.sat_support)
    cur = panel[panel["year"] == min(start_year, panel["year"].max())].set_index("ba")
    best_effect = float(regimes["regime_effect"][regimes["regime_in_sample"]].min())
    zrows, trows = [], []
    for ba, r in cur.iterrows():
        if ba not in regimes.index:
            continue
        effect = best_effect if regime_override == "best" else regimes.at[ba, "regime_effect"]
        yr = int(regimes.at[ba, "trend_year"]) if "trend_year" in regimes else None   # zone's own data coverage
        s0 = float(r["saturation"])
        if s0 < edge:
            n = max(1, int(np.ceil((edge - s0) / tc["step_width"])))
            edges = np.linspace(s0, edge, n + 1)
            mids = (edges[:-1] + edges[1:]) / 2
        else:   # already past the data: one short step at the edge price
            edges = np.array([s0, s0 + tc["edge_step_width"]])
            mids = np.array([edge])
        steps = np.diff(edges)
        cost = np.maximum.accumulate(_predict_with_effect(model, mids, effect, cfg, yr))
        cost = np.minimum(cost, tc["max_cost_per_kw"])
        # released headroom sits just below s0: price it at the marginal cost at s0
        release = float(min(_predict_with_effect(model, np.array([s0]), effect, cfg, yr)[0], cost[0]))
        zrows.append({"ba": ba, "base_capacity_mw": float(r["headroom_proxy_floored_mw"]),
                      "start_saturation": s0, "release_cost_per_kw": release,
                      "regime": regimes.at[ba, "regime"], "trend_year": yr})
        for i, (st, c) in enumerate(zip(steps, cost)):
            trows.append({"ba": ba, "tranche": f"nu{i + 1}", "width": float(st),
                          "cost_per_kw": float(c), "sat_from": edges[i], "sat_to": edges[i + 1],
                          "extrapolated": bool(mids[i] > model.sat_support + 1e-12),
                          "beyond_support": bool(s0 >= edge), "available_year": 0})
    return pd.DataFrame(zrows), pd.DataFrame(trows)


def apply_scenario(zones: pd.DataFrame, tranches: pd.DataFrame, cfg: dict, scen: dict,
                   best: tuple[pd.DataFrame, pd.DataFrame] | None = None
                   ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Apply one scenario from config.yaml. Returns (zones, tranches, uprates).

    Levers:
      regime_override: best     height: every zone gets the lowest regime effect (at the reference status)
      cost_multiplier: x        height: scale every step's cost (and the release cost)
      slope_multiplier: m       steepness: c_k -> c_1 * (c_k / c_1) ** m within each zone
      uprates: [names]          network capacity options from `uprate_options`, which Switch may build
                                (plus `backstop_uprates`, available in every scenario)
    """
    if scen.get("regime_override") == "best" and best is not None:
        zones, tranches = best
    z, t = zones.copy(), tranches.copy()
    if "slope_multiplier" in scen:
        # scale the rise above the first step in the same log(1 + $/kW) space the curve is estimated in
        m = float(scen["slope_multiplier"])
        first = np.log1p(t.groupby("ba")["cost_per_kw"].transform("first"))
        t["cost_per_kw"] = np.expm1(first + m * (np.log1p(t["cost_per_kw"]) - first))
        t["cost_per_kw"] = t.groupby("ba")["cost_per_kw"].cummax()
    if "cost_multiplier" in scen:
        t["cost_per_kw"] *= scen["cost_multiplier"]
        z["release_cost_per_kw"] *= scen["cost_multiplier"]
    opts = cfg.get("uprate_options", {})
    gen_per_h = gen_mw_per_mw_h(z, t, cfg)
    zone_cost = reinforcement_costs(cfg) if any(
        isinstance(opts[n].get("cost_per_kw"), str) for n in list(scen.get("uprates", [])) + list(cfg.get("backstop_uprates", []))) else None
    rows = []
    names = list(dict.fromkeys(list(scen.get("uprates", [])) + list(cfg.get("backstop_uprates", []))))
    for name in names:
        o = opts[name]
        for _, r in z.iterrows():
            rows.append({"ba": r["ba"], "uprate": name, "type": o.get("type", name),
                         "max_mw": o["share_of_capacity"] * r["base_capacity_mw"],
                         "cost_per_kw": uprate_cost(o, r["ba"], zone_cost, gen_per_h),
                         "available_year": int(o.get("available_year", 0)),
                         # ReEDS basis ($ per kW of generation) and the MW of weighted generation one MW
                         # of H hosts, for options priced from ReEDS (blank otherwise)
                         "reeds_cost_per_kw_gen": (float(zone_cost[r["ba"]]) if o["cost_per_kw"] == "reeds_reinforcement"
                                                   else np.nan),
                         "gen_mw_per_mw_h": (float(gen_per_h[r["ba"]]) if o["cost_per_kw"] == "reeds_reinforcement"
                                             else np.nan)})
    u = pd.DataFrame(rows, columns=["ba", "uprate", "type", "max_mw", "cost_per_kw", "available_year",
                                    "reeds_cost_per_kw_gen", "gen_mw_per_mw_h"])
    return z, t.sort_values(["ba", "sat_from"]).reset_index(drop=True), u


def reinforcement_costs(cfg: dict) -> pd.Series:
    """Zone -> new_line $/kW from the ReEDS reinforcement table (reinforcement.zone_table, statistic
    reinforcement.quantile), in the pipeline dollar year."""
    rc = cfg["reinforcement"]
    t = pd.read_csv(Path(cfg["_root"]) / rc["zone_table"])
    if int(t["dollar_year"].iat[0]) != int(cfg["dollar_year"]):
        raise ValueError(f"{rc['zone_table']} is in {t['dollar_year'].iat[0]}$, the pipeline in {cfg['dollar_year']}$; "
                         "rebuild it with `python -m icsc.cli reinforcement`")
    return t.set_index("ba")[f"reinforcement_{rc.get('quantile', 'median')}_per_kw"]


def gen_mw_per_mw_h(zones: pd.DataFrame, tranches: pd.DataFrame, cfg: dict) -> pd.Series:
    """Weighted generation MW that one MW of network capacity H hosts, per zone, for converting ReEDS's
    reinforcement cost ($ per MW of generation) to $ per MW of H (reinforcement.per_mw_h):

      curve_end (recommended)  the saturation at the end of the zone's curve: one MW of H releases s0 MW
                               of headroom and stretches the remaining steps by (end - s0) MW, so it
                               hosts `end` MW in all (the support edge, or s0 + edge_step for zones past it)
      s0                       release only: the s0 MW freed at the start saturation
      gen                      1, ReEDS's own method: one MW of route capacity per MW of generation
    """
    how = (cfg.get("reinforcement") or {}).get("per_mw_h", "curve_end")
    s0 = zones.set_index("ba")["start_saturation"]
    if how == "gen":
        return pd.Series(1.0, index=s0.index)
    if how == "s0":
        return s0.astype(float)
    if how == "curve_end":
        end = tranches.groupby("ba")["sat_to"].max() if len(tranches) else pd.Series(dtype=float)
        return end.reindex(s0.index).fillna(s0).astype(float)
    raise ValueError(f"reinforcement.per_mw_h must be curve_end, s0 or gen, not {how!r}")


def uprate_cost(option: dict, ba: str, zone_cost: pd.Series | None, gen_per_h: pd.Series | None = None) -> float:
    """$/kW of network capacity: a number from config, or 'reeds_reinforcement': the zone's ReEDS cost
    per kW of generation x the generation MW one MW of network hosts (gen_mw_per_mw_h; 1 if not given)."""
    c = option["cost_per_kw"]
    if c == "reeds_reinforcement":
        if zone_cost is None or ba not in zone_cost.index:
            raise KeyError(f"no ReEDS reinforcement cost for zone {ba}")
        factor = 1.0 if gen_per_h is None else float(gen_per_h[ba])
        return float(zone_cost[ba]) * factor
    if isinstance(c, str):
        raise ValueError(f"unknown uprate cost source {c!r}")
    return float(c)
