"""Technology groups covered by the build-rate constraint.

wind_onshore, solar, storage, gas (combined cycle + combustion turbine), nuclear (§79: a path group, see
config.yaml path_groups; large and SMR). Offshore wind, distributed generation and all other technologies are not
covered (None).
"""
from __future__ import annotations

import re

import pandas as pd

GROUPS = ("wind_onshore", "solar", "storage", "gas", "nuclear")


def eia_group(technology: str) -> str | None:
    """EIA-860M 'Technology' -> group."""
    t = str(technology).lower()
    if "offshore wind" in t:
        return None
    if "onshore wind" in t:
        return "wind_onshore"
    if "solar photovoltaic" in t:
        return "solar"
    if "batteries" in t:
        return "storage"
    if t in ("natural gas fired combined cycle", "natural gas fired combustion turbine"):
        return "gas"
    return None


def queue_group(resource_type: str) -> str | None:
    """LBNL Queued Up type_1/2/3 -> group."""
    t = str(resource_type).strip().lower()
    if t == "wind":
        return "wind_onshore"
    if t == "solar":
        return "solar"
    if t in ("battery", "other storage"):
        return "storage"
    if t == "gas":
        return "gas"
    return None   # Offshore Wind, Hydro, Nuclear, ...


def switch_group(gen_tech: str, energy_source: str = "", is_distributed=0) -> str | None:
    """Switch gen_info (PowerGenome gen_tech / gen_energy_source) -> group."""
    if str(is_distributed).strip() in ("1", "1.0", "True", "true"):
        return None
    t = str(gen_tech).lower()
    es = str(energy_source).lower()
    if "offshore" in t or "distributed" in t or "residential" in t or "commercial" in t:
        return None
    if "nuclear" in t or es == "uranium":
        return "nuclear"
    if "wind" in t or es == "wind":
        return "wind_onshore"
    if "csp" in t or "thermal" in t:
        return None
    if re.search(r"pv|solar", t) or es in ("sun", "solar"):
        return "solar"
    if "batter" in t or (es in ("electricity", "storage") and "pump" not in t and "hydro" not in t):
        return "storage"
    if (es.startswith("naturalgas") or "naturalgas" in t or t.startswith("ng_")) and "ccs" not in t:
        if re.search(r"cc|ct|combined|combustion|gt", t):
            return "gas"
    return None


def assign(df: pd.DataFrame, func, *cols) -> pd.Series:
    return pd.Series([func(*vals) for vals in zip(*(df[c] for c in cols))], index=df.index, dtype=object)
