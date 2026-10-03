"""S0 production case build: settings and case-input steps (pg/settings/s0_production.yml).

pg_to_switch.py calls this module when a case's settings have `s0_production.enabled` (turned on per
case by the `s0_production` column of scenario_inputs.csv). It replaces the hand steps of the S0 test
workflow (s0_workflow/README.md) with tracked settings:

  apply_state_policies(case_settings)  before the region scope: state RPS/CES files of the pinned
                                       ReEDS release (s0_production.state_policies.release)
  apply_settings(case_settings)        before PowerGenome builds anything: deep-merge
                                       s0_production.settings (build rate central, headroom atts_s0)
                                       and set the gas capex basis (ATB Moderate)
  write_case_inputs(folder, settings)  after the case is written: GridLab gas premium (optional),
                                       zonal coal CF caps, wind loss, the NY RPS buyout (ACP),
                                       headroom slack, the per-period retirement rule, period spans
  scenario_options(settings)           extra Switch modules for the case's scenario line
                                       (gen_amortization_period, retirement_rules)
  plan_stages(years, settings)         stages of a myopic chain (mode A) or of two-period windows
                                       (mode B), with the period each stage commits

Every step logs to <case>/s0_production_log.txt. Nothing here runs unless s0_production.enabled.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[1]
# ATB/ReEDS wind losses (13.4%) against the 1.7% already in the reV profiles
WIND_LOSS_FACTOR = (1 - 0.134) / (1 - 0.017)


# ---------------------------------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------------------------------
def s0_settings(settings: dict) -> dict | None:
    s = settings.get("s0_production") or {}
    return s if s.get("enabled") else None


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out.get(k) or {}, v) if isinstance(v, dict) and isinstance(out.get(k), (dict, type(None))) \
            else copy.deepcopy(v)
    return out


def neutralise_gas_capex_override(settings: dict) -> list[str]:
    """Make every atb_modifiers entry that sets an absolute NaturalGas capex_mw (the GridLab override in
    resources.yml) a no-op ([mul, 1.0]), so new CC/CT capex is ATB Moderate. Returns the entries changed."""
    changed = []
    for name, mod in (settings.get("atb_modifiers") or {}).items():
        if isinstance(mod, dict) and str(mod.get("technology")) == "NaturalGas" and \
                isinstance(mod.get("capex_mw"), (int, float)) and not isinstance(mod.get("capex_mw"), bool):
            mod["capex_mw"] = ["mul", 1.0]
            changed.append(name)
    return changed


def apply_settings(case_settings: dict) -> None:
    """In place, for every case/year with s0_production.enabled (call before any PowerGenome step)."""
    for case, years in case_settings.items():
        for year, s in years.items():
            s0 = s0_settings(s)
            if s0 is None:
                continue
            merged = deep_merge(s, s0.get("settings") or {})
            s.clear()
            s.update(merged)
            gc = s0.get("gas_capex") or {}
            mode = gc.get("mode", "atb_moderate")
            if mode in ("atb_moderate", "premium"):
                changed = neutralise_gas_capex_override(s)
                logger.info("s0_production %s/%s: gas capex basis ATB Moderate (override %s neutralised)",
                            case, year, changed)
            elif mode != "gridlab":
                raise ValueError(f"s0_production.gas_capex.mode must be premium, atb_moderate or gridlab, "
                                 f"not {mode!r}")
            gtc = s0.get("gas_turbine_cap") or {}
            form = gtc.get("form", "legacy")
            if form == "allowance":
                br = s.setdefault("build_rate", {})
                br["gas_turbine_cap"] = deep_merge(br.get("gas_turbine_cap") or {}, {
                    "enabled": True, "form": "cumulative_additions",
                    "allowance_path": gtc.get("path", "central")})
            elif form != "legacy":
                raise ValueError(f"s0_production.gas_turbine_cap.form must be allowance or legacy, not {form!r}")
            spans = s0.get("period_spans") or {}
            if int(year) in {int(k) for k in spans}:
                first = int({int(k): v for k, v in spans.items()}[int(year)][0])
                if int(s.get("model_first_planning_year", first)) != first:
                    raise ValueError(
                        f"s0_production.period_spans gives {year} a first year of {first} but the settings give "
                        f"model_first_planning_year {s.get('model_first_planning_year')} (model_definition.yml)")


CURRENT_POLICIES_FN = "rggi_carbon/emission_policies_current.csv"


def state_policy_doc(release: str) -> dict:
    """The S0 state-policy file for a pinned ReEDS release (s0_workflow/scripts/build_reeds_state_policies.py)."""
    import yaml
    path = REPO / "pg/extra_inputs/reeds_state_policies" / f"s0_state_policies_{release}.yml"
    if not path.exists():
        raise FileNotFoundError(f"s0_production.state_policies.release {release!r}: {path} not found; build it "
                                f"with s0_workflow/scripts/build_reeds_state_policies.py")
    return yaml.safe_load(open(path))


def apply_state_policies(case_settings: dict) -> None:
    """In place, for every S0 case/year whose s0_production.state_policies.release is a ReEDS release (not
    legacy): state RPS/CES targets from emission_policies_reeds_<release>.csv, and ESR eligibility
    (ESR_* regional tags and the ESR_* entries of model_tag_names / generator_columns) from
    s0_state_policies_<release>.yml. Called before the region scope is applied, so aggregated zones
    take these tags. Other cases, and legacy, keep the current policy files."""
    docs = {}
    for case, years in case_settings.items():
        for year, s in years.items():
            s0 = s0_settings(s)
            if s0 is None:
                continue
            release = str((s0.get("state_policies") or {}).get("release", "legacy"))
            if release == "legacy":
                continue
            doc = docs.get(release) or docs.setdefault(release, state_policy_doc(release))
            fn = s.get("emission_policies_fn")
            if fn != CURRENT_POLICIES_FN:
                raise ValueError(f"{case}/{year}: s0_production.state_policies.release {release} replaces "
                                 f"{CURRENT_POLICIES_FN}, but the case uses {fn!r}")
            s["emission_policies_fn"] = doc["emission_policies_fn"]
            # new dicts throughout: cases and years may share nested settings objects
            rtv = {r: {p: v for p, v in (progs or {}).items() if not str(p).startswith("ESR_")}
                   for r, progs in (s.get("regional_tag_values") or {}).items()}
            for r, progs in doc["regional_tag_values"].items():
                rtv[r] = {**rtv.get(r, {}), **copy.deepcopy(progs)}
            s["regional_tag_values"] = rtv
            for key in ("model_tag_names", "generator_columns"):
                tags = s.get(key)
                if not isinstance(tags, list) or {t for t in tags if str(t).startswith("ESR_")} == \
                        set(doc["esr_tags"]):
                    continue
                at = next((i for i, t in enumerate(tags) if str(t).startswith("ESR_")), len(tags))
                rest = [t for t in tags if not str(t).startswith("ESR_")]
                s[key] = rest[:at] + list(doc["esr_tags"]) + rest[at:]
            logger.info("s0_production %s/%s: state policies from ReEDS %s (%s)", case, year, release,
                        doc["emission_policies_fn"])


# ---------------------------------------------------------------------------------------------------
# foresight (item 8): myopic chain (mode A) or rolling two-period windows (mode B)
# ---------------------------------------------------------------------------------------------------
def foresight_mode(settings: dict) -> str:
    s0 = s0_settings(settings)
    if s0 is None:
        return "default"
    mode = (s0.get("foresight") or {}).get("mode", "single")
    if mode not in ("single", "myopic", "windows"):
        raise ValueError(f"s0_production.foresight.mode must be single, myopic or windows, not {mode!r}")
    return mode


def stage_name(years) -> str:
    return "_".join(str(y) for y in years)


def plan_stages(years, mode: str, window_periods: int = 2) -> list[dict]:
    """Stages for a chain. Each: {"name", "years", "commit_period", "next"}.

    myopic (A):  one stage per year; each commits its only period.
    windows (B): windows of `window_periods` consecutive years advancing one period at a time
                 (2028-30, 2030-35, 2035-40, 2040-45); each commits its first period, the last commits
                 all of its periods. The next stage starts from the committed fleet.
    single:      one stage with all years (perfect foresight), committing everything.
    """
    years = sorted(int(y) for y in years)
    if mode == "myopic":
        groups = [[y] for y in years]
    elif mode == "windows":
        n = max(1, int(window_periods))
        groups = [years[i:i + n] for i in range(0, max(1, len(years) - n + 1))] if len(years) >= n else [years]
    else:
        groups = [years]
    out = []
    for i, g in enumerate(groups):
        last = i == len(groups) - 1
        out.append({"name": stage_name(g) if mode == "windows" else (str(g[0]) if len(g) == 1 else "foresight"),
                    "years": g, "commit_period": g[-1] if last else g[0],
                    "next": None if last else (stage_name(groups[i + 1]) if mode == "windows" else str(groups[i + 1][0]))})
    return out


def write_stage_info(out_folder: Path, stage: dict) -> None:
    """stage_info.csv, read by study_modules.prepare_next_stage: which period this stage commits and
    the folder (sibling of this stage's year folder) of the next stage."""
    pd.DataFrame([{"stage": stage["name"], "commit_period": stage["commit_period"],
                   "next_stage": stage["next"] or "."}]).to_csv(Path(out_folder) / "stage_info.csv", index=False)


# ---------------------------------------------------------------------------------------------------
# case-input steps (items 2-4, 8)
# ---------------------------------------------------------------------------------------------------
class Log:
    def __init__(self, folder: Path):
        self.folder, self.lines = Path(folder), []

    def __call__(self, msg: str):
        logger.info("s0_production: %s", msg)
        self.lines.append(msg)

    def write(self):
        with open(self.folder / "s0_production_log.txt", "a") as f:
            f.write(f"\n{datetime.now().isoformat(timespec='seconds')}\n" + "\n".join(self.lines) + "\n")


def _read(folder: Path, name: str, **kw) -> pd.DataFrame:
    return pd.read_csv(Path(folder) / name, **kw)


def premium_fraction(table: dict, year: int) -> float:
    """Premium over ATB for an in-service year: the value of the latest key <= year (the first key's
    value for earlier years; keys are 'to year' breakpoints)."""
    t = {int(k): float(v) for k, v in table.items()}
    ks = [k for k in sorted(t) if k <= int(year)]
    return t[ks[-1]] if ks else t[min(t)]


def premium_for_period(table: dict, start: int, end: int, basis: str = "span_mean") -> float:
    """Premium for capacity built in a period: the mean over its in-service years (span_mean, the same
    averaging PowerGenome applies to ATB capex) or the value at the period's label year (period_label)."""
    if basis == "period_label":
        return premium_fraction(table, end)
    if basis != "span_mean":
        raise ValueError(f"s0_production.gas_capex.year_basis must be span_mean or period_label, not {basis!r}")
    return float(np.mean([premium_fraction(table, y) for y in range(int(start), int(end) + 1)]))


def gas_capex_class(gen_tech: str) -> str | None:
    """cc / ct for new-build ATB gas (CCS plants excluded), else None."""
    t = str(gen_tech)
    if not t.startswith("NaturalGas_") or "CCS" in t.upper():
        return None
    if "Combined Cycle" in t:
        return "cc"
    if "Combustion Turbine" in t:
        return "ct"
    return None


def apply_gas_capex(folder: Path, s0: dict, log: Log) -> None:
    """premium: new CC/CT overnight cost x (1 + premium) by in-service year (premium_paths[path]); ATB
    Moderate alone with atb_moderate (set in apply_settings); the resources.yml override with gridlab."""
    gc = s0.get("gas_capex") or {}
    mode = gc.get("mode", "atb_moderate")
    if mode != "premium":
        log(f"gas capex: {mode} (new CC/CT at {'ATB 2024 Moderate' if mode == 'atb_moderate' else 'the GridLab override'})")
        return
    path, basis = gc.get("path", "central"), gc.get("year_basis", "span_mean")
    tables = (gc.get("premium_paths") or {}).get(path)
    if not tables:
        raise ValueError(f"s0_production.gas_capex.premium_paths has no path {path!r}")
    per = _read(folder, "periods.csv").set_index("INVESTMENT_PERIOD")
    bc = _read(folder, "gen_build_costs.csv", dtype=str, keep_default_na=False)
    tech = _read(folder, "gen_info.csv", usecols=["GENERATION_PROJECT", "gen_tech"]).set_index("GENERATION_PROJECT")["gen_tech"]
    pre = _read(folder, "gen_build_predetermined.csv")
    pre_keys = set(zip(pre.iloc[:, 0].astype(str), pre.iloc[:, 1].astype(float).astype(int)))
    by = bc.iloc[:, 1].astype(float).astype(int)
    cls = bc["GENERATION_PROJECT"].map(tech).map(gas_capex_class)
    new = ~pd.Series([(g, y) in pre_keys for g, y in zip(bc["GENERATION_PROJECT"], by)], index=bc.index)
    applied = {}
    for i in bc.index[cls.notna() & new & by.isin(per.index)]:
        y = int(by[i])
        f = premium_for_period(tables[cls[i]], per.at[y, "period_start"], per.at[y, "period_end"], basis)
        bc.at[i, "gen_overnight_cost"] = repr(float(bc.at[i, "gen_overnight_cost"]) * (1 + f))
        applied[(cls[i], y)] = f
    bc.to_csv(Path(folder) / "gen_build_costs.csv", index=False)
    log(f"gas capex: ATB Moderate + {path} premium ({basis}): "
        + ", ".join(f"{c.upper()} {y} +{f:.1%}" for (c, y), f in sorted(applied.items())))


def apply_coal_cf_caps(folder: Path, s0: dict, settings: dict, log: Log) -> None:
    cc = s0.get("coal_cf_caps") or {}
    if not cc.get("enabled"):
        return
    table = pd.read_csv(REPO / cc.get("table", "s0_workflow/data/coal_cf_caps_eia923_2021_2024.csv"), comment="#")
    min_mw, fallback = float(cc.get("min_history_mw", 500)), float(cc.get("fallback_cf", 0.65))
    gi = _read(folder, "gen_info.csv", dtype=str, keep_default_na=False)
    zone_map = settings.get("_zone_map") or {}
    members = {}
    for ba, z in zone_map.items():
        members.setdefault(z, []).append(ba)
    t = table.set_index("ba")

    def zone_cap(z):
        bas = [b for b in members.get(z, [z]) if b in t.index]
        mw = float(t.loc[bas, "hist_mw"].sum()) if bas else 0.0
        if mw < min_mw:
            return fallback, "fallback"
        return float(np.average(t.loc[bas, "cap_cf"], weights=t.loc[bas, "hist_mw"])), "EIA-923"

    m = gi["gen_energy_source"].str.lower() == "coal"
    if "gen_max_annual_availability" not in gi:
        gi["gen_max_annual_availability"] = "."
    caps = gi.loc[m, "gen_load_zone"].map(lambda z: zone_cap(z)[0])
    src = gi.loc[m, "gen_load_zone"].map(lambda z: zone_cap(z)[1])
    forced = pd.to_numeric(gi.loc[m, "gen_forced_outage_rate"], errors="coerce").fillna(0)
    avail = (caps / (1 - forced)).clip(upper=1.0)
    gi.loc[m, "gen_max_annual_availability"] = avail.round(6).map(repr)
    gi.to_csv(Path(folder) / "gen_info.csv", index=False)
    log(f"coal CF caps: {int(m.sum())} coal clusters, gen_max_annual_availability = zone cap / (1 - forced) "
        f"(capped at 1); {int((src == 'fallback').sum())} at the {fallback} fallback (< {min_mw:.0f} MW history); "
        f"table {cc.get('table')}")


def wind_kinds(gen_tech: pd.Series) -> pd.Series:
    t = gen_tech.astype(str)
    return pd.Series(np.select([t.str.startswith("LandbasedWind") | (t == "Onshore Wind Turbine"),
                                t.str.startswith("OffShoreWind") | (t == "Offshore Wind Turbine")],
                               ["onshore", "offshore"], default=""), index=gen_tech.index)


def apply_wind_loss(folder: Path, s0: dict, log: Log) -> None:
    wl = s0.get("wind_loss") or {}
    if not wl.get("enabled"):
        return
    f = float(wl.get("factor", WIND_LOSS_FACTOR))
    kinds = [k for k in ("onshore", "offshore") if wl.get(k, True)]
    gi = _read(folder, "gen_info.csv", usecols=["GENERATION_PROJECT", "gen_tech"])
    k = wind_kinds(gi["gen_tech"])
    gens = set(gi.loc[k.isin(kinds), "GENERATION_PROJECT"])
    v = _read(folder, "variable_capacity_factors.csv", dtype=str, keep_default_na=False)
    col = v.columns[2]
    m = v["GENERATION_PROJECT"].isin(gens)
    v.loc[m, col] = (v.loc[m, col].astype(float) * f).map(repr)
    v.to_csv(Path(folder) / "variable_capacity_factors.csv", index=False)
    log(f"wind loss: {'+'.join(kinds)} wind profiles x {f:.5f}; {int(m.sum())} rows, {len(gens)} generators")


def apply_rps_acp(folder: Path, s0: dict, log: Log) -> None:
    """State policy buyouts (rps_acp_per_mwh in rps_requirements.csv, study_modules.rps_regional).

    mode flat (S0 default): `price` ($/MWh) on every state RPS and CES program (RPS_PROGRAM starting
      with `program_prefix`, ESR_ by default; carve-outs included), replacing any per-program value
    mode programs (legacy): only the programs listed in `programs` ({program: $/MWh}); others keep
      what the case had ('.' = hard requirement)
    """
    ra = s0.get("rps_acp") or {}
    if not ra.get("enabled"):
        return
    p = Path(folder) / "rps_requirements.csv"
    if not p.exists():
        log("rps acp: no rps_requirements.csv; skipped")
        return
    raw = pd.read_csv(p, dtype=str, keep_default_na=False)
    have = raw["rps_acp_per_mwh"] if "rps_acp_per_mwh" in raw else pd.Series(".", index=raw.index)
    mode = ra.get("mode", "programs")
    if mode == "flat":
        price = float(ra.get("price", 100.0))
        prefix = ra.get("program_prefix", "ESR_")
        m = raw["RPS_PROGRAM"].str.startswith(prefix)
        raw["rps_acp_per_mwh"] = [repr(price) if x else h for x, h in zip(m, have)]
        raw.to_csv(p, index=False)
        log(f"rps acp: flat ${price:g}/MWh on {raw.loc[m, 'RPS_PROGRAM'].nunique()} state programs "
            f"({int(m.sum())} rows); other programs unchanged")
        return
    if mode != "programs":
        raise ValueError(f"s0_production.rps_acp.mode must be flat or programs, not {mode!r}")
    acp = {k: float(v) for k, v in (ra.get("programs") or {}).items()}
    raw["rps_acp_per_mwh"] = [repr(acp[pr]) if pr in acp else h for pr, h in zip(raw["RPS_PROGRAM"], have)]
    raw.to_csv(p, index=False)
    missing = set(acp) - set(raw["RPS_PROGRAM"])
    log(f"rps acp: {acp} on {int(raw['RPS_PROGRAM'].isin(acp).sum())} rows (others unchanged; hard where '.')"
        + (f"; programs not in the case: {sorted(missing)}" if missing else ""))


def apply_ic_slack(folder: Path, s0: dict, log: Log) -> None:
    cost = s0.get("interconnection_slack_cost_per_mw")
    p = Path(folder) / "ic_params.csv"
    if cost is None or not p.exists():
        return
    params = pd.read_csv(p)
    params["ic_slack_cost_per_mw"] = float(cost)
    params.to_csv(p, index=False)
    log(f"interconnection headroom: diagnostic slack at ${float(cost):,.0f}/MW (ic_params.csv)")


def write_retirement_rules(folder: Path, s0: dict, log: Log) -> None:
    """retirement_rules.csv for study_modules.retirement_rules, and gen_can_retire_early = 1 for the
    existing generators it covers (economic retirement allowed from the rule's year on)."""
    rr = s0.get("retirement_rule") or {}
    if not rr.get("enabled"):
        return
    year = int(rr.get("no_retirement_before", 2030))
    sources = [str(x).lower() for x in rr.get("energy_sources", ["coal", "naturalgas"])]
    gi = _read(folder, "gen_info.csv", dtype=str, keep_default_na=False)
    pre = _read(folder, "gen_build_predetermined.csv")
    existing = set(pre["GENERATION_PROJECT"])
    m = gi["gen_energy_source"].str.lower().isin(sources) & gi["GENERATION_PROJECT"].isin(existing)
    if "gen_can_retire_early" not in gi:
        gi["gen_can_retire_early"] = "0"
    before = int((gi.loc[m, "gen_can_retire_early"].isin(["1", "1.0"])).sum())
    gi.loc[m, "gen_can_retire_early"] = "1"
    gi.to_csv(Path(folder) / "gen_info.csv", index=False)
    pd.DataFrame({"gen_energy_source": [s for s in sorted(set(gi.loc[m, "gen_energy_source"]))],
                  "rr_no_retirement_before": year}).to_csv(Path(folder) / "retirement_rules.csv", index=False)
    log(f"retirement rule: no economic retirement of {sources} before period {year}, economic from {year}; "
        f"gen_can_retire_early = 1 on {int(m.sum())} existing generators ({before} already had it)")


def write_case_inputs(out_folder: Path, scen_settings_dict: dict) -> list[str]:
    """Run the case-input steps for one case folder (all its model years). Returns the log lines."""
    first = next(iter(scen_settings_dict.values()))
    s0 = s0_settings(first)
    if s0 is None:
        return []
    log = Log(out_folder)
    log(f"case {first.get('case_id')} years {list(scen_settings_dict)}: s0_production steps")
    apply_gas_capex(out_folder, s0, log)
    apply_coal_cf_caps(out_folder, s0, first, log)
    apply_wind_loss(out_folder, s0, log)
    apply_rps_acp(out_folder, s0, log)
    apply_ic_slack(out_folder, s0, log)
    write_retirement_rules(out_folder, s0, log)
    per = _read(out_folder, "periods.csv")
    log("periods: " + "; ".join(f"{int(r.INVESTMENT_PERIOD)} = {int(r.period_start)}-{int(r.period_end)} "
                                f"({int(r.period_end) - int(r.period_start) + 1} yr)" for r in per.itertuples()))
    log.write()
    return log.lines


def scenario_options(settings: dict) -> str:
    s0 = s0_settings(settings)
    if s0 is None:
        return ""
    mods = list(s0.get("extra_modules") or [])
    if (s0.get("retirement_rule") or {}).get("enabled") and "study_modules.retirement_rules" not in mods:
        mods.append("study_modules.retirement_rules")
    return "".join(f"--include-module {m} " for m in mods)
