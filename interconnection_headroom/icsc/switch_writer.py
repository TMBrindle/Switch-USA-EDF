"""Write a scenario's tables as standalone Switch inputs (outputs/switch/<scenario>/).

For Switch-USA-EDF cases, pg_to_switch.py calls icsc/switch_case.write_case_inputs instead, which
keys weights by the case's own gen_tech values and drops zones outside the case.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .switch_case import params_frame, switch_frames


def to_switch(zones: pd.DataFrame, tranches: pd.DataFrame, uprates: pd.DataFrame, outdir: Path,
              cfg: dict, zone_map: dict | None = None) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    for name, df in switch_frames(zones, tranches, uprates, zone_map).items():
        df.to_csv(outdir / name, index=False)
    w = cfg["saturation"]["tech_weights"]
    params_frame(cfg, w, cfg["saturation"]["retirement_reuse_share"]).to_csv(outdir / "ic_params.csv",
                                                                          index=False)
    keys = cfg.get("switch", {}).get("weight_keys", {})
    pd.DataFrame({"ic_key": list(keys), "ic_weight": [w.get(c, 1.0) for c in keys.values()]}).to_csv(
        outdir / "ic_weights.csv", index=False)
    return outdir
