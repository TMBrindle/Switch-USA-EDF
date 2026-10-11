"""Repair of the zeroed reV solar profiles (CHANGES §111; s0_production.pv_profile_repair).

solar_rev_profiles_20240801.parquet (RESOURCE_GROUP_PROFILES) holds one column per reV site and one row per hour of
PowerGenome's seven 365-day weather years, 2007-2013, in order. The dashboards session found 5,390 sites (blocks of 539
every 6,468 columns) all zero in 2007-2011 and normal in 2012-13. Wind is unaffected.

neighbour_fill, for each site-year that is all zero while the site has output in other years:
    fill = the nearest unaffected site's profile in the SAME weather year (keeping the alignment with load and wind)
           x (the site's mean over the reference years 2012-13 / the neighbour's mean over the same years),
           clipped to [0, 1] (clipped hours are counted in the log)
Unaffected = output in every weather year and a positive reference mean. Distance: great circle between site centroids
(sites table: site, latitude, longitude, capacity_mw).

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


def scan(path: Path, columns: list[str], first_year=2007, hours=8760, batch_rows=2190) -> dict:
    """Per column and weather year: max and sum. Returns {"years", "max": DataFrame, "sum": DataFrame} (year x column)."""
    pq = _pq()
    f = pq.ParquetFile(path)
    years = years_of(f.metadata.num_rows, first_year, hours)
    mx = np.zeros((len(years), len(columns)))
    sm = np.zeros((len(years), len(columns)))
    row = 0
    for b in f.iter_batches(batch_size=batch_rows, columns=columns):
        a = np.column_stack([b.column(i).to_numpy(zero_copy_only=False) for i in range(b.num_columns)]).astype(float)
        a = np.nan_to_num(a)
        start = 0
        while start < len(a):
            y = (row + start) // hours
            take = min(len(a) - start, hours - (row + start) % hours)
            part = a[start:start + take]
            mx[y] = np.maximum(mx[y], part.max(axis=0))
            sm[y] += part.sum(axis=0)
            start += take
        row += len(a)
    return {"years": years, "max": pd.DataFrame(mx, index=years, columns=columns),
            "sum": pd.DataFrame(sm, index=years, columns=columns), "hours": hours}


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
    """One row per zeroed site-year: neighbour, distance and scale (or the reason it can't be filled)."""
    from scipy.spatial import cKDTree
    mx, sm, hours = stats["max"], stats["sum"], stats["hours"]
    ref = [y for y in ref_years if y in mx.index]
    if not ref:
        raise ValueError(f"reference years {ref_years} not in the file's weather years {list(mx.index)}")
    s = sites.assign(site=sites.site.astype(str)).drop_duplicates("site").set_index("site")
    cols = [c for c in mx.columns if str(c) in s.index]
    zero = mx[cols] <= 0
    ref_mean = sm.loc[ref, cols].sum() / (hours * len(ref))
    ref_ok = (~zero.loc[ref]).all()                                       # output in every reference year
    clean = [c for c in cols if not zero[c].any() and ref_mean[c] > 0]
    if not clean:
        raise ValueError("no unaffected site to fill from")
    tree = cKDTree(_xyz(s.loc[[str(c) for c in clean], "latitude"], s.loc[[str(c) for c in clean], "longitude"]))
    rows = []
    for c in cols:
        yz = [int(y) for y in zero.index[zero[c]]]
        if not yz:
            continue
        reason = ""
        if len(yz) == len(zero.index):
            reason = "all weather years zero: no reference mean"
        elif not ref_ok[c] or ref_mean[c] <= 0:
            reason = f"zero in a reference year {ref}"
        if reason:
            rows += [{"site": str(c), "year": y, "neighbour": None, "distance_km": np.nan, "scale": np.nan,
                      "site_ref_mean": ref_mean[c], "neighbour_ref_mean": np.nan, "status": "unfilled: " + reason}
                     for y in yz]
            continue
        d, i = tree.query(_xyz([s.at[str(c), "latitude"]], [s.at[str(c), "longitude"]])[0])
        n = clean[int(i)]
        km = 2 * EARTH_KM * np.arcsin(min(1.0, d / 2))
        rows += [{"site": str(c), "year": y, "neighbour": str(n), "distance_km": round(float(km), 2),
                  "scale": float(ref_mean[c] / ref_mean[n]), "site_ref_mean": float(ref_mean[c]),
                  "neighbour_ref_mean": float(ref_mean[n]), "status": "filled"} for y in yz]
    return pd.DataFrame(rows, columns=["site", "year", "neighbour", "distance_km", "scale", "site_ref_mean",
                                       "neighbour_ref_mean", "status"])


def write_repaired(src: Path, out: Path, fills: pd.DataFrame, first_year=2007, hours=8760, batch_rows=2190) -> dict:
    """Copy src to out batch by batch, replacing each filled site-year. Returns clipped-hour counts by (site, year)."""
    import pyarrow as pa
    pq = _pq()
    f = pq.ParquetFile(src)
    years = years_of(f.metadata.num_rows, first_year, hours)
    names = f.schema_arrow.names
    pos = {str(n): k for k, n in enumerate(names)}
    todo = {}
    for r in fills[fills.status == "filled"].itertuples():
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
                  ref_years=(2012, 2013), batch_rows=2190, link_others=True, with_hash=False, log=print) -> dict:
    """Write out_dir: the repaired profile file (same name), the source folder's other files (hard links, else copies),
    the fill log and the manifest. Returns the manifest."""
    src_dir, out_dir = Path(src_dir), Path(out_dir)
    src = src_dir / profile_name
    if out_dir.resolve() == src_dir.resolve():
        raise ValueError("out_dir must differ from the source profiles folder (the source is never changed)")
    out_dir.mkdir(parents=True, exist_ok=True)
    pq = _pq()
    cols = _site_columns(pq.ParquetFile(src).schema_arrow.names, sites)
    stats = scan(src, cols, first_year, hours, batch_rows)
    before = zero_site_years(stats, sites)
    fills = plan(stats, sites, ref_years)
    log(f"pv_profile_repair: {before.site.nunique()} sites, {len(before)} site-years all zero with capacity; "
        f"{(fills.status == 'filled').sum()} site-years filled, {(fills.status != 'filled').sum()} unfillable")
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
           "site_years_filled": int((fills.status == "filled").sum()),
           "site_years_unfillable": int((fills.status != "filled").sum()),
           "zero_site_years_with_capacity_after": int(len(after)),
           "clipped_hours": int(fills.clipped_hours.sum()), "other_files_linked": linked, "other_files_copied": copied,
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
