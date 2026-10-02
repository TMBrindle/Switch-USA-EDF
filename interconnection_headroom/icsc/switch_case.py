"""Write interconnection-headroom inputs into a Switch-USA-EDF case folder (called from pg_to_switch.py).

Only pandas and a YAML reader are needed here, so pg_to_switch.py can import this without the
estimation dependencies (statsmodels etc.).

Settings (pg/settings/interconnection_headroom.yml):

    interconnection_headroom:
      enabled: false
      scenario: reference                 # zones/tranches/uprates_<scenario>.csv from the pipeline
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


def is_distributed(gen_tech: str, gen_is_distributed=0) -> bool:
    """Distributed generation sits behind the meter, outside the transmission interconnection queue."""
    flag = pd.to_numeric(pd.Series([gen_is_distributed]), errors="coerce").fillna(0).iloc[0]
    t = str(gen_tech).lower()
    return bool(flag == 1) or "distributed" in t or t in ("dg", "distpv", "dpv")


def weights_by_tech(gen_info: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """One row per gen_tech with the weight its new builds use against headroom.

    Distributed generation (gen_is_distributed == 1, or a gen_tech named as distributed) gets 0.
    """
    w = {**weights, "none": 0.0}
    cols = ["gen_tech", "gen_energy_source"] + (
        ["gen_is_distributed"] if "gen_is_distributed" in gen_info else [])
    df = gen_info[cols].drop_duplicates().copy()
    df["category"] = [category(es, t) for es, t in zip(df["gen_energy_source"], df["gen_tech"])]
    dist = [is_distributed(t, d) for t, d in
            zip(df["gen_tech"], df.get("gen_is_distributed", pd.Series(0, index=df.index)))]
    df.loc[dist, "category"] = "none"
    df["ic_weight"] = df["category"].map(lambda c: w.get(c, w.get("other", 1.0)))
    out = df.groupby("gen_tech", as_index=False).agg(ic_weight=("ic_weight", "max"),
                                                      category=("category", "first"))
    return out.rename(columns={"gen_tech": "ic_key"})


REINFORCEMENT_SHARE = REPO_ROOT / "interconnection_headroom" / "data" / "reference" / "reinforcement_share.csv"


def reeds_tech(gen_tech: str, energy_source: str = "") -> str | None:
    """ReEDS supply-curve tech (upv, wind-ons, wind-ofs) for a wind/solar gen_tech, else None."""
    c = category(energy_source, gen_tech)
    if c == "solar":
        return "upv"
    if c == "wind":
        return "wind-ofs" if "offshore" in str(gen_tech).lower() else "wind-ons"
    return None


def reinforcement_shares(gen_info: pd.DataFrame, shares: pd.DataFrame | None = None) -> pd.Series:
    """Reinforcement share of interconnection cost for each gen_info row (NaN if not wind/solar).

    Looked up by (gen_load_zone, ReEDS tech) in reinforcement_share.csv, falling back to the
    tech's "_national" row for zones without one.
    """
    if shares is None:
        shares = pd.read_csv(REINFORCEMENT_SHARE)
    s = shares.set_index(["zone", "tech"])["reinforcement_share"]
    nat = shares[shares["zone"] == "_national"].set_index("tech")["reinforcement_share"]
    es = gen_info["gen_energy_source"] if "gen_energy_source" in gen_info else pd.Series("", index=gen_info.index)
    out = []
    for zone, t, e in zip(gen_info["gen_load_zone"], gen_info["gen_tech"], es):
        rt = reeds_tech(t, e)
        out.append(None if rt is None else s.get((zone, rt), nat.get(rt)))
    return pd.Series(out, index=gen_info.index, dtype=float)


def strip_network_reinforcement(gen_info: pd.DataFrame, source: pd.DataFrame,
                                shares: pd.DataFrame | None = None) -> pd.DataFrame:
    """Remove network reinforcement from gen_info.gen_connect_cost_per_mw.

    PowerGenome-native costs carry it as tx_capex, which is subtracted. ReEDS-CPA site data carry
    one bundled interconnect_capex_mw (tx_capex all zero or absent); then the reinforcement part
    of it, interconnect_capex_mw x share (data/reference/reinforcement_share.csv, by load zone x
    ReEDS tech, national fallback), is subtracted for wind and solar clusters.

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
    tx = source["tx_capex"].fillna(0).values if "tx_capex" in source else None
    ic = source["interconnect_capex_mw"].fillna(0).values if "interconnect_capex_mw" in source else None
    if tx is not None and (tx > 0).any():
        gen_info["gen_connect_cost_per_mw"] = (gen_info["gen_connect_cost_per_mw"] - tx).clip(lower=0)
        diag["removal_method"] = "tx_capex"
        logger.info("interconnection_headroom: removed tx_capex from gen_connect_cost_per_mw for %d "
                    "resources", int((tx > 0).sum()))
    elif ic is not None and (ic > 0).any():
        share = reinforcement_shares(gen_info.reset_index(drop=True), shares).values
        removed = pd.Series(ic * share).fillna(0).values
        gen_info["gen_connect_cost_per_mw"] = (gen_info["gen_connect_cost_per_mw"] - removed).clip(lower=0)
        diag["reinforcement_share"] = share
        diag["removal_method"] = "interconnect_capex_mw x reinforcement_share"
        n = int((removed > 0).sum())
        logger.info("interconnection_headroom: tx_capex is zero or absent; removed the reinforcement "
                    "share of interconnect_capex_mw (reinforcement_share.csv) for %d wind/solar "
                    "resources, mean %.1f $/kW", n, removed[removed > 0].mean() / 1000 if n else 0.0)
    else:
        diag["removal_method"] = "none"
        logger.warning("interconnection_headroom: no tx_capex or interconnect_capex_mw cost; "
                       "gen_connect_cost_per_mw unchanged. Check that network reinforcement is not "
                       "still counted.")
    diag["gen_connect_cost_per_mw_after"] = gen_info["gen_connect_cost_per_mw"].values
    diag["removed_per_mw"] = diag["gen_connect_cost_per_mw_before"] - diag["gen_connect_cost_per_mw_after"]
    return diag


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
            "ic_uprate_mode": u["mode"] if "mode" in u else "stretch",
        }) if len(u) else pd.DataFrame(columns=["IC_UPRATE", "ic_uprate_zone", "ic_uprate_type",
                                                 "ic_uprate_max_mw", "ic_uprate_cost_per_mw",
                                                 "ic_uprate_available_year", "ic_uprate_mode"]),
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
    logger.info("interconnection_headroom: wrote %d IC zones, %d steps and %d uprate options (%s) to %s",
                len(frames["ic_zones.csv"]), len(frames["ic_tranches.csv"]),
                len(frames["ic_uprates.csv"]), scenario, out_folder)
