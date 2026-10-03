"""Fleet-independent representative-day selection, a drop-in replacement for
powergenome.time_reduction.kmeans_time_clustering (used via pg_to_switch_netload.py with NL_SELECTOR=netload_days_fi).
Works on PowerGenome's own hourly arrays (no reconstruction).

Targets, by transreg (all 11, incl. CAISO, NorthernGrid, WestConnect) and nationally, full-record means:
  * mean load                           tolerance +/- TOL_LOAD_REL (relative)
  * mean onshore-wind CF                tolerance +/- TOL_WIND (absolute CF); unweighted mean over the region's onshore
                                        wind resource clusters (existing + candidate), windloss2 (x 0.881) applied
  * mean solar CF                       tolerance +/- TOL_SOLAR (absolute CF); unweighted mean over utility-scale and
                                        existing solar clusters (distributed PV excluded)
  These use no fleet: every cluster counts equally, whatever is built.
Tails (national):
  * top 1% of full-record load hours: sample share in [TOP_LO, TOP_HI] %
  * bottom 1% of full-record net-load hours: sample share in [LOW_LO, LOW_HI] % on EACH of the fleets in TAIL_FLEETS
    (net load needs a fleet; two fleets that bracket the plausible range are used only for this tail check)
Candidates: k-means medoids (K = N_DAYS - N_TOP - N_LOW) on fleet-independent daily profiles (regional load, wind CF,
solar CF; each block scaled), + N_TOP days with the most top-1% load hours, + N_LOW days with the most bottom-1% net-load
hours (union over TAIL_FLEETS). PowerGenome's peak-load day is appended at its usual weight (1 raw day ~ 1/7 day/yr).
Weights: LP, w_d >= W_MIN, sum 365 (peak day fixed); minimises the sum of |error| / tolerance over all targets subject to
the hard tolerances. If infeasible, tolerances are relaxed x1.5 (at most twice) and the factor is recorded.
Diagnostics (written before the case is solved): errors per target, tail shares, and the mean net-load error by region on
every fleet in REPORT_FLEETS (exact, PowerGenome arrays).
"""
import json, os
from pathlib import Path
import numpy as np, pandas as pd

CFG = json.loads(os.environ.get("NL_DAYS_CFG", "{}"))
N_DAYS = int(CFG.get("n_days", 24)); N_TOP = int(CFG.get("n_top", 3)); N_LOW = int(CFG.get("n_low", 3))
TOL_LOAD_REL = float(CFG.get("tol_load_rel", 0.01)); TOL_WIND = float(CFG.get("tol_wind", 0.010)); TOL_SOLAR = float(CFG.get("tol_solar", 0.005))
TOP_LO, TOP_HI = CFG.get("top_share", [0.75, 1.25]); LOW_LO, LOW_HI = CFG.get("low_share", [0.5, 1.5])
W_MIN = float(CFG.get("w_min", 1.0)); SEED = int(CFG.get("seed", 0))
TAIL_FLEETS = CFG["tail_fleets"]; REPORT_FLEETS = CFG.get("report_fleets", TAIL_FLEETS)
GEN_INFO = CFG["gen_info"]; DIAG_DIR = Path(CFG["diag_dir"]); HIER = CFG["hierarchy"]
WINDLOSS = (1 - 0.134) / (1 - 0.017)


def _kind(tech, src):
    t = str(tech).lower()
    if src == "wind": return "offwind" if "offshore" in t else "onwind"
    if src == "sun": return "dpv" if "distributed" in t else "solar"
    return None


def kmeans_time_clustering(resource_profiles, load_profiles, days_in_group, num_clusters, include_peak_day=True,
                           load_weight=1, variable_resources_only=True, n_init=100):
    from scipy.optimize import linprog
    from sklearn.cluster import KMeans
    assert days_in_group == 1
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    tr = pd.read_csv(HIER).set_index("ba").transreg
    L = load_profiles.reset_index(drop=True).astype(float); R = resource_profiles.reset_index(drop=True).astype(float)
    H = len(L); D = H // 24; assert H == D * 24 and len(R) == H
    gi = pd.read_csv(GEN_INFO, usecols=["GENERATION_PROJECT", "gen_tech", "gen_energy_source", "gen_load_zone"]).set_index("GENERATION_PROJECT")
    gi = gi.reindex([c for c in R.columns if c in gi.index]); gi["k"] = [_kind(t, s) for t, s in zip(gi.gen_tech, gi.gen_energy_source)]
    gi["tr"] = gi.gen_load_zone.map(tr)
    regions = sorted(set(tr.reindex(L.columns).dropna()))
    day = lambda x: np.asarray(x).reshape(D, 24)
    # fleet-independent series
    S = {}
    for r in regions:
        S[f"load {r}"] = L[[z for z in L.columns if tr.get(z) == r]].sum(axis=1).values
        w = gi[(gi.tr == r) & (gi.k == "onwind")].index; s = gi[(gi.tr == r) & (gi.k == "solar")].index
        if len(w): S[f"wind CF {r}"] = R[w].mean(axis=1).values * WINDLOSS
        if len(s): S[f"solar CF {r}"] = R[s].mean(axis=1).values
    S["load national"] = L.sum(axis=1).values
    S["wind CF national"] = R[gi[gi.k == "onwind"].index].mean(axis=1).values * WINDLOSS
    S["solar CF national"] = R[gi[gi.k == "solar"].index].mean(axis=1).values
    tol = {n: (TOL_LOAD_REL * v.mean() if n.startswith("load") else (TOL_WIND if "wind" in n else TOL_SOLAR)) for n, v in S.items()}

    # fleets (tail check and reporting)
    def fleet_nl(out):
        d = pd.read_csv(Path(out) / "dispatch_gen_annual_summary.csv"); d = d[d.period == d.period.max()]
        d["k"] = [_kind(t, s) for t, s in zip(d.gen_tech, d.gen_energy_source)]
        d = d.dropna(subset=["k"]).set_index("generation_project"); cols = [c for c in R.columns if c in d.index]
        cov = d.loc[cols, "GenCapacity_MW"].sum() / d.GenCapacity_MW.sum()
        if cov < 0.98: raise RuntimeError(f"fleet {out}: only {cov:.1%} of wind/solar MW matched")
        cap = d.loc[cols, "GenCapacity_MW"].values * np.where(d.loc[cols, "k"].isin(["onwind", "offwind"]), WINDLOSS, 1.0)
        vre_mw = R[cols].values * cap; z = d.loc[cols, "gen_load_zone"].map(tr).values
        out_r = {}
        for r in regions:
            m = z == r
            out_r[r] = S[f"load {r}"] - (vre_mw[:, m].sum(axis=1) if m.any() else 0)
        return out_r
    NLF = {f: fleet_nl(f) for f in dict.fromkeys(TAIL_FLEETS + REPORT_FLEETS)}
    nat_load = S["load national"]
    top_thr = np.quantile(nat_load, 0.99); top_h = day(nat_load > top_thr).sum(axis=1)
    low_h = {}
    for f in TAIL_FLEETS:
        nn = sum(NLF[f].values()); low_h[f] = day(nn < np.quantile(nn, 0.01)).sum(axis=1)
    peak = int(np.argmax(day(nat_load).max(axis=1))); w_peak = 365.0 / (D + 1)
    # candidates
    blocks = []
    for n, v in S.items():
        if "national" in n: continue
        x = day(v); blocks.append((x - x.mean()) / (x.std() + 1e-9))
    feat = np.hstack(blocks)
    K = N_DAYS - N_TOP - N_LOW
    km = KMeans(n_clusters=K, n_init=20, random_state=SEED).fit(feat)
    med = []
    for k in range(K):
        idx = np.where(km.labels_ == k)[0]; med.append(int(idx[np.argmin(((feat[idx] - km.cluster_centers_[k]) ** 2).sum(axis=1))]))
    top_days = [int(i) for i in np.argsort(-top_h - day(nat_load).max(axis=1) * 1e-9) if i != peak][:N_TOP]
    low_tot = sum(low_h.values()); low_days = [int(i) for i in np.argsort(-low_tot) if i != peak][:N_LOW]
    sel = [s for s in dict.fromkeys(med + top_days + low_days) if s != peak]
    Sn = len(sel)
    names = list(S.keys())
    A = np.array([day(S[n])[sel].mean(axis=1) for n in names]) / 365.0
    b = np.array([S[n].mean() - day(S[n])[peak].mean() * w_peak / 365.0 for n in names])
    t = np.array([tol[n] for n in names])
    # tail rows: share (%) = 100 * sum_d w_d * hours_d / (365*24) (+ peak contribution)
    tails = [("top 1% load hours", top_h, TOP_LO, TOP_HI)] + [(f"bottom 1% net-load hours [{Path(f).name}]", low_h[f], LOW_LO, LOW_HI) for f in TAIL_FLEETS]
    relax = 1.0
    for attempt in range(3):
        T = len(names)
        c = np.concatenate([np.zeros(Sn), 1 / t, 1 / t])
        Aeq = np.vstack([np.hstack([A, -np.eye(T), np.eye(T)]), np.concatenate([np.ones(Sn), np.zeros(2 * T)])[None, :]])
        beq = np.concatenate([b, [365.0 - w_peak]])
        Aub, bub = [], []
        for i in range(T):
            for sgn in (1, -1):
                row = np.zeros(Sn + 2 * T); row[:Sn] = sgn * A[i]; Aub.append(row); bub.append(relax * t[i] + sgn * b[i])
        for _, hrs, lo, hi in tails:
            coef = 100 * hrs[sel] / (365 * 24); const = 100 * hrs[peak] * w_peak / (365 * 24)
            row = np.zeros(Sn + 2 * T); row[:Sn] = coef; Aub.append(row); bub.append(hi * relax - const)
            row = np.zeros(Sn + 2 * T); row[:Sn] = -coef; Aub.append(row); bub.append(-(lo / relax) + const)
        res = linprog(c, A_ub=np.array(Aub), b_ub=np.array(bub), A_eq=Aeq, b_eq=beq,
                      bounds=[(W_MIN, None)] * Sn + [(0, None)] * (2 * T), method="highs")
        if res.status == 0: break
        relax *= 1.5
    if res.status != 0:
        raise RuntimeError(f"fleet-independent day weights LP infeasible even with tolerances x{relax / 1.5:.2f}: {res.message}")
    w = res.x[:Sn]; days_all = sel + [peak]; w_all = np.concatenate([w, [w_peak]]); hw = np.repeat(w_all, 24) / 365.0
    wmean = lambda s: (day(s)[days_all].reshape(-1) * hw).sum() / hw.sum()
    diag = pd.DataFrame([{"target": n, "full mean": S[n].mean(), "sample mean": wmean(S[n]), "error": wmean(S[n]) - S[n].mean(),
                          "tolerance": tol[n] * relax, "within": abs(wmean(S[n]) - S[n].mean()) <= tol[n] * relax + 1e-9} for n in names])
    tl = pd.DataFrame([{"tail": nm, "full share %": 1.0, "sample share %": 100 * (hrs[days_all] * w_all).sum() / (365 * 24),
                        "band %": f"{lo / relax:.2f}-{hi * relax:.2f}"} for nm, hrs, lo, hi in tails])
    nle = []
    for f in REPORT_FLEETS:
        for r in regions + ["national"]:
            s = sum(NLF[f].values()) if r == "national" else NLF[f][r]
            nle.append({"fleet": Path(f).name, "transreg": r, "full mean GW": s.mean() / 1e3, "sample mean GW": wmean(s) / 1e3, "error GW": (wmean(s) - s.mean()) / 1e3})
    nle = pd.DataFrame(nle)
    days_df = pd.DataFrame({"slot": [f"p{x + 1}" for x in days_all], "weight_days_per_yr": w_all,
                            "role": ["top load" if x in top_days else ("low net load" if x in low_days else "medoid") for x in sel] + ["peak load (PG convention)"],
                            "top-1% load hours": top_h[days_all], "national load max GW": day(nat_load)[days_all].max(axis=1) / 1e3})
    diag.to_csv(DIAG_DIR / "fi_target_errors.csv", index=False); tl.to_csv(DIAG_DIR / "fi_tail_shares.csv", index=False)
    nle.to_csv(DIAG_DIR / "fi_netload_error_by_fleet.csv", index=False); days_df.to_csv(DIAG_DIR / "fi_days_selected.csv", index=False)
    json.dump({"cfg": CFG, "relax_factor": relax, "n_selected": Sn, "lp_objective": res.fun, "record_days": D}, open(DIAG_DIR / "fi_info.json", "w"), indent=1)
    hrs = np.concatenate([np.arange(x * 24, x * 24 + 24) for x in days_all])
    load_df = L.iloc[hrs].reset_index(drop=True); res_df = R.iloc[hrs].reset_index(drop=True)
    if variable_resources_only:
        const = R.std() == 0; res_df.loc[:, const[const].index] = 1.0
    rep = pd.DataFrame({"slot": [f"p{x + 1}" for x in days_all]})
    return ({"load_profiles": load_df, "resource_profiles": res_df, "ClusterWeights": list(w_all), "AnnualGenScaleFactor": 1.0}, rep, list(w_all))
