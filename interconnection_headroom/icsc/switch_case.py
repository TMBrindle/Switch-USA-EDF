"""Write interconnection-headroom inputs into a Switch-USA-EDF case folder (called from pg_to_switch.py).

Only pandas and a YAML reader are needed here, so pg_to_switch.py can import this without the
estimation dependencies (statsmodels etc.).

Settings (pg/settings/interconnection_headroom.yml):

    interconnection_headroom:
      enabled: false
      scenario: reference                 # zones/tranches/uprates_<scenario>.csv from the pipeline
      tranches_dir: interconnection_headroom/outputs
      exclude_network_reinforcement: true # drop PowerGenome tx_capex from gen_connect_cost_per_mw
      split_connect_costs: true           # gen_connect_cost_per_mw = spur + POI (LBNL), see below

With split_connect_costs, each project's connection cost is rebuilt as
    spur  = the spur cost PowerGenome used (spur_capex, or spur_miles x capex_mw_mile) + offshore_spur_capex
    poi   = LBNL median POI $/kW for the zone's regime and the project's technology
            (<tranches_dir>/poi_costs.csv from `python -m icsc.cli run`); offshore wind and
            technologies with no LBNL estimate keep PowerGenome's own non-spur, non-tx component
    gen_connect_cost_per_mw = spur + poi (+ co2_pipeline_capex_mw, unchanged, when present)
and written to ic_connect_components.csv for the module's spend report. Network reinforcement
(tx_capex) is dropped either way: the zonal curve prices it.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
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


POI_TECHS = ("solar", "wind", "storage", "gas", "hybrid")


def split_connect_costs(gen_info: pd.DataFrame, source: pd.DataFrame, poi: pd.DataFrame,
                        zone_regime: dict) -> pd.DataFrame:
    """Rebuild gen_info.gen_connect_cost_per_mw as spur + POI (+ CO2 pipeline). Returns the
    per-project diagnostic, including PowerGenome's residual component that POI replaces.

    `source` is the PowerGenome frame gen_info was built from (row-aligned); `poi` is
    poi_costs.csv; `zone_regime` maps ReEDS BA -> regime label (zones_<scenario>.csv)."""
    src = source.reset_index(drop=True)
    col = lambda c: src[c].fillna(0).astype(float).values if c in src else 0.0
    before = gen_info["gen_connect_cost_per_mw"].astype(float).values
    co2 = col("co2_pipeline_capex_mw")
    icx = col("interconnect_capex_mw")
    # gen_info_table set gen_connect = spur_used + interconnect_capex_mw (+ co2): recover spur_used
    spur = before - icx - co2 + col("offshore_spur_capex")
    # what interconnect_capex_mw holds beyond spur, offshore spur and network reinforcement
    residual = icx - col("spur_capex") - col("offshore_spur_capex") - col("tx_capex")
    table = poi.set_index(["region", "tech"])["poi_cost_per_kw"]
    ba = src["region"] if "region" in src else gen_info["gen_load_zone"].reset_index(drop=True)
    ba = ba.where(ba.isin(zone_regime.keys()), gen_info["gen_load_zone"].reset_index(drop=True))
    rows = []
    offshore = np.asarray(col("offshore_spur_capex")) > 0 if "offshore_spur_capex" in src else \
        np.zeros(len(gen_info), bool)
    for i, (es, tech) in enumerate(zip(gen_info["gen_energy_source"], gen_info["gen_tech"])):
        cat = category(es, tech)
        reg = zone_regime.get(ba.iat[i])
        if cat in POI_TECHS and not offshore[i]:
            key = (reg, cat) if (reg, cat) in table.index else ("national", cat)
            rows.append((float(table.get(key, np.nan)) * 1000, key[0] if key[0] == "national" else "lbnl_regional"))
        else:
            rows.append((max(float(residual[i] if np.ndim(residual) else residual), 0.0), "powergenome_residual"))
    poi_mw = np.array([r[0] for r in rows])
    if np.isnan(poi_mw).any():
        raise ValueError("poi_costs.csv has no national row for some technologies: "
                         f"{sorted(set(gen_info['gen_tech'][np.isnan(poi_mw)]))}")
    gen_info["gen_connect_cost_per_mw"] = spur + poi_mw + co2
    diag = pd.DataFrame({"GENERATION_PROJECT": gen_info["GENERATION_PROJECT"].values,
                         "gen_tech": gen_info["gen_tech"].values, "gen_load_zone": gen_info["gen_load_zone"].values,
                         "ic_region": [zone_regime.get(b) for b in ba], "category":
                         [category(e, t) for e, t in zip(gen_info["gen_energy_source"], gen_info["gen_tech"])],
                         "gen_connect_cost_per_mw_before": before,
                         "pg_residual_replaced_per_mw": residual, "tx_capex_removed_per_mw": col("tx_capex"),
                         "spur_cost_per_mw": spur, "poi_cost_per_mw": poi_mw, "poi_source": [r[1] for r in rows],
                         "co2_pipeline_cost_per_mw": co2,
                         "gen_connect_cost_per_mw_after": gen_info["gen_connect_cost_per_mw"].values})
    return diag


def connect_components(diag: pd.DataFrame) -> pd.DataFrame:
    """ic_connect_components.csv: spur and POI $/MW per project (their sum, plus any CO2 pipeline
    cost, equals gen_connect_cost_per_mw)."""
    out = diag[["GENERATION_PROJECT", "spur_cost_per_mw", "poi_cost_per_mw"]].copy()
    if (diag["co2_pipeline_cost_per_mw"] != 0).any():
        out["co2_pipeline_cost_per_mw"] = diag["co2_pipeline_cost_per_mw"]
    return out.round(2)


def prepare_connect_costs(gen_info: pd.DataFrame, source: pd.DataFrame, settings: dict) -> pd.DataFrame | None:
    """Called from pg_to_switch.gen_info_file when interconnection_headroom is enabled."""
    ic = ic_settings(settings)
    if ic is None:
        return None
    if not ic.get("split_connect_costs", True):
        return strip_network_reinforcement(gen_info, source) if ic.get("exclude_network_reinforcement", True) else None
    tdir = REPO_ROOT / ic.get("tranches_dir", "interconnection_headroom/outputs")
    path = tdir / "poi_costs.csv"
    if not path.exists():
        raise FileNotFoundError(f"split_connect_costs is on but {path} is missing. Run `python -m icsc.cli run` "
                                "in interconnection_headroom/ first, or set split_connect_costs: false.")
    zones = read_scenario(tdir, ic.get("scenario", "reference"))[0]
    return split_connect_costs(gen_info, source, pd.read_csv(path), dict(zip(zones["ba"], zones["regime"])))


def switch_frames(zones: pd.DataFrame, tranches: pd.DataFrame, uprates: pd.DataFrame,
                  zone_map: dict | None = None, load_zones: set | None = None
                  ) -> dict[str, pd.DataFrame]:
    """Convert pipeline zones/tranches/uprates tables into Switch input frames.

    Each ReEDS BA is one IC zone attached to a Switch load zone (itself, or its aggregate under
    `zone_map`). Zones whose load zone isn't in `load_zones` are dropped.
    """
    zone_map = zone_map or {}
    z = zones.copy()
    z["load_zone"] = z["ba"].map(lambda b: zone_map.get(b, b))
    if load_zones is not None:
        z = z[z["load_zone"].isin(load_zones)]
    keep = set(z["ba"])
    t = tranches[tranches["ba"].isin(keep) & (tranches["width"] > 0)]
    u = uprates[uprates["ba"].isin(keep) & (uprates["max_mw"] > 0)] if len(uprates) else uprates
    return {
        "ic_zones.csv": pd.DataFrame({
            "IC_ZONE": z["ba"],
            "ic_zone_load_zone": z["load_zone"],
            "ic_base_capacity_mw": z["base_capacity_mw"].round(3),
            "ic_start_saturation": z["start_saturation"].round(6),
            "ic_release_cost_per_mw": (z["release_cost_per_kw"] * 1000).round(0),
        }),
        "ic_tranches.csv": pd.DataFrame({
            "IC_TRANCHE": t["ba"] + "_" + t["tranche"],
            "ic_tranche_zone": t["ba"],
            "ic_tranche_width": t["width"].round(6),
            "ic_tranche_cost_per_mw": (t["cost_per_kw"] * 1000).round(0),
            "ic_tranche_available_year": t["available_year"].astype(int),
        }),
        "ic_uprates.csv": pd.DataFrame({
            "IC_UPRATE": u["ba"] + "_" + u["uprate"],
            "ic_uprate_zone": u["ba"],
            "ic_uprate_type": u["type"],
            "ic_uprate_max_mw": u["max_mw"].round(3),
            "ic_uprate_cost_per_mw": (u["cost_per_kw"] * 1000).round(0),
            "ic_uprate_available_year": u["available_year"].astype(int),
        }) if len(u) else pd.DataFrame(columns=["IC_UPRATE", "ic_uprate_zone", "ic_uprate_type",
                                                 "ic_uprate_max_mw", "ic_uprate_cost_per_mw",
                                                 "ic_uprate_available_year"]),
    }


def params_frame(cfg: dict, weights: dict, reuse_share: float) -> pd.DataFrame:
    return pd.DataFrame([{
        "ic_retirement_reuse_share": reuse_share,
        "ic_storage_weight": weights.get("storage", 0.5),
        "ic_default_weight": weights.get("other", 1.0),
        "ic_asset_life_years": cfg.get("switch", {}).get("asset_life_years", 40),
        "ic_reactive_cost_per_mw_network": cfg.get("reactive_cost_per_kw_network", 0) * 1000,
    }])


def read_scenario(tables_dir: Path, scenario: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    paths = [Path(tables_dir) / f"{kind}_{scenario}.csv" for kind in ("zones", "tranches", "uprates")]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"interconnection_headroom is enabled but {', '.join(map(str, missing))} not found. Run "
            "`python -m icsc.cli run` in interconnection_headroom/ first, or set enabled: false.")
    return tuple(pd.read_csv(p) for p in paths)


def write_case_inputs(gen_info: pd.DataFrame, settings: dict, out_folder: Path,
                      diag: pd.DataFrame | None = None) -> None:
    """Write the ic_*.csv inputs for one case/year."""
    ic = ic_settings(settings)
    if ic is None:
        return
    cfg = pipeline_config()
    sat = cfg["saturation"]
    weights = {**sat["tech_weights"], **(ic.get("tech_weights") or {})}
    scenario = ic.get("scenario", "reference")
    zones, tranches, uprates = read_scenario(
        REPO_ROOT / ic.get("tranches_dir", "interconnection_headroom/outputs"), scenario)
    frames = switch_frames(zones, tranches, uprates, settings.get("_zone_map"),
                           set(gen_info["gen_load_zone"]))
    out_folder = Path(out_folder)
    for name, df in frames.items():
        df.to_csv(out_folder / name, index=False)
    weights_by_tech(gen_info, weights)[["ic_key", "ic_weight"]].to_csv(out_folder / "ic_weights.csv",
                                                                        index=False)
    params_frame(cfg, weights, ic.get("retirement_reuse_share", sat["retirement_reuse_share"])).to_csv(
        out_folder / "ic_params.csv", index=False)
    if diag is not None:
        diag.to_csv(out_folder / "ic_connect_cost_check.csv", index=False)
        if "spur_cost_per_mw" in diag:
            connect_components(diag).to_csv(out_folder / "ic_connect_components.csv", index=False)
    logger.info("interconnection_headroom: wrote %d IC zones, %d steps and %d uprate options (%s) to %s",
                len(frames["ic_zones.csv"]), len(frames["ic_tranches.csv"]),
                len(frames["ic_uprates.csv"]), scenario, out_folder)
