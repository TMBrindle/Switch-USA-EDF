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


def zone_regimes(hierarchy: pd.DataFrame, model: CostModel, cfg: dict) -> pd.DataFrame:
    """Assign each zone a planning regime; zones whose regime is not in the sample get the mean effect."""
    z = hierarchy[["ba", "transreg", "hurdlereg"]].copy()
    z["regime"] = zone_regime_labels(hierarchy, cfg).reindex(z["ba"]).values
    eff = model.regime_effects()
    z["regime_in_sample"] = z["regime"].isin(eff.index)
    z["regime_effect"] = z["regime"].map(eff).fillna(eff.mean())
    return z.set_index("ba")


def _predict_with_effect(model: CostModel, sat, effect: float, cfg: dict) -> np.ndarray:
    """Predict at the reference project but with an explicit regime effect (base-regime design + offset)."""
    tc = cfg["tranches"]
    yhat = model.log_pred(sat, tc["reference_tech"], tc["reference_service"], tc["reference_capacity_mw"],
                          tc.get("reference_year", cfg["dollar_year"]), model.base_regime,
                          tc.get("reference_status", "completed")) + effect
    return model.to_cost(yhat)


def build_reference(model: CostModel, panel: pd.DataFrame, regimes: pd.DataFrame, cfg: dict,
                    start_year: int, regime_override: str | None = None
                    ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (zones, tranches) for every zone, starting from its saturation in start_year.

    Costs are made non-decreasing (cumulative max) so the curve is convex and the LP fills it in order.
    """
    tc = cfg["tranches"]
    steps = np.asarray(tc["steps"], dtype=float)
    cur = panel[panel["year"] == min(start_year, panel["year"].max())].set_index("ba")
    eff = model.regime_effects()
    zrows, trows = [], []
    for ba, r in cur.iterrows():
        if ba not in regimes.index:
            continue
        effect = float(eff.min()) if regime_override == "best" else regimes.at[ba, "regime_effect"]
        s0 = float(r["saturation"])
        edges = s0 + np.concatenate([[0], np.cumsum(steps)])
        mids = (edges[:-1] + edges[1:]) / 2
        cost = np.maximum.accumulate(_predict_with_effect(model, mids, effect, cfg))
        cost = np.minimum(cost, tc["max_cost_per_kw"])
        # released headroom sits just below s0: price it at the marginal cost at s0
        release = float(min(_predict_with_effect(model, np.array([s0]), effect, cfg)[0], cost[0]))
        zrows.append({"ba": ba, "base_capacity_mw": float(r["headroom_proxy_floored_mw"]),
                      "start_saturation": s0, "release_cost_per_kw": release,
                      "regime": regimes.at[ba, "regime"]})
        for i, (st, c) in enumerate(zip(steps, cost)):
            trows.append({"ba": ba, "tranche": f"nu{i + 1}", "width": float(st),
                          "cost_per_kw": float(c), "sat_from": edges[i], "sat_to": edges[i + 1],
                          "extrapolated": bool(mids[i] > model.sat_support), "available_year": 0})
    return pd.DataFrame(zrows), pd.DataFrame(trows)


def apply_scenario(zones: pd.DataFrame, tranches: pd.DataFrame, cfg: dict, scen: dict,
                   best: tuple[pd.DataFrame, pd.DataFrame] | None = None
                   ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Apply one scenario from config.yaml. Returns (zones, tranches, uprates).

    Levers:
      regime_override: best     height: every zone gets the lowest regime effect
      cost_multiplier: x        height: scale every step's cost (and the release cost)
      slope_multiplier: m       steepness: c_k -> c_1 * (c_k / c_1) ** m within each zone
      uprates: [names]          network capacity options from `uprate_options`, which Switch may build
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
    rows = []
    for name in scen.get("uprates", []):
        o = opts[name]
        for _, r in z.iterrows():
            rows.append({"ba": r["ba"], "uprate": name, "type": o.get("type", name),
                         "max_mw": o["share_of_capacity"] * r["base_capacity_mw"],
                         "cost_per_kw": o["cost_per_kw"],
                         "available_year": int(o.get("available_year", 0))})
    u = pd.DataFrame(rows, columns=["ba", "uprate", "type", "max_mw", "cost_per_kw", "available_year"])
    return z, t.sort_values(["ba", "sat_from"]).reset_index(drop=True), u
