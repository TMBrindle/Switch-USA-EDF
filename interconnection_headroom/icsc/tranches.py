"""Turn the fitted cost model into stepwise, convex network-upgrade supply curves per ReEDS zone."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .estimate import CostModel


def zone_regimes(hierarchy: pd.DataFrame, model: CostModel, cfg: dict) -> pd.DataFrame:
    """Assign each zone a planning regime; zones whose regime is not in the sample get the mean effect."""
    aliases = cfg.get("regimes", {}).get("transreg_to_regime", {})
    z = hierarchy[["ba", "transreg"]].copy()
    z["regime"] = z["transreg"].map(lambda t: aliases.get(t, t))
    eff = model.regime_effects()
    z["regime_in_sample"] = z["regime"].isin(eff.index)
    z["regime_effect"] = z["regime"].map(eff).fillna(eff.mean())
    return z.set_index("ba")


def _predict_with_effect(model: CostModel, sat, effect: float, cfg: dict) -> np.ndarray:
    """Predict at the reference project but with an explicit regime effect (base-regime design + offset)."""
    tc = cfg["tranches"]
    yhat = model.log_pred(sat, tc["reference_tech"], tc["reference_service"], tc["reference_capacity_mw"],
                          tc.get("reference_year", cfg["dollar_year"]), model.base_regime) + effect
    return np.clip(np.exp(yhat) * model.smear - 1, 0, None)


def build_reference(model: CostModel, panel: pd.DataFrame, regimes: pd.DataFrame, cfg: dict,
                    start_year: int, regime_override: str | None = None) -> pd.DataFrame:
    """One row per (zone, tranche): MW available and $/kW, starting from the zone's saturation in start_year.

    Costs are made non-decreasing (cumulative max) so the curve is convex and the LP fills it in order.
    """
    tc = cfg["tranches"]
    steps = np.asarray(tc["steps"], dtype=float)
    cur = panel[panel["year"] == min(start_year, panel["year"].max())].set_index("ba")
    eff = model.regime_effects()
    rows = []
    for ba, r in cur.iterrows():
        if ba not in regimes.index:
            continue
        effect = regimes.at[ba, "regime_effect"]
        if regime_override == "best":
            effect = float(eff.min())
        s0 = float(r["saturation"])
        edges = s0 + np.concatenate([[0], np.cumsum(steps)])
        mids = (edges[:-1] + edges[1:]) / 2
        cost = np.maximum.accumulate(_predict_with_effect(model, mids, effect, cfg))
        cost = np.minimum(cost, tc["max_cost_per_kw"])
        for i, (st, c) in enumerate(zip(steps, cost)):
            rows.append({"ba": ba, "tranche": f"nu{i + 1}", "tranche_type": "network_upgrade",
                         "sat_from": edges[i], "sat_to": edges[i + 1],
                         "max_mw": st * r["headroom_proxy_floored_mw"], "cost_per_kw": float(c),
                         "extrapolated": bool(mids[i] > model.sat_support),
                         "available_year": start_year})
    return pd.DataFrame(rows)


def apply_scenario(ref: pd.DataFrame, best: pd.DataFrame | None, panel_now: pd.DataFrame,
                   scen: dict) -> pd.DataFrame:
    """Apply one scenario definition from config.yaml to the reference tranches."""
    out = (best if scen.get("regime_override") == "best" and best is not None else ref).copy()
    if "cost_multiplier" in scen:
        out["cost_per_kw"] *= scen["cost_multiplier"]
    extra = []
    for x in scen.get("extra_tranches", []):
        for ba, r in panel_now.iterrows():
            extra.append({"ba": ba, "tranche": x["name"], "tranche_type": x["name"],
                          "sat_from": np.nan, "sat_to": np.nan,
                          "max_mw": x["share_of_headroom"] * r["headroom_proxy_floored_mw"],
                          "cost_per_kw": x["cost_per_kw"],
                          "available_year": x.get("available_year", int(out["available_year"].min()))})
    if extra:
        out = pd.concat([pd.DataFrame(extra), out], ignore_index=True)
    return out.sort_values(["ba", "cost_per_kw", "tranche"]).reset_index(drop=True)
