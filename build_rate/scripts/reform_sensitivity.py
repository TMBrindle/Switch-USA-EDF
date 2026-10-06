"""Sensitivity of reform_bp's 2035 national ceilings to each reform_benchmark choice (CHANGES §74; for Tom's review).

One parameter at a time from config.yaml's reform_benchmark; for each variant: the benchmark completion and duration per
group, the national implied-rate gain (I'/I) and the 2035 national ceiling (top band x R_central(2035) x I'/I), GW/yr.

usage (build_rate/): python scripts/reform_sensitivity.py   -> outputs/reform_sensitivity.csv
"""
import copy
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from brc import cli, data, rates  # noqa: E402

VARIANTS = [
    ("base", {}),
    ("unit = transreg", {"unit": "transreg"}),
    ("duration window 2022-2025", {"duration_on_years": [2022, 2025]}),
    ("duration window 2014-2025", {"duration_on_years": [2014, 2025]}),
    ("completion cohort to 2016", {"completion_cohort_queue_years": [2000, 2016]}),
    ("completion cohort to 2020", {"completion_cohort_queue_years": [2000, 2020]}),
    ("trim 0%", {"trim_share": 0.0}),
    ("trim 20%", {"trim_share": 0.20}),
    ("top share 10%", {"top_share": 0.10}),
    ("top share 50%", {"top_share": 0.50}),
    ("min resolved 1,000 MW", {"min_basis_mw": 1000}),
    ("min resolved 5,000 MW", {"min_basis_mw": 5000}),
    ("min completions 3", {"min_operational": 3}),
    ("min completions 10", {"min_operational": 10}),
    ("storage own data (no proxy)", {"proxy": {}}),
]


def run(year: int = 2035, out: Path | None = None) -> pd.DataFrame:
    cfg = cli.load_cfg(str(ROOT / "config.yaml"))
    c2t = data.county_transreg(ROOT / cfg["paths"]["county2zone"], ROOT / cfg["paths"]["hierarchy"])
    comp = data.queue_components(data.load_queued_up(ROOT / cfg["paths"]["queued_up"]), c2t)
    central = pd.read_csv(ROOT / "outputs/rates_central.csv")
    r35 = central[(central.region == "national") & (central.year == year)].set_index("group")["r_data_mw_per_yr"]
    rows = []
    for name, over in VARIANTS:
        rb = copy.deepcopy(cfg["reform_benchmark"])
        rb.update(over)
        try:
            bm, up = rates.reform_uplift(comp, cfg, rb)
        except ValueError as e:
            rows.append({"variant": name, "group": "", "note": str(e)})
            continue
        for g in rb["groups"]:
            b = bm[bm.group == g].iloc[0]
            gain = 1 + float(up[(up.group == g) & (up.region == "national")]["delta"].iat[0])
            top = rates.tiers(cfg, g, rates.level_tier_set(cfg, "reform_bp"))["upto"].max()
            n_units = int((bm[(bm.group == g) & (bm.unit != "national")]["own_completion"]).sum())
            rows.append({"variant": name, "group": g, "benchmark_completion": round(b.benchmark_completion, 4),
                         "benchmark_duration_years": b.benchmark_duration_years,
                         "units_with_own_completion": n_units, "implied_rate_gain": round(gain, 3),
                         f"ceiling_{year}_gw_per_yr": round(top * r35[g] * gain / 1e3, 1),
                         f"central_ceiling_{year}_gw_per_yr": round(top * r35[g] / 1e3, 1), "note": ""})
    df = pd.DataFrame(rows)
    df.to_csv(out or ROOT / "outputs/reform_sensitivity.csv", index=False)
    return df


if __name__ == "__main__":
    print(run().to_string(index=False))
