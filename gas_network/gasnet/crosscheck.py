"""Region-level cross-checks (the 11 NGMM flow regions of AEO Table 64).

region_shares(): for each region and year, three basin mixes
  state_model       throughput-weighted mean of the region's hub shares from the state-level trace
  region_same_flows region-level trace on the state model's own flows aggregated to regions (isolates the effect of
                    mixing within a region)
  region_table64    region-level trace with Table 64's absolute interregional flows and Canada arcs (isolates the effect
                    of indexing EIA 2024 arcs instead of taking NGMM's levels; base year uses Table 64's 2025)
arcs(): EIA 2024 state arcs aggregated to Table 64 region pairs against Table 64 2025, and the projected bundles.
"""
from __future__ import annotations

import pandas as pd

from . import tracing


def _region_supply(inp, net):
    s = net["supply"].assign(region=net["supply"].hub.map(inp.region))
    return s.groupby(["region", "basin"], as_index=False).value.sum().rename(columns={"region": "hub"})


def _region_flows(inp, net):
    f = net["flows"].assign(rs=net["flows"].src.map(inp.region), rd=net["flows"].dst.map(inp.region))
    f = f[f.rs != f.rd]
    return f.groupby(["rs", "rd"], as_index=False).value.sum().rename(columns={"rs": "src", "rd": "dst"})


def region_shares(inp, net, hub_shares: pd.DataFrame, basins, t64_year: int) -> pd.DataFrame:
    regions = sorted(set(inp.region.values()))
    hs = hub_shares.assign(region=hub_shares.hub.map(inp.region)).dropna(subset=["share"])
    hs = hs.assign(w=hs.share * hs.throughput)
    sm = hs.groupby(["region", "basin"]).w.sum() / hs.groupby(["region", "basin"]).throughput.sum()
    out = sm.rename("state_model").reset_index()
    rs = tracing.trace(_region_supply(inp, net), _region_flows(inp, net), regions, basins)
    out = out.merge(rs.rename(columns={"hub": "region", "share": "region_same_flows"})[
        ["region", "basin", "region_same_flows"]], on=["region", "basin"], how="outer")
    # Table 64 absolute flows; Canada arcs replace the state model's Canada imports
    sup = _region_supply(inp, net)
    sup = sup[~sup.basin.isin(["w_canada", "e_canada"])]
    can = net["supply"][net["supply"].kind == "import_canada"].assign(region=lambda d: d.hub.map(inp.region))
    t = inp.t64[inp.t64.year == t64_year]
    rows = []
    for r in t.itertuples():
        if r.src.startswith("Canada"):
            if r.src == "Canada@ID":
                basin_mix = {"w_canada": 1.0}
            elif r.src == "Canada@WA":
                basin_mix = {"w_canada": 1.0}
            else:
                c = can[can.region == r.dst].groupby("basin").value.sum()
                basin_mix = (c / c.sum()).to_dict() if c.sum() > 0 else {"e_canada": 1.0}
            for b, x in basin_mix.items():
                rows.append((r.dst, b, r.bcf * 1000 * x))
    sup = pd.concat([sup, pd.DataFrame(rows, columns=["hub", "basin", "value"])], ignore_index=True)
    fl = t[~t.src.str.startswith("Canada")].rename(columns={"bcf": "value"})[["src", "dst", "value"]]
    fl = fl.assign(value=fl.value * 1000)
    rt = tracing.trace(sup, fl, regions, basins)
    out = out.merge(rt.rename(columns={"hub": "region", "share": "region_table64"})[
        ["region", "basin", "region_table64"]], on=["region", "basin"], how="outer")
    out["table64_year"] = t64_year
    return out


def arcs(inp, base, nets: dict) -> pd.DataFrame:
    f = base["flows"].assign(rs=base["flows"].src.map(inp.region), rd=base["flows"].dst.map(inp.region))
    eia = f[f.rs != f.rd].groupby(["rs", "rd"]).value.sum().div(1000).rename("eia_2024_bcf")
    t = inp.t64[~inp.t64.src.str.startswith("Canada")]
    t = t.pivot_table(index=["src", "dst"], columns="year", values="bcf", aggfunc="sum")
    out = pd.DataFrame(eia)
    out.index.names = ["src", "dst"]
    out = out.join(t[[2025]].rename(columns={2025: "t64_2025_bcf"}), how="outer")
    for y, n in nets.items():
        g = n["flows"].assign(rs=n["flows"].src.map(inp.region), rd=n["flows"].dst.map(inp.region))
        out[f"model_{y}_bcf"] = g[g.rs != g.rd].groupby(["rs", "rd"]).value.sum().div(1000)
        if y in t.columns:
            out[f"t64_{y}_bcf"] = t[y]
    return out.reset_index()
