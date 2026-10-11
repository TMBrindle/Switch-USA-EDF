"""Write a repaired copy of the reV solar profiles folder (CHANGES §111; s0_workflow/pv_profile_repair.py).

  python s0_workflow/scripts/repair_pv_profiles.py --profiles-dir pg_data/profiles --out-dir pg_data/profiles_pvfix \
      --sites pv_sites.csv [--profile solar_rev_profiles_20240801.parquet] [--hash] [--no-links]

  python s0_workflow/scripts/repair_pv_profiles.py ... --metadata <cpa metadata.csv> --site-map <solar_site_mapping.parquet> \
      --cpa-col <cpa id> --site-col <profile site id> --lat-col <lat> --lon-col <lon> --cap-col <MW>
      (builds the sites table: capacity summed and coordinates capacity-weighted over each profile site's CPAs)

--sites: site (the profile column name), latitude, longitude, capacity_mw. The source folder is only read. The output
folder gets the repaired file under its original name, hard links (else copies) of the folder's other files,
pv_profile_repair_log.csv (one row per zeroed site-year: neighbour, distance, scale, clipped hours, status) and
pv_profile_repair_manifest.json. Then set s0_production.pv_profile_repair: {mode: neighbour_fill, profiles_dir: <out>}.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from s0_workflow import pv_profile_repair as pr  # noqa: E402


def sites_from(metadata: pd.DataFrame, site_map: pd.DataFrame, cpa, site, lat, lon, cap) -> pd.DataFrame:
    m = metadata[[cpa, lat, lon, cap]].merge(site_map[[cpa, site]], on=cpa)
    m["w"] = m[cap].clip(lower=0)
    g = m.groupby(site)
    w = g.w.sum()
    out = pd.DataFrame({"latitude": g.apply(lambda d: (d[lat] * d.w).sum() / d.w.sum() if d.w.sum() > 0 else d[lat].mean()),
                        "longitude": g.apply(lambda d: (d[lon] * d.w).sum() / d.w.sum() if d.w.sum() > 0 else d[lon].mean()),
                        "capacity_mw": w})
    return out.reset_index().rename(columns={site: "site"}).assign(site=lambda d: d.site.astype(str))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profiles-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--profile", default="solar_rev_profiles_20240801.parquet")
    ap.add_argument("--sites", type=Path)
    ap.add_argument("--metadata", type=Path)
    ap.add_argument("--site-map", type=Path)
    for k in ("cpa", "site", "lat", "lon", "cap"):
        ap.add_argument(f"--{k}-col")
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
                           a.batch_rows, link_others=not a.no_links, with_hash=a.hash)
    return 0 if man["zero_site_years_with_capacity_after"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
