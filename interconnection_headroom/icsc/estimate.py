"""Estimate how network-upgrade cost rises with zonal saturation.

Model (OLS, cluster-robust by zone):

    log(1 + NU_$/kW) = f(saturation) + tech + service + b*log(MW) + c*(queue_year - 2015)
                       + regime fixed effect + e

f(.) is a linear spline in saturation (knots in config). The regime effect is what the
"best_regime" policy scenario swaps out; the saturation slope is treated as physics.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm


def spline_basis(s: np.ndarray, knots: list[float]) -> pd.DataFrame:
    s = np.asarray(s, dtype=float)
    cols = {"sat": s}
    for k in knots:
        cols[f"sat_gt_{k:g}"] = np.clip(s - k, 0, None)
    return pd.DataFrame(cols)


def attach_saturation(sample: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """Each project gets its zone's saturation in the year it entered the queue."""
    p = panel[["ba", "year", "saturation"]].copy()
    yr = sample["queue_year"].clip(p["year"].min(), p["year"].max()).astype(int)
    out = sample.assign(_yr=yr.values).merge(p, left_on=["ba", "_yr"], right_on=["ba", "year"], how="left",
                                             suffixes=("", "_p"))
    return out.drop(columns=["_yr", "year"])


@dataclass
class CostModel:
    result: object
    columns: list[str]
    knots: list[float]
    regimes: list[str]
    base_regime: str
    smear: float
    techs: list[str]
    sat_support: float = 1.0      # saturation beyond which predictions are extrapolated

    def _linear_pred(self, X: pd.DataFrame) -> np.ndarray:
        return X.values @ self.result.params[self.columns].values

    def log_pred(self, sat, tech, service, cap_mw, queue_year, regime) -> np.ndarray:
        """Log-scale prediction; beyond the data support the last segment's slope is extended (floored at 0)."""
        sat = np.atleast_1d(np.asarray(sat, dtype=float))
        inside = np.minimum(sat, self.sat_support)
        y = self._linear_pred(self.design(inside, tech, service, cap_mw, queue_year, regime))
        p = self.result.params
        slope = p.get("sat", 0.0) + sum(p.get(f"sat_gt_{k:g}", 0.0) for k in self.knots if k < self.sat_support)
        return y + max(slope, 0.0) * np.clip(sat - self.sat_support, 0, None)

    def regime_effects(self) -> pd.Series:
        eff = {self.base_regime: 0.0}
        for r in self.regimes:
            c = f"regime_{r}"
            if c in self.result.params:
                eff[r] = float(self.result.params[c])
        return pd.Series(eff).sort_values()

    def design(self, sat, tech, service, cap_mw, queue_year, regime) -> pd.DataFrame:
        n = len(np.atleast_1d(sat))
        X = spline_basis(np.atleast_1d(sat), self.knots)
        X["log_mw"] = np.log(np.broadcast_to(cap_mw, n).astype(float))
        X["trend"] = np.broadcast_to(queue_year, n).astype(float) - 2015
        for c in self.columns:
            if c.startswith("tech_"):
                X[c] = (np.broadcast_to(tech, n) == c[5:]).astype(float)
            elif c.startswith("service_"):
                X[c] = (np.broadcast_to(service, n) == c[8:]).astype(float)
            elif c.startswith("regime_"):
                X[c] = (np.broadcast_to(regime, n) == c[7:]).astype(float)
        X["const"] = 1.0
        return X.reindex(columns=self.columns, fill_value=0.0)

    def predict_cost(self, sat, tech, service, cap_mw, queue_year, regime, smear=True) -> np.ndarray:
        """Predicted network upgrade cost ($/kW, real) for the given characteristics."""
        yhat = self.log_pred(sat, tech, service, cap_mw, queue_year, regime)
        return np.clip(np.exp(yhat) * (self.smear if smear else 1.0) - 1, 0, None)


def fit(sample: pd.DataFrame, cfg: dict) -> CostModel:
    ec = cfg["estimation"]
    d = sample.dropna(subset=["saturation", "network_cost_real", "capacity_mw", "queue_year"]).copy()
    y = np.log1p(d["network_cost_real"].values)
    # keep only knots with enough projects above them; otherwise the last segment is noise
    min_obs = ec.get("min_obs_above_knot", 50)
    knots = [k for k in ec["saturation_knots"] if (d["saturation"] > k).sum() >= min_obs]
    X = spline_basis(d["saturation"].values, knots)
    X["log_mw"] = np.log(d["capacity_mw"].values)
    X["trend"] = d["queue_year"].values - 2015
    techs = sorted(d["tech_n"].unique())
    base_tech = "solar" if "solar" in techs else techs[0]
    for t in techs:
        if t != base_tech:
            X[f"tech_{t}"] = (d["tech_n"].values == t).astype(float)
    if d["service_n"].nunique() > 1:
        X["service_ERIS"] = (d["service_n"].values == "ERIS").astype(float)
    counts = d["regime"].value_counts()
    regimes = list(counts.index)
    base_regime = regimes[0]  # most common regime is the reference level
    for r in regimes[1:]:
        X[f"regime_{r}"] = (d["regime"].values == r).astype(float)
    X["const"] = 1.0
    # drop knot columns with no support (all zeros) to keep X full rank
    X = X.loc[:, (X != 0).any(axis=0)]
    X.index = d.index
    groups = pd.factorize(d[ec["cluster_column"]])[0]
    res = sm.OLS(pd.Series(y, index=d.index), X).fit(cov_type="cluster", cov_kwds={"groups": groups})
    smear = float(np.mean(np.exp(res.resid))) if cfg["tranches"]["duan_smearing"] else 1.0
    knots = [k for k in knots if f"sat_gt_{k:g}" in X.columns]
    support = float(d["saturation"].quantile(ec.get("support_quantile", 0.98)))
    return CostModel(res, list(X.columns), knots, regimes, base_regime, smear, techs, support)


def coef_table(m: CostModel) -> pd.DataFrame:
    r = m.result
    return pd.DataFrame({"coef": r.params, "se": r.bse,
                         "t": r.params / r.bse}).assign(n=int(r.nobs), r2=r.rsquared)
