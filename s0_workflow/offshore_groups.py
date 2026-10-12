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
    return man


def mw_by_zone(gen_info: pd.DataFrame) -> pd.DataFrame:
    """Offshore capacity limits written to the case, by zone and technology (gen_tech starting OffShoreWind)."""
    if "gen_capacity_limit_mw" not in gen_info or "gen_tech" not in gen_info:
        return pd.DataFrame(columns=["gen_load_zone", "gen_tech", "mw"])
    o = gen_info[gen_info.gen_tech.astype(str).str.startswith("OffShoreWind")]
    o = o.assign(gen_capacity_limit_mw=pd.to_numeric(o.gen_capacity_limit_mw, errors="coerce"))
    return o.groupby(["gen_load_zone", "gen_tech"]).gen_capacity_limit_mw.sum().rename("mw").reset_index()


def check_case(folder: Path, true_by_zone: pd.DataFrame, tol=0.001) -> pd.DataFrame:
    """Per zone: offshore capacity limits in gen_info.csv against the true resource (sum over turbine types and pref
    sites). Returns the table; ratio 1 = the resource offered once."""
    gi = pd.read_csv(Path(folder) / "gen_info.csv", na_values=["."])
    case = mw_by_zone(gi).groupby("gen_load_zone").mw.sum().rename("case_mw")
    tr = true_by_zone.groupby("zone").mw.sum().rename("true_mw")
    t = pd.concat([case, tr], axis=1).fillna(0.0)
    t["ratio"] = t.case_mw / t.true_mw.where(t.true_mw > 0)
    t["ok"] = (t.case_mw - t.true_mw).abs() <= tol * t.true_mw.clip(lower=1.0)
    return t.reset_index().rename(columns={"index": "zone"})
