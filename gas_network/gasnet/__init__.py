"""gas_network Stage 1: basin attribution of delivered gas by Switch load zone (no model change).

See "Guides and documentation/gas_network.md" and CHANGES §93.
"""
from __future__ import annotations

from pathlib import Path

import yaml

PKG = Path(__file__).resolve().parents[1]   # gas_network/
REPO = PKG.parent


def load_config(path: str | Path | None = None) -> dict:
    with open(path or PKG / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    for k, v in cfg["paths"].items():
        cfg["paths"][k] = (PKG / v).resolve()
    return cfg
