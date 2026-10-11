"""Case-build writer for tracked demands (CHANGES §108 (g)).

Turns a spec (YAML) into the tracked-demand inputs for a built Switch case, written into a copy of its inputs
folder (default: <inputs-dir>_td), so the original case, and every other scenario reading it, is untouched:

    python -m tracked_demands.case_writer --inputs-dir switch/in/2035/s4x1_S0prod_2035 \\
        --spec tracked_demands/specs/dc_1gw_example.yml [--out-dir ...]

Everything is taken from the case where the case already says it:
- **time blocks** come from timepoints.csv and leave out stress hours (zero-weight timeseries, and those listed in
  prm_timeseries.csv or stress_light_timeseries.csv);
- **energy-source names** are the case's own: an on-site tech's `energy_source: solar` becomes the
  gen_energy_source the case uses for solar (e.g. `sun`), and a linked generator's `fuel: gas` becomes the gas fuel
  the case's existing gas plant in that zone burns;
- **tax credits** come from the case's gen_tax_credits.csv, which pg_to_switch wrote from `tax_credit_values`: an
  on-site tech or linked generator with `tax_credit_key: UtilityPV` takes, per period, the credit of the case's
  new-build projects whose technology contains that key (the same substring rule as pg_to_switch). A
  `tax_credit_values` block in the spec overrides it;
- **zone network-upgrade cost** for the grid connection: ReEDS conventional reinforcement, median $/kW by zone
  (interconnection_headroom/data/reference/reeds_reinforcement_by_zone.csv), unless the spec gives one.

Linked on-site generators are added to gen_info.csv and gen_build_costs.csv of the copy (their template: the case's
existing gas plant in the zone), with reserve credit none (prm_gen_credit.csv), interconnection weight 0
(ic_weights.csv) and gas-turbine-cap membership for turbines (gas_turbine_cap_gens.csv), when those files exist.
The writer prints the scenario-line options (tracked_demands_scenario_options.txt).
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[1]
RULES = REPO / "tracked_demands" / "data" / "cap_coverage_rules.yml"
REINFORCEMENT = REPO / "interconnection_headroom" / "data" / "reference" / "reeds_reinforcement_by_zone.csv"
HIERARCHY = REPO / "hierarchy.csv"

# spec names -> candidate names a case may use (case-insensitive); the first present in the case wins
SOURCE_ALIASES = {
    "solar": ("sun", "solar", "Solar"),
    "wind": ("wind", "Wind"),
    "geothermal": ("geothermal", "Geothermal"),
    "water": ("water", "Water", "hydro"),
}
FUEL_ALIASES = {
    "gas": ("naturalgas", "natural_gas", "NaturalGas", "Naturalgas", "gas"),
    "distillate": ("distillate", "DistillateFuelOil", "distillatefueloil"),
}


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, na_values=["."])


def _write(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, na_rep=".")


class Log(list):
    def __call__(self, msg):
        print(msg)
        self.append(msg)


# --------------------------------------------------------------------------------------------- names as Switch uses
def case_energy_source(gen_info: pd.DataFrame, name: str, zone: str | None = None) -> str:
    """The case's gen_energy_source for a spec name (e.g. solar -> sun)."""
    present = {str(s).lower(): s for s in gen_info["gen_energy_source"].dropna().unique()}
    for cand in SOURCE_ALIASES.get(name.lower(), (name,)):
        if cand.lower() in present:
            return present[cand.lower()]
    raise ValueError(f"case_writer: no generator in the case uses energy source {name!r} "
                     f"(looked for {SOURCE_ALIASES.get(name.lower(), (name,))}); give the case's name in the spec")


def case_fuel(gen_info: pd.DataFrame, fuels: pd.DataFrame, name: str, zone: str) -> str:
    """The case's fuel for a spec fuel name: the one the zone's existing plant of that fuel burns, else the only
    matching fuel in fuels.csv."""
    cands = [c.lower() for c in FUEL_ALIASES.get(name.lower(), (name,))]
    fuel_names = [str(f) for f in fuels["fuel"]]
    matches = [f for f in fuel_names if f.lower() in cands or any(f.lower().endswith("_" + c) for c in cands)]
    if not matches:
        raise ValueError(f"case_writer: no fuel in fuels.csv matches {name!r}")
    if len(matches) == 1:
        return matches[0]
    in_zone = gen_info[(gen_info["gen_load_zone"] == zone) & gen_info["gen_energy_source"].isin(matches)]
    used = in_zone["gen_energy_source"].value_counts()
    if len(used):
        return used.index[0]
    raise ValueError(f"case_writer: fuels {matches} all match {name!r} and no plant in {zone} burns one; "
                     f"give the case's fuel name in the spec")


# --------------------------------------------------------------------------------------------- time
def stress_timeseries(inp: Path) -> set:
    ts = _read(inp / "timeseries.csv")
    out = set(ts.loc[ts["ts_scale_to_period"] == 0, "TIMESERIES"])
    for f in ("prm_timeseries.csv", "stress_light_timeseries.csv"):
        if (inp / f).exists():
            out |= set(_read(inp / f).iloc[:, 0])
    return out


def time_blocks(inp: Path, td: str, matching: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(tracked_demand_time_blocks rows, tracked_demand_cfe_targets rows) for one TD, stress hours left out.

    matching.mode: hourly (one block per timepoint), annual (one per period) or hour_ranges ({block: [from, to)} by
    hour of day, read from the last two digits of the timestamp)."""
    tp = _read(inp / "timepoints.csv")
    ts = _read(inp / "timeseries.csv").set_index("TIMESERIES")
    tp = tp[~tp["timeseries"].isin(stress_timeseries(inp))].copy()
    tp["period"] = tp["timeseries"].map(ts["ts_period"]).astype("int64")
    mode = matching.get("mode", "hourly")
    if mode == "hourly":
        tp["time_block"] = "h" + tp["timepoint_id"].astype(str)
    elif mode == "annual":
        tp["time_block"] = "annual"
    elif mode == "hour_ranges":
        hours = tp["timestamp"].astype("int64") % 100
        tp["time_block"] = None
        for blk, (lo, hi) in matching["ranges"].items():
            sel = (hours >= lo) & (hours < hi) if lo < hi else (hours >= lo) | (hours < hi)
            tp.loc[sel & tp["time_block"].isna(), "time_block"] = blk
        if tp["time_block"].isna().any():
            raise ValueError(f"case_writer: hour_ranges leave hours {sorted(set(hours[tp['time_block'].isna()]))} "
                             f"without a block")
    else:
        raise ValueError(f"case_writer: matching mode {mode!r} (hourly, annual or hour_ranges)")
    blocks = pd.DataFrame({"TRACKED_DEMAND": td, "time_block": tp["time_block"], "TIMEPOINT": tp["timepoint_id"]})
    targets = tp[["period", "time_block"]].drop_duplicates().rename(columns={"period": "PERIOD"})
    targets.insert(0, "TRACKED_DEMAND", td)
    tgt = matching.get("target", 0.9)
    targets["td_cfe_target"] = targets["PERIOD"].map(tgt).fillna(tgt) if isinstance(tgt, dict) else tgt
    targets["td_cfe_shortfall_penalty"] = matching.get("penalty", 0.0)
    return blocks, targets


# --------------------------------------------------------------------------------------------- tax credits
def credit_by_period(inp: Path, key: str, periods, override: dict | None, gen_info: pd.DataFrame) -> dict:
    """{period: $/MWh} for technology key: from the spec's tax_credit_values when given (flat or by period), else
    from the case's gen_tax_credits.csv (new builds whose gen_tech contains key)."""
    if override:
        out = {}
        for p in periods:
            vals = override.get(p, override.get(str(p), override))
            v = sum(float(x) for k, x in vals.items() if isinstance(x, (int, float)) and k.lower() in key.lower()) \
                if isinstance(vals, dict) else 0.0
            out[p] = v
        return out
    f = inp / "gen_tax_credits.csv"
    if not f.exists():
        return {p: 0.0 for p in periods}
    tc = _read(f)
    techs = gen_info.set_index("GENERATION_PROJECT")["gen_tech"].astype(str)
    tc["gen_tech"] = tc["GENERATION_PROJECT"].map(techs)
    tc = tc[tc["gen_tech"].str.lower().str.contains(key.lower(), regex=False)]
    out = {}
    for p in periods:
        v = tc.loc[tc["PERIOD"] == p, "gen_ptc_value_per_mwh"].dropna().unique()
        if len(v) > 1:
            raise ValueError(f"case_writer: projects matching {key!r} carry different credits in {p}: {sorted(v)}")
        out[p] = float(v[0]) if len(v) else 0.0
    return out


# --------------------------------------------------------------------------------------------- writer
def write_case_inputs(inputs_dir, spec, out_dir=None, log=None) -> Path:
    inp = Path(inputs_dir)
    out = Path(out_dir) if out_dir else inp.parent / (inp.name + "_td")
    log = log if log is not None else Log()
    spec = yaml.safe_load(Path(spec).read_text()) if not isinstance(spec, dict) else spec
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(inp, out)
    gen_info = _read(out / "gen_info.csv")
    fuels = _read(out / "fuels.csv") if (out / "fuels.csv").exists() else pd.DataFrame({"fuel": []})
    periods = [int(p) for p in _read(out / "periods.csv")["INVESTMENT_PERIOD"]]
    zones = set(_read(out / "load_zones.csv")["LOAD_ZONE"])
    credits_override = spec.get("tax_credit_values")

    rows = {k: [] for k in ("td", "zones", "blocks", "targets", "techs", "subsidies", "caps", "storage", "links",
                            "gi", "bc", "credit", "prm", "gtc")}
    for d in spec["tracked_demands"]:
        name, zone = d["name"], d["zone"]
        if zone not in zones:
            raise ValueError(f"case_writer: zone {zone} of {name} isn't in the case")
        mx = float(d["max_mw"])
        mn = float(d.get("min_mw", mx))
        r = {"TRACKED_DEMAND": name, "td_type": d.get("type", "data_center"),
             "td_energy_requirement_mwh_per_year": float(d.get("annual_energy_share", 0.95)) * mx * 8760,
             "td_default_min_power_mw": mn, "td_default_max_power_mw": mx,
             "td_grid_interconnect_mw": float(d.get("connection_mw", mx)),
             "td_stress_mode": d.get("stress_mode", "max"),
             "td_stress_curtailable_share": float(d.get("curtailable_share", 0.0)),
             "td_export_limit_share": d.get("export_limit_share", ".")}
        c = d.get("connection", {}) or {}
        r.update({"td_connection_cost_multiplier": float(c.get("cost_multiplier", 1.0)),
                  "td_connection_cost_per_kw": c.get("cost_per_kw", "."),
                  "td_connection_cost_share_demand": float(c.get("cost_share_demand", 1.0)),
                  "td_connection_endogenous": int(bool(c.get("endogenous", False))),
                  "td_connection_uses_headroom": int(bool(c.get("uses_headroom", False))),
                  "td_headroom_weight": float(c.get("headroom_weight", 1.0))})
        rows["td"].append(r)
        rows["zones"].append({"TRACKED_DEMAND": name, "LOAD_ZONE": zone})
        if d.get("matching"):
            b, t = time_blocks(out, name, d["matching"])
            rows["blocks"].append(b)
            rows["targets"].append(t)
            log(f"{name}: {len(b)} timepoints in {t['time_block'].nunique()} blocks "
                f"({d['matching'].get('mode', 'hourly')}), stress hours left out")
        for t in d.get("onsite_techs", []) or []:
            src = case_energy_source(gen_info, t["energy_source"]) if t.get("energy_source") else "."
            rows["techs"].append({"td_onsite_tech": t["name"],
                                  "td_onsite_tech_capital_cost": float(t["capital_cost_per_mw_yr"]),
                                  "td_onsite_tech_variable_om": float(t.get("variable_om", 0.0)),
                                  "td_onsite_tech_is_clean": int(bool(t.get("clean", True))),
                                  "td_onsite_tech_cf_source": src})
            if t.get("tax_credit_key"):
                for p, v in credit_by_period(out, t["tax_credit_key"], periods, credits_override, gen_info).items():
                    rows["subsidies"].append({"td_onsite_tech": t["name"], "PERIOD": p,
                                              "td_onsite_tech_subsidy_by_period": v})
            if t.get("max_mw") is not None:
                rows["caps"].append({"TRACKED_DEMAND": name, "td_onsite_tech": t["name"],
                                     "td_onsite_build_mw_cap": float(t["max_mw"])})
            log(f"{name}: on-site {t['name']} (energy source {src})")
        if d.get("storage"):
            s = d["storage"]
            rows["storage"].append({"TRACKED_DEMAND": name,
                                    "td_storage_power_cost": float(s["power_cost_per_mw_yr"]),
                                    "td_storage_energy_cost": float(s["energy_cost_per_mwh_yr"]),
                                    "td_storage_max_hours": float(s.get("max_hours", 8)),
                                    "td_storage_roundtrip_eff": float(s.get("roundtrip_eff", 0.85))})
        for g in d.get("onsite_gens", []) or []:
            gid = g["name"]
            fuel = case_fuel(gen_info, fuels, g.get("fuel", "gas"), zone)
            tmpl = gen_info[(gen_info["gen_load_zone"] == zone) & (gen_info["gen_energy_source"] == fuel)]
            row = (tmpl.iloc[0].copy() if len(tmpl) else pd.Series({c: float("nan") for c in gen_info.columns},
                                                                       dtype=object))
            tech = g.get("gen_tech", f"TD_{g.get('prime_mover', 'other')}")
            fields = {"GENERATION_PROJECT": gid, "gen_tech": tech, "gen_load_zone": zone,
                        "gen_energy_source": fuel, "gen_full_load_heat_rate": float(g["heat_rate"]),
                        "gen_variable_om": float(g.get("variable_om", 0.0)),
                        "gen_connect_cost_per_mw": 0.0, "gen_capacity_limit_mw": g.get("capacity_limit_mw"),
                        "gen_max_age": g.get("max_age", row.get("gen_max_age", 30)),
                        "gen_unit_size": float("nan"), "gen_min_build_capacity": 0,
                        "gen_is_variable": 0, "gen_is_baseload": 0, "gen_dbid": gid}
            for k, v in fields.items():          # assigned one by one: Series.update would skip NaN (unit size)
                if k in row.index:
                    row[k] = float("nan") if v is None else v
            rows["gi"].append(row)
            cost_cols = _read(out / "gen_build_costs.csv").columns
            for p in periods:
                b = {c: float("nan") for c in cost_cols}
                b.update({"GENERATION_PROJECT": gid, "build_year": p,
                          "gen_overnight_cost": float(g["overnight_cost_per_kw"]) * 1000.0,
                          "gen_fixed_om": float(g.get("fixed_om_per_kw_yr", 0.0)) * 1000.0})
                rows["bc"].append(b)
            rows["links"].append({"TRACKED_DEMAND": name, "GENERATION_PROJECT": gid,
                                  "td_onsite_prime_mover": g.get("prime_mover", "other"),
                                  "td_onsite_unit_mw": g.get("unit_mw", "."),
                                  "td_onsite_rated_mw": g.get("rated_mw", g.get("capacity_limit_mw", "."))})
            if g.get("tax_credit_key"):
                for p, v in credit_by_period(out, g["tax_credit_key"], periods, credits_override, gen_info).items():
                    rows["credit"].append({"GENERATION_PROJECT": gid, "PERIOD": p, "gen_ptc_value_per_mwh": v})
            rows["prm"].append({"GENERATION_PROJECT": gid, "prm_credit": "none", "prm_class": "onsite"})
            if g.get("prime_mover") == "turbine":
                rows["gtc"].append({"GENERATION_PROJECT": gid, "gtc_class": g.get("gtc_class", "ct"),
                                    "gtc_weight": float(g.get("gtc_weight", 1.0))})
            log(f"{name}: linked generator {gid} ({g.get('prime_mover', 'other')}, fuel {fuel})")

    def put(key, fname, cols=None):
        if rows[key]:
            df = pd.concat(rows[key]) if isinstance(rows[key][0], pd.DataFrame) else pd.DataFrame(rows[key])
            _write(df[cols] if cols else df, out / fname)

    put("td", "tracked_demands.csv")
    put("zones", "tracked_demand_candidate_zones.csv")
    put("blocks", "tracked_demand_time_blocks.csv")
    put("targets", "tracked_demand_cfe_targets.csv")
    put("techs", "tracked_demand_onsite_techs.csv")
    put("subsidies", "tracked_demand_onsite_tech_subsidies.csv")
    put("caps", "tracked_demand_onsite_build_caps.csv")
    put("storage", "tracked_demand_storage.csv")
    put("links", "tracked_demand_onsite_gens.csv")

    if rows["gi"]:
        gi = pd.concat([gen_info, pd.DataFrame(rows["gi"])[gen_info.columns]], ignore_index=True)
        _write(gi, out / "gen_info.csv")
        bc = _read(out / "gen_build_costs.csv")
        _write(pd.concat([bc, pd.DataFrame(rows["bc"])[bc.columns]], ignore_index=True), out / "gen_build_costs.csv")
        techs = sorted({r["gen_tech"] for r in rows["gi"]})
        if (out / "prm_gen_credit.csv").exists():
            pc = _read(out / "prm_gen_credit.csv")
            _write(pd.concat([pc, pd.DataFrame(rows["prm"])], ignore_index=True)[pc.columns], out / "prm_gen_credit.csv")
        if (out / "ic_weights.csv").exists():
            w = _read(out / "ic_weights.csv")
            key = w.columns[0]
            w = w[~w[key].isin(techs)]
            _write(pd.concat([w, pd.DataFrame({key: techs, w.columns[1]: 0.0})], ignore_index=True),
                   out / "ic_weights.csv")
        if rows["gtc"] and (out / "gas_turbine_cap_gens.csv").exists():
            g = _read(out / "gas_turbine_cap_gens.csv")
            _write(pd.concat([g, pd.DataFrame(rows["gtc"])], ignore_index=True)[g.columns],
                   out / "gas_turbine_cap_gens.csv")
        if rows["credit"]:
            f = out / "gen_tax_credits.csv"
            tc = _read(f) if f.exists() else pd.DataFrame(columns=["GENERATION_PROJECT", "PERIOD",
                                                                    "gen_ptc_value_per_mwh"])
            _write(pd.concat([tc, pd.DataFrame(rows["credit"])], ignore_index=True), f)

    # zone network-upgrade cost for the connection
    zcost = spec.get("zone_upgrade_cost_per_kw") or {}
    need = sorted({d["zone"] for d in spec["tracked_demands"]} - set(zcost))
    if need and REINFORCEMENT.exists():
        ref = pd.read_csv(REINFORCEMENT).set_index("ba")
        for z in need:
            if z in ref.index:
                zcost[z] = float(ref.at[z, "reinforcement_median_per_kw"])
                log(f"zone {z}: network upgrade {zcost[z]:.2f} $/kW ({int(ref.at[z, 'dollar_year'])}$, ReEDS median)")
            else:
                log(f"zone {z}: no ReEDS reinforcement cost (aggregated zone?); give zone_upgrade_cost_per_kw")
    if zcost:
        _write(pd.DataFrame({"LOAD_ZONE": list(zcost), "td_zone_upgrade_cost_per_kw": list(zcost.values())}),
               out / "tracked_demand_zone_upgrade_costs.csv")

    # carbon-cap coverage by programme
    cov = spec.get("cap_coverage") or {}
    if cov:
        rules = yaml.safe_load(RULES.read_text())
        programmes = set(_read(out / "carbon_policies_regional.csv")["CO2_PROGRAM"]) \
            if (out / "carbon_policies_regional.csv").exists() else set()
        crows, srows = [], []
        for prog, family in cov.items():
            if programmes and prog not in programmes:
                raise ValueError(f"case_writer: programme {prog!r} isn't in carbon_policies_regional.csv")
            r = rules[family]
            crows.append({"CO2_PROGRAM": prog, "td_cap_counts_onsite": r.get("counts_onsite", 1),
                          "td_cap_threshold_mw": r.get("threshold_mw", "."),
                          "td_cap_threshold_tco2": r.get("threshold_tco2", "."),
                          "td_cap_threshold_basis": r.get("basis", "unit"),
                          "td_cap_limited_export_exemption": r.get("limited_export_exemption", 0)})
            for st, sr in (r.get("states") or {}).items():
                srows.append({"CO2_PROGRAM": prog, "STATE": st, "td_cap_state_threshold_mw": sr["threshold_mw"],
                              "td_cap_state_condition": sr.get("condition", "always")})
        _write(pd.DataFrame(crows), out / "tracked_demand_cap_coverage.csv")
        if srows:
            _write(pd.DataFrame(srows), out / "tracked_demand_cap_coverage_states.csv")
            if HIERARCHY.exists():
                h = pd.read_csv(HIERARCHY, dtype=str)
                tdz = {d["zone"] for d in spec["tracked_demands"]}
                zs = h[h["ba"].isin(tdz)][["ba", "st"]].drop_duplicates()
                if len(zs):
                    _write(zs.rename(columns={"ba": "LOAD_ZONE", "st": "STATE"}), out / "tracked_demand_zone_states.csv")

    if spec.get("grid_clean_fraction") is not None:
        g = spec["grid_clean_fraction"]
        tp = _read(out / "timepoints.csv")["timepoint_id"]
        if isinstance(g, (int, float)):
            _write(pd.DataFrame([{"LOAD_ZONE": z, "TIMEPOINT": t, "grid_clean_fraction": float(g)}
                                 for z in sorted(zones) for t in tp]), out / "grid_clean_fraction.csv")
        else:
            src = _read(Path(g))
            src = src[src["TIMEPOINT"].isin(set(tp))][["LOAD_ZONE", "TIMEPOINT", "grid_clean_fraction"]]
            _write(src, out / "grid_clean_fraction.csv")

    opts = "--include-module study_modules.tracked_demands"
    (out / "tracked_demands_scenario_options.txt").write_text(
        opts + "\n# Put this before prm_regional's options (S0: s0_production.extra_modules), since prm_regional reads "
        "the tracked demands' reserve requirement.\n")
    (out / "tracked_demands_writer_log.txt").write_text("\n".join(log) + "\n")
    log(f"wrote {out} (scenario options: {opts})")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs-dir", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out-dir")
    a = ap.parse_args(argv)
    write_case_inputs(a.inputs_dir, a.spec, a.out_dir)


if __name__ == "__main__":
    main()
