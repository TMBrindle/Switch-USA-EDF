"""Write a copy of RESOURCE_GROUPS with the offshore wind group split by turbine type and pref site (CHANGES §120;
s0_workflow/offshore_groups.py).

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
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--turbine-col")
    ap.add_argument("--depth-col")
    ap.add_argument("--floating-depth-m", type=float, default=60.0)
    ap.add_argument("--pref-col", required=True)
    ap.add_argument("--region-col", default="ipm_region")
    ap.add_argument("--mw-col", default="mw")
    ap.add_argument("--lcoe-col", default="lcoe")
    ap.add_argument("--lcoe-max", type=float, default=125.0)
    ap.add_argument("--no-links", action="store_true")
    a = ap.parse_args(argv)
    og.split_folder(a.groups_dir, a.out_dir, a.json, a.turbine_col, a.depth_col, a.floating_depth_m, a.pref_col,
                    a.region_col, a.mw_col, a.lcoe_col, a.lcoe_max, link_others=not a.no_links)
    return 0


if __name__ == "__main__":
    sys.exit(main())
