"""Post-run statutory tax-credit tally (CHANGES §90; s0_workflow/tax_credits.py).

The model carries each credit levelised over the plant's capital-recovery life. This tally shows what the statute
pays: for every credited vintage (model builds and predetermined builds alike) whose technology and build year a
statutory term covers, energy x the statutory $/MWh (x the phase-down) in each of its first duration_years in
service, by calendar year, plus a present-value column at the model's discount rate to its base year.

Inputs per stage (a chain's stages in order, or one stage): the inputs folder (tax_credit_terms.csv, periods.csv,
financials.csv, gen_info.csv) and the outputs folder (BuildGen.csv, dispatch_gen_annual_summary.csv).
  * vintages: BuildGen rows with capacity > 0 across the stages (a later stage carries earlier builds as
    predetermined; one row per project and build year);
  * a vintage's energy in a year: its project's energy in the period holding that year (the stage that solved the
    period first; MWh per typical year) x the vintage's share of the project's capacity in that period; years past
    the last period take the last period's;
  * first year in service: the period's first year for a model build (build year = a period label), else the build
    year itself;
  * terms: each vintage takes the terms of the period it was built in (tax_credit_terms.csv); a predetermined
    vintage, the first stage's terms (prelevelised placeholders are skipped: they have no statutory profile).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _read(p: Path) -> pd.DataFrame | None:
    return pd.read_csv(p, na_values=["."]) if Path(p).exists() else None


def tally(stages: list[dict]) -> pd.DataFrame:
    """stages: [{"inputs": Path, "outputs": Path}] in chain order. Long table, one row per vintage and year."""
    first_in = Path(stages[0]["inputs"])
    fin = pd.read_csv(first_in / "financials.csv").iloc[0]
    d, base = float(fin.discount_rate), int(fin.base_financial_year)
    periods, energy, builds, terms, gi = [], [], [], [], []
    for st in stages:
        i, o = Path(st["inputs"]), Path(st["outputs"])
        periods.append(pd.read_csv(i / "periods.csv"))
        gi.append(pd.read_csv(i / "gen_info.csv", na_values=["."])[["GENERATION_PROJECT", "gen_tech"]])
        t = _read(i / "tax_credit_terms.csv")
        if t is not None:
            terms.append(t)
        e = pd.read_csv(o / "dispatch_gen_annual_summary.csv")
        e = e.groupby(["generation_project", "period"], as_index=False)[["Energy_GWh_typical_yr", "GenCapacity_MW"]].sum()
        energy.append(e)
        b = pd.read_csv(o / "BuildGen.csv")
        builds.append(b.rename(columns={b.columns[0]: "gen", b.columns[1]: "build_year", b.columns[2]: "mw"}))
    per = pd.concat(periods).drop_duplicates("INVESTMENT_PERIOD").sort_values("INVESTMENT_PERIOD")
    energy = pd.concat(energy).drop_duplicates(["generation_project", "period"], keep="first")
    builds = pd.concat(builds).drop_duplicates(["gen", "build_year"], keep="first")
    builds = builds[builds.mw > 1e-6]
    tech = pd.concat(gi).drop_duplicates("GENERATION_PROJECT").set_index("GENERATION_PROJECT").gen_tech
    if not terms:
        return pd.DataFrame()
    terms = pd.concat(terms).drop_duplicates(["PERIOD", "tax_credit"], keep="first")
    terms = terms[~terms.prelevelised.astype(bool)]
    first_terms_period = terms.PERIOD.min()
    spans = list(zip(per.INVESTMENT_PERIOD, per.period_start, per.period_end))
    labels = {int(p) for p, _, _ in spans}

    def period_of(y):
        for p, s, e in spans:
            if s <= y <= e:
                return int(p)
        return int(spans[-1][0]) if y > spans[-1][2] else None

    e_idx = energy.set_index(["generation_project", "period"])
    rows = []
    for b in builds.itertuples():
        g, by = b.gen, int(b.build_year)
        model_build = by in labels
        start = int(per.set_index("INVESTMENT_PERIOD").at[by, "period_start"]) if model_build else by
        tp = by if model_build else first_terms_period
        for t in terms[terms.PERIOD == tp].itertuples():
            subs = json.loads(t.techs)
            if not any(str(s).lower() in str(tech.get(g, "")).lower() for s in subs):
                continue
            lo, hi = t.build_year_first, t.build_year_last
            yr_built = by if model_build else by
            if (pd.notna(lo) and yr_built < int(lo)) or (pd.notna(hi) and yr_built > int(hi)):
                continue
            ph = 1.0
            for k, v in sorted((int(k), float(v)) for k, v in json.loads(t.phase_down or "{}").items()):
                if yr_built >= k:
                    ph = v
            for y in range(start, start + int(t.duration_years)):
                p = period_of(y)
                if p is None or (g, p) not in e_idx.index:
                    continue
                x = e_idx.loc[(g, p)]
                share = b.mw / x.GenCapacity_MW if x.GenCapacity_MW > 1e-9 else 0.0
                mwh = x.Energy_GWh_typical_yr * 1000.0 * min(share, 1.0)
                usd = mwh * float(t.value_per_mwh) * ph
                rows.append({"GENERATION_PROJECT": g, "gen_tech": tech.get(g, ""), "tax_credit": t.tax_credit,
                             "build_year": by, "model_build": model_build, "year": y, "period_energy_from": p,
                             "energy_mwh": mwh, "statutory_value_per_mwh": float(t.value_per_mwh), "phase": ph,
                             "credit_usd": usd, "pv_usd": usd / (1 + d) ** (y - base),
                             "discount_rate": d, "base_year": base})
    return pd.DataFrame(rows)


def summary(t: pd.DataFrame) -> pd.DataFrame:
    if t.empty:
        return t
    return (t.groupby(["tax_credit", "gen_tech", "build_year", "model_build"], as_index=False)
             [["energy_mwh", "credit_usd", "pv_usd"]].sum())
