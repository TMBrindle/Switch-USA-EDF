"""Fleet-independent representative-day selection (item 1), called by pg_to_switch.py per model year.

Productionised from s0_workflow/day_selection/netload_days_fi.py (the nl24_fi test runs). Settings:
`s0_production.time_sampling` in pg/settings/s0_production.yml, method `fleet_independent`
(`powergenome` keeps PowerGenome's k-means). Same algorithm and defaults as the test runs, with two
changes so it needs no hand steps:
  * resource clusters are classified from PowerGenome's own generator table for the model year
    (technology, region) instead of the gen_info.csv of an earlier case;
  * the low-net-load tail uses solved fleets only if `low_net_load_fleets` lists them; otherwise a
    stylised fleet sized from the record itself (each region's wind and solar sized to supply
    `stylised_fleet` shares of its load), so the selection depends on no model result.

Targets, by transreg and nationally (full-record means; every cluster counts equally):
  mean load (relative tolerance), mean onshore-wind CF (absolute; x the wind-loss factor when
  s0_production.wind_loss is on) and mean utility solar CF (absolute).
Tails (national): the top-1% load hours and the bottom-1% net-load hours must carry a sample weight
share in the configured bands (%).
Candidates: k-means medoids on daily regional load / wind CF / solar CF profiles (each block
standardised), plus the days with the most top-1% load hours and the most bottom-1% net-load hours.
PowerGenome's peak-load day is appended at its usual weight. Weights: an LP (w_d >= w_min, total
365 days) minimising sum |error| / tolerance subject to the hard tolerances and tail bands; if
infeasible, tolerances are relaxed (relax.factor, at most relax.max_steps times) and the factor is
recorded; if still infeasible it raises and no case is written.
Diagnostics in <case>/<diag_dir>/<model year>/: fi_target_errors.csv, fi_tail_shares.csv,
fi_netload_error_by_fleet.csv, fi_days_selected.csv, fi_info.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "method": "powergenome", "n_days": 24, "n_top_load_days": 3, "n_low_net_load_days": 3,
    "tolerances": {"load_rel": 0.01, "wind_cf": 0.010, "solar_cf": 0.005},
    "top_load_share_pct": [0.75, 1.25], "low_net_load_share_pct": [0.5, 1.5],
    "low_net_load_fleets": [], "stylised_fleet": {"wind_energy_share": 0.20, "solar_energy_share": 0.20},
    "w_min": 1.0, "seed": 0, "kmeans_n_init": 20, "relax": {"factor": 1.5, "max_steps": 2},
    "diag_dir": "time_sampling",
}


def ts_settings(settings: dict) -> dict | None:
    """The fleet-independent settings when this case/year uses them, else None."""
    s0 = settings.get("s0_production") or {}
    if not s0.get("enabled"):
        return None
    ts = {**DEFAULTS, **(s0.get("time_sampling") or {})}
    for k in ("tolerances", "relax", "stylised_fleet"):
        ts[k] = {**DEFAULTS[k], **((s0.get("time_sampling") or {}).get(k) or {})}
    if ts["method"] == "powergenome":
        return None
    if ts["method"] != "fleet_independent":
        raise ValueError(f"s0_production.time_sampling.method must be fleet_independent or powergenome, "
                         f"not {ts['method']!r}")
    if ts.get("sample") is not None:
        ts["n_days"], ts["block_days"] = parse_sample(ts["sample"])
    wl = s0.get("wind_loss") or {}
    ts["wind_factor"] = float(wl.get("factor", (1 - 0.134) / (1 - 0.017))) if wl.get("enabled") else 1.0
    return ts


BLOCK_DEFAULTS = {"block_pool": 16, "block_relax_max_steps": 12}


def parse_sample(sample) -> tuple[int, int]:
    """`sample`: N (N single days) or "NxL" (N chronological L-day blocks). Returns (N, L)."""
    txt = str(sample).strip().lower()
    n, sep, length = txt.partition("x")
    try:
        n, length = int(n), int(length if sep else 1)
    except ValueError:
        raise ValueError(f"s0_production.time_sampling.sample must be N or NxL (e.g. 24 or 4x3), not {sample!r}")
    if n < 1 or length < 1:
        raise ValueError(f"s0_production.time_sampling.sample {sample!r}: N and L must be at least 1")
    return n, length


def kind(technology: str) -> str | None:
    t = str(technology)
    tl = t.lower()
    if tl.startswith("offshorewind") or tl == "offshore wind turbine":
        return "offwind"
    if tl.startswith("landbasedwind") or tl == "onshore wind turbine":
        return "onwind"
    if "distributed" in tl or "rooftop" in tl:
        return "dpv"
    if tl.startswith("utilitypv") or tl == "solar photovoltaic":
        return "solar"
    return None


def zone_transreg(zones, zone_map: dict | None = None) -> dict:
    """Load zone -> transreg (hierarchy.csv); aggregate zones take their members' transreg when unique,
    or their own name when it is a transreg."""
    h = pd.read_csv(REPO / "hierarchy.csv")[["ba", "transreg"]]
    ba_tr = dict(zip(h["ba"], h["transreg"]))
    members = {}
    for ba, z in (zone_map or {}).items():
        members.setdefault(z, set()).add(ba)
    out = {}
    trs = set(h["transreg"])
    for z in zones:
        if z in ba_tr:
            out[z] = ba_tr[z]
        elif z in members and len({ba_tr.get(b) for b in members[z]}) == 1:
            out[z] = ba_tr.get(next(iter(members[z])))
        elif z in trs:
            out[z] = z
    return out


def _prepare(resource_profiles, load_profiles, gens, ts, zone_map, include_peak_day) -> dict:
    """Targets, tolerances, tails and the peak day shared by the day and block selections."""
    L = load_profiles.reset_index(drop=True).astype(float)
    R = resource_profiles.reset_index(drop=True).astype(float)
    H = len(L)
    D = H // 24
    if H != D * 24 or len(R) != H:
        raise ValueError(f"fleet-independent day selection needs whole days of hourly data ({H} load, {len(R)} resource rows)")
    tr = zone_transreg(L.columns, zone_map)
    g = pd.DataFrame({"Resource": list(gens["Resource"]), "k": [kind(t) for t in gens["technology"]],
                      "tr": [tr.get(z) for z in gens["region"]]})
    g = g[g["Resource"].isin(R.columns)].drop_duplicates("Resource").set_index("Resource")
    regions = sorted({tr[z] for z in L.columns if z in tr})
    day = lambda x: np.asarray(x).reshape(D, 24)
    wf = float(ts.get("wind_factor", 1.0))
    S = {}
    for r in regions:
        S[f"load {r}"] = L[[z for z in L.columns if tr.get(z) == r]].sum(axis=1).values
        w = g[(g["tr"] == r) & (g["k"] == "onwind")].index
        s = g[(g["tr"] == r) & (g["k"] == "solar")].index
        if len(w):
            S[f"wind CF {r}"] = R[w].mean(axis=1).values * wf
        if len(s):
            S[f"solar CF {r}"] = R[s].mean(axis=1).values
    S["load national"] = L.sum(axis=1).values
    S["wind CF national"] = R[g[g["k"] == "onwind"].index].mean(axis=1).values * wf
    S["solar CF national"] = R[g[g["k"] == "solar"].index].mean(axis=1).values
    tol_cfg = ts["tolerances"]
    tol = {n: (float(tol_cfg["load_rel"]) * v.mean() if n.startswith("load")
               else float(tol_cfg["wind_cf"] if "wind" in n else tol_cfg["solar_cf"])) for n, v in S.items()}

    # net load: solved fleets if given, else the stylised fleet
    def stylised():
        sf = ts["stylised_fleet"]
        out = {}
        for r in regions:
            load = S[f"load {r}"]
            nl = load.copy()
            for key, share in (("wind CF", sf["wind_energy_share"]), ("solar CF", sf["solar_energy_share"])):
                cf = S.get(f"{key} {r}")
                if cf is not None and cf.mean() > 0:
                    nl = nl - float(share) * load.mean() / cf.mean() * cf
            out[r] = nl
        return out

    def fleet(out_dir):
        d = pd.read_csv(Path(out_dir) / "dispatch_gen_annual_summary.csv")
        d = d[d.period == d.period.max()]
        d["k"] = [kind(t) for t in d.gen_tech]
        d = d.dropna(subset=["k"]).set_index("generation_project")
        cols = [c for c in R.columns if c in d.index]
        cov = d.loc[cols, "GenCapacity_MW"].sum() / d.GenCapacity_MW.sum()
        if cov < 0.98:
            raise RuntimeError(f"fleet {out_dir}: only {cov:.1%} of wind/solar MW matched")
        cap = d.loc[cols, "GenCapacity_MW"].values * np.where(d.loc[cols, "k"].isin(["onwind", "offwind"]), wf, 1.0)
        vre = R[cols].values * cap
        z = d.loc[cols, "gen_load_zone"].map(tr).values
        return {r: S[f"load {r}"] - (vre[:, z == r].sum(axis=1) if (z == r).any() else 0) for r in regions}

    fleets = list(ts.get("low_net_load_fleets") or [])
    NLF = {f: fleet(f) for f in fleets} if fleets else {"stylised": stylised()}
    tail_names = list(NLF)
    nat_load = S["load national"]
    top_h = day(nat_load > np.quantile(nat_load, 0.99)).sum(axis=1)
    low_h = {}
    for f in tail_names:
        nn = sum(NLF[f].values())
        low_h[f] = day(nn < np.quantile(nn, 0.01)).sum(axis=1)
    peak = int(np.argmax(day(nat_load).max(axis=1)))
    w_peak = 365.0 / (D + 1) if include_peak_day else 0.0

    return {"L": L, "R": R, "D": D, "S": S, "tol": tol, "NLF": NLF, "tail_names": tail_names, "fleets": fleets,
            "regions": regions, "top_h": top_h, "low_h": low_h, "peak": peak, "w_peak": w_peak,
            "nat_load": nat_load, "day": day}


def select_days(resource_profiles: pd.DataFrame, load_profiles: pd.DataFrame, gens: pd.DataFrame,
                ts: dict, diag_dir: Path, zone_map: dict | None = None, include_peak_day=True,
                variable_resources_only=True):
    """Drop-in for powergenome.time_reduction.kmeans_time_clustering with 1-day periods.

    gens: PowerGenome generator table for the model year, aligned with resource_profiles' columns
    (columns Resource, technology, region). Returns (results, representative_point, weights)."""
    from scipy.optimize import linprog
    from sklearn.cluster import KMeans

    diag_dir = Path(diag_dir)
    diag_dir.mkdir(parents=True, exist_ok=True)
    P = _prepare(resource_profiles, load_profiles, gens, ts, zone_map, include_peak_day)
    if int(ts.get("block_days") or 1) > 1:
        return _select_blocks(P, ts, diag_dir, include_peak_day, variable_resources_only)
    L, R, D, S, tol, NLF, tail_names, fleets, regions = (P[k] for k in (
        "L", "R", "D", "S", "tol", "NLF", "tail_names", "fleets", "regions"))
    top_h, low_h, peak, w_peak, nat_load, day = (P[k] for k in ("top_h", "low_h", "peak", "w_peak", "nat_load", "day"))

    blocks = []
    for n, v in S.items():
        if "national" in n:
            continue
        x = day(v)
        blocks.append((x - x.mean()) / (x.std() + 1e-9))
    feat = np.hstack(blocks)
    n_top, n_low = int(ts["n_top_load_days"]), int(ts["n_low_net_load_days"])
    K = int(ts["n_days"]) - n_top - n_low
    km = KMeans(n_clusters=K, n_init=int(ts["kmeans_n_init"]), random_state=int(ts["seed"])).fit(feat)
    med = []
    for k in range(K):
        idx = np.where(km.labels_ == k)[0]
        med.append(int(idx[np.argmin(((feat[idx] - km.cluster_centers_[k]) ** 2).sum(axis=1))]))
    top_days = [int(i) for i in np.argsort(-top_h - day(nat_load).max(axis=1) * 1e-9) if i != peak][:n_top]
    low_tot = sum(low_h.values())
    low_days = [int(i) for i in np.argsort(-low_tot) if i != peak][:n_low]
    sel = [d_ for d_ in dict.fromkeys(med + top_days + low_days) if not (include_peak_day and d_ == peak)]
    Sn = len(sel)
    names = list(S)
    A = np.array([day(S[n])[sel].mean(axis=1) for n in names]) / 365.0
    b = np.array([S[n].mean() - (day(S[n])[peak].mean() * w_peak / 365.0) for n in names])
    t = np.array([tol[n] for n in names])
    top_lo, top_hi = ts["top_load_share_pct"]
    low_lo, low_hi = ts["low_net_load_share_pct"]
    tails = [("top 1% load hours", top_h, top_lo, top_hi)] + [
        (f"bottom 1% net-load hours [{Path(str(f)).name}]", low_h[f], low_lo, low_hi) for f in tail_names]
    relax, factor, steps = 1.0, float(ts["relax"]["factor"]), int(ts["relax"]["max_steps"])
    for attempt in range(steps + 1):
        T = len(names)
        c = np.concatenate([np.zeros(Sn), 1 / t, 1 / t])
        Aeq = np.vstack([np.hstack([A, -np.eye(T), np.eye(T)]),
                         np.concatenate([np.ones(Sn), np.zeros(2 * T)])[None, :]])
        beq = np.concatenate([b, [365.0 - w_peak]])
        Aub, bub = [], []
        for i in range(T):
            for sgn in (1, -1):
                row = np.zeros(Sn + 2 * T)
                row[:Sn] = sgn * A[i]
                Aub.append(row)
                bub.append(relax * t[i] + sgn * b[i])
        for _, hrs, lo, hi in tails:
            coef = 100 * hrs[sel] / (365 * 24)
            const = 100 * hrs[peak] * w_peak / (365 * 24)
            row = np.zeros(Sn + 2 * T)
            row[:Sn] = coef
            Aub.append(row)
            bub.append(hi * relax - const)
            row = np.zeros(Sn + 2 * T)
            row[:Sn] = -coef
            Aub.append(row)
            bub.append(-(lo / relax) + const)
        res = linprog(c, A_ub=np.array(Aub), b_ub=np.array(bub), A_eq=Aeq, b_eq=beq,
                      bounds=[(float(ts["w_min"]), None)] * Sn + [(0, None)] * (2 * T), method="highs")
        if res.status == 0:
            break
        if attempt < steps:
            relax *= factor
    if res.status != 0:
        raise RuntimeError(f"fleet-independent day weights LP infeasible even with tolerances x{relax:.2f}: "
                           f"{res.message}")
    w = res.x[:Sn]
    days_all = sel + ([peak] if include_peak_day else [])
    w_all = np.concatenate([w, [w_peak]]) if include_peak_day else w
    hw = np.repeat(w_all, 24) / 365.0
    wmean = lambda s_: (day(s_)[days_all].reshape(-1) * hw).sum() / hw.sum()
    pd.DataFrame([{"target": n, "full mean": S[n].mean(), "sample mean": wmean(S[n]),
                   "error": wmean(S[n]) - S[n].mean(), "tolerance": tol[n] * relax,
                   "within": abs(wmean(S[n]) - S[n].mean()) <= tol[n] * relax + 1e-9} for n in names]
                 ).to_csv(diag_dir / "fi_target_errors.csv", index=False)
    pd.DataFrame([{"tail": nm, "full share %": 1.0, "sample share %": 100 * (hrs[days_all] * w_all).sum() / (365 * 24),
                   "band %": f"{lo / relax:.2f}-{hi * relax:.2f}"} for nm, hrs, lo, hi in tails]
                 ).to_csv(diag_dir / "fi_tail_shares.csv", index=False)
    nle = []
    for f in tail_names:
        for r in regions + ["national"]:
            s_ = sum(NLF[f].values()) if r == "national" else NLF[f][r]
            nle.append({"fleet": Path(str(f)).name, "transreg": r, "full mean GW": s_.mean() / 1e3,
                        "sample mean GW": wmean(s_) / 1e3, "error GW": (wmean(s_) - s_.mean()) / 1e3})
    pd.DataFrame(nle).to_csv(diag_dir / "fi_netload_error_by_fleet.csv", index=False)
    roles = ["top load" if x in top_days else ("low net load" if x in low_days else "medoid") for x in sel]
    pd.DataFrame({"slot": [f"p{x + 1}" for x in days_all], "weight_days_per_yr": w_all,
                  "role": roles + (["peak load (PG convention)"] if include_peak_day else []),
                  "top-1% load hours": top_h[days_all],
                  "national load max GW": day(nat_load)[days_all].max(axis=1) / 1e3}
                 ).to_csv(diag_dir / "fi_days_selected.csv", index=False)
    json.dump({"settings": {k: v for k, v in ts.items() if k != "low_net_load_fleets"},
               "fleets": [str(f) for f in fleets] or ["stylised"], "relax_factor": relax, "n_selected": Sn,
               "lp_objective": float(res.fun), "record_days": D}, open(diag_dir / "fi_info.json", "w"), indent=1)
    hrs = np.concatenate([np.arange(x * 24, x * 24 + 24) for x in days_all])
    load_df = L.iloc[hrs].reset_index(drop=True)
    res_df = R.iloc[hrs].reset_index(drop=True)
    if variable_resources_only:
        const = R.std() == 0
        res_df.loc[:, const[const].index] = 1.0
    rep = pd.DataFrame({"slot": [f"p{x + 1}" for x in days_all]})
    return ({"load_profiles": load_df, "resource_profiles": res_df, "ClusterWeights": list(w_all),
             "AnnualGenScaleFactor": 1.0}, rep, list(w_all))


# ---------------------------------------------------------------------------------------------------
# chronological multi-day blocks (sample: NxL, L > 1)
# ---------------------------------------------------------------------------------------------------
def _weights_lp(A, b, t, tails, total, w_lo, relax):
    """The day-mode weight LP for given candidate columns: minimise sum |error| / tolerance subject to
    |error| <= relax x tolerance, the tail bands (hi x relax, lo / relax), sum w = total, w >= w_lo.
    tails: [(coef, const, lo, hi)] with share % = coef . w + const."""
    from scipy.optimize import linprog
    T, n = A.shape
    c = np.concatenate([np.zeros(n), 1 / t, 1 / t])
    Aeq = np.vstack([np.hstack([A, -np.eye(T), np.eye(T)]),
                     np.concatenate([np.ones(n), np.zeros(2 * T)])[None, :]])
    beq = np.concatenate([b, [total]])
    Z = np.zeros((T, 2 * T))
    Aub = [np.hstack([A, Z]), np.hstack([-A, Z])]
    bub = [relax * t + b, relax * t - b]
    for coef, const, lo, hi in tails:
        row = np.concatenate([coef, np.zeros(2 * T)])[None, :]
        Aub += [row, -row]
        bub += [[hi * relax - const], [-(lo / relax) + const]]
    return linprog(c, A_ub=np.vstack(Aub), b_ub=np.concatenate(bub), A_eq=Aeq, b_eq=beq,
                   bounds=[(w_lo, None)] * n + [(0, None)] * (2 * T), method="highs")


def _min_relax_lb(A, b, t, tails, total, w_lo):
    """A lower bound on the uniform relaxation a candidate set needs: minimise rho subject to |error| <= rho x
    tolerance, tail <= hi x rho and tail >= lo x (2 - rho) (the tangent of lo / rho at 1, a weaker bound)."""
    from scipy.optimize import linprog
    T, n = A.shape
    c = np.concatenate([np.zeros(n), [1.0]])
    Aub = [np.hstack([A, -t[:, None]]), np.hstack([-A, -t[:, None]])]
    bub = [b, -b]
    for coef, const, lo, hi in tails:
        Aub += [np.concatenate([coef, [-hi]])[None, :], np.concatenate([-coef, [-lo]])[None, :]]
        bub += [[-const], [const - 2 * lo]]
    Aeq = np.concatenate([np.ones(n), [0.0]])[None, :]
    res = linprog(c, A_ub=np.vstack(Aub), b_ub=np.concatenate(bub), A_eq=Aeq, b_eq=[total],
                  bounds=[(w_lo, None)] * n + [(1.0, None)], method="highs")
    return float(res.x[-1]) if res.status == 0 else np.inf


def _select_blocks(P: dict, ts: dict, diag_dir: Path, include_peak_day: bool, variable_resources_only: bool):
    """N chronological L-day blocks against the day mode's targets and tail bands.

    Candidates: k-means medoids of the record's aligned L-day blocks (block_pool of them, on the day mode's
    standardised daily profiles, concatenated over the block's days) plus a block centred on each of the
    day mode's top-load and low-net-load days; no candidate overlaps the peak day. Every non-overlapping set
    of N candidates gets the day mode's weight LP (weights in days/yr, each block at least w_min occurrences a
    year); the feasible set with the smallest objective wins. Tolerances and bands are relaxed (relax.factor
    per step, at most block_relax_max_steps steps) only when no set is feasible; the factor is reported.
    PowerGenome's peak-load day is appended as a single day at its usual weight."""
    import itertools
    import logging
    from sklearn.cluster import KMeans

    log = logging.getLogger(__name__)
    L, R, D, S, tol, NLF, tail_names, fleets, regions = (P[k] for k in (
        "L", "R", "D", "S", "tol", "NLF", "tail_names", "fleets", "regions"))
    top_h, low_h, peak, w_peak, nat_load, day = (P[k] for k in ("top_h", "low_h", "peak", "w_peak", "nat_load", "day"))
    N, B = int(ts["n_days"]), int(ts["block_days"])
    pool_k = int(ts.get("block_pool", BLOCK_DEFAULTS["block_pool"]))
    max_steps = int(ts.get("block_relax_max_steps", BLOCK_DEFAULTS["block_relax_max_steps"]))
    if N * B > D:
        raise ValueError(f"sample {N}x{B} needs more days than the record's {D}")
    clash = lambda s_: include_peak_day and s_ <= peak < s_ + B

    # candidates
    feats = []
    for n, v in S.items():
        if "national" in n:
            continue
        x = day(v)
        feats.append((x - x.mean()) / (x.std() + 1e-9))
    fday = np.hstack(feats)
    aligned = [s_ for s_ in range(0, D - B + 1, B) if not clash(s_)]
    bf = np.array([fday[s_:s_ + B].reshape(-1) for s_ in aligned])
    K = min(pool_k, len(aligned))
    km = KMeans(n_clusters=K, n_init=int(ts["kmeans_n_init"]), random_state=int(ts["seed"])).fit(bf)
    med = []
    for k in range(K):
        idx = np.where(km.labels_ == k)[0]
        med.append(aligned[int(idx[np.argmin(((bf[idx] - km.cluster_centers_[k]) ** 2).sum(axis=1))])])

    def around(d_):
        for off in sorted(range(B), key=lambda o: abs(o - B // 2)):
            s_ = d_ - off
            if 0 <= s_ <= D - B and not clash(s_):
                return s_
        return None

    n_top, n_low = int(ts["n_top_load_days"]), int(ts["n_low_net_load_days"])
    top_days = [int(i) for i in np.argsort(-top_h - day(nat_load).max(axis=1) * 1e-9) if i != peak][:n_top]
    low_tot = sum(low_h.values())
    low_days = [int(i) for i in np.argsort(-low_tot) if i != peak][:n_low]
    role = {}
    for s_ in med:
        role.setdefault(s_, "medoid")
    for d_ in top_days:
        s_ = around(d_)
        if s_ is not None:
            role.setdefault(s_, "top load")
    for d_ in low_days:
        s_ = around(d_)
        if s_ is not None:
            role.setdefault(s_, "low net load")
    pool = list(role)

    names = list(S)
    bmean = lambda n, s_: day(S[n])[s_:s_ + B].mean()
    Afull = np.array([[bmean(n, s_) for s_ in pool] for n in names]) / 365.0
    b = np.array([S[n].mean() - (day(S[n])[peak].mean() * w_peak / 365.0) for n in names])
    t = np.array([tol[n] for n in names])
    top_lo, top_hi = ts["top_load_share_pct"]
    low_lo, low_hi = ts["low_net_load_share_pct"]
    tail_defs = [("top 1% load hours", top_h, top_lo, top_hi)] + [
        (f"bottom 1% net-load hours [{Path(str(f)).name}]", low_h[f], low_lo, low_hi) for f in tail_names]
    bh = lambda hrs, s_: hrs[s_:s_ + B].sum()
    tail_full = [(np.array([100 * bh(hrs, s_) / B / (365 * 24) for s_ in pool]),
                  100 * hrs[peak] * w_peak / (365 * 24), lo, hi) for _, hrs, lo, hi in tail_defs]
    total = 365.0 - w_peak
    w_lo = float(ts["w_min"]) * B

    combos = [c for c in itertools.combinations(range(len(pool)), N)
              if all(abs(pool[i] - pool[j]) >= B for i, j in itertools.combinations(c, 2))]
    if not combos:
        raise RuntimeError(f"no {N} non-overlapping {B}-day candidate blocks")
    sub = lambda c: (Afull[:, list(c)], [(coef[list(c)], const, lo, hi) for coef, const, lo, hi in tail_full])
    lbs = []
    for c in combos:
        A_, tl = sub(c)
        lbs.append(_min_relax_lb(A_, b, t, tl, total, w_lo))
    lbs = np.array(lbs)
    factor = float(ts["relax"]["factor"])
    best, relax, n_feasible = None, 1.0, 0
    for step in range(max_steps + 1):
        relax = factor ** step
        for ci in np.where(lbs <= relax * (1 + 1e-9))[0]:
            A_, tl = sub(combos[ci])
            res = _weights_lp(A_, b, t, tl, total, w_lo, relax)
            if res.status == 0:
                n_feasible += 1
                if best is None or res.fun < best[1].fun - 1e-12:
                    best = (combos[ci], res)
        if best is not None:
            break
    if best is None:
        raise RuntimeError(f"block sample {N}x{B}: no feasible selection of {len(combos)} candidate sets even with "
                           f"tolerances x{relax:.2f} (lower bound needed x{lbs.min():.2f})")
    if relax > 1:
        log.warning(f"block sample {N}x{B}: no selection meets the targets and tail bands; tolerances and bands "
                    f"relaxed x{relax:.3f} (see fi_target_errors.csv)")
    c, res = best
    c = sorted(c, key=lambda i: pool[i])
    order = [list(best[0]).index(i) for i in c]
    starts = [pool[i] for i in c]
    w_days = res.x[:N][order]

    # diagnostics (weights in days/yr; each block hour stands for w/B of its hours)
    hrs_idx = [np.arange(s_ * 24, (s_ + B) * 24) for s_ in starts] + ([np.arange(peak * 24, peak * 24 + 24)]
                                                                       if include_peak_day else [])
    hw = np.concatenate([np.full(B * 24, w / B) for w in w_days] + ([np.full(24, w_peak)] if include_peak_day else []))
    hw = hw / 365.0
    hcat = np.concatenate(hrs_idx)
    wmean = lambda s_: (np.asarray(s_)[hcat] * hw).sum() / hw.sum()
    pd.DataFrame([{"target": n, "full mean": S[n].mean(), "sample mean": wmean(S[n]),
                   "error": wmean(S[n]) - S[n].mean(), "tolerance": tol[n] * relax, "base tolerance": tol[n],
                   "error / base tolerance": abs(wmean(S[n]) - S[n].mean()) / tol[n],
                   "within": abs(wmean(S[n]) - S[n].mean()) <= tol[n] * relax + 1e-9} for n in names]
                 ).to_csv(diag_dir / "fi_target_errors.csv", index=False)
    pd.DataFrame([{"tail": nm, "full share %": 1.0,
                   "sample share %": 100 * (sum(bh(hrs, s_) * w / B for s_, w in zip(starts, w_days))
                                            + (hrs[peak] * w_peak if include_peak_day else 0)) / (365 * 24),
                   "band %": f"{lo / relax:.2f}-{hi * relax:.2f}", "base band %": f"{lo:.2f}-{hi:.2f}"}
                  for nm, hrs, lo, hi in tail_defs]).to_csv(diag_dir / "fi_tail_shares.csv", index=False)
    nle = []
    for f in tail_names:
        for r in regions + ["national"]:
            s_ = sum(NLF[f].values()) if r == "national" else NLF[f][r]
            nle.append({"fleet": Path(str(f)).name, "transreg": r, "full mean GW": s_.mean() / 1e3,
                        "sample mean GW": wmean(s_) / 1e3, "error GW": (wmean(s_) - s_.mean()) / 1e3})
    pd.DataFrame(nle).to_csv(diag_dir / "fi_netload_error_by_fleet.csv", index=False)
    slots = [f"p{s_ + 1}x{B}" for s_ in starts] + ([f"p{peak + 1}"] if include_peak_day else [])
    first = [s_ for s_ in starts] + ([peak] if include_peak_day else [])
    lens = [B] * N + ([1] if include_peak_day else [])
    occ = [w / B for w in w_days] + ([w_peak] if include_peak_day else [])
    pd.DataFrame({"slot": slots, "start_day": first, "n_days": lens,
                  "weight_days_per_yr": list(w_days) + ([w_peak] if include_peak_day else []),
                  "occurrences_per_yr": occ,
                  "role": [role[s_] for s_ in starts] + (["peak load (PG convention)"] if include_peak_day else []),
                  "top-1% load hours": [bh(top_h, s_) for s_ in starts] + ([top_h[peak]] if include_peak_day else []),
                  "national load max GW": [day(nat_load)[s_:s_ + B].max() / 1e3 for s_ in starts]
                  + ([day(nat_load)[peak].max() / 1e3] if include_peak_day else [])}
                 ).to_csv(diag_dir / "fi_days_selected.csv", index=False)
    json.dump({"settings": {k: v for k, v in ts.items() if k != "low_net_load_fleets"},
               "fleets": [str(f) for f in fleets] or ["stylised"], "sample": f"{N}x{B}", "relax_factor": relax,
               "relaxed": relax > 1, "n_candidates": len(pool), "n_candidate_sets": len(combos),
               "n_feasible_at_chosen_relax": n_feasible, "min_relax_lower_bound": float(lbs.min()),
               "lp_objective": float(res.fun), "record_days": D}, open(diag_dir / "fi_info.json", "w"), indent=1)
    hrs = np.concatenate(hrs_idx)
    load_df = L.iloc[hrs].reset_index(drop=True)
    res_df = R.iloc[hrs].reset_index(drop=True)
    if variable_resources_only:
        const = R.std() == 0
        res_df.loc[:, const[const].index] = 1.0
    rep = pd.DataFrame({"slot": slots, "start_day": first, "n_days": lens})
    return ({"load_profiles": load_df, "resource_profiles": res_df, "ClusterWeights": occ,
             "AnnualGenScaleFactor": 1.0, "relax_factor": relax}, rep, occ)


def sample_is_blocks(representative_point: pd.DataFrame) -> bool:
    return "n_days" in representative_point.columns


def ts_tp_blocks(representative_point: pd.DataFrame, weights, planning_year: int, planning_start_year: int):
    """timeseries.csv and timepoints.csv rows for a sample of mixed-length chronological slots (blocks from
    _select_blocks, the single peak day, stress days appended by s0_workflow/prm.py). Same ids as
    conversion_functions.ts_tp_pg_kmeans: timeseries <year>_<slot>, timepoint yyyy[rep]mmddhh (365-day years);
    each slot is one timeseries of n_days x 24 hours scaled by its weight (occurrences a year). Rows without
    start_day / n_days (stress days) are single days pN."""
    rp = representative_point.reset_index(drop=True)
    start = [int(s) if pd.notna(s) else int(str(p)[1:]) - 1
             for s, p in zip(rp.get("start_day", [np.nan] * len(rp)), rp.slot)]
    ndays = [int(n) if pd.notna(n) else 1 for n in rp.get("n_days", [np.nan] * len(rp))]
    cal = pd.date_range("2001-01-01", "2001-12-31", freq="D")              # a non-leap year
    last_hour = max((s + n) * 24 - 1 for s, n in zip(start, ndays))
    rep_count = last_hour // 8760 + 1
    rep_digits = int(np.log10(rep_count)) + 1
    planning_yrs = planning_year - planning_start_year + 1
    ts_rows, tp_rows = [], []
    for slot, w, s, n in zip(rp.slot, weights, start, ndays):
        tsn = f"{planning_year}_{slot}"
        ts_rows.append({"timeseries": tsn, "ts_period": planning_year, "ts_duration_of_tp": 1,
                        "ts_num_tps": n * 24, "ts_scale_to_period": w * planning_yrs})
        for h in range(n * 24):
            rep_num, hour_of_year = divmod(s * 24 + h, 8760)
            doy, hod = divmod(hour_of_year, 24)
            tp = f"{planning_year:04d}{cal.month[doy]:02d}{cal.day[doy]:02d}{hod:02d}"
            if rep_count > 1:
                tp = f"{tp[:4]}{rep_num + 1:0{rep_digits}d}{tp[4:]}"
            tp_rows.append({"timepoint_id": tp, "timestamp": tp, "timeseries": tsn})
    return pd.DataFrame(ts_rows), pd.DataFrame(tp_rows)
