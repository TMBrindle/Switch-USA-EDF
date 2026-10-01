"""Place LBNL cost-data projects in ReEDS zones.

MISO, PJM and SPP cost workbooks have no county, so each project is located by a cascade
(the first step that succeeds wins; `ba_source` records which):

  lbnl_county        the workbook's own state + county
  queued_up          LBNL "Queued Up" county (FIPS) for the same normalised queue ID, within the
                     same region/entity and state (lbnl.queued_up_scope)
  crosswalk          data/reference/lbnl_county_crosswalk.csv for county strings that are not a
                     single county (each entry records its evidence)
  state_single_zone  the state is served by one ReEDS zone
  owner_single       the project's transmission owner (same workbook, same state) has located
  owner_multi        projects in one zone / several zones. owner_multi rows get no `ba`; they
                     carry `ba_candidates` ("p1:0.6|p2:0.4", MW shares of the owner's located
                     projects) and take the weighted mean saturation of those zones.

Rows that no step places keep ba = NaN and drop out of the estimation sample.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .geo import norm_county

LOCATED = ("lbnl_county", "queued_up", "crosswalk")


def norm_qid(x) -> str | None:
    """Queue ID key: upper case, letters and digits only ("Q007 - 061" == "Q007-061")."""
    if pd.isna(x):
        return None
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    return re.sub(r"[^A-Z0-9]", "", str(x).upper()) or None


def _fips5(x) -> str | None:
    if pd.isna(x):
        return None
    return str(int(float(x))).zfill(5)


@lru_cache(maxsize=2)
def load_queued_up(path: str, sheet: str) -> pd.DataFrame:
    """LBNL Queued Up project list: region, entity, queue-ID key, state, FIPS."""
    if not Path(path).exists():
        raise FileNotFoundError(f"LBNL Queued Up workbook not found: {path}")
    raw = pd.read_excel(path, sheet_name=sheet, header=None, nrows=5)
    hdr = next(i for i in range(len(raw)) if "q_id" in raw.iloc[i].astype(str).tolist())
    q = pd.read_excel(path, sheet_name=sheet, header=hdr, usecols=lambda c: not str(c).startswith("Unnamed"))
    return pd.DataFrame({"region": q["region"].astype(str), "entity": q["entity"].astype(str),
                         "k": q["q_id"].map(norm_qid), "state": q["state"].astype(str).str.upper().str.strip(),
                         "fips": q["fips_code"].map(_fips5)})


def _queued_up_fips(df: pd.DataFrame, cfg: dict, root: Path) -> pd.Series:
    """FIPS from Queued Up for each row, or None (no match, or IDs that map to several counties)."""
    scope = cfg["lbnl"].get("queued_up_scope") or {}
    out = pd.Series(None, index=df.index, dtype=object)
    todo = df["region"].astype(str).isin(scope)
    if not todo.any():
        return out
    q = load_queued_up(str(root / cfg["paths"]["queued_up"]), cfg["lbnl"]["queued_up_sheet"])
    q = q.dropna(subset=["k", "fips"])
    uniq = q.groupby(["region", "entity", "k", "state"])["fips"].agg(lambda s: s.iat[0] if s.nunique() == 1 else None)
    key = df["qu_id"].map(norm_qid).fillna(df["project_id"].map(norm_qid))
    for region, sc in scope.items():
        rows = todo & (df["region"].astype(str) == region)
        if not rows.any():
            continue
        col, val = next(iter(sc.items()))           # {region: MISO} or {entity: Duke}
        lvl = "region" if col == "region" else "entity"
        sub = uniq[uniq.index.get_level_values(lvl) == val].droplevel(["region", "entity"])
        idx = list(zip(key[rows], df.loc[rows, "_state"]))
        out[rows] = [sub.get(i) if i[0] else None for i in idx]
    return out


def link(df: pd.DataFrame, c2z: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    root = Path(cfg.get("_root", "."))
    out = df.copy()
    out["_state"] = out["state"].fillna("").astype(str).str.upper().str.strip()
    f2b = c2z.set_index("FIPS")["ba"]
    fips = pd.Series(None, index=out.index, dtype=object)
    src = pd.Series(None, index=out.index, dtype=object)

    def take(new: pd.Series, label: str):
        new = new.where(new.isin(f2b.index))     # only FIPS that ReEDS knows
        hit = fips.isna() & new.notna()
        fips[hit] = new[hit]
        src[hit] = label

    if "fips" in out and out["fips"].notna().any():
        take(out["fips"].map(_fips5), "lbnl_county")
    key = c2z.drop_duplicates(["state", "county_key"]).set_index(["state", "county_key"])["FIPS"]
    take(pd.Series([key.get((s, norm_county(c))) for s, c in zip(out["_state"], out["county"])],
                   index=out.index, dtype=object), "lbnl_county")
    recode = cfg["lbnl"].get("fips_recode") or {}
    take(_queued_up_fips(out, cfg, root).map(lambda f: recode.get(f, f)), "queued_up")

    xw_path = cfg["paths"].get("county_crosswalk")
    if xw_path and (root / xw_path).exists():
        xw = pd.read_csv(root / xw_path, dtype=str, comment="#").dropna(subset=["fips"])
        xw = xw.fillna("")
        xk = {(r.source_file, r.state.upper().strip(), r.county_raw.strip()): r.fips.zfill(5) for r in xw.itertuples()}
        take(pd.Series([xk.get((f, s, c)) for f, s, c in zip(out["source_file"], out["_state"],
                                                             out["county"].fillna("").astype(str).str.strip())],
                       index=out.index, dtype=object), "crosswalk")

    out["fips"] = fips
    out["ba"] = fips.map(f2b)
    out["ba_source"] = src

    nz = c2z.groupby("state")["ba"].nunique()
    single = c2z[c2z["state"].isin(nz[nz == 1].index)].drop_duplicates("state").set_index("state")["ba"]
    miss = out["ba"].isna() & out["_state"].isin(single.index)
    out.loc[miss, "ba"] = out.loc[miss, "_state"].map(single)
    out.loc[miss, "ba_source"] = "state_single_zone"

    # transmission owner footprint, from this workbook's located projects in the same state
    out["ba_candidates"] = None
    loc = out[out["ba_source"].isin(LOCATED) & out["owner"].notna()]
    foot = (loc.assign(mw=loc["capacity_mw"].fillna(0).clip(lower=0) + 1e-9)
            .groupby(["source_file", "owner", "_state", "ba"])["mw"].sum())
    miss = out["ba"].isna() & out["owner"].notna()
    for i, r in out[miss].iterrows():
        try:
            w = foot.loc[(r["source_file"], r["owner"], r["_state"])]
        except KeyError:
            continue
        w = w / w.sum()
        if len(w) == 1:
            out.at[i, "ba"] = w.index[0]
            out.at[i, "ba_source"] = "owner_single"
        else:
            out.at[i, "ba_candidates"] = "|".join(f"{b}:{s:.4f}" for b, s in w.sort_values(ascending=False).items())
            out.at[i, "ba_source"] = "owner_multi"
    out["ba_multi"] = out["ba_source"].eq("owner_multi")
    # clustering label: the zone, or for owner_multi rows the largest candidate zone
    out["ba_cluster"] = out["ba"].fillna(out["ba_candidates"].str.split(":").str[0])
    return out.drop(columns="_state")


def candidates_long(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (project row, candidate zone, weight) for owner_multi rows."""
    m = df[df["ba_candidates"].notna()]
    rows = [(i, b, float(w)) for i, c in m["ba_candidates"].items()
            for b, w in (p.split(":") for p in c.split("|"))]
    return pd.DataFrame(rows, columns=["row", "ba", "w"])


def match_table(df: pd.DataFrame, by: str) -> pd.DataFrame:
    """MW and row shares placed in one zone, and including owner_multi, by `by`."""
    mw = df["capacity_mw"].fillna(0)
    g = pd.DataFrame({by: df[by], "mw": mw, "rows": 1,
                      "mw_one_zone": mw * df["ba"].notna(), "mw_usable": mw * (df["ba"].notna() | df["ba_multi"]),
                      "rows_usable": (df["ba"].notna() | df["ba_multi"]).astype(int),
                      "mw_missing": df["capacity_mw"].isna().astype(int)}).groupby(by).sum()
    t = pd.DataFrame({"rows": g["rows"], "rows_no_mw": g["mw_missing"], "mw": g["mw"].round(0),
                      "mw_share_one_zone": g["mw_one_zone"] / g["mw"].replace(0, np.nan),
                      "mw_share_usable": g["mw_usable"] / g["mw"].replace(0, np.nan),
                      "row_share_usable": g["rows_usable"] / g["rows"]})
    return t


def report(df: pd.DataFrame, cfg: dict) -> str:
    d = df.assign(file=df["source_file"].str.replace(r"\.xlsx?$", "", regex=True),
                  status_g=df["status_n"].fillna("unmapped"))
    lines = ["Step that placed each row (rows):",
             d["ba_source"].fillna("unplaced").value_counts().to_string(), "",
             "By workbook:", match_table(d, "file").round(3).to_string(), "",
             "By status:", match_table(d, "status_g").round(3).to_string(), "",
             "By workbook x status (MW share usable):",
             d.groupby(["file", "status_g"]).apply(
                 lambda x: match_table(x.assign(k=1), "k")["mw_share_usable"].iat[0], include_groups=False)
             .unstack().round(3).to_string()]
    floor = cfg["lbnl"].get("min_mw_share", 0.85)
    t = match_table(d, "file")
    low = t[t["mw_share_usable"] < floor]
    lines += ["", f"Workbooks below {floor:.0%} of MW usable: {', '.join(low.index) if len(low) else 'none'}"]
    return "\n".join(lines)
