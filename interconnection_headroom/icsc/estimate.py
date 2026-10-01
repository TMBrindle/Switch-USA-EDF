"""Estimate how network-upgrade cost rises with zonal saturation.

Model (estimation.estimator; cluster-robust SEs by zone):

  ppml (default)  E[NU_$/kW] = exp(f(saturation) + tech + service + status + b*log(MW)
                                   + c*(queue_year - 2015) + regime fixed effect)
                  Poisson pseudo-maximum likelihood (GLM, log link): consistent for the
                  conditional mean, keeps zero-cost projects, needs no retransformation.
  log_ols         log(1 + NU_$/kW) = same index + e, OLS; back-transformed with Duan smearing.

status: dummies for each sample status other than "active". With estimation.status_by_regime_min
set, a status gets its own dummy in each regime with at least that many projects of that status
(column status_<st>@<regime>) and one shared dummy for all other regimes (status_<st>@pooled).

f(.) is a linear spline in saturation (knots in config). The regime effect is what the
"best_regime" policy scenario swaps out; the saturation slope is treated as physics.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm

from . import linkage


def spline_basis(s: np.ndarray, knots: list[float]) -> pd.DataFrame:
    s = np.asarray(s, dtype=float)
    cols = {"sat": s}
    for k in knots:
        cols[f"sat_gt_{k:g}"] = np.clip(s - k, 0, None)
    return pd.DataFrame(cols)


def attach_saturation(sample: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """Each project gets its zone's saturation in the year it entered the queue.

    Projects placed only by transmission owner (`ba_candidates`, see linkage.py) get the
    MW-share-weighted mean saturation of their candidate zones.
    """
    p = panel[["ba", "year", "saturation"]].copy()
    yr = sample["queue_year"].clip(p["year"].min(), p["year"].max()).astype(int)
    out = sample.assign(_yr=yr.values).merge(p, left_on=["ba", "_yr"], right_on=["ba", "year"], how="left",
                                             suffixes=("", "_p"))
    out = out.drop(columns=["_yr", "year"])
    if "ba_candidates" in out and out["ba_candidates"].notna().any():
        cand = linkage.candidates_long(out).assign(year=lambda c: out.loc[c["row"], "queue_year"].clip(
            p["year"].min(), p["year"].max()).astype(int).values)
        cand = cand.merge(p, on=["ba", "year"], how="left")
        sat = (cand["w"] * cand["saturation"]).groupby(cand["row"]).sum(min_count=1)
        sat = sat.where(cand["saturation"].notna().groupby(cand["row"]).all())  # all candidate zones needed
        out.loc[sat.index, "saturation"] = sat
    return out


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
    estimator: str = "log_ols"
    status_regimes: dict = None   # status -> regimes with their own status effect (others pooled)

    @property
    def r2(self) -> float:
        """R² for log-OLS; for PPML the deviance pseudo-R² (1 - deviance / null deviance)."""
        r = self.result
        return float(r.rsquared) if self.estimator == "log_ols" else float(1 - r.deviance / r.null_deviance)

    def to_cost(self, index: np.ndarray, smear: bool = True) -> np.ndarray:
        """$/kW from the linear index."""
        if self.estimator == "ppml":
            return np.exp(index)
        return np.clip(np.exp(index) * (self.smear if smear else 1.0) - 1, 0, None)

    def _linear_pred(self, X: pd.DataFrame) -> np.ndarray:
        return X.values @ self.result.params[self.columns].values

    def log_pred(self, sat, tech, service, cap_mw, queue_year, regime, status="active") -> np.ndarray:
        """Linear index (log scale); beyond the data support the last segment's slope is extended (floored at 0)."""
        sat = np.atleast_1d(np.asarray(sat, dtype=float))
        inside = np.minimum(sat, self.sat_support)
        y = self._linear_pred(self.design(inside, tech, service, cap_mw, queue_year, regime, status))
        p = self.result.params
        slope = p.get("sat", 0.0) + sum(p.get(f"sat_gt_{k:g}", 0.0) for k in self.knots if k < self.sat_support)
        return y + max(slope, 0.0) * np.clip(sat - self.sat_support, 0, None)

    def status_effect(self, regime: str, status: str) -> float:
        """Coefficient of `status` (vs active) for a regime: its own if it has one, else the pooled one."""
        if status == "active":
            return 0.0
        p = self.result.params
        for c in (f"status_{status}@{regime}", f"status_{status}@pooled", f"status_{status}"):
            if c in p and (not c.endswith("@pooled") or regime not in (self.status_regimes or {}).get(status, [])):
                return float(p[c])
        return 0.0

    def regime_effects(self) -> pd.Series:
        eff = {self.base_regime: 0.0}
        for r in self.regimes:
            c = f"regime_{r}"
            if c in self.result.params:
                eff[r] = float(self.result.params[c])
        return pd.Series(eff).sort_values()

    def design(self, sat, tech, service, cap_mw, queue_year, regime, status="active") -> pd.DataFrame:
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
            elif c.startswith("status_"):
                st, _, grp = c[7:].partition("@")
                hit = np.broadcast_to(status, n) == st
                if grp:
                    own = np.isin(np.broadcast_to(regime, n), (self.status_regimes or {}).get(st, []))
                    hit = hit & ((np.broadcast_to(regime, n) == grp) if grp != "pooled" else ~own)
                X[c] = hit.astype(float)
        X["const"] = 1.0
        return X.reindex(columns=self.columns, fill_value=0.0)

    def predict_cost(self, sat, tech, service, cap_mw, queue_year, regime, status="active",
                     smear=True) -> np.ndarray:
        """Predicted mean network upgrade cost ($/kW, real) for the given characteristics."""
        return self.to_cost(self.log_pred(sat, tech, service, cap_mw, queue_year, regime, status), smear)


def fit(sample: pd.DataFrame, cfg: dict) -> CostModel:
    ec = cfg["estimation"]
    estimator = ec.get("estimator", "ppml")
    if estimator not in ("ppml", "log_ols"):
        raise ValueError(f"estimation.estimator must be ppml or log_ols, not {estimator!r}")
    d = sample.dropna(subset=["saturation", "network_cost_real", "capacity_mw", "queue_year"]).copy()
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
    status_regimes = {}
    if ec.get("status_term", True):
        min_n = ec.get("status_by_regime_min")
        for st in sorted(set(d["status_n"].unique()) - {"active"}):
            is_st = d["status_n"].values == st
            if not min_n:
                X[f"status_{st}"] = is_st.astype(float)
                continue
            n_by = d.loc[is_st, "regime"].value_counts()
            own = sorted(n_by[n_by >= min_n].index)
            status_regimes[st] = own
            for r in own:
                X[f"status_{st}@{r}"] = (is_st & (d["regime"].values == r)).astype(float)
            pooled = is_st & ~np.isin(d["regime"].values, own)
            if pooled.any():
                X[f"status_{st}@pooled"] = pooled.astype(float)
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
    if estimator == "ppml":
        y = pd.Series(d["network_cost_real"].values, index=d.index)
        res = sm.GLM(y, X, family=sm.families.Poisson()).fit(cov_type="cluster", cov_kwds={"groups": groups})
        smear = 1.0
    else:
        y = pd.Series(np.log1p(d["network_cost_real"].values), index=d.index)
        res = sm.OLS(y, X).fit(cov_type="cluster", cov_kwds={"groups": groups})
        smear = float(np.mean(np.exp(res.resid))) if cfg["tranches"]["duan_smearing"] else 1.0
    knots = [k for k in knots if f"sat_gt_{k:g}" in X.columns]
    support = float(d["saturation"].quantile(ec.get("support_quantile", 0.98)))
    return CostModel(res, list(X.columns), knots, regimes, base_regime, smear, techs, support, estimator,
                     status_regimes)


def coef_table(m: CostModel) -> pd.DataFrame:
    r = m.result
    return pd.DataFrame({"coef": r.params, "se": r.bse, "t": r.params / r.bse}).assign(
        n=int(r.nobs), r2=m.r2, estimator=m.estimator)
