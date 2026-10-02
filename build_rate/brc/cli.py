"""Command line for the build-rate pipeline (run from build_rate/).

    python -m brc.cli run                       # all tables -> outputs/
    python -m brc.cli patch-case <inputs_dir>   # add build_rate_*.csv to an existing Switch case
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

from . import data, rates

LEVELS = ("low", "central", "high", "reform", "high_ipm")


def load_cfg(path: str = "config.yaml") -> dict:
    cfg = yaml.safe_load(open(path))
    root = Path(path).resolve().parent
    cfg["paths"] = {k: str((root / v).resolve()) for k, v in cfg["paths"].items()}
    cfg["_root"] = str(root)
    return cfg


def check_inputs(cfg: dict):
    missing = [f"{k}: {v}" for k, v in cfg["paths"].items() if k != "outputs" and not Path(v).exists()]
    if missing:
        raise FileNotFoundError("Missing real inputs (run scripts/fetch_data.sh): " + "; ".join(missing))


def cmd_run(cfg: dict) -> dict:
    check_inputs(cfg)
    out = Path(cfg["paths"]["outputs"])
    out.mkdir(parents=True, exist_ok=True)
    c2t = data.county_transreg(cfg["paths"]["county2zone"], cfg["paths"]["hierarchy"])
    add = data.eia_additions(cfg["paths"]["eia860m"], c2t)
    h = cfg["history"]
    base = rates.base_rates(add, h["first_year"], h["last_year"])
    base.to_csv(out / "base_rates.csv", index=False)
    r0s = pd.DataFrame({rule: rates.r0(base, spec) for rule, spec in cfg["r0_rule"].items()})
    rates.check_r0_order(r0s)
    r0s.rename_axis("group").to_csv(out / "r0.csv")
    shares = rates.regional_shares(base, h["share_years"])
    shares.to_csv(out / "regional_shares.csv", index=False)
    comp = data.queue_components(data.load_queued_up(cfg["paths"]["queued_up"]), c2t)
    comp_rates = rates.completion_rates(comp, cfg)
    comp_rates.to_csv(out / "completion_rates.csv", index=False)
    delay = rates.cod_delay(comp, cfg)
    delay.to_csv(out / "cod_delay.csv")
    rates.overdue_summary(comp, delay, cfg).rename("overdue_mw").to_csv(out / "overdue_pipeline.csv")
    near = rates.near_term(comp, comp_rates, delay, cfg)
    near.to_csv(out / "near_term.csv", index=False)
    def tier_table(ts):
        return pd.concat([rates.tiers(cfg, g, ts).assign(group=g) for g in cfg["groups"]])
    tier_table(None).to_csv(out / "tiers.csv", index=False)       # default tier_set
    for lv in LEVELS:
        tier_table(rates.level_tier_set(cfg, lv)).to_csv(out / f"tiers_{lv}.csv", index=False)
    written, skipped = [], {}
    for lv in LEVELS:
        try:
            rates.rate_table(cfg, lv, base, near, shares).to_csv(out / f"rates_{lv}.csv", index=False)
            written.append(lv)
        except ValueError as e:
            skipped[lv] = str(e)
            (out / f"rates_{lv}.csv").unlink(missing_ok=True)
    match = add[add["year"].between(h["first_year"], h["last_year"])]
    info = {"eia_mw_mapped_to_transreg": float(match.loc[match["transreg"].notna(), "mw"].sum() / match["mw"].sum()),
            "queue_mw_mapped_to_transreg": float(comp.loc[comp["transreg"].notna(), "mw"].sum() / comp["mw"].sum()),
            "levels_written": written, "levels_skipped": skipped}
    yaml.safe_dump(info, open(out / "run_info.yaml", "w"), sort_keys=False)
    return info


def cmd_patch_case(cfg: dict, inputs_dir: str, level: str, groups: list[str], regional: bool,
                   zone_map_csv: str | None, slack_cost: float | None, tables_dir: str | None) -> list[str]:
    """Write build_rate_*.csv (incl. the generator -> group mapping, build_rate_gens.csv) into an
    existing case's inputs folder from its own gen_info.csv, periods.csv, gen_build_costs.csv,
    gen_build_predetermined.csv and min/max cap files, without rebuilding the case."""
    from . import switch_case
    settings = {"build_rate": {"enabled": True, "level": level, "groups": groups, "regional": regional,
                               "tables_dir": tables_dir or str(Path(cfg["paths"]["outputs"])),
                               "ceiling_slack_cost": slack_cost}}
    if zone_map_csv:   # aggregated zones: columns ba, zone
        zm = pd.read_csv(zone_map_csv)
        settings["_zone_map"] = dict(zip(zm["ba"], zm["zone"]))
    files = switch_case.write_case_inputs(Path(inputs_dir), settings, cfg)
    gens = pd.read_csv(Path(inputs_dir) / "build_rate_gens.csv")
    print(f"wrote {', '.join(files)} in {inputs_dir}")
    print("generators by group: " + ", ".join(f"{g} {n}" for g, n in gens["br_gen_group"].value_counts().items()))
    if regional and "build_rate_zones.csv" not in files:
        print("note: no load zone mapped to a single transreg; regional ceilings not written "
              "(pass --zone-map for aggregated zones)")
    print("switch the module on: add `--include-module study_modules.build_rate` to the case's line in "
          "scenarios.txt (not needed if its module list already has study_modules.build_rate)")
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brc")
    ap.add_argument("command", choices=["run", "patch-case"])
    ap.add_argument("inputs_dir", nargs="?", help="patch-case: the case's Switch inputs folder")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--level", default="central")
    ap.add_argument("--groups", nargs="+", default=["wind_onshore", "solar", "storage"])
    ap.add_argument("--no-regional", action="store_true")
    ap.add_argument("--zone-map", help="CSV with columns ba, zone (only for aggregated zones)")
    ap.add_argument("--ceiling-slack-cost", type=float, help="$/kW; diagnostic slack above the ceiling")
    ap.add_argument("--tables-dir", help="pipeline outputs (default: this config's outputs)")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    if a.command == "run":
        info = cmd_run(cfg)
        print(yaml.safe_dump(info, sort_keys=False))
    elif a.command == "patch-case":
        if not a.inputs_dir:
            ap.error("patch-case needs the case's inputs folder")
        cmd_patch_case(cfg, a.inputs_dir, a.level, a.groups, not a.no_regional, a.zone_map,
                       a.ceiling_slack_cost, a.tables_dir)


if __name__ == "__main__":
    main()
