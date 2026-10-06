"""National build-rate ceilings with the §77 deliverability layer, for Tom's review.

Per level, group and year 2026-2045: the module ceiling (queue/pace layer: top band x R), the deliverability ceiling
(config `deliverability`) and the final ceiling = min of the two, and which layer binds. Also S0 (central) against the
placeholder tables the running S0 v3 chain was built with (post-2030 growth 5 / 5 / 8% a year, no deliverability
layer, no storage floor): annual and per model period (window means, as the case writer averages them).

§78: the layer applies from 2029, and where it binds R is scaled so the ceiling is 2.0R (tiers kept). Also S0's
effective free tier (1.3 x the window mean of R) per model period, against the placeholder tables'.

usage (build_rate/): python scripts/deliverability_report.py
    -> outputs/deliverability_report.csv, outputs/deliverability_s0_vs_placeholder.csv,
       outputs/deliverability_s0_tiers.csv
"""
import copy
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from brc import cli, data, rates  # noqa: E402

LEVELS = ["central", "reform_bp", "reform_bp_siting", "high", "high_reform"]
GROUPS = ["wind_onshore", "solar", "storage"]
YEARS = range(2026, 2046)
PERIODS = {2028: (2026, 2028), 2030: (2029, 2030), 2035: (2031, 2035), 2040: (2036, 2040), 2045: (2041, 2045)}


def inputs(cfg):
    c2t = data.county_transreg(cfg["paths"]["county2zone"], cfg["paths"]["hierarchy"])
    add = data.eia_additions(cfg["paths"]["eia860m"], c2t)
    h = cfg["history"]
    base = rates.base_rates(add, h["first_year"], h["last_year"])
    shares = rates.regional_shares(base, h["share_years"])
    comp = data.queue_components(data.load_queued_up(cfg["paths"]["queued_up"]), c2t)
    delay = rates.cod_delay(comp, cfg)
    near = rates.near_term(comp, rates.completion_rates(comp, cfg), delay, cfg)
    fb = cfg["floor_basis"]
    basis = rates.floor_basis(add, data.eia_stock(cfg["paths"]["eia860m"], c2t, fb["stock_year"]), fb["peak_years"])
    _, uplift = rates.reform_uplift(comp, cfg)
    return base, near, shares, basis, uplift


def placeholder_cfg(cfg):
    """The config the running S0 v3 chain was built with, for central: no growth_paths (scalar growth 5 / 5 / 8%), no
    deliverability layer or storage floor."""
    c = copy.deepcopy(cfg)
    c.pop("growth_paths", None)
    c.pop("deliverability", None)
    return c


def national(t):
    n = t[(t["region"] == rates.NATIONAL) & t["year"].isin(YEARS) & t["group"].isin(GROUPS)]
    return n.set_index(["group", "year"])


def run():
    cfg = cli.load_cfg(str(ROOT / "config.yaml"))
    args = inputs(cfg)
    tabs = rates.rate_tables(cfg, LEVELS, *args)
    rows = []
    for lv in LEVELS:
        n = national(tabs[lv])
        for (g, y), r in n.iterrows():
            rows.append({"level": lv, "group": g, "year": y, "module_gw": r["module_ceiling_mw_per_yr"] / 1e3,
                         "deliverability_gw": r["deliverability_mw_per_yr"] / 1e3,
                         "final_gw": r["ceiling_mw_per_yr"] / 1e3,
                         "binds": "deliverability" if r["deliverability_binds"] else "module"})
    rep = pd.DataFrame(rows)
    rep.round(3).to_csv(ROOT / "outputs/deliverability_report.csv", index=False)
    old = national(rates.rate_tables(placeholder_cfg(cfg), ["central"], *args)["central"])["ceiling_mw_per_yr"]
    new = rep[rep["level"] == "central"].set_index(["group", "year"])["final_gw"] * 1e3
    cmp_rows = [{"group": g, "span": str(y), "placeholder_gw": old[(g, y)] / 1e3, "final_gw": new[(g, y)] / 1e3}
                for g in GROUPS for y in YEARS]
    for p, (s, e) in PERIODS.items():
        for g in GROUPS:
            ys = range(s, e + 1)
            cmp_rows.append({"group": g, "span": f"period {p} ({s}-{e})",
                             "placeholder_gw": sum(old[(g, y)] for y in ys) / len(ys) / 1e3,
                             "final_gw": sum(new[(g, y)] for y in ys) / len(ys) / 1e3})
    cmp = pd.DataFrame(cmp_rows)
    cmp["change_pct"] = 100 * (cmp["final_gw"] / cmp["placeholder_gw"] - 1)
    cmp.round(3).to_csv(ROOT / "outputs/deliverability_s0_vs_placeholder.csv", index=False)
    r_new = national(tabs["central"])["r_data_mw_per_yr"]
    r_old = national(rates.rate_tables(placeholder_cfg(cfg), ["central"], *args)["central"])["r_data_mw_per_yr"]
    tiers = []
    for p, (s, e) in PERIODS.items():
        for g in GROUPS:
            ys = range(s, e + 1)
            rn = sum(r_new[(g, y)] for y in ys) / len(ys)
            ro = sum(r_old[(g, y)] for y in ys) / len(ys)
            tiers.append({"period": p, "group": g, "free_1p3r_gw": 1.3 * rn / 1e3, "plus15_to_1p75r_gw": 1.75 * rn / 1e3,
                          "ceiling_2r_gw": 2.0 * rn / 1e3, "placeholder_free_1p3r_gw": 1.3 * ro / 1e3})
    pd.DataFrame(tiers).round(3).to_csv(ROOT / "outputs/deliverability_s0_tiers.csv", index=False)
    return rep, cmp


if __name__ == "__main__":
    rep, cmp = run()
    for lv in LEVELS:
        print(f"\n{lv}: module / deliverability / final GW/yr (* = deliverability binds)")
        d = rep[rep["level"] == lv]
        cell = d.assign(c=d.apply(lambda r: f"{r.module_gw:.1f} / {r.deliverability_gw:.1f} / {r.final_gw:.1f}"
                                  + ("*" if r.binds == "deliverability" else ""), axis=1))
        print(cell.pivot(index="year", columns="group", values="c")[GROUPS].to_string())
    print("\nS0 (central) final vs the running chain's placeholder ceilings, GW/yr")
    print(cmp.round(1).to_string(index=False))
