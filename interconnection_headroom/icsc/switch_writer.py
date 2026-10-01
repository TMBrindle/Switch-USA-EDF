"""Write scenario tranche tables as standalone Switch inputs (outputs/switch/<scenario>/).

For Switch-USA-EDF cases, pg_to_switch.py uses icsc/switch_case.py instead, which keys weights by
the case's own gen_tech values and maps zones through any region aggregation.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def to_switch(tranches: pd.DataFrame, outdir: Path, cfg: dict, zone_map: dict | None = None) -> Path:
    """Write ic_tranches.csv, ic_params.csv and ic_weights.csv for one scenario.

    zone_map: optional {ReEDS ba -> Switch load zone}. When several BAs map to one Switch zone
    their tranches are simply pooled, which is the correct aggregate supply curve.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    t = tranches.copy()
    t["load_zone"] = t["ba"].map(zone_map) if zone_map else t["ba"]
    t = t[t["load_zone"].notna() & (t["max_mw"] > 0)]
    sw = pd.DataFrame({
        "IC_TRANCHE": t["ba"] + "_" + t["tranche"],
        "ic_tranche_zone": t["load_zone"],
        "ic_tranche_max_mw": t["max_mw"].round(3),
        "ic_tranche_cost_per_mw": (t["cost_per_kw"] * 1000).round(0),
        "ic_tranche_available_year": t["available_year"].astype(int),
    })
    sw.to_csv(outdir / "ic_tranches.csv", index=False)
    w = cfg["saturation"]["tech_weights"]
    sw_cfg = cfg.get("switch", {})
    pd.DataFrame([{
        "ic_retirement_reuse_share": cfg["saturation"]["retirement_reuse_share"],
        "ic_storage_weight": w.get("storage", 0.5),
        "ic_default_weight": w.get(sw_cfg.get("default_weight_category", "other"), 1.0),
        "ic_asset_life_years": sw_cfg.get("asset_life_years", 40),
    }]).to_csv(outdir / "ic_params.csv", index=False)
    keys = sw_cfg.get("weight_keys", {})
    pd.DataFrame({"ic_key": list(keys), "ic_weight": [w.get(c, 1.0) for c in keys.values()]}).to_csv(
        outdir / "ic_weights.csv", index=False)
    return outdir / "ic_tranches.csv"
