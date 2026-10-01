"""EIA-860M generator inventory -> zone-level headroom proxy and saturation panel.

Every new generator uses headroom, weighted by technology (config `saturation.tech_weights`):
the weight is how much of the network one MW of nameplate occupies under interconnection-study
conditions. Retiring generators free headroom at their own weight times the reuse share.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .geo import attach_ba

CATEGORIES = ("solar", "wind", "storage", "gas", "other")

# EIA-860M "Technology" substrings -> category (first match wins; everything else is "other")
_TECH_RULES = [
    ("storage", ("batteries", "flywheel", "pumped storage", "compressed air")),
    ("solar", ("solar",)),
    ("wind", ("wind",)),
    ("gas", ("natural gas",)),
]


def _read(path, sheet):
    df = pd.read_excel(path, sheet_name=sheet, header=2)
    df = df[pd.to_numeric(df["Plant ID"], errors="coerce").notna()]  # drop footnote rows
    return df


def load_eia860m(path) -> pd.DataFrame:
    """Return one row per generator with status, years, nameplate MW and technology category."""
    op = _read(path, "Operating").assign(status="operating")
    pl = _read(path, "Planned").assign(status="planned")
    rt = _read(path, "Retired").assign(status="retired")
    pl = pl.rename(columns={"Planned Operation Year": "Operating Year"})
    op = op.rename(columns={"Planned Retirement Year": "Retirement Year"})
    cols = ["Plant ID", "Generator ID", "Plant State", "County", "Technology", "Energy Source Code",
            "Nameplate Capacity (MW)", "Operating Year", "Retirement Year", "status"]
    gens = pd.concat([d.reindex(columns=cols) for d in (op, pl, rt)], ignore_index=True)
    gens = gens.rename(columns={"Plant State": "state", "County": "county",
                                "Nameplate Capacity (MW)": "mw", "Operating Year": "op_year",
                                "Retirement Year": "ret_year", "Technology": "technology"})
    for c in ("mw", "op_year", "ret_year"):
        gens[c] = pd.to_numeric(gens[c], errors="coerce")
    # Planned retirements on operating units are not yet real; keep them separately
    gens["planned_ret_year"] = np.where(gens["status"] == "operating", gens["ret_year"], np.nan)
    gens.loc[gens["status"] == "operating", "ret_year"] = np.nan
    gens["category"] = categorise(gens["technology"])
    return gens


def categorise(tech: pd.Series) -> pd.Series:
    low = tech.fillna("").str.lower()
    out = pd.Series("other", index=tech.index, dtype=object)
    done = pd.Series(False, index=tech.index)
    for cat, keys in _TECH_RULES:
        hit = ~done & low.apply(lambda s: any(k in s for k in keys))
        out[hit] = cat
        done |= hit
    return out


def zone_components(gens: pd.DataFrame, c2z: pd.DataFrame, cfg: dict, years=range(2000, 2031),
                    proxy_override: pd.Series | None = None) -> pd.DataFrame:
    """Zone x year panel of unweighted building blocks, so any weight set can be applied cheaply.

    add_<cat>  MW of <cat> online in year t that came online after baseline_year
    ret_<cat>  MW of <cat> retired between baseline_year and t
    headroom_proxy_mw  transfer capacity (proxy_override) or all nameplate online at baseline_year
    """
    sc = cfg["saturation"]
    base = sc["baseline_year"]
    g = attach_ba(gens, c2z, state_col="state", county_col="county")
    g = g[g["ba"].notna() & (g["status"] != "planned")]  # historical panel: built and retired units only
    zones = pd.Index(sorted(c2z["ba"].unique()), name="ba")

    online_at_base = (g["op_year"] <= base) & (g["ret_year"].isna() | (g["ret_year"] > base))
    proxy = g[online_at_base].groupby("ba")["mw"].sum().reindex(zones, fill_value=0.0)
    if proxy_override is not None:
        proxy = proxy_override.reindex(zones).fillna(0.0)

    rows = []
    for y in years:
        alive = (g["op_year"] > base) & (g["op_year"] <= y) & (g["ret_year"].isna() | (g["ret_year"] > y))
        retired = (g["ret_year"] > base) & (g["ret_year"] <= y)
        add = g[alive].pivot_table(index="ba", columns="category", values="mw", aggfunc="sum")
        ret = g[retired].pivot_table(index="ba", columns="category", values="mw", aggfunc="sum")
        df = pd.concat([add.add_prefix("add_"), ret.add_prefix("ret_")], axis=1).reindex(zones)
        df["year"] = y
        rows.append(df)
    comp = pd.concat(rows).fillna(0.0).reset_index()
    for c in CATEGORIES:
        for p in ("add_", "ret_"):
            if p + c not in comp:
                comp[p + c] = 0.0
    comp["headroom_proxy_mw"] = comp["ba"].map(proxy)
    comp["headroom_proxy_floored_mw"] = comp["headroom_proxy_mw"].clip(lower=sc["min_headroom_mw"])
    return comp


def apply_weights(comp: pd.DataFrame, weights: dict, reuse_share: float) -> pd.DataFrame:
    """Add weighted additions/retirements, used MW and saturation to a components panel."""
    out = comp.copy()
    out["added_mw_weighted"] = sum(out["add_" + c] * weights.get(c, 1.0) for c in CATEGORIES)
    out["retired_mw_weighted"] = sum(out["ret_" + c] * weights.get(c, 1.0) for c in CATEGORIES)
    out["used_mw"] = (out["added_mw_weighted"] - reuse_share * out["retired_mw_weighted"]).clip(lower=0)
    out["saturation"] = out["used_mw"] / out["headroom_proxy_floored_mw"]
    return out


def zone_panel(gens: pd.DataFrame, c2z: pd.DataFrame, cfg: dict, years=range(2000, 2031),
               proxy_override: pd.Series | None = None) -> pd.DataFrame:
    sc = cfg["saturation"]
    comp = zone_components(gens, c2z, cfg, years, proxy_override)
    return apply_weights(comp, sc["tech_weights"], sc["retirement_reuse_share"])


def planned_changes(gens: pd.DataFrame, c2z: pd.DataFrame, cfg: dict, start: int, end: int) -> pd.DataFrame:
    """Weighted planned additions and announced retirements by zone for start..end (EIA pipeline)."""
    w = cfg["saturation"]["tech_weights"]
    g = attach_ba(gens, c2z, state_col="state", county_col="county")
    g = g[g["ba"].notna()].copy()
    g["wmw"] = g["mw"] * g["category"].map(lambda c: w.get(c, 1.0))
    pl = g[(g["status"] == "planned") & g["op_year"].between(start, end)]
    pr = g[(g["status"] == "operating") & g["planned_ret_year"].between(start, end)]
    return pd.DataFrame({
        "planned_add_mw_weighted": pl.groupby("ba")["wmw"].sum(),
        "planned_retire_mw_weighted": pr.groupby("ba")["wmw"].sum(),
    }).fillna(0.0)
