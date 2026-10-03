"""Zone-level network reinforcement cost from ReEDS, the cost of the conventional
reinforcement uprate (conv_reinforcement) and the basis of advanced-conductor reconductoring's cost.

Source (pinned ReEDS commit, data/reference/REEDS_COMMIT.txt; scripts/fetch_data.sh):
  inputs/supply_curve/interconnection_land.h5   cost_reinforcement_usd_per_mw per reV site
      (sc_point_gid), group attribute dollaryear (2023 at the pinned commit). It is ReEDS's cost of
      reinforcing the network from a site's POI to the zone's load centre, per MW of generation
      capacity: the closest public analogue to Switch adding a MW of intra-zonal network capacity.
  inputs/supply_curve/supplycurve_{upv,wind-ons}-reference.csv   site capacity (MW), the weights

Per zone (county FIPS -> ReEDS zone, county2zone.csv): capacity-weighted median, p10 and p90 of the
sites' reinforcement cost, converted to the pipeline dollar year with CPI-U (cpi_u_annual.csv).
Sites with no UPV or onshore-wind capacity carry no weight. Zones with no weighted site take their
transreg's capacity-weighted median, then the national one (column `source`).
"""
from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pandas as pd

from . import geo


def read_sites(h5_path: str | Path) -> tuple[pd.DataFrame, int]:
    """Site table (sc_point_gid, FIPS, cost_reinforcement_usd_per_mw) and its dollar year."""
    with h5py.File(h5_path, "r") as f:
        g = f["data"]
        df = pd.DataFrame({
            "sc_point_gid": g["sc_point_gid"][:],
            "FIPS": pd.Series(g["FIPS"][:]).str.decode("utf-8").str.zfill(5),
            "cost_reinforcement_usd_per_mw": g["cost_reinforcement_usd_per_mw"][:].astype(float)})
        dollar_year = int(g.attrs["dollaryear"])
    return df, dollar_year


def weighted_quantile(x: np.ndarray, w: np.ndarray, q: float) -> float:
    """Quantile q of x with weights w: the cost of the site at which the cumulative capacity first
    reaches q of the total (a site's value, never an interpolation between sites)."""
    o = np.argsort(x, kind="stable")
    x, w = np.asarray(x, float)[o], np.asarray(w, float)[o]
    cw = np.cumsum(w)
    return float(x[np.searchsorted(cw, q * cw[-1] - 1e-9 * cw[-1])])


def zone_costs(sites: pd.DataFrame, capacity: pd.DataFrame, c2z: pd.DataFrame, hierarchy: pd.DataFrame,
               cpi_factor: float) -> pd.DataFrame:
    """Per zone: n_sites, capacity_mw, reinforcement_{p10,median,p90}_per_kw (pipeline dollar year), source."""
    cap = capacity.groupby("sc_point_gid")["capacity"].sum()
    s = sites.assign(capacity=sites["sc_point_gid"].map(cap).fillna(0.0),
                     ba=sites["FIPS"].map(c2z.set_index("FIPS")["ba"]),
                     cost=sites["cost_reinforcement_usd_per_mw"] / 1000 * cpi_factor)
    s = s[(s["capacity"] > 0) & s["ba"].notna() & s["cost"].notna()]
    s = s.assign(transreg=s["ba"].map(hierarchy.set_index("ba")["transreg"]))

    def stats(d):
        return pd.Series({"n_sites": len(d), "capacity_mw": d["capacity"].sum(),
                          **{f"reinforcement_{k}_per_kw": weighted_quantile(d["cost"].values, d["capacity"].values, q)
                             for k, q in (("p10", 0.1), ("median", 0.5), ("p90", 0.9))}})

    by_zone = s.groupby("ba").apply(stats, include_groups=False)
    by_tr = s.groupby("transreg").apply(stats, include_groups=False)
    nat = stats(s)
    out = []
    for _, h in hierarchy.iterrows():
        ba, tr = h["ba"], h["transreg"]
        if ba in by_zone.index:
            out.append({"ba": ba, **by_zone.loc[ba].to_dict(), "source": "zone"})
        elif tr in by_tr.index:
            out.append({"ba": ba, **by_tr.loc[tr].to_dict(), "n_sites": 0, "capacity_mw": 0.0, "source": "transreg"})
        else:
            out.append({"ba": ba, **nat.to_dict(), "n_sites": 0, "capacity_mw": 0.0, "source": "national"})
    z = pd.DataFrame(out)
    z["n_sites"] = z["n_sites"].astype(int)
    return z


def build(cfg: dict) -> pd.DataFrame:
    """Read the ReEDS files named in cfg['reinforcement'] and return the zone table."""
    rc, root = cfg["reinforcement"], Path(cfg["_root"])
    missing = [p for p in [rc["h5"], *rc["supply_curves"]] if not (root / p).exists()]
    if missing:
        raise FileNotFoundError("ReEDS reinforcement inputs missing (run scripts/fetch_data.sh): " + ", ".join(missing))
    sites, dollar_year = read_sites(root / rc["h5"])
    capacity = pd.concat([pd.read_csv(root / p, usecols=["sc_point_gid", "capacity"]) for p in rc["supply_curves"]])
    cpi = pd.read_csv(cfg["paths"]["cpi"]).set_index("year")["cpi"]
    factor = float(cpi.loc[cfg["dollar_year"]] / cpi.loc[dollar_year])
    z = zone_costs(sites, capacity, geo.load_county2zone(cfg["paths"]["county2zone"]),
                   geo.load_hierarchy(cfg["paths"]["hierarchy"]), factor)
    return z.assign(reeds_dollar_year=dollar_year, dollar_year=cfg["dollar_year"])
