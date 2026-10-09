"""Parsers for the EIA workbooks pinned in data/SOURCES.yml (Natural Gas Annual dnav files, proved-reserves report,
EIA-860M). Quantities are returned in MMcf unless a name says otherwise."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

STATE_NAMES = {
    "Alabama": "AL", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "Alaska": "AK", "Hawaii": "HI"}


def _year_row(path: Path, sheet: str, year: int) -> pd.Series:
    d = pd.read_excel(path, sheet_name=sheet, header=2)
    yr = pd.to_datetime(d["Date"], errors="coerce").dt.year
    rows = d[yr == year]
    if rows.empty:
        raise ValueError(f"{path.name} [{sheet}] has no {year} row (latest {int(yr.max())})")
    return rows.iloc[0]


def _sheets(path: Path) -> list[str]:
    return [s for s in pd.ExcelFile(path).sheet_names if s.startswith("Data")]


def dry_production(path: Path, year: int) -> pd.Series:
    """Dry production by state (2-letter) plus 'GOM' (Federal Offshore Gulf of America/Mexico), MMcf."""
    out = {}
    for s in _sheets(path):
        for c, v in _year_row(path, s, year).items():
            m = re.match(r"^(.*?)\s+Dry Natural Gas Production", str(c))
            if not m or pd.isna(v):
                continue
            nm = m.group(1).strip()
            if nm in STATE_NAMES:
                out[STATE_NAMES[nm]] = float(v)
            elif nm.startswith("Federal Offshore") and "Gulf" in nm:
                out["GOM"] = out.get("GOM", 0.0) + float(v)
    return pd.Series(out, name="dry_mmcf")


_INTERSTATE = re.compile(r"Interstate (?:Movements: )?Receipts [Ff]rom (.+?) \(")
_INTERSTATE_DEL = re.compile(r"Interstate (?:Movements: )?Deliveries [Tt]o (.+?) \(")
_INTL = re.compile(r"(?:International Receipts|Imports \+ Intransit) [Ff]rom (.+?) \(")
_INTL_DEL = re.compile(r"(?:International Deliveries|Exports \+ Intransit) [Tt]o (.+?) \(")


def movements(mov_dir: Path, states, year: int):
    """(flows, intl) from each state's receipts sheet: flows = src, dst, mmcf (src may be 'GOM');
    intl = state, country, mmcf (international receipts by country of origin)."""
    flows, intl = [], []
    for st in states:
        p = mov_dir / f"NG_MOVE_IST_A2DCU_S{st}_A.xls"
        r = _year_row(p, "Data 1", year)
        for c, v in r.items():
            if c == "Date" or pd.isna(v) or float(v) == 0:
                continue
            c = str(c)
            m = _INTERSTATE.search(c)
            if m:
                src = m.group(1).strip()
                if src.startswith("Federal Offshore"):
                    flows.append(("GOM", st, float(v)))
                elif src in STATE_NAMES:
                    flows.append((STATE_NAMES[src], st, float(v)))
                continue
            m = _INTL.search(c)
            if m and m.group(1).strip() != "All Countries":
                intl.append((st, m.group(1).strip(), float(v)))
    f = pd.DataFrame(flows, columns=["src", "dst", "mmcf"]).groupby(["src", "dst"], as_index=False).mmcf.sum()
    i = pd.DataFrame(intl, columns=["state", "country", "mmcf"]).groupby(["state", "country"], as_index=False).mmcf.sum()
    return f, i


def international_deliveries(mov_dir: Path, st: str, year: int) -> pd.Series:
    """International deliveries (exports) from a state by destination country, MMcf."""
    p = mov_dir / f"NG_MOVE_IST_A2DCU_S{st}_A.xls"
    r = _year_row(p, "Data 2", year)
    out = {}
    for c, v in r.items():
        m = _INTL_DEL.search(str(c))
        if m and pd.notna(v) and m.group(1).strip() != "All Countries":
            out[m.group(1).strip()] = out.get(m.group(1).strip(), 0.0) + float(v)
    return pd.Series(out, dtype=float)


def consumption(path: Path, year: int) -> dict:
    """State consumption by end use (MMcf): residential, commercial, industrial, electric, vehicle, lease, plant,
    pipeline (pipeline and distribution use), total."""
    r = _year_row(path, "Data 1", year)
    keys = {"Residential Consumption": "residential", "Commercial Consumers": "commercial",
            "Industrial Consumption": "industrial", "Electric Power Consumers": "electric",
            "Vehicle Fuel Consumption": "vehicle", "Lease Fuel Consumption": "lease",
            "Plant Fuel Consumption": "plant", "Pipeline and Distribution Use": "pipeline",
            "Total Consumption": "total"}
    out = {}
    for c, v in r.items():
        for k, name in keys.items():
            if k in str(c) and pd.notna(v):
                out[name] = float(v)
    return out


def arr_production(path: Path) -> pd.DataFrame:
    """Proved-reserves report Table 8: 'Estimated production' (2024, wet after lease separation, Bcf) by state and
    subdivision. Returns state, subdivision ('' for the state total), bcf."""
    d = pd.read_excel(path, sheet_name="8", header=None)
    hdr = d.index[d.iloc[:, 0].astype(str).str.strip() == "State and subdivision"][0]
    col = [i for i in range(d.shape[1]) if "Estimated production" in str(d.iloc[hdr - 1, i])][0]
    rows, state = [], None
    for i in range(hdr + 1, d.shape[0]):
        name = str(d.iloc[i, 0]).strip()
        v = d.iloc[i, col]
        if name in ("nan", "") or pd.isna(pd.to_numeric(v, errors="coerce")):
            continue
        if name in STATE_NAMES:
            state = STATE_NAMES[name]
            rows.append((state, "", float(v)))
        elif name.startswith(("Federal Offshore", "Lower 48", "U.S. Total", "Other States")):
            state = None
            rows.append((name, "", float(v)))
        elif state is not None:
            rows.append((state, name, float(v)))
        else:
            rows.append(("Federal Offshore", name, float(v)))
    return pd.DataFrame(rows, columns=["state", "subdivision", "bcf"])


def gas_capacity_by_county(path: Path, state: str) -> pd.DataFrame:
    """EIA-860M operating generators burning natural gas (Energy Source Code NG): county, nameplate MW."""
    d = pd.read_excel(path, sheet_name="Operating", header=2)
    d = d[(d["Plant State"] == state) & (d["Energy Source Code"] == "NG")]
    d = d.assign(mw=pd.to_numeric(d["Nameplate Capacity (MW)"], errors="coerce"))
    return d.groupby("County", as_index=False).mw.sum().rename(columns={"County": "county"})


def generators(path: Path, state: str) -> pd.DataFrame:
    """EIA-860M operating generators in a state: county, esc (energy source code), mw, lat, lon."""
    d = pd.read_excel(path, sheet_name="Operating", header=2)
    d = d[d["Plant State"] == state]
    return pd.DataFrame({"county": d["County"].values, "esc": d["Energy Source Code"].values,
                         "mw": pd.to_numeric(d["Nameplate Capacity (MW)"], errors="coerce").values,
                         "lat": pd.to_numeric(d["Latitude"], errors="coerce").values,
                         "lon": pd.to_numeric(d["Longitude"], errors="coerce").values}).dropna(subset=["lat", "lon"])
