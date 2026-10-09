"""python -m gasnet.cli {fetch,pin,verify,run,report} (run from gas_network/)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import yaml

from . import PKG, crosscheck, fetch, load_config, network, report, tracing, zones


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


def run(cfg, log=print) -> dict:
    out = Path(cfg["paths"]["outputs"])
    out.mkdir(parents=True, exist_ok=True)
    bad = fetch.verify(*fetch.default_paths())
    if bad:
        raise RuntimeError(f"raw files differ from data/SOURCES.yml pins: {bad[:5]} ... (run fetch / check)")
    inp = network.Inputs(cfg)
    nodes, basins = inp.hubs.hub.tolist(), list(cfg["basins"])
    vint = _vintages(cfg)
    base = network.base_network(inp)
    nets = {cfg["base_year"]: base}
    for y in cfg["model_years"]:
        nets[y] = network.project(inp, base, y)
    hub_rows, flags, txr, reg = [], [], [], []
    for y, n in nets.items():
        t = tracing.trace(n["supply"][["hub", "basin", "value"]], n["flows"][["src", "dst", "value"]], nodes, basins)
        t["year"] = y
        # trace_eia: proportional sharing on EIA state-to-state movements; trace_aeo_indexed: the same network with
        # production and flows indexed to AEO2026 (gas_network.md, "Projections")
        t["method"] = "trace_eia" if y == cfg["base_year"] else "trace_aeo_indexed"
        t["source_vintage"] = vint["base"] if y == cfg["base_year"] else vint["proj"]
        hub_rows.append(t)
        flags += [dict(zip(["year", "flag", "what", "mmcf", "note"], f)) for f in n["flags"]]
        ti = n["texas"]
        if ti["capacity_slack_mmcf"] > 1:
            flags.append(dict(year=y, flag="texas_intra_capacity_exceeded", what="intra-Texas",
                              mmcf=ti["capacity_slack_mmcf"], note="flow above NGMM 2023 capacity (slack arcs)"))
        txr.append(n["tx_routes"].assign(year=y, disposition_scale=ti["disposition_scale"]))
        reg.append(crosscheck.region_shares(inp, n, t, basins, 2025 if y == cfg["base_year"] else y).assign(year=y))
    hubs = pd.concat(hub_rows, ignore_index=True)
    areas = inp.areas
    w, txc, zflags = zones.zone_hub_weights(cfg, areas)
    flags += [dict(year="", flag=f[0], what=f[1], mmcf="", note=f"{f[2]:.1f} MW gas; {f[3]}") for f in zflags]
    z = w.merge(hubs, on="hub")
    z = z.assign(share=z.weight * z.share).groupby(["zone", "year", "basin", "method", "source_vintage"],
                                                   as_index=False).share.sum()
    z = z[["zone", "year", "basin", "share", "method", "source_vintage"]].sort_values(["zone", "year", "basin"])
    s = z.groupby(["zone", "year"]).share.sum()
    if (s.sub(1).abs() > 1e-9).any():
        raise AssertionError(f"zone shares do not sum to 1: {s[s.sub(1).abs() > 1e-9].head()}")
    z.to_csv(out / "zone_basin_shares.csv", index=False, float_format="%.6f")
    hubs[["hub", "year", "basin", "share", "throughput", "method", "source_vintage"]].rename(
        columns={"throughput": "throughput_mmcf"}).to_csv(out / "hub_basin_shares.csv", index=False, float_format="%.6f")
    w.to_csv(out / "zone_hub_weights.csv", index=False, float_format="%.6f")
    txc.to_csv(out / "tx_county_hubs.csv", index=False)
    pd.concat(reg, ignore_index=True).to_csv(out / "region_crosscheck.csv", index=False, float_format="%.6f")
    crosscheck.arcs(inp, base, {y: n for y, n in nets.items() if y != cfg["base_year"]}).to_csv(
        out / "table64_arcs_check.csv", index=False, float_format="%.1f")
    pd.concat(txr, ignore_index=True).to_csv(out / "texas_routing.csv", index=False, float_format="%.4f")
    pd.DataFrame(flags).to_csv(out / "flags.csv", index=False)
    tmpl = PKG / "data/basin_intensity.csv"
    if not tmpl.exists():
        rows = [(b, y, "", "", "") for b in basins for y in nets]
        pd.DataFrame(rows, columns=["basin", "year", "kg_ch4_per_mmbtu", "gwp_basis", "source"]).to_csv(tmpl, index=False)
    summ = {"years": list(nets), "zones": int(z.zone.nunique()), "hubs": len(nodes), "basins": basins,
            "aeo_datekey": inp.aeo_datekey, "flags": len(flags),
            "texas": {str(y): {"disposition_scale": round(n["texas"]["disposition_scale"], 4),
                               "capacity_slack_mmcf": round(n["texas"]["capacity_slack_mmcf"], 1)}
                      for y, n in nets.items()}}
    json.dump(summ, open(out / "run_summary.json", "w"), indent=1)
    log(f"zone_basin_shares.csv: {len(z)} rows, {summ['zones']} zones x {len(nets)} years; flags {len(flags)}")
    return {"zones": z, "hubs": hubs, "nets": nets, "flags": flags}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gasnet")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    sub.add_parser("pin")
    sub.add_parser("verify")
    sub.add_parser("run")
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
    elif a.cmd == "report":
        d = report.basin_gas_mmbtu(a.outputs_dir, a.shares, cfg["gas_fuels"], a.inputs_dir)
        print(f"wrote {Path(a.outputs_dir) / 'basin_gas_mmbtu.csv'} ({len(d)} rows)")


if __name__ == "__main__":
    main()
