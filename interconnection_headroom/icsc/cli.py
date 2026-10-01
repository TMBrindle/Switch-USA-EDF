"""Command line entry point.

    python -m icsc.cli panel            # EIA-860M + NARIS -> zone saturation panel
    python -m icsc.cli inspect-lbnl     # show how LBNL workbook headers were resolved
    python -m icsc.cli link-report      # how LBNL projects were placed in ReEDS zones (MW shares)
    python -m icsc.cli fit-weights      # search proxy x tech weights x reuse share by fit
    python -m icsc.cli run              # full pipeline -> outputs/<scenario>/ic_tranches.csv
    python -m icsc.cli synthetic        # write SYNTHETIC LBNL-style files for testing
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
from pathlib import Path

import pandas as pd
import yaml

from . import eia, estimate, geo, linkage, lbnl, network, switch_writer, synthetic, tranches


def load_cfg(path: str) -> dict:
    cfg = yaml.safe_load(open(path))
    root = Path(path).resolve().parent
    for k, v in cfg["paths"].items():
        cfg["paths"][k] = str(root / v)
    cfg["saturation"]["transfer_capacity"] = str(root / cfg["saturation"]["transfer_capacity"])
    cfg["_root"] = str(root)
    return cfg


def build_panel(cfg: dict, proxy: str | None = None):
    c2z = geo.load_county2zone(cfg["paths"]["county2zone"])
    gens = eia.load_eia860m(cfg["paths"]["eia860m"])
    proxy = proxy or cfg["saturation"]["headroom_proxy"]
    override = None
    if proxy == "transfer":
        tx = network.transfer_capacity_by_zone(cfg["saturation"]["transfer_capacity"], c2z,
                                               cfg["saturation"]["boundary_weight"])
        override = tx["transfer_mw"]
    panel = eia.zone_panel(gens, c2z, cfg, proxy_override=override)
    panel["proxy"] = proxy
    return panel, gens, c2z


def start_saturation(panel: pd.DataFrame, gens, c2z, cfg: dict, start_year: int) -> pd.DataFrame:
    """Saturation at the start of the model horizon: last observed year plus EIA planned additions/retirements."""
    built = gens[gens["status"] == "operating"]
    last = int(min(built["op_year"].max(), panel["year"].max()))  # EIA-860M vintage
    now = panel[panel["year"] == last].set_index("ba").copy()
    if start_year > last:
        pc = eia.planned_changes(gens, c2z, cfg, last + 1, start_year).reindex(now.index).fillna(0)
        now["used_mw"] = (now["used_mw"] + pc["planned_add_mw_weighted"]
                          - cfg["saturation"]["retirement_reuse_share"] * pc["planned_retire_mw_weighted"]).clip(lower=0)
        now["saturation"] = now["used_mw"] / now["headroom_proxy_floored_mw"]
    now["year"] = start_year
    return now


def cmd_run(cfg: dict, start_year: int, scenarios: list[str] | None):
    out = Path(cfg["paths"]["outputs"])
    out.mkdir(parents=True, exist_ok=True)
    panel, gens, c2z = build_panel(cfg)
    panel.to_csv(out / "zone_panel.csv", index=False)
    hier = geo.load_hierarchy(cfg["paths"]["hierarchy"])

    projects = lbnl.load_all(cfg, c2z)
    aliases = cfg.get("regimes", {}).get("lbnl_to_regime", {})
    projects["regime"] = projects["regime"].map(lambda r: aliases.get(r, r))
    projects.to_csv(out / "lbnl_projects_clean.csv", index=False)
    sample = estimate.attach_saturation(lbnl.estimation_sample(projects, cfg), panel)
    sample.to_csv(out / "estimation_sample.csv", index=False)

    model = estimate.fit(sample, cfg)
    estimate.coef_table(model).to_csv(out / "coefficients.csv")
    regimes = tranches.zone_regimes(hier, model, cfg)
    regimes.to_csv(out / "zone_regimes.csv")

    now = start_saturation(panel, gens, c2z, cfg, start_year)
    now.to_csv(out / "start_saturation.csv")
    now_panel = now.reset_index()
    ref = tranches.build_reference(model, now_panel, regimes, cfg, start_year)
    best = tranches.build_reference(model, now_panel, regimes, cfg, start_year, regime_override="best")

    zone_map = None
    zm = cfg.get("switch", {}).get("zone_map")
    if zm:
        m = pd.read_csv(Path(cfg["_root"]) / zm)
        zone_map = dict(zip(m["ba"], m["load_zone"]))

    summary = {"n_projects_clean": int(len(projects)),
               "ba_match_rate": float(projects["ba"].notna().mean()),
               "n_estimation": int(model.result.nobs), "r2": float(model.result.rsquared),
               "regime_effects": model.regime_effects().round(3).to_dict(), "scenarios": {}}
    for name, scen in cfg["scenarios"].items():
        if scenarios and name not in scenarios:
            continue
        z, t, u = tranches.apply_scenario(*ref, cfg, scen or {}, best=best)
        z.to_csv(out / f"zones_{name}.csv", index=False)
        t.to_csv(out / f"tranches_{name}.csv", index=False)
        u.to_csv(out / f"uprates_{name}.csv", index=False)
        switch_writer.to_switch(z, t, u, out / "switch" / name, cfg, zone_map)
        mw = t.merge(z[["ba", "base_capacity_mw"]], on="ba")
        mw["mw"] = mw["width"] * mw["base_capacity_mw"]
        summary["scenarios"][name] = {
            "zones": int(len(z)),
            "curve_mw_at_base_capacity": round(float(mw["mw"].sum())),
            "first_step_median_cost_per_kw": round(float(t.groupby("ba")["cost_per_kw"].first().median()), 1),
            "share_of_curve_mw_extrapolated": round(float((mw["mw"] * mw["extrapolated"]).sum() / mw["mw"].sum()), 3),
            "uprate_mw_available": round(float(u["max_mw"].sum())) if len(u) else 0,
        }
    json.dump(summary, open(out / "run_summary.json", "w"), indent=2)
    print(json.dumps(summary, indent=2))


def weight_grid(cfg: dict) -> list[dict]:
    g = cfg.get("weight_search", {})
    keys = ["solar", "wind", "storage", "gas", "other"]
    vals = [g.get(k, [cfg["saturation"]["tech_weights"].get(k, 1.0)]) for k in keys]
    return [dict(zip(keys, v)) for v in itertools.product(*vals)]


def cmd_fit_weights(cfg: dict, proxies: list[str] | None = None) -> pd.DataFrame:
    """Grid search over headroom proxy, technology weights and retirement reuse share.

    Every combination is fitted on the same LBNL sample, so AIC is comparable across rows.
    Weights are only identified where zones differ in technology mix: treat rows within ~2 AIC
    of the best as equally supported.
    """
    c2z = geo.load_county2zone(cfg["paths"]["county2zone"])
    gens = eia.load_eia860m(cfg["paths"]["eia860m"])
    base = lbnl.estimation_sample(lbnl.load_all(cfg, c2z), cfg)
    reuse_vals = cfg.get("weight_search", {}).get("reuse_share", [cfg["saturation"]["retirement_reuse_share"]])
    rows = []
    for proxy in proxies or ["transfer", "generation"]:
        override = None
        if proxy == "transfer":
            override = network.transfer_capacity_by_zone(cfg["saturation"]["transfer_capacity"], c2z,
                                                         cfg["saturation"]["boundary_weight"])["transfer_mw"]
        comp = eia.zone_components(gens, c2z, cfg, proxy_override=override)
        for w in weight_grid(cfg):
            for reuse in reuse_vals:
                panel = eia.apply_weights(comp, w, reuse)
                m = estimate.fit(estimate.attach_saturation(base, panel), cfg)
                rows.append({"proxy": proxy, **{f"w_{k}": v for k, v in w.items()}, "reuse_share": reuse,
                             "n": int(m.result.nobs), "r2": m.result.rsquared, "aic": m.result.aic,
                             "sat_coef": m.result.params.get("sat"),
                             "sat_t": m.result.params.get("sat") / m.result.bse.get("sat")})
    res = pd.DataFrame(rows).sort_values("aic").reset_index(drop=True)
    res["d_aic"] = res["aic"] - res["aic"].iloc[0]
    out = Path(cfg["paths"]["outputs"])
    out.mkdir(parents=True, exist_ok=True)
    res.to_csv(out / "weight_search.csv", index=False)
    print(res.head(15).round(3).to_string(index=False))
    print(f"\n{(res['d_aic'] <= 2).sum()} of {len(res)} combinations within 2 AIC of the best. "
          f"Full table: {out / 'weight_search.csv'}")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(prog="icsc")
    ap.add_argument("command", choices=["panel", "inspect-lbnl", "link-report", "fit-weights", "run", "synthetic"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--start-year", type=int, default=2026)
    ap.add_argument("--scenarios", nargs="*")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    if a.command == "panel":
        panel, gens, c2z = build_panel(cfg)
        Path(cfg["paths"]["outputs"]).mkdir(parents=True, exist_ok=True)
        panel.to_csv(Path(cfg["paths"]["outputs"]) / "zone_panel.csv", index=False)
        print(geo.match_report(geo.attach_ba(gens, c2z, "state", "county"), "mw"))
        print(panel[panel["year"] == panel["year"].max()].sort_values("saturation", ascending=False)
              .head(15).round(2).to_string(index=False))
    elif a.command == "inspect-lbnl":
        print(lbnl.inspect(cfg))
    elif a.command == "link-report":
        c2z = geo.load_county2zone(cfg["paths"]["county2zone"])
        projects = lbnl.load_all(cfg, c2z)
        out = Path(cfg["paths"]["outputs"])
        out.mkdir(parents=True, exist_ok=True)
        projects.to_csv(out / "lbnl_projects_linked.csv", index=False)
        print(linkage.report(projects, cfg))
    elif a.command == "fit-weights":
        cmd_fit_weights(cfg)
    elif a.command == "run":
        cmd_run(cfg, a.start_year, a.scenarios)
    elif a.command == "synthetic":
        panel, _, c2z = build_panel(cfg)
        d = synthetic.make(panel, c2z, geo.load_hierarchy(cfg["paths"]["hierarchy"]),
                           Path(cfg["paths"]["lbnl_dir"]))
        print(f"SYNTHETIC workbooks written to {d}. Delete them before running on real LBNL data.")


if __name__ == "__main__":
    main()
