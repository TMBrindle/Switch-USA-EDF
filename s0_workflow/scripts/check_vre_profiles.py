"""Variable-resource profile checks on a built case (diagnostic; nothing in the build reads this).

  python s0_workflow/scripts/check_vre_profiles.py <case inputs folder> [--zones p1 p3 p8] [--tech utilitypv]
      [--metadata cf.csv] [--out vre_profile_check.csv]

Per generator and weather year (from the timestamps: <period><weather-year index 1-7><MMDD><HH>; 1 = 2007, as
PowerGenome's 2007-2013 365-day years), over the case's sample days (stress days excluded):
  mean_cf          mean hourly capacity factor
  day_hours        hours with CF > 0.01;  first_hour / last_hour / peak_hour: model clock (a time-zone shift moves
                   them against the other zones of the same technology)
  ratio_to_peers   mean CF / the mean of the same technology's generators in the other listed zones on the same hours
  zero_daylight    share of hours where the peers produce (> 0.05) and this generator gives 0 (missing data shows here)
  ratio_to_meta    mean CF / the metadata CF (--metadata: GENERATION_PROJECT, metadata_cf), when given
A unit error shows as a ratio far from 1 in every year; missing data as zero_daylight > 0; a time-zone shift as
first/last/peak hours offset from the peers by whole hours.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

FIRST_WEATHER_YEAR = 2007


def load(folder: Path, zones, tech: str) -> pd.DataFrame:
    gi = pd.read_csv(folder / "gen_info.csv", na_values=["."])
    gi = gi[gi.gen_load_zone.isin(zones) & gi.gen_tech.str.contains(tech, case=False, regex=False)]
    vc = pd.read_csv(folder / "variable_capacity_factors.csv")
    vc = vc[vc.GENERATION_PROJECT.isin(gi.GENERATION_PROJECT)]
    tp = pd.read_csv(folder / "timepoints.csv").rename(columns={"timepoint_id": "TIMEPOINT"})
    tp = tp[tp.timestamp.astype(str).str.isdigit()]                      # stress days carry a suffix
    t = tp.timestamp.astype(str)
    tp = tp.assign(weather_year=FIRST_WEATHER_YEAR - 1 + t.str[4].astype(int), day=t.str[4:9],
                   hour=t.str[-2:].astype(int))
    x = vc.merge(tp[["TIMEPOINT", "timeseries", "weather_year", "day", "hour"]], on="TIMEPOINT")
    return x.merge(gi[["GENERATION_PROJECT", "gen_load_zone", "gen_tech"]], on="GENERATION_PROJECT")


def check(x: pd.DataFrame, meta: pd.DataFrame | None = None) -> pd.DataFrame:
    x = x.drop_duplicates(["GENERATION_PROJECT", "day", "hour"])           # a day sampled for several periods: once
    rows = []
    for (g, wy), z in x.groupby(["GENERATION_PROJECT", "weather_year"]):
        zone = z.gen_load_zone.iat[0]
        peers = x[(x.gen_load_zone != zone) & (x.weather_year == wy)]
        pm = peers.groupby(["day", "hour"]).gen_max_capacity_factor.mean()
        zz = z.set_index(["day", "hour"]).gen_max_capacity_factor
        common = zz.index.intersection(pm.index)
        on = zz[zz > 0.01]
        lit = pm.reindex(common) > 0.05
        rows.append({"GENERATION_PROJECT": g, "zone": zone, "weather_year": wy, "days": z.day.nunique(),
                     "mean_cf": zz.mean(), "day_hours": len(on) / max(z.day.nunique(), 1),
                     "first_hour": on.index.get_level_values("hour").min() if len(on) else None,
                     "last_hour": on.index.get_level_values("hour").max() if len(on) else None,
                     "peak_hour": int(z.groupby("hour").gen_max_capacity_factor.mean().idxmax()),
                     "peer_first_hour": pm[pm > 0.01].index.get_level_values("hour").min() if len(pm) else None,
                     "peer_last_hour": pm[pm > 0.01].index.get_level_values("hour").max() if len(pm) else None,
                     "ratio_to_peers": zz.reindex(common).mean() / pm.reindex(common).mean() if len(common) else None,
                     "zero_daylight": ((zz.reindex(common)[lit] <= 0.0).mean() if lit.any() else None)})
    out = pd.DataFrame(rows)
    if meta is not None and len(out):
        out = out.merge(meta[["GENERATION_PROJECT", "metadata_cf"]], on="GENERATION_PROJECT", how="left")
        out["ratio_to_meta"] = out.mean_cf / out.metadata_cf
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--zones", nargs="+", default=["p1", "p3", "p5", "p8"])
    ap.add_argument("--tech", default="utilitypv")
    ap.add_argument("--metadata", type=Path)
    ap.add_argument("--out", type=Path, default=Path("vre_profile_check.csv"))
    a = ap.parse_args(argv)
    meta = pd.read_csv(a.metadata) if a.metadata else None
    out = check(load(a.folder, a.zones, a.tech), meta)
    out.to_csv(a.out, index=False)
    print(out.round(3).to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
