"""Texas as three NGMM hubs (TX_E, TX_N, TX_W).

Routing assumption (CHANGES §93): EIA publishes Texas as one state. Each interstate arc is assigned to a sub-hub by
NGMM's border mapping (config texas.border_hub; Oklahoma split by NGMM's 2023 history). Each sub-hub's disposition is
its share of Texas consumption (NGMM TexasConsShares; lease and plant fuel with its own production; pipeline and
distribution use with its supply), its interstate deliveries, Mexico exports (NGMM 2024 split) and LNG exports (TX_E).
Dispositions are scaled so Texas balances (the scale absorbs storage and EIA's balancing item). Intra-Texas transfers
are then the least total flow that balances the three hubs within NGMM's 2023 intra-Texas capacities.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import linprog

HUBS = ("TX_E", "TX_N", "TX_W")


def split(v: float, shares: dict) -> dict:
    tot = float(sum(shares.values()))
    return {h: v * float(shares.get(h, 0.0)) / tot for h in HUBS}


def assign_interstate(flows: pd.DataFrame, tx: dict) -> pd.DataFrame:
    """Replace 'TX' in src/dst with sub-hubs. flows: src, dst, value."""
    rows = []
    for s, d, v in flows[["src", "dst", "value"]].itertuples(index=False):
        if s == "TX" and d == "TX":
            continue
        if d == "TX":
            if s == "OK":
                for h, x in split(v, tx["ok_receipts_split"]).items():
                    rows.append((s, h, x))
            elif s in tx["border_hub"]:
                rows.append((s, tx["border_hub"][s], v))
            else:
                raise KeyError(f"Texas receipts from {s}: no sub-hub in config texas.border_hub")
        elif s == "TX":
            if d == "OK":
                for h, x in split(v, tx["ok_deliveries_split"]).items():
                    rows.append((h, d, x))
            elif d in tx["border_hub"]:
                rows.append((tx["border_hub"][d], d, v))
            else:
                raise KeyError(f"Texas deliveries to {d}: no sub-hub in config texas.border_hub")
        else:
            rows.append((s, d, v))
    out = pd.DataFrame(rows, columns=["src", "dst", "value"])
    return out[out.value > 0].groupby(["src", "dst"], as_index=False).value.sum()


def dispositions(cons: dict, production: dict, receipts: dict, deliveries: dict, mexico: float, lng: float,
                 tx: dict) -> dict:
    """Disposition by sub-hub before balancing. cons: EIA end uses (MMcf)."""
    sh = tx["consumption_shares"]
    d = {h: 0.0 for h in HUBS}
    for use, key in [("residential", "residential"), ("commercial", "commercial"), ("industrial", "industrial"),
                     ("electric", "electric"), ("vehicle", "commercial")]:
        for h, x in split(cons.get(use, 0.0), sh[key]).items():
            d[h] += x
    prod_tot = sum(production.values()) or 1.0
    for use in ("lease", "plant"):
        for h in HUBS:
            d[h] += cons.get(use, 0.0) * production.get(h, 0.0) / prod_tot
    sup = {h: production.get(h, 0.0) + receipts.get(h, 0.0) for h in HUBS}
    sup_tot = sum(sup.values()) or 1.0
    for h in HUBS:
        d[h] += cons.get("pipeline", 0.0) * sup[h] / sup_tot
    for h, x in split(mexico, tx["mexico_export_split"]).items():
        d[h] += x
    d[tx["lng_export_hub"]] += lng
    for h in HUBS:
        d[h] += deliveries.get(h, 0.0)
    return d


def route(supply: dict, disposition: dict, tx: dict, days: int):
    """Least-flow intra-Texas transfers. Returns (flows frame src,dst,value,capacity; info dict)."""
    s_tot, d_tot = sum(supply.values()), sum(disposition.values())
    scale = s_tot / d_tot
    dem = {h: disposition[h] * scale for h in HUBS}
    arcs = list(tx["capacity_mmcfd"].keys())
    n = len(arcs)
    cost = np.array([float(tx["arc_cost"][a]) for a in arcs] + [1e6] * 6)
    slack_arcs = [f"{a}->{b}" for a in HUBS for b in HUBS if a != b]
    allarcs = arcs + slack_arcs
    A = np.zeros((3, len(allarcs)))
    for j, a in enumerate(allarcs):
        src, dst = a.split("->")
        A[HUBS.index(src), j] -= 1
        A[HUBS.index(dst), j] += 1
    b = np.array([dem[h] - supply[h] for h in HUBS])
    bounds = [(0, float(tx["capacity_mmcfd"][a]) * days) for a in arcs] + [(0, None)] * 6
    res = linprog(cost, A_eq=A, b_eq=b, bounds=bounds, method="highs")
    if res.status != 0:
        raise RuntimeError(f"Texas routing LP failed: {res.message}")
    x = res.x
    rows = [(a.split("->")[0], a.split("->")[1], x[j], float(tx["capacity_mmcfd"][a]) * days if j < n else np.nan)
            for j, a in enumerate(allarcs)]
    fl = pd.DataFrame(rows, columns=["src", "dst", "value", "capacity"])
    slack = float(x[n:].sum())
    fl = fl.groupby(["src", "dst"], as_index=False).agg(value=("value", "sum"), capacity=("capacity", "max"))
    info = {"disposition_scale": scale, "capacity_slack_mmcf": slack,
            "supply": supply, "disposition_scaled": dem}
    return fl[fl.value > 1e-6].reset_index(drop=True), info
