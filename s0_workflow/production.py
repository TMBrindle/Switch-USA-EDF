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
                                       zonal coal CF caps (coal spec rev. 2 by stage, coal_fleet.py;
                                       or the legacy table), wind loss, the NY RPS buyout (ACP),
                                       headroom slack, the per-period retirement rule, the new-build
                                       rule (no new nuclear before 2035), period spans
  scenario_options(settings)           extra Switch modules for the case's scenario line
                                       (gen_amortization_period, retirement_rules, build_rules)
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

from s0_workflow import coal_fleet
from s0_workflow import prm
from s0_workflow import tx_policy

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
            apply_levels_by_period(s, s0, case, year)
            apply_rggi(s, s0, case, year)
            apply_retirement_option(s, s0)
            apply_forced_tx(s, s0, case, year)
            if prm.apply_settings(s, s0):
                logger.info("s0_production %s/%s: regional planning reserve (prm_regional; extreme-day script off)",
                            case, year)
            if coal_fleet.spec_settings(s0):            # coal spec rev. 2: hold technologies
                techs = coal_fleet.apply_settings(s, s0)
                logger.info("s0_production %s/%s: coal spec; hold projects %s", case, year, techs)
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


RETIREMENT_OVERRIDE_KEYS = ("predetermined_retirement_override", "clean_power_regs_retirement_override")


def apply_retirement_option(s: dict, s0: dict) -> None:
    """s0_production.retirements_pre2030, in place for one case/year:
      block_all     (S0 default, current policy) fedpol's blocked_2030_coal_gas predetermined override for coal and
                    gas (dated 2026-29 -> 2030: in service through the 2030 stage, gone from 2035) and no economic
                    retirement before 2030 (retirement_rule)
      planned_only  dated retirements as scheduled (no predetermined override), no economic retirement before 2030
      unrestricted  dated retirements as scheduled, economic retirement from the first stage (no retirement rule)
      legacy        the settings as they are (the regression case)"""
    o = coal_fleet.retirement_option(s0)
    if o == "block_all":
        s["predetermined_retirement_override"] = copy.deepcopy(coal_fleet.cs.BLOCK_RULE)
    elif o in ("planned_only", "unrestricted"):
        for k in RETIREMENT_OVERRIDE_KEYS:
            s.pop(k, None)


FORCED_TX_OPTIONS = ("reeds_certain", "named_projects", "reeds_certain_plus_A", "reeds_certain_plus_AB")
# status-review projects for reeds_certain_plus_A / _AB (CHANGES §60): placeholder until the list is supplied
STATUS_REVIEW_TABLE = "pg/extra_inputs/transmission/forced_tx_status_review.csv"
STATUS_REVIEW_COLUMNS = ["from_zone", "to_zone", "project_name", "status_class", "new_cap_mw", "mw_basis",
                         "new_cap_year", "trans_length_km", "trans_efficiency", "source", "notes"]
STATUS_CLASSES = {"reeds_certain_plus_A": ["A"], "reeds_certain_plus_AB": ["A", "B"]}


def status_review_rows(table: str, classes) -> pd.DataFrame:
    """The status-review projects of these classes, checked: the agreed columns; MW on the model's basis (transfer
    capability, the MW of trans_build_minimum and BuildTx); a year, length and efficiency on every row. Stops when
    there are none (the placeholder): the case can't be built until the list is supplied."""
    f = pd.read_csv(REPO / table)
    if list(f.columns) != STATUS_REVIEW_COLUMNS:
        raise ValueError(f"{table}: columns must be {STATUS_REVIEW_COLUMNS}, not {list(f.columns)}")
    rows = f[f.status_class.isin(classes)]
    if rows.empty:
        raise ValueError(f"s0_production.forced_tx with status classes {list(classes)}: {table} has no such rows "
                         f"(placeholder until the status-review list is supplied); the case can't be built yet")
    bad = rows[rows.mw_basis != "transfer_capability"]
    if len(bad):
        raise ValueError(f"{table}: mw_basis must be transfer_capability (MW of transfer capability, the model's "
                         f"basis); convert these first: {list(bad.project_name)}")
    miss = rows[rows[["new_cap_mw", "new_cap_year", "trans_length_km", "trans_efficiency"]].isna().any(axis=1)]
    if len(miss):
        raise ValueError(f"{table}: new_cap_mw, new_cap_year, trans_length_km and trans_efficiency are required: "
                         f"{list(miss.project_name)}")
    return rows


def forced_tx_table(release: str) -> str:
    """The forced-line table of a pinned ReEDS release (s0_workflow/scripts/build_reeds_forced_tx.py), repo-relative."""
    rel = f"pg/extra_inputs/transmission/forced_tx_reeds_certain_{release}.csv"
    if not (REPO / rel).exists():
        raise FileNotFoundError(f"s0_production.forced_tx reeds_certain, release {release!r}: {rel} not found; build "
                                f"it with s0_workflow/scripts/build_reeds_forced_tx.py")
    return rel


def apply_forced_tx(s: dict, s0: dict, case=None, year=None) -> None:
    """s0_production.forced_tx and forced_tx_expansion_limit, in place for one case/year (pg_to_switch
    transmission_tables reads the keys it sets; without them it builds as before):
      reeds_certain   (S0 default) forced lines = the ReEDS release's certain additions (forced_tx_table)
      named_projects  forced lines = transmission_connections.csv new_cap_mw (the list every other case uses)
      expansion limit minimum (S0 default): a forced line's trans_path_expansion_limit in its forced period is its
                      trans_build_minimum_mw, and it is limited like any other line in other periods; legacy: forced
                      lines are left out of trans_path_expansion_limit.csv (no limit), as in every other case"""
    o = s0.get("forced_tx", "named_projects") or "named_projects"
    if o not in FORCED_TX_OPTIONS:
        raise ValueError(f"s0_production.forced_tx must be one of {FORCED_TX_OPTIONS}, not {o!r}")
    if o == "reeds_certain":
        s["forced_tx_table"] = forced_tx_table(str(s0.get("forced_tx_release", "2026.09.21")))
    elif o in STATUS_CLASSES:
        table = s0.get("forced_tx_status_review", STATUS_REVIEW_TABLE)
        rows = status_review_rows(table, STATUS_CLASSES[o])
        s["forced_tx_table"] = [forced_tx_table(str(s0.get("forced_tx_release", "2026.09.21"))), table]
        s["forced_tx_status_classes"] = list(STATUS_CLASSES[o])
        logger.info("s0_production %s/%s: %d status-review projects (classes %s) forced with the ReEDS certain lines",
                    case, year, len(rows), STATUS_CLASSES[o])
    lim = s0.get("forced_tx_expansion_limit", "legacy") or "legacy"
    if lim not in ("minimum", "legacy"):
        raise ValueError(f"s0_production.forced_tx_expansion_limit must be minimum or legacy, not {lim!r}")
    if lim == "minimum":
        s["forced_tx_expansion_limit"] = "minimum"
    logger.info("s0_production %s/%s: forced transmission %s%s; forced-line expansion limit %s", case, year, o,
                f" ({s['forced_tx_table']})" if o != "named_projects" else "", lim)


RGGI_DEFAULTS = {"mode": "3pr", "parameters": "pg/extra_inputs/rggi_carbon/rggi_3pr_parameters.csv",
                 "program": "ETS 1", "escalation_after_last_year": 0.07}
SHORT_TON_PER_TONNE = 0.907185


def rggi_settings(s0: dict) -> dict:
    r = {**RGGI_DEFAULTS, **((s0 or {}).get("rggi") or {})}
    if r["mode"] not in ("3pr", "legacy"):
        raise ValueError(f"s0_production.rggi.mode must be 3pr or legacy, not {r['mode']!r}")
    return r


def rggi_3pr_values(year: int, r: dict | None = None) -> dict:
    """RGGI Third Program Review values for a model year ($ and tonnes per metric tonne; CHANGES §62), from
    rggi_3pr_parameters.csv (Model Rule, 2027-2037): auction reserve price (floor), CCR tier 1 and 2 trigger prices
    and CCR volumes. After the file's last year the floor and trigger prices keep escalating at the Model Rule rate
    (7%/yr: the file's own year-on-year growth, to the cent), applied to the short-ton prices and converted as the
    file does (/ 0.907185, to the cent); CCR volumes are held at the last year's. Years before the first take it."""
    r = r or RGGI_DEFAULTS
    t = pd.read_csv(REPO / r["parameters"]).set_index("year")
    first, last = int(t.index.min()), int(t.index.max())
    y = max(int(year), first)
    if y <= last:
        row = t.loc[y]
        return {"floor": float(row.min_reserve_price_per_metric_tonne),
                "ccr_prices": [float(row.ccr_t1_trigger_per_metric_tonne), float(row.ccr_t2_trigger_per_metric_tonne)],
                "ccr_volumes": [float(row.ccr_t1_volume_metric_tonnes), float(row.ccr_t2_volume_metric_tonnes)]}
    row, g = t.loc[last], (1 + float(r["escalation_after_last_year"])) ** (y - last)
    metric = lambda short: round(round(float(short) * g, 2) / SHORT_TON_PER_TONNE, 2)  # noqa: E731
    return {"floor": metric(row.min_reserve_price_per_short_ton),
            "ccr_prices": [metric(row.ccr_t1_trigger_per_short_ton), metric(row.ccr_t2_trigger_per_short_ton)],
            "ccr_volumes": [float(row.ccr_t1_volume_metric_tonnes), float(row.ccr_t2_volume_metric_tonnes)]}


def apply_rggi(s: dict, s0: dict, case=None, year=None) -> None:
    """s0_production.rggi (CHANGES §62): with mode 3pr (S0 default) every S0 case gets the Third Program Review
    auction reserve price (carbon_floor_price_by_program) and both CCR tiers (carbon_ccr_prices, carbon_ccr pools)
    for its model year, whatever its policies preset (before, only the `current` preset set them, and only for
    2028/2030/2035). The cap stays rggi_carbon/rggicon_3pr.csv's (through the policy files). legacy: the preset's
    values (the regression case). There is no emissions containment reserve: the Third Program Review removed it."""
    r = rggi_settings(s0)
    if r["mode"] != "3pr":
        return
    v = rggi_3pr_values(int(year), r)
    prog = r["program"]
    s["carbon_floor_price_by_program"] = {**(s.get("carbon_floor_price_by_program") or {}), prog: v["floor"]}
    s["carbon_ccr_prices"] = {**(s.get("carbon_ccr_prices") or {}), prog: list(v["ccr_prices"])}
    ccr = dict(s.get("carbon_ccr") or {})
    ccr[prog] = {**(ccr.get(prog) or {}), "ccr_pools_tco2_per_yr": list(v["ccr_volumes"])}
    s["carbon_ccr"] = ccr
    logger.info("s0_production %s/%s: RGGI 3PR floor %.2f, CCR triggers %s, volumes %s ($ and t per metric tonne)",
                case, year, v["floor"], v["ccr_prices"], v["ccr_volumes"])


LEVEL_KEYS = {"interconnection_headroom": "scenario", "build_rate": "level"}


def level_for(s0: dict, what: str, year: int):
    """The level s0_production.levels_by_period sets for `what` (interconnection_headroom | build_rate) in a model
    year (each key holds until the next; earlier years take the first), or None when it sets none."""
    table = ((s0 or {}).get("levels_by_period") or {}).get(what) or {}
    if not table:
        return None
    return tx_policy.step_value(table, int(year))


def apply_levels_by_period(s: dict, s0: dict, case=None, year=None) -> None:
    """s0_production.levels_by_period (CHANGES §60 item 3): the interconnection-headroom scenario and the build-rate
    level by period, so they can change between the stages of a mode-A chain. Empty (default): the levels of
    s0_production.settings (atts_s0, central) in every period. The headroom state carried between stages follows the
    switch (study_modules.prepare_next_stage.chain_ic_inputs, ic_scenario_switch.csv)."""
    over = (s0 or {}).get("level_overrides") or {}
    for what, key in LEVEL_KEYS.items():
        v = over.get(what, level_for(s0, what, year))
        if v is not None:
            s.setdefault(what, {})[key] = v
            logger.info("s0_production %s/%s: %s %s %s (levels_by_period)", case, year, what, key, v)


def write_level_switch(out_folder: Path, s0: dict, scen_settings_dict: dict, log) -> None:
    """In a stage whose headroom scenario differs from the previous chain period's, ic_scenario_switch.csv tells
    prepare_next_stage (of the previous stage) to carry the headroom state onto this stage's own curves."""
    first_year = min(int(y) for y in scen_settings_dict)
    first = scen_settings_dict[next(iter(scen_settings_dict))]
    chain = sorted(int(y) for y in (first.get("_chain_years") or []))
    prev = [y for y in chain if y < first_year]
    if not prev or level_for(s0, "interconnection_headroom", first_year) is None \
            or ((s0 or {}).get("level_overrides") or {}).get("interconnection_headroom"):
        return
    a, b = level_for(s0, "interconnection_headroom", prev[-1]), level_for(s0, "interconnection_headroom", first_year)
    if a != b:
        pd.DataFrame([{"from_scenario": a, "to_scenario": b, "from_period": prev[-1], "to_period": first_year}]).to_csv(
            Path(out_folder) / "ic_scenario_switch.csv", index=False)
        log(f"interconnection headroom scenario switch {a} ({prev[-1]}) -> {b} ({first_year}): ic_scenario_switch.csv")


def forced_tx_period(in_service_year: int, chain_years, stage_years):
    """The period in which a forced line with this in-service year gets its trans_build_minimum in a stage, or
    None. The forced period is the first chain period at or after the year (the period whose span holds it; an
    earlier year falls in the first period); a stage forces the line only when it models that period. Myopic
    stages (mode A) force each line once; rolling windows (mode B) force it in each window holding the period,
    but only the window that commits the period hands the build on. Outside S0 chains chain_years =
    stage_years (pg_to_switch's behaviour before)."""
    period = next((p for p in sorted(chain_years) if p >= int(in_service_year)), None)
    return period if period is not None and period in set(stage_years) else None


def economic_rule_on(s0: dict) -> bool:
    """The per-period retirement rule applies (block_all, planned_only; legacy: as retirement_rule.enabled)."""
    o = coal_fleet.retirement_option(s0)
    return o in ("block_all", "planned_only") or (o == "legacy" and bool((s0.get("retirement_rule") or {}).get("enabled")))


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
    unrestricted = coal_fleet.retirement_option(s0) == "unrestricted"
    if not (economic_rule_on(s0) or unrestricted):
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
    if unrestricted:                                       # retirements_pre2030: unrestricted
        log(f"pre-2030 retirements unrestricted: economic retirement of {sources} from the first stage "
            f"(gen_can_retire_early = 1 on {int(m.sum())} existing generators, {before} already); no retirement rule")
        return
    pd.DataFrame({"gen_energy_source": [s for s in sorted(set(gi.loc[m, "gen_energy_source"]))],
                  "rr_no_retirement_before": year}).to_csv(Path(folder) / "retirement_rules.csv", index=False)
    log(f"retirement rule: no economic retirement of {sources} before period {year}, economic from {year}; "
        f"gen_can_retire_early = 1 on {int(m.sum())} existing generators ({before} already had it)")


def write_build_rules(folder: Path, s0: dict, log: Log) -> None:
    """build_rules.csv for study_modules.build_rules: no NEW capacity of the rule's technologies (gen_tech
    containing one of `technologies`, case-insensitive; e.g. nuclear, large and SMR) in periods before
    `no_new_build_before`. Rows are by gen_energy_source, taken from the matching generators."""
    br = s0.get("new_build_rule") or {}
    if not br.get("enabled"):
        return
    year = int(br.get("no_new_build_before", 2035))
    techs = [str(x).lower() for x in br.get("technologies", ["nuclear"])]
    gi = _read(folder, "gen_info.csv", dtype=str, keep_default_na=False)
    m = gi["gen_tech"].str.lower().apply(lambda t: any(x in t for x in techs))
    sources = sorted(set(gi.loc[m, "gen_energy_source"]))
    other = gi[~m & gi["gen_energy_source"].isin(sources)]
    if len(other):
        raise ValueError(f"new_build_rule: energy source(s) {sources} of {techs} are also used by "
                         f"{sorted(set(other['gen_tech']))[:5]}; the rule would block those too")
    pd.DataFrame({"gen_energy_source": sources, "br_no_new_build_before": year}).to_csv(
        Path(folder) / "build_rules.csv", index=False)
    log(f"new-build rule: no new {techs} (energy sources {sources}, {int(m.sum())} generators) in periods "
        f"before {year}; existing and planned units unaffected")


def write_case_inputs(out_folder: Path, scen_settings_dict: dict) -> list[str]:
    """Run the case-input steps for one case folder (all its model years). Returns the log lines."""
    first = next(iter(scen_settings_dict.values()))
    s0 = s0_settings(first)
    if s0 is None:
        return []
    log = Log(out_folder)
    log(f"case {first.get('case_id')} years {list(scen_settings_dict)}: s0_production steps")
    apply_gas_capex(out_folder, s0, log)
    if not coal_fleet.spec_settings(s0):                   # legacy: one table, 500 MW / 0.65 fallback rule
        apply_coal_cf_caps(out_folder, s0, first, log)
    apply_wind_loss(out_folder, s0, log)
    apply_rps_acp(out_folder, s0, log)
    apply_ic_slack(out_folder, s0, log)
    write_retirement_rules(out_folder, s0, log)
    write_build_rules(out_folder, s0, log)
    coal_fleet.write_case_inputs(out_folder, s0, scen_settings_dict, log)   # coal spec rev. 2 (after the retirement rule)
    prm.write_case_inputs(out_folder, s0, scen_settings_dict, log)          # regional planning reserve (§55)
    tx_policy.write_case_inputs(out_folder, s0, scen_settings_dict, log)    # moratorium, national cap (§60)
    write_level_switch(out_folder, s0, scen_settings_dict, log)             # headroom scenario by period (§60)
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
    if economic_rule_on(s0) and "study_modules.retirement_rules" not in mods:
        mods.append("study_modules.retirement_rules")
    if (s0.get("new_build_rule") or {}).get("enabled") and "study_modules.build_rules" not in mods:
        mods.append("study_modules.build_rules")
    if tx_policy.needs_module(s0) and "study_modules.tx_build_cap" not in mods:
        mods.append("study_modules.tx_build_cap")
    # demand response (3% shiftable load at $43/kW-yr deployed; adjust/add_dr_info.py writes its inputs for every case)
    if (s0.get("demand_response") or {}).get("enabled") and "study_modules.demand_response_investment" not in mods:
        mods.append("study_modules.demand_response_investment")
    return "".join(f"--include-module {m} " for m in mods) + prm.scenario_options(s0)
