"""Zonal coal capacity-factor caps from public EIA-923 / EIA-860 data, 2021-24 (item 3).

Method (same rules as the B8 test step, s0_workflow/case_aliases/b8_coalcf.py, which read PUDL):
  Units: coal generators (EIA-860 energy source 1 in COAL_CODES) in the latest EIA-860 year's Operable
    sheet, status OP, with no planned retirement before `online_year` (2035).
  Unit-year CF = annual net generation (EIA-923 Page 4, sum of the 12 monthly values) /
    (winter capacity in that year's EIA-860 x hours in the year). Winter capacity is the basis
    PowerGenome uses for model capacity (resources.yml capacity_col).
  Unit-years skipped: the first year in service, the retirement year, missing or partial generation
    (fewer than 12 monthly values), zero winter capacity.
  Unit max = highest valid CF over the years (clipped to [0, 1]).
  Zone cap = winter-capacity-weighted mean of unit maxima over units with history; plants mapped to
    ReEDS zones by (state, county) with county2zone.csv. The table keeps every zone with history;
    the case writer gives zones with < min_history_mw (500 MW) the fallback (0.65).

`fetch_coal_cf_eia923.py` downloads the files and writes data/coal_cf_caps_eia923_2021_2024.csv.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

COAL_CODES = {"ANT", "BIT", "LIG", "SUB", "SGC", "WC", "RC"}   # PUDL's fuel_type_code_pudl == "coal"


def _norm_county(name) -> str:
    if pd.isna(name):
        return ""
    s = str(name).lower().strip()
    s = s.replace("saint ", "st ").replace("st. ", "st ").replace("ste. ", "ste ")
    s = re.sub(r"\s+(county|parish|borough|census area|city and borough|municipality|municipio|city)$", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def read_860_generators(path: Path, sheet: str = "Operable") -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet, header=1, dtype={"Generator ID": str})
    df = df[pd.to_numeric(df["Plant Code"], errors="coerce").notna()].copy()
    df["Plant Code"] = df["Plant Code"].astype(int)
    for c in ("Nameplate Capacity (MW)", "Winter Capacity (MW)", "Operating Year", "Planned Retirement Year"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def read_923_generation(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="Page 4 Generator Data", header=5, dtype={"Generator Id": str})
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    df = df[pd.to_numeric(df["Plant Id"], errors="coerce").notna()].copy()
    df["Plant Id"] = df["Plant Id"].astype(int)
    months = [c for c in df.columns if c.startswith("Net Generation ") and "Year To Date" not in c]
    assert len(months) == 12, months
    m = df[months].apply(pd.to_numeric, errors="coerce")
    return pd.DataFrame({"plant": df["Plant Id"], "gen": df["Generator Id"].astype(str).str.strip(),
                         "year": pd.to_numeric(df["YEAR"], errors="coerce").astype(int),
                         "mwh": m.sum(axis=1, min_count=1), "months": m.notna().sum(axis=1)})


def zone_caps(gens_by_year: dict[int, pd.DataFrame], latest: pd.DataFrame, retired: pd.DataFrame | None,
              generation: pd.DataFrame, county2zone: pd.DataFrame, years=(2021, 2024), online_year=2035
              ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(zone table, unit table). gens_by_year: EIA-860 Operable sheet per year; latest: the latest
    year's Operable sheet; retired: its 'Retired and Canceled' sheet (retirement years)."""
    key = lambda df: df["Plant Code"].astype(str) + "|" + df["Generator ID"].astype(str).str.strip()
    lat = latest.assign(k=key(latest))
    units = lat[lat["Energy Source 1"].isin(COAL_CODES) & (lat["Status"].astype(str).str.strip() == "OP")
                & (lat["Planned Retirement Year"].isna() | (lat["Planned Retirement Year"] >= online_year))].copy()
    ret_year = {}
    if retired is not None and "Retirement Year" in retired:
        r = retired.assign(k=key(retired))
        ret_year = dict(zip(r["k"], pd.to_numeric(r["Retirement Year"], errors="coerce")))
    caps = []
    for y, g in gens_by_year.items():
        if not (years[0] <= y <= years[1]):
            continue
        caps.append(g.assign(k=key(g), year=y)[["k", "year", "Winter Capacity (MW)", "Nameplate Capacity (MW)"]])
    cap = pd.concat(caps)
    gen = generation.assign(k=generation["plant"].astype(str) + "|" + generation["gen"])
    uy = units[["k", "Operating Year"]].merge(cap, on="k").merge(gen[["k", "year", "mwh", "months"]],
                                                                 on=["k", "year"], how="left")
    hrs = np.where(uy["year"] % 4 == 0, 8784, 8760)
    uy["cf_winter"] = uy["mwh"] / (uy["Winter Capacity (MW)"] * hrs)
    uy["cf_nameplate"] = uy["mwh"] / (uy["Nameplate Capacity (MW)"] * hrs)
    uy["valid"] = (uy["mwh"].notna() & (uy["months"] >= 12) & (uy["Operating Year"] != uy["year"])
                   & (uy["k"].map(ret_year) != uy["year"]) & (uy["Winter Capacity (MW)"] > 0))
    um = uy[uy["valid"]].groupby("k").agg(cf_max=("cf_winter", "max"), cf_max_np=("cf_nameplate", "max"),
                                           years=("year", "nunique")).reset_index()
    um[["cf_max", "cf_max_np"]] = um[["cf_max", "cf_max_np"]].clip(0, 1)
    units = units.merge(um, on="k", how="left")
    c2z = county2zone.assign(key=county2zone["state"].str.upper() + "|" + county2zone["county_name"].map(_norm_county))
    zmap = dict(zip(c2z["key"], c2z["ba"]))
    units["zone"] = (units["State"].str.upper() + "|" + units["County"].map(_norm_county)).map(zmap)
    h = units[units["zone"].notna() & units["cf_max"].notna()]
    # plain aggregations (no groupby.apply include_groups): pandas 1.4 and 2.x
    w = h.assign(_wcf=h["cf_max"] * h["Winter Capacity (MW)"], _ncf=h["cf_max_np"] * h["Nameplate Capacity (MW)"])
    zc = w.groupby("zone").agg(hist_mw=("Winter Capacity (MW)", "sum"), n_units=("cf_max", "size"),
                               _wcf=("_wcf", "sum"), _np=("Nameplate Capacity (MW)", "sum"), _ncf=("_ncf", "sum"))
    zc["cap_cf"] = zc._wcf / zc.hist_mw
    zc["cap_cf_nameplate"] = zc._ncf / zc._np
    zc = zc[["hist_mw", "n_units", "cap_cf", "cap_cf_nameplate"]].reset_index().rename(columns={"zone": "ba"})
    zc["n_units"] = zc["n_units"].astype(int)
    return zc, units
