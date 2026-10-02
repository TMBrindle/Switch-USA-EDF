"""Readers for EIA-860M and LBNL Queued Up, with county -> ReEDS BA -> transreg mapping."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from .groups import eia_group, queue_group

_SUFFIXES = re.compile(r"\b(county|parish|borough|census area|city and borough|municipality|municipio)\b")
ISO_TRANSREG = {"MISO": "MISO", "PJM": "PJM", "SPP": "SPP", "ISO-NE": "ISONE", "NYISO": "NYISO",
                "ERCOT": "ERCOT", "CAISO": "CAISO"}


def norm_county(name) -> str:
    if pd.isna(name):
        return ""
    s = str(name).lower().strip()
    s = s.replace("saint ", "st ").replace("st. ", "st ").replace("ste. ", "ste ")
    s = _SUFFIXES.sub("", s)
    return re.sub(r"[^a-z0-9]", "", s)


def county_transreg(county2zone: Path, hierarchy: Path) -> pd.DataFrame:
    """FIPS, state, county_key, ba, transreg."""
    c = pd.read_csv(county2zone, dtype={"FIPS": str})
    c["FIPS"] = c["FIPS"].str.zfill(5)
    c["state"] = c["state"].str.upper()
    c["county_key"] = c["county_name"].map(norm_county)
    h = pd.read_csv(hierarchy)[["ba", "transreg"]]
    return c.merge(h, on="ba", how="left")


def _read_sheet(path, sheet) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet, header=2)
    return df[pd.to_numeric(df["Plant ID"], errors="coerce").notna()]


def eia_additions(path: Path, c2t: pd.DataFrame) -> pd.DataFrame:
    """Generators that came online (Operating and Retired sheets): group, transreg, year, mw."""
    frames = []
    for sheet in ("Operating", "Retired"):
        d = _read_sheet(path, sheet)
        frames.append(pd.DataFrame({
            "group": d["Technology"].map(eia_group), "state": d["Plant State"].astype(str).str.upper(),
            "county": d["County"], "year": pd.to_numeric(d["Operating Year"], errors="coerce"),
            "mw": pd.to_numeric(d["Nameplate Capacity (MW)"], errors="coerce")}))
    g = pd.concat(frames, ignore_index=True)
    g = g[g["group"].notna() & g["year"].notna() & g["mw"].notna()]
    key = c2t.drop_duplicates(["state", "county_key"]).set_index(["state", "county_key"])["transreg"]
    g["transreg"] = [key.get((s, norm_county(c))) for s, c in zip(g["state"], g["county"])]
    g["year"] = g["year"].astype(int)
    return g


def load_queued_up(path: Path) -> pd.DataFrame:
    sheet = "03. Complete Queue Data"
    raw = pd.read_excel(path, sheet_name=sheet, header=None, nrows=5)
    hdr = next(i for i in range(len(raw)) if "q_id" in raw.iloc[i].astype(str).tolist())
    q = pd.read_excel(path, sheet_name=sheet, header=hdr, usecols=lambda c: not str(c).startswith("Unnamed"))
    for c in ("mw_1", "mw_2", "mw_3", "q_year", "prop_year"):
        q[c] = pd.to_numeric(q[c], errors="coerce")
    q["on_year"] = pd.to_datetime(q["on_date"], errors="coerce").dt.year
    q["ia_year"] = pd.to_datetime(q["ia_date"], errors="coerce").dt.year
    return q


def queue_components(q: pd.DataFrame, c2t: pd.DataFrame) -> pd.DataFrame:
    """One row per (request, technology component): hybrids split into their parts (mw_1..3)."""
    fips = q["fips_code"].map(lambda f: None if pd.isna(f) else str(int(float(f))).zfill(5))
    tr = fips.map(c2t.drop_duplicates("FIPS").set_index("FIPS")["transreg"])
    tr = tr.fillna(q["region"].map(ISO_TRANSREG))
    rows = []
    for k in (1, 2, 3):
        part = pd.DataFrame({"req": q.index, "group": q[f"type_{k}"].map(queue_group), "mw": q[f"mw_{k}"],
                             "transreg": tr, "status": q["q_status"].astype(str).str.lower(),
                             "phase": q["IA_phase_clean"].astype(str), "q_year": q["q_year"],
                             "prop_year": q["prop_year"], "on_year": q["on_year"], "ia_year": q["ia_year"]})
        rows.append(part[part["group"].notna() & (part["mw"] > 0)])
    return pd.concat(rows, ignore_index=True)
