"""Build-rate tables: base rates, R0, queue-based near-term rates, growth, regional ceilings, tiers.

R_data[G, national, y] (MW/yr) by level:
    y <= near_term.last_year:  max(queue-based expected additions in y, R0)
    later:                     R_data[last near-term year] x (1 + growth_G) ** (y - last near-term year)
Regional ceiling[G, r, y] = max(share_r x regional_mult_G x top band x R_data[G, y], regional_floor_G).
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
    """National R0 (MW/yr) by group for one rule {years: [a, b], stat: mean|max}."""
    lo, hi = rule["years"]
    b = base[(base["region"] == NATIONAL) & base["year"].between(lo, hi)]
    return b.groupby("group")["mw"].agg(rule["stat"])


def regional_shares(base: pd.DataFrame, years) -> pd.DataFrame:
    lo, hi = years
    b = base[(base["region"] != NATIONAL) & base["year"].between(lo, hi)]
    tot = b.groupby("group")["mw"].transform("sum")
    s = b.groupby(["group", "region"])["mw"].sum() / b.groupby("group")["mw"].sum()
    return s.rename("share").reset_index()


def completion_rates(comp: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """MW-weighted share reaching operation, by stage and group.

    IA Executed: requests whose phase is IA Executed, queued by cohort_max_queue_year and resolved
    (operational, withdrawn or suspended). Construction: config placeholder (the file reclassifies
    completed projects as IA Executed, so it has no resolved Construction history)."""
    nt = cfg["near_term"]
    c = comp[(comp["phase"] == "IA Executed") & (comp["q_year"] <= nt["completion_cohort_max_queue_year"])
             & comp["status"].isin(["operational", "withdrawn", "suspended"])]
    ia = c.groupby("group").apply(lambda d: pd.Series({
        "n_mw": d["mw"].sum(), "rate": d.loc[d["status"] == "operational", "mw"].sum() / d["mw"].sum()}),
        include_groups=False)
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


def ipm_r0(cfg: dict, group: str) -> dict | None:
    """IPM Table 4-13 Step 1 bound per calendar year, by run year (None if IPM has no row)."""
    ip = cfg["ipm"]
    if group not in ip["step1_mw"]:
        return None
    span = ip["run_year_span"]
    missing = [y for y, v in span.items() if not v]
    if missing:
        raise ValueError(
            f"level high_ipm needs ipm.run_year_span for run years {missing} (IPM Platform v6 Chapter 2 "
            "run-year mapping), which is not in the inputs. Fill it in config.yaml before using high_ipm.")
    out = {}
    for y, mw in ip["step1_mw"][group].items():
        sc = ip["scalars_45x"][y] if (ip.get("apply_45x_scalars") and group != "gas") else 1.0
        out[int(y)] = mw * sc / span[y]
    return out


def level_params(cfg: dict, level: str, group: str, key: str):
    lv = cfg["levels"][level]
    return (lv.get(key) or {}).get(group, cfg[key][group])


def rate_table(cfg: dict, level: str, base: pd.DataFrame, near: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    """R_data (national) and regional ceilings, MW/yr, for every year near_term.first_year..horizon."""
    lv = cfg["levels"][level]
    nt = cfg["near_term"]
    years = range(nt["first_year"], cfg["horizon_last_year"] + 1)
    rows = []
    for g in cfg["groups"]:
        growth = cfg["growth"][lv["growth"]][g]
        if lv["r0"] == "ipm":
            ipm = ipm_r0(cfg, g)
            base_r0 = float(r0(base, cfg["r0_rule"]["high"]).get(g, 0.0))
        else:
            ipm, base_r0 = None, float(r0(base, cfg["r0_rule"][lv["r0"]]).get(g, 0.0))
        q = near[(near["group"] == g) & (near["region"] == NATIONAL)].set_index("year")["mw"]
        r = {}
        for y in years:
            if y <= nt["last_year"]:
                r[y] = max(float(q.get(y, 0.0)), base_r0)
            else:
                r[y] = r[nt["last_year"]] * (1 + growth) ** (y - nt["last_year"])
            if ipm:   # IPM-equivalent: rate of the first run year at or after y; growth after the last
                later = [ry for ry in sorted(ipm) if ry >= y]
                ipm_y = ipm[later[0]] if later else ipm[max(ipm)] * (1 + growth) ** (y - max(ipm))
                r[y] = max(r[y], ipm_y)
        top = tiers(cfg, g)["upto"].max()
        mult = level_params(cfg, level, g, "regional_mult")
        floor = level_params(cfg, level, g, "regional_floor_mw")
        sh = shares[shares["group"] == g].set_index("region")["share"]
        for y in years:
            rows.append({"group": g, "region": NATIONAL, "year": y, "r_data_mw_per_yr": r[y],
                         "ceiling_mw_per_yr": top * r[y], "r0_mw_per_yr": base_r0, "growth": growth,
                         "floor_applied": False})
            for reg, s in sh.items():
                cap = s * mult * top * r[y]
                rows.append({"group": g, "region": reg, "year": y, "r_data_mw_per_yr": s * r[y],
                             "ceiling_mw_per_yr": max(cap, floor), "r0_mw_per_yr": s * base_r0,
                             "growth": growth, "floor_applied": cap < floor})
    return pd.DataFrame(rows)


def tiers(cfg: dict, group: str) -> pd.DataFrame:
    """Bands for a group: tier, lower/upper edge (x R), width (x R), adder (fraction of capex)."""
    t = pd.DataFrame(cfg["gas_tiers"] if group == "gas" else cfg["tier_sets"][cfg["tier_set"]])
    t["lower"] = t["upto"].shift(fill_value=0.0)
    t["width"] = t["upto"] - t["lower"]
    t["tier"] = [f"t{i + 1}" for i in range(len(t))]
    if (t["width"] <= 0).any() or not t["adder"].is_monotonic_increasing:
        raise ValueError(f"tiers for {group} must have increasing edges and non-decreasing adders")
    return t[["tier", "lower", "upto", "width", "adder"]]
