"""Basin production totals implied by a traced network, against AEO2026 Tables 59/60.

For each hub h: throughput T_h (own supply + receipts), outflow O_h, local disposition L_h = T_h - O_h (consumption,
exports, storage, losses at h). The basin total implied by the traced shares is  sum_h L_h s[h, b];  with every
L_h >= 0 it equals node supply of basin b exactly. A projection whose scaled arcs make a hub ship more than it has
(L_h < 0, "overdraw") breaks that identity, so the gap traced - supply measures how far a method's flows are from a
consistent network, and traced - AEO measures the total miss against the published production.
"""
from __future__ import annotations

import pandas as pd

# basin -> (table, row codes summed): the nearest published AEO2026 series (not exact definitions; see guide)
AEO_COMPARE = {
    "appalachia": ("suptab_60", ["SGTO000:select_appalachia"]),
    "haynesville": ("suptab_60", ["SGTO000:select_haynes"]),
    "permian": ("suptab_60", ["SGTO000:select_permian"]),
    "eagle_ford": ("suptab_60", ["SGTO000:select_eagleford"]),
    "barnett_other_tx": ("suptab_60", ["SGTO000:select_barnett"]),
    "bakken": ("suptab_60", ["SGTO000:select_bakken"]),
    "midcon_anadarko": ("suptab_59", ["L48000:ca_Midcontinent"]),
    "san_juan+rockies_other": ("suptab_59", ["L48000:ca_RockyMountain"]),
    "fed_gom": ("suptab_59", ["L48000:da_Gulf"]),
    "lower48_production": ("suptab_59", ["L48000:ba_Lower48Total"]),
}
DOMESTIC = ["appalachia", "haynesville", "permian", "eagle_ford", "barnett_other_tx", "midcon_anadarko", "san_juan",
            "rockies_other", "bakken", "fed_gom", "other"]


def basin_totals(net: dict, traced: pd.DataFrame) -> pd.DataFrame:
    """basin, supply_mmcf, traced_mmcf, plus overdraw_mmcf (same on every row)."""
    fl = net["flows"]
    out = fl.groupby("src").value.sum()
    t = traced.dropna(subset=["share"]).copy()
    T = t.drop_duplicates("hub").set_index("hub").throughput
    L = T - out.reindex(T.index).fillna(0.0)
    t["traced"] = t.share * t.hub.map(L)
    traced_b = t.groupby("basin").traced.sum()
    sup_b = net["supply"].groupby("basin").value.sum()
    df = pd.DataFrame({"supply_mmcf": sup_b, "traced_mmcf": traced_b}).fillna(0.0)
    df["overdraw_mmcf"] = float(L[L < 0].sum())
    df["overdrawn_hubs"] = ",".join(sorted(L[L < -1].index))
    return df.rename_axis("basin").reset_index()


def compare(inp, net: dict, traced: pd.DataFrame, year: int, method: str) -> pd.DataFrame:
    b = basin_totals(net, traced).set_index("basin")
    rows = []
    groups = {k: k.split("+") for k in AEO_COMPARE if k != "lower48_production"}
    groups["lower48_production"] = DOMESTIC
    aeo_year = 2025 if year == inp.cfg["base_year"] else year
    for key, members in groups.items():
        table, codes = AEO_COMPARE[key]
        aeo = sum(float(inp.aeo_series(table, c)[aeo_year]) for c in codes) * 1e6   # Tcf -> MMcf
        sup = float(b.reindex(members).supply_mmcf.fillna(0).sum())
        tr = float(b.reindex(members).traced_mmcf.fillna(0).sum())
        rows.append(dict(method=method, year=year, basin=key, aeo_table=table, aeo_year=aeo_year, aeo_mmcf=aeo,
                         supply_mmcf=sup, traced_mmcf=tr, traced_minus_supply_mmcf=tr - sup,
                         traced_minus_aeo_pct=100 * (tr - aeo) / aeo if aeo else float("nan"),
                         supply_minus_aeo_pct=100 * (sup - aeo) / aeo if aeo else float("nan"),
                         overdraw_mmcf=float(b.overdraw_mmcf.iloc[0]), overdrawn_hubs=b.overdrawn_hubs.iloc[0]))
    return pd.DataFrame(rows)
