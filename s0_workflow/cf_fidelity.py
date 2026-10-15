"""Sample-fidelity rescaling of variable profiles (option B; CHANGES §116; s0_production.time_sampling.cf_fidelity).

The national sample (24 days plus tails) matches regional and national load and CF targets, but individual zones'
sampled CF can drift from the full record: the dashboards' scan of S0 v3.1 (data/rev-cf-mismatch dbc3637) found
sampled/full-record CF outside 0.95-1.05 in 110 of 670 PV, 422 of 660 onshore-wind and 127 of 180 offshore zone-stages
(load: 1 of 670).

rescale (Tom: the national-run fix; option A dropped; the sample size does not grow): for every PV, onshore-wind and
offshore-wind cluster (day_selection.kind: solar, onwind, offwind),
    scale = full-record mean CF / sample-weighted mean CF,    weights = ts_scale_to_period x ts_duration_of_tp
and the cluster's sampled profile is multiplied by it, capped at 1. The energy the cap removes is put back on the
uncapped sample hours, in up to `max_passes` proportional passes, so the sample-weighted mean equals the full-record mean
(any residual is reported). Stress days (timeseries ending _prm, weight 0) are left as they are. Pre-processing only:
the same days, timepoints and model size. Scales outside flag_range (default 0.8-1.25) are applied and flagged.

Report: vcf_fidelity.csv (one row per cluster and model year: full-record and sampled CF before and after, scale,
capped share of sample hours, residual, flag). Production-cost runs over many days or the full record should leave
this off (their scale is about 1), and they must start from a build without it.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from s0_workflow.day_selection import kind

logger = logging.getLogger(__name__)
DEFAULTS = {"mode": "off", "techs": ["solar", "onwind", "offwind"], "flag_range": [0.8, 1.25], "max_passes": 5}


def settings(year_settings: dict) -> dict | None:
    """The rescale settings when this case/year uses them (S0 cases, mode rescale), else None."""
    s0 = (year_settings or {}).get("s0_production") or {}
    if not s0.get("enabled"):
        return None
    c = {**DEFAULTS, **(((s0.get("time_sampling") or {}).get("cf_fidelity")) or {})}
    if c["mode"] not in ("off", "rescale"):
        raise ValueError(f"s0_production.time_sampling.cf_fidelity.mode must be off or rescale, not {c['mode']!r}")
    bad = set(c["techs"]) - {"solar", "onwind", "offwind"}
    if bad:
        raise ValueError(f"s0_production.time_sampling.cf_fidelity.techs: unknown {sorted(bad)}")
    return c if c["mode"] == "rescale" else None


def row_weights(timeseries_df: pd.DataFrame, timepoints_df: pd.DataFrame) -> np.ndarray:
    """One weight per sampled row (the rows of the sampled profiles are timepoints_df's rows, in order): the timeseries'
    ts_scale_to_period x ts_duration_of_tp; 0 for stress days (_prm)."""
    ts = timeseries_df.set_index("timeseries")
    w = (ts["ts_scale_to_period"].astype(float) * ts["ts_duration_of_tp"].astype(float))
    w[[str(x).endswith("_prm") for x in w.index]] = 0.0
    return timepoints_df["timeseries"].map(w).fillna(0.0).to_numpy(float)


def _rescale_column(x: np.ndarray, w: np.ndarray, target: float, passes: int, cap: float = 1.0):
    on = w > 0
    m = float((x[on] * w[on]).sum() / w[on].sum())
    if m <= 0:
        return x, np.nan, 0.0, target
    s = target / m
    y = x.copy()
    y[on] = np.minimum(x[on] * s, cap)
    for _ in range(passes):
        cur = float((y[on] * w[on]).sum() / w[on].sum())
        gap = target - cur
        free = on & (y < cap) & (y > 0)
        if abs(gap) < 1e-9 or not free.any():
            break
        mass = float((y[free] * w[free]).sum())
        y[free] = np.minimum(y[free] * (1 + gap * w[on].sum() / mass), cap)
    capped = float(((y >= cap) & on & (x * s > cap)).sum() / max(on.sum(), 1))
    after = float((y[on] * w[on]).sum() / w[on].sum())
    return y, s, capped, after


def rescale(full: pd.DataFrame, sampled: pd.DataFrame, gens: pd.DataFrame, weights: np.ndarray, cfg: dict,
            model_year=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rescaled copy of `sampled` (same rows and columns) and the report. full: the full-record profiles (columns =
    Resource); gens: Resource, technology."""
    out = sampled.copy()
    tech = dict(zip(gens["Resource"], gens["technology"]))
    lo, hi = cfg["flag_range"]
    rows = []
    for col in sampled.columns:
        k = kind(tech.get(col, ""))
        if k not in cfg["techs"] or col not in full.columns:
            continue
        target = float(full[col].astype(float).mean())
        x = sampled[col].astype(float).to_numpy()
        y, s, capped, after = _rescale_column(x, weights, target, int(cfg["max_passes"]))
        on = weights > 0
        before = float((x[on] * weights[on]).sum() / weights[on].sum()) if on.any() else np.nan
        out[col] = y.astype(sampled[col].dtype) if np.issubdtype(sampled[col].dtype, np.floating) else y
        flag = "" if (np.isnan(s) or lo <= s <= hi) else f"scale outside {lo}-{hi}"
        if np.isnan(s):
            flag = "no output on the sampled days: not rescaled"
        rows.append({"model_year": model_year, "GENERATION_PROJECT": col, "kind": k, "cf_full_record": target,
                     "cf_sampled_before": before, "scale": s, "cf_sampled_after": after,
                     "residual": after - target, "capped_share_of_sample_hours": capped, "flag": flag})
    rep = pd.DataFrame(rows)
    if len(rep):
        f = rep[rep.flag != ""]
        logger.info("cf_fidelity rescale %s: %d clusters, scale %.3f-%.3f (median %.3f); %d flagged%s", model_year,
                    len(rep), rep.scale.min(), rep.scale.max(), rep.scale.median(), len(f),
                    (": " + ", ".join(f"{r.GENERATION_PROJECT} {r.scale:.3f}" for r in f.head(20).itertuples()))
                    if len(f) else "")
    return out, rep


# --------------------------------------------------------------------------------------------- load and hydro (§246)
LOAD_HYDRO_MODES = ("off", "rescale")


def load_hydro_settings(year_settings: dict) -> dict | None:
    """cf_fidelity.load_hydro (v3.2 flag, CHANGES §246): rescale (with cf_fidelity mode rescale) or off (the code
    default, v3.1). The settings when on, else None."""
    c = settings(year_settings)
    if c is None:
        return None
    mode = c.get("load_hydro", "off")
    if mode not in LOAD_HYDRO_MODES:
        raise ValueError(f"s0_production.time_sampling.cf_fidelity.load_hydro must be off or rescale, not {mode!r}")
    return c if mode == "rescale" else None


def rescale_load_hydro(full_load: pd.DataFrame, sampled_load: pd.DataFrame, full_var: pd.DataFrame,
                       sampled_var: pd.DataFrame, gens: pd.DataFrame, weights: np.ndarray, cfg: dict, model_year=None):
    """Each zone's sample-weighted load, and each hydro project's sample-weighted flow, set to its full-record mean
    (the day weights let national load drift by up to 1.5% and hydro budgets by -14 to +31 TWh in the S0 v3.2 bridge).

    Load: zone column x (full-record mean / sample-weighted mean), uniformly over the sampled hours, the peak day
    included (Tom, 15 Oct 2026). Hydro (gens HYDRO == 1): the same scale, capped at the column's full-record maximum
    (at least 1; PowerGenome's hydro_factor can lift hydro profiles above 1), with the capped energy put back on the
    uncapped hours as for wind and solar. Stress days (weight 0) are left as they are. The hydro budgets
    (hydro_avg_flow_mw) and water-node flows follow from the rescaled profiles. Returns (load, profiles, report)."""
    load, var = sampled_load.copy(), sampled_var.copy()
    rows = []
    on = weights > 0
    for kind_, full, out, cols, cap_of in (
            ("load", full_load, load, [c for c in sampled_load.columns if c in full_load.columns], lambda c: np.inf),
            ("hydro", full_var, var, [r for r in gens.loc[gens["HYDRO"] == 1, "Resource"]
                                      if r in sampled_var.columns and r in full_var.columns],
             lambda c: max(1.0, float(full_var[c].astype(float).max())))):
        for col in cols:
            target = float(full[col].astype(float).mean())
            x = out[col].astype(float).to_numpy()
            before = float((x[on] * weights[on]).sum() / weights[on].sum()) if on.any() else np.nan
            y, sc, capped, after = _rescale_column(x, weights, target, int(cfg["max_passes"]), cap_of(col))
            out[col] = y
            rows.append({"model_year": model_year, "kind": kind_, "name": col, "full_record_mean": target,
                         "sampled_mean_before": before, "scale": sc, "sampled_mean_after": after,
                         "residual": after - target, "capped_share_of_sample_hours": capped,
                         "full_record_annual": target * 8760})
    rep = pd.DataFrame(rows)
    if len(rep):
        ld = rep[rep.kind == "load"]
        logger.info("cf_fidelity load_hydro %s: load %d zones, scale %.4f-%.4f, national %+.2f TWh to the full record; "
                    "hydro %d projects, scale %.3f-%.3f", model_year, len(ld), ld.scale.min(), ld.scale.max(),
                    float(((ld.sampled_mean_after - ld.sampled_mean_before) * 8760).sum()) / 1e6,
                    int((rep.kind == "hydro").sum()), rep[rep.kind == "hydro"].scale.min(),
                    rep[rep.kind == "hydro"].scale.max())
    return load, var, rep
