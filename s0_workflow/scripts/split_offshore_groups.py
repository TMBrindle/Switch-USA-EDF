"""Write a copy of RESOURCE_GROUPS with the offshore wind group split by turbine type and pref site (CHANGES §120,
§134; s0_workflow/offshore_groups.py).

§134, the ReEDS-cpas-patched layout (several JSONs sharing one metadata file; VM_RECIPES.md recipe L):

  python s0_workflow/scripts/split_offshore_groups.py --survey --groups-dir <RESOURCE_GROUPS>
  python s0_workflow/scripts/split_offshore_groups.py --groups-dir <RESOURCE_GROUPS> --out-dir <copy> \
      --map <json>=<per-type membership csv> (once per JSON using the shared file) [--id-col CPA_ID] \
      [--region-col ipm_region] [--mw-col mw] [--lcoe-col lcoe] [--lcoe-max 125]

Older form (one JSON, split by metadata columns):

  python s0_workflow/scripts/split_offshore_groups.py --groups-dir pg_data/resource_groups \
      --out-dir pg_data/resource_groups_oswfix --json <offshore group json, relative to --groups-dir> \
      (--turbine-col <fixed/floating column> | --depth-col <water depth column> [--floating-depth-m 60]) \
      --pref-col <preferred-site column> [--region-col ipm_region] [--mw-col mw] [--lcoe-col lcoe] [--lcoe-max 125]

Find the offshore group's json with: grep -l -i offshorewind pg_data/resource_groups/**/*.json. Column names are
matched without regard to case; a missing one stops the run with the list of columns. The source folder is only read;
every other file is hard-linked (else copied, --no-links). Then set
s0_production.offshore_filter_fix: {mode: split_groups, resource_groups_dir: pg_data/resource_groups_oswfix}.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from s0_workflow import offshore_groups as og  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--groups-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--json", help="older form: the one offshore JSON to split by columns")
    ap.add_argument("--survey", action="store_true", help="list the offshore JSONs and the CSVs next to them, then stop")
    ap.add_argument("--map", action="append", default=[], metavar="JSON=CSV",
                    help="§134: a JSON (relative to --groups-dir) and its per-type membership file")
    ap.add_argument("--id-col", default="CPA_ID")
    ap.add_argument("--turbine-col")
    ap.add_argument("--depth-col")
    ap.add_argument("--floating-depth-m", type=float, default=60.0)
    ap.add_argument("--pref-col")
    ap.add_argument("--region-col", default="ipm_region")
    ap.add_argument("--mw-col", default="mw")
    ap.add_argument("--lcoe-col", default="lcoe")
    ap.add_argument("--lcoe-max", type=float, default=125.0)
    ap.add_argument("--no-links", action="store_true")
    a = ap.parse_args(argv)
    if a.survey:
        import pandas as pd
        sv = og.survey(a.groups_dir)
        print(sv.to_string(index=False))
        for d in sorted({(a.groups_dir / j).parent for j in sv.json}):
            for f in sorted(d.glob("offshore*")):
                if f.suffix == ".csv":
                    print(f"  {f.relative_to(a.groups_dir)}: {len(pd.read_csv(f, usecols=[0])):,} rows")
                elif f.suffix == ".parquet":
                    print(f"  {f.relative_to(a.groups_dir)}")
        return 0
    if not a.out_dir:
        ap.error("--out-dir is needed")
    if a.map:
        mapping = dict(m.split("=", 1) for m in a.map)
        og.split_by_type_files(a.groups_dir, a.out_dir, mapping, a.id_col, a.region_col, a.mw_col, a.lcoe_col,
                               a.lcoe_max, link_others=not a.no_links)
        return 0
    if not (a.json and a.pref_col):
        ap.error("give --map (several JSONs sharing a metadata file), or --json with --pref-col (older form)")
    og.split_folder(a.groups_dir, a.out_dir, a.json, a.turbine_col, a.depth_col, a.floating_depth_m, a.pref_col,
                    a.region_col, a.mw_col, a.lcoe_col, a.lcoe_max, link_others=not a.no_links)
    return 0


if __name__ == "__main__":
    sys.exit(main())
