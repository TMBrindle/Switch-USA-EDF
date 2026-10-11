"""Mass-balance repair of a projected network (CHANGES §102).

Scaling arcs independently (by Table 64 bundles and regional growth) does not conserve gas at through-hubs: by 2045
TN, MO, OH, WV, MN, NH and GOM ship out more than they receive and produce ("overdraw", several Tcf). Overdrawn gas
carries the hub's mix downstream, so it distorts shares. The repair keeps every Table 64 interregional bundle as
projected and moves only the adjustable arcs (intra-region state arcs, GOM outflows, intra-Texas arcs) by the least
total volume (L1, HiGHS LP) so that every hub keeps a local disposition of at least

    Lmin_h = kappa * L_h(2024) * g_div(h)(y)

where L_h(2024) = throughput - outflow in the base network (consumption, exports, storage, losses at h), g_div is
AEO2026 Table 62 all-sector consumption growth 2025 -> y of the hub's census division (West South Central adds Table 61
LNG and Mexico pipeline exports), and kappa <= 1 scales the floors to the network's total supply. A hub that cannot
reach its floor through existing adjustable arcs takes a penalised slack (at most its floor, so no hub is
overdrawn), reported as unmet floor.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import linprog

CENDIV = {
    "NewEngland": "CT ME MA NH RI VT", "MiddleAtlanti": "NJ NY PA", "EastNorthCent": "IL IN MI OH WI",
    "WestNorthCent": "IA KS MN MO NE ND SD", "SouthAtlantic": "DE DC FL GA MD NC SC VA WV",
    "EastSouthCent": "AL KY MS TN", "WestSouthCent": "AR LA OK TX_E TX_N TX_W GOM", "Mountain": "AZ CO ID MT NV NM UT WY",
    "Pacific": "CA OR WA"}
HUB_DIV = {h: d for d, hs in CENDIV.items() for h in hs.split()}
SLACK_COST = 1e3


def division_growth(inp, year: int) -> dict:
    b0 = int(inp.cfg["aeo_index_base_year"])
    g = {}
    for d in CENDIV:
        s = inp.aeo_series("suptab_62", f"NGC000:ga_{d}")
        num, den = float(s[year]), float(s[b0])
        if d == "WestSouthCent":
            for code in ("NGI000:da_LiquefiedNatu", "NGI000:da_PipelineExpor"):
                e = inp.aeo_series("suptab_61", code)
                num += float(e[year])
                den += float(e[b0])
        g[d] = num / den
    return g


def local_disposition(supply: pd.DataFrame, flows: pd.DataFrame, nodes) -> pd.Series:
    P = supply.groupby("hub").value.sum().reindex(nodes).fillna(0.0)
    inn = flows.groupby("dst").value.sum().reindex(nodes).fillna(0.0)
    out = flows.groupby("src").value.sum().reindex(nodes).fillna(0.0)
    return P + inn - out


def balance(inp, base: dict, net: dict, nodes, tie_break: bool = False) -> tuple[dict, dict]:
    """Returns (balanced net, info)."""
    year = net["year"]
    reg = inp.region
    # The L1 problem can have tied optima. Default: the accepted Stage 1 formulation (ddac626). tie_break=True sorts
    # the arcs and adds tiny index-based costs: a second optimum, used only to measure how non-unique the projected
    # shares are (outputs/balance_degeneracy.csv, §103).
    fl = net["flows"].reset_index(drop=True).copy()
    if tie_break:
        fl = fl.sort_values(["src", "dst", "kind"]).reset_index(drop=True)
    rs, rd = fl.src.map(reg).fillna("SouthCentral"), fl.dst.map(reg).fillna("SouthCentral")
    adj = ((rs == rd) | (fl.src == "GOM") | (fl.kind == "intra_texas")).values
    L0 = local_disposition(base["supply"], base["flows"], nodes).clip(lower=0.0)
    g = division_growth(inp, year)
    target = pd.Series({h: L0[h] * g[HUB_DIV[h]] for h in nodes})
    total_supply = float(net["supply"].value.sum())
    kappa = min(1.0, total_supply / float(target.sum()))
    lmin = target * kappa
    ni = {h: i for i, h in enumerate(nodes)}
    N, A = len(nodes), int(adj.sum())
    ia = np.where(adj)[0]
    # fixed part of each hub's local disposition
    fixed = fl[~adj]
    Lfix = local_disposition(net["supply"], fixed, nodes)
    # variables: f_a (A), dplus (A), dminus (A), slack (N)
    nv = 3 * A + N
    tie = 1e-7 * np.arange(A) if tie_break else np.zeros(A)
    tn = 1e-7 * np.arange(N) if tie_break else np.zeros(N)
    c = np.concatenate([np.zeros(A), 1 + tie, 1 + tie, SLACK_COST * (1 + tn)])
    # f - dplus + dminus = target
    Aeq = np.zeros((A, nv))
    for k in range(A):
        Aeq[k, k], Aeq[k, A + k], Aeq[k, 2 * A + k] = 1, -1, 1
    beq = fl.value.values[ia]
    # Lfix_h + sum_in f - sum_out f + slack_h >= lmin_h   ->   -(...) <= Lfix_h - lmin_h
    Aub = np.zeros((N, nv))
    for k, j in enumerate(ia):
        Aub[ni[fl.dst[j]], k] -= 1
        Aub[ni[fl.src[j]], k] += 1
    for h in nodes:
        Aub[ni[h], 3 * A + ni[h]] = -1
    bub = np.array([Lfix[h] - lmin[h] for h in nodes])
    # slack <= lmin: an unmet floor may leave a hub at zero local disposition, never below (no overdraw)
    bounds = [(0, None)] * (3 * A) + [(0, float(lmin[h])) for h in nodes]
    res = linprog(c, A_ub=Aub, b_ub=bub, A_eq=Aeq, b_eq=beq, bounds=bounds, method="highs")
    if res.status != 0:
        raise RuntimeError(f"balance LP failed ({year}): {res.message}")
    x = res.x
    out = fl.copy()
    out.loc[ia, "value"] = x[:A]
    slack = pd.Series(x[3 * A:], index=nodes)
    moved = float(np.abs(x[:A] - beq).sum())
    L_before = local_disposition(net["supply"], net["flows"], nodes)
    L_after = local_disposition(net["supply"], out, nodes)
    info = {"kappa": kappa, "moved_mmcf": moved, "adjustable_arc_mmcf": float(beq.sum()),
            "overdraw_before_mmcf": float(L_before[L_before < 0].sum()),
            "overdraw_after_mmcf": float(L_after[L_after < 0].sum()),
            "slack_mmcf": float(slack.sum()), "slack_hubs": {h: float(v) for h, v in slack.items() if v > 1},
            "floor_hubs": {h: float(lmin[h]) for h, v in slack.items() if v > 1}}
    new = dict(net)
    new["flows"] = out[out.value > 1e-6].reset_index(drop=True)
    new["balance"] = info
    return new, info


def division_error(inp, base: dict, net: dict, nodes) -> float:
    """Sum over census divisions of |local disposition - its AEO-grown 2024 level (scaled to supply)| / sum of
    targets, %. Measures how far a projected network's regional off-take is from AEO2026 consumption (+ WSC exports)."""
    L0 = local_disposition(base["supply"], base["flows"], nodes).clip(lower=0.0)
    L = local_disposition(net["supply"], net["flows"], nodes)
    g = division_growth(inp, net["year"])
    tgt = pd.Series({h: L0[h] * g[HUB_DIV[h]] for h in nodes})
    tgt *= min(1.0, float(net["supply"].value.sum()) / float(tgt.sum()))
    div = pd.Series(HUB_DIV)
    a = L.groupby(div.reindex(L.index)).sum()
    t = tgt.groupby(div.reindex(tgt.index)).sum()
    return float(100 * (a - t).abs().sum() / t.sum())
