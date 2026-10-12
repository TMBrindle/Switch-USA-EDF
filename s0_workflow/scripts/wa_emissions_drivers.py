"""What drives Washington's emissions in a solved S0 chain (v3.2 WA reassessment; CHANGES §127).

    python s0_workflow/scripts/wa_emissions_drivers.py --stage 2028=<case>/2028 --stage 2030=<case>/2030 ... --out <dir>

Each --stage points at a solved stage folder holding inputs/ and outputs/ (or pass inputs=...,outputs=... as
YEAR=<inputs_dir>,<outputs_dir>). Reads only Switch outputs and the stage's own inputs; writes into --out:

  wa_gen_by_fuel.csv     stage, zone, energy source: GWh/yr and tCO2/yr (dispatch_gen_annual_summary.csv), WA zones
  wa_flows.csv           stage, from, to, from_state, to_state: GWh/yr delivered (DispatchTx x efficiency x timepoint
                         weight in year, as trans_hurdle_cost's results), and the import charge on that direction
                         (trans_import_cost_results.csv / trans_import_tranches_results.csv, when present)
  wa_summary.csv         per stage: WA generation and emissions by fuel group, WA net exports to CA and to other
                         states, WA charged imports, the CA tranche (delivered, free, excess), and the WA emission
                         change from the first stage split by fuel
States from hierarchy.csv (`st`): WA zones p1-p4, CA zones the CA ones; "other" = everything else.

The questions it answers (Tom, 11 Oct 2026):
  * Is WA's 1.3 -> 2.9 Mt rise from WA gas serving WA load, or WA gas exported to CA (CA<->WA flows pay no import
    charge, so with CA's free tranche bound, WA generation is a cheaper route into CA than charged imports)?
  * How much of WA's imports are charged, and does the charge (0.37 TWh in 2028) plausibly move anything?
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
FOSSIL = {"Naturalgas": "gas", "NaturalGas": "gas", "Coal": "coal", "Distillate": "oil", "Residual": "oil"}


def states() -> dict:
    h = pd.read_csv(REPO / "hierarchy.csv")
    return dict(zip(h.ba, h.st))


def tp_weights(inputs: Path) -> pd.Series:
    """Hours per year each timepoint stands for (Switch's tp_weight_in_year)."""
    tp = pd.read_csv(inputs / "timepoints.csv")
    ts = pd.read_csv(inputs / "timeseries.csv")
    per = pd.read_csv(inputs / "periods.csv").set_index("INVESTMENT_PERIOD")
    ts["years"] = ts.ts_period.map(per.period_end - per.period_start + 1)
    w = ts.set_index("TIMESERIES")
    w = w.ts_duration_of_tp * w.ts_scale_to_period / w.years
    return tp.set_index("timepoint_id").timeseries.map(w).fillna(0.0)


def stage_tables(year: int, inputs: Path, outputs: Path, st: dict):
    g = pd.read_csv(outputs / "dispatch_gen_annual_summary.csv")
    g = g[g.gen_load_zone.map(st) == "WA"]
    gen = (g.groupby(["gen_load_zone", "gen_energy_source"])[["Energy_GWh_typical_yr",
                                                              "DispatchEmissions_tCO2_per_typical_yr"]]
           .sum().reset_index().assign(stage=year))
    tx = pd.read_csv(outputs / "DispatchTx.csv")
    tx.columns = ["from", "to", "tp", "mw"]
    lines = pd.read_csv(inputs / "transmission_lines.csv")
    eff = {}
    for _, r in lines.iterrows():
        e = float(r.get("trans_efficiency", 1.0))
        eff[(r.trans_lz1, r.trans_lz2)] = eff[(r.trans_lz2, r.trans_lz1)] = e
    w = tp_weights(inputs)
    tx["gwh"] = tx.mw * tx.tp.map(w).fillna(0.0) * [eff.get((a, b), 1.0) for a, b in zip(tx["from"], tx["to"])] / 1e3
    fl = tx.groupby(["from", "to"]).gwh.sum().reset_index()
    fl["from_state"] = fl["from"].map(st).fillna("?")
    fl["to_state"] = fl["to"].map(st).fillna("?")
    fl = fl[(fl.from_state == "WA") | (fl.to_state == "WA")].assign(stage=year)
    ic = outputs / "trans_import_cost_results.csv"
    if ic.exists():
        c = pd.read_csv(ic)
        c = c[c.PERIOD == c.PERIOD.max()].set_index(["trans_lz_from", "trans_lz_to"])
        fl["charge_per_mwh"] = [c.cost_per_mwh.get((a, b)) for a, b in zip(fl["from"], fl["to"])]
        fl["charged_gwh"] = [c.delivered_mwh_per_yr.get((a, b), 0.0) / 1e3 for a, b in zip(fl["from"], fl["to"])]
    tr = {}
    for name, key in (("trans_import_tranche_results.csv", "CA"), ("trans_import_tranches_results.csv", None)):
        f = outputs / name
        if f.exists():
            t = pd.read_csv(f)
            if key is None:
                t = t[t.TRANCHE == "WA"]
            t = t[t.PERIOD == t.PERIOD.max()]
            if len(t):
                tr[key or "WA"] = t.iloc[0].to_dict()
    return gen, fl, tr


def summarise(gen: pd.DataFrame, fl: pd.DataFrame, tranches: dict) -> pd.DataFrame:
    rows = []
    for y in sorted(gen.stage.unique()):
        g = gen[gen.stage == y]
        f = fl[fl.stage == y]
        grp = g.gen_energy_source.map(lambda s: FOSSIL.get(s, "non-fossil"))
        r = {"stage": y, "wa_gen_twh": g.Energy_GWh_typical_yr.sum() / 1e3,
             "wa_emissions_mt": g.DispatchEmissions_tCO2_per_typical_yr.sum() / 1e6}
        for k in ("gas", "coal", "oil", "non-fossil"):
            m = grp == k
            r[f"wa_{k}_twh"] = g.Energy_GWh_typical_yr[m].sum() / 1e3
            r[f"wa_{k}_mt"] = g.DispatchEmissions_tCO2_per_typical_yr[m].sum() / 1e6
        for other in ("CA", "other"):
            to = f[(f.from_state == "WA") & ((f.to_state == other) if other == "CA" else ~f.to_state.isin(["WA", "CA"]))]
            fr = f[(f.to_state == "WA") & ((f.from_state == other) if other == "CA" else ~f.from_state.isin(["WA", "CA"]))]
            r[f"wa_exports_to_{other}_twh"] = to.gwh.sum() / 1e3
            r[f"wa_imports_from_{other}_twh"] = fr.gwh.sum() / 1e3
            r[f"wa_net_exports_to_{other}_twh"] = (to.gwh.sum() - fr.gwh.sum()) / 1e3
        if "charged_gwh" in f:
            r["wa_charged_imports_twh"] = f[f.to_state == "WA"].charged_gwh.sum() / 1e3
        for k, t in (tranches.get(y) or {}).items():
            r[f"{k}_tranche_delivered_twh"] = t["delivered_mwh_per_yr"] / 1e6
            r[f"{k}_tranche_free_twh"] = t["free_mwh_per_yr"] / 1e6
            r[f"{k}_tranche_excess_twh"] = t["excess_mwh_per_yr"] / 1e6
        rows.append(r)
    s = pd.DataFrame(rows)
    if len(s) > 1:
        base = s.iloc[0]
        for k in ("gas", "coal", "oil"):
            s[f"d_wa_{k}_mt_vs_first"] = s[f"wa_{k}_mt"] - base[f"wa_{k}_mt"]
        s["d_wa_emissions_mt_vs_first"] = s.wa_emissions_mt - base.wa_emissions_mt
        s["d_wa_net_exports_to_CA_twh_vs_first"] = s.wa_net_exports_to_CA_twh - base.wa_net_exports_to_CA_twh
    return s


def parse_stage(v: str):
    y, _, p = v.partition("=")
    if "," in p:
        a, b = p.split(",", 1)
        return int(y), Path(a), Path(b)
    return int(y), Path(p) / "inputs", Path(p) / "outputs"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stage", action="append", required=True, help="YEAR=<stage folder> or YEAR=<inputs>,<outputs>")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    st = states()
    gens, flows, tr = [], [], {}
    for v in a.stage:
        y, i, o = parse_stage(v)
        g, f, t = stage_tables(y, i, o, st)
        gens.append(g)
        flows.append(f)
        tr[y] = t
    gen, fl = pd.concat(gens, ignore_index=True), pd.concat(flows, ignore_index=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    gen.to_csv(out / "wa_gen_by_fuel.csv", index=False)
    fl.to_csv(out / "wa_flows.csv", index=False)
    s = summarise(gen, fl, tr)
    s.to_csv(out / "wa_summary.csv", index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 50):
        print(s.round(3).T.to_string())


if __name__ == "__main__":
    main()
