"""
Copies key input files into the output folder after each solve, so that
output folders are self-contained for downstream data processing.

Input aliases are resolved so that the file actually used by the run is
copied, always saved under the canonical filename. For example, if the run
used gen_info.csv=gen_info_adjusted.csv, gen_info_adjusted.csv is copied
but saved as gen_info.csv in the output folder.
"""

import os
import shutil

# Canonical input filenames to copy into the output directory after each solve
INPUT_FILES_TO_COPY = [
    "gen_build_predetermined.csv",  # existing/planned capacity classification
    "gen_info.csv",                 # gen_max_age and gen_can_retire_early for retirement classification
    "fuel_cost.csv",                # assumed fuel prices (not reproduced in outputs)
    "gen_build_costs.csv",          # full CAPEX schedule for all technologies
    "gen_om_by_period.csv",         # period-specific operating cost adjustments
    "loads.csv",                    # zonal demand by timepoint (for annual and peak demand)
]


def post_solve(m, outdir):
    inputs_dir = m.options.inputs_dir

    # Build alias map: canonical name -> actual filename used
    alias_map = {}
    for alias in getattr(m.options, "input_aliases", []) or []:
        if "=" in alias:
            canonical, actual = alias.split("=", 1)
            alias_map[canonical.strip()] = actual.strip()

    for canonical in INPUT_FILES_TO_COPY:
        actual = alias_map.get(canonical, canonical)
        src = os.path.join(inputs_dir, actual)
        dst = os.path.join(outdir, canonical)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
        else:
            print(f"copy_inputs_to_outputs: skipping {canonical} (not found in {inputs_dir})")
