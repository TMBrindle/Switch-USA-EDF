"""Stage 2a arc capacity by year (CHANGES §103), MMcf/d on the Stage 1 hub topology.

Base (end of 2025):
  * Interstate: EIA U.S. State-to-State Capacity, January 2026, sheet "Pipeline State2State Capacity" (per pipeline and
    county pair). Each pipeline row takes its 2025 value, or its 2024 value if the 2025 row is missing (EIA file gaps,
    e.g. Texas Eastern AL->TN), flagged. Texas endpoints go to TX_E/TX_N/TX_W by county (Stage 1 tx_county_hubs);
    "Gulf of America*" -> GOM. Canadian and Mexican endpoints are not arcs: imports are supply at the crossing state
    and exports demand there (their capacity is not limiting in Stage 2a; flagged in the guide).
  * Intra-Texas: NGMM texas_capacity.txt 2024 rows (includes Matterhorn Express in TX_W->TX_E 10,880).
Additions (data/reference/capacity_projects.csv, from the EIA pipeline projects tracker, August 2026):
  status rule (Tom, §103): Completed / Part Completed / Construction count from November of the in-service year;
  Approved only with include_approved (sensitivity); everything else excluded. Annual capacity in the in-service year
  = 2/12 of the addition (November-December); full from the next year.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import eia

IN = ("Completed", "Part Completed", "Construction")
TX_HUBS = ("TX_E", "TX_N", "TX_W")
STATE_CODE = dict(eia.STATE_NAMES)   # "Texas" -> "TX"


def _norm(s) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalpha())


def tx_county_hub_map(tx_county_hubs: pd.DataFrame, zone_weights: pd.DataFrame) -> tuple[dict, list]:
    """County (normalised name) -> TX hub. Counties without an RRC district (no HSM project, no gas plant) take the
    dominant hub of their ReEDS zone (flagged)."""
    top = zone_weights.sort_values("weight").drop_duplicates("zone", keep="last").set_index("zone").hub
    m, flags = {}, []
    for r in tx_county_hubs.itertuples():
        if isinstance(r.hub, str):
            m[_norm(r.county_name)] = r.hub
        elif r.ba in top.index:
            m[_norm(r.county_name)] = top[r.ba]
            flags.append(("capacity_tx_county_hub_by_zone", r.county_name, 0.0, f"{top[r.ba]} (dominant hub of {r.ba})"))
    return m, flags


def eia_base(raw: Path, tx_county_hub: dict, year: int = 2025) -> tuple[pd.DataFrame, list]:
    """src, dst, mmcfd (summed over pipelines), plus flags for carried-forward rows and unmapped Texas counties."""
    t = pd.read_excel(raw / "eia" / "EIA-StatetoStateCapacity_Jan2026.xlsx", sheet_name="Pipeline State2State Capacity",
                      header=1)
    t.columns = ["year", "pipe", "rf", "rt", "sf", "cf", "st", "ct", "cap", "notes"]
    t = t[pd.to_numeric(t.year, errors="coerce").notna()].copy()
    t["year"] = t.year.astype(int)
    t["cap"] = pd.to_numeric(t.cap, errors="coerce").fillna(0.0)
    flags = []

    def hub(state, county):
        s = str(state).strip()
        if s.startswith("Gulf of America") or s.startswith("Gulf of Mexico"):
            return "GOM"
        if s == "Texas":
            h = tx_county_hub.get(_norm(county))
            if h is None:
                flags.append(("capacity_tx_county_unmapped", str(county), 0.0, "Texas county not in tx_county_hubs"))
            return h
        return STATE_CODE.get(s)          # None for Canadian provinces, Mexico

    key = ["pipe", "sf", "cf", "st", "ct"]
    piv = t.pivot_table(index=key, columns="year", values="cap", aggfunc="sum")
    rows = []
    for k, r in piv.iterrows():
        v = r.get(year)
        if pd.isna(v) and pd.notna(r.get(year - 1)):
            v = r.get(year - 1)
            flags.append(("capacity_carried_forward", f"{k[0]}: {k[1]}->{k[3]}", float(v), f"{year} row missing; {year - 1} used"))
        if pd.isna(v) or v <= 0:
            continue
        s, d = hub(k[1], k[2]), hub(k[3], k[4])
        if s and d and s != d:
            rows.append((s, d, float(v)))
    df = pd.DataFrame(rows, columns=["src", "dst", "mmcfd"]).groupby(["src", "dst"], as_index=False).mmcfd.sum()
    return df, flags


def ngmm_texas(raw: Path, year: int = 2024) -> pd.DataFrame:
    """Intra-Texas arcs (TX_* -> TX_*) from NGMM texas_capacity.txt, HistoricalCapacity rows for `year`."""
    rows = []
    for line in open(raw / "nems" / "texas_capacity.txt", encoding="utf-8-sig"):
        p = line.split("!")[0].split()
        if len(p) == 4 and p[0] in TX_HUBS and p[1] in TX_HUBS and p[2] == str(year):
            rows.append((p[0], p[1], float(p[3])))
    return pd.DataFrame(rows, columns=["src", "dst", "mmcfd"])


def projects(ref: Path) -> pd.DataFrame:
    return pd.read_csv(ref / "capacity_projects.csv", keep_default_na=False)


def capacity(raw: Path, ref: Path, tx_county_hub: dict, year: int, include_approved: bool = False):
    """Arc capacity (src, dst, mmcfd) in model year `year` and flags. Base is end-2025; the 2024 run removes
    additions dated 2025 with the same November rule (only Golden Pass and Wisconsin Reliability are dated 2025)."""
    base, flags = eia_base(raw, tx_county_hub)
    base = pd.concat([base, ngmm_texas(raw)], ignore_index=True)
    pr = projects(ref)
    ok = pr.status.isin(IN) | (include_approved & (pr.status == "Approved"))
    add = []
    for r in pr[ok].itertuples():
        isy = int(r.in_service_year)
        if r.in_base_2025 == "yes":          # already in the EIA/NGMM base: only matters for the 2024 run
            if year < isy:
                add.append((r.src, r.dst, -float(r.mmcfd)))
            elif year == isy:
                add.append((r.src, r.dst, -float(r.mmcfd) * 10 / 12))
            continue
        if year > isy:
            add.append((r.src, r.dst, float(r.mmcfd)))
        elif year == isy:
            add.append((r.src, r.dst, float(r.mmcfd) * 2 / 12))
    cap = pd.concat([base, pd.DataFrame(add, columns=["src", "dst", "mmcfd"])], ignore_index=True)
    cap = cap.groupby(["src", "dst"], as_index=False).mmcfd.sum()
    cap["mmcfd"] = cap.mmcfd.clip(lower=0.0)
    return cap[cap.mmcfd > 0].reset_index(drop=True), flags
