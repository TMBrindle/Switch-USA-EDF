"""Offshore wind duplicates (CHANGES §120; s0_production.offshore_filter_fix).

The finding (dashboards session, dbc3637): in all 36 offshore zones, the three offshore projects resources.yml asks for
(`renewables_clusters` entries for offshorewind with turbine_type fixed / pref_site 1, fixed / 0, floating / 0) are the
same project: the same CPAs (every eligible one, floating included: 65.8 of p1's 68.3 GW), MW and profiles. Each carries
the full capacity limit, so the resource is offered three times: the committed s4x1 fedpol inputs give 2,985,509 MW to
each of OffShoreWind_Class3_Moderate_fixed_0, _fixed_1 and OffShoreWind_Class12_Moderate_floating_0.

Why: PowerGenome picks the resource group for a cluster entry by the entry's keys (technology, turbine_type, pref_site)
against the group's JSON attributes (`technology`, `metadata`, `profiles`, ...; see make_study_loads.py). The offshore
group in ReEDS-cpas-patched has no turbine_type or pref_site attribute, so every entry picks the one all-CPA group.

split_groups (off = v3.1): a copy of the RESOURCE_GROUPS folder in which the offshore group is replaced by one group per
(turbine_type, pref_site), each with its own JSON (the original's keys plus turbine_type and pref_site) and the subset of
the metadata; the profiles and site map are shared (same file names, so the same profile columns). Every other file is
hard-linked (else copied). turbine_type comes from a metadata column, or from water depth (floating deeper than
floating_depth_m); pref_site from a metadata column. The build points RESOURCE_GROUPS at the copy after checking its
manifest, and checks that the offshore capacity limits written per zone sum to the true resource (the manifest's MW
with LCOE at or below the clusters' limit), not to a multiple of it. MW by zone before (each entry carries the zone's
whole resource) and after are written to offshore_mw_by_zone.csv in each case.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pandas as pd

MANIFEST = "offshore_split_manifest.json"
BY_ZONE = "offshore_mw_by_zone.csv"


def _col(df: pd.DataFrame, name: str | None, what: str) -> str | None:
    if name is None:
        return None
    m = {c.lower(): c for c in df.columns}
    if name.lower() not in m:
        raise ValueError(f"offshore split: no {what} column {name!r}; columns are {list(df.columns)[:40]}")
    return m[name.lower()]


def classify(meta: pd.DataFrame, turbine_col=None, depth_col=None, floating_depth_m=60.0, pref_col=None) -> pd.DataFrame:
    """turbine_type (fixed | floating) and pref_site (int) for every CPA."""
    out = meta.copy()
    tc, dc, pc = _col(meta, turbine_col, "turbine_type"), _col(meta, depth_col, "depth"), _col(meta, pref_col, "pref_site")
    if tc:
        t = out[tc].astype(str).str.lower().str.strip()
        bad = ~t.isin(["fixed", "floating"])
        if bad.any():
            raise ValueError(f"offshore split: {turbine_col} values must be fixed / floating; got {sorted(t[bad].unique())[:10]}")
        out["turbine_type"] = t
    elif dc:
        out["turbine_type"] = (pd.to_numeric(out[dc], errors="coerce").abs() > float(floating_depth_m)).map(
            {True: "floating", False: "fixed"})
    else:
        raise ValueError("offshore split: give turbine_col, or depth_col with floating_depth_m")
    if pc is None:
        raise ValueError("offshore split: give pref_col (the CPA's preferred-site flag)")
    out["pref_site"] = pd.to_numeric(out[pc], errors="raise").astype(int)
    return out


def true_resource(meta: pd.DataFrame, region_col="ipm_region", mw_col="mw", lcoe_col="lcoe", lcoe_max=125.0,
                  by=("turbine_type", "pref_site")) -> pd.DataFrame:
    """MW by zone (and by turbine type and pref site) with LCOE at or below the clusters' filter."""
    m = meta[pd.to_numeric(meta[lcoe_col], errors="coerce") <= float(lcoe_max)]
    return m.groupby([region_col, *by])[mw_col].sum().rename("mw").reset_index().rename(columns={region_col: "zone"})


def split_folder(groups_dir: Path, out_dir: Path, group_json: str, turbine_col=None, depth_col=None,
                 floating_depth_m=60.0, pref_col=None, region_col="ipm_region", mw_col="mw", lcoe_col="lcoe",
                 lcoe_max=125.0, link_others=True, log=print) -> dict:
    """Write out_dir: groups_dir with the offshore group (group_json, relative) split by (turbine_type, pref_site)."""
    groups_dir, out_dir = Path(groups_dir), Path(out_dir)
    if out_dir.resolve() == groups_dir.resolve():
        raise ValueError("out_dir must differ from the resource groups folder (the source is never changed)")
    src_json = groups_dir / group_json
    g = json.loads(src_json.read_text())
    meta_path = src_json.parent / g["metadata"]
    meta = pd.read_csv(meta_path)
    cls = classify(meta, turbine_col, depth_col, floating_depth_m, pref_col)
    rc, mc, lc = _col(meta, region_col, "region"), _col(meta, mw_col, "MW"), _col(meta, lcoe_col, "LCOE")
    # copy the tree: everything linked or copied, except the offshore group's json and metadata
    skip = {src_json.resolve(), meta_path.resolve()}
    linked = copied = 0
    for p in groups_dir.rglob("*"):
        rel = p.relative_to(groups_dir)
        q = out_dir / rel
        if p.is_dir():
            q.mkdir(parents=True, exist_ok=True)
            continue
        if p.resolve() in skip or q.exists():
            continue
        q.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not link_others:
                raise OSError
            os.link(p, q)
            linked += 1
        except OSError:
            shutil.copy2(p, q)
            copied += 1
    stem = src_json.stem
    groups = []
    for (tt, ps), part in cls.groupby(["turbine_type", "pref_site"]):
        mname = f"{Path(g['metadata']).stem}_{tt}_{ps}.csv"
        part.drop(columns=["turbine_type", "pref_site"]).to_csv(
            out_dir / src_json.parent.relative_to(groups_dir) / mname, index=False)
        gj = {**g, "metadata": mname, "turbine_type": tt, "pref_site": int(ps)}
        jpath = out_dir / src_json.parent.relative_to(groups_dir) / f"{stem}_{tt}_{ps}.json"
        jpath.write_text(json.dumps(gj, indent=4))
        groups.append({"json": str(jpath.relative_to(out_dir)), "turbine_type": tt, "pref_site": int(ps),
                       "cpas": int(len(part)), "mw_all": float(part[mc].sum())})
    tr = true_resource(cls.rename(columns={rc: "ipm_region", mc: "mw", lc: "lcoe"}), lcoe_max=lcoe_max)
    total = true_resource(cls.rename(columns={rc: "ipm_region", mc: "mw", lc: "lcoe"}), lcoe_max=lcoe_max, by=())
    tr.to_csv(out_dir / "offshore_true_resource_by_zone.csv", index=False)
    sums = tr.groupby("zone").mw.sum()
    assert (abs(sums - total.set_index("zone").mw) < 1e-6).all()                # the split adds up to the whole
    man = {"method": "split_groups", "source_json": group_json, "groups": groups, "lcoe_max": float(lcoe_max),
           "turbine_basis": turbine_col or f"{depth_col} > {floating_depth_m} m", "pref_col": pref_col,
           "zones": int(total.shape[0]), "true_resource_mw": float(total.mw.sum()),
           "files_linked": linked, "files_copied": copied}
    (out_dir / MANIFEST).write_text(json.dumps(man, indent=2))
    names = ", ".join(f"{x['turbine_type']}/{x['pref_site']}" for x in groups)
    log(f"offshore split: {len(groups)} groups ({names}); "
        f"true resource {man['true_resource_mw'] / 1e3:,.1f} GW in {man['zones']} zones at LCOE <= {lcoe_max}")
    return man


def check_folder(folder: Path) -> dict:
    mf = Path(folder) / MANIFEST
    if not mf.exists():
        raise ValueError(f"offshore_filter_fix: {mf} not found; run s0_workflow/scripts/split_offshore_groups.py first")
    man = json.loads(mf.read_text())
    for gr in man["groups"]:
        if not (Path(folder) / gr["json"]).exists():
            raise ValueError(f"offshore_filter_fix: {gr['json']} missing from {folder}")
    broken = check_references(folder)                     # §134: every JSON PowerGenome will load must resolve
    if broken:
        raise ValueError(f"offshore_filter_fix: {len(broken)} JSONs in {folder} reference missing files: {broken[:5]}")
    return man


def mw_by_zone(gen_info: pd.DataFrame) -> pd.DataFrame:
    """Offshore capacity limits written to the case, by zone and technology (gen_tech starting OffShoreWind)."""
    if "gen_capacity_limit_mw" not in gen_info or "gen_tech" not in gen_info:
        return pd.DataFrame(columns=["gen_load_zone", "gen_tech", "mw"])
    o = gen_info[gen_info.gen_tech.astype(str).str.startswith("OffShoreWind")]
    o = o.assign(gen_capacity_limit_mw=pd.to_numeric(o.gen_capacity_limit_mw, errors="coerce"))
    return o.groupby(["gen_load_zone", "gen_tech"]).gen_capacity_limit_mw.sum().rename("mw").reset_index()


TECH_GROUP = r"_(fixed|floating)_(\d)$"


def case_groups(techs) -> set | None:
    """(turbine_type, pref_site) of each offshore technology name (..._fixed_1, ..._floating_0), or None if any name
    doesn't follow that pattern."""
    out = set()
    for t in techs:
        m = pd.Series([str(t)]).str.extract(TECH_GROUP, flags=2).iloc[0]
        if m.isna().any():
            return None
        out.add((m[0].lower(), int(m[1])))
    return out


def check_case(folder: Path, true_by_zone: pd.DataFrame, tol=0.001) -> pd.DataFrame:
    """Per zone: offshore capacity limits in gen_info.csv against the true resource of the groups the case's offshore
    technologies come from (§134: by turbine type and pref site where the names carry them, e.g. 3 of the 4 groups;
    else every group). Returns the table; ratio 1 = each requested group's resource offered once."""
    gi = pd.read_csv(Path(folder) / "gen_info.csv", na_values=["."])
    byz = mw_by_zone(gi)
    case = byz.groupby("gen_load_zone").mw.sum().rename("case_mw")
    tz = true_by_zone
    req = case_groups(byz.gen_tech.unique())
    if req and {"turbine_type", "pref_site"} <= set(tz.columns):
        key = list(zip(tz.turbine_type.astype(str).str.lower(), tz.pref_site.map(_pref)))
        tz = tz[[k in req for k in key]]
    tr = tz.groupby("zone").mw.sum().rename("true_mw")
    t = pd.concat([case, tr], axis=1).fillna(0.0)
    t["ratio"] = t.case_mw / t.true_mw.where(t.true_mw > 0)
    t["ok"] = (t.case_mw - t.true_mw).abs() <= tol * t.true_mw.clip(lower=1.0)
    return t.reset_index().rename(columns={"index": "zone"})


# ---- §134: four offshore JSONs sharing one metadata file (the real ReEDS-cpas-patched layout) ----------------------
#
# ReEDS-cpas-patched has offshorewind_{fixed,floating}_{false,true}.json, each with its own turbine_type and pref_site,
# all pointing at the one offshorewind_lcoe_ReEDS.csv (every CPA). Per-type CSVs are in the folder too, but no JSON uses
# them, and they predate patch_pg_resource_groups.py: that script rewrote only the shared file (ipm_region_y ->
# ipm_region; CPAs nearest MD/RI moved to p123/p133; latitude/longitude added). So the per-type CSVs are used as
# MEMBERSHIP LISTS only (which CPAs each type has, checked to partition the shared file exactly), and each JSON is
# pointed at a new metadata file holding the shared, patched rows of its members.

DATA_EXT = (".csv", ".parquet", ".csv.gz")


def json_refs(j: dict) -> list[str]:
    """File names a resource-group JSON refers to (string values ending in a data extension), relative to its folder."""
    return [v for v in j.values() if isinstance(v, str) and v.lower().endswith(DATA_EXT)]


def check_references(folder: Path) -> list[dict]:
    """Every JSON under folder (PowerGenome loads **/*.json): each referenced file must exist next to it. Returns the
    broken references ([] = pass)."""
    bad = []
    for p in sorted(Path(folder).rglob("*.json")):
        if p.name == MANIFEST:
            continue
        try:
            j = json.loads(p.read_text())
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(j, dict):
            continue
        for ref in json_refs(j):
            if not (p.parent / ref).exists():
                bad.append({"json": str(p.relative_to(folder)), "missing": ref})
    return bad


def _pref(v) -> int:
    return 1 if str(v).strip().lower() in ("1", "true", "yes") else 0


def _read_any(p: Path) -> pd.DataFrame:
    return pd.read_parquet(p) if str(p).endswith(".parquet") else pd.read_csv(p)


def survey(groups_dir: Path, shared: str | None = None) -> pd.DataFrame:
    """The JSONs whose metadata is `shared` (or whose name or technology mentions offshore), with turbine_type, pref_site
    and metadata; for the runner to fill the --map."""
    groups_dir = Path(groups_dir)
    rows = []
    for p in sorted(groups_dir.rglob("*.json")):
        try:
            j = json.loads(p.read_text())
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(j, dict):
            continue
        meta = j.get("metadata")
        hit = (meta == shared) if shared else ("offshore" in (p.name + str(j.get("technology", ""))).lower())
        if hit:
            rows.append({"json": str(p.relative_to(groups_dir)), "technology": j.get("technology"),
                         "turbine_type": j.get("turbine_type"), "pref_site": j.get("pref_site"), "metadata": meta})
    return pd.DataFrame(rows, columns=["json", "technology", "turbine_type", "pref_site", "metadata"])


def split_by_type_files(groups_dir: Path, out_dir: Path, mapping: dict, id_col="CPA_ID", region_col="ipm_region",
                        mw_col="mw", lcoe_col="lcoe", lcoe_max=125.0, link_others=True, log=print) -> dict:
    """out_dir = groups_dir with each JSON in `mapping` ({json: per-type membership file}, paths relative to groups_dir)
    pointed at a new metadata file: the rows of its shared metadata file whose id is in the membership file. Stops unless
    the membership files partition the shared file exactly (every id once), every JSON using the shared file is mapped,
    and every JSON in out_dir references files that exist."""
    groups_dir, out_dir = Path(groups_dir), Path(out_dir)
    if out_dir.resolve() == groups_dir.resolve():
        raise ValueError("out_dir must differ from the resource groups folder (the source is never changed)")
    js = {k: json.loads((groups_dir / k).read_text()) for k in mapping}
    shared = {j["metadata"] for j in js.values()}
    if len(shared) != 1:
        raise ValueError(f"offshore split: the mapped JSONs use several metadata files {sorted(shared)}; map one set")
    shared = shared.pop()
    users = set(survey(groups_dir, shared).json)
    if users != set(mapping):
        raise ValueError(f"offshore split: JSONs using {shared} {sorted(users)} differ from the mapped ones "
                         f"{sorted(mapping)}; map every one (PowerGenome loads every JSON)")
    jdir = (groups_dir / next(iter(mapping))).parent
    if any((groups_dir / k).parent != jdir for k in mapping):
        raise ValueError("offshore split: the mapped JSONs must sit in one folder")
    meta = _read_any(jdir / shared)
    ic, rc, mc, lc = (_col(meta, id_col, "id"), _col(meta, region_col, "region"), _col(meta, mw_col, "MW"),
                      _col(meta, lcoe_col, "LCOE"))
    if meta[ic].duplicated().any():
        raise ValueError(f"offshore split: {shared} has duplicate {ic} values")
    ids_all = set(meta[ic])
    members, check = {}, {}
    for k, tf in mapping.items():
        t = _read_any(groups_dir / tf)
        tic = _col(t, id_col, "id")
        ids = list(t[tic])
        if len(set(ids)) != len(ids):
            raise ValueError(f"offshore split: {tf} lists some {tic} twice")
        extra = set(ids) - ids_all
        if extra:
            raise ValueError(f"offshore split: {tf} has {len(extra)} ids not in {shared} (e.g. {sorted(extra)[:5]})")
        members[k] = set(ids)
        diff = None
        trc = {c.lower(): c for c in t.columns}.get(region_col.lower())
        if trc:                                   # zones that differ from the patched shared file (the MD/RI moves)
            z = t[[tic, trc]].rename(columns={tic: "id", trc: "z_type"}).merge(
                meta[[ic, rc]].rename(columns={ic: "id", rc: "z_shared"}), on="id")
            diff = int((z.z_type.astype(str) != z.z_shared.astype(str)).sum())
        check[k] = {"membership_file": tf, "rows": len(ids), "zone_differs_from_shared": diff}
    seen = [i for s in members.values() for i in s]
    if len(seen) != len(set(seen)):
        raise ValueError("offshore split: the membership files overlap (an id in two types)")
    missing = ids_all - set(seen)
    if missing:
        raise ValueError(f"offshore split: {len(missing)} ids of {shared} are in no membership file "
                         f"(e.g. {sorted(missing)[:5]})")
    # copy the tree (links), except the mapped JSONs
    skip = {(groups_dir / k).resolve() for k in mapping}
    linked = copied = 0
    for p in groups_dir.rglob("*"):
        q = out_dir / p.relative_to(groups_dir)
        if p.is_dir():
            q.mkdir(parents=True, exist_ok=True)
            continue
        if p.resolve() in skip or q.exists():
            continue
        q.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not link_others:
                raise OSError
            os.link(p, q)
            linked += 1
        except OSError:
            shutil.copy2(p, q)
            copied += 1
    odir = out_dir / jdir.relative_to(groups_dir)
    groups, tr = [], []
    stem = Path(shared).name.split(".")[0]
    for k, j in js.items():
        part = meta[meta[ic].isin(members[k])]
        name = f"{stem}__{Path(k).stem}.csv"
        part.to_csv(odir / name, index=False)
        (out_dir / k).write_text(json.dumps({**j, "metadata": name}, indent=4))
        tt, ps = str(j.get("turbine_type")), _pref(j.get("pref_site"))
        f = part[pd.to_numeric(part[lc], errors="coerce") <= float(lcoe_max)]
        by = f.groupby(rc)[mc].sum()
        tr += [{"zone": z, "turbine_type": tt, "pref_site": ps, "mw": float(v)} for z, v in by.items()]
        groups.append({"json": k, "metadata": name, "turbine_type": tt, "pref_site": ps, "cpas": int(len(part)),
                       "mw_all": float(part[mc].sum()), "mw_at_lcoe_max": float(f[mc].sum()), **check[k]})
    trd = pd.DataFrame(tr, columns=["zone", "turbine_type", "pref_site", "mw"])
    trd.to_csv(out_dir / "offshore_true_resource_by_zone.csv", index=False)
    whole = meta[pd.to_numeric(meta[lc], errors="coerce") <= float(lcoe_max)]
    assert abs(trd.mw.sum() - whole[mc].sum()) < 1e-6 * max(1.0, whole[mc].sum())   # the types add up to the whole
    broken = check_references(out_dir)
    man = {"method": "type_files", "shared_metadata": shared, "groups": groups, "lcoe_max": float(lcoe_max),
           "id_col": ic, "zones": int(whole[rc].nunique()), "true_resource_mw": float(whole[mc].sum()),
           "partition": {"shared_rows": int(len(meta)), "membership_rows": int(len(seen)), "exact": True},
           "broken_references": broken, "files_linked": linked, "files_copied": copied}
    (out_dir / MANIFEST).write_text(json.dumps(man, indent=2))
    if broken:
        raise ValueError(f"offshore split: {len(broken)} JSON references to missing files in {out_dir}: {broken[:5]}")
    log("offshore split (type files): " + "; ".join(
        f"{g['turbine_type']}/{g['pref_site']} {g['cpas']:,} CPAs, {g['mw_at_lcoe_max'] / 1e3:,.1f} GW at LCOE <= "
        f"{lcoe_max:g}" for g in groups) + f"; whole {man['true_resource_mw'] / 1e3:,.1f} GW in {man['zones']} zones; "
        f"partition exact ({len(meta):,} CPAs); no broken references")
    return man
