"""Gas-turbine supply cap: case inputs for study_modules.build_rate (called from pg_to_switch.py).

Replaces MaxCapTag_GasTurbineSupply, which make_emission_policies.py used to write as MaxCapReq
entries (see SHARED_CHANGES.md). Settings: `build_rate.gas_turbine_cap` in pg/settings/build_rate.yml.

cap(P), cumulative_in_service form:
    baseline_mw + sum of annual_additions_mw[y] for y = baseline_year + 1 .. P
    raised to the covered generators' predetermined MW where that is higher (cap_req_files did the same)
new_additions_per_period form:
    sum of annual_additions_mw[y] over the period's build window period_start .. period_end
cumulative_additions form (S0 default from Oct 2026; "Gas-turbine supply constraint for S0", 3 Oct 2026):
    sum of class weight x capacity basis factor x MW of covered units with a build year from
    since_year (2025) to the period's end year (new builds and planned/predetermined units; retirements
    irrelevant; no installed-base offset) <= allowance at the period's end year
    (allowance_paths[allowance_path], GW turbine-equivalent, EIA-860M nameplate basis, extended past its
    last year at the slope between extend_slope_years), raised to the covered predetermined additions
    where those are higher

Written files (only when gas_turbine_cap.enabled):
    gas_turbine_cap_gens.csv     GENERATION_PROJECT, gtc_class, gtc_weight
    gas_turbine_cap.csv          PERIOD, gtc_max_mw
    gas_turbine_cap_params.csv   gtc_form, gtc_retirements_free_room, gtc_since_year
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
    # cumulative_additions form only
    "since_year": 2025, "allowance_path": "central", "allowance_paths": {}, "class_weights": {},
    "extend_slope_years": [2035, 2040], "capacity_basis": {},
}
FORMS = ("cumulative_in_service", "new_additions_per_period", "cumulative_additions")


def gtc_settings(settings: dict) -> dict | None:
    s = ((settings.get("build_rate") or {}).get("gas_turbine_cap")) or {}
    if not s.get("enabled"):
        return None
    out = {**DEFAULTS, **s}
    bad = set(out["coverage"]) - set(CLASSES)
    if bad:
        raise ValueError(f"gas_turbine_cap.coverage: unknown class(es) {sorted(bad)}; use {CLASSES}")
    if out["form"] not in FORMS:
        raise ValueError(f"gas_turbine_cap.form must be one of {FORMS}, not {out['form']!r}")
    if out["form"] == "cumulative_additions" and out.get("allowance_coverage"):
        out["coverage"] = list(out["allowance_coverage"])
        bad = set(out["coverage"]) - set(CLASSES)
        if bad:
            raise ValueError(f"gas_turbine_cap.allowance_coverage: unknown class(es) {sorted(bad)}")
    if out["form"] == "cumulative_additions" and out["allowance_path"] not in (out["allowance_paths"] or {}):
        raise ValueError(f"gas_turbine_cap.allowance_path {out['allowance_path']!r} not in allowance_paths "
                         f"({sorted(out['allowance_paths'] or {})})")
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


def allowance_mw(cfg: dict, year: int) -> float:
    """Cumulative allowance (MW turbine-equivalent) at the end of `year`: the path's table (GW), 0 before
    its first year, extended past its last year at the slope between extend_slope_years."""
    tab = {int(k): float(v) * 1000 for k, v in cfg["allowance_paths"][cfg["allowance_path"]].items()}
    years = sorted(tab)
    y = int(year)
    if y < years[0]:
        return 0.0
    if y in tab:
        return tab[y]
    if y > years[-1]:
        # beyond the source table (2040 in build_rate.yml): a coordinator estimate, not sourced
        a, b = (int(x) for x in cfg["extend_slope_years"])
        logger.info("gas_turbine_cap: %s allowance for %d extended past %d at the %d-%d rate "
                    "(coordinator estimate)", cfg["allowance_path"], y, years[-1], a, b)
        return tab[years[-1]] + (tab[b] - tab[a]) / (b - a) * (y - years[-1])
    lo = max(k for k in years if k < y)
    hi = min(k for k in years if k > y)
    return tab[lo] + (tab[hi] - tab[lo]) * (y - lo) / (hi - lo)


def cap_mw(cfg: dict, period: int, start: int, end: int) -> float:
    """The cap for one period before the predetermined floor."""
    if cfg["form"] == "cumulative_additions":
        return allowance_mw(cfg, end)
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
    pred_path = out_folder / "gen_build_predetermined.csv"
    pred = pd.read_csv(pred_path, na_values=["."]) if pred_path.exists() else None
    if cfg["form"] == "cumulative_additions":
        # turbine-equivalent class weights x the factor converting Switch MW to the allowance's basis
        # (EIA-860M nameplate), by class: predetermined (existing/planned) units and new builds each
        # take a per-class factor (a number applies to every class)
        cw = {k: float(v) for k, v in (cfg["class_weights"] or {}).items()}
        basis = cfg["capacity_basis"] or {}

        def factors(v):
            if isinstance(v, dict):
                return lambda c: float(v.get(c, 1.0))
            return lambda c: float(1.0 if v is None else v)
        pre_f = factors(basis.get("predetermined_to_allowance"))
        new_f = factors(basis.get("new_build_to_allowance"))
        existing = set(pred["GENERATION_PROJECT"]) if pred is not None else set()
        w = [cw.get(c, 1.0) * (pre_f(c) if g in existing else new_f(c))
             for g, c in zip(gens["GENERATION_PROJECT"], gens["gtc_class"])]
    else:
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
    wmap = gens.set_index("GENERATION_PROJECT")["gtc_weight"]
    if pred is not None:
        pred = pred.assign(mw=pd.to_numeric(pred["build_gen_predetermined"], errors="coerce").fillna(0),
                           w=pred["GENERATION_PROJECT"].map(wmap).fillna(0))
    if cfg["form"] == "cumulative_in_service" and pred is not None:
        floor = float((pred["mw"] * pred["w"]).sum())
    rows = []
    for _, pr in periods.iterrows():
        p, s, e = int(pr["INVESTMENT_PERIOD"]), int(pr["period_start"]), int(pr["period_end"])
        c = cap_mw(cfg, p, s, e)
        if cfg["form"] == "cumulative_additions" and pred is not None:
            by = pred["build_year"].astype(float)
            floor = float((pred["mw"] * pred["w"])[(by >= int(cfg["since_year"])) & (by <= e)].sum())
        if c < floor:
            logger.warning("gas_turbine_cap: %s cap %.1f MW is below the covered predetermined %s "
                           "%.1f MW; raising it to that floor", p, c,
                           "additions" if cfg["form"] == "cumulative_additions" else "capacity", floor)
            c = floor
        rows.append({"PERIOD": p, "gtc_max_mw": c})
    files = {
        "gas_turbine_cap_gens.csv": gens,
        "gas_turbine_cap.csv": pd.DataFrame(rows, columns=["PERIOD", "gtc_max_mw"]),
        "gas_turbine_cap_params.csv": pd.DataFrame([{
            "gtc_form": cfg["form"], "gtc_retirements_free_room": int(bool(cfg["retirements_free_room"])),
            "gtc_since_year": int(cfg["since_year"])}]),
    }
    for name, df in files.items():
        df.to_csv(out_folder / name, index=False)
    logger.info("gas_turbine_cap: %s, %d generators (%s), caps %s", cfg["form"], len(gens),
                ", ".join(sorted(gens["gtc_class"].unique())), [round(r["gtc_max_mw"]) for r in rows])
    return list(files)
