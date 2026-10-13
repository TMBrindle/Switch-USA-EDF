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
recorded; if still infeasible, medoids are swapped for other members of their own clusters (CHANGES §240,
_swap_representatives; the same targets, tails and tolerance schedule), and if that fails too it raises and no case
is written.
Diagnostics in <case>/<diag_dir>/<model year>/: fi_target_errors.csv, fi_tail_shares.csv,
fi_netload_error_by_fleet.csv, fi_days_selected.csv, fi_info.json. When the weights LP stays infeasible, before
raising: fi_elastic_slack.csv (the LP with elastic slack per constraint family, all together and each alone),
fi_target_range.csv, fi_cluster_profile_stats.csv and fi_failed_lp.npz (for scripts/diagnose_day_weights.py;
CHANGES §139), and fi_candidate_swaps.csv when a swap makes it feasible. Offshore wind is in neither the targets
nor the tails.
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
    swaps = []
    if res.status != 0:
        tl = [(nm, 100 * hrs[sel] / (365 * 24), 100 * hrs[peak] * w_peak / (365 * 24), lo, hi)
              for nm, hrs, lo, hi in tails]
        why = diagnose_infeasible(diag_dir, P, gens, A, b, t, names, tl, 365.0 - w_peak, float(ts["w_min"]), relax,
                                  [f"p{d_ + 1}" for d_ in sel])
        # §240: before giving up, try other members of the k-means clusters as their representatives (same
        # targets, tails and tolerance schedule); only reached when the medoids fail, so feasible selections are
        # unchanged
        found = _swap_representatives(sel, med, set(top_days) | set(low_days), km, feat, peak, include_peak_day, names,
                                      S, day, b, t, tails, w_peak, float(ts["w_min"]), factor, steps, ts)
        if found is None:
            raise RuntimeError(f"fleet-independent day weights LP infeasible even with tolerances x{relax:.2f}, "
                               f"and no swap of cluster representatives helps: {res.message}\n{why}")
        sel, relax, res, swaps = found
        Sn = len(sel)
        import logging
        logging.getLogger(__name__).warning(
            "fleet-independent day selection: the k-means medoids give no feasible weights at tolerances x%.2f; "
            "%d cluster representative(s) swapped, feasible at x%.3f (fi_candidate_swaps.csv; the medoids' "
            "diagnosis is in fi_elastic_slack.csv)", factor ** steps, len(swaps), relax)
        pd.DataFrame(swaps).to_csv(diag_dir / SWAPS_CSV, index=False)
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
    swapped = {x["to day"] for x in swaps}
    roles = ["top load" if x in top_days else ("low net load" if x in low_days else
             ("cluster member (swapped for the medoid)" if x + 1 in swapped else "medoid")) for x in sel]
    pd.DataFrame({"slot": [f"p{x + 1}" for x in days_all], "weight_days_per_yr": w_all,
                  "role": roles + (["peak load (PG convention)"] if include_peak_day else []),
                  "top-1% load hours": top_h[days_all],
                  "national load max GW": day(nat_load)[days_all].max(axis=1) / 1e3}
                 ).to_csv(diag_dir / "fi_days_selected.csv", index=False)
    json.dump({"settings": {k: v for k, v in ts.items() if k != "low_net_load_fleets"},
               "fleets": [str(f) for f in fleets] or ["stylised"], "relax_factor": relax, "n_selected": Sn,
               "lp_objective": float(res.fun), "record_days": D,
               **({"candidate_swaps": [f"p{x['from day']}->p{x['to day']}" for x in swaps]} if swaps else {})},
              open(diag_dir / "fi_info.json", "w"), indent=1)
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
# infeasibility diagnosis (CHANGES §139): only runs when the weights LP has no solution
# ---------------------------------------------------------------------------------------------------
FAMILIES = ("target", "tail", "w_min", "weight_sum")
LP_DUMP, SLACK_CSV, RANGE_CSV, CLUSTER_CSV = ("fi_failed_lp.npz", "fi_elastic_slack.csv", "fi_target_range.csv",
                                              "fi_cluster_profile_stats.csv")
SWAPS_CSV = "fi_candidate_swaps.csv"
SWAP_ALTERNATES, SWAP_MAX = 10, 3


def elastic_lp(A, b, t, tails, total, w_min, relax, families=FAMILIES):
    """The weights LP with elastic slack on each constraint family; slack is allowed only for `families`.

    Families and slack units: target (each side, in target units; scaled by the base tolerance), tail (each side, in
    percentage points; scaled by the base band width), w_min (each day, in days/yr; scaled by w_min), weight_sum (each
    side, in days/yr; scaled by w_min). Minimises the scaled slack sum; returns (status, x split by part, message)."""
    from scipy.optimize import linprog
    T, n = A.shape
    K = len(tails)
    coef = np.array([tl[1] for tl in tails]).reshape(K, n)
    const = np.array([tl[2] for tl in tails], dtype=float)
    lo = np.array([tl[3] for tl in tails], dtype=float)
    hi = np.array([tl[4] for tl in tails], dtype=float)
    # columns: w | s+ s- (T) | u_hi u_lo (K) | v (n) | q+ q- (2)
    sizes = [n, T, T, K, K, n, 2]
    off = np.cumsum([0] + sizes)
    N = off[-1]
    blk = lambda i, m: (slice(off[i], off[i] + m))
    rows, rhs = [], []

    def row():
        return np.zeros(N)
    for i in range(T):
        r = row(); r[blk(0, n)] = A[i]; r[off[1] + i] = -t[i]; rows.append(r); rhs.append(b[i] + relax * t[i])
        r = row(); r[blk(0, n)] = -A[i]; r[off[2] + i] = -t[i]; rows.append(r); rhs.append(-b[i] + relax * t[i])
    for k in range(K):
        r = row(); r[blk(0, n)] = coef[k]; r[off[3] + k] = -1; rows.append(r); rhs.append(hi[k] * relax - const[k])
        r = row(); r[blk(0, n)] = -coef[k]; r[off[4] + k] = -1; rows.append(r); rhs.append(-(lo[k] / relax) + const[k])
    for j in range(n):
        r = row(); r[j] = -1; r[off[5] + j] = -1; rows.append(r); rhs.append(-w_min)
    eq = row(); eq[blk(0, n)] = 1; eq[off[6]] = -1; eq[off[6] + 1] = 1
    band = np.maximum(hi - lo, 1e-9)
    scale = np.concatenate([np.zeros(n), np.ones(2 * T), 1 / band, 1 / band, np.full(n + 2, 1 / max(w_min, 1e-9))])
    fam_of = ["w"] * n + ["target"] * (2 * T) + ["tail"] * (2 * K) + ["w_min"] * n + ["weight_sum"] * 2
    bounds = [(0, None) if f == "w" or f in families else (0, 0) for f in fam_of]
    res = linprog(scale, A_ub=np.array(rows) if rows else None, b_ub=np.array(rhs) if rows else None,
                  A_eq=eq[None, :], b_eq=[total], bounds=bounds, method="highs")
    if res.status != 0:
        return res.status, None, res.message
    x = res.x
    parts = {"w": x[blk(0, n)], "s_hi": x[blk(1, T)], "s_lo": x[blk(2, T)], "u_hi": x[blk(3, K)],
             "u_lo": x[blk(4, K)], "v": x[blk(5, n)], "q": x[blk(6, 2)], "objective": float(res.fun)}
    return 0, parts, res.message


def _swap_representatives(sel, med, keep, km, feat, peak, include_peak_day, names, S, day, b, t, tails, w_peak, w_min,
                          factor, steps, ts):
    """When the medoids give no feasible weights (CHANGES §240). A k-means medoid is one member of its cluster, and
    the members nearest its centre are the closest alternatives to it. For each relaxation step of the usual schedule
    (x1, x factor, ..., x factor^steps), greedily swap medoids for other members of their own cluster (the
    `swap_alternates` members nearest its centre, default 10), at most `swap_max` swaps (default 3): a swap that
    makes the LP feasible ends the search (the one with the lowest weights-LP objective); otherwise the swap that
    most reduces the elastic slack (elastic_lp, every family) is kept and the search goes on. The top-load and
    low-net-load days (`keep`, also when a medoid is one) and the peak day are never swapped or swapped in;
    targets, tails, w_min and tolerances are unchanged. Returns (sel, relax, res, swaps) or None."""
    m = int(ts.get("swap_alternates", SWAP_ALTERNATES))
    max_swaps = int(ts.get("swap_max", SWAP_MAX))
    total = 365.0 - w_peak
    tails_of = lambda sel_: [(nm, 100 * hrs[sel_] / (365 * 24), 100 * hrs[peak] * w_peak / (365 * 24), lo, hi)
                             for nm, hrs, lo, hi in tails]
    A_of = lambda sel_: np.array([day(S[n])[sel_].mean(axis=1) for n in names]) / 365.0
    slots = {}                                              # position in sel -> (cluster, members by distance)
    for k, d_ in enumerate(med):
        if d_ not in sel or d_ in keep:
            continue
        idx = np.where(km.labels_ == k)[0]
        dist = ((feat[idx] - km.cluster_centers_[k]) ** 2).sum(axis=1)
        order = [int(i) for i in idx[np.argsort(dist, kind="stable")] if int(i) != d_]
        slots[sel.index(d_)] = (k, d_, order)

    def score(sel_, r):
        st, x, _ = elastic_lp(A_of(sel_), b, t, tails_of(sel_), total, w_min, r)
        return x["objective"] if st == 0 else np.inf

    for step in range(steps + 1):
        r = factor ** step
        cur, done = list(sel), []
        base = score(cur, r)
        for _ in range(max_swaps):
            best, feas = None, None
            for pos, (k, d0, order) in slots.items():
                alts = [a for a in order if a not in cur and a not in keep and not (include_peak_day and a == peak)][:m]
                for rank, a in enumerate(alts):
                    trial = list(cur)
                    trial[pos] = a
                    sc = score(trial, r)
                    if sc <= 1e-9:
                        tl = [x[1:] for x in tails_of(trial)]
                        res = _weights_lp(A_of(trial), b, t, tl, total, w_min, r)
                        if res.status == 0 and (feas is None or res.fun < feas[0] - 1e-12):
                            feas = (res.fun, trial, res, (pos, k, cur[pos], a, rank))
                    elif best is None or sc < best[0] - 1e-12:
                        best = (sc, trial, (pos, k, cur[pos], a, rank))
            if feas is not None:
                _, trial, res, (pos, k, d_from, a, rank) = feas
                done.append((k, d_from, a, rank, 0.0))
                return trial, r, res, [{"relax step": step, "tolerance factor": r, "cluster": k_, "from day": d1 + 1,
                                        "to day": d2 + 1, "alternate rank": rk + 1,
                                        "elastic slack after": sc_} for k_, d1, d2, rk, sc_ in done]
            if best is None or best[0] >= base - 1e-9:
                break
            base, cur = best[0], best[1]
            pos, k, d_from, a, rank = best[2]
            done.append((k, d_from, a, rank, base))
    return None


def slack_table(A, b, t, names, tails, total, w_min, relax, slots, tol_eps=1e-7) -> pd.DataFrame:
    """One elastic solve with every family elastic ("all"), then one per family with only that family elastic
    ("only <family>"). A row per nonzero slack, plus a row per run saying whether it is feasible."""
    out = []
    lo = {tl[0]: tl[3] for tl in tails}
    hi = {tl[0]: tl[4] for tl in tails}
    for run, fams in [("all", FAMILIES)] + [(f"only {f}", (f,)) for f in FAMILIES]:
        st, x, msg = elastic_lp(A, b, t, tails, total, w_min, relax, fams)
        base = {"run": run, "feasible": st == 0}
        if st != 0:
            out.append({**base, "family": "", "constraint": f"(infeasible: {msg})"})
            continue
        n_before = len(out)
        for i, nm in enumerate(names):
            for side, s_ in (("above", x["s_hi"][i]), ("below", x["s_lo"][i])):
                if s_ > tol_eps:
                    out.append({**base, "family": "target", "constraint": nm, "side": side,
                                "slack": s_ * t[i], "unit": "target units", "scaled slack": s_,
                                "limit now": f"tolerance x{relax:.3f} = {relax * t[i]:.6g}",
                                "limit needed": f"tolerance x{relax + s_:.3f} = {(relax + s_) * t[i]:.6g}"})
        for k, tl in enumerate(tails):
            for side, u, now, need in (("above hi", x["u_hi"][k], hi[tl[0]] * relax, hi[tl[0]] * relax + x["u_hi"][k]),
                                       ("below lo", x["u_lo"][k], lo[tl[0]] / relax, lo[tl[0]] / relax - x["u_lo"][k])):
                if u > tol_eps:
                    out.append({**base, "family": "tail", "constraint": tl[0], "side": side, "slack": u,
                                "unit": "% of hours", "scaled slack": u / max(hi[tl[0]] - lo[tl[0]], 1e-9),
                                "limit now": f"{now:.4f}", "limit needed": f"{need:.4f}"})
        for j, v in enumerate(x["v"]):
            if v > tol_eps:
                out.append({**base, "family": "w_min", "constraint": slots[j], "side": "below w_min", "slack": v,
                            "unit": "days/yr", "scaled slack": v / max(w_min, 1e-9), "limit now": f"{w_min:.4g}",
                            "limit needed": f"{w_min - v:.4g}"})
        for side, q in (("above total", x["q"][0]), ("below total", x["q"][1])):
            if q > tol_eps:
                out.append({**base, "family": "weight_sum", "constraint": "sum of weights", "side": side, "slack": q,
                            "unit": "days/yr", "scaled slack": q / max(w_min, 1e-9), "limit now": f"{total:.4f}",
                            "limit needed": f"{total + q if side == 'above total' else total - q:.4f}"})
        if len(out) == n_before:
            out.append({**base, "family": "", "constraint": "(no slack needed)"})
    cols = ["run", "feasible", "family", "constraint", "side", "slack", "unit", "scaled slack", "limit now",
            "limit needed"]
    return pd.DataFrame(out).reindex(columns=cols)


def target_range(A, b, t, names, total, relax) -> pd.DataFrame:
    """Whether each target is reachable on its own: with sum w = total, the sample mean lies between total x the
    smallest and largest candidate-day mean. A target outside that range, widened by its tolerance, needs other
    candidates (or another target definition), whatever the weights."""
    lo_, hi_ = A.min(axis=1) * total, A.max(axis=1) * total
    return pd.DataFrame({"target": names, "target (net of peak day)": b, "tolerance now": relax * t,
                         "reachable min": lo_, "reachable max": hi_,
                         "reachable alone": (b >= lo_ - relax * t - 1e-12) & (b <= hi_ + relax * t + 1e-12)})


def cluster_stats(P: dict, gens) -> pd.DataFrame:
    """Per wind/solar cluster: kind, region, mean, min, max, std, share of zero hours. Flags degenerate profiles
    (constant, mean near zero, outside [0, 1] by more than 1e-4: rounding excess such as 1.000015 is not flagged);
    only onwind and solar enter the targets."""
    if gens is None:
        return pd.DataFrame()
    R = P["R"]
    g = pd.DataFrame({"Resource": list(gens["Resource"]), "technology": list(gens["technology"]),
                      "region": list(gens["region"])}).drop_duplicates("Resource")
    g["kind"] = [kind(x) for x in g["technology"]]
    g = g[g["Resource"].isin(R.columns) & g["kind"].notna()]
    rows = []
    for _, r_ in g.iterrows():
        x = R[r_["Resource"]]
        x = x.iloc[:, 0] if isinstance(x, pd.DataFrame) else x
        rows.append({"Resource": r_["Resource"], "technology": r_["technology"], "region": r_["region"],
                     "kind": r_["kind"], "in targets": r_["kind"] in ("onwind", "solar"), "mean": x.mean(),
                     "min": x.min(), "max": x.max(), "std": x.std(), "share of zero hours": float((x == 0).mean()),
                     "degenerate": bool(x.max() - x.min() <= 1e-9 or abs(x.mean()) < 1e-6 or x.max() > 1 + 1e-4
                                        or x.min() < -1e-4)})
    return pd.DataFrame(rows)


def diagnose_infeasible(diag_dir: Path, P: dict, gens, A, b, t, names, tails, total, w_min, relax, slots) -> str:
    """Writes the failed LP (fi_failed_lp.npz, for scripts/diagnose_day_weights.py), the elastic slack table, the
    target ranges and the per-cluster profile stats; returns a short summary for the error message."""
    diag_dir = Path(diag_dir)
    diag_dir.mkdir(parents=True, exist_ok=True)
    np.savez(diag_dir / LP_DUMP, A=A, b=b, t=t, names=np.array(names, dtype=str),
             tail_names=np.array([tl[0] for tl in tails], dtype=str),
             tail_coef=np.array([tl[1] for tl in tails]).reshape(len(tails), A.shape[1]),
             tail_const=np.array([tl[2] for tl in tails], dtype=float),
             tail_lo=np.array([tl[3] for tl in tails], dtype=float),
             tail_hi=np.array([tl[4] for tl in tails], dtype=float),
             total=total, w_min=w_min, relax=relax, slots=np.array(slots, dtype=str))
    tab = slack_table(A, b, t, names, tails, total, w_min, relax, slots)
    tab.to_csv(diag_dir / SLACK_CSV, index=False)
    rng = target_range(A, b, t, names, total, relax)
    rng.to_csv(diag_dir / RANGE_CSV, index=False)
    cs = cluster_stats(P, gens)
    if len(cs):
        cs.to_csv(diag_dir / CLUSTER_CSV, index=False)
    return summarise(tab, rng, cs, diag_dir)


def summarise(tab: pd.DataFrame, rng: pd.DataFrame, cs: pd.DataFrame | None = None, where="") -> str:
    lines = [f"elastic diagnosis ({SLACK_CSV}, {RANGE_CSV}{', ' + CLUSTER_CSV if cs is not None and len(cs) else ''}"
             f"{' in ' + str(where) if where else ''}):"]
    for run, g in tab.groupby("run", sort=False):
        if not g["feasible"].iloc[0]:
            lines.append(f"  {run}: infeasible")
            continue
        g = g[g["family"].astype(str) != ""].sort_values("scaled slack", ascending=False)
        if not len(g):
            lines.append(f"  {run}: feasible with no slack")
            continue
        top = "; ".join(f"{r.family} '{r.constraint}' {r.side} by {r.slack:.4g} {r.unit} (needs {r['limit needed']})"
                        for _, r in g.head(5).iterrows())
        lines.append(f"  {run}: {len(g)} slack(s): {top}")
    bad = rng[~rng["reachable alone"]]
    if len(bad):
        lines.append("  unreachable from the candidates whatever the weights: " + ", ".join(bad["target"]))
    if cs is not None and len(cs) and cs["degenerate"].any():
        d = cs[cs["degenerate"]]
        lines.append(f"  degenerate profiles (constant, near zero or outside [0, 1]): {len(d)} clusters, "
                     f"{int(d['in targets'].sum())} in targets")
    return "\n".join(lines)


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
        c0 = combos[int(np.argmin(lbs))]
        A_, tl = sub(c0)
        why = diagnose_infeasible(diag_dir, P, None, A_, b, t, names,
                                  [(d[0], *x) for d, x in zip(tail_defs, tl)], total, w_lo, relax,
                                  [f"p{pool[i] + 1}x{B}" for i in c0])
        raise RuntimeError(f"block sample {N}x{B}: no feasible selection of {len(combos)} candidate sets even with "
                           f"tolerances x{relax:.2f} (lower bound needed x{lbs.min():.2f}); the set with the lowest "
                           f"bound:\n{why}")
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
