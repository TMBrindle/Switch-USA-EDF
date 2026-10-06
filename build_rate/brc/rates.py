"""Build-rate tables: base rates, R0, queue-based near-term rates, growth, regional ceilings, tiers.

R_data[G, national, y] (MW/yr) by level:
    y <= near_term.last_year:  max(queue-based expected additions in y, R0)
    later:                     R_data[y-1] x (1 + growth_paths[level growth][G][y]) (§76: anchored to the round-1
                               caps), or R_data[last near-term year] x (1 + growth_G) ** (y - last) without a path
Regional ceiling[G, r, y] = max(share_r x regional_mult_G x top band x R_data[G, y], floor[G, r]),
floor[G, r] = max(floor_min_G, k_stock_G x existing MW end-2025, k_peak_G x peak annual build 2010-25).

§77 deliverability layer (config `deliverability`): final national ceiling = min(top band x R, D[G, y]), D the
level's deliverability path; where it cuts, regional ceilings are scaled by the same factor. R is unchanged (the case
writer truncates the tier bands at the final ceiling). Module floor: R[G, y] >= D_path[G, y] in configured years.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NATIONAL = "national"


def base_rates(add: pd.DataFrame, first: int, last: int) -> pd.DataFrame:
    """Annual additions (MW) by group, region (transregs + national), year."""
    a = add[add["year"].between(first, last)]
    reg = a[a["transreg"].notna()].groupby(["group", "transreg", "year"])["mw"].sum().rename("mw").reset_index()
    reg = reg.rename(columns={"transreg": "region"})
    nat = a.groupby(["group", "year"])["mw"].sum().rename("mw").reset_index().assign(region=NATIONAL)
    full = pd.concat([nat, reg], ignore_index=True)
    # fill missing (group, region, year) with 0
    idx = pd.MultiIndex.from_product([sorted(full["group"].unique()), sorted(full["region"].unique()),
                                      range(first, last + 1)], names=["group", "region", "year"])
    return full.set_index(["group", "region", "year"])["mw"].reindex(idx, fill_value=0.0).reset_index()


def r0(base: pd.DataFrame, rule: dict) -> pd.Series:
    """National R0 (MW/yr) by group for one rule: {years: [a, b], stat: mean|max}, or
    {combine: min|max, of: [rule, ...]}."""
    if "combine" in rule:
        return pd.concat([r0(base, r) for r in rule["of"]], axis=1).agg(rule["combine"], axis=1)
    lo, hi = rule["years"]
    b = base[(base["region"] == NATIONAL) & base["year"].between(lo, hi)]
    return b.groupby("group")["mw"].agg(rule["stat"])


def regional_shares(base: pd.DataFrame, years) -> pd.DataFrame:
    lo, hi = years
    b = base[(base["region"] != NATIONAL) & base["year"].between(lo, hi)]
    tot = b.groupby("group")["mw"].transform("sum")
    s = b.groupby(["group", "region"])["mw"].sum() / b.groupby("group")["mw"].sum()
    return s.rename("share").reset_index()


def floor_basis(add: pd.DataFrame, stock: pd.DataFrame, peak_years) -> pd.DataFrame:
    """Per group and transreg: existing MW (stock) and peak annual additions over peak_years (MW/yr)."""
    lo, hi = peak_years
    a = add[add["transreg"].notna() & add["year"].between(lo, hi)]
    peak = a.groupby(["group", "transreg", "year"])["mw"].sum().groupby(["group", "transreg"]).max()
    st = stock[stock["transreg"].notna()].groupby(["group", "transreg"])["mw"].sum()
    out = pd.concat([st.rename("stock_mw"), peak.rename("peak_build_mw")], axis=1).fillna(0.0)
    return out.rename_axis(["group", "region"]).reset_index()


def regional_floor(cfg: dict, level: str, group: str, stock_mw: float, peak_mw: float) -> float:
    """floor = max(floor_min, k_stock x existing MW, k_peak x peak annual build), MW/yr."""
    f = level_params(cfg, level, group, "regional_floor")
    return max(f["floor_min_mw"], f["k_stock"] * stock_mw, f["k_peak"] * peak_mw)


def completion_rates(comp: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """MW-weighted share reaching operation, by stage and group.

    IA Executed: requests whose phase is IA Executed, queued by cohort_max_queue_year and resolved
    (operational, withdrawn or suspended). Construction: config placeholder (the file reclassifies
    completed projects as IA Executed, so it has no resolved Construction history)."""
    nt = cfg["near_term"]
    c = comp[(comp["phase"] == "IA Executed") & (comp["q_year"] <= nt["completion_cohort_max_queue_year"])
             & comp["status"].isin(["operational", "withdrawn", "suspended"])]
    # a loop over the groups, not groupby.apply(include_groups=) (pandas >= 2.2; the case-build env has 1.4.4);
    # the same Series sums as before, so the same floats
    ia = pd.DataFrame([{"group": g, "n_mw": d["mw"].sum(),
                        "rate": d.loc[d["status"] == "operational", "mw"].sum() / d["mw"].sum()}
                       for g, d in c.groupby("group")], columns=["group", "n_mw", "rate"]).set_index("group")
    rows = [{"stage": "IA Executed", "group": g, "rate": r.rate, "basis_mw": r.n_mw,
             "source": f"Queued Up, IA Executed requests queued <= {nt['completion_cohort_max_queue_year']}, resolved"}
            for g, r in ia.iterrows()]
    rows += [{"stage": "Construction", "group": g, "rate": nt["construction_completion"], "basis_mw": np.nan,
              "source": "PLACEHOLDER (config near_term.construction_completion)"} for g in ia.index]
    return pd.DataFrame(rows)


def cod_delay(comp: pd.DataFrame, cfg: dict) -> pd.Series:
    """Median years from IA execution to operation, operational requests queued from
    delay_cohort_min_queue_year on, by group. (Proposed CODs in Queued Up are revised as projects
    progress, so proposed-vs-actual shows no slip and can't be used.)"""
    c = comp[(comp["status"] == "operational") & comp["on_year"].notna() & comp["ia_year"].notna()
             & (comp["q_year"] >= cfg["near_term"]["delay_cohort_min_queue_year"])]
    return (c["on_year"] - c["ia_year"]).groupby(c["group"]).median().clip(lower=0).rename("ia_to_cod_years")


def near_term(comp: pd.DataFrame, rates: pd.DataFrame, delay: pd.Series, cfg: dict) -> pd.DataFrame:
    """Expected MW coming online per year (first..last near-term year) by group and region.

    COD = max(proposed year, IA year + median IA-to-COD years) for IA-executed requests, the proposed
    year for those under construction. Requests whose COD has already passed (overdue) are spread
    evenly over the near-term years (near_term.overdue_rule)."""
    nt = cfg["near_term"]
    years = list(range(nt["first_year"], nt["last_year"] + 1))
    a = comp[(comp["status"] == "active") & comp["phase"].isin(nt["stages"])].copy()
    rate = rates.set_index(["stage", "group"])["rate"]
    a["exp_mw"] = a["mw"] * [rate.get((s, g), np.nan) for s, g in zip(a["phase"], a["group"])]
    via_ia = a["ia_year"] + a["group"].map(delay).fillna(0)
    a["cod"] = np.where(a["phase"] == "IA Executed", np.fmax(a["prop_year"], via_ia), a["prop_year"])
    a["overdue"] = a["cod"].isna() | (a["cod"] < nt["first_year"])
    a = a[a["overdue"] | (a["cod"] <= nt["last_year"])]
    on_time = a[~a["overdue"]].assign(year=lambda d: d["cod"].astype(int))
    if nt.get("overdue_rule", "spread_even") != "spread_even":
        raise ValueError(f"unknown near_term.overdue_rule {nt['overdue_rule']!r}")
    late = a[a["overdue"]]
    spread = pd.concat([late.assign(year=y, exp_mw=late["exp_mw"] / len(years)) for y in years])
    a = pd.concat([on_time, spread], ignore_index=True)
    reg = a[a["transreg"].notna()].groupby(["group", "transreg", "year"])["exp_mw"].sum().reset_index()
    reg = reg.rename(columns={"transreg": "region", "exp_mw": "mw"})
    nat = a.groupby(["group", "year"])["exp_mw"].sum().reset_index().rename(columns={"exp_mw": "mw"})
    nat["region"] = NATIONAL
    out = pd.concat([nat, reg], ignore_index=True)
    idx = pd.MultiIndex.from_product([sorted(out["group"].unique()), sorted(out["region"].unique()), years],
                                     names=["group", "region", "year"])
    return out.set_index(["group", "region", "year"])["mw"].reindex(idx, fill_value=0.0).reset_index()


def overdue_summary(comp: pd.DataFrame, delay: pd.Series, cfg: dict) -> pd.Series:
    """Active IA-executed / under-construction MW whose COD has already passed, by group."""
    nt = cfg["near_term"]
    a = comp[(comp["status"] == "active") & comp["phase"].isin(nt["stages"])]
    via_ia = a["ia_year"] + a["group"].map(delay).fillna(0)
    cod = np.where(a["phase"] == "IA Executed", np.fmax(a["prop_year"], via_ia), a["prop_year"])
    late = pd.isna(cod) | (cod < nt["first_year"])
    return a.loc[late].groupby("group")["mw"].sum()


def check_r0_order(r0s: pd.DataFrame):
    """low <= central <= high for every group (columns low, central, high)."""
    bad = r0s[(r0s["low"] > r0s["central"] + 1e-9) | (r0s["central"] > r0s["high"] + 1e-9)]
    if len(bad):
        raise ValueError(f"R0 rules out of order (need low <= central <= high): {bad.round(1).to_dict('index')}")


def ipm_windows(cfg: dict) -> dict:
    """Build window (first, last calendar year) of each IPM run year: consecutive spans from
    ipm.build_window_first_year (the windows the Table 4-13 bounds imply, not Table 2-1)."""
    ip = cfg["ipm"]
    start, out = int(ip["build_window_first_year"]), {}
    for ry, n in sorted((int(k), v) for k, v in ip["run_year_span"].items()):
        if not n:
            raise ValueError(f"level high_ipm needs ipm.run_year_span for run year {ry}.")
        out[ry] = (start, start + int(n) - 1)
        start += int(n)
    return out


def ipm_r0(cfg: dict, group: str) -> dict | None:
    """IPM Table 4-13 Step 1 bound per build year (MW/yr), keyed by run year (None if IPM has no
    row for the group)."""
    ip = cfg["ipm"]
    if group not in ip["step1_mw"]:
        return None
    win = ipm_windows(cfg)
    out = {}
    for y, mw in ip["step1_mw"][group].items():
        sc = ip["scalars_45x"][y] if (ip.get("apply_45x_scalars") and group != "gas") else 1.0
        first, last = win[int(y)]
        out[int(y)] = mw * sc / (last - first + 1)
    return out


def ipm_rate_for_year(cfg: dict, ipm: dict, y: int, growth: float) -> float:
    """IPM rate for calendar year y: the run year whose build window holds y; earlier years take
    the first; later years grow from the last (IPM has no adders after 2035, so the ipm2025 bands
    are free there anyway)."""
    ry = {k: v for k, v in ipm_windows(cfg).items() if k in ipm}
    for r, (first, last) in sorted(ry.items()):
        if first <= y <= last:
            return ipm[r]
    first_ry = min(ry)
    if y < ry[first_ry][0]:
        return ipm[first_ry]
    last_ry = max(ry)
    return ipm[last_ry] * (1 + growth) ** (y - ry[last_ry][1])


def level_params(cfg: dict, level: str, group: str, key: str):
    lv = cfg["levels"][level]
    return (lv.get(key) or {}).get(group, cfg[key][group])


def growth_path(cfg: dict, growth_set: str, group: str) -> dict | None:
    """growth_paths[growth_set][group] as {year: growth}; a group name as value follows that group's path (storage:
    solar). None when the set or group has no path (the scalar `growth` applies)."""
    gp = (cfg.get("growth_paths") or {}).get(growth_set) or {}
    v, seen = gp.get(group), set()
    while isinstance(v, str) and v not in seen:
        seen.add(v)
        v = gp.get(v)
    return {int(k): float(x) for k, x in v.items()} if isinstance(v, dict) else None


def path_value(path: dict, year: int) -> float:
    """The path's growth in `year`; years after its last key take the last value, earlier ones the first."""
    ks = sorted(path)
    return path[max([k for k in ks if k <= year] or [ks[0]])]


def ramp_growth(path: dict) -> float:
    """One growth rate for the ramp bound between model periods (study_modules.build_rate br_growth): the geometric
    mean of the path."""
    v = [path[k] for k in sorted(path)]
    return float(np.prod([1 + x for x in v]) ** (1 / len(v)) - 1)


def deliverability_path(cfg: dict, path: str | None, group: str) -> dict | None:
    """§77: national deliverability ceiling {year: MW/yr} for base_year + 1 .. horizon: actual_gw[group] (base year)
    compounded by cagr[path][group] (years in (previous key, key] take that key's rate; later years the last one).
    None when the path or the group has none (no ceiling)."""
    d = cfg.get("deliverability") or {}
    base = (d.get("actual_gw") or {}).get(group)
    g = ((d.get("cagr") or {}).get(path) or {}).get(group) if path else None
    if base is None or not g:
        return None
    ends = sorted(int(k) for k in g)
    rate = {int(k): float(v) for k, v in g.items()}
    out, v = {}, float(base) * 1e3
    for y in range(int(d["base_year"]) + 1, int(cfg["horizon_last_year"]) + 1):
        v *= 1 + rate[min([e for e in ends if e >= y] or [ends[-1]])]
        out[y] = v
    return out


def level_deliverability(cfg: dict, level: str) -> str | None:
    """The deliverability path of a level (deliverability.level_path; null = none). No `deliverability` block: none."""
    d = cfg.get("deliverability")
    if not d:
        return None
    lp = d.get("level_path") or {}
    if level not in lp:
        raise ValueError(f"deliverability.level_path has no entry for build-rate level {level!r} (null = no ceiling)")
    return lp[level]


def module_floor(cfg: dict, group: str) -> dict:
    """§77: {year: MW/yr} floor on the module's R (deliverability.module_floor[group]: years, path); {} if none."""
    f = ((cfg.get("deliverability") or {}).get("module_floor") or {}).get(group)
    if not f:
        return {}
    path = deliverability_path(cfg, f["path"], group) or {}
    return {int(y): path[int(y)] for y in f["years"] if int(y) in path}


def apply_deliverability(cfg: dict, level: str, t: pd.DataFrame) -> pd.DataFrame:
    """§77: the module table with the deliverability layer. Adds module_ceiling_mw_per_yr (the module's own),
    deliverability_mw_per_yr (national rows), deliverability_factor = min(1, D / national module ceiling) by group and
    year, deliverability_binds; ceiling_mw_per_yr = module ceiling x factor (national: min(module, D); regional: scaled
    by the national factor). r_data_mw_per_yr is unchanged."""
    t = t.copy()
    t["module_ceiling_mw_per_yr"] = t["ceiling_mw_per_yr"]
    t["deliverability_mw_per_yr"] = np.nan
    t["deliverability_factor"] = 1.0
    name = level_deliverability(cfg, level)
    for g in t["group"].unique():
        path = deliverability_path(cfg, name, g) if name else None
        if not path:
            continue
        nat = (t["group"] == g) & (t["region"] == NATIONAL)
        n = t[nat].set_index("year")
        dv = pd.Series(path).reindex(n.index)
        fac = np.minimum(1.0, dv / n["ceiling_mw_per_yr"].where(n["ceiling_mw_per_yr"] > 0)).fillna(1.0)
        gm = t["group"] == g
        t.loc[nat, "deliverability_mw_per_yr"] = t.loc[nat, "year"].map(dv).values
        t.loc[gm, "deliverability_factor"] = t.loc[gm, "year"].map(fac).fillna(1.0).values
    t["ceiling_mw_per_yr"] = t["module_ceiling_mw_per_yr"] * t["deliverability_factor"]
    t["deliverability_binds"] = t["deliverability_factor"] < 1.0 - 1e-12
    return t


def rate_table(cfg: dict, level: str, base: pd.DataFrame, near: pd.DataFrame, shares: pd.DataFrame,
               basis: pd.DataFrame | None = None) -> pd.DataFrame:
    """R_data (national) and regional ceilings, MW/yr, for every year near_term.first_year..horizon.
    basis: floor_basis() (existing MW and peak build per transreg); missing regions count as 0."""
    lv = cfg["levels"][level]
    nt = cfg["near_term"]
    years = range(nt["first_year"], cfg["horizon_last_year"] + 1)
    rows = []
    for g in cfg["groups"]:
        path = growth_path(cfg, lv["growth"], g)
        growth = ramp_growth(path) if path else cfg["growth"][lv["growth"]][g]
        if lv["r0"] == "ipm":
            ipm = ipm_r0(cfg, g)
            base_r0 = (ipm_rate_for_year(cfg, ipm, nt["first_year"], growth) if ipm
                       else float(r0(base, cfg["r0_rule"]["high"]).get(g, 0.0)))
        else:
            ipm, base_r0 = None, float(r0(base, cfg["r0_rule"][lv["r0"]]).get(g, 0.0))
        q = near[(near["group"] == g) & (near["region"] == NATIONAL)].set_index("year")["mw"]
        floor = module_floor(cfg, g)
        r = {}
        for y in years:
            if y <= nt["last_year"]:
                r[y] = max(float(q.get(y, 0.0)), base_r0)
            elif path:                                    # §76: anchored year-by-year growth path
                r[y] = r[y - 1] * (1 + path_value(path, y))
            else:
                r[y] = r[nt["last_year"]] * (1 + growth) ** (y - nt["last_year"])
            if ipm:   # IPM's shape: R = Step 1 per build year (implied build windows)
                r[y] = ipm_rate_for_year(cfg, ipm, y, growth)
            elif y in floor:   # §77: queue-visibility floor (storage 2029-30); later years compound from it
                r[y] = max(r[y], floor[y])
        top = tiers(cfg, g, level_tier_set(cfg, level))["upto"].max()
        mult = level_params(cfg, level, g, "regional_mult")
        sh = shares[shares["group"] == g].set_index("region")["share"]
        bg = (basis[basis["group"] == g].set_index("region") if basis is not None
              else pd.DataFrame(columns=["stock_mw", "peak_build_mw"]))
        floors = {reg: regional_floor(cfg, level, g, float(bg["stock_mw"].get(reg, 0.0)),
                                      float(bg["peak_build_mw"].get(reg, 0.0))) for reg in sh.index}
        for y in years:
            rows.append({"group": g, "region": NATIONAL, "year": y, "r_data_mw_per_yr": r[y],
                         "ceiling_mw_per_yr": top * r[y], "r0_mw_per_yr": base_r0, "growth": growth,
                         "floor_applied": False})
            for reg, s in sh.items():
                floor = floors[reg]
                cap = s * mult * top * r[y]
                rows.append({"group": g, "region": reg, "year": y, "r_data_mw_per_yr": s * r[y],
                             "ceiling_mw_per_yr": max(cap, floor), "r0_mw_per_yr": s * base_r0,
                             "growth": growth, "floor_applied": cap < floor})
    return pd.DataFrame(rows)


def level_tier_set(cfg: dict, level: str | None) -> str:
    return (cfg["levels"].get(level) or {}).get("tier_set", cfg["tier_set"]) if level else cfg["tier_set"]


def tiers(cfg: dict, group: str, tier_set: str | None = None) -> pd.DataFrame:
    """Bands for a group: tier, lower/upper edge (x R), width (x R), adder (fraction of capex),
    adders_last_year (last calendar build year the adders apply to; NaN = always)."""
    ts = tier_set or cfg["tier_set"]
    gt = cfg["gas_tiers"]
    override = ((cfg.get("group_tier_overrides") or {}).get(ts) or {}).get(group)
    t = pd.DataFrame(override or (gt.get(ts, gt["default"]) if group == "gas" else cfg["tier_sets"][ts]))
    t["lower"] = t["upto"].shift(fill_value=0.0)
    t["width"] = t["upto"] - t["lower"]
    t["tier"] = [f"t{i + 1}" for i in range(len(t))]
    t["adders_last_year"] = (cfg.get("tier_adders_last_year") or {}).get(ts, np.nan)
    if (t["width"] <= 0).any() or not t["adder"].is_monotonic_increasing:
        raise ValueError(f"tiers for {group} must have increasing edges and non-decreasing adders")
    return t[["tier", "lower", "upto", "width", "adder", "adders_last_year"]]


# --------------------------------------------------------------------------------------------- reform: benchmark
def queue_metrics(comp: pd.DataFrame, cfg: dict, group: str, rb: dict | None = None) -> pd.DataFrame:
    """Per unit of analysis (reform_benchmark.unit: state | transreg) for one group, from LBNL Queued Up:
    completion = MW share reaching operation among resolved requests (operational, withdrawn, suspended; every phase)
    queued in completion_cohort_queue_years; duration = median years from request (q_year) to operation (on_year) of
    requests online in duration_on_years (each at least min_duration_years). A unit with too little data (resolved
    MW below min_basis_mw; fewer than min_operational requests online in the window) takes the national value
    (own_completion / own_duration False) and is left out of the benchmark."""
    rb = rb or cfg["reform_benchmark"]
    unit = rb["unit"]
    d = comp[comp["group"] == group]
    c0, c1 = rb["completion_cohort_queue_years"]
    res = d[d["status"].isin(["operational", "withdrawn", "suspended"]) & d["q_year"].between(c0, c1)]
    lo, hi = rb["duration_on_years"]
    op = d[(d["status"] == "operational") & d["on_year"].between(lo, hi) & d["q_year"].notna()]
    dur = (op["on_year"] - op["q_year"]).clip(lower=rb["min_duration_years"])
    nat_c = res.loc[res["status"] == "operational", "mw"].sum() / res["mw"].sum()
    nat_d = float(dur.median())
    rows = []
    units = sorted(u for u in set(res[unit].dropna()) | set(op[unit].dropna()) | set(d[unit].dropna())
                   if str(u) not in ("", "NAN", "NONE"))
    for u in units:
        x, o = res[res[unit] == u], dur[op[unit] == u]
        basis = float(x["mw"].sum())
        own_c = basis >= rb["min_basis_mw"]
        own_d = len(o) >= rb["min_operational"]
        rows.append({"group": group, "unit": u, "resolved_mw": basis, "n_operational": len(o),
                     "completion": float(x.loc[x["status"] == "operational", "mw"].sum() / basis) if own_c else nat_c,
                     "duration_years": float(o.median()) if own_d else nat_d, "own_completion": own_c,
                     "own_duration": own_d})
    rows.append({"group": group, "unit": NATIONAL, "resolved_mw": float(res["mw"].sum()), "n_operational": len(op),
                 "completion": nat_c, "duration_years": nat_d, "own_completion": True, "own_duration": True})
    return pd.DataFrame(rows)


def benchmark(values, higher_is_better: bool, trim_share: float, top_share: float) -> float:
    """Best-performer benchmark, round 1's Implied Rate rule (cap_derivation_methodology.trimmed_top_quartile_mean):
    drop round(n x trim_share) units at each end, then the mean of the best round(m x top_share) of the m left
    (Python round; at least one)."""
    v = sorted(float(x) for x in values if pd.notna(x))
    n = len(v)
    if not n:
        raise ValueError("reform benchmark: no unit has enough queue data (lower min_basis_mw / min_operational)")
    k = round(n * trim_share)
    kept = v[k:n - k] if k > 0 else v
    kept = sorted(kept, reverse=higher_is_better)
    m = max(1, round(len(kept) * top_share))
    return float(np.mean(kept[:m]))


def reform_benchmark(comp: pd.DataFrame, cfg: dict, rb: dict | None = None) -> pd.DataFrame:
    """Per group and unit: completion, duration, the benchmark (units with their own data), and the reformed values:
    completion raised to the benchmark, duration cut to it; better units keep theirs. A group in `proxy` takes another
    group's unit values (e.g. storage -> solar: too little resolved storage history)."""
    rb = rb or cfg["reform_benchmark"]
    out = []
    for g in rb["groups"]:
        src = (rb.get("proxy") or {}).get(g, g)
        q = queue_metrics(comp, cfg, src, rb).assign(group=g, metrics_from=src)
        reg = q[q["unit"] != NATIONAL]
        bc = benchmark(reg.loc[reg["own_completion"], "completion"], True, rb["trim_share"], rb["top_share"])
        bd = benchmark(reg.loc[reg["own_duration"], "duration_years"], False, rb["trim_share"], rb["top_share"])
        q = q.assign(benchmark_completion=bc, benchmark_duration_years=bd)
        q["completion_reform"] = q["completion"].clip(lower=bc)
        q["duration_reform_years"] = q["duration_years"].clip(upper=bd)
        q["raised_completion"] = q["completion"] < bc - 1e-12
        q["cut_duration"] = q["duration_years"] > bd + 1e-12
        out.append(q)
    return pd.concat(out, ignore_index=True)


def reform_uplift(comp: pd.DataFrame, cfg: dict, rb: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(benchmark table by unit, reform increment by group and transreg).

    Implied rate of the active queue (round 1's Implied Rate, on the queue parameters): I = sum over active requests
    of MW x completion / duration, with each request's unit's values (national ones where the unit lacks data);
    I' the same with the reformed values. A transreg's increment delta_r = (I'_r - I_r) / I, its requests' gain as
    a share of the national implied rate (requests outside any transreg count in I and in the national increment).
    reform_bp's R: regional = (share_r + delta_r) x R_central, national = R_central x I' / I."""
    rb = rb or cfg["reform_benchmark"]
    bm = reform_benchmark(comp, cfg, rb)
    unit = rb["unit"]
    rows = []
    for g in rb["groups"]:
        b = bm[bm["group"] == g].set_index("unit")
        nat = b.loc[NATIONAL]
        act = comp[(comp["group"] == g) & (comp["status"] == "active")]
        u = act[unit].where(act[unit].isin(b.index), NATIONAL)
        i0 = act["mw"] * u.map(b["completion"]) / u.map(b["duration_years"])
        i1 = act["mw"] * u.map(b["completion_reform"]) / u.map(b["duration_reform_years"])
        tot0, tot1 = float(i0.sum()), float(i1.sum())
        for r, idx in act.groupby("transreg").groups.items():
            rows.append({"group": g, "region": r, "active_mw": float(act.loc[idx, "mw"].sum()),
                         "implied_mw_per_yr": float(i0[idx].sum()), "implied_reform_mw_per_yr": float(i1[idx].sum()),
                         "delta": float((i1[idx].sum() - i0[idx].sum()) / tot0)})
        rows.append({"group": g, "region": NATIONAL, "active_mw": float(act["mw"].sum()), "implied_mw_per_yr": tot0,
                     "implied_reform_mw_per_yr": tot1, "delta": tot1 / tot0 - 1.0})
    return bm, pd.DataFrame(rows)


def derived_rate_table(cfg: dict, level: str, tables: dict, uplift: pd.DataFrame | None,
                       basis: pd.DataFrame | None = None) -> pd.DataFrame:
    """Rate tables of levels defined on others (cfg levels[level]):
    benchmark_of: L   R of level L with each region's R x its uplift (reform_uplift) from reform_from_year
                      (regional R = share x R_L x uplift; national R = the sum of the regions'), ceilings recomputed
                      (regional: max(share x uplift x mult x top x R_L, floor); national: top x R);
    max_of: [A, B]    per region and year the larger R of A and B (national = the sum of the regions'), the larger
                      regional ceiling; national ceiling = top x R."""
    lv = cfg["levels"][level]
    if "benchmark_of" in lv:
        t = tables[lv["benchmark_of"]].copy()
        if uplift is None:
            raise ValueError(f"level {level} needs reform_uplift (LBNL queue data)")
        dl = uplift.set_index(["group", "region"])["delta"]
        start = int(lv.get("reform_from_year", 0))
        out = []
        for g, d in t.groupby("group", sort=False):
            top = tiers(cfg, g, level_tier_set(cfg, level))["upto"].max()
            mult = level_params(cfg, level, g, "regional_mult")
            d = d.copy()
            reg = (d["region"] != NATIONAL).values
            on = (d["year"] >= start).values
            r_nat = d["year"].map(d.loc[~reg].set_index("year")["r_data_mw_per_yr"])     # R of the base level
            add = np.array([dl.get((g, r), 0.0) for r in d["region"]]) * r_nat.values
            d.loc[reg & on, "r_data_mw_per_yr"] = (d["r_data_mw_per_yr"] + add)[reg & on]
            if basis is not None:                         # the level's own floor terms (e.g. reform_bp_siting)
                bg = basis[basis["group"] == g].set_index("region")
                fl = {r: regional_floor(cfg, level, g, float(bg["stock_mw"].get(r, 0.0)),
                                        float(bg["peak_build_mw"].get(r, 0.0))) for r in d.loc[reg, "region"].unique()}
                floor = pd.Series([fl.get(r, 0.0) for r in d["region"]], index=d.index)
            else:                                         # without the basis: the base level's floor
                floor = d["ceiling_mw_per_yr"].where(d["floor_applied"], 0.0)
            cap = d["r_data_mw_per_yr"] * mult * top
            d.loc[reg, "ceiling_mw_per_yr"] = np.maximum(cap[reg], floor[reg])
            d.loc[reg, "floor_applied"] = (cap < floor)[reg]
            f = 1.0 + float(dl.get((g, NATIONAL), 0.0))
            d.loc[~reg & on, "r_data_mw_per_yr"] = d.loc[~reg & on, "r_data_mw_per_yr"] * f
            d.loc[~reg, "ceiling_mw_per_yr"] = top * d.loc[~reg, "r_data_mw_per_yr"]
            out.append(d)
        return pd.concat(out, ignore_index=True)
    if "max_of" in lv:
        a, b = (tables[x].set_index(["group", "region", "year"]) for x in lv["max_of"])
        b = b.reindex(a.index)
        t = a.copy()
        t["r_data_mw_per_yr"] = np.maximum(a["r_data_mw_per_yr"], b["r_data_mw_per_yr"])
        t["ceiling_mw_per_yr"] = np.maximum(a["ceiling_mw_per_yr"], b["ceiling_mw_per_yr"])
        t["floor_applied"] = a["floor_applied"] & b["floor_applied"]
        t = t.reset_index()
        for g in t["group"].unique():
            top = tiers(cfg, g, level_tier_set(cfg, level))["upto"].max()
            regm = (t["group"] == g) & (t["region"] != NATIONAL)
            natm = (t["group"] == g) & (t["region"] == NATIONAL)
            # national: each level's national R plus the regions where the other is higher (the regional maxima,
            # keeping each level's part of R outside any transreg); the larger of the two
            ka = a.reset_index()
            kb = b.reset_index()
            ra = ka[(ka["group"] == g) & (ka["region"] != NATIONAL)].set_index(["region", "year"])["r_data_mw_per_yr"]
            rbv = kb[(kb["group"] == g) & (kb["region"] != NATIONAL)].set_index(["region", "year"])["r_data_mw_per_yr"]
            mx = np.maximum(ra, rbv)
            na = ka[(ka["group"] == g) & (ka["region"] == NATIONAL)].set_index("year")["r_data_mw_per_yr"]
            nb = kb[(kb["group"] == g) & (kb["region"] == NATIONAL)].set_index("year")["r_data_mw_per_yr"]
            via_a = na + (mx - ra).groupby("year").sum().reindex(na.index).fillna(0)
            via_b = nb + (mx - rbv).groupby("year").sum().reindex(nb.index).fillna(0)
            t.loc[natm, "r_data_mw_per_yr"] = t.loc[natm, "year"].map(np.maximum(via_a, via_b)).values
            t.loc[natm, "ceiling_mw_per_yr"] = top * t.loc[natm, "r_data_mw_per_yr"]
        return t
    raise ValueError(f"level {level} is not a derived level")


def rate_tables(cfg: dict, levels, base, near, shares, basis=None, uplift=None) -> dict:
    """Rate tables of the given levels: data levels by rate_table, derived ones (benchmark_of / max_of) from their
    dependencies' module tables, resolved recursively; then each level's deliverability layer (§77)."""
    out = {}

    def get(lv):
        if lv not in out:
            spec = cfg["levels"][lv]
            deps = [d for d in [spec.get("benchmark_of")] + list(spec.get("max_of", [])) if d]
            if deps:
                for d in deps:
                    get(d)
                out[lv] = derived_rate_table(cfg, lv, out, uplift, basis)
            else:
                out[lv] = rate_table(cfg, lv, base, near, shares, basis)
        return out[lv]

    return {lv: apply_deliverability(cfg, lv, get(lv)) for lv in levels}
