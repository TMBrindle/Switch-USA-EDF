"""Synthetic LBNL-style workbooks with a known saturation effect.

Used only to test the pipeline end to end (Excel parsing -> mapping -> regression -> tranches)
before the real LBNL files are in place. Nothing produced from these files is a result.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TRUE = {"const": 3.0, "sat": 1.2, "sat_gt_0.5": 0.8, "log_mw": -0.15, "trend": 0.06,
        "wind": -0.3, "storage": -0.2, "gas": -0.9, "ERIS": -0.7, "sigma": 0.6}
REGIME_EFFECT = {"MISO": 0.0, "PJM": 0.25, "SPP": -0.15, "NYISO": 0.5, "ISONE": 0.3, "CAISO": 0.1}


def make(panel: pd.DataFrame, c2z: pd.DataFrame, hierarchy: pd.DataFrame, outdir: Path,
         n_per_region: int = 600, seed: int = 0) -> Path:
    rng = np.random.default_rng(seed)
    outdir.mkdir(parents=True, exist_ok=True)
    zreg = hierarchy.set_index("ba")["transreg"]
    counties = c2z.merge(zreg.rename("transreg"), left_on="ba", right_index=True)
    sat = panel.set_index(["ba", "year"])["saturation"]
    for region, eff in REGIME_EFFECT.items():
        cty = counties[counties["transreg"] == region]
        if cty.empty:
            continue
        pick = cty.sample(n_per_region, replace=True, random_state=int(rng.integers(1e9)))
        qy = rng.integers(2012, 2025, n_per_region)
        tech = rng.choice(["Solar", "Wind", "Battery Storage", "Solar+Storage", "Gas"], n_per_region,
                          p=[0.45, 0.2, 0.15, 0.12, 0.08])
        mw = np.round(np.exp(rng.normal(np.log(120), 0.8, n_per_region)), 1)
        svc = rng.choice(["NRIS", "ERIS"], n_per_region, p=[0.85, 0.15])
        s = np.array([sat.get((b, y), 0.0) for b, y in zip(pick["ba"], qy)])
        y = (TRUE["const"] + TRUE["sat"] * s + TRUE["sat_gt_0.5"] * np.clip(s - 0.5, 0, None)
             + TRUE["log_mw"] * np.log(mw) + TRUE["trend"] * (qy - 2015) + eff
             + np.select([tech == "Wind", tech == "Battery Storage", tech == "Gas"],
                         [TRUE["wind"], TRUE["storage"], TRUE["gas"]], 0.0)
             + np.where(svc == "ERIS", TRUE["ERIS"], 0.0)
             + rng.normal(0, TRUE["sigma"], n_per_region))
        nu = np.expm1(y)
        status = rng.choice(["Active", "Completed", "Withdrawn"], n_per_region, p=[0.4, 0.2, 0.4])
        df = pd.DataFrame({
            "Queue ID": [f"{region}-{i}" for i in range(n_per_region)],
            "ISO/RTO": region, "State": pick["state"].values,
            "County": pick["county_name"].str.title().values,
            "Fuel": tech, "Nameplate Capacity (MW)": mw, "Queue Year": qy, "Request Status": status,
            "Service Type": svc, "Cost Year": qy + 1,
            "POI Cost ($/kW)": np.round(rng.gamma(2, 20, n_per_region), 1),
            "Network Upgrade Cost ($/kW)": np.round(nu, 1),
        })
        with pd.ExcelWriter(outdir / f"SYNTHETIC_{region}.xlsx") as xw:
            pd.DataFrame([["SYNTHETIC test data - not LBNL"], [""]]).to_excel(xw, sheet_name="Project Data", header=False, index=False)
            df.to_excel(xw, sheet_name="Project Data", startrow=3, index=False)
    return outdir
