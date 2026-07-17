"""
Extract zonal LMPs from Distributed_Energy_Balance constraint duals.

The dual of Distributed_Energy_Balance[z, t] is the shadow price of serving
one additional MW at zone z at timepoint t. Dividing by tp_weight_in_year[t]
converts from $/MW (per-timepoint) to $/MWh.

Extreme-day and PRM-only timepoints (identified by ts_scale_to_period below a
configurable threshold) are excluded from the annual load-weighted average,
since their prices reflect scarcity events that are not representative of the
typical annual market price. They are still written to the per-timepoint file
so the full picture is available.

Output files (written to outputs_dir after each solve):
  zonal_lmp.csv         — one row per zone × timepoint
  zonal_lmp_annual.csv  — load-weighted annual average LMP by zone × period,
                          excluding extreme-day timepoints
"""

import os
import csv
from pyomo.environ import value


def define_arguments(argparser):
    argparser.add_argument(
        "--lmp-extreme-day-threshold",
        type=float,
        default=2.0,
        metavar="SCALE",
        help=(
            "Timeseries with ts_scale_to_period below this value are treated as "
            "extreme days and excluded from the annual average LMP. "
            "Default: 2.0 (catches scale=0 PRM-only and scale~0.43 extreme days; "
            "regular days have scale 100+)."
        ),
    )


def post_solve(m, outputs_dir):
    threshold = m.options.lmp_extreme_day_threshold

    if not hasattr(m, "Distributed_Energy_Balance"):
        print(
            "write_zonal_lmp: Distributed_Energy_Balance not found — "
            "is switch_model.transmission.local_td loaded? Skipping."
        )
        return

    if not hasattr(m, "dual"):
        print("write_zonal_lmp: no dual suffix found. Skipping.")
        return

    # ── Per-timepoint file ────────────────────────────────────────────────────
    tp_path = os.path.join(outputs_dir, "zonal_lmp.csv")
    annual_path = os.path.join(outputs_dir, "zonal_lmp_annual.csv")

    # Accumulators for annual average: {(zone, period): [sum_lmp_x_load_x_wt, sum_load_x_wt]}
    annual_acc = {}

    print(f"write_zonal_lmp: writing {tp_path} ... ", end="", flush=True)

    with open(tp_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "load_zone", "timepoint", "period",
            "lmp_real_per_mwh", "load_mw", "tp_weight_hr", "is_extreme_day",
        ])

        for t in sorted(m.TIMEPOINTS, key=str):
            tp_weight = value(m.tp_weight_in_year[t])
            period = m.tp_period[t]
            ts = m.tp_ts[t]
            scale = value(m.ts_scale_to_period[ts])
            is_extreme = scale < threshold

            for z in sorted(m.LOAD_ZONES, key=str):
                constr = m.Distributed_Energy_Balance[z, t]
                dual_per_mw = m.dual.get(constr, 0.0)
                lmp = dual_per_mw / tp_weight if tp_weight else 0.0
                load_mw = value(m.zone_demand_mw[z, t])

                writer.writerow([
                    z, t, period,
                    round(lmp, 4),
                    round(load_mw, 4),
                    round(tp_weight, 4),
                    int(is_extreme),
                ])

                # accumulate for annual average (excluding extreme days)
                if not is_extreme:
                    key = (z, period)
                    if key not in annual_acc:
                        annual_acc[key] = [0.0, 0.0]
                    weight = load_mw * tp_weight
                    annual_acc[key][0] += lmp * weight
                    annual_acc[key][1] += weight

    print("done")

    # ── Annual average file ───────────────────────────────────────────────────
    print(f"write_zonal_lmp: writing {annual_path} ... ", end="", flush=True)

    with open(annual_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "load_zone", "period",
            "lmp_annual_avg_real_per_mwh",
            "load_weighted_twh",
        ])
        for (z, p) in sorted(annual_acc, key=lambda zp: (str(zp[0]), str(zp[1]))):
            lmp_wt_sum, load_wt_sum = annual_acc[(z, p)]
            avg_lmp = lmp_wt_sum / load_wt_sum if load_wt_sum else 0.0
            load_twh = load_wt_sum / 1e6  # MW × hr → TWh
            writer.writerow([z, p, round(avg_lmp, 4), round(load_twh, 2)])

    print("done")
