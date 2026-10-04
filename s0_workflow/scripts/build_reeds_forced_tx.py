"""Forced (certain) transmission additions for the S0 new-defaults cases from a pinned ReEDS release
(s0_production.forced_tx: reeds_certain; CHANGES §54).

Source: pg/extra_inputs/transmission/reeds_<release>/hvdc_planned-baseline.csv, the lines ReEDS includes in all runs,
rows with certain == 1 (REEDS_RELEASE.yml in that folder explains the file choice). Each line's endpoints are
placed in our zones (p1-p134) with the ReEDS BA shapes (pg/extra_inputs/US_PCA_region); its in-service year goes to
the first model period at or after it, as the case build does for transmission_connections.csv. Nothing already in
the 2024 starting capacity is kept: lines in service by 2024, in ReEDS's hvdc_existing.csv, or on a zone pair of
transmission_capacity_init_nonAC_ba.csv stop the build of the table. Length and losses of the corridor: the
case's own (network_costs_ReEDS.csv, the user_transmission_costs file), else transmission_connections.csv.

Writes:
  pg/extra_inputs/transmission/forced_tx_reeds_certain_<release>.csv   the case build's forced-line table
  pg/extra_inputs/transmission/forced_tx_comparison_<release>.csv      MW, MW-km and interregional share by period,
                                                                       reeds_certain against named_projects
usage (repo root): python s0_workflow/scripts/build_reeds_forced_tx.py [--release 2026.09.21] [--check]
  mapping endpoints to zones needs pyshp, shapely and pyproj; without them (--check) the zones of the committed
  table are reused and everything else is rebuilt and compared.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import coal_spec as cs  # noqa: E402

TX = REPO / "pg/extra_inputs/transmission"
SHAPES = REPO / "pg/extra_inputs/US_PCA_region/US_PCA_region.shp"
PERIODS = (2028, 2030, 2035, 2040, 2045)          # the S0 stages (s0_production.yml period_spans)
BASIS_YEAR = 2024                                   # starting capacity: NARIS 2024 ITLs and the 2024 non-AC lines
COLUMNS = ["from_zone", "to_zone", "project_name", "new_cap_mw", "new_cap_year", "period", "trtype", "certain",
           "trans_length_km", "trans_efficiency", "length_source", "from_transreg", "to_transreg"]


def table_path(release: str) -> Path:
    return TX / f"forced_tx_reeds_certain_{release}.csv"


def comparison_path(release: str) -> Path:
    return TX / f"forced_tx_comparison_{release}.csv"


def zone_of_points(points: dict) -> dict:
    """name -> zone of each (lon, lat), with the ReEDS BA shapes (needs pyshp, shapely, pyproj)."""
    import shapefile
    from pyproj import CRS, Transformer
    from shapely.geometry import Point, shape
    r = shapefile.Reader(str(SHAPES))
    crs = CRS.from_wkt(SHAPES.with_suffix(".prj").read_text())
    tf = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    shapes = [(rec["rb"], shape(s.__geo_interface__)) for rec, s in zip(r.records(), r.shapes())]
    out = {}
    for k, (lon, lat) in points.items():
        p = Point(*tf.transform(lon, lat))
        hit = [z for z, g in shapes if g.contains(p)]
        if len(hit) != 1:
            raise ValueError(f"{k} ({lon}, {lat}) is in {len(hit)} ReEDS zones: {hit}")
        out[k] = hit[0]
    return out


def period_of(year: int) -> int | None:
    return next((p for p in PERIODS if p >= int(year)), None)


def corridors() -> dict:
    """frozenset(zone pair) -> (length km, efficiency, source): the case's line, else transmission_connections."""
    out = {}
    tc = pd.read_csv(TX / "transmission_connections.csv")
    for r in tc.itertuples():
        out[frozenset([r.from_zone, r.to_zone])] = (float(r.trans_length_km), float(r.trans_efficiency),
                                                    "transmission_connections.csv")
    nc = pd.read_csv(TX / "network_costs_ReEDS.csv").rename(columns={"total_mw-km_per_mw": "km"})
    for r in nc.itertuples():
        out[frozenset([r.start_region, r.dest_region])] = (float(r.km), 1 - float(r.total_line_loss_frac),
                                                           "network_costs_ReEDS.csv")
    return out


def build(release: str, zones: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    src = TX / f"reeds_{release}"
    lines = pd.read_csv(src / "hvdc_planned-baseline.csv")
    certain = lines[lines.certain == 1].copy()
    pts = {}
    for r in certain.itertuples():
        pts[f"{r.name}|from"], pts[f"{r.name}|to"] = (r.from_lon, r.from_lat), (r.to_lon, r.to_lat)
    zones = zones if zones is not None else zone_of_points(pts)
    existing = set(pd.read_csv(src / "hvdc_existing.csv").name)
    nonac = pd.read_csv(TX / "transmission_capacity_init_nonAC_ba.csv")
    nonac_pairs = {frozenset([a, b]) for a, b in zip(nonac.r, nonac.rr)}
    hier = pd.read_csv(REPO / "hierarchy.csv").set_index("ba").transreg
    cor = corridors()
    rows = []
    for r in certain.itertuples():
        a, b = sorted([zones[f"{r.name}|from"], zones[f"{r.name}|to"]], key=lambda z: int(z[1:]))
        pair = frozenset([a, b])
        if int(r.year_online) <= BASIS_YEAR or r.name in existing or pair in nonac_pairs:
            raise ValueError(f"{r.name} ({a}-{b}, {r.year_online}) may already be in the {BASIS_YEAR} starting "
                             f"capacity: review before forcing it")
        if pair not in cor:
            raise ValueError(f"{r.name}: no corridor {a}-{b} in network_costs_ReEDS.csv or transmission_connections.csv")
        km, eff, how = cor[pair]
        rows.append({"from_zone": a, "to_zone": b, "project_name": r.name, "new_cap_mw": float(r.MW),
                     "new_cap_year": int(r.year_online), "period": period_of(r.year_online), "trtype": r.trtype,
                     "certain": int(r.certain), "trans_length_km": km, "trans_efficiency": eff, "length_source": how,
                     "from_transreg": hier[a], "to_transreg": hier[b]})
    t = pd.DataFrame(rows, columns=COLUMNS)
    return t, compare(t)


def named_projects() -> pd.DataFrame:
    """The current forced lines (transmission_connections.csv new_cap_mw > 0, forced_tx: named_projects)."""
    tc = pd.read_csv(TX / "transmission_connections.csv")
    n = tc[tc.new_cap_mw.fillna(0) > 0].copy()
    cor = corridors()
    hier = pd.read_csv(REPO / "hierarchy.csv").set_index("ba").transreg
    n["trans_length_km"] = [cor[frozenset([a, b])][0] for a, b in zip(n.from_zone, n.to_zone)]
    n["period"] = n.new_cap_year.map(period_of)
    n["from_transreg"], n["to_transreg"] = n.from_zone.map(hier), n.to_zone.map(hier)
    return n


def compare(t: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, d in (("reeds_certain", t), ("named_projects", named_projects())):
        for p in PERIODS + ("all",):
            x = d if p == "all" else d[d.period == p]
            inter = x.from_transreg != x.to_transreg
            mw = float(x.new_cap_mw.sum())
            rows.append({"option": name, "period": p, "lines": len(x), "MW": round(mw, 1),
                         "MW_km": round(float((x.new_cap_mw * x.trans_length_km).sum()), 0),
                         "interregional_MW": round(float(x.new_cap_mw[inter].sum()), 1),
                         "interregional_share": round(float(x.new_cap_mw[inter].sum()) / mw, 4) if mw else 0.0})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", default="2026.09.21")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    zones = None
    try:
        import shapefile, shapely, pyproj  # noqa: F401, E401
    except ImportError:
        if not a.check:
            sys.exit("mapping endpoints needs pyshp, shapely and pyproj (or use --check)")
        old = pd.read_csv(table_path(a.release))
        zones = {}
        src = pd.read_csv(TX / f"reeds_{a.release}" / "hvdc_planned-baseline.csv").set_index("name")
        for r in old.itertuples():                  # zones of the committed table (endpoint order from the source)
            f, t = (r.from_zone, r.to_zone)
            zones[f"{r.project_name}|from"], zones[f"{r.project_name}|to"] = f, t
        print("pyshp/shapely/pyproj not installed: zones taken from the committed table", list(src.index))
    t, c = build(a.release, zones)
    out = {table_path(a.release): cs.csv_text(t, index=False), comparison_path(a.release): cs.csv_text(c, index=False)}
    if a.check:
        bad = [p for p, s in out.items() if not p.exists() or p.read_text() != s]
        for p in bad:
            print("differs from a fresh build:", p.relative_to(REPO))
        sys.exit(1 if bad else 0)
    for p, s in out.items():
        p.write_text(s)
        print("wrote", p.relative_to(REPO))
    print(c.to_string(index=False))


if __name__ == "__main__":
    main()
