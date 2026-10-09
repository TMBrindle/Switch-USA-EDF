"""Switch load zone -> gas hub weights.

Every ReEDS zone lies in one state (hierarchy.csv `st`), so a zone takes its state's hub. Texas zones are split over
TX_E/TX_N/TX_W: county -> zone from ReEDS county2zone.csv; county -> RRC district from NEMS HSM onshore project files
(state, cnty_fips, rrc; the most frequent district per county); district -> hub as NGMM (production_areas.csv). Each
Texas zone's weights are its operating gas-fired nameplate MW by hub (EIA-860M, Energy Source Code NG). A zone with no
gas plants in mapped counties falls back to its count of counties by hub (flagged).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from . import eia

# HSM `rrc` codes. 73 = 7B and 72 = 7C: checked against RRC's published District 7B county list (all 19 of its
# counties present in the HSM files carry code 73; Spraberry-trend counties Upton, Reagan, Irion carry 72).
RRC_CODE = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 72: "7C", 73: "7B", 8: "8", 81: "8A", 9: "9", 10: "10"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", str(s).lower())


def _km(lat, lon, lats, lons):
    import numpy as np
    p = np.pi / 180
    a = (np.sin((lats - lat) * p / 2) ** 2 + np.cos(lat * p) * np.cos(lats * p) * np.sin((lons - lon) * p / 2) ** 2)
    return 12742 * np.arcsin(np.sqrt(a))


def tx_county_district(nems_dir: Path) -> pd.DataFrame:
    """county_fips (5-digit str), county_name, district, n_projects, n_other (projects listing another district)."""
    rows = []
    for p in sorted(nems_dir.glob("on_projects_*.csv")):
        d = pd.read_csv(p, skiprows=1, low_memory=False,
                        usecols=lambda c: c in ("state", "cnty_fips", "cnty_name", "rrc"))
        if not {"state", "cnty_fips", "rrc"} <= set(d.columns):
            continue   # on_projects_undiscovered.csv has no county or RRC columns
        d = d[(d.state.astype(str).str.strip() == "TX") & d.rrc.notna() & d.cnty_fips.notna()]
        rows.append(d)
    d = pd.concat(rows, ignore_index=True)
    d["district"] = d.rrc.astype(int).map(RRC_CODE)
    d["county_fips"] = d.cnty_fips.astype(int).map(lambda x: f"48{x:03d}")
    c = d.groupby(["county_fips", "district"]).size().rename("n").reset_index()
    c = c.sort_values(["county_fips", "n", "district"], ascending=[True, False, True])
    tot = c.groupby("county_fips").n.sum()
    top = c.drop_duplicates("county_fips").copy()
    top["n_projects"] = top.county_fips.map(tot)
    top["n_other"] = top.n_projects - top.n
    names = d.drop_duplicates("county_fips").set_index("county_fips").cnty_name
    top["county_name"] = top.county_fips.map(names)
    return top[["county_fips", "county_name", "district", "n_projects", "n_other"]].reset_index(drop=True)


def zone_hub_weights(cfg: dict, areas: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list]:
    """(weights: zone, hub, weight, basis; tx_counties diagnostic; flags)."""
    h = pd.read_csv(cfg["paths"]["hierarchy"])
    h = h[h.country == "USA"]
    rows, flags = [], []
    for z, st in zip(h.ba, h.st):
        if st != "TX":
            rows.append((z, st, 1.0, "state"))
    raw = Path(cfg["paths"]["raw"])
    cd = tx_county_district(raw / "nems")
    dist_hub = {a.replace("TX_D", ""): hub for a, hub in zip(areas.area, areas.hub) if a.startswith("TX_D")}
    cd["hub"] = cd.district.map(dist_hub)
    c2z = pd.read_csv(cfg["paths"]["county2zone"], dtype={"FIPS": str})
    c2z = c2z[c2z.state == "TX"].assign(county_fips=lambda d: d.FIPS.str.zfill(5))
    tx = c2z.merge(cd[["county_fips", "district", "hub", "n_projects", "n_other"]], on="county_fips", how="left")
    cap = eia.gas_capacity_by_county(raw / "eia" / "august_generator2026.xlsx", "TX")
    cap["key"] = cap.county.map(_norm)
    tx["key"] = tx.county_name.map(_norm)
    tx = tx.merge(cap[["key", "mw"]], on="key", how="left").fillna({"mw": 0.0})
    unmatched = set(cap.key) - set(tx.key)
    if unmatched:
        flags.append(("tx_860m_county_unmatched", ",".join(sorted(unmatched)),
                      float(cap[cap.key.isin(unmatched)].mw.sum()), "860M county name not in county2zone"))
    # counties with gas plants but no district in the HSM files: hub of the nearest generator (EIA-860M coordinates)
    # in a county that has one. ASSUMPTION, flagged.
    nod = tx[tx.hub.isna() & (tx.mw > 0)]
    if not nod.empty:
        gens = eia.generators(raw / "eia" / "august_generator2026.xlsx", "TX")
        gens["key"] = gens.county.map(_norm)
        hub_of = dict(zip(tx.key, tx.hub))
        gens["hub"] = gens.key.map(hub_of)
        ref = gens[gens.hub.notna()]
        for i, r in nod.iterrows():
            mine = gens[(gens.key == r.key) & (gens.esc == "NG")]
            votes = {}
            for g in mine.itertuples():
                dist = _km(g.lat, g.lon, ref.lat.values, ref.lon.values)
                j = dist.argmin()
                votes[ref.hub.values[j]] = votes.get(ref.hub.values[j], 0.0) + g.mw
                last = (ref.county.values[j], float(dist[j]))
            hub = max(votes, key=votes.get)
            tx.loc[i, "hub"] = hub
            tx.loc[i, "district"] = "nearest:" + last[0]
            flags.append(("tx_county_hub_by_nearest_plant", r.county_name, float(r.mw),
                          f"{hub} (nearest mapped plant in {last[0]} county, {last[1]:.0f} km)"))
    tzones = sorted(set(h[h.st == "TX"].ba))
    for z in tzones:
        g = tx[(tx.ba == z) & tx.hub.notna()]
        w = g.groupby("hub").mw.sum()
        basis = "gas_mw_860m"
        if w.sum() <= 0:
            w = g.groupby("hub").size().astype(float)
            basis = "county_count"
            flags.append(("tx_zone_no_gas_mw", z, 0.0, "weights by county count"))
        for hub, v in (w / w.sum()).items():
            rows.append((z, hub, float(v), basis))
    wt = pd.DataFrame(rows, columns=["zone", "hub", "weight", "basis"])
    return wt, tx.drop(columns=["key"]), flags
