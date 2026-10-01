"""Share of ReEDS interconnection cost that is network reinforcement, by zone x tech.

PowerGenome's ReEDS-CPA site files carry one bundled `interconnect_capex_mw` per site (no
spur/tx split), so pg_to_switch can't strip reinforcement the PowerGenome-native way (tx_capex).
This writes data/reference/reinforcement_share.csv, used by icsc.switch_case to scale those
costs down to their non-reinforcement part when interconnection headroom is on.

    share = sum(capacity x cost_reinforcement) / sum(capacity x cost_total_trans)

over the ReEDS reference-siting supply-curve points of each tech in each zone (zone from the
point's county via county2zone.csv), plus a national row per tech (zone "_national") used as the
fallback. Inputs: interconnection_{land,offshore}.h5 (committed, ReEDS inputs/supply_curve, dollar
year 2023) and supplycurve_<tech>-reference.csv (capacity weights) from the same ReEDS commit
(REEDS_COMMIT.txt), downloaded to data/raw/reeds/. Offshore totals include the export cable;
`--offshore-topology` picks radial (default) or meshed.

    python scripts/reinforcement_share.py
"""
from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

import h5py
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "data" / "reference"
RAW = ROOT / "data" / "raw" / "reeds"
TECHS = {"upv": "land", "wind-ons": "land", "wind-ofs": "offshore"}


def reeds_commit() -> str:
    return (REF / "REEDS_COMMIT.txt").read_text().split()[0]


def supply_curve(tech: str) -> pd.DataFrame:
    fn = RAW / f"supplycurve_{tech}-reference.csv"
    if not fn.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        url = (f"https://raw.githubusercontent.com/NREL/ReEDS-2.0/{reeds_commit()}/inputs/"
               f"supply_curve/{fn.name}")
        urllib.request.urlretrieve(url, fn)
    sc = pd.read_csv(fn)
    return sc.groupby("sc_point_gid", as_index=False)["capacity"].sum()


def interconnection(kind: str, topology: str) -> tuple[pd.DataFrame, int]:
    with h5py.File(REF / f"interconnection_{kind}.h5", "r") as f:
        g = f["data"]
        total = "cost_total_trans_usd_per_mw" + ("" if kind == "land" else f"|{topology}")
        df = pd.DataFrame({
            "sc_point_gid": g["sc_point_gid"][:],
            "FIPS": [x.decode() for x in g["FIPS"][:]],
            "cost_spur": g["cost_spur_usd_per_mw"][:],
            "cost_poi": g["cost_poi_usd_per_mw"][:],
            "cost_reinforcement": g["cost_reinforcement_usd_per_mw"][:],
            "cost_total": g[total][:],
        })
        return df, int(g.attrs["dollaryear"])


def build(topology: str = "radial") -> pd.DataFrame:
    c2z = pd.read_csv(REF / "county2zone.csv", dtype={"FIPS": str})
    c2z["FIPS"] = c2z["FIPS"].str.zfill(5)
    rows = []
    for tech, kind in TECHS.items():
        ic, dollar_year = interconnection(kind, topology)
        d = supply_curve(tech).merge(ic, on="sc_point_gid", how="inner")
        d = d.merge(c2z[["FIPS", "ba"]], on="FIPS", how="left")
        d = d[d["capacity"] > 0]
        for c in ["cost_spur", "cost_poi", "cost_reinforcement", "cost_total"]:
            d[c + "_mw"] = d[c] * d["capacity"]
        d["ba"] = d["ba"].fillna("_unmapped")
        groups = [(z, g) for z, g in d.groupby("ba")] + [("_national", d)]
        for zone, g in groups:
            cap = g["capacity"].sum()
            tot = g["cost_total_mw"].sum()
            rows.append({
                "zone": zone, "tech": tech, "n_points": len(g), "capacity_mw": round(cap, 1),
                "reinforcement_share": g["cost_reinforcement_mw"].sum() / tot if tot > 0 else None,
                "spur_share": g["cost_spur_mw"].sum() / tot if tot > 0 else None,
                "poi_share": g["cost_poi_mw"].sum() / tot if tot > 0 else None,
                "mean_total_usd_per_kw": tot / cap / 1000,
                "mean_reinforcement_usd_per_kw": g["cost_reinforcement_mw"].sum() / cap / 1000,
                "dollar_year": dollar_year,
            })
    out = pd.DataFrame(rows)
    out = out[out["zone"] != "_unmapped"]
    for c in ["reinforcement_share", "spur_share", "poi_share"]:
        out[c] = out[c].round(4)
    for c in ["mean_total_usd_per_kw", "mean_reinforcement_usd_per_kw"]:
        out[c] = out[c].round(1)
    return out.sort_values(["tech", "zone"]).reset_index(drop=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offshore-topology", choices=["radial", "meshed"], default="radial")
    args = ap.parse_args()
    out = build(args.offshore_topology)
    fn = REF / "reinforcement_share.csv"
    out.to_csv(fn, index=False)
    print(f"wrote {fn} ({len(out)} rows), ReEDS {reeds_commit()[:8]}, offshore {args.offshore_topology}")
