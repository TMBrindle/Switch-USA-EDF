"""Load and clean LBNL generator interconnection cost data (emp.lbl.gov/interconnection_costs).

LBNL publishes one workbook per region/study (MISO, PJM, SPP, NYISO, ISO-NE, CAISO, and a
non-ISO "study BAs" release). Their headers differ a little between releases, so column
names are resolved through the candidate lists in config.yaml (lbnl.columns).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import linkage

FIELDS = ["project_id", "qu_id", "region", "owner", "state", "county", "fips", "tech", "capacity_mw",
          "queue_year", "status", "service", "cost_year", "poi_cost", "network_cost", "total_cost"]
REQUIRED = ["state", "tech", "capacity_mw", "queue_year", "status", "network_cost"]


def _find_header(raw: pd.DataFrame, candidates: dict, max_rows: int) -> int:
    """Return the row index that contains the most candidate column names."""
    names = {c.lower() for v in candidates.values() for c in v}
    best, best_hits = 0, -1
    for i in range(min(max_rows, len(raw))):
        hits = sum(str(x).strip().lower() in names for x in raw.iloc[i].tolist())
        if hits > best_hits:
            best, best_hits = i, hits
    return best


def _pick_sheet(xl: pd.ExcelFile, hints: list[str]) -> str:
    for h in hints:
        for s in xl.sheet_names:
            if h.lower() in s.lower():
                return s
    return xl.sheet_names[0]


def read_workbook(path: Path, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Read one LBNL workbook and return (standardised frame, resolved column map)."""
    lc = cfg["lbnl"]
    xl = pd.ExcelFile(path)
    sheet = _pick_sheet(xl, lc["sheet_hint"])
    raw = pd.read_excel(xl, sheet_name=sheet, header=None, nrows=lc["header_search_rows"])
    hdr = _find_header(raw, lc["columns"], lc["header_search_rows"])
    df = pd.read_excel(xl, sheet_name=sheet, header=hdr)
    lower = {str(c).strip().lower(): c for c in df.columns}
    resolved = {}
    for field, cands in lc["columns"].items():
        for c in cands:
            if c.lower() in lower:
                resolved[field] = lower[c.lower()]
                break
    out = pd.DataFrame({f: df[resolved[f]] if f in resolved else np.nan for f in FIELDS})
    out["queue_year"] = _to_year(out["queue_year"])
    if "cost_year" not in resolved:
        out["cost_year"] = _dollar_year(path, resolved, lc)
    out["source_file"] = path.name
    out["source_sheet"] = sheet
    if pd.isna(out["region"]).all():
        out["region"] = path.stem  # fall back to file name (e.g. "MISO")
    elif "owner" not in resolved:
        out["owner"] = out["region"]  # non-ISO release: the balancing authority is the transmission owner
    return out, resolved


def _to_year(s: pd.Series) -> pd.Series:
    """Year from a year or a date column (LBNL gives queue dates; SPP gives the year only)."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.year.astype(float)
    num = pd.to_numeric(s, errors="coerce")
    if num.dropna().between(1900, 2100).all():
        return num
    return pd.to_datetime(s, errors="coerce").dt.year.astype(float)


def _dollar_year(path: Path, resolved: dict, lc: dict) -> float:
    """Dollar year of a workbook's costs: config lbnl.cost_dollar_year, checked against any "$YYYY" header."""
    in_header = {int(m.group(1)) for f in ("poi_cost", "network_cost", "total_cost") if f in resolved
                 for m in [re.match(r"\s*\$(\d{4})\b", str(resolved[f]))] if m}
    configured = (lc.get("cost_dollar_year") or {}).get(path.stem)
    if len(in_header) > 1 or (configured and in_header and in_header != {configured}):
        raise ValueError(f"{path.name}: cost headers say ${sorted(in_header)}, config lbnl.cost_dollar_year "
                         f"says {configured}")
    year = configured or next(iter(in_header), None)
    return float(year) if year else np.nan


def inspect(cfg: dict) -> str:
    """Report sheet/header resolution for every workbook so config.yaml can be fixed up."""
    lines = []
    for p in sorted(Path(cfg["paths"]["lbnl_dir"]).glob("*.xls*")):
        xl = pd.ExcelFile(p)
        df, resolved = read_workbook(p, cfg)
        missing = [f for f in REQUIRED if f not in resolved]
        if df["cost_year"].isna().all():
            missing.append("cost_year (no column; add the file to lbnl.cost_dollar_year)")
        lines.append(f"\n== {p.name}\n  sheets: {xl.sheet_names}\n  using sheet: {df['source_sheet'].iat[0]}"
                     f"\n  resolved: {resolved}\n  cost dollar year: {sorted(int(y) for y in df['cost_year'].dropna().unique())}"
                     f"\n  MISSING required: {missing or 'none'}")
        raw = pd.read_excel(xl, sheet_name=df["source_sheet"].iat[0], header=None,
                            nrows=cfg["lbnl"]["header_search_rows"])
        hdr = _find_header(raw, cfg["lbnl"]["columns"], cfg["lbnl"]["header_search_rows"])
        lines.append(f"  header row {hdr}: {[str(x) for x in raw.iloc[hdr].tolist()]}")
    return "\n".join(lines) if lines else f"No workbooks found in {cfg['paths']['lbnl_dir']}"


def _map_values(s: pd.Series, mapping: dict, default=None) -> pd.Series:
    low = s.astype(str).str.lower()
    out = pd.Series(default, index=s.index, dtype=object)
    # hybrid first so "solar + storage" is not classed as solar; other next so "biogas" is not gas
    order = sorted(mapping, key=lambda k: (k != "hybrid", k != "other"))
    for k in order:
        hit = out.isna() & low.apply(lambda x: any(tok in x for tok in mapping[k]))
        out[hit] = k
    return out


def _deflate(values: pd.Series, from_year: pd.Series, to_year: int, cpi: pd.DataFrame) -> pd.Series:
    c = cpi.set_index("year")["cpi"]
    fy = from_year.fillna(to_year).astype(int).clip(c.index.min(), c.index.max())
    return values * c.loc[min(to_year, c.index.max())] / fy.map(c)


def clean(frames: list[pd.DataFrame], cfg: dict, c2z: pd.DataFrame) -> pd.DataFrame:
    lc, ec = cfg["lbnl"], cfg["estimation"]
    df = pd.concat(frames, ignore_index=True)
    for c in ("capacity_mw", "queue_year", "cost_year", "poi_cost", "network_cost", "total_cost"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if lc["costs_are_total_dollars"]:
        for c in ("poi_cost", "network_cost", "total_cost"):
            df[c] = df[c] / (df["capacity_mw"] * 1000)
    df["status_n"] = _map_values(df["status"], lc["status_map"])
    df["tech_n"] = _map_values(df["tech"], lc["tech_map"])
    df["offshore_wind"] = df["tech"].astype(str).str.contains("offshore", case=False)
    svc = df["service"].astype(str).str.upper()
    # PJM's service types are "Capacity" and "Energy" (energy-only)
    is_energy = svc.str.contains(r"\bERIS\b|ENERGY[ -]ONLY|ENERGY RESOURCE|^\s*ENERGY\s*$", regex=True)
    extra = {str(v).strip().upper() for v in lc.get("service_eris_extra") or []}
    is_energy |= svc.str.strip().isin(extra)
    df["service_n"] = np.where(is_energy, "ERIS", "NRIS")  # NRIS/CRIS/capacity is the default
    cpi = pd.read_csv(cfg["paths"]["cpi"])
    year_basis = df["cost_year"].fillna(df["queue_year"])
    for c in ("poi_cost", "network_cost", "total_cost"):
        df[c + "_real"] = _deflate(df[c], year_basis, cfg["dollar_year"], cpi)
    df = linkage.link(df, c2z, cfg)
    # one regime label set for run and fit-weights: LBNL region/BA -> regime (config regimes.lbnl_to_regime)
    aliases = cfg.get("regimes", {}).get("lbnl_to_regime", {})
    df["regime"] = df[ec["regime_column"]].astype(str).str.strip().map(lambda r: aliases.get(r, r))
    return df


def estimation_sample(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    ec = cfg["estimation"]
    s = df[df["status_n"].isin(ec["sample_statuses"]) & df["tech_n"].isin(ec["techs"])
           & (df["ba"].notna() | df["ba_multi"]) & df["network_cost_real"].notna() & df["queue_year"].notna()
           & (df["capacity_mw"] >= ec["min_capacity_mw"])
           & ~(df["offshore_wind"] & ec.get("exclude_offshore_wind", True))].copy()
    cap = s["network_cost_real"].quantile(ec["winsorize_pct"])
    s["network_cost_real"] = s["network_cost_real"].clip(lower=0, upper=cap)
    return s


def load_all(cfg: dict, c2z: pd.DataFrame) -> pd.DataFrame:
    paths = sorted(Path(cfg["paths"]["lbnl_dir"]).glob("*.xls*"))
    if not paths:
        raise FileNotFoundError(
            f"No LBNL workbooks in {cfg['paths']['lbnl_dir']}. Download the project-level cost files "
            "from https://emp.lbl.gov/interconnection_costs and place them there.")
    return clean([read_workbook(p, cfg)[0] for p in paths], cfg, c2z)
