"""Statutory production tax credits, levelised over each generator's capital-recovery life (CHANGES §90).

Settings (the tax_credits axis in pg/settings/scenario_management.yml), per model year:

    tax_credit_terms:
      <name>:
        value_per_mwh: 27.5        # statutory $/MWh
        dollar_year: 2024          # must equal the model's target_usd_year (no conversion here)
        duration_years: 10         # years of output that earn it
        techs: [LandbasedWind, ...]  # case-insensitive substrings of the technology (as tax_credit_values)
        build_years: [2025, null]  # eligible build-year window, inclusive (null: open); the build year of a
                                   # model build is its period (the period label)
        phase_down: {2033: 0.75}   # optional {first build year: fraction}, each key holding until the next
        prelevelised: false        # true: the value is already an in-model $/MWh (placeholder): not scaled
        levelised_value_per_mwh: x # optional hand-entered in-model value: must equal the computed one
        rate: x                    # optional rate override (warned: differs from capital annualisation)

In-model credit (gen_tax_credits.csv, $/MWh on every MWh the generator produces in the model):

    value x phase x AF(r_g, duration) / AF(r_g, life_g)     (duration >= life: no scaling)

AF(r, n) = (1 - (1 + r)^-n) / r, the annuity factor: the credit earned for `duration` years, spread evenly over the
`life` years over which the model recovers the generator's capital, has the same present value at r. r_g and life_g
are read from the fields Switch annualises the generator's capital with:
  * r_g: interest_rate (financials.csv, the same setting pg_to_switch writes there; Switch's capital recovery factor
    crf(interest_rate, n)). Not a PowerGenome per-technology WACC: Switch annualises overnight capex itself.
  * life_g: gen_amortization_period (gen_info.csv, PowerGenome's cap_recovery_years) when the case loads
    study_modules.gen_amortization_period (S0 cases: s0_production.extra_modules), else gen_max_age (the core
    module's crf(interest_rate, gen_max_age)).

Outputs (case inputs folder; written only when a statutory, non-prelevelised term applies, so S0's inputs stay as in
v3.1): credit_levelisation_report.csv (per term, technology and period: value, duration, r,
life, factor, result, run mode, discount rate, flags), gen_tax_credits_by_vintage.csv (the hook for vintage-indexed
dispatch credits: statutory terms per project and build year; not read by any Switch module yet) and
tax_credit_terms.csv (the statutory terms by period, for the post-run tally: s0_workflow/credit_tally.py).

The same levelised treatment applies in every run mode (perfect foresight, myopic, rolling windows); the mode is
recorded in the report.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)
TERMS_KEY = "tax_credit_terms"
AMORT_MODULE = "study_modules.gen_amortization_period"
RUN_MODES = {"single": "perfect_foresight", "myopic": "myopic", "windows": "rolling"}
TOL = 0.01          # $/MWh: hand-entered vs computed in-model value


def annuity_factor(r: float, n: float) -> float:
    r, n = float(r), float(n)
    return n if abs(r) < 1e-12 else (1 - (1 + r) ** -n) / r


def level_factor(r: float, duration: float, life: float) -> float:
    """AF(r, duration) / AF(r, life); 1 when the credit lasts at least the capital-recovery life."""
    if float(duration) >= float(life):
        return 1.0
    return annuity_factor(r, duration) / annuity_factor(r, life)


def financial_rates(settings: dict) -> tuple[float, float]:
    """(interest_rate, discount_rate) exactly as pg_to_switch writes financials.csv (same keys, same defaults)."""
    s = settings or {}
    return float(s.get("interest_rate", 0.05)), float(s.get("discount_rate", 0.03))


def amortization_on(settings: dict) -> bool:
    """Whether the case loads study_modules.gen_amortization_period: on an S0 case's scenario line
    (s0_production.extra_modules) or in switch/modules.txt."""
    s0 = (settings or {}).get("s0_production") or {}
    if s0.get("enabled") and AMORT_MODULE in (s0.get("extra_modules") or []):
        return True
    mt = REPO / "switch/modules.txt"
    return mt.exists() and AMORT_MODULE in {x.strip() for x in mt.read_text().splitlines()}


def run_mode(settings: dict) -> str:
    s0 = (settings or {}).get("s0_production") or {}
    if s0.get("enabled"):
        mode = (s0.get("foresight") or {}).get("mode", "single")
        return RUN_MODES.get(mode, mode)
    return "set_by_pg_to_switch_flags"      # legacy cases: --myopic on the command line, not in the settings


def terms_for(settings: dict) -> dict:
    t = (settings or {}).get(TERMS_KEY) or {}
    for name, term in t.items():
        if term is None:
            continue
        missing = {"value_per_mwh", "techs"} - set(term)
        if missing:
            raise ValueError(f"{TERMS_KEY}.{name}: missing {sorted(missing)}")
        if not term.get("prelevelised") and "duration_years" not in term:
            raise ValueError(f"{TERMS_KEY}.{name}: duration_years is required unless prelevelised: true")
    return {k: v for k, v in t.items() if v is not None}


def eligible(term: dict, build_year: int) -> bool:
    lo, hi = (list(term.get("build_years") or [None, None]) + [None, None])[:2]
    return (lo is None or build_year >= int(lo)) and (hi is None or build_year <= int(hi))


def phase(term: dict, build_year: int) -> float:
    pd_ = {int(k): float(v) for k, v in (term.get("phase_down") or {}).items()}
    f = 1.0
    for k in sorted(pd_):
        if build_year >= k:
            f = pd_[k]
    return f


def lives(gen_info: pd.DataFrame, amort: bool) -> pd.DataFrame:
    """GENERATION_PROJECT -> (life_years, life_source), the life Switch recovers its capital over."""
    gi = gen_info.set_index("GENERATION_PROJECT")
    out = pd.DataFrame({"life_years": pd.to_numeric(gi["gen_max_age"], errors="coerce"),
                        "life_source": "gen_max_age"}, index=gi.index)
    if amort and "gen_amortization_period" in gi.columns:
        ap = pd.to_numeric(gi["gen_amortization_period"], errors="coerce")
        use = ap.notna() & (ap > 0)
        out.loc[use, "life_years"] = ap[use]
        out.loc[use, "life_source"] = "gen_amortization_period"
    return out


def apply_terms(gens: pd.DataFrame, scen_settings_dict: dict, out_folder: Path, first_settings: dict) -> bool:
    """Add each period's levelised statutory credits to gens["gen_ptc_value_per_mwh"] (in place; gens: new-build rows
    of gens_by_model_year with Resource, technology, model_year) and write the report, vintage hook and terms files.
    A project-period already credited by hand-entered tax_credit_values (gens["_hand_value"]) must agree with the
    computed value, or the build stops. Returns True when any term applied."""
    out_folder = Path(out_folder)
    periods = {y: s for y, s in scen_settings_dict.items() if terms_for(s)}
    if not periods:
        return False
    gi = pd.read_csv(out_folder / "gen_info.csv", na_values=["."])
    r_cap, d_rate = financial_rates(first_settings)
    amort = amortization_on(first_settings)
    life = lives(gi, amort)
    mode = run_mode(first_settings)
    usd = (first_settings or {}).get("target_usd_year")
    tech_lower = gens["technology"].str.lower()
    report, vint, term_rows, warned = [], [], [], set()
    for year, s in periods.items():
        for name, term in terms_for(s).items():
            if usd is not None and int(term.get("dollar_year", usd)) != int(usd):
                raise ValueError(f"{TERMS_KEY}.{name}: dollar_year {term['dollar_year']} but target_usd_year {usd}")
            pre = bool(term.get("prelevelised"))
            r = float(term.get("rate", r_cap))
            if r != r_cap and (name, "rate") not in warned:
                warned.add((name, "rate"))
                logger.warning("tax credit %s: levelised at rate %.4f, not the capital annualisation rate "
                               "(interest_rate %.4f)", name, r, r_cap)
            if r != d_rate and ("discount",) not in warned and not pre:
                warned.add(("discount",))
                logger.warning("tax credits levelised at interest_rate %.4f (the rate Switch annualises capital "
                               "with); the period discount rate is %.4f", r, d_rate)
            by = int(year) if year is not None else None
            term_rows.append({"PERIOD": year, "tax_credit": name, "techs": json.dumps(list(term["techs"])),
                              "value_per_mwh": float(term["value_per_mwh"]),
                              "duration_years": term.get("duration_years"), "prelevelised": pre,
                              "build_year_first": (term.get("build_years") or [None, None])[0],
                              "build_year_last": ((term.get("build_years") or [None, None]) + [None])[1],
                              "phase_down": json.dumps({str(k): v for k, v in (term.get("phase_down") or {}).items()}),
                              "dollar_year": term.get("dollar_year", usd)})
            if by is not None and not eligible(term, by):
                continue
            ph = phase(term, by) if by is not None else 1.0
            period_mask = (gens["model_year"] == year) if year is not None else pd.Series(True, index=gens.index)
            for sub in term["techs"]:
                mask = period_mask & tech_lower.str.contains(str(sub).lower(), regex=False)
                for i in gens.index[mask]:
                    g = gens.at[i, "Resource"]
                    lf, src = (life.at[g, "life_years"], life.at[g, "life_source"]) if g in life.index else (None, "")
                    if pre:
                        factor = 1.0
                    else:
                        if lf is None or pd.isna(lf):
                            raise ValueError(f"tax credit {name}: no capital-recovery life for {g}")
                        factor = level_factor(r, term["duration_years"], lf)
                    val = float(term["value_per_mwh"]) * ph * factor
                    hand = term.get("levelised_value_per_mwh")
                    if hand is not None and abs(float(hand) - val) > TOL:
                        raise ValueError(f"tax credit {name}: hand-entered levelised_value_per_mwh {hand} conflicts with "
                                         f"the computed {val:.4f} for {g} ({gens.at[i, 'technology']}, {year})")
                    hv = gens.at[i, "_hand_value"]
                    if hv > 0 and abs(hv - val) > TOL:
                        raise ValueError(f"tax credit {name}: {g} in {year} is also credited {hv} by hand-entered "
                                         f"tax_credit_values; the computed value is {val:.4f}")
                    if hv > 0:
                        continue                          # the same credit, entered both ways: count it once
                    gens.at[i, "gen_ptc_value_per_mwh"] += val
                    report.append({"tax_credit": name, "technology": gens.at[i, "technology"], "PERIOD": year,
                                   "value_per_mwh": float(term["value_per_mwh"]), "phase": ph,
                                   "duration_years": term.get("duration_years"), "rate": r,
                                   "life_years": lf, "life_source": src, "factor": factor,
                                   "levelised_value_per_mwh": val, "prelevelised": pre, "run_mode": mode,
                                   "interest_rate": r_cap, "discount_rate": d_rate,
                                   "flag": ("PLACEHOLDER: prelevelised value, not computed from statutory terms"
                                            if pre else "")})
                    vint.append({"GENERATION_PROJECT": g, "BUILD_YEAR": year, "tax_credit": name,
                                 "statutory_value_per_mwh": float(term["value_per_mwh"]) * ph,
                                 "duration_years": term.get("duration_years"), "levelised_value_per_mwh": val,
                                 "prelevelised": pre})
    rep = pd.DataFrame(report)
    if not len(rep) or rep.prelevelised.all():
        # only prelevelised placeholders (S0: nuclear $15): gen_tax_credits.csv as before and no new files, so the
        # case's inputs stay byte-identical to v3.1's (stage reuse fingerprints every input file); flag in the log
        for x in rep.drop_duplicates(["tax_credit", "PERIOD"]).itertuples() if len(rep) else ():
            logger.info("tax credit %s %s: %g $/MWh PLACEHOLDER (prelevelised, not computed from statutory terms)",
                        x.tax_credit, x.PERIOD, x.value_per_mwh)
        return True
    if len(rep):
        rep = rep.drop_duplicates(subset=[c for c in rep.columns if c != "technology"] + ["technology"])
        rep = rep.sort_values(["PERIOD", "tax_credit", "technology"]).reset_index(drop=True)
    rep.to_csv(out_folder / "credit_levelisation_report.csv", index=False)
    pd.DataFrame(vint, columns=["GENERATION_PROJECT", "BUILD_YEAR", "tax_credit", "statutory_value_per_mwh",
                                "duration_years", "levelised_value_per_mwh", "prelevelised"]).to_csv(
        out_folder / "gen_tax_credits_by_vintage.csv", index=False)
    pd.DataFrame(term_rows).to_csv(out_folder / "tax_credit_terms.csv", index=False)
    if len(rep):
        logger.info("tax credits (%s): %s", mode, "; ".join(
            f"{x.tax_credit} {x.technology} {x.PERIOD}: {x.value_per_mwh:g} x {x.factor:.4f} = "
            f"{x.levelised_value_per_mwh:.2f} $/MWh (r {x.rate:g}, life {x.life_years:g} {x.life_source}, "
            f"{x.duration_years} yrs){' PLACEHOLDER' if x.prelevelised else ''}"
            for x in rep.drop_duplicates(["tax_credit", "PERIOD", "factor"]).itertuples()))
    return True
