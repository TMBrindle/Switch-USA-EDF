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
existing gas plant in the zone), with reserve credit none (prm_gen_credit.csv) and interconnection weight 0
(ic_weights.csv), when those files exist. Gas-turbine cap membership follows the case's own cap coverage
(build_rate.gas_turbine_cap: `coverage`, or `allowance_coverage` under the cumulative_additions form that S0
production cases use): a linked generator joins gas_turbine_cap_gens.csv only if its category (GTC_CATEGORY) is
covered, at the weight the case gives new builds of that category.
The writer prints the scenario-line options (tracked_demands_scenario_options.txt).
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[1]
RULES = REPO / "tracked_demands" / "data" / "cap_coverage_rules.yml"
REINFORCEMENT = REPO / "interconnection_headroom" / "data" / "reference" / "reeds_reinforcement_by_zone.csv"
HIERARCHY = REPO / "hierarchy.csv"
BUILD_RATE_SETTINGS = REPO / "pg" / "settings" / "build_rate.yml"
S0_PRODUCTION_SETTINGS = REPO / "pg" / "settings" / "s0_production.yml"
UC_DEFAULTS = REPO / "tracked_demands" / "data" / "onsite_uc_defaults.yml"
# Switch's unit-commitment and ramp columns (gen_info.csv), set from the spec or onsite_uc_defaults.yml, never copied
# from a template plant
UC_FIELDS = ("min_load_fraction", "min_uptime", "min_downtime", "startup_om", "startup_fuel",
             "ramp_limit_up", "ramp_limit_down")
TECH_PRIME_MOVER = {"recip": "recip", "diesel": "recip", "aero": "turbine", "industrial_ct": "turbine",
                    "frame_ct": "turbine", "cc": "turbine", "fuel_cell": "fuel_cell"}
# technology -> gas-turbine cap category (build_rate/brc/turbine_cap.py CLASSES); None never counts. Small
# industrial CTs (Titan 130 class) count with aeroderivatives (Tom, 12 Oct 2026)
GTC_CATEGORY = {"frame_ct": "combustion_turbine", "cc": "combined_cycle", "aero": "aeroderivative",
                "industrial_ct": "aeroderivative", "recip": "reciprocating_engine", "diesel": None, "fuel_cell": None}
# technology -> S0 gas capex premium class (s0_production.gas_capex.premium_paths: ct, cc); None: no premium. A
# spec's `premium_class` (ct | cc | none) or `premium` (a fraction, or a {year: fraction} table) overrides it
PREMIUM_CLASS = {"aero": "ct", "industrial_ct": "ct", "frame_ct": "ct", "cc": "cc",
                 "recip": None, "diesel": None, "fuel_cell": None, "battery": None}

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


# --------------------------------------------------------------------------------------------- case files
def variants(folder: Path, fname: str) -> list[Path]:
    """The base file and every <stem>.chained.<case>.csv of it in folder: a chained stage reads the chained
    version (pg_to_switch's --input-aliases), so the writer edits all of them alike."""
    stem = fname[:-4]
    out = [folder / fname] if (folder / fname).exists() else []
    return out + sorted(folder.glob(f"{stem}.chained.*.csv"))


def add_rows(folder: Path, fname: str, rows: list[dict], n_index: int, log, replace: bool = False,
             create: tuple | None = None) -> None:
    """Append rows to fname and each chained variant, using each file's own column names.

    rows use logical keys "_i0", "_i1", ... for the index columns, which take the file's first n_index column
    names (e.g. GENERATION_PROJECT / BUILD_YEAR or build_year, whatever the case wrote); other keys are Switch's
    parameter column names and are added as columns when the file lacks them. Rows whose index is already in the
    file are left alone (a stage folder that already carries them), or replaced with replace=True. create: the
    index column names for a file that doesn't exist yet (None: leave a missing file missing)."""
    paths = variants(folder, fname)
    if not paths:
        if not create:
            return
        paths = [folder / fname]
    for path in paths:
        df = _read(path) if path.exists() else None
        if df is None:
            cols = [k for k in rows[0] if not k.startswith("_i")]
            df = pd.DataFrame(columns=list(create) + cols)
        idx = list(df.columns[:n_index])
        new = pd.DataFrame(rows).rename(columns={f"_i{i}": c for i, c in enumerate(idx)})
        for c in new.columns:
            if c not in df.columns:
                df[c] = float("nan")
                log(f"{path.name}: added column {c}")
        new = new.reindex(columns=df.columns)
        keys = set(map(tuple, df[idx].astype(str).values))
        dup = new[idx].astype(str).apply(tuple, axis=1).isin(keys)
        if replace:
            drop = df[idx].astype(str).apply(tuple, axis=1).isin(set(map(tuple, new[idx].astype(str).values)))
            df = df[~drop]
        elif dup.any():
            log(f"{path.name}: {int(dup.sum())} row(s) already there, kept as they are")
            new = new[~dup]
        _write(pd.concat([df, new], ignore_index=True), path)


def uc_settings(g: dict, log) -> tuple[dict, str, str]:
    """(UC fields, technology, prime mover) of a linked generator: the spec's `uc:` values, else the technology's
    defaults (onsite_uc_defaults.yml), each default or gap logged as a WARNING."""
    defaults = yaml.safe_load(UC_DEFAULTS.read_text())
    tech = g.get("technology")
    pm = g.get("prime_mover")
    if tech is None:
        if pm == "recip":
            tech = "diesel" if str(g.get("fuel", "gas")).lower().startswith("diesel") or \
                str(g.get("fuel", "gas")).lower().startswith("distillate") else "recip"
        elif pm == "turbine":
            tech = "aero"
            log(f"WARNING: {g['name']}: no technology given for a turbine; assumed aero (set technology: frame_ct "
                f"for a frame turbine)")
        elif pm == "fuel_cell":
            tech = "fuel_cell"
    if tech is not None and tech not in defaults:
        raise ValueError(f"case_writer: technology {tech!r} of {g['name']} (one of {sorted(defaults)})")
    pm = pm or TECH_PRIME_MOVER.get(tech, "other")
    d = defaults.get(tech, {})
    spec_uc = g.get("uc") or {}
    out, missing = {}, []
    for f in UC_FIELDS:
        if f in spec_uc and spec_uc[f] is not None:
            out[f] = float(spec_uc[f])
        elif f in d:
            out[f] = float(d[f])
            missing.append(f"{f}={d[f]:g} (default)")
        else:
            missing.append(f"{f} unset (Switch default)")
    if missing:
        log(f"WARNING: {g['name']}: unit commitment not in the spec, using {tech or 'no technology'} defaults "
            f"[{d.get('source', 'none')}]: " + ", ".join(missing))
    return out, tech or "", pm


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


# --------------------------------------------------------------------------------------------- gas-turbine cap
def gtc_case_settings(out: Path, spec: dict, log) -> dict | None:
    """The case's gas-turbine cap settings as turbine_cap resolved them (None: no cap in the case).

    build_rate.gas_turbine_cap from the spec's `build_rate_settings` file (default pg/settings/build_rate.yml),
    with the spec's own `gas_turbine_cap:` keys over it and the case's form (gas_turbine_cap_params.csv), so an S0
    production case (form cumulative_additions) takes allowance_coverage. The classes already in the case's
    gas_turbine_cap_gens.csv must all be covered, else the settings aren't the ones the case was built with."""
    if not (out / "gas_turbine_cap.csv").exists() or not (out / "gas_turbine_cap_gens.csv").exists():
        return None
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from build_rate.brc.turbine_cap import gtc_settings
    path = Path(spec.get("build_rate_settings", BUILD_RATE_SETTINGS))
    base = ((yaml.safe_load(path.read_text()) or {}).get("build_rate") or {}).get("gas_turbine_cap") or {}
    gtc = {**base, **(spec.get("gas_turbine_cap") or {}), "enabled": True}
    params = out / "gas_turbine_cap_params.csv"
    if "form" not in (spec.get("gas_turbine_cap") or {}) and params.exists():
        gtc["form"] = str(_read(params)["gtc_form"].iat[0])
    cfg = gtc_settings({"build_rate": {"gas_turbine_cap": gtc}})
    present = set(_read(out / "gas_turbine_cap_gens.csv")["gtc_class"].dropna().astype(str))
    if present - set(cfg["coverage"]):
        raise ValueError(f"case_writer: the case's gas_turbine_cap_gens.csv has class(es) "
                         f"{sorted(present - set(cfg['coverage']))} outside the coverage {cfg['coverage']} read from "
                         f"{path} (form {cfg['form']}); give the spec the case's build_rate_settings or "
                         f"gas_turbine_cap.coverage")
    log(f"gas-turbine cap: form {cfg['form']}, coverage {cfg['coverage']} ({path.name})")
    return cfg


def gtc_new_build_weight(cfg: dict, cat: str) -> float:
    """The weight turbine_cap.write_case_inputs gives a new build of category `cat`."""
    if cfg["form"] == "cumulative_additions":
        f = (cfg["capacity_basis"] or {}).get("new_build_to_allowance")
        f = float(f.get(cat, 1.0)) if isinstance(f, dict) else float(1.0 if f is None else f)
        return float((cfg["class_weights"] or {}).get(cat, 1.0)) * f
    if cat == "combined_cycle" and cfg["cc_accounting"] == "turbine_share":
        return float(cfg["cc_turbine_share"])
    return 1.0


# --------------------------------------------------------------------------------------------- gas capex premium
def gas_capex_settings(out: Path, spec: dict, log) -> dict:
    """The case's s0_production.gas_capex, as the case build used it: the spec's `s0_production_settings` file
    (default pg/settings/s0_production.yml), with the spec's `gas_capex:` keys over it.

    The case build logs its gas-capex step in the case's s0_production_log.txt; that line is checked against the
    settings (mode, path, year basis, and each logged premium). No log: the case wasn't built with s0_production,
    so there is no premium unless the spec's `gas_capex:` sets one (warned)."""
    path = Path(spec.get("s0_production_settings", S0_PRODUCTION_SETTINGS))
    base = ((yaml.safe_load(path.read_text()) or {}).get("s0_production") or {}).get("gas_capex") or {}
    over = spec.get("gas_capex") or {}
    gc = {**base, **over, "_source": path.name + (" + spec" if over else "")}
    case_log = out / "s0_production_log.txt"
    lines = [ln for ln in case_log.read_text().splitlines() if ln.startswith("gas capex:")] \
        if case_log.exists() else []
    if not lines:
        if not over:
            gc = {"mode": "none", "_source": "no s0_production_log.txt in the case"}
        else:
            log(f"WARNING: the case has no s0_production gas-capex log line; using the spec's gas_capex {over}")
        return gc
    last = lines[-1]
    m = re.match(r"gas capex: ATB Moderate \+ (\w+) premium \((\w+)\)", last)
    case_mode = "premium" if m else last.split(":", 1)[1].split()[0]
    mode = gc.get("mode", "atb_moderate")
    if case_mode != mode or (m and (m.group(1), m.group(2)) != (gc.get("path", "central"),
                                                                 gc.get("year_basis", "span_mean"))):
        msg = (f"gas capex settings ({mode}, path {gc.get('path')}, {gc.get('year_basis')}; {gc['_source']}) don't "
               f"match the case's build: {last!r}")
        if not over:
            raise ValueError(f"case_writer: {msg}; give the spec the case's s0_production_settings or gas_capex")
        log(f"WARNING: {msg}; the spec's gas_capex is used (a sensitivity: linked generators differ from the "
            f"case's own gas plant)")
        return gc
    gc["_case_logged"] = {(c.lower(), int(y)): float(f) / 100
                          for c, y, f in re.findall(r"\b(CC|CT) (\d{4}) \+([\d.]+)%", last)}
    return gc


def gas_capex_premium(gc: dict, g: dict, techname: str, per: pd.DataFrame, log) -> dict:
    """{period: premium fraction} for a linked generator: the case's premium path for its premium class, averaged
    over each period as the case build did (span_mean or period_label); a spec `premium` overrides the path."""
    from s0_workflow.production import premium_for_period
    cls = g.get("premium_class", PREMIUM_CLASS.get(techname))
    cls = None if cls in (None, "none") else str(cls)
    if cls not in (None, "ct", "cc"):
        raise ValueError(f"case_writer: premium_class of {g['name']} must be ct, cc or none, not {cls!r}")
    basis = gc.get("year_basis", "span_mean")
    if g.get("premium") is not None:
        p = g["premium"]
        table = p if isinstance(p, dict) else {int(per.index.min()): float(p)}
        what = f"spec premium {p}"
    elif gc.get("mode") == "premium" and cls is not None:
        tables = (gc.get("premium_paths") or {}).get(gc.get("path", "central"))
        if not tables:
            raise ValueError(f"case_writer: s0_production.gas_capex.premium_paths has no path {gc.get('path')!r}")
        table = tables[cls]
        what = f"{gc.get('path', 'central')} {cls.upper()} path"
    else:
        log(f"{g['name']}: no gas capex premium (mode {gc.get('mode')}, premium class {cls or 'none'})")
        return {}
    out = {int(y): premium_for_period(table, r["period_start"], r["period_end"], basis) for y, r in per.iterrows()}
    for y, f in out.items():
        logged = (gc.get("_case_logged") or {}).get((cls, y))
        if g.get("premium") is None and logged is not None and abs(f - logged) > 0.0006:   # logged to 0.1%
            raise ValueError(f"case_writer: {g['name']} {cls.upper()} premium {f:.1%} in {y} differs from the "
                             f"case's {logged:.1%} (s0_production_log.txt)")
    log(f"{g['name']}: gas capex premium ({what}, {basis}, premium class {cls or 'none'}): "
        + ", ".join(f"{y} +{f:.1%}" for y, f in out.items()))
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
    gtc_cfg = gtc_case_settings(out, spec, log)
    gas_capex = gas_capex_settings(out, spec, log)
    per = _read(out / "periods.csv").set_index("INVESTMENT_PERIOD")
    log(f"gas capex: mode {gas_capex.get('mode')}" + (f", path {gas_capex.get('path', 'central')}, "
                                                       f"{gas_capex.get('year_basis', 'span_mean')}"
                                                       if gas_capex.get("mode") == "premium" else "")
        + f" ({gas_capex['_source']})")

    rows = {k: [] for k in ("td", "zones", "blocks", "targets", "techs", "subsidies", "caps", "storage", "links",
                            "gi", "bc", "credit", "prm", "gtc", "ic")}
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
        for t in [*(d.get("onsite_techs") or []), *([d["storage"]] if d.get("storage") else [])]:
            if t.get("premium") is not None or t.get("premium_class") not in (None, "none"):
                raise ValueError(f"case_writer: {name}: the gas capex premium applies to linked generators only "
                                 f"(onsite_gens), not to {t.get('name', 'storage')}")
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
            uc, techname, pm = uc_settings(g, log)
            # template for the remaining gen_info fields (outage rates, age...): the zone's plant of that fuel,
            # preferring one without CCS; CCS, storage and unit-commitment fields are never copied
            tmpl = gen_info[(gen_info["gen_load_zone"] == zone) & (gen_info["gen_energy_source"] == fuel)]
            if "gen_ccs_capture_efficiency" in tmpl:
                tmpl = tmpl.sort_values("gen_ccs_capture_efficiency", na_position="first")
            row = {k: v for k, v in (tmpl.iloc[0].to_dict() if len(tmpl) else {}).items()
                   if not (k.startswith(("gen_ccs", "gen_storage")) or k[4:] in UC_FIELDS)}
            row.pop(gen_info.columns[0], None)
            tech = g.get("gen_tech", f"TD_{techname or pm}")
            row.update({"_i0": gid, "gen_tech": tech, "gen_load_zone": zone,
                        "gen_energy_source": fuel, "gen_full_load_heat_rate": float(g["heat_rate"]),
                        "gen_variable_om": float(g.get("variable_om", 0.0)),
                        "gen_connect_cost_per_mw": 0.0,
                        "gen_capacity_limit_mw": (float(g["capacity_limit_mw"])
                                                  if g.get("capacity_limit_mw") is not None else float("nan")),
                        "gen_max_age": g.get("max_age", row.get("gen_max_age", 30)),
                        "gen_unit_size": float("nan"), "gen_min_build_capacity": 0,
                        "gen_is_variable": 0, "gen_is_baseload": 0, "gen_is_cogen": 0})
            if "gen_dbid" in gen_info.columns:
                row["gen_dbid"] = gid
            for f in UC_FIELDS:
                row["gen_" + f] = uc.get(f, float("nan"))
            rows["gi"].append(row)
            premium = gas_capex_premium(gas_capex, g, techname, per, log)
            for p in periods:
                rows["bc"].append({"_i0": gid, "_i1": p,
                                   "gen_overnight_cost": float(g["overnight_cost_per_kw"]) * 1000.0
                                   * (1 + premium.get(p, 0.0)),
                                   "gen_fixed_om": float(g.get("fixed_om_per_kw_yr", 0.0)) * 1000.0})
            cat = GTC_CATEGORY.get(techname) if techname else ("reciprocating_engine" if pm == "recip" else None)
            if techname and techname not in GTC_CATEGORY:
                raise ValueError(f"case_writer: technology {techname!r} of {gid} has no gas-turbine cap category")
            rows["links"].append({"TRACKED_DEMAND": name, "GENERATION_PROJECT": gid,
                                  "td_onsite_prime_mover": pm, "td_onsite_gtc_class": cat or "none",
                                  "td_onsite_unit_mw": g.get("unit_mw", "."),
                                  "td_onsite_rated_mw": g.get("rated_mw", g.get("capacity_limit_mw", "."))})
            if g.get("tax_credit_key"):
                for p, v in credit_by_period(out, g["tax_credit_key"], periods, credits_override, gen_info).items():
                    rows["credit"].append({"_i0": gid, "_i1": p, "gen_ptc_value_per_mwh": v})
            rows["prm"].append({"_i0": gid, "prm_credit": "none", "prm_class": "onsite"})
            if g.get("gtc_class") is not None or g.get("gtc_weight") is not None:
                log(f"WARNING: {gid}: the spec's gtc_class / gtc_weight are ignored; cap membership and weight follow "
                    f"the case's gas-turbine cap coverage")
            if gtc_cfg is not None:
                if cat in gtc_cfg["coverage"]:
                    rows["gtc"].append({"_i0": gid, "gtc_class": cat, "gtc_weight": gtc_new_build_weight(gtc_cfg, cat)})
                log(f"{name}: {gid} ({cat or 'no category'}) "
                    + ("in" if cat in gtc_cfg["coverage"] else "not in") + " the gas-turbine cap")
            rows["ic"].append(tech)
            log(f"{name}: linked generator {gid} ({techname or pm}, prime mover {pm}, fuel {fuel}, gen_tech {tech}, "
                f"UC " + ", ".join(f"{k}={v:g}" for k, v in uc.items()) + ")")

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
        # every file the case reads, base and chained (each with its own column names)
        add_rows(out, "gen_info.csv", rows["gi"], 1, log)
        add_rows(out, "gen_build_costs.csv", rows["bc"], 2, log)
        add_rows(out, "prm_gen_credit.csv", rows["prm"], 1, log)
        add_rows(out, "gas_turbine_cap_gens.csv", rows["gtc"], 1, log) if rows["gtc"] else None
        if gtc_cfg is not None:
            _write(pd.DataFrame({"gtc_class": list(gtc_cfg["coverage"])}), out / "tracked_demand_gtc_coverage.csv")
        if variants(out, "ic_weights.csv"):
            w = _read(variants(out, "ic_weights.csv")[0])
            add_rows(out, "ic_weights.csv", [{"_i0": t, w.columns[1]: 0.0} for t in sorted(set(rows["ic"]))], 1, log,
                     replace=True)
        if rows["credit"]:
            add_rows(out, "gen_tax_credits.csv", rows["credit"], 2, log, create=("GENERATION_PROJECT", "PERIOD"))

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
        for prog, entry in cov.items():
            if programmes and prog not in programmes:
                raise ValueError(f"case_writer: programme {prog!r} isn't in carbon_policies_regional.csv")
            # "ETS 1": RGGI, or "ETS 1": {family: RGGI, limited_export_exemption: 1, ...} to override the rule
            entry = {"family": entry} if isinstance(entry, str) else dict(entry)
            family = entry.pop("family")
            r = {**rules[family], **entry}
            if r.get("limited_export_exemption", 0) and not r.get("exemption_available", 0):
                log(f"WARNING: {prog}: limited-export exemption set, but {family}'s rule has none")
            log(f"{prog}: on-site coverage from the {family} rule, limited-export exemption "
                f"{'ON' if r.get('limited_export_exemption', 0) else 'off'}")
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
