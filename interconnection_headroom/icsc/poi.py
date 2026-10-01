"""Point-of-interconnection (POI) costs from LBNL: median POI $/kW (real, dollar_year) by regime and
technology, from the estimation sample's projects of `poi.statuses` (completed by default).

Cells with fewer than `poi.min_n` projects take the national median for the technology
(source = "national"). Written to outputs/poi_costs.csv by `run`; pg_to_switch uses it to price
the POI part of each new project's connection cost (icsc/switch_case.py).
"""
from __future__ import annotations

import pandas as pd

COLS = ["region", "tech", "n", "regional_median_per_kw", "national_median_per_kw", "national_n",
        "poi_cost_per_kw", "source"]


def poi_costs(sample: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    pc = cfg.get("poi", {})
    min_n = int(pc.get("min_n", 10))
    d = sample[sample["status_n"].isin(pc.get("statuses", ["completed"])) & sample["poi_cost_real"].notna()]
    techs = cfg["estimation"]["techs"]
    nat = d.groupby("tech_n")["poi_cost_real"].agg(["size", "median"])
    reg = d.groupby(["regime", "tech_n"])["poi_cost_real"].agg(["size", "median"])
    rows = []
    for r in sorted(set(sample["regime"])) + ["national"]:
        for t in techs:
            n, med = (reg.loc[(r, t)] if (r, t) in reg.index else (0, float("nan")))
            nn, nmed = (nat.loc[t] if t in nat.index else (0, float("nan")))
            own = r != "national" and n >= min_n
            rows.append({"region": r, "tech": t, "n": int(n if r != "national" else nn),
                         "regional_median_per_kw": med if r != "national" else nmed,
                         "national_median_per_kw": nmed, "national_n": int(nn),
                         "poi_cost_per_kw": med if own else nmed,
                         "source": "regional" if own else "national"})
    return pd.DataFrame(rows, columns=COLS)
