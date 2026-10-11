"""NGMM interregional pipeline capacity in the AEO2026 reference case vs Stage 2a's minimum expansion (CHANGES §104).

Source: the NEMS restart file published in EIAgov/NEMS (commit 1bfbb2a) for the reference case,
output/aeo2026/cb2026/d011626a/restart.npz. NOTE: this is run d011626a; the published AEO2026 tables (and Stage 1)
are run d021826b, whose restart file is not in the repo. NGMM writes common block NGRPT to the restart file:
NGCAPS(12, 6, MNUMYR) = capacity entering each NGMM flow region by arc and NGFLOWS = the flows on the same arcs,
Bcf/yr, years 1990 + index. includes/ngrpt says NGARCMP maps the arcs, but its node ids do not line up with the
arcs that carry flow, so arcs are identified here by matching NGFLOWS to Table 64 (d021826b): row r is the r-th
destination region and column k the k-th Table 64 source of that region, in Table 64 order. compare() reports how
well each matched arc's flows agree; columns that carry flow but have no Table 64 row are kept as 'unidentified'.

NGMM builds pipeline capacity endogenously after NoBuildYear (2027, ngassumptions.txt); its planned additions
(ngcapacity.txt 'PlannedCapacity') are 2025-2028 and none crosses the Mid-Atlantic/Ohio -> Eastern Midwest boundary.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import aeo

RESTART = "nems/restart_cb2026_d011626a.npz"
DEST = ["NewEngland", "MidAtlantic_OH", "EasternMidwest", "Southeast", "FL", "SouthCentral", "RockiesPlains",
        "NorthernGreatPlains", "AZ_NM", "CA", "OR_WA"]          # NGCAPS rows 1-11 (row 12 has no Table 64 region)


def ngmm_arcs(raw: Path, t64: pd.DataFrame) -> pd.DataFrame:
    """src, dst, year, cap_bcf, flow_bcf, t64_bcf (Table 64, d021826b) for every NGCAPS arc with capacity."""
    z = np.load(raw / RESTART)
    cap, flow = z["NGRPT/ngcaps"], z["NGRPT/ngflows"]
    years = 1990 + np.arange(cap.shape[2])
    t = t64[t64.year >= 2025]
    rows = []
    for r in range(cap.shape[0]):
        dst = DEST[r] if r < len(DEST) else f"row{r + 1}"
        srcs = list(dict.fromkeys(t[t.dst == dst].src)) if r < len(DEST) else []
        tv = t[t.dst == dst].set_index(["src", "year"]).bcf
        for k in range(cap.shape[1]):
            if not (cap[r, k] > 0).any():
                continue
            src = srcs[k] if k < len(srcs) else f"unidentified{k + 1}"
            for i, y in enumerate(years):
                rows.append((src, dst, int(y), float(cap[r, k, i]), float(flow[r, k, i]),
                             float(tv.get((src, int(y)), np.nan))))
    return pd.DataFrame(rows, columns=["src", "dst", "year", "cap_bcf", "flow_bcf", "t64_bcf"])


def compare(raw: Path, ref: Path, out: Path, years=(2035, 2045), base_year=2025) -> dict:
    t64 = aeo.table64_arcs(raw / "aeo2026" / "suptab_64.xlsx", pd.read_csv(ref / "table64_regions.csv"))
    a = ngmm_arcs(raw, t64)
    m = a.dropna(subset=["t64_bcf"])
    m = m[(m.year >= 2025) & (m.t64_bcf.abs() > 50)]
    match = (m.flow_bcf - m.t64_bcf).abs().sum() / m.t64_bcf.abs().sum()
    b = a[a.year == base_year].set_index(["src", "dst"]).cap_bcf
    hubs = pd.read_csv(ref / "hubs.csv").set_index("hub").flow_region
    s2 = pd.read_csv(out / "arc_flows.csv")
    s2 = s2[s2.variant == "status_rule_min_expansion"].assign(src_r=lambda d: d.src.map(hubs),
                                                                 dst_r=lambda d: d.dst.map(hubs))
    rows = []
    for y in years:
        ng = a[a.year == y].set_index(["src", "dst"])
        e = s2[(s2.year == y) & (s2.expansion > 0)]
        # nameplate MMcf/d (expansion_mmcfd = annual expansion / (utilisation cap x days)), like NGCAPS capacity
        ex = e.groupby(["src_r", "dst_r"]).expansion_mmcfd.sum() / 1000     # Bcf/d
        arcs = e.assign(a=e.src + "->" + e.dst + ":" + (e.expansion_mmcfd / 1000).round(1).astype(str)) \
            .groupby(["src_r", "dst_r"]).a.apply(" ".join)
        keys = sorted(set(ng.index) | set(ex.index))
        for k in keys:
            add = float(ng.cap_bcf.get(k, np.nan) - b.get(k, np.nan))
            rows.append(dict(year=y, src=k[0], dst=k[1], ngmm_cap_base_bcf=b.get(k, np.nan),
                             ngmm_cap_bcf=ng.cap_bcf.get(k, np.nan), ngmm_added_since_base_bcf=add,
                             ngmm_added_bcfd=add / 365, ngmm_flow_bcf=ng.flow_bcf.get(k, np.nan),
                             stage2a_min_expansion_bcfd=float(ex.get(k, 0.0)),
                             stage2a_arcs=arcs.get(k, "")))
    d = pd.DataFrame(rows)
    d = d[(d.ngmm_added_since_base_bcf.abs() > 1) | (d.stage2a_min_expansion_bcfd > 0)]
    d.to_csv(out / "nems_capacity_vs_stage2a.csv", index=False, float_format="%.2f")
    a.to_csv(out / "nems_ngmm_capacity_d011626a.csv", index=False, float_format="%.1f")
    return {"flow_match_weighted_abs_error": float(match), "rows": d}
