"""Repair of the zeroed reV solar profiles (CHANGES §111; s0_production.pv_profile_repair).

solar_rev_profiles_20240801.parquet (RESOURCE_GROUP_PROFILES) holds one column per reV site and one row per hour of
PowerGenome's seven 365-day weather years, 2007-2013, in order. The dashboards session found 5,390 sites (blocks of 539
every 6,468 columns) all zero in 2007-2011 and normal in 2012-13. Wind is unaffected.

neighbour_fill, for each site-year that is all zero while the site has output in other years:
    fill = the nearest unaffected site's profile in the SAME weather year (keeping the alignment with load and wind)
           x (the site's mean over the reference years 2012-13 / the neighbour's mean over the same years),
           clipped to [0, 1] (clipped hours are counted in the log)
Unaffected = output in every weather year and a positive reference mean. The neighbour (neighbour_method):
  distance     the nearest by great circle between site centroids (sites table: site, latitude, longitude, capacity_mw)
  correlation  (CHANGES §131; no coordinates needed) the site in the same zone whose DAILY output over the reference
               years correlates best with the site's own (Pearson, 730 days for 2012-13), among sites with data in that
               weather year; if the zone has none, the best-correlated site anywhere (status "filled outside zone").
               Sites table: site, zone, capacity_mw. Daily sums, not hours: shared clouds are what the neighbour must
               carry, the diurnal shape is common to every site, and 730 x 68,923 values fit in memory (17,520 hourly
               rows would be 4.8 GB).

The file is processed in row batches (pyarrow), never loaded whole (about 61,320 x 64,680 values). The output keeps the
source's schema, column order and every non-site column. A repaired profiles folder holds the repaired file under the
original name plus links (or copies) of the folder's other files, and pv_profile_repair_manifest.json; the build points
RESOURCE_GROUP_PROFILES at it (production.apply_pv_profile_repair) after checking the manifest.

Check (`zero_site_years`): every site-year of a site with capacity_mw > 0 that is still all zero. The repair records the
count for its output; the build refuses a folder whose count isn't 0 (or, with verify: full, rescans the file).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
MANIFEST = "pv_profile_repair_manifest.json"
LOG = "pv_profile_repair_log.csv"
EARTH_KM = 6371.0


def _pq():
    try:
        import pyarrow.parquet as pq
        return pq
    except ImportError as e:                          # PowerGenome environments have it
        raise ImportError("pv_profile_repair needs pyarrow") from e


def years_of(n_rows: int, first_year: int = 2007, hours: int = 8760) -> list[int]:
    if n_rows % hours:
        raise ValueError(f"profile file has {n_rows} rows, not a whole number of {hours}-hour weather years")
    return [first_year + i for i in range(n_rows // hours)]


def _site_columns(names, sites: pd.DataFrame | None) -> list[str]:
    if sites is None:
        return [c for c in names if not str(c).startswith("__")]
    keep = set(sites["site"].astype(str))
    return [c for c in names if str(c) in keep]


def scan(path: Path, columns: list[str], first_year=2007, hours=8760, batch_rows=2190, daily_years=()) -> dict:
    """Per column and weather year: max and sum. Returns {"years", "max": DataFrame, "sum": DataFrame} (year x column);
    with daily_years, also "daily": daily sums over those years (day x column, float32)."""
    pq = _pq()
    f = pq.ParquetFile(path)
    years = years_of(f.metadata.num_rows, first_year, hours)
    mx = np.zeros((len(years), len(columns)))
    sm = np.zeros((len(years), len(columns)))
    dy = [y for y in years if y in set(daily_years)]
    dpy = hours // 24
    daily = np.zeros((len(dy) * dpy, len(columns)), dtype=np.float32) if dy else None
    row = 0
    for b in f.iter_batches(batch_size=batch_rows, columns=columns):
        a = np.column_stack([b.column(i).to_numpy(zero_copy_only=False) for i in range(b.num_columns)])
        a = np.nan_to_num(a.astype(np.float32, copy=False))
        start = 0
        while start < len(a):
            y = (row + start) // hours
            take = min(len(a) - start, hours - (row + start) % hours)
            part = a[start:start + take]
            mx[y] = np.maximum(mx[y], part.max(axis=0))
            sm[y] += part.sum(axis=0, dtype=np.float64)
            if daily is not None and years[y] in dy:
                h0 = (row + start) % hours
                base = dy.index(years[y]) * dpy
                for d in range(h0 // 24, (h0 + take - 1) // 24 + 1):
                    lo, hi = max(d * 24, h0) - h0, min((d + 1) * 24, h0 + take) - h0
                    daily[base + d] += part[lo:hi].sum(axis=0)
            start += take
        row += len(a)
    out = {"years": years, "max": pd.DataFrame(mx, index=years, columns=columns),
           "sum": pd.DataFrame(sm, index=years, columns=columns), "hours": hours}
    if daily is not None:
        out["daily"] = pd.DataFrame(daily, columns=columns)
        out["daily_years"] = dy
    return out


def zero_site_years(stats: dict, sites: pd.DataFrame | None) -> pd.DataFrame:
    """Site-years all zero at a site with capacity (capacity_mw > 0; every column when no sites table)."""
    mx = stats["max"]
    cols = list(mx.columns)
    if sites is not None and "capacity_mw" in sites:
        cap = sites.assign(site=sites.site.astype(str)).set_index("site").capacity_mw
        cols = [c for c in cols if float(cap.get(str(c), 0) or 0) > 0]
    z = (mx[cols] <= 0).stack()
    z = z[z]
    return pd.DataFrame({"site": [str(c) for _, c in z.index], "year": [int(y) for y, _ in z.index]})


def _xyz(lat, lon):
    la, lo = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def plan(stats: dict, sites: pd.DataFrame, ref_years=(2012, 2013)) -> pd.DataFrame:
    """One row per zeroed site-year: the nearest site WITH DATA IN THAT WEATHER YEAR (and in the reference years), the
    distance and the scale; or the reason it can't be filled. A site zero only in 2010-11 can fill another's 2008."""
    from scipy.spatial import cKDTree
    mx, sm, hours = stats["max"], stats["sum"], stats["hours"]
    ref = [y for y in ref_years if y in mx.index]
    if not ref:
        raise ValueError(f"reference years {ref_years} not in the file's weather years {list(mx.index)}")
    s = sites.assign(site=sites.site.astype(str)).drop_duplicates("site").set_index("site")
    cols = [c for c in mx.columns if str(c) in s.index]
    zero = mx[cols] <= 0
    ref_mean = sm.loc[ref, cols].sum() / (hours * len(ref))
    ref_ok = (~zero.loc[ref]).all() & (ref_mean > 0)                       # data in every reference year
    xyz = pd.DataFrame(_xyz(s.loc[[str(c) for c in cols], "latitude"], s.loc[[str(c) for c in cols], "longitude"]),
                       index=cols)
    trees = {}
    for y in mx.index:
        cand = [c for c in cols if ref_ok[c] and not zero.at[y, c]]
        if cand:
            trees[int(y)] = (cKDTree(xyz.loc[cand].to_numpy()), cand)
    rows = []
    for c in cols:
        yz = [int(y) for y in zero.index[zero[c]]]
        for y in yz:
            base = {"site": str(c), "year": y, "site_ref_mean": float(ref_mean[c])}
            reason = ("all weather years zero: no reference mean" if len(yz) == len(zero.index)
                      else f"zero in a reference year {ref}" if not ref_ok[c]
                      else f"no site with data in {y}" if y not in trees else "")
            if reason:
                rows.append({**base, "neighbour": None, "distance_km": np.nan, "scale": np.nan,
                             "neighbour_ref_mean": np.nan, "status": "unfilled: " + reason})
                continue
            tree, cand = trees[y]
            d, i = tree.query(xyz.loc[c].to_numpy())
            n = cand[int(i)]
            rows.append({**base, "neighbour": str(n), "distance_km": round(float(2 * EARTH_KM * np.arcsin(min(1.0, d / 2))), 2),
                         "scale": float(ref_mean[c] / ref_mean[n]), "neighbour_ref_mean": float(ref_mean[n]),
                         "status": "filled"})
    return pd.DataFrame(rows, columns=["site", "year", "neighbour", "distance_km", "scale", "site_ref_mean",
                                       "neighbour_ref_mean", "status"])


def _standardise(a: np.ndarray) -> np.ndarray:
    a = a - a.mean(axis=0)
    sd = np.sqrt((a * a).sum(axis=0))
    return a / np.where(sd > 0, sd, np.inf)


def plan_correlation(stats: dict, sites: pd.DataFrame, ref_years=(2012, 2013)) -> pd.DataFrame:
    """As plan(), with the neighbour chosen by daily correlation over the reference years (module docstring): same zone
    first, then anywhere. Columns as plan()'s plus correlation and scope; distance_km is empty."""
    mx, sm, hours = stats["max"], stats["sum"], stats["hours"]
    if "daily" not in stats:
        raise ValueError("correlation neighbours need scan(..., daily_years=reference years)")
    ref = [y for y in ref_years if y in mx.index]
    if not ref or list(stats["daily_years"]) != ref:
        raise ValueError(f"reference years {ref_years}: daily sums cover {stats.get('daily_years')}")
    s = sites.assign(site=sites.site.astype(str)).drop_duplicates("site").set_index("site")
    if "zone" not in s:
        raise ValueError("correlation neighbours need a sites table with a zone column")
    cols = [c for c in mx.columns if str(c) in s.index]
    zone = pd.Series({c: s.at[str(c), "zone"] for c in cols})
    zero = mx[cols] <= 0
    ref_mean = sm.loc[ref, cols].sum() / (hours * len(ref))
    ref_ok = (~zero.loc[ref]).all() & (ref_mean > 0)
    z = _standardise(stats["daily"][cols].to_numpy(np.float64))
    pos = {c: k for k, c in enumerate(cols)}
    donors = {int(y): np.array([bool(ref_ok[c] and not zero.at[y, c]) for c in cols]) for y in mx.index}
    rows = []
    targets = [c for c in cols if zero[c].any()]
    for zn, grp in pd.Series(targets, dtype=object).groupby(zone.reindex(targets).fillna("__no_zone__").values):
        ts = list(grp)
        inzone = (zone == zn).to_numpy()
        corr = z[:, [pos[c] for c in ts]].T @ z                       # targets x all sites
        for i, c in enumerate(ts):
            yz = [int(y) for y in zero.index[zero[c]]]
            r = corr[i].copy()
            r[pos[c]] = -np.inf
            for y in yz:
                base = {"site": str(c), "year": y, "site_ref_mean": float(ref_mean[c]), "distance_km": np.nan}
                reason = ("all weather years zero: no reference mean" if len(yz) == len(zero.index)
                          else f"zero in a reference year {ref}" if not ref_ok[c] else "")
                ok = donors[y]
                best, scope = None, ""
                if not reason:
                    for mask, sc in ((ok & inzone, "zone"), (ok, "outside zone")):
                        if mask.any():
                            k = int(np.argmax(np.where(mask, r, -np.inf)))
                            if np.isfinite(r[k]):
                                best, scope = cols[k], sc
                                break
                    if best is None:
                        reason = f"no site with data in {y}"
                if reason:
                    rows.append({**base, "neighbour": None, "scale": np.nan, "neighbour_ref_mean": np.nan,
                                 "correlation": np.nan, "scope": "", "status": "unfilled: " + reason})
                    continue
                rows.append({**base, "neighbour": str(best), "scale": float(ref_mean[c] / ref_mean[best]),
                             "neighbour_ref_mean": float(ref_mean[best]), "correlation": round(float(r[pos[best]]), 4),
                             "scope": scope, "status": "filled" if scope == "zone" else "filled outside zone"})
    return pd.DataFrame(rows, columns=["site", "year", "neighbour", "distance_km", "correlation", "scope", "scale",
                                       "site_ref_mean", "neighbour_ref_mean", "status"])


def compare_with_list(fills: pd.DataFrame, zeroed: pd.DataFrame) -> dict:
    """Agreement with the dashboards' zeroed_pv_sites.csv (site, years_zero "2007;2008;..."; one row per site and zone)."""
    z = zeroed.assign(site=zeroed.site.astype(str)).drop_duplicates("site")
    listed = {(s, int(y)) for s, ys in zip(z.site, z.years_zero.astype(str)) for y in ys.split(";") if y.strip()}
    found = set(zip(fills.site.astype(str), fills.year.astype(int)))
    return {"listed_site_years": len(listed), "found_site_years": len(found), "in_both": len(listed & found),
            "listed_not_found": sorted(f"{s}:{y}" for s, y in listed - found)[:50],
            "found_not_listed": sorted(f"{s}:{y}" for s, y in found - listed)[:50]}


def filled(fills: pd.DataFrame) -> pd.Series:
    return fills.status.astype(str).str.startswith("filled")


def fill_summary(fills: pd.DataFrame) -> dict:
    f = fills[filled(fills)]
    if f.empty:
        return {}
    uses = f.groupby("neighbour").size()
    out = {"distinct_neighbours": int(uses.size), "max_site_years_from_one_neighbour": int(uses.max()),
           "scale_median": float(f.scale.median()), "scale_min": float(f.scale.min()), "scale_max": float(f.scale.max()),
           "scale_share_0.7_1.3": float(f.scale.between(0.7, 1.3).mean())}
    if f.distance_km.notna().any():
        q = f.distance_km.quantile([0.5, 0.9, 0.99]).round(1)
        out.update({"distance_km_median": float(q.loc[0.5]), "distance_km_p90": float(q.loc[0.9]),
                    "distance_km_p99": float(q.loc[0.99]), "distance_km_max": float(f.distance_km.max())})
    if "correlation" in f and f.correlation.notna().any():
        q = f.correlation.quantile([0.01, 0.1, 0.5]).round(4)
        out.update({"correlation_min": float(f.correlation.min()), "correlation_p01": float(q.loc[0.01]),
                    "correlation_p10": float(q.loc[0.1]), "correlation_median": float(q.loc[0.5]),
                    "site_years_filled_outside_zone": int((f.scope == "outside zone").sum())})
    return out


def write_repaired(src: Path, out: Path, fills: pd.DataFrame, first_year=2007, hours=8760, batch_rows=2190) -> dict:
    """Copy src to out batch by batch, replacing each filled site-year. Returns clipped-hour counts by (site, year)."""
    import pyarrow as pa
    pq = _pq()
    f = pq.ParquetFile(src)
    years = years_of(f.metadata.num_rows, first_year, hours)
    names = f.schema_arrow.names
    pos = {str(n): k for k, n in enumerate(names)}
    todo = {}
    for r in fills[filled(fills)].itertuples():
        todo.setdefault(int(r.year), []).append((pos[r.site], pos[r.neighbour], float(r.scale), r.site))
    clipped = {}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".partial")
    w = pq.ParquetWriter(tmp, f.schema_arrow)
    row = 0
    try:
        for b in f.iter_batches(batch_size=batch_rows):
            arrays = list(b.columns)
            n = b.num_rows
            start = 0
            pieces = []
            while start < n:                                              # split at weather-year boundaries
                y = years[(row + start) // hours]
                take = min(n - start, hours - (row + start) % hours)
                pieces.append((y, start, take))
                start += take
            for y, s0, tk in pieces:
                for k_site, k_nb, scale, site in todo.get(y, []):
                    col = arrays[k_site].to_numpy(zero_copy_only=False).astype(float).copy()
                    nb = arrays[k_nb].to_numpy(zero_copy_only=False).astype(float)
                    v = nb[s0:s0 + tk] * scale
                    clipped[(site, y)] = clipped.get((site, y), 0) + int((v > 1.0).sum())
                    col[s0:s0 + tk] = np.clip(v, 0.0, 1.0)
                    arrays[k_site] = pa.array(col, type=f.schema_arrow.field(k_site).type, from_pandas=True)
            w.write_batch(pa.RecordBatch.from_arrays(arrays, schema=f.schema_arrow))
            row += n
    finally:
        w.close()
    os.replace(tmp, out)
    return clipped


def _stat(p: Path) -> dict:
    st = Path(p).stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def sha256(p: Path, chunk=1 << 24) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def repair_folder(src_dir: Path, out_dir: Path, profile_name: str, sites: pd.DataFrame, first_year=2007, hours=8760,
                  ref_years=(2012, 2013), batch_rows=2190, link_others=True, with_hash=False, log=print,
                  zeroed: pd.DataFrame | None = None, warn_km: float = 100.0, neighbour_method: str = "distance",
                  warn_corr: float = 0.8) -> dict:
    """Write out_dir: the repaired profile file (same name), the source folder's other files (hard links, else copies),
    the fill log and the manifest. Returns the manifest."""
    src_dir, out_dir = Path(src_dir), Path(out_dir)
    src = src_dir / profile_name
    if out_dir.resolve() == src_dir.resolve():
        raise ValueError("out_dir must differ from the source profiles folder (the source is never changed)")
    out_dir.mkdir(parents=True, exist_ok=True)
    pq = _pq()
    cols = _site_columns(pq.ParquetFile(src).schema_arrow.names, sites)
    if neighbour_method not in ("distance", "correlation"):
        raise ValueError(f"neighbour_method must be distance or correlation, not {neighbour_method!r}")
    corr = neighbour_method == "correlation"
    stats = scan(src, cols, first_year, hours, batch_rows, daily_years=ref_years if corr else ())
    before = zero_site_years(stats, sites)
    fills = plan_correlation(stats, sites, ref_years) if corr else plan(stats, sites, ref_years)
    log(f"pv_profile_repair: {before.site.nunique()} sites, {len(before)} site-years all zero with capacity; "
        f"{filled(fills).sum()} site-years filled ({neighbour_method} neighbours), {(~filled(fills)).sum()} unfillable")
    summ = fill_summary(fills)
    if summ and corr:
        log(f"pv_profile_repair: daily correlation with the neighbour: min {summ['correlation_min']}, p1 "
            f"{summ['correlation_p01']}, p10 {summ['correlation_p10']}, median {summ['correlation_median']}; "
            f"{summ['site_years_filled_outside_zone']} site-years filled outside their zone; {summ['distinct_neighbours']}"
            f" distinct neighbours, at most {summ['max_site_years_from_one_neighbour']} site-years from one; scale "
            f"{summ['scale_min']:.3f}-{summ['scale_max']:.3f}")
        low = fills[filled(fills) & (fills.correlation < warn_corr)]
        if len(low):
            log(f"pv_profile_repair: WARNING {len(low)} site-years filled from a neighbour with daily correlation below "
                f"{warn_corr} ({low.site.nunique()} sites); see the log")
    elif summ:
        log(f"pv_profile_repair: fill distance median {summ['distance_km_median']} km, p90 {summ['distance_km_p90']} km, "
            f"max {summ['distance_km_max']} km; {summ['distinct_neighbours']} distinct neighbours, at most "
            f"{summ['max_site_years_from_one_neighbour']} site-years from one; scale {summ['scale_min']:.3f}-"
            f"{summ['scale_max']:.3f}")
        far = fills[(fills.status == "filled") & (fills.distance_km > warn_km)]
        if len(far):
            log(f"pv_profile_repair: WARNING {len(far)} site-years filled from more than {warn_km} km away "
                f"({far.site.nunique()} sites); see the log")
    agree = compare_with_list(fills, zeroed) if zeroed is not None else None
    if agree:
        log(f"pv_profile_repair: zeroed list {agree['listed_site_years']} site-years, found {agree['found_site_years']}, "
            f"in both {agree['in_both']}")
    clipped = write_repaired(src, out_dir / profile_name, fills, first_year, hours, batch_rows)
    fills["clipped_hours"] = [clipped.get((r.site, int(r.year)), 0) for r in fills.itertuples()]
    fills.to_csv(out_dir / LOG, index=False)
    after = zero_site_years(scan(out_dir / profile_name, cols, first_year, hours, batch_rows), sites)
    linked, copied = [], []
    for p in sorted(src_dir.iterdir()):
        if p.name == profile_name or not p.is_file() or (out_dir / p.name).exists():
            continue
        try:
            if not link_others:
                raise OSError
            os.link(p, out_dir / p.name)
            linked.append(p.name)
        except OSError:
            shutil.copy2(p, out_dir / p.name)
            copied.append(p.name)
    man = {"method": "neighbour_fill", "profile_file": profile_name, "source_dir": str(src_dir.resolve()),
           "source": _stat(src), "output": _stat(out_dir / profile_name),
           "first_weather_year": first_year, "hours_per_year": hours, "reference_years": list(ref_years),
           "site_years_zero_before": int(len(before)), "sites_zero_before": int(before.site.nunique()),
           "neighbour_method": neighbour_method,
           "neighbour_basis": ("daily output correlation over the reference years, same zone first" if corr
                               else "great-circle distance between site centroids"),
           "site_years_filled": int(filled(fills).sum()),
           "site_years_unfillable": int((~filled(fills)).sum()),
           "zero_site_years_with_capacity_after": int(len(after)),
           "clipped_hours": int(fills.clipped_hours.sum()), "fill_summary": summ, "zeroed_list_check": agree,
           "other_files_linked": linked, "other_files_copied": copied,
           "log": LOG}
    if with_hash:
        man["source"]["sha256"] = sha256(src)
        man["output"]["sha256"] = sha256(out_dir / profile_name)
    (out_dir / MANIFEST).write_text(json.dumps(man, indent=2))
    log(f"pv_profile_repair: wrote {out_dir / profile_name}; zero site-years with capacity after: {len(after)}")
    return man


def check_folder(folder: Path, sites: pd.DataFrame | None = None, full=False) -> dict:
    """The build's check: the manifest exists, matches the file (size, mtime) and records no zero site-year with
    capacity; with full=True the file is rescanned (needs the sites table). Raises ValueError otherwise."""
    folder = Path(folder)
    mf = folder / MANIFEST
    if not mf.exists():
        raise ValueError(f"pv_profile_repair: {mf} not found; run s0_workflow/scripts/repair_pv_profiles.py first")
    man = json.loads(mf.read_text())
    p = folder / man["profile_file"]
    if _stat(p) != {k: man["output"][k] for k in ("size", "mtime_ns")}:
        raise ValueError(f"pv_profile_repair: {p} changed since the repair (size/mtime differ from the manifest)")
    if man["zero_site_years_with_capacity_after"]:
        raise ValueError(f"pv_profile_repair: {man['zero_site_years_with_capacity_after']} site-years with capacity "
                         f"are still all zero in {p} (see {man['log']})")
    if full:
        if sites is None:
            raise ValueError("pv_profile_repair: verify full needs the sites table")
        cols = _site_columns(_pq().ParquetFile(p).schema_arrow.names, sites)
        z = zero_site_years(scan(p, cols, man["first_weather_year"], man["hours_per_year"]), sites)
        if len(z):
            raise ValueError(f"pv_profile_repair: rescan finds {len(z)} zero site-years with capacity in {p}")
    return man
