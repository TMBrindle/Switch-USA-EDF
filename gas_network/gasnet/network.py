"""Build the state-hub gas network for the base year (EIA) and roll it forward to model years (AEO2026 indices).

Nodes: 47 states + DC, Texas as TX_E/TX_N/TX_W, and GOM (data/reference/hubs.csv). Supply (hub, basin, value MMcf):
dry production by production area (data/reference/production_areas.csv; Texas districts, NM East/West and LA North/South
split by the proved-reserves report's 2024 production) and international receipts (Canada by crossing, LNG, Mexico).
Flows: EIA gross directional state-to-state receipts, Texas pairs moved to sub-hubs, plus intra-Texas transfers.

Projection (ASSUMPTION, CHANGES §101): every quantity keeps its 2024 base and moves with the AEO2026 growth from 2025
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


SAN_JUAN_PROVINCE = 22          # USGS province code = HSM play // 100 (play 2205 = San Juan Basin mesaverde, ...)
SAN_JUAN_COUNTIES = {"CO": {"La Plata", "Archuleta", "Montezuma", "San Juan"},
                     "UT": {"San Juan"}, "AZ": {"Apache"}, "NM": {"San Juan", "Rio Arriba", "Sandoval", "McKinley"}}


def hsm_san_juan(nems_dir: Path, state: str) -> dict:
    """San Juan Basin vs other gas in a state, from the AEO2026 HSM onshore *producing* project files (EIA, NEMS
    1bfbb2a): GP1 = each project's first projection-year gas from wells already producing (MMcf). Model-based county
    volumes; observed county production (ECMC) is not reachable from cloud sessions (CHANGES §102).
    San Juan = USGS province 22 projects in the basin's counties. Returns {'san_juan', 'other', 'province22_elsewhere'}."""
    d = pd.concat([pd.read_csv(nems_dir / f"on_projects_{k}.csv", skiprows=1, low_memory=False,
                               usecols=["state", "cnty_name", "play", "GP1"]) for k in ("producing_gas", "producing_oil")])
    d = d[d.state == state]
    prov22 = (pd.to_numeric(d.play, errors="coerce") // 100) == SAN_JUAN_PROVINCE
    incty = d.cnty_name.isin(SAN_JUAN_COUNTIES.get(state, set()))
    sj = float(d[prov22 & incty].GP1.sum())
    return {"san_juan": sj, "other": float(d.GP1.sum()) - sj, "province22_elsewhere": float(d[prov22 & ~incty].GP1.sum())}


def _area_shares(inp: Inputs) -> pd.DataFrame:
    """Production area -> share of its state's dry production."""
    rows = []
    for st, g in inp.areas.groupby("state"):
        subs = g[g.arr_subdivision != ""]
        if subs.empty:
            for r in g.itertuples():
                rows.append((r.area, r.state, r.hub, r.basin, 1.0))
            continue
        if subs.arr_subdivision.str.startswith("HSM:").all():
            sj = hsm_san_juan(inp.raw / "nems", st)
            vals = {r.area: sj[r.arr_subdivision[4:]] for r in subs.itertuples()}
        else:
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


def _move(tot: float, v0, v1, method: str):
    """New total (MMcf) for a bundle with 2024 total `tot` and Table 64 levels v0 (2025) / v1 (year), Bcf.
    ratio: tot x v1/v0; additive: tot + (v1 - v0), floored at 0. Returns (new, reason or None if moved)."""
    if v0 is None:
        return tot, "not_in_table64_held"
    v1 = v1 or 0.0
    if method == "ratio":
        if v0 == 0:
            return tot, "zero_in_table64_held"
        return tot * v1 / v0, None
    new = tot + (v1 - v0) * 1000.0
    if new < 0:
        return 0.0, "additive_floored_at_0"
    return new, None


def project(inp: Inputs, base: dict, year: int, method: str | None = None) -> dict:
    cfg = inp.cfg
    method = method or cfg.get("projection_method", "additive")
    if method not in ("additive", "ratio"):
        raise ValueError(f"projection_method must be additive or ratio, not {method!r}")
    b0 = int(cfg["aeo_index_base_year"])
    flags = []
    reg = inp.region
    # supply
    sup = base["supply"].copy()
    new = []
    can_arc = {}
    for i, r in sup.iterrows():
        if r.kind == "import_canada":
            st = r.area
            can_arc[i] = ("Canada@WA", "OR_WA") if st == "WA" else ("Canada@ID", "OR_WA") if st == "ID" \
                else ("Canada", reg[st])
    arc_tot = {}
    for i, a in can_arc.items():
        arc_tot[a] = arc_tot.get(a, 0.0) + sup.loc[i, "value"]
    arc_new = {}
    for a, tot in arc_tot.items():
        v0, v1 = _t64(inp, a[0], a[1], b0), _t64(inp, a[0], a[1], year)
        if v0 is None or (method == "ratio" and v0 == 0):
            arc_new[a] = tot
            flags.append((year, "canada_import_held", f"{a[0]}->{a[1]}", tot, f"no usable Table 64 Canada arc ({method})"))
        else:
            arc_new[a], why = _move(tot, v0, v1, method)
            if why:
                flags.append((year, "canada_import_" + why, f"{a[0]}->{a[1]}", tot, method))
    for i, r in sup.iterrows():
        if r.kind == "production":
            new.append(r.value * _index(inp, r.basin, year))
        elif r.kind == "import_lng":
            new.append(r.value * _index(inp, "lng_imports", year))
        elif r.kind == "import_mexico":
            new.append(r.value)
        else:   # Canada: crossing's share of its Table 64 arc
            a = can_arc[i]
            new.append(r.value * arc_new[a] / arc_tot[a] if arc_tot[a] > 0 else 0.0)
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
        newtot, why = _move(tot, v0, v1, method)
        if why:
            if why == "not_in_table64_held":
                note = "held at 2024"
            elif why == "zero_in_table64_held":
                note = f"Table 64 shows 0 in {b0} ({(v1 or 0) * 1000:.0f} MMcf in {year}); held"
            else:
                note = f"2024 {tot:.0f} + Table 64 change {((v1 or 0) - v0) * 1000:.0f} < 0"
            flags.append((year, "arc_" + why, f"{rs}->{rd}", newtot, note))
        if tot > 0:
            factor[g.index] = newtot / tot
        if method == "ratio" and v0 and not (1 / 3 <= tot / (v0 * 1000.0) <= 3):
            flags.append((year, "arc_level_mismatch_indexed", f"{rs}->{rd}", newtot,
                          f"EIA 2024 {tot / 1000:.0f} Bcf vs Table 64 {b0} {v0:.0f} Bcf (>3x apart); indexed "
                          f"to {newtot / 1000:.0f} Bcf (Table 64 {year}: {v1:.0f})"))
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
            "flags": flags, "region_growth": g_r, "method": method}
