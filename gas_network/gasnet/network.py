"""Build the state-hub gas network for the base year (EIA) and roll it forward to model years (AEO2026 indices).

Nodes: 47 states + DC, Texas as TX_E/TX_N/TX_W, and GOM (data/reference/hubs.csv). Supply (hub, basin, value MMcf):
dry production by production area (data/reference/production_areas.csv; Texas districts, NM East/West and LA North/South
split by the proved-reserves report's 2024 production) and international receipts (Canada by crossing, LNG, Mexico).
Flows: EIA gross directional state-to-state receipts, Texas pairs moved to sub-hubs, plus intra-Texas transfers.

Projection (ASSUMPTION, CHANGES §93): every quantity keeps its 2024 base and moves with the AEO2026 growth from 2025
(the first AEO2026 table year) to the model year:
  production by basin  x  AEO series(y) / AEO series(2025)          (config basin_aeo_index)
  Canada imports       x  Table 64 Canada arc(y) / (2025)            (by crossing state's region; WA, ID own rows)
  interregional arcs   x  Table 64 region-pair(y) / (2025)           (bundle of state pairs crossing that pair)
  intra-region arcs    x  growth of the region's supply + interregional inflow (state model)
  GOM outflows         x  Fed. GOM index
  Texas intra arcs     re-routed each year (texas.py) with indexed dispositions.
Arcs Table 64 does not list, or lists as 0 in 2025 (no index), are held at 2024 and flagged (flags list).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import aeo, eia, fetch, texas

WEST_REGIONS = {"CA", "OR_WA", "RockiesPlains", "AZ_NM", "NorthernGreatPlains"}


class Inputs:
    """Everything read from data/raw and data/reference, once."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        raw, ref = Path(cfg["paths"]["raw"]), Path(cfg["paths"]["reference"])
        self.raw, self.ref = raw, ref
        self.hubs = pd.read_csv(ref / "hubs.csv", keep_default_na=False)
        self.areas = pd.read_csv(ref / "production_areas.csv", keep_default_na=False)
        self.region = dict(zip(self.hubs.hub, self.hubs.flow_region))
        self.region["TX"] = "SouthCentral"
        self.year = int(cfg["base_year"])
        e = raw / "eia"
        self.dry = eia.dry_production(e / "NG_PROD_SUM_A_EPG0_FPD_MMCF_A.xls", self.year)
        self.arr = eia.arr_production(e / "ARR_2024_TABLES_ALL.xlsx")
        self.flows_state, self.intl = eia.movements(e / "movements", fetch.STATES, self.year)
        self.tx_intl_del = eia.international_deliveries(e / "movements", "TX", self.year)
        self.tx_cons = eia.consumption(e / "NG_CONS_SUM_DCU_STX_A.xls", self.year)
        a = raw / "aeo2026"
        self.t64 = aeo.table64_arcs(a / "suptab_64.xlsx", pd.read_csv(ref / "table64_regions.csv"))
        self.aeo_datekey = aeo.datekey(a / "suptab_64.xlsx")
        self._aeo = {}

    def aeo_series(self, table: str, code: str) -> pd.Series:
        k = (table, code)
        if k not in self._aeo:
            self._aeo[k] = aeo.series(self.raw / "aeo2026" / f"{table}.xlsx", code)
        return self._aeo[k]


def _area_shares(inp: Inputs) -> pd.DataFrame:
    """Production area -> share of its state's dry production."""
    rows = []
    for st, g in inp.areas.groupby("state"):
        subs = g[g.arr_subdivision != ""]
        if subs.empty:
            for r in g.itertuples():
                rows.append((r.area, r.state, r.hub, r.basin, 1.0))
            continue
        arr = inp.arr[inp.arr.state == st].set_index("subdivision").bcf
        vals = {r.area: sum(float(arr[s]) for s in r.arr_subdivision.split(";")) for r in subs.itertuples()}
        tot = sum(vals.values())
        for r in subs.itertuples():
            rows.append((r.area, r.state, r.hub, r.basin, vals[r.area] / tot))
    return pd.DataFrame(rows, columns=["area", "state", "hub", "basin", "share_of_state"])


def _canada_basin(inp: Inputs, state: str) -> str:
    b = inp.hubs.set_index("hub").canada_basin.get(state, "")
    if b:
        return b
    return "w_canada" if inp.region.get(state) in WEST_REGIONS else "e_canada"


def base_network(inp: Inputs) -> dict:
    cfg, tx = inp.cfg, inp.cfg["texas"]
    flags = []
    # supply: production
    sh = _area_shares(inp)
    sup = []
    for st, v in inp.dry.items():
        if st in ("AK", "HI"):
            continue
        a = sh[sh.state == st]
        if a.empty:
            sup.append((st, "other", v, "production", st))
        for r in a.itertuples():
            sup.append((r.hub, r.basin, v * r.share_of_state, "production", r.area))
    # supply: international receipts
    for r in inp.intl.itertuples():
        hub = "TX_E" if r.state == "TX" else r.state
        if r.country == "Canada":
            sup.append((hub, _canada_basin(inp, r.state), r.mmcf, "import_canada", r.state))
        elif r.country == "Mexico":
            sup.append((hub, "other", r.mmcf, "import_mexico", r.state))
        else:
            sup.append((hub, "lng_imports", r.mmcf, "import_lng", r.state))
    supply = pd.DataFrame(sup, columns=["hub", "basin", "value", "kind", "area"])
    # flows: interstate, Texas pairs to sub-hubs
    f = inp.flows_state.rename(columns={"mmcf": "value"})
    flows = texas.assign_interstate(f, tx)
    flows["kind"] = "interstate"
    # Texas balance and intra-Texas routing
    tx_routes, tx_info = _route_texas(inp, supply, flows, cons=inp.tx_cons,
                                      mexico=float(inp.tx_intl_del.get("Mexico", 0.0)),
                                      lng=float(inp.tx_intl_del.drop("Mexico", errors="ignore").sum()),
                                      days=366 if inp.year % 4 == 0 else 365)
    flows = pd.concat([flows, tx_routes.assign(kind="intra_texas")[["src", "dst", "value", "kind"]]], ignore_index=True)
    return {"year": inp.year, "supply": supply, "flows": flows, "texas": tx_info, "tx_routes": tx_routes,
            "flags": flags, "tx_base": {"cons": dict(inp.tx_cons), "mexico": float(inp.tx_intl_del.get("Mexico", 0.0)),
                                        "lng": float(inp.tx_intl_del.drop("Mexico", errors="ignore").sum())}}


def _route_texas(inp, supply, flows, cons, mexico, lng, days):
    tx = inp.cfg["texas"]
    prod = supply[(supply.kind == "production") & supply.hub.isin(texas.HUBS)].groupby("hub").value.sum().to_dict()
    imp = supply[(supply.kind != "production") & supply.hub.isin(texas.HUBS)].groupby("hub").value.sum().to_dict()
    rec = flows[flows.dst.isin(texas.HUBS) & ~flows.src.isin(texas.HUBS)].groupby("dst").value.sum().to_dict()
    dlv = flows[flows.src.isin(texas.HUBS) & ~flows.dst.isin(texas.HUBS)].groupby("src").value.sum().to_dict()
    receipts = {h: rec.get(h, 0.0) + imp.get(h, 0.0) for h in texas.HUBS}
    disp = texas.dispositions(cons, prod, receipts, dlv, mexico, lng, tx)
    sup = {h: prod.get(h, 0.0) + receipts[h] for h in texas.HUBS}
    routes, info = texas.route(sup, disp, tx, days)
    info.update({"production": prod, "receipts": receipts, "deliveries": dlv, "mexico_exports": mexico,
                 "lng_exports": lng, "consumption": dict(cons)})
    return routes, info


def _index(inp: Inputs, basin: str, year: int) -> float:
    table, code = inp.cfg["basin_aeo_index"][basin]
    s = inp.aeo_series(table, code)
    b = int(inp.cfg["aeo_index_base_year"])
    return float(s[year] / s[b])


def _t64(inp: Inputs, src: str, dst: str, year: int) -> float | None:
    r = inp.t64[(inp.t64.src == src) & (inp.t64.dst == dst) & (inp.t64.year == year)]
    return None if r.empty else float(r.bcf.sum())


def project(inp: Inputs, base: dict, year: int) -> dict:
    cfg = inp.cfg
    b0 = int(cfg["aeo_index_base_year"])
    flags = []
    reg = inp.region
    # supply
    sup = base["supply"].copy()
    new = []
    for r in sup.itertuples():
        if r.kind == "production":
            new.append(r.value * _index(inp, r.basin, year))
        elif r.kind == "import_lng":
            new.append(r.value * _index(inp, "lng_imports", year))
        elif r.kind == "import_mexico":
            new.append(r.value)
        else:   # Canada
            st = r.area
            src, dst = ("Canada@WA", "OR_WA") if st == "WA" else ("Canada@ID", "OR_WA") if st == "ID" \
                else ("Canada", reg[st])
            v0, v1 = _t64(inp, src, dst, b0), _t64(inp, src, dst, year)
            if v0 is None or v0 == 0:
                new.append(r.value)
                flags.append((year, "canada_import_held", st, r.value, f"no Table 64 Canada arc into {dst} in {b0}"))
            else:
                new.append(r.value * v1 / v0)
    sup["value"] = new
    held_mx = base["supply"][base["supply"].kind == "import_mexico"].value.sum()
    if held_mx > 0:
        flags.append((year, "mexico_import_held", "", held_mx, "AEO2026 Table 61 has no pipeline imports from Mexico"))
    # flows
    fl = base["flows"][base["flows"].kind != "intra_texas"].copy()
    fl["rs"] = fl.src.map(reg).fillna("SouthCentral")
    fl["rd"] = fl.dst.map(reg).fillna("SouthCentral")
    factor = pd.Series(1.0, index=fl.index)
    gom = fl.src == "GOM"
    factor[gom] = _index(inp, "fed_gom", year)
    inter = (fl.rs != fl.rd) & ~gom
    for (rs, rd), g in fl[inter].groupby(["rs", "rd"]):
        if (rs, rd) == ("RockiesPlains", "OR_WA"):
            # Table 64 lists Canada (through Idaho) separately from Rockies; the ID->OR/WA state arcs carry both
            v0 = sum(_t64(inp, s, "OR_WA", b0) or 0 for s in ("RockiesPlains", "Canada@ID"))
            v1 = sum(_t64(inp, s, "OR_WA", year) or 0 for s in ("RockiesPlains", "Canada@ID"))
        else:
            v0, v1 = _t64(inp, rs, rd, b0), _t64(inp, rs, rd, year)
        tot = float(g.value.sum())
        if v0 is None:
            flags.append((year, "arc_not_in_table64_held", f"{rs}->{rd}", tot, "held at 2024"))
        elif v0 == 0:
            flags.append((year, "arc_zero_in_table64_held", f"{rs}->{rd}", tot,
                          f"Table 64 shows 0 in {b0} ({(v1 or 0) * 1000:.0f} MMcf in {year}); held at 2024"))
        else:
            factor[g.index] = v1 / v0
            if not (1 / 3 <= tot / (v0 * 1000.0) <= 3):
                flags.append((year, "arc_level_mismatch_indexed", f"{rs}->{rd}", tot * v1 / v0,
                              f"EIA 2024 {tot / 1000:.0f} Bcf vs Table 64 {b0} {v0:.0f} Bcf (>3x apart); indexed "
                              f"to {tot * v1 / v0 / 1000:.0f} Bcf (Table 64 {year}: {v1:.0f})"))
    fl["value"] = fl.value * factor
    # intra-region growth
    sup["region"] = sup.hub.map(reg)
    sb = base["supply"].assign(region=base["supply"].hub.map(reg))
    infl1 = fl[inter].groupby("rd").value.sum()
    fb = base["flows"][base["flows"].kind != "intra_texas"]
    fb = fb.assign(rs=fb.src.map(reg).fillna("SouthCentral"), rd=fb.dst.map(reg).fillna("SouthCentral"))
    infl0 = fb[(fb.rs != fb.rd) & (fb.src != "GOM")].groupby("rd").value.sum()
    g_r = {}
    for r in set(fl.rs) | set(fl.rd):
        num = sup[sup.region == r].value.sum() + infl1.get(r, 0.0)
        den = sb[sb.region == r].value.sum() + infl0.get(r, 0.0)
        g_r[r] = num / den if den > 0 else 1.0
    intra = (fl.rs == fl.rd) & ~gom
    fl.loc[intra, "value"] = fl.loc[intra, "value"] * fl.loc[intra, "rs"].map(g_r)
    fl = fl[["src", "dst", "value", "kind"]]
    # Texas
    tb = base["tx_base"]
    cons = dict(tb["cons"])
    for use, code in [("residential", "NGC000:ba_WestSouthCent"), ("commercial", "NGC000:ca_WestSouthCent"),
                      ("industrial", "NGC000:da_WestSouthCent"), ("electric", "NGC000:ea_WestSouthCent"),
                      ("vehicle", "NGC000:fa_WestSouthCent")]:
        s = inp.aeo_series("suptab_62", code)
        cons[use] = cons.get(use, 0.0) * float(s[year] / s[b0])
    p0 = base["supply"][(base["supply"].kind == "production") & base["supply"].hub.isin(texas.HUBS)].value.sum()
    p1 = sup[(sup.kind == "production") & sup.hub.isin(texas.HUBS)].value.sum()
    for use in ("lease", "plant", "pipeline"):
        cons[use] = cons.get(use, 0.0) * p1 / p0
    mx = inp.aeo_series("suptab_61", "NGI000:da_PipelineExpor")
    lng = inp.aeo_series("suptab_61", "NGI000:da_LiquefiedNatu")
    routes, info = _route_texas(inp, sup, fl, cons, tb["mexico"] * float(mx[year] / mx[b0]),
                                tb["lng"] * float(lng[year] / lng[b0]), days=365)
    fl = pd.concat([fl, routes.assign(kind="intra_texas")[["src", "dst", "value", "kind"]]], ignore_index=True)
    return {"year": year, "supply": sup.drop(columns="region"), "flows": fl, "texas": info, "tx_routes": routes,
            "flags": flags, "region_growth": g_r}
