"""Regenerate s0_workflow/data/coal_cf_caps_eia923_2021_2024.csv from public EIA data (item 3).

Downloads (to --cache, default s0_workflow/data/raw/, gitignored):
  EIA-923 annual files  https://www.eia.gov/electricity/data/eia923/archive/xls/f923_<year>.zip
                        (Schedules 2-5, sheet "Page 4 Generator Data": monthly net generation by generator)
  EIA-860 annual files  https://www.eia.gov/electricity/data/eia860/archive/xls/eia860<year>.zip
                        (3_1_Generator_Y<year>.xlsx, sheets Operable and Retired and Canceled:
                        winter/nameplate capacity, status, operating and planned retirement years,
                        state and county)
for the years --first-year..--last-year (2021-2024). The newest year may sit outside archive/ on the
EIA site; the script tries both URLs. Zone mapping: interconnection_headroom/data/reference/county2zone.csv.

usage (repo root):  python s0_workflow/scripts/fetch_coal_cf_eia923.py [--first-year 2021] [--last-year 2024]
Writes the table and data/coal_cf_caps_eia923_2021_2024.units.csv (unit detail, for review).
"""
import argparse
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import coal_cf  # noqa: E402

URLS = {
    "923": ["https://www.eia.gov/electricity/data/eia923/archive/xls/f923_{y}.zip",
            "https://www.eia.gov/electricity/data/eia923/xls/f923_{y}.zip"],
    "860": ["https://www.eia.gov/electricity/data/eia860/archive/xls/eia860{y}.zip",
            "https://www.eia.gov/electricity/data/eia860/xls/eia860{y}.zip"],
}


def fetch(kind: str, y: int, cache: Path) -> Path:
    out = cache / f"{kind}_{y}.zip"
    if out.exists() and zipfile.is_zipfile(out):
        return out
    for url in URLS[kind]:
        u = url.format(y=y)
        print("downloading", u)
        try:
            urllib.request.urlretrieve(u, out)
        except Exception as e:  # noqa: BLE001
            print("  failed:", e)
            continue
        if zipfile.is_zipfile(out):
            return out
        print("  not a zip (EIA redirects missing files to an HTML page)")
    raise SystemExit(f"could not download EIA-{kind} {y}")


def member(z: Path, pattern: str, cache: Path) -> Path:
    with zipfile.ZipFile(z) as f:
        names = [n for n in f.namelist() if pattern in n]
        if not names:
            raise SystemExit(f"{z.name}: no member matching {pattern!r}")
        return Path(f.extract(names[0], cache / z.stem))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--first-year", type=int, default=2021)
    ap.add_argument("--last-year", type=int, default=2024)
    ap.add_argument("--online-year", type=int, default=2035)
    ap.add_argument("--cache", default=str(REPO / "s0_workflow/data/raw"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    years = range(a.first_year, a.last_year + 1)
    gens, gen923 = {}, []
    for y in years:
        g = member(fetch("860", y, cache), "3_1_Generator", cache)
        gens[y] = coal_cf.read_860_generators(g)
        gen923.append(coal_cf.read_923_generation(member(fetch("923", y, cache), "Schedules_2_3_4_5", cache)))
    latest_file = member(fetch("860", a.last_year, cache), "3_1_Generator", cache)
    retired = coal_cf.read_860_generators(latest_file, "Retired and Canceled")
    c2z = pd.read_csv(REPO / "interconnection_headroom/data/reference/county2zone.csv")
    zc, units = coal_cf.zone_caps(gens, gens[a.last_year], retired, pd.concat(gen923), c2z,
                                  (a.first_year, a.last_year), a.online_year)
    out = Path(a.out or REPO / f"s0_workflow/data/coal_cf_caps_eia923_{a.first_year}_{a.last_year}.csv")
    hdr = (f"# zonal coal CF caps: winter-capacity-weighted mean of unit max annual CF {a.first_year}-{a.last_year}; "
           f"EIA-923 Page 4 + EIA-860 3_1_Generator ({a.first_year}-{a.last_year}); units operable in "
           f"{a.last_year} with no planned retirement before {a.online_year}; built {date.today()} by "
           "s0_workflow/scripts/fetch_coal_cf_eia923.py\n")
    with open(out, "w") as f:
        f.write(hdr)
        zc.round({"hist_mw": 1, "cap_cf": 6, "cap_cf_nameplate": 6}).to_csv(f, index=False)
    units.drop(columns=[c for c in units.columns if c not in (
        "Plant Code", "Plant Name", "Generator ID", "State", "County", "Technology", "Energy Source 1",
        "Winter Capacity (MW)", "Nameplate Capacity (MW)", "Operating Year", "Planned Retirement Year",
        "cf_max", "cf_max_np", "years", "zone")]).to_csv(out.with_suffix(".units.csv"), index=False)
    print(f"wrote {out}: {len(zc)} zones, {zc.hist_mw.sum() / 1e3:.1f} GW with history; "
          f"units {len(units)}, {units['Winter Capacity (MW)'].sum() / 1e3:.1f} GW, "
          f"mapped {units[units.zone.notna()]['Winter Capacity (MW)'].sum() / 1e3:.1f} GW")
    w = zc.hist_mw
    print(f"capacity-weighted national cap {(zc.cap_cf * w).sum() / w.sum():.3f}; zones >= 500 MW: {(w >= 500).sum()}")


if __name__ == "__main__":
    main()
