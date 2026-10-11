"""Stage 2a: standalone annual gas network LP (CHANGES §96). No Switch link; no model code touched.

Nodes: the 52 Stage 1 hubs (47 states + DC, TX_E/TX_N/TX_W, GOM) and one supply node per basin (14). Basin nodes reach
the hubs where the basin's gas enters through gathering arcs fixed at the hub's share of the basin (production areas
for producing basins, crossings for Canada, terminals for LNG imports), so supply by basin is honoured exactly.
Arcs: capacity.py (EIA state-to-state 2025 + NGMM intra-Texas 2024 + tracker projects under Tom's status rule).

Annual LP (HiGHS):  min  sum_a c_a f_a + M_u sum_h u_h + M_s sum_h s_h
  hub balance:   sum_b g_bh + sum_in f - sum_out f + u_h - s_h = D_h          (u unserved demand, s spilled supply)
  arc limits:    0 <= f_a <= U * cap_a * days                                  (U: annual utilisation cap)
  c_a = hurdle + great-circle km between hub centroids (EIA-860M gas generators' mean location; all generators where a state has no gas plant (VT); GOM at 28.5N 90.5W).
  ASSUMPTION: with fixed supply and demand the cost scale does not change flows, only relative distances do; hurdle
  (a per-arc cost in km-equivalents) and U are the two calibration knobs, chosen on 2024 arc error.
Supply: 2024 EIA (Stage 1 base); projected years Stage 1's AEO2026-indexed supply (Tables 59/60; Canada additive
  Table 64; LNG Table 61). Demand D_h: EIA 2024 consumption by end use + exports by state; projected years x AEO2026
  growth (Table 62 division x sector; Table 61 exports; lease/plant with state production; pipeline & distribution
  with delivered use). Texas split as Stage 1 (NGMM shares). Demand scaled to total supply (factor reported).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from . import balance, capacity, eia, fetch, network, texas, tracing

M_UNSERVED = 1e6
M_SPILL = 1e6
GOM_XY = (28.5, -90.5)
SECTOR_CODE = {"residential": "ba", "commercial": "ca", "industrial": "da", "electric": "ea", "vehicle": "fa"}


# ---------------------------------------------------------------- geometry
def hub_coords(inp, tx_hubs: pd.DataFrame) -> pd.DataFrame:
    allg = pd.read_excel(inp.raw / "eia" / "august_generator2026.xlsx", sheet_name="Operating", header=2)
    d = allg[allg["Energy Source Code"] == "NG"]
    d = pd.DataFrame({"state": d["Plant State"].values, "county": d["County"].values,
                      "lat": pd.to_numeric(d["Latitude"], errors="coerce").values,
                      "lon": pd.to_numeric(d["Longitude"], errors="coerce").values}).dropna()
    m = {capacity._norm(n): h for n, h in zip(tx_hubs.county_name, tx_hubs.hub) if isinstance(h, str)}
    d["hub"] = np.where(d.state == "TX", d.county.map(lambda c: m.get(capacity._norm(c))), d.state)
    xy = d.dropna(subset=["hub"]).groupby("hub")[["lat", "lon"]].mean()
    xy.loc["GOM"] = GOM_XY
    for h in set(inp.hubs.hub) - set(xy.index):      # no gas generators (VT): all generators in the state
        g = allg[allg["Plant State"] == h]
        xy.loc[h] = (pd.to_numeric(g["Latitude"], errors="coerce").mean(), pd.to_numeric(g["Longitude"], errors="coerce").mean())
    missing = set(inp.hubs.hub) - set(xy.index)
    if missing:
        raise ValueError(f"hubs without gas generators for a centroid: {sorted(missing)}")
    return xy.loc[inp.hubs.hub]


def km(a, b) -> float:
    p = np.pi / 180
    x = (np.sin((b[0] - a[0]) * p / 2) ** 2 + np.cos(a[0] * p) * np.cos(b[0] * p) * np.sin((b[1] - a[1]) * p / 2) ** 2)
    return float(12742 * np.arcsin(np.sqrt(x)))


# ---------------------------------------------------------------- demand
def base_demand(inp) -> pd.DataFrame:
    """2024: state, use, mmcf (end uses + exports_lng / exports_mexico / exports_canada)."""
    rows = []
    for st in fetch.STATES:
        c = eia.consumption(inp.raw / "eia" / "consumption" / f"NG_CONS_SUM_DCU_S{st}_A.xls", inp.year)
        for u in ("residential", "commercial", "industrial", "electric", "vehicle", "lease", "plant", "pipeline"):
            rows.append((st, u, c.get(u, 0.0)))
        x = eia.international_deliveries(inp.raw / "eia" / "movements", st, inp.year)
        rows.append((st, "exports_mexico", float(x.get("Mexico", 0.0))))
        rows.append((st, "exports_canada", float(x.get("Canada", 0.0))))
        rows.append((st, "exports_lng", float(x.drop(["Mexico", "Canada"], errors="ignore").sum())))
    return pd.DataFrame(rows, columns=["state", "use", "mmcf"])


def demand(inp, base_dem: pd.DataFrame, base_sup: pd.DataFrame, sup: pd.DataFrame, year: int) -> pd.DataFrame:
    """Hub demand for `year` (hub, use, mmcf), before scaling to supply."""
    d = base_dem.copy()
    if year != inp.year:
        b0 = int(inp.cfg["aeo_index_base_year"])
        div = {h: dv for h, dv in balance.HUB_DIV.items()}
        div["TX"] = "WestSouthCent"
        for u, code in SECTOR_CODE.items():
            g = {dv: float(inp.aeo_series("suptab_62", f"NGC000:{code}_{dv}")[year] /
                         inp.aeo_series("suptab_62", f"NGC000:{code}_{dv}")[b0]) for dv in balance.CENDIV}
            m = d.use == u
            d.loc[m, "mmcf"] = d.loc[m, "mmcf"] * d.loc[m, "state"].map(div).map(g)
        for u, code in [("exports_lng", "NGI000:da_LiquefiedNatu"), ("exports_mexico", "NGI000:da_PipelineExpor"),
                        ("exports_canada", "NGI000:ca_PipelineExpor")]:
            s = inp.aeo_series("suptab_61", code)
            d.loc[d.use == u, "mmcf"] *= float(s[year] / s[b0])
        st_of = lambda h: "TX" if str(h).startswith("TX_") else h
        p0 = base_sup[base_sup.kind == "production"].groupby(base_sup.hub.map(st_of)).value.sum()
        p1 = sup[sup.kind == "production"].groupby(sup.hub.map(st_of)).value.sum()
        gp = (p1 / p0).replace([np.inf], 1.0).fillna(1.0)
        m = d.use.isin(["lease", "plant"])
        d.loc[m, "mmcf"] = d.loc[m, "mmcf"] * d.loc[m, "state"].map(gp).fillna(1.0)
        enduse = ["residential", "commercial", "industrial", "electric", "vehicle"]
        b = base_dem[base_dem.use.isin(enduse)].groupby("state").mmcf.sum()
        n = d[d.use.isin(enduse)].groupby("state").mmcf.sum()
        gd = (n / b).fillna(1.0)
        m = d.use == "pipeline"
        d.loc[m, "mmcf"] = d.loc[m, "mmcf"] * d.loc[m, "state"].map(gd).fillna(1.0)
    # Texas sub-hubs (Stage 1 shares)
    tx = d[d.state == "TX"].set_index("use").mmcf
    prod = sup[(sup.kind == "production") & sup.hub.isin(texas.HUBS)].groupby("hub").value.sum().to_dict()
    cons = {u: float(tx.get(u, 0.0)) for u in ("residential", "commercial", "industrial", "electric", "vehicle",
                                                "lease", "plant", "pipeline")}
    disp = texas.dispositions(cons, prod, {}, {}, float(tx.get("exports_mexico", 0.0)),
                              float(tx.get("exports_lng", 0.0)), inp.cfg["texas"])
    out = d[d.state != "TX"].rename(columns={"state": "hub"})
    out = pd.concat([out, pd.DataFrame([(h, "texas_total", v) for h, v in disp.items()], columns=["hub", "use", "mmcf"]),
                     pd.DataFrame([("TX_E", "exports_canada", float(tx.get("exports_canada", 0.0)))],
                                  columns=["hub", "use", "mmcf"])], ignore_index=True)
    return out


# ---------------------------------------------------------------- LP
EXPAND_COST = 1e4      # km-equivalent per MMcf of expansion: >> any routing cost (max arc ~3,000 km), << M_UNSERVED


def solve(nodes, coords, supply: pd.DataFrame, dem: pd.Series, cap: pd.DataFrame, days: int,
          util: float, hurdle: float, expand: bool = False) -> dict:
    """supply: hub, basin, value (gathering arcs, fixed); dem: hub -> MMcf; cap: src, dst, mmcfd."""
    ni = {h: i for i, h in enumerate(nodes)}
    cap = cap[cap.src.isin(ni) & cap.dst.isin(ni)].reset_index(drop=True)
    A, N = len(cap), len(nodes)
    xy = {h: (float(r.lat), float(r.lon)) for h, r in coords.iterrows()}
    cost = np.array([hurdle + km(xy[s], xy[d]) for s, d in zip(cap.src, cap.dst)])
    inj = supply.groupby("hub").value.sum().reindex(nodes).fillna(0.0).values
    D = dem.reindex(nodes).fillna(0.0).values
    # variables: f (A), u (N), s (N) [, e (A): expansion diagnostic, adds to the arc's limit]
    rows, cols, vals = [], [], []
    for j, (s, d) in enumerate(zip(cap.src, cap.dst)):
        rows += [ni[d], ni[s]]
        cols += [j, j]
        vals += [1.0, -1.0]
    for i in range(N):
        rows += [i, i]
        cols += [A + i, A + N + i]
        vals += [1.0, -1.0]
    nv = A + 2 * N + (A if expand else 0)
    Aeq = coo_matrix((vals, (rows, cols)), shape=(N, nv)).tocsr()
    beq = D - inj
    c = np.concatenate([cost, np.full(N, M_UNSERVED), np.full(N, M_SPILL)] + ([EXPAND_COST + cost] if expand else []))
    capv = util * cap.mmcfd.values * days
    if expand:      # f_a - e_a <= cap_a
        ub = [None] * A + [None] * (2 * N) + [None] * A
        Aub = coo_matrix((np.r_[np.ones(A), -np.ones(A)], (np.r_[np.arange(A), np.arange(A)],
                                                            np.r_[np.arange(A), A + 2 * N + np.arange(A)])),
                         shape=(A, nv)).tocsr()
        res = linprog(c, A_ub=Aub, b_ub=capv, A_eq=Aeq, b_eq=beq, bounds=list(zip([0.0] * nv, ub)), method="highs")
    else:
        ub = list(capv) + [None] * (2 * N)
        res = linprog(c, A_eq=Aeq, b_eq=beq, bounds=list(zip([0.0] * nv, ub)), method="highs")
    if res.status != 0:
        raise RuntimeError(f"gas LP failed: {res.message}")
    x = res.x
    fl = cap.assign(flow=x[:A], cap_annual=util * cap.mmcfd * days, cost=cost,
                    expansion=(x[A + 2 * N:] if expand else np.zeros(A)))
    fl["utilisation"] = fl.flow / fl.cap_annual
    fl["expansion_mmcfd"] = fl.expansion / days / util
    return {"flows": fl, "unserved": pd.Series(x[A:A + N], index=nodes), "spill": pd.Series(x[A + N:A + 2 * N], index=nodes),
            "objective": float(res.fun)}


def arc_error(model: pd.DataFrame, obs: pd.DataFrame) -> dict:
    """Arc-level comparison on state pairs (TX sub-hubs kept): weighted absolute error and R^2."""
    m = model.groupby(["src", "dst"]).flow.sum()
    o = obs.groupby(["src", "dst"]).value.sum()
    j = pd.concat([m.rename("model"), o.rename("obs")], axis=1).fillna(0.0)
    wae = float((j.model - j.obs).abs().sum() / j.obs.sum())
    ss = float(((j.obs - j.obs.mean()) ** 2).sum())
    r2 = 1 - float(((j.model - j.obs) ** 2).sum()) / ss if ss else float("nan")
    # cross-hauling in the data (both directions used) cannot be reproduced by a min-cost LP: net comparison too
    net = j.copy()
    net.index = pd.MultiIndex.from_tuples([tuple(sorted(k)) if True else k for k in net.index])
    sign = np.array([1 if k == tuple(sorted(k)) else -1 for k in j.index])
    jn = pd.DataFrame({"model": j.model.values * sign, "obs": j.obs.values * sign}, index=net.index).groupby(level=[0, 1]).sum()
    wae_net = float((jn.model - jn.obs).abs().sum() / jn.obs.abs().sum())
    return {"weighted_abs_error": wae, "r2": r2, "weighted_abs_error_net": wae_net, "table": j}


def basin_supply_frame(sup: pd.DataFrame) -> pd.DataFrame:
    """Gathering arcs basin -> hub (fixed): hub, basin, value."""
    return sup.groupby(["hub", "basin"], as_index=False).value.sum()


# ---------------------------------------------------------------- run
YEARS = [2024, 2030, 2035, 2045]
UTIL_GRID = [0.6, 0.7, 0.8, 0.9, 1.0]
HURDLE_GRID = [0.0, 100.0, 300.0, 1000.0]
EXAMPLE = {"FL (p91, p101, p102)": "p101", "east TX (p57, p64-p67)": "p64", "VA (p99, p100, p118, p124)": "p99",
           "MA (p131)": "p131", "CA (p8-p11)": "p10"}


def _region_pairs(inp, fl: pd.DataFrame, col: str) -> pd.Series:
    rs, rd = fl.src.map(inp.region), fl.dst.map(inp.region)
    x = fl.assign(rs=rs, rd=rd)
    r = x[x.rs != x.rd].groupby(["rs", "rd"])[col].sum()
    r.index.names = ["src", "dst"]
    return r


def run(cfg, log=print) -> dict:
    from pathlib import Path
    from . import zones
    out = Path(cfg["paths"]["outputs"]) / "stage2a"
    out.mkdir(parents=True, exist_ok=True)
    inp = network.Inputs(cfg)
    nodes, basins = inp.hubs.hub.tolist(), list(cfg["basins"])
    base = network.base_network(inp)
    w, txc, _ = zones.zone_hub_weights(cfg, inp.areas)
    cmap, cflags = capacity.tx_county_hub_map(txc, w)
    xy = hub_coords(inp, txc)
    bdem = base_demand(inp)
    raw, ref = Path(cfg["paths"]["raw"]), Path(cfg["paths"]["reference"])
    obs = base["flows"][base["flows"].kind == "interstate"]
    flags = [dict(year="", flag=f[0], what=f[1], note=f[3]) for f in cflags]

    def inputs(y):
        sup = base["supply"] if y == inp.year else network.project(inp, base, y, "additive")["supply"]
        dem = demand(inp, bdem, base["supply"], sup, y).groupby("hub").mmcf.sum()
        scale = float(sup.value.sum() / dem.sum())
        return sup, dem * scale, scale

    # calibration on 2024
    sup0, dem0, sc0 = inputs(inp.year)
    cap0, f0 = capacity.capacity(raw, ref, cmap, inp.year)
    flags += [dict(year=inp.year, flag=f[0], what=f[1], note=f[3]) for f in f0]
    grid = []
    for u in UTIL_GRID:
        for h in HURDLE_GRID:
            r = solve(nodes, xy, basin_supply_frame(sup0), dem0, cap0, 366, u, h)
            e = arc_error(r["flows"][~(r["flows"].src.isin(texas.HUBS) & r["flows"].dst.isin(texas.HUBS))], obs)
            grid.append(dict(util=u, hurdle_km=h, weighted_abs_error=e["weighted_abs_error"], r2=e["r2"],
                             weighted_abs_error_net=e["weighted_abs_error_net"],
                             unserved_mmcf=float(r["unserved"].sum()), spill_mmcf=float(r["spill"].sum()),
                             binding_arcs=int((r["flows"].utilisation > 0.99).sum())))
    grid = pd.DataFrame(grid)
    grid.to_csv(out / "calibration_grid.csv", index=False, float_format="%.4f")
    tol = 1e-3 * float(sup0.value.sum())      # 0.1% of supply: a few hubs have no usable arc (reported in summary)
    ok = grid[(grid.unserved_mmcf <= tol) & (grid.spill_mmcf <= tol)]
    if ok.empty:
        raise RuntimeError(f"no calibration setting serves all 2024 demand; see {out / 'calibration_grid.csv'}")
    best = ok.sort_values(["weighted_abs_error", "hurdle_km"]).iloc[0]
    U, H = float(best.util), float(best.hurdle_km)
    grid.to_csv(out / "calibration_grid.csv", index=False, float_format="%.4f")
    # runs
    arcs, binding, regs, hubrows, summ, zrows = [], [], [], [], [], []
    s1 = pd.read_csv(Path(cfg["paths"]["outputs"]) / "zone_basin_shares.csv")
    for variant, appr, expand in [("status_rule", False, False), ("with_approved", True, False),
                                  ("status_rule_min_expansion", False, True)]:
        for y in YEARS:
            sup, dem, sc = inputs(y)
            cap, _ = capacity.capacity(raw, ref, cmap, y, include_approved=appr)
            r = solve(nodes, xy, basin_supply_frame(sup), dem, cap, 366 if y % 4 == 0 else 365, U, H, expand)
            fl = r["flows"].assign(year=y, variant=variant)
            if y == inp.year:
                o = obs.groupby(["src", "dst"]).value.sum()
                fl["obs_2024"] = [o.get((s, d), np.nan) for s, d in zip(fl.src, fl.dst)]
            arcs.append(fl)
            binding.append(fl[fl.utilisation > 0.99])
            # region comparison with Table 64 (2024 against EIA aggregates and Table 64 2025)
            m = _region_pairs(inp, fl, "flow").div(1000).rename("model_bcf")
            t = inp.t64[(inp.t64.year == (2025 if y == inp.year else y)) & ~inp.t64.src.str.startswith("Canada")]
            t = t.groupby(["src", "dst"]).bcf.sum().rename("table64_bcf")
            rg = pd.concat([m, t], axis=1).fillna(0.0)
            if y == inp.year:
                rg = rg.join(_region_pairs(inp, obs, "value").div(1000).rename("eia_2024_bcf"), how="outer").fillna(0.0)
            rg.index.names = ["src", "dst"]
            regs.append(rg.reset_index().assign(year=y, variant=variant))
            # traced shares (spill removed from supply)
            sp = r["spill"]
            g = basin_supply_frame(sup)
            tot = g.groupby("hub").value.transform("sum")
            g["value"] = g.value * (1 - g.hub.map(sp).fillna(0.0) / tot).clip(lower=0.0)
            tr = tracing.trace(g, fl.rename(columns={"flow": "value"})[["src", "dst", "value"]], nodes, basins)
            hubrows.append(tr.assign(year=y, variant=variant))
            z = w.merge(tr, on="hub").assign(share=lambda d: d.weight * d.share).groupby(
                ["zone", "basin"], as_index=False).share.sum()
            for name, zone in EXAMPLE.items():
                a = z[z.zone == zone].set_index("basin").share
                b = s1[(s1.zone == zone) & (s1.year == y)].set_index("basin").share
                for bs in basins:
                    zrows.append(dict(zones=name, year=y, variant=variant, basin=bs, stage2a=float(a.get(bs, 0.0)),
                                      stage1=float(b.get(bs, 0.0))))
            rgt = rg[rg.table64_bcf.abs() + rg.model_bcf.abs() > 0]
            summ.append(dict(year=y, variant=variant, util=U, hurdle_km=H, demand_scale=sc,
                             supply_tcf=float(sup.value.sum() / 1e6),
                             unserved_bcf=float(r["unserved"].sum() / 1e3), spill_bcf=float(r["spill"].sum() / 1e3),
                             unserved_hubs=" ".join(f"{k}:{v / 1e3:.0f}" for k, v in r["unserved"].items() if v > 1e3),
                             spill_hubs=" ".join(f"{k}:{v / 1e3:.0f}" for k, v in r["spill"].items() if v > 1e3),
                             binding_arcs=int((fl.utilisation > 0.99).sum()),
                             expansion_bcf=float(fl.expansion.sum() / 1e3),
                             expansion_arcs=" ".join(f"{a}->{b}:{v:.0f}" for a, b, v in
                                                     zip(fl.src, fl.dst, fl.expansion_mmcfd) if v > 1),
                             region_weighted_abs_error_vs_table64=float((rgt.model_bcf - rgt.table64_bcf).abs().sum()
                                                                       / rgt.table64_bcf.sum())))
            if y == inp.year:
                e = arc_error(fl[~(fl.src.isin(texas.HUBS) & fl.dst.isin(texas.HUBS))], obs)
                summ[-1].update(arc_weighted_abs_error_2024=e["weighted_abs_error"], arc_r2_2024=e["r2"],
                                arc_weighted_abs_error_net_2024=e["weighted_abs_error_net"])
    pd.concat(arcs, ignore_index=True).to_csv(out / "arc_flows.csv", index=False, float_format="%.2f")
    pd.concat(binding, ignore_index=True)[["variant", "year", "src", "dst", "mmcfd", "flow", "cap_annual", "utilisation",
                                           "expansion_mmcfd"]] \
        .to_csv(out / "binding_arcs.csv", index=False, float_format="%.3f")
    pd.concat(regs, ignore_index=True).to_csv(out / "region_vs_table64.csv", index=False, float_format="%.1f")
    pd.concat(hubrows, ignore_index=True).to_csv(out / "hub_basin_shares.csv", index=False, float_format="%.6f")
    zc = pd.DataFrame(zrows)
    zc.to_csv(out / "zone_shares_vs_stage1.csv", index=False, float_format="%.6f")
    s = pd.DataFrame(summ)
    s.to_csv(out / "summary.csv", index=False, float_format="%.4f")
    over = obs.merge(cap0, on=["src", "dst"], how="left")
    over["cap_annual"] = over.mmcfd * 366
    for rr in over[over.cap_annual.isna() & (over.value > 1000)].itertuples():
        flags.append(dict(year=inp.year, flag="observed_arc_without_capacity", what=f"{rr.src}->{rr.dst}",
                          note=f"{rr.value / 1e3:.0f} Bcf observed; no EIA capacity row"))
    for rr in over[over.value > over.cap_annual].itertuples():
        flags.append(dict(year=inp.year, flag="observed_flow_above_capacity", what=f"{rr.src}->{rr.dst}",
                          note=f"{rr.value / 1e3:.0f} Bcf observed vs {rr.cap_annual / 1e3:.0f} Bcf/yr capacity"))
    pd.DataFrame(flags).to_csv(out / "flags.csv", index=False)
    log(f"stage2a: util {U}, hurdle {H} km; years {YEARS}; " +
        "; ".join(f"{r.year} {r.variant}: unserved {r.unserved_bcf:.0f} Bcf, binding {r.binding_arcs}" for r in s.itertuples()))
    return {"summary": s, "grid": grid, "zones": zc}
