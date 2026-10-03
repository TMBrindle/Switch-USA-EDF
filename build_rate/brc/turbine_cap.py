"""Gas-turbine supply cap: case inputs for study_modules.build_rate (called from pg_to_switch.py).

Replaces MaxCapTag_GasTurbineSupply, which make_emission_policies.py used to write as MaxCapReq
entries (see SHARED_CHANGES.md). Settings: `build_rate.gas_turbine_cap` in pg/settings/build_rate.yml.

cap(P), cumulative_in_service form:
    baseline_mw + sum of annual_additions_mw[y] for y = baseline_year + 1 .. P
    raised to the covered generators' predetermined MW where that is higher (cap_req_files did the same)
new_additions_per_period form:
    sum of annual_additions_mw[y] over the period's build window period_start .. period_end

Written files (only when gas_turbine_cap.enabled):
    gas_turbine_cap_gens.csv     GENERATION_PROJECT, gtc_class, gtc_weight
    gas_turbine_cap.csv          PERIOD, gtc_max_mw
    gas_turbine_cap_params.csv   gtc_form, gtc_retirements_free_room
and MaxCapTag_GasTurbineSupply rows are removed from max_cap_requirements.csv and
max_cap_generators.csv, so the two caps never both apply.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
GAS_CAP_TAG = "MaxCapTag_GasTurbineSupply"
CLASSES = ("combined_cycle", "combustion_turbine", "aeroderivative", "reciprocating_engine")
DEFAULTS = {
    "enabled": False, "baseline_year": 2024, "baseline_mw": 451444.2,
    "annual_additions_mw": {2025: 58000 / 6}, "coverage": ["combined_cycle", "combustion_turbine"],
    "cc_accounting": "full_plant", "cc_turbine_share": 0.67, "form": "cumulative_in_service",
    "retirements_free_room": False,
}


def gtc_settings(settings: dict) -> dict | None:
    s = ((settings.get("build_rate") or {}).get("gas_turbine_cap")) or {}
    if not s.get("enabled"):
        return None
    out = {**DEFAULTS, **s}
    bad = set(out["coverage"]) - set(CLASSES)
    if bad:
        raise ValueError(f"gas_turbine_cap.coverage: unknown class(es) {sorted(bad)}; use {CLASSES}")
    if out["form"] not in ("cumulative_in_service", "new_additions_per_period"):
        raise ValueError(f"gas_turbine_cap.form must be cumulative_in_service or new_additions_per_period, "
                         f"not {out['form']!r}")
    if out["cc_accounting"] not in ("full_plant", "turbine_share"):
        raise ValueError(f"gas_turbine_cap.cc_accounting must be full_plant or turbine_share, "
                         f"not {out['cc_accounting']!r}")
    return out


def gas_class(gen_tech: str, energy_source: str = "") -> str | None:
    """Switch gen_tech (PowerGenome technology name) -> turbine class, or None if not a gas turbine.

    Existing units use EIA-860 technology names; new builds use ATB names (NaturalGas_<detail>_<case>).
    CCS combined cycles count as combined_cycle (the old MaxCapTag covered every new-build NaturalGas
    technology). Steam turbines, fuel cells and other gas technologies are not covered.
    """
    t = str(gen_tech).lower()
    es = str(energy_source).lower()
    if t == "natural gas fired combined cycle":
        return "combined_cycle"
    if t == "natural gas fired combustion turbine":
        return "combustion_turbine"
    if t == "natural gas internal combustion engine":
        return "reciprocating_engine"
    if not (t.startswith("naturalgas") or (es.startswith("naturalgas") and t.startswith("ng"))):
        return None
    if "aeroderivative" in t:
        return "aeroderivative"
    if "reciprocating" in t or "engine" in t:
        return "reciprocating_engine"
    if "combustion turbine" in t or "ctavg" in t or "_ct" in t or t.endswith("_gt") or "_gt_" in t:
        return "combustion_turbine"
    if "combined cycle" in t or "cc" in t:
        return "combined_cycle"
    return None


def _add(annual: dict, year: int) -> float:
    """Annual additions for build year `year`: the value of the latest key <= year (else the first key)."""
    a = {int(k): float(v) for k, v in annual.items()}
    keys = sorted(a)
    ks = [k for k in keys if k <= year]
    return a[ks[-1]] if ks else a[keys[0]]


def cap_mw(cfg: dict, period: int, start: int, end: int) -> float:
    """The cap for one period before the predetermined floor."""
    if cfg["form"] == "cumulative_in_service":
        return float(cfg["baseline_mw"]) + sum(_add(cfg["annual_additions_mw"], y)
                                               for y in range(int(cfg["baseline_year"]) + 1, int(period) + 1))
    return sum(_add(cfg["annual_additions_mw"], y) for y in range(int(start), int(end) + 1))


def write_case_inputs(out_folder: Path, settings: dict) -> list[str]:
    """Write the gas-turbine cap files and drop the MaxCapTag rows. Returns the files written."""
    cfg = gtc_settings(settings)
    out_folder = Path(out_folder)
    if cfg is None:
        return []
    gi = pd.read_csv(out_folder / "gen_info.csv", na_values=["."])
    periods = pd.read_csv(out_folder / "periods.csv")
    cls = pd.Series([gas_class(t, e) for t, e in zip(gi["gen_tech"], gi["gen_energy_source"])], index=gi.index)
    gens = gi.loc[cls.isin(cfg["coverage"]), ["GENERATION_PROJECT"]].assign(gtc_class=cls[cls.isin(cfg["coverage"])])
    w = np.where((gens["gtc_class"] == "combined_cycle") & (cfg["cc_accounting"] == "turbine_share"),
                 float(cfg["cc_turbine_share"]), 1.0)
    gens = gens.assign(gtc_weight=w)

    # membership cross-check against the old tag (written by PowerGenome from resource_tags.yml)
    mcg_path, mcr_path = out_folder / "max_cap_generators.csv", out_folder / "max_cap_requirements.csv"
    old = None
    if mcg_path.exists():
        mcg = pd.read_csv(mcg_path)
        old = set(mcg.loc[mcg["MAX_CAP_PROGRAM"] == GAS_CAP_TAG, "MAX_CAP_GEN"])
        if old and cfg["coverage"] == DEFAULTS["coverage"]:
            new = set(gens["GENERATION_PROJECT"])
            if old != new:
                logger.warning("gas_turbine_cap: coverage differs from %s: only in the tag: %s; only here: %s",
                               GAS_CAP_TAG, sorted(old - new)[:10], sorted(new - old)[:10])
        mcg[mcg["MAX_CAP_PROGRAM"] != GAS_CAP_TAG].to_csv(mcg_path, index=False)
    if mcr_path.exists():
        mcr = pd.read_csv(mcr_path)
        mcr[mcr["MAX_CAP_PROGRAM"] != GAS_CAP_TAG].to_csv(mcr_path, index=False)

    floor = 0.0
    pred_path = out_folder / "gen_build_predetermined.csv"
    if cfg["form"] == "cumulative_in_service" and pred_path.exists():
        pred = pd.read_csv(pred_path, na_values=["."])
        pmw = pd.to_numeric(pred["build_gen_predetermined"], errors="coerce").fillna(0)
        by_gen = pmw.groupby(pred["GENERATION_PROJECT"]).sum()
        floor = float((gens["GENERATION_PROJECT"].map(by_gen).fillna(0) * gens["gtc_weight"]).sum())
    rows = []
    for _, pr in periods.iterrows():
        p, s, e = int(pr["INVESTMENT_PERIOD"]), int(pr["period_start"]), int(pr["period_end"])
        c = cap_mw(cfg, p, s, e)
        if c < floor:
            logger.warning("gas_turbine_cap: %s cap %.1f MW is below the covered predetermined capacity "
                           "%.1f MW; raising it to that floor", p, c, floor)
            c = floor
        rows.append({"PERIOD": p, "gtc_max_mw": c})
    files = {
        "gas_turbine_cap_gens.csv": gens,
        "gas_turbine_cap.csv": pd.DataFrame(rows, columns=["PERIOD", "gtc_max_mw"]),
        "gas_turbine_cap_params.csv": pd.DataFrame([{
            "gtc_form": cfg["form"], "gtc_retirements_free_room": int(bool(cfg["retirements_free_room"]))}]),
    }
    for name, df in files.items():
        df.to_csv(out_folder / name, index=False)
    logger.info("gas_turbine_cap: %s, %d generators (%s), caps %s", cfg["form"], len(gens),
                ", ".join(sorted(gens["gtc_class"].unique())), [round(r["gtc_max_mw"]) for r in rows])
    return list(files)
