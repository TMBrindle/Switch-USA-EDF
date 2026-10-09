"""AEO2026 supplemental tables (run cb2026.d021826b): series by row code, and Table 64 flow arcs."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


def _table(path: Path) -> tuple[pd.DataFrame, dict]:
    d = pd.read_excel(path, header=None)
    yr_row = next(i for i in range(15) if any(str(v).strip() == "2050" or str(v).strip() == "2050.0"
                                               for v in d.iloc[i, 2:].tolist()))
    years = {}
    for c in range(2, d.shape[1]):
        v = pd.to_numeric(d.iloc[yr_row, c], errors="coerce")
        if pd.notna(v) and 1990 < v < 2100:
            years[c] = int(v)
    return d, years


def datekey(path: Path) -> str:
    d = pd.read_excel(path, header=None, nrows=8)
    for i in range(8):
        if str(d.iloc[i, 2]).strip() == "Datekey":
            return str(d.iloc[i, 3]).strip()
    return ""


def series(path: Path, code: str) -> pd.Series:
    """Values by year for the row whose first column is `code` (e.g. 'SGTO000:select_permian')."""
    d, years = _table(path)
    rows = d.index[d.iloc[:, 0].astype(str).str.strip() == code]
    if len(rows) != 1:
        raise KeyError(f"{path.name}: {len(rows)} rows with code {code}")
    r = rows[0]
    return pd.Series({y: float(d.iloc[r, c]) for c, y in years.items()}, name=code)


def table64_arcs(path: Path, region_labels: pd.DataFrame) -> pd.DataFrame:
    """Long table: code, src (flow region or Canada@X), dst (flow region), year, bcf."""
    d, years = _table(path)
    lab = dict(zip(region_labels.label, region_labels.flow_region))
    clean = lambda s: re.sub(r"\s*\d+/\s*$", "", str(s).strip()).rstrip(":").strip()
    out, dst = [], None
    for i in range(d.shape[0]):
        c0, c1 = str(d.iloc[i, 0]).strip(), str(d.iloc[i, 1]).strip()
        m = re.match(r"Into (.+?) from", c1)
        if m and c0 in ("nan", ""):
            dst = lab[clean(m.group(1))]
            continue
        if c0.startswith("NGF000:") and dst:
            src = lab[clean(c1)]
            for c, y in years.items():
                out.append((c0, src, dst, y, float(d.iloc[i, c])))
    return pd.DataFrame(out, columns=["code", "src", "dst", "year", "bcf"])
