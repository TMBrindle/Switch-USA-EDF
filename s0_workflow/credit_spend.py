"""Post-run tax-credit spend by vintage for S-set and other S0 cases (CHANGES §75; settings
s0_workflow/specs/credits/credit_spend.yaml).

Per stage of a chain (or the single stage of a single-year case) and its model period p, every wind/solar (and other
credited) generator's energy (dispatch_gen_annual_summary.csv, MWh per typical year) is split over its vintages by
capacity (BuildGen.csv rows with build year <= p and build year + gen_max_age > p). Each vintage's credit goes to:
  existing_pipeline   vintage in the stage's base gen_build_predetermined.csv (already built, or EIA-860M pipeline);
                      not in the model: energy x value_per_mwh x ptc_share while within ptc_years of its in-service
                      year, if that year is eligible (from first_in_service; current law up to current_last_in_service,
                      reinstated cases with no upper bound)
  paid_not_optimised  model-built vintage whose build stage had no credit for its project: the model's credit
                      (tax_credit_value.csv) in later stages, and energy x value in reinstated years the model did not
                      credit (the 2028 stage of a reinstated chain)
  optimised_on        the model's credit on vintages whose build stage had it
A case is reinstated when any stage credits wind or solar (gen_tax_credits.csv).

Storage investment credit (§136; storage_credit obbba, s0_workflow/tax_credits.py): per stage, the 48E ITC on the
storage built in its period, a one-time amount (dollars_one_time, overnight $ of the model's dollar year), not $/yr:
    BuildGen MW x gen_overnight_cost + BuildStorageEnergy MWh x gen_storage_energy_overnight_cost (both after the
    credit, gen_build_costs.csv, build year = the period) x f / (1 - f),   f = itc_rate x phase
with itc_rate and phase from the stage's investment_credit_report.csv (no report = no investment credit in that
stage, e.g. v3.1's flat x0.7, which sits inside the capex). Category investment_credit, tech storage.

Summary (summary()): $M/yr by category, the same three plus total as v3.1, so v3.1's wind and solar figures compare;
then wind_per_yr and solar_per_yr (the production credits by technology) and storage_itc_one_time ($M, this period's
builds).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "s0_workflow/specs/credits/credit_spend.yaml"
CATEGORIES = ("existing_pipeline", "paid_not_optimised", "optimised_on")


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def tech_group(gen_tech: str, cfg: dict) -> str | None:
    t = _norm(gen_tech)
    for grp, keys in cfg["techs"].items():
        if any(_norm(k) in t for k in keys):
            return grp
    return None


def _read(p: Path, **kw) -> pd.DataFrame | None:
    return pd.read_csv(p, na_values=["."], **kw) if Path(p).exists() else None


def stage_tables(inputs: Path, outputs: Path) -> dict:
    inputs, outputs = Path(inputs), Path(outputs)
    out = {"periods": sorted(int(x) for x in pd.read_csv(inputs / "periods.csv").INVESTMENT_PERIOD),
           "gen_info": pd.read_csv(inputs / "gen_info.csv", na_values=["."]).set_index("GENERATION_PROJECT"),
           "base_pre": _read(inputs / "gen_build_predetermined.csv"),
           "credits_in": _read(inputs / "gen_tax_credits.csv"),
           "build": _read(outputs / "BuildGen.csv"),
           "energy": _read(outputs / "dispatch_gen_annual_summary.csv"),
           "model_credit": _read(outputs / "tax_credit_value.csv")}
    if out["build"] is None or out["energy"] is None:
        raise FileNotFoundError(f"credit_spend: {outputs} needs BuildGen.csv and dispatch_gen_annual_summary.csv")
    return out


def spend(stages: list[dict], cfg: dict | None = None, case: str = "") -> pd.DataFrame:
    """Long table: case, period, category, tech, vintage, mwh_per_yr, dollars_per_yr. stages: [{"inputs", "outputs"}]
    in chain order."""
    cfg = cfg or yaml.safe_load(open(CONFIG))
    tabs = [stage_tables(s["inputs"], s["outputs"]) for s in stages]
    # which projects carried a credit in which stage (keyed by the stage's first period)
    credited_in = {}
    reinstated = False
    for t in tabs:
        ci = t["credits_in"]
        names = set() if ci is None else set(ci.loc[ci["gen_ptc_value_per_mwh"] > 0, "GENERATION_PROJECT"])
        credited_in[t["periods"][0]] = names
        if any(tech_group(t["gen_info"].at[g, "gen_tech"], cfg) for g in names if g in t["gen_info"].index):
            reinstated = True
    stage_of = {}
    for t in tabs:
        for p in t["periods"]:
            stage_of[p] = t["periods"][0]
    ep = cfg["existing_pipeline"]
    last_ok = None if reinstated else int(ep["current_last_in_service"])
    rows = []
    for t in tabs:
        gi = t["gen_info"]
        base = set() if t["base_pre"] is None else set(zip(t["base_pre"]["GENERATION_PROJECT"],
                                                           t["base_pre"]["build_year"].astype(int)))
        b = t["build"].rename(columns={"GEN_BLD_YRS_1": "g", "GEN_BLD_YRS_2": "y", "BuildGen": "mw"})
        b = b[b["mw"] > 1e-9].astype({"y": int})
        e = t["energy"].set_index(["generation_project", "period"])["Energy_GWh_typical_yr"] * 1000
        mc = (t["model_credit"].set_index(["GENERATION_PROJECT", "PERIOD"])["tax_credit_value_dollars"]
              if t["model_credit"] is not None else pd.Series(dtype=float))
        for p in t["periods"]:
            for g, d in b.groupby("g"):
                if g not in gi.index:
                    continue
                grp = tech_group(gi.at[g, "gen_tech"], cfg)
                life = float(gi.at[g, "gen_max_age"]) if pd.notna(gi.at[g, "gen_max_age"]) else 1e9
                alive = d[(d["y"] <= p) & (d["y"] + life > p)]
                if alive.empty:
                    continue
                share = alive.set_index("y")["mw"] / alive["mw"].sum()
                energy = float(e.get((g, p), 0.0))
                credit = float(mc.get((g, p), 0.0))
                for y, sh in share.items():
                    mwh = energy * sh
                    if (g, y) in base:
                        if grp is None:
                            continue
                        rule = ep[grp]
                        ok = y >= int(rule["first_in_service"]) and (last_ok is None or y <= last_ok) \
                            and p <= y + int(cfg["ptc_years"]) - 1
                        val = float(cfg["value_per_mwh"]) * float(rule["ptc_share"]) if ok else 0.0
                        cat, dollars = "existing_pipeline", mwh * val + credit * sh
                    else:
                        built_stage = stage_of.get(y, y)
                        optimised = g in credited_in.get(built_stage, set())
                        dollars = credit * sh
                        if (not optimised and reinstated and grp is not None and credit == 0
                                and p >= int(cfg["reinstated_from"]) and p <= y + int(cfg["ptc_years"]) - 1):
                            dollars = mwh * float(cfg["value_per_mwh"])
                        cat = "optimised_on" if optimised else "paid_not_optimised"
                    if dollars:
                        rows.append({"case": case, "period": p, "category": cat, "tech": grp or gi.at[g, "gen_tech"],
                                     "vintage": int(y), "mwh_per_yr": mwh, "dollars_per_yr": dollars})
    return pd.DataFrame(rows, columns=["case", "period", "category", "tech", "vintage", "mwh_per_yr", "dollars_per_yr"])


def storage_itc(stages: list[dict], case: str = "") -> pd.DataFrame:
    """One row per stage period with a storage investment credit: dollars_one_time (module docstring)."""
    rows = []
    for st in stages:
        i, o = Path(st["inputs"]), Path(st["outputs"])
        rep = _read(i / "investment_credit_report.csv")
        if rep is None or rep.empty:
            continue
        costs = _read(i / "gen_build_costs.csv")
        costs = costs.rename(columns={"BUILD_YEAR": "build_year"})
        b = _read(o / "BuildGen.csv")
        b = b.rename(columns=dict(zip(b.columns[:3], ["g", "y", "mw"])))
        se = _read(o / "BuildStorageEnergy.csv")
        if se is not None:
            se = se.rename(columns=dict(zip(se.columns[:3], ["g", "y", "mwh"])))
        gi = pd.read_csv(i / "gen_info.csv", na_values=["."]).set_index("GENERATION_PROJECT")
        for p, r in rep.groupby("PERIOD"):
            p = int(p)
            techs = {str(t).lower() for t in r.technology}
            f = float(r.itc_rate.iat[0]) * float(r.phase.iat[0])
            if f <= 0 or f >= 1:
                continue
            c = costs[(costs.build_year.astype(int) == p)]
            c = c[c.GENERATION_PROJECT.map(lambda g: str(gi.gen_tech.get(g, "")).lower() in techs)]
            if c.empty:
                continue
            c = c.set_index("GENERATION_PROJECT")
            mw = b[(b.y.astype(int) == p) & b.g.isin(c.index)].set_index("g").mw
            dollars = float((mw * pd.to_numeric(c.gen_overnight_cost.reindex(mw.index), errors="coerce").fillna(0)).sum())
            mwh_built = 0.0
            if se is not None and "gen_storage_energy_overnight_cost" in c:
                e = se[(se.y.astype(int) == p) & se.g.isin(c.index)].set_index("g").mwh
                ec = pd.to_numeric(c.gen_storage_energy_overnight_cost.reindex(e.index), errors="coerce").fillna(0)
                dollars += float((e * ec).sum())
                mwh_built = float(e.sum())
            credit = dollars * f / (1 - f)
            if credit:
                rows.append({"case": case, "period": p, "category": "investment_credit", "tech": "storage",
                             "vintage": p, "mw_built": float(mw.sum()), "mwh_built": mwh_built,
                             "itc_rate": float(r.itc_rate.iat[0]), "phase": float(r.phase.iat[0]),
                             "dollars_one_time": credit})
    return pd.DataFrame(rows, columns=["case", "period", "category", "tech", "vintage", "mw_built", "mwh_built",
                                       "itc_rate", "phase", "dollars_one_time"])


def summary(long: pd.DataFrame, storage: pd.DataFrame | None = None) -> pd.DataFrame:
    """case x period: $/yr by category and total (millions), as v3.1; then the production credits by technology
    (wind_per_yr, solar_per_yr) and the storage ITC on the period's builds (storage_itc_one_time, $M)."""
    cols = ["case", "period", *CATEGORIES, "total", "wind_per_yr", "solar_per_yr", "storage_itc_one_time"]
    if long.empty and (storage is None or storage.empty):
        return pd.DataFrame(columns=cols)
    idx = pd.MultiIndex.from_tuples(sorted(set(zip(long.case, long.period))
                                           | (set(zip(storage.case, storage.period)) if storage is not None else set())),
                                    names=["case", "period"])
    t = (long.pivot_table(index=["case", "period"], columns="category", values="dollars_per_yr", aggfunc="sum",
                          fill_value=0.0) if len(long) else pd.DataFrame(index=idx))
    t = t.reindex(index=idx, columns=list(CATEGORIES), fill_value=0.0).fillna(0.0) / 1e6
    t["total"] = t.sum(axis=1)
    for tech in ("wind", "solar"):
        x = long[long.tech == tech].groupby(["case", "period"]).dollars_per_yr.sum() if len(long) else pd.Series(dtype=float)
        t[f"{tech}_per_yr"] = x.reindex(idx).fillna(0.0) / 1e6
    st = (storage.groupby(["case", "period"]).dollars_one_time.sum() if storage is not None and len(storage)
          else pd.Series(dtype=float))
    t["storage_itc_one_time"] = st.reindex(idx).fillna(0.0) / 1e6
    return t.round(1).reset_index()[cols]
