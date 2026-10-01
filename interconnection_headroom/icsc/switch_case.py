"""Write interconnection-headroom inputs into a Switch-USA-EDF case folder (called from pg_to_switch.py).

Only pandas and a YAML reader are needed here, so pg_to_switch.py can import this without the
estimation dependencies (statsmodels etc.).

Settings (pg/settings/interconnection_headroom.yml):

    interconnection_headroom:
      enabled: false
      scenario: reference                 # tranches_<scenario>.csv from the pipeline
      tranches_dir: interconnection_headroom/outputs
      exclude_network_reinforcement: true # drop PowerGenome tx_capex from gen_connect_cost_per_mw
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_CONFIG = REPO_ROOT / "interconnection_headroom" / "config.yaml"

# gen_energy_source values produced by conversion_functions.infer_gen_energy_source
_SOURCE_CATEGORY = {
    "sun": "solar",
    "wind": "wind",
    "storage": "storage",
    "demand_response": "none",
    "imports": "none",
}


def ic_settings(settings: dict) -> dict | None:
    s = settings.get("interconnection_headroom") or {}
    return s if s.get("enabled") else None


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load(open(path))
    except ImportError:  # PowerGenome environments ship ruamel
        from ruamel.yaml import YAML
        return YAML(typ="safe").load(open(path))


def pipeline_config() -> dict:
    return _load_yaml(PIPELINE_CONFIG)


def category(energy_source: str, technology: str = "") -> str:
    es = str(energy_source).lower()
    if es in _SOURCE_CATEGORY:
        return _SOURCE_CATEGORY[es]
    if es.startswith("naturalgas"):
        return "gas"
    t = str(technology).lower()
    if "solar" in t or "photovoltaic" in t or t.endswith("pv"):
        return "solar"
    if "wind" in t:
        return "wind"
    if "batter" in t or "storage" in t:
        return "storage"
    return "other"


def weights_by_tech(gen_info: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """One row per gen_tech with the weight its new builds use against headroom."""
    w = {**weights, "none": 0.0}
    df = gen_info[["gen_tech", "gen_energy_source"]].drop_duplicates().copy()
    df["category"] = [category(es, t) for es, t in zip(df["gen_energy_source"], df["gen_tech"])]
    df["ic_weight"] = df["category"].map(lambda c: w.get(c, w.get("other", 1.0)))
    out = df.groupby("gen_tech", as_index=False).agg(ic_weight=("ic_weight", "max"),
                                                      category=("category", "first"))
    return out.rename(columns={"gen_tech": "ic_key"})


def strip_network_reinforcement(gen_info: pd.DataFrame, source: pd.DataFrame) -> pd.DataFrame:
    """Remove PowerGenome's reinforcement cost (tx_capex) from gen_info.gen_connect_cost_per_mw.

    `source` is the PowerGenome generator frame gen_info was built from, row-aligned with it
    (gen_info_table resets the index, so pass `gens.reset_index(drop=True)`). Modifies gen_info in
    place and returns a diagnostic frame so the first real run can confirm what was removed.
    """
    source = source.reset_index(drop=True)
    cols = [c for c in ["spur_capex", "offshore_spur_capex", "tx_capex", "interconnect_capex_mw",
                        "interconnect_annuity"] if c in source]
    diag = pd.concat([gen_info[["GENERATION_PROJECT", "gen_tech", "gen_load_zone"]].reset_index(drop=True),
                      source[cols]], axis=1)
    diag["gen_connect_cost_per_mw_before"] = gen_info["gen_connect_cost_per_mw"].values
    if "tx_capex" in source:
        tx = source["tx_capex"].fillna(0).values
        gen_info["gen_connect_cost_per_mw"] = (gen_info["gen_connect_cost_per_mw"] - tx).clip(lower=0)
        logger.info("interconnection_headroom: removed tx_capex from gen_connect_cost_per_mw for %d "
                    "resources", int((tx > 0).sum()))
    else:
        logger.warning("interconnection_headroom: no tx_capex column; gen_connect_cost_per_mw unchanged. "
                       "Check that network reinforcement is not still counted.")
    diag["gen_connect_cost_per_mw_after"] = gen_info["gen_connect_cost_per_mw"].values
    return diag


def write_case_inputs(gen_info: pd.DataFrame, settings: dict, out_folder: Path,
                      diag: pd.DataFrame | None = None) -> None:
    """Write ic_tranches.csv, ic_weights.csv and ic_params.csv for one case/year."""
    ic = ic_settings(settings)
    if ic is None:
        return
    cfg = pipeline_config()
    sat = cfg["saturation"]
    weights = {**sat["tech_weights"], **(ic.get("tech_weights") or {})}
    scenario = ic.get("scenario", "reference")
    tranches_path = REPO_ROOT / ic.get("tranches_dir", "interconnection_headroom/outputs") / \
        f"tranches_{scenario}.csv"
    if not tranches_path.exists():
        raise FileNotFoundError(
            f"interconnection_headroom is enabled but {tranches_path} does not exist. Run "
            "`python -m icsc.cli run` in interconnection_headroom/ first, or set enabled: false.")
    t = pd.read_csv(tranches_path)

    zone_map = settings.get("_zone_map") or {}
    t["load_zone"] = t["ba"].map(lambda b: zone_map.get(b, b))
    zones = set(gen_info["gen_load_zone"])
    t = t[t["load_zone"].isin(zones) & (t["max_mw"] > 0)]
    sw = pd.DataFrame({
        "IC_TRANCHE": t["ba"] + "_" + t["tranche"],
        "ic_tranche_zone": t["load_zone"],
        "ic_tranche_max_mw": t["max_mw"].round(3),
        "ic_tranche_cost_per_mw": (t["cost_per_kw"] * 1000).round(0),
        # empirical network-upgrade tranches are available in every period (including historical
        # ones, which would otherwise be forced to build nothing); policy tranches keep their year
        "ic_tranche_available_year": t["available_year"].astype(int).where(
            t["tranche_type"] != "network_upgrade", 0),
    })
    out_folder = Path(out_folder)
    sw.to_csv(out_folder / "ic_tranches.csv", index=False)
    weights_by_tech(gen_info, weights)[["ic_key", "ic_weight"]].to_csv(out_folder / "ic_weights.csv",
                                                                        index=False)
    pd.DataFrame([{
        "ic_retirement_reuse_share": ic.get("retirement_reuse_share", sat["retirement_reuse_share"]),
        "ic_storage_weight": weights.get("storage", 0.5),
        "ic_default_weight": weights.get("other", 1.0),
        "ic_asset_life_years": cfg.get("switch", {}).get("asset_life_years", 40),
    }]).to_csv(out_folder / "ic_params.csv", index=False)
    if diag is not None:
        diag.to_csv(out_folder / "ic_connect_cost_check.csv", index=False)
    logger.info("interconnection_headroom: wrote %d tranches (%s) for %d zones to %s",
                len(sw), scenario, sw["ic_tranche_zone"].nunique(), out_folder)
