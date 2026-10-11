"""python -m gasnet.cli {fetch,pin,verify,run,report} (run from gas_network/)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import yaml

from . import PKG, balance, crosscheck, fetch, limitations, load_config, network, reconcile, report, tracing, zones


def _vintages(cfg) -> dict:
    src = PKG / "data/SOURCES.yml"
    rel = {}
    if src.exists():
        for e in yaml.safe_load(open(src))["files"]:
            rel[e["id"]] = e["release"]
    # short codes; data/SOURCES.yml has the full entries
    base = (f"NGA{cfg['base_year']}@{rel.get('eia_dry_production_state', '?')};ARR2024@{rel.get('eia_arr_2024', '?')};"
            f"NEMS@{fetch.NEMS_COMMIT[:7]}")
    return {"base": base, "proj": base + ";AEO2026@cb2026.d021826b"}


VARIANTS = [("additive", True), ("ratio", True), ("additive", False), ("ratio", False)]
EXAMPLE_ZONES = {"FL (p91, p101, p102)": "p101", "TX_E zones (p57, p64-p67)": "p64", "MA (p131)": "p131",
                 "CA (p8-p11)": "p10", "TX_W zones (p48, p59, p62)": "p48", "VA (p99, p100, p118, p124)": "p99"}


def _label(method: str, balanced: bool) -> str:
    return f"trace_aeo_{method}" + ("_balanced" if balanced else "_unbalanced")


def _zone_shares(w, hubs):
    z = w.merge(hubs, on="hub")
    z = z.assign(share=z.weight * z.share).groupby(["zone", "year", "basin", "method", "source_vintage"],
                                                   as_index=False).share.sum()
    z = z[["zone", "year", "basin", "share", "method", "source_vintage"]].sort_values(["zone", "year", "basin"])
    s = z.groupby(["zone", "year"]).share.sum()
    if (s.sub(1).abs() > 1e-9).any():
        raise AssertionError(f"zone shares do not sum to 1: {s[s.sub(1).abs() > 1e-9].head()}")
    return z


def run(cfg, log=print) -> dict:
    out = Path(cfg["paths"]["outputs"])
    out.mkdir(parents=True, exist_ok=True)
    bad = fetch.verify(*fetch.default_paths())
    if bad:
        raise RuntimeError(f"raw files differ from data/SOURCES.yml pins: {bad[:5]} ... (run fetch / check)")
    inp = network.Inputs(cfg)
    nodes, basins = inp.hubs.hub.tolist(), list(cfg["basins"])
    vint = _vintages(cfg)
    by = cfg["base_year"]
    default = (cfg.get("projection_method", "additive"), bool(cfg.get("balance", True)))
    base = network.base_network(inp)
    w, txc, zflags = zones.zone_hub_weights(cfg, inp.areas)

    def trace(n, y, label):
        t = tracing.trace(n["supply"][["hub", "basin", "value"]], n["flows"][["src", "dst", "value"]], nodes, basins)
        t["year"], t["method"] = y, label
        t["source_vintage"] = vint["base"] if y == by else vint["proj"]
        return t

    tb = trace(base, by, "trace_eia")
    variants, recon, prod = {}, [], [reconcile.compare(inp, base, tb, by, "trace_eia")]
    for method, bal in VARIANTS:
        label = _label(method, bal)
        nets, hub_rows = {}, [tb]
        for y in cfg["model_years"]:
            p = network.project(inp, base, y, method)
            ob = balance.local_disposition(p["supply"], p["flows"], nodes)
            info = {"overdraw_before_mmcf": float(ob[ob < 0].sum())}
            if bal:
                p, binfo = balance.balance(inp, base, p, nodes)
                info.update(binfo)
            la = balance.local_disposition(p["supply"], p["flows"], nodes)
            info.update(method=label, year=y, overdraw_after_mmcf=float(la[la < 0].sum()),
                        division_disposition_error_pct=balance.division_error(inp, base, p, nodes))
            info.pop("slack_hubs", None)
            info.pop("floor_hubs", None)
            recon.append(info)
            nets[y] = p
            t = trace(p, y, label)
            hub_rows.append(t)
            prod.append(reconcile.compare(inp, p, t, y, label))
        hubs = pd.concat(hub_rows, ignore_index=True)
        variants[(method, bal)] = {"nets": nets, "hubs": hubs, "zones": _zone_shares(w, hubs)}
    d = variants[default]
    z, hubs, nets = d["zones"], d["hubs"], {by: base, **d["nets"]}
    sens = variants[("ratio", True)]["zones"]
    sens[sens.year != by].to_csv(out / "zone_basin_shares_sensitivity_ratio.csv", index=False, float_format="%.6f")
    hubs[["hub", "year", "basin", "share", "throughput", "method", "source_vintage"]].rename(
        columns={"throughput": "throughput_mmcf"}).to_csv(out / "hub_basin_shares.csv", index=False, float_format="%.6f")
    # method comparison for example zones
    cmp_rows = []
    for (method, bal), v in variants.items():
        zz = v["zones"]
        for name, zone in EXAMPLE_ZONES.items():
            x = zz[(zz.zone == zone) & (zz.year != by)]
            cmp_rows.append(x.assign(zones=name, method=_label(method, bal)))
    x = z[(z.year == by) & z.zone.isin(EXAMPLE_ZONES.values())]
    cmp_rows.append(x.assign(zones=x.zone.map({v: k for k, v in EXAMPLE_ZONES.items()})))
    mc = pd.concat(cmp_rows, ignore_index=True)
    mc.pivot_table(index=["zones", "method", "year"], columns="basin", values="share").mul(100).round(2).to_csv(
        out / "method_comparison.csv")
    pd.DataFrame(recon).to_csv(out / "projection_reconciliation.csv", index=False, float_format="%.4f")
    pd.concat(prod, ignore_index=True).to_csv(out / "basin_production_vs_aeo.csv", index=False, float_format="%.4f")
    mix = limitations.district_mixing(inp, base["supply"])
    mix.to_csv(out / "district_basin_mixing.csv", index=False, float_format="%.2f")
    # default-variant diagnostics
    flags, txr, reg = [], [], []
    for y, n in nets.items():
        t = hubs[hubs.year == y]
        flags += [dict(zip(["year", "flag", "what", "mmcf", "note"], f)) for f in n["flags"]]
        ti = n["texas"]
        if ti["capacity_slack_mmcf"] > 1:
            flags.append(dict(year=y, flag="texas_intra_capacity_exceeded", what="intra-Texas",
                              mmcf=ti["capacity_slack_mmcf"], note="flow above NGMM 2023 capacity (slack arcs)"))
        if "balance" in n:
            for h, v in n["balance"]["slack_hubs"].items():
                flags.append(dict(year=y, flag="balance_unmet_floor", what=h, mmcf=v,
                                  note="local disposition below its AEO-grown floor after balancing"))
        txr.append(n["tx_routes"].assign(year=y, disposition_scale=ti["disposition_scale"]))
        reg.append(crosscheck.region_shares(inp, n, t, basins, 2025 if y == by else y).assign(year=y))
    sj = network.hsm_san_juan(inp.raw / "nems", "CO")
    if sj["province22_elsewhere"] > 0:
        flags.append(dict(year="", flag="hsm_province22_outside_san_juan_counties", what="CO",
                          mmcf=sj["province22_elsewhere"], note="left in Rockies (other)"))
    flags += [dict(year="", flag=f[0], what=f[1], mmcf="", note=f"{f[2]:.1f} MW gas; {f[3]}") for f in zflags]
    w.to_csv(out / "zone_hub_weights.csv", index=False, float_format="%.6f")
    txc.to_csv(out / "tx_county_hubs.csv", index=False)
    pd.concat(reg, ignore_index=True).to_csv(out / "region_crosscheck.csv", index=False, float_format="%.6f")
    crosscheck.arcs(inp, base, {y: n for y, n in nets.items() if y != by}).to_csv(
        out / "table64_arcs_check.csv", index=False, float_format="%.1f")
    pd.concat(txr, ignore_index=True).to_csv(out / "texas_routing.csv", index=False, float_format="%.4f")
    pd.DataFrame(flags).to_csv(out / "flags.csv", index=False)
    tmpl = PKG / "data/basin_intensity.csv"
    if not tmpl.exists():
        rows = [(b, y, "", "", "") for b in basins for y in nets]
        pd.DataFrame(rows, columns=["basin", "year", "kg_ch4_per_mmbtu", "gwp_basis", "source"]).to_csv(tmpl, index=False)
    # degeneracy of the balancing LP: a second optimum (tie-break) for the default variant
    deg_rows, deg_zone = [], []
    for y in cfg["model_years"]:
        p = network.project(inp, base, y, default[0])
        p2, _ = balance.balance(inp, base, p, nodes, tie_break=True)
        t2 = trace(p2, y, "tie_break")
        z2 = _zone_shares(w, t2)
        z1 = z[z.year == y]
        m = z1.merge(z2[["zone", "basin", "share"]], on=["zone", "basin"], suffixes=("", "_alt"))
        d = m.assign(ad=(m.share - m.share_alt).abs()).groupby("zone").ad.sum().div(2).mul(100)
        deg_zone += [dict(year=y, zone=k, tvd_pp=float(v)) for k, v in d.items()]
        for name, zone in EXAMPLE_ZONES.items():
            deg_rows.append(dict(year=y, zones=name, tvd_pp=float(d.get(zone, 0.0))))
        deg_rows.append(dict(year=y, zones="all zones: max", tvd_pp=float(d.max())))
        deg_rows.append(dict(year=y, zones="all zones: mean", tvd_pp=float(d.mean())))
    deg = pd.DataFrame(deg_rows)
    deg.to_csv(out / "balance_degeneracy.csv", index=False, float_format="%.2f")
    unmet = limitations.unmet_local_use(cfg, {y: n for y, n in nets.items() if y != by}, inp.raw)
    unmet.to_csv(out / "unmet_local_use.csv", index=False)
    deg_zone = pd.DataFrame(deg_zone, columns=["year", "zone", "tvd_pp"])
    deg_zone.to_csv(out / "balance_degeneracy_by_zone.csv", index=False, float_format="%.2f")
    fb = cfg.get("share_fallback", {})
    z.to_csv(out / "zone_basin_shares_no_fallback.csv", index=False, float_format="%.6f")
    z, switches = limitations.share_fallback(z, w, unmet, deg_zone, by, float(fb.get("unmet_pct_of_local_use", 25)),
                                             float(fb.get("degeneracy_pp", 10)))
    switches.to_csv(out / "share_fallback.csv", index=False)
    z.to_csv(out / "zone_basin_shares.csv", index=False, float_format="%.6f")
    hold = int(cfg.get("constrained_pipe_hold_year", 2030))
    limitations.constrained_pipe(z, hold).to_csv(out / "zone_basin_shares_constrained_pipe.csv", index=False,
                                                 float_format="%.6f")
    meta = limitations.metadata(cfg, default, mix, sj, _label(*default))
    meta["status"] = ("Stage 1 accepted (ddac626). 2024 shares are the reference; projected years are INDICATIVE "
                      "(scaled-and-rebalanced network, not a solved one).")
    meta["year_status"] = {str(y): ("reference" if y == by else "indicative") for y in nets}
    meta["balance_degeneracy"] = {
        "text": "The balancing LP has tied optima. A second optimum (sorted arcs, tiny tie-break costs) gives the "
                "total-variation distance (pp) below between zone shares; projected shares are indicative to this extent.",
        "rows": deg.round(2).to_dict(orient="records")}
    meta["unmet_local_use_after_balancing"] = {
        "text": "States whose local use (consumption + exports) stays below its AEO2026-grown floor after balancing "
                "(default variant). Bcf/yr. gas_mw = EIA-860M Aug 2026 operating NG nameplate. Allocation between "
                "states of one region can be a tie; regional totals are robust.",
        "rows": unmet.to_dict(orient="records")}
    meta["share_fallback"] = {
        "text": "CHANGES §104 (Tom): a projected zone-year uses the zone's 2024 shares (method fallback_2024, column "
                "'fallback' gives the reason) when a hub it draws on has unmet local use above "
                f"{fb.get('unmet_pct_of_local_use', 25)}% of its floor, or its shares differ by more than "
                f"{fb.get('degeneracy_pp', 10)} pp between equally good balancing solutions. Shares before the "
                "fallback: zone_basin_shares_no_fallback.csv.",
        "rows": switches.to_dict(orient="records")}
    meta["share_paths"] = {
        "aeo": "zone_basin_shares.csv: AEO2026 production on NGMM's network, which builds +11.1/+22.5 Bcf/d of "
               "Mid-Atlantic/Ohio -> Eastern Midwest capacity by 2035/2045 (CHANGES §104).",
        "constrained_pipe": f"zone_basin_shares_constrained_pipe.csv: no interregional capacity beyond existing + under "
                            f"construction; each zone holds its {hold} shares from {hold} on (CHANGES §105)."}
    json.dump(meta, open(out / "zone_basin_shares.meta.json", "w"), indent=1)
    summ = {"years": list(nets), "zones": int(z.zone.nunique()), "hubs": len(nodes), "basins": basins,
            "default_method": _label(*default), "aeo_datekey": inp.aeo_datekey, "flags": len(flags),
            "texas": {str(y): {"disposition_scale": round(n["texas"]["disposition_scale"], 4),
                               "capacity_slack_mmcf": round(n["texas"]["capacity_slack_mmcf"], 1)}
                      for y, n in nets.items()}}
    json.dump(summ, open(out / "run_summary.json", "w"), indent=1)
    log(f"zone_basin_shares.csv ({_label(*default)}): {len(z)} rows, {summ['zones']} zones x {len(nets)} years; "
        f"flags {len(flags)}")
    return {"zones": z, "hubs": hubs, "nets": nets, "flags": flags, "variants": variants}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gasnet")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    sub.add_parser("pin")
    sub.add_parser("verify")
    sub.add_parser("run")
    sub.add_parser("stage2a")
    sub.add_parser("nemscap")
    u = sub.add_parser("upstream", help="post-solve upstream CH4 for a Switch run (§105)")
    u.add_argument("outputs_dir")
    u.add_argument("--out-dir", help="default <outputs_dir>/upstream_ch4")
    u.add_argument("--share-path", nargs="*", choices=["aeo", "constrained_pipe"])
    u.add_argument("--basis", nargs="*", choices=["measured", "inventory"])
    u.add_argument("--methane-path", nargs="*", choices=["current_rules", "no_further_reductions"])
    u.add_argument("--gas-fuels", nargs="*", help="override config gas_fuels (case-insensitive)")
    r = sub.add_parser("report")
    r.add_argument("outputs_dir")
    r.add_argument("--inputs-dir")
    r.add_argument("--shares", default=str(PKG / "outputs/zone_basin_shares.csv"))
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    raw, src = fetch.default_paths()
    if a.cmd == "fetch":
        n = fetch.fetch(raw)
        bad = fetch.verify(raw, src)
        print(f"missing {n}; sha256 mismatches {len(bad)} {bad[:5]}")
    elif a.cmd == "pin":
        fetch.pin(raw, src)
        print(f"wrote {src}")
    elif a.cmd == "verify":
        bad = fetch.verify(raw, src)
        print("ok" if not bad else f"mismatch: {bad}")
    elif a.cmd == "run":
        run(cfg)
    elif a.cmd == "stage2a":
        from . import stage2a
        stage2a.run(cfg)
    elif a.cmd == "nemscap":
        from . import nems_capacity
        r = nems_capacity.compare(raw, PKG / "data/reference", Path(cfg["paths"]["outputs"]) / "stage2a")
        print(f"NGCAPS flows vs Table 64 weighted abs error {r['flow_match_weighted_abs_error']:.3f}; "
              f"{len(r['rows'])} rows")
    elif a.cmd == "upstream":
        from . import upstream
        if a.gas_fuels:
            cfg["gas_fuels"] = a.gas_fuels
        r = upstream.upstream(a.outputs_dir, cfg, share_paths=a.share_path, bases=a.basis,
                              methane_paths=a.methane_path, out_dir=a.out_dir)
        tot = r["by_period"]
        c = tot[(tot.level == "central")]
        print(f"wrote upstream_ch4_by_zone/by_state/by_period.csv to {r['out_dir']}")
        for f in r["flags"]:
            print("FLAG", f)
        print(c[["period", "share_path", "basis", "methane_path", "total_tch4", "kg_ch4_per_mwh",
                 "gwp100_pct_of_stack", "gwp20_pct_of_stack"]].round(3).to_string(index=False))
    elif a.cmd == "report":
        d = report.basin_gas_mmbtu(a.outputs_dir, a.shares, cfg["gas_fuels"], a.inputs_dir)
        print(f"wrote {Path(a.outputs_dir) / 'basin_gas_mmbtu.csv'} ({len(d)} rows)")


if __name__ == "__main__":
    main()
