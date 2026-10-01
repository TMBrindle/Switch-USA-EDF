"""County <-> ReEDS zone (BA) mapping."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

_SUFFIXES = re.compile(
    r"\b(county|parish|borough|census area|city and borough|municipality|municipio)\b"
)


def norm_county(name) -> str:
    """Normalise a county name so EIA, LBNL and ReEDS spellings match."""
    if pd.isna(name):
        return ""
    s = str(name).lower().strip()
    s = s.replace("saint ", "st ").replace("st. ", "st ").replace("ste. ", "ste ")
    s = _SUFFIXES.sub("", s)
    # drop spaces, hyphens and punctuation: "Miami-Dade" == "miami dade", "LaSalle" == "la salle"
    return re.sub(r"[^a-z0-9]", "", s)


def load_county2zone(path: str | Path) -> pd.DataFrame:
    c2z = pd.read_csv(path, dtype={"FIPS": str})
    c2z["FIPS"] = c2z["FIPS"].str.zfill(5)
    c2z["county_key"] = c2z["county_name"].map(norm_county)
    c2z["state"] = c2z["state"].str.upper()
    return c2z


def load_hierarchy(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path)


def attach_ba(
    df: pd.DataFrame,
    c2z: pd.DataFrame,
    state_col: str = "state",
    county_col: str | None = "county",
    fips_col: str | None = None,
) -> pd.DataFrame:
    """Add `fips` and `ba` columns. Uses FIPS when present, otherwise state+county name.

    Returns a copy; unmatched rows keep ba = NaN so match rates can be reported.
    """
    out = df.copy()
    if fips_col and fips_col in out and out[fips_col].notna().any():
        f = out[fips_col].astype("Int64").astype(str).str.zfill(5)
        out["fips"] = f.where(out[fips_col].notna())
        out = out.merge(c2z[["FIPS", "ba"]], left_on="fips", right_on="FIPS", how="left").drop(columns="FIPS")
        return out
    key = c2z[["state", "county_key", "FIPS", "ba"]].drop_duplicates(["state", "county_key"])
    out["_state"] = out[state_col].astype(str).str.upper().str.strip()
    out["_ck"] = out[county_col].map(norm_county)
    out = out.merge(key, left_on=["_state", "_ck"], right_on=["state", "county_key"], how="left",
                    suffixes=("", "_c2z"))
    # Fallback for states served by a single ReEDS zone (e.g. CT, whose counties were replaced by planning regions)
    single = c2z.groupby("state")["ba"].nunique()
    single_ba = c2z[c2z["state"].isin(single[single == 1].index)].drop_duplicates("state").set_index("state")["ba"]
    miss = out["ba"].isna()
    out.loc[miss, "ba"] = out.loc[miss, "_state"].map(single_ba)
    out = out.rename(columns={"FIPS": "fips"}).drop(columns=["_state", "_ck", "county_key"])
    if "state_c2z" in out:
        out = out.drop(columns="state_c2z")
    return out


def match_report(df: pd.DataFrame, weight: str | None = None) -> str:
    hit = df["ba"].notna()
    if weight:
        w = df[weight].fillna(0)
        return f"{hit.mean():.1%} of rows, {w[hit].sum() / max(w.sum(), 1e-9):.1%} of {weight} mapped to a ReEDS zone"
    return f"{hit.mean():.1%} of rows mapped to a ReEDS zone"
