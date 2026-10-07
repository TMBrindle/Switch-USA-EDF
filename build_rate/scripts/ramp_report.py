"""§84: the ramp from the deliverability brief against the data rate and the deliverability ceiling, per model period,
for S0 (central) and S2-type (high) settings. For Tom's review.

Per group and period (window): data rate R (window mean of the final rate table: queue/pace layer, scaled where the
deliverability ceiling binds), ceiling 2R (= the deliverability ceiling where it binds), ramp floor F and factor G
(scripts' source: rates.ramp_window), and the ramp bound on R, max(F, G x base), for three bases (the previous stage's
build rate, MW/yr): the previous stage at its floor F (a trough), at its pace R, and at its ceiling 2R. The ramp binds
where the bound is below the data rate. The first stage (2028) has no ramp (no history).

usage (build_rate/): python scripts/ramp_report.py   -> outputs/ramp_report.csv
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from brc import cli, rates  # noqa: E402

PERIODS = {2028: (2026, 2028), 2030: (2029, 2030), 2035: (2031, 2035), 2040: (2036, 2040), 2045: (2041, 2045)}
GROUPS = ["wind_onshore", "solar", "storage"]


def run(levels=("central", "high"), out=None):
    cfg = cli.load_cfg(str(ROOT / "config.yaml"))
    rows = []
    for lv in levels:
        t = pd.read_csv(ROOT / f"outputs/rates_{lv}.csv")
        t = t[t.region == "national"]
        for g in GROUPS:
            d = t[t.group == g].set_index("year")
            prev = None
            for p, (s, e) in PERIODS.items():
                r = d.loc[s:e, "r_data_mw_per_yr"].mean()
                dl = d.loc[s:e, "deliverability_mw_per_yr"].mean()
                w = rates.ramp_window(cfg, lv, g, s, e)
                row = {"level": lv, "group": g, "period": p, "data_rate_gw": r / 1e3, "ceiling_2r_gw": 2 * r / 1e3,
                       "deliverability_gw": dl / 1e3, "ramp_floor_gw": w["floor_mw"] / 1e3,
                       "ramp_factor": w["factor"]}
                if prev is not None:
                    pr = prev
                    for k, base in (("trough", pf), ("pace", pr), ("ceiling", 2 * pr)):
                        b = max(w["floor_mw"], w["factor"] * base)
                        row[f"bound_after_{k}_gw"] = b / 1e3
                        row[f"binds_after_{k}"] = b < r - 1e-6
                rows.append(row)
                prev, pf = r, w["floor_mw"]
    df = pd.DataFrame(rows)
    df.round(3).to_csv(out or ROOT / "outputs/ramp_report.csv", index=False)
    return df


if __name__ == "__main__":
    print(run().round(2).to_string(index=False))
