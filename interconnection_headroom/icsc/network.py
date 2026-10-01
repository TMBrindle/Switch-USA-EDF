"""Network-capacity headroom proxy from ReEDS county-to-county AC transfer limits (NARIS 2024)."""
from __future__ import annotations

import pandas as pd


def transfer_capacity_by_zone(path, c2z: pd.DataFrame, boundary_weight: float = 0.5) -> pd.DataFrame:
    """Return per-zone intra-zone and boundary AC transfer capacity (MW).

    intra_mw     = sum over county pairs inside the zone of the mean of forward/reverse limits
    boundary_mw  = sum over county pairs that cross the zone boundary
    transfer_mw  = intra_mw + boundary_weight * boundary_mw  (boundary capacity is shared by two zones)
    """
    t = pd.read_csv(path, index_col=0)
    fips2ba = dict(zip("p" + c2z["FIPS"], c2z["ba"]))
    t["ba_r"] = t["r"].map(fips2ba)
    t["ba_rr"] = t["rr"].map(fips2ba)
    t["mw"] = t[["MW_f0", "MW_r0"]].mean(axis=1)
    intra = t[t["ba_r"] == t["ba_rr"]].groupby("ba_r")["mw"].sum()
    b = t[t["ba_r"] != t["ba_rr"]]
    boundary = pd.concat([b.groupby("ba_r")["mw"].sum(), b.groupby("ba_rr")["mw"].sum()]).groupby(level=0).sum()
    out = pd.DataFrame({"intra_mw": intra, "boundary_mw": boundary}).reindex(sorted(c2z["ba"].unique())).fillna(0.0)
    out["transfer_mw"] = out["intra_mw"] + boundary_weight * out["boundary_mw"]
    out.index.name = "ba"
    return out
