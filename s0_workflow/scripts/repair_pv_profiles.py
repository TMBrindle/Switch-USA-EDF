"""Write a repaired copy of the reV solar profiles folder (CHANGES §111; s0_workflow/pv_profile_repair.py).

  python s0_workflow/scripts/repair_pv_profiles.py --profiles-dir pg_data/profiles --out-dir pg_data/profiles_pvfix \
      --sites pv_sites.csv [--profile solar_rev_profiles_20240801.parquet] [--hash] [--no-links]

  python s0_workflow/scripts/repair_pv_profiles.py --profiles-dir pg_data/profiles --out-dir pg_data/profiles_pvfix \
      --metadata pg_data/resource_groups/ReEDS-cpas-patched/solar_lcoe_ReEDS.csv \
      --site-map pg_data/profiles/solar_site_mapping_20240801.parquet --zeroed zeroed_pv_sites.csv
      (builds the sites table: capacity summed and coordinates capacity-weighted over each profile site's CPAs;
      columns default to CPA_ID, Site, latitude, longitude, cpa_mw, matched case-insensitively; --*-col to change)

--sites: site (the profile column name), latitude, longitude, capacity_mw. The source folder is only read. The output
folder gets the repaired file under its original name, hard links (else copies) of the folder's other files,
pv_profile_repair_log.csv (one row per zeroed site-year: neighbour, distance, scale, clipped hours, status) and
pv_profile_repair_manifest.json. Then set s0_production.pv_profile_repair: {mode: neighbour_fill, profiles_dir: <out>}.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from s0_workflow import pv_profile_repair as pr  # noqa: E402


def _col(df: pd.DataFrame, name: str, what: str) -> str:
    """The column called `name` (case-insensitive), or stop with the columns there are."""
    m = {c.lower(): c for c in df.columns}
    if name.lower() not in m:
        raise SystemExit(f"{what}: no column {name!r}; columns are {list(df.columns)[:40]}; pass --{what}-col")
    return m[name.lower()]


def sites_from(metadata: pd.DataFrame, site_map: pd.DataFrame, cpa="CPA_ID", site="Site", lat="latitude",
               lon="longitude", cap="cpa_mw") -> pd.DataFrame:
    """Profile sites from the CPA metadata (solar_lcoe_ReEDS.csv) and the site mapping (CPA_ID -> Site): capacity
    summed over each site's CPAs, coordinates capacity-weighted (plain mean where the MW sum is 0)."""
    mc, mla, mlo, mcap = (_col(metadata, cpa, "cpa"), _col(metadata, lat, "lat"), _col(metadata, lon, "lon"),
                          _col(metadata, cap, "cap"))
    sc, ss = _col(site_map, cpa, "cpa"), _col(site_map, site, "site")
    m = metadata[[mc, mla, mlo, mcap]].rename(columns={mc: "cpa", mla: "lat", mlo: "lon", mcap: "mw"}).merge(
        site_map[[sc, ss]].rename(columns={sc: "cpa", ss: "site"}), on="cpa")
    m["w"] = m.mw.clip(lower=0)
    m["wlat"], m["wlon"] = m.lat * m.w, m.lon * m.w
    g = m.groupby("site")
    agg = g.agg(w=("w", "sum"), wlat=("wlat", "sum"), wlon=("wlon", "sum"), lat=("lat", "mean"), lon=("lon", "mean"))
    pos = agg.w > 0
    out = pd.DataFrame({"latitude": np.where(pos, agg.wlat / agg.w.where(pos, 1), agg.lat),
                        "longitude": np.where(pos, agg.wlon / agg.w.where(pos, 1), agg.lon),
                        "capacity_mw": agg.w}, index=agg.index)
    return out.reset_index().assign(site=lambda d: d.site.astype(str))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profiles-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--profile", default="solar_rev_profiles_20240801.parquet")
    ap.add_argument("--sites", type=Path)
    ap.add_argument("--metadata", type=Path)
    ap.add_argument("--site-map", type=Path)
    for k, d in (("cpa", "CPA_ID"), ("site", "Site"), ("lat", "latitude"), ("lon", "longitude"), ("cap", "cpa_mw")):
        ap.add_argument(f"--{k}-col", default=d)
    ap.add_argument("--zeroed", type=Path, help="zeroed_pv_sites.csv (data/rev-cf-mismatch): cross-check the fills")
    ap.add_argument("--warn-km", type=float, default=100.0)
    ap.add_argument("--first-year", type=int, default=2007)
    ap.add_argument("--hours", type=int, default=8760)
    ap.add_argument("--ref-years", type=int, nargs="+", default=[2012, 2013])
    ap.add_argument("--batch-rows", type=int, default=2190)
    ap.add_argument("--hash", action="store_true", help="sha256 of source and output in the manifest (slow, 16 GB)")
    ap.add_argument("--no-links", action="store_true", help="copy the folder's other files instead of hard links")
    a = ap.parse_args(argv)
    if a.sites:
        sites = pd.read_csv(a.sites, dtype={"site": str})
    elif a.metadata and a.site_map:
        rd = lambda p: pd.read_parquet(p) if str(p).endswith(".parquet") else pd.read_csv(p)  # noqa: E731
        sites = sites_from(rd(a.metadata), rd(a.site_map), a.cpa_col, a.site_col, a.lat_col, a.lon_col, a.cap_col)
        sites.to_csv(a.out_dir.with_name(a.out_dir.name + "_sites.csv"), index=False)
    else:
        ap.error("give --sites, or --metadata and --site-map with the column names")
    man = pr.repair_folder(a.profiles_dir, a.out_dir, a.profile, sites, a.first_year, a.hours, tuple(a.ref_years),
                           a.batch_rows, link_others=not a.no_links, with_hash=a.hash,
                           zeroed=pd.read_csv(a.zeroed, dtype={"site": str}) if a.zeroed else None, warn_km=a.warn_km)
    return 0 if man["zero_site_years_with_capacity_after"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
