"""Proportional-sharing gas tracing (full mixing at each node; Bialek 1996 / Kirschen 1997 flow tracing).

For node n with own supply P[n, b] by source basin b and gross directional receipts F[m, n]:
    T[n] = sum_b P[n, b] + sum_m F[m, n]               (throughput)
    T[n] s[n, b] = P[n, b] + sum_m F[m, n] s[m, b]     ->   (diag(T) - F^T) S = P
Every delivery out of n and every burn at n carries the mix s[n, .]. Consumption, exports and storage drop out.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def trace(supply: pd.DataFrame, flows: pd.DataFrame, nodes=None, basins=None) -> pd.DataFrame:
    """supply: hub, basin, value; flows: src, dst, value (gross, directional, >= 0).
    Returns a long frame hub, basin, share, throughput. Hubs with zero throughput get NaN shares."""
    nodes = list(nodes) if nodes is not None else sorted(set(supply.hub) | set(flows.src) | set(flows.dst))
    basins = list(basins) if basins is not None else sorted(set(supply.basin))
    ni, bi = {n: i for i, n in enumerate(nodes)}, {b: i for i, b in enumerate(basins)}
    unknown = (set(supply.hub) | set(flows.src) | set(flows.dst)) - set(nodes)
    if unknown:
        raise ValueError(f"trace: nodes not in node list: {sorted(unknown)}")
    if (flows.value < 0).any() or (supply.value < 0).any():
        raise ValueError("trace: negative supply or flow")
    N, B = len(nodes), len(basins)
    P = np.zeros((N, B))
    for h, b, v in supply[["hub", "basin", "value"]].itertuples(index=False):
        P[ni[h], bi[b]] += v
    F = np.zeros((N, N))
    for s, d, v in flows[["src", "dst", "value"]].itertuples(index=False):
        if s != d:
            F[ni[s], ni[d]] += v
    T = P.sum(1) + F.sum(0)
    live = T > 0
    A = np.diag(np.where(live, T, 1.0)) - F.T
    S = np.linalg.solve(A, P)
    S[~live] = np.nan
    long = pd.DataFrame({"hub": np.repeat(nodes, B), "basin": np.tile(basins, N), "share": S.reshape(-1),
                         "throughput": np.repeat(T, B)})
    return long
