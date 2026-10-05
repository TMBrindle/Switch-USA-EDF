"""Regional planning reserve requirement for S0 production cases (s0_production.prm; CHANGES §55).

design: legacy   today's per-zone planning_reserves + the extreme-day block (adjust/add_extreme_days.py); the case
                 is built exactly as before (the regression case)
design: regional switch/study_modules/prm_regional.py with:
  1. regions: ReEDS nercr (hierarchy.csv), WECC_NW split by transgrp (NorthernGrid_West / _South / _East,
     WestConnect_North): 16 regions; zones of a region share capacity over internal lines (deliverability is
     checked zone by zone, with line limits and losses)
  2. margins: NERC 2025 LTRA reference margin levels (s0_workflow/specs/prm/prm_redesign_config.yaml), linear
     2026 -> 2030 and flat after; applied to derated capacity: margin = (1 + RML) x (1 - FOR_w) - 1, FOR_w the
     region's capacity-weighted normal-weather (ReEDS static) forced-outage rate of its existing thermal fleet
  3. stress days: about 10-12 real weather days (2007-2013) per model year, greedy coverage of every region's
     worst summer peak-load day, worst winter peak-load day and worst low wind/solar high-load (stylised net
     load) day; zero weight; the requirement is checked in their hours only
  4. thermal capacity in stress hours = nameplate x (1 - FOR): seasonal (winter days at the cold end of the
     murphy2019 curves, -15 C; summer days at the hot end, 35 C; shoulder months at the static rate), or hourly
     from ReEDS's temperature_state.h5 (state -> zone) when supplied; hydro at its dispatch on the day
  5. imports: net reserve imports into a region capped in every stress hour at the ReEDS 99.9th-percentile share
     of the region's peak (flat in S0; relaxed / none for transmission scenarios); new lines derated 15%
  6. storage: net dispatch, backed by state of charge through the stress day (the storage module's cyclic SOC)
  7. shortfall: slack per region and period at $300/kW-yr (2028$, in the model's 2024$), sensitivity $106/kW-yr
  8. diagnostics: written by the module's post_solve
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "s0_workflow/specs/prm/prm_redesign_config.yaml"
CPI = REPO / "interconnection_headroom/data/reference/cpi_u_annual.csv"
logger = logging.getLogger(__name__)

DESIGNS = ("legacy", "regional")
WECC_SPLIT = "WECC_NW"
CURVE_TEMPS = (-15, -10, -5, 0, 25, 30, 35)
LEGACY_MODULES = ("study_modules.planning_reserves", "study_modules.planning_reserves_extreme_days")
MODULE = "study_modules.prm_regional"
EXTREME_DAY_SCRIPT = "Add extreme day"
DEFAULTS = {
    "design": "legacy",
    "config": "s0_workflow/specs/prm/prm_redesign_config.yaml",
    "stress_days": {"rule": "greedy", "formulation": "full", "top_load_share": 0.01, "cover_plus_tolerance": 0.06,
                    "max_days": 12, "cover_tolerance": 0.02, "tolerance_step": 0.02, "max_tolerance": 0.20,
                    "summer_months": [6, 7, 8, 9], "winter_months": [12, 1, 2], "first_weather_year": 2007,
                    "diag_dir": "prm"},
    "thermal_derate": {"method": "seasonal", "temperature_h5": None, "temperature_tz": "Etc/GMT+6",
                       "cold_c": -15, "hot_c": 35, "winter_months": [11, 12, 1, 2, 3], "summer_months": [5, 6, 7, 8, 9]},
    "imports": {"mode": "flat", "relax_from": 2031, "relax_to": 2050, "new_tx_allowance": 0.0},
    "new_tx_derate": 0.15,
    "reserve_rows": "hourly",
    "penalty": "central",
    "penalty_values": {"central": {"usd_per_kw_yr": 300.0, "dollar_year": 2028},
                       "low": {"usd_per_kw_yr": 106.0, "dollar_year": 2025}},
    "inflation_after_cpi": 0.025,
}


# ---------------------------------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------------------------------
def _merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def prm_settings(s0: dict | None) -> dict | None:
    """The merged prm settings when the case uses the regional design, else None (legacy)."""
    if not s0:
        return None
    p = _merge(DEFAULTS, s0.get("prm") or {})
    if p["design"] not in DESIGNS:
        raise ValueError(f"s0_production.prm.design must be one of {DESIGNS}, not {p['design']!r}")
    return p if p["design"] == "regional" else None


def year_prm(settings: dict) -> dict | None:
    s0 = settings.get("s0_production") or {}
    return prm_settings(s0) if s0.get("enabled") else None


def config(p: dict | None = None) -> dict:
    path = REPO / ((p or DEFAULTS)["config"])
    return yaml.safe_load(open(path, encoding="utf-8"))


def apply_settings(s: dict, s0: dict) -> bool:
    """In place for one case/year: the regional design drops the extreme-day script (its stress days replace it)."""
    p = prm_settings(s0)
    if p is None:
        return False
    scripts = dict(s.get("model_adjustment_scripts") or {})
    if EXTREME_DAY_SCRIPT in scripts:
        scripts[EXTREME_DAY_SCRIPT] = None
        s["model_adjustment_scripts"] = scripts
    return True


def scenario_options(s0: dict) -> str:
    """Switch options for the regional design: the two legacy reserve modules out, prm_regional in. Duals come from
    study_modules.write_dual_costs (modules.txt), which every run loads."""
    if prm_settings(s0) is None:
        return ""
    return "".join(f"--exclude-module {m} " for m in LEGACY_MODULES) + f"--include-module {MODULE} "


# ---------------------------------------------------------------------------------------------------
# regions, margins, imports
# ---------------------------------------------------------------------------------------------------
def zone_regions(zones=None, zone_map: dict | None = None) -> dict:
    """Load zone -> reserve region: hierarchy.csv nercr, WECC_NW by transgrp. Aggregate zones take their members'
    region (it must be unique)."""
    h = pd.read_csv(REPO / "hierarchy.csv")
    h = h[h.ba.str.match(r"^p\d+$") & (h.country == "USA")]          # p1-p134 (Canada / Mexico: no reserve region)
    reg = dict(zip(h.ba, np.where(h.nercr == WECC_SPLIT, h.transgrp, h.nercr)))
    if zones is None:
        return reg
    members = {}
    for ba, z in (zone_map or {}).items():
        members.setdefault(z, set()).add(ba)
    out = {}
    for z in zones:
        if z in reg:
            out[z] = reg[z]
        elif z in members:
            rr = {reg.get(b) for b in members[z]}
            if len(rr) != 1 or None in rr:
                raise ValueError(f"aggregate zone {z} spans reserve regions {sorted(map(str, rr))}")
            out[z] = rr.pop()
    return out


def zone_interconnects(zones, zone_map: dict | None = None) -> dict:
    """Load zone -> interconnection (hierarchy.csv interconnect: eastern, western, ercot), US zones; aggregate zones
    take their members' interconnection (it must be unique)."""
    h = pd.read_csv(REPO / "hierarchy.csv")
    h = h[h.ba.str.match(r"^p\d+$") & (h.country == "USA")]
    ic = dict(zip(h.ba, h.interconnect))
    members = {}
    for ba, z in (zone_map or {}).items():
        members.setdefault(z, set()).add(ba)
    out = {}
    for z in zones:
        if z in ic:
            out[z] = ic[z]
        elif z in members:
            v = {ic.get(b) for b in members[z]}
            if len(v) != 1 or None in v:
                raise ValueError(f"aggregate zone {z} spans interconnections {sorted(map(str, v))}")
            out[z] = v.pop()
    return out


def region_entry(cfg: dict, region: str) -> dict:
    regs = cfg["prm"]["regions"]
    if region in regs:
        return regs[region]
    return regs[WECC_SPLIT]["by_transgrp"][region]


def rml(entry: dict, year: int) -> float:
    """Reference margin level for a model year: linear 2026 -> 2030, flat after (and before)."""
    a, b = float(entry["rml_2026"]), float(entry["rml_2030"])
    y = min(max(int(year), 2026), 2030)
    return a + (b - a) * (y - 2026) / 4


def import_share(cfg: dict, region: str, year: int, p: dict) -> float | None:
    shares = cfg["import_limit"]["share_of_peak"]
    s = shares.get(region, shares.get(WECC_SPLIT) if region in region_entry_names(cfg) else None)
    mode = p["imports"]["mode"]
    if mode == "none" or s is None:
        return None
    s = float(s)
    if mode == "flat":
        return s
    if mode == "relaxed":           # ReEDS default "2031_hist/2050_100": historical share to 100% of peak
        a, b = int(p["imports"]["relax_from"]), int(p["imports"]["relax_to"])
        f = min(max((int(year) - a) / (b - a), 0.0), 1.0)
        return s + (1.0 - s) * f
    raise ValueError(f"s0_production.prm.imports.mode must be flat, relaxed or none, not {mode!r}")


def region_entry_names(cfg: dict) -> set:
    return set(cfg["prm"]["regions"][WECC_SPLIT]["by_transgrp"])


# ---------------------------------------------------------------------------------------------------
# generator classes, forced-outage rates
# ---------------------------------------------------------------------------------------------------
def gen_class(tech: str, source: str, is_variable, is_storage: bool, duration=None) -> tuple[str, str, str | None]:
    """(prm_credit, prm_class, murphy curve or None) for a gen_info row."""
    t, s = str(tech).lower(), str(source).lower()
    if is_storage or s == "storage":
        if "pumped" in t:
            return "storage", "pumped_storage", None
        d = float(duration) if duration is not None and pd.notna(duration) else 4.0
        return "storage", f"storage_{min((4, 6, 8, 10), key=lambda x: abs(x - d))}h", None
    if s == "demand_response":
        # PowerGenome's flexible-demand virtual generators (load_growth, us_exports): curtailment of added load, off
        # in S0 (annual availability 0). Annual limits don't bind in zero-weight stress hours, so crediting their
        # dispatch would give free reserve: no credit. Demand response proper is ShiftDemand
        # (demand_response_investment), credited through served load and reported as class "dr".
        return "none", "flex_load", None
    if s == "imports":
        return "dispatch", "imports", None
    if s == "water" or "hydro" in t:
        return "dispatch", "hydro", None
    if s == "wind" or "wind" in t:
        return "variable", "offshore_wind" if "offshore" in t else "onshore_wind", None
    if s in ("sun", "solar") or "pv" in t or "solar" in t:
        return "variable", "solar", None
    if bool(is_variable) and not (isinstance(is_variable, float) and np.isnan(is_variable)):
        return "variable", "other_variable", None
    if s == "uranium" or "nuclear" in t:
        return "capacity", "nuclear", "nuclear"
    if s == "coal" or "coal" in t:
        return "capacity", "coal", "steam_coal_ogs"
    if s in ("distillate", "residual_fuel_oil", "oil") or "petroleum" in t:
        return "capacity", "oil", "diesel"
    if s == "naturalgas":
        if "combined cycle" in t or "_cc" in t:
            return "capacity", "gas_cc", "combined_cycle"
        if "steam" in t:
            return "capacity", "steam", "steam_coal_ogs"
        return "capacity", "gas_ct", "combustion_turbine"        # CT, aero, ICE, Other_peaker
    if s == "geothermal":
        return "capacity", "geothermal", None
    if s in ("biomass", "biosolid", "bioliquid", "landfill gas", "msw") or "bio" in t or "waste" in t:
        return "capacity", "biopower", None
    return "capacity", "other", None


def static_for(cfg: dict, cls: str) -> float:
    """Normal-weather forced-outage rate (ReEDS outage_forced_static.csv, via the config block)."""
    f = cfg["thermal_derate"]["static_fallback"]
    return float({"gas_cc": f["gas_cc"], "gas_ct": f["gas_ct"], "coal": f["coal_old"], "steam": f["o_g_s"],
                  "oil": f["o_g_s"], "nuclear": f["nuclear"], "geothermal": f["geothermal"],
                  "biopower": f["biopower"]}.get(cls, f["o_g_s"]))


def curve_for(cfg: dict, curve: str, temp_c) -> np.ndarray:
    """murphy2019 forced-outage rate at a temperature (linear between the listed points, clamped at the ends)."""
    vals = cfg["thermal_derate"]["forced_outage_vs_temp_C"][curve]
    return np.interp(np.asarray(temp_c, dtype=float), CURVE_TEMPS, vals)


def season_of_month(month: int, td: dict) -> str:
    if int(month) in td["winter_months"]:
        return "winter"
    if int(month) in td["summer_months"]:
        return "summer"
    return "shoulder"


def stress_for(cfg: dict, cls: str, curve: str | None, season: str, td: dict) -> float:
    """Seasonal fallback: winter stress days at the cold end of the curve, summer at the hot end, shoulder at the
    static (normal-weather) rate; classes without a curve at their static rate."""
    if curve is None or season == "shoulder":
        return static_for(cfg, cls)
    return float(curve_for(cfg, curve, td["cold_c"] if season == "winter" else td["hot_c"]))


def classify(gi: pd.DataFrame) -> pd.DataFrame:
    dur = gi["gen_storage_energy_to_power_ratio"] if "gen_storage_energy_to_power_ratio" in gi else pd.Series(np.nan, index=gi.index)
    st = gi["gen_storage_efficiency"].notna() if "gen_storage_efficiency" in gi else pd.Series(False, index=gi.index)
    rows = [gen_class(t, s, v, bool(x), d) for t, s, v, x, d in
            zip(gi.gen_tech, gi.gen_energy_source, gi.get("gen_is_variable", pd.Series(0, index=gi.index)), st, dur)]
    out = pd.DataFrame(rows, columns=["prm_credit", "prm_class", "curve"], index=gi.index)
    out.insert(0, "GENERATION_PROJECT", gi.GENERATION_PROJECT)
    return out


def normal_weather_for(gi: pd.DataFrame, gbp: pd.DataFrame, classes: pd.DataFrame, regions: dict, period: int,
                       cfg: dict) -> pd.DataFrame:
    """Capacity-weighted normal-weather FOR of each region's existing thermal fleet in service in the period."""
    pre = gbp.merge(gi[["GENERATION_PROJECT", "gen_load_zone", "gen_max_age"]], on="GENERATION_PROJECT")
    pre = pre[(pre.build_year <= period) & (pre.build_year + pre.gen_max_age.fillna(500) > period)]
    pre = pre.merge(classes, on="GENERATION_PROJECT")
    pre = pre[pre.prm_credit == "capacity"]
    pre["region"] = pre.gen_load_zone.map(regions)
    pre["for"] = [static_for(cfg, c) for c in pre.prm_class]
    g = pre.assign(w=pre["for"] * pre.build_gen_predetermined).groupby("region")[["build_gen_predetermined", "w"]].sum()
    return pd.DataFrame({"thermal_mw": g.build_gen_predetermined, "for_w": g.w / g.build_gen_predetermined})


def margins(cfg: dict, regions: dict, periods, gi, gbp, classes, p: dict) -> pd.DataFrame:
    rows = []
    for per in periods:
        fw = normal_weather_for(gi, gbp, classes, regions, int(per), cfg)
        for r in dict.fromkeys(regions.values()):
            e = region_entry(cfg, r)
            m = rml(e, per)
            f = float(fw.for_w.get(r, 0.0)) if len(fw) else 0.0
            sh = import_share(cfg, r, per, p)
            rows.append({"PRM_REGION": r, "PERIOD": int(per), "ltra_area": e["ltra_area"], "rml_2026": e["rml_2026"],
                         "rml_2030": e["rml_2030"], "rml": round(m, 6),
                         "thermal_mw": float(fw.thermal_mw.get(r, 0.0)) if len(fw) else 0.0, "for_w": round(f, 6),
                         "prm_margin": round((1 + m) * (1 - f) - 1, 6),
                         "prm_import_share": sh, "import_mode": p["imports"]["mode"]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------------
# penalty
# ---------------------------------------------------------------------------------------------------
def cpi_index(year: int, rate: float) -> float:
    c = pd.read_csv(CPI, comment="#")
    c.columns = [str(x).strip().lower() for x in c.columns]
    ycol, vcol = c.columns[0], c.columns[1]
    s = dict(zip(c[ycol].astype(int), c[vcol].astype(float)))
    last = max(s)
    return s[int(year)] if int(year) in s else s[last] * (1 + rate) ** (int(year) - last)


def penalty_per_mw_yr(p: dict, model_dollar_year: int) -> tuple[float, str]:
    v = p["penalty_values"][p["penalty"]]
    f = cpi_index(model_dollar_year, p["inflation_after_cpi"]) / cpi_index(v["dollar_year"], p["inflation_after_cpi"])
    per_kw = float(v["usd_per_kw_yr"]) * f
    return per_kw * 1000.0, (f"${v['usd_per_kw_yr']}/kW-yr ({v['dollar_year']}$) x {f:.4f} = ${per_kw:.2f}/kW-yr in "
                             f"{model_dollar_year}$ (CPI-U to {last_cpi_year()}, then {p['inflation_after_cpi']:.1%}/yr)")


def last_cpi_year() -> int:
    c = pd.read_csv(CPI, comment="#")
    return int(c.iloc[:, 0].max())


# ---------------------------------------------------------------------------------------------------
# stress days
# ---------------------------------------------------------------------------------------------------
def day_dates(n_days: int, first_year: int) -> pd.DataFrame:
    """Calendar of n_days days of 365-day years (PowerGenome drops Feb 29)."""
    base = pd.date_range("2001-01-01", "2001-12-31", freq="D")              # a non-leap year
    d = np.arange(n_days)
    yr = first_year + d // 365
    doy = d % 365
    return pd.DataFrame({"day": d, "year": yr, "month": base.month.values[doy], "dom": base.day.values[doy]})


def region_series(L: pd.DataFrame, R: pd.DataFrame, gens: pd.DataFrame, regions: dict, wind_factor=1.0,
                  stylised=None, wind=None) -> tuple[dict, dict]:
    """Per reserve region: hourly load and stylised net load (each region's wind and solar sized to supply the
    stylised shares of its load, as the fleet-independent day selector's low-net-load tail). When `wind` is a dict,
    it also gets each region's hourly onshore-wind CF (mean over the region's onshore-wind profiles; the national
    mean for a region without any, recorded under the key ("_national", r))."""
    from s0_workflow.day_selection import kind
    sty = stylised or {"wind_energy_share": 0.20, "solar_energy_share": 0.20}
    g = pd.DataFrame({"Resource": list(gens["Resource"]), "k": [kind(t) for t in gens["technology"]],
                      "r": [regions.get(z) for z in gens["region"]]})
    g = g[g.Resource.isin(R.columns)].drop_duplicates("Resource").set_index("Resource")
    load, net = {}, {}
    for r in dict.fromkeys(v for z, v in regions.items() if z in L.columns):
        x = L[[z for z in L.columns if regions.get(z) == r]].sum(axis=1).to_numpy(dtype=float)
        nl = x.copy()
        for k, share, f in (("onwind", sty["wind_energy_share"], wind_factor), ("solar", sty["solar_energy_share"], 1.0)):
            cols = g[(g.r == r) & (g.k == k)].index
            if len(cols):
                cf = R[cols].mean(axis=1).to_numpy(dtype=float) * f
                if cf.mean() > 0:
                    nl = nl - float(share) * x.mean() / cf.mean() * cf
        load[r], net[r] = x, nl
        if wind is not None:
            cols = g[(g.r == r) & (g.k == "onwind")].index
            if len(cols):
                wind[r] = R[cols].mean(axis=1).to_numpy(dtype=float)
            else:
                allw = g[g.k == "onwind"].index
                if not len(allw):
                    raise ValueError("stress days: no onshore-wind profiles for the low-wind need")
                wind[r] = R[allw].mean(axis=1).to_numpy(dtype=float)
                wind[("_national", r)] = True
    return load, net


def select_stress_days(load: dict, net: dict, sd: dict, wind: dict | None = None, ic: tuple | None = None
                       ) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """Stress days, by stress_days.rule: guaranteed (guaranteed_stress_days), greedy (greedy_stress_days) or
    cover_plus_interconnect_wind (cover_plus_interconnect_wind; needs `ic`, the interconnections' (load, wind))."""
    rule = sd.get("rule", "greedy")
    if rule == "guaranteed":
        if wind is None:
            raise ValueError("stress_days.rule guaranteed needs the regions' wind CFs")
        return guaranteed_stress_days(load, wind, sd)
    if rule == "cover_plus_interconnect_wind":
        if ic is None:
            raise ValueError("stress_days.rule cover_plus_interconnect_wind needs the interconnections' load and wind")
        return cover_plus_interconnect_wind(load, net, ic[0], ic[1], sd)
    if rule != "greedy":
        raise ValueError(f"stress_days.rule must be one of {STRESS_RULES}, not {rule!r}")
    return greedy_stress_days(load, net, sd)


STRESS_RULES = ("cover_plus_interconnect_wind", "guaranteed", "greedy")
IC_LABEL = {"eastern": "Eastern", "western": "Western", "ercot": "ERCOT"}


def cover_plus_interconnect_wind(load: dict, net: dict, ic_load: dict, ic_wind: dict, sd: dict
                                 ) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """S0 default (CHANGES §68): the greedy cover rule of recipe E at cover_plus_tolerance (0.06: a day covers a
    region's need if within 6% of that region's worst value; the tolerance widens only if more than max_days are
    needed), PLUS for each interconnection (Eastern, Western, ERCOT) its lowest-wind high-load day: of its top_load_share
    of the 2007-2013 days by daily peak load (interconnection-wide load), the one with the lowest daily mean
    onshore-wind CF (the mean over its onshore-wind profiles), on weather alone. A day already in the set is not added
    again (its row says so). Returns (days, coverage, tolerance)."""
    days, cov, tol = greedy_stress_days(load, net, dict(sd, cover_tolerance=float(sd.get("cover_plus_tolerance", 0.06))))
    D = len(next(iter(ic_load.values()))) // 24
    cal = day_dates(D, int(sd["first_weather_year"]))
    n_top = max(1, int(np.ceil(float(sd.get("top_load_share", 0.01)) * D)))
    have = {int(d): i for i, d in enumerate(days.day)}
    add, rows = [], []
    for name in sorted(ic_load, key=lambda k: list(IC_LABEL).index(k) if k in IC_LABEL else 99):
        dmax = np.asarray(ic_load[name], dtype=float).reshape(D, 24).max(axis=1)
        wcf = np.asarray(ic_wind[name], dtype=float).reshape(D, 24).mean(axis=1)
        top = np.argsort(-dmax, kind="stable")[:n_top]
        d = int(top[np.argmin(wcf[top])])
        label = f"{IC_LABEL.get(name, name)} low_wind_top_load (interconnection)"
        already = d in have or d in {a for a, _ in add}
        if d in have:
            i = have[d]
            days.loc[i, "covers"] = days.loc[i, "covers"] + "; " + label
            days.loc[i, "n_needs_covered"] = int(days.loc[i, "n_needs_covered"]) + 1
        elif already:
            add = [(a, lab + "; " + label if a == d else lab) for a, lab in add]
        else:
            add.append((d, label))
        rows.append({"PRM_REGION": f"interconnection:{name}", "need": "low_wind_top_load",
                     "worst_date": days_label(cal, d), "worst_mw": round(float(dmax[d]), 1),
                     "covered_by": days_label(cal, d), "covering_value_ratio": 1.0,
                     "wind_cf": round(float(wcf[d]), 4), "top_load_days": n_top,
                     "added": "no (already in the set)" if already else "yes"})
    if add:
        extra = cal.loc[[a for a, _ in add]].copy()
        extra["slot"] = [f"p{a + 1}" for a, _ in add]
        extra["date"] = [f"{y:04d}-{m:02d}-{dd:02d}" for y, m, dd in zip(extra.year, extra.month, extra.dom)]
        extra["covers"] = [lab for _, lab in add]
        extra["n_needs_covered"] = [lab.count(";") + 1 for _, lab in add]
        days = pd.concat([days, extra], ignore_index=True).sort_values("day").reset_index(drop=True)
    cov = pd.concat([cov, pd.DataFrame(rows)], ignore_index=True)
    return days, cov, tol


def guaranteed_stress_days(load: dict, wind: dict, sd: dict) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """The stated rule, on weather alone (S0 default, CHANGES §66): for each reserve region, its worst summer
    peak-load day (highest daily peak load in summer_months), its worst winter peak-load day (winter_months) and its
    lowest-wind day among its top-load days (the top_load_share of the record's days by daily peak load; lowest daily
    mean onshore-wind CF). Every such day is in the set, whatever the count (no max_days); a day that is several
    needs' worst day appears once. Returns (days, coverage, 0.0)."""
    any_r = next(iter(load))
    H = len(load[any_r])
    D = H // 24
    if H != D * 24:
        raise ValueError("stress-day selection needs whole days of hourly data")
    cal = day_dates(D, int(sd["first_weather_year"]))
    summer = cal.month.isin(sd["summer_months"]).to_numpy()
    winter = cal.month.isin(sd["winter_months"]).to_numpy()
    n_top = max(1, int(np.ceil(float(sd.get("top_load_share", 0.01)) * D)))
    pick, cov = {}, []
    for r in load:
        dmax = load[r].reshape(D, 24).max(axis=1)
        wcf = np.asarray(wind[r], dtype=float).reshape(D, 24).mean(axis=1)
        top = np.argsort(-dmax, kind="stable")[:n_top]
        low_wind = int(top[np.argmin(wcf[top])])
        for need, d, val in (("summer_peak_load", int(np.argmax(np.where(summer, dmax, -np.inf))), None),
                             ("winter_peak_load", int(np.argmax(np.where(winter, dmax, -np.inf))), None),
                             ("low_wind_top_load", low_wind, wcf[low_wind])):
            pick.setdefault(d, []).append(f"{r} {need}")
            cov.append({"PRM_REGION": r, "need": need, "worst_date": days_label(cal, d),
                        "worst_mw": round(float(dmax[d]), 1), "covered_by": days_label(cal, d),
                        "covering_value_ratio": 1.0,
                        "wind_cf": round(float(val), 4) if val is not None else float("nan"),
                        "wind_basis": ("national" if wind.get(("_national", r)) else "region")
                        if val is not None else "",
                        "top_load_days": n_top if val is not None else float("nan")})
    chosen = sorted(pick)
    days = cal.loc[chosen].copy()
    days["slot"] = [f"p{d + 1}" for d in chosen]
    days["date"] = [f"{y:04d}-{m:02d}-{dd:02d}" for y, m, dd in zip(days.year, days.month, days.dom)]
    days["covers"] = ["; ".join(pick[d]) for d in chosen]
    days["n_needs_covered"] = [len(pick[d]) for d in chosen]
    return days.reset_index(drop=True), pd.DataFrame(cov), 0.0


def greedy_stress_days(load: dict, net: dict, sd: dict) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """Greedy coverage (S0 before §66; rule greedy): each region's worst summer peak-load day, worst winter peak-load day and worst
    low-wind/solar high-load day (daily maximum stylised net load). A day covers a need if its value is within
    `tol` of the worst; tol widens by tolerance_step until max_days suffice. Returns (days, coverage, tol)."""
    any_r = next(iter(load))
    H = len(load[any_r])
    D = H // 24
    if H != D * 24:
        raise ValueError("stress-day selection needs whole days of hourly data")
    cal = day_dates(D, int(sd["first_weather_year"]))
    summer = cal.month.isin(sd["summer_months"]).to_numpy()
    winter = cal.month.isin(sd["winter_months"]).to_numpy()
    needs = {}
    for r in load:
        dmax = load[r].reshape(D, 24).max(axis=1)
        nmax = net[r].reshape(D, 24).max(axis=1)
        needs[(r, "summer_peak_load")] = np.where(summer, dmax, -np.inf)
        needs[(r, "winter_peak_load")] = np.where(winter, dmax, -np.inf)
        needs[(r, "low_wind_solar")] = nmax
    best = {n: float(np.max(v)) for n, v in needs.items()}
    names = list(needs)
    M = np.vstack([needs[n] for n in names])                         # needs x days
    V = np.array([best[n] for n in names])[:, None]
    tol = float(sd["cover_tolerance"])
    while True:
        cover = (M >= (1 - tol) * V) & np.isfinite(M) & (V > 0)
        chosen, left = [], np.ones(len(names), bool)
        while left.any():
            gain = (cover & left[:, None]).sum(axis=0)
            score = gain + 1e-6 * np.nan_to_num(np.where(np.isfinite(M), M / np.where(V > 0, V, 1), 0), nan=0).sum(axis=0)
            d = int(np.argmax(score))
            if gain[d] == 0:
                break
            chosen.append(d)
            left &= ~cover[:, d]
        if len(chosen) <= int(sd["max_days"]) or tol >= float(sd["max_tolerance"]) - 1e-12:
            break
        tol = round(tol + float(sd["tolerance_step"]), 6)
    chosen = sorted(chosen)
    days = cal.loc[chosen].copy()
    days["slot"] = [f"p{d + 1}" for d in chosen]
    days["date"] = [f"{y:04d}-{m:02d}-{dd:02d}" for y, m, dd in zip(days.year, days.month, days.dom)]
    days["covers"] = ["; ".join(f"{r} {k}" for (r, k), c in zip(names, cover[:, d]) if c) for d in chosen]
    days["n_needs_covered"] = [int(cover[:, d].sum()) for d in chosen]
    cov = []
    for i, (r, k) in enumerate(names):
        by = [d for d in chosen if cover[i, d]]
        worst = int(np.argmax(M[i]))
        cov.append({"PRM_REGION": r, "need": k, "worst_date": days_label(cal, worst),
                    "worst_mw": round(best[(r, k)], 1),
                    "covered_by": "; ".join(days_label(cal, d) for d in by) or "NOT COVERED",
                    "covering_value_ratio": round(max((M[i, d] / best[(r, k)] for d in by), default=float("nan")), 4)})
    return days.reset_index(drop=True), pd.DataFrame(cov), tol


FORMULATIONS = ("full", "light")


def stress_formulation(p: dict) -> str:
    """prm.stress_days.formulation (CHANGES §69): full (the stress days carry the sample days' operating detail) or
    light (stress_light_timeseries.csv: no unit commitment, ramping, operating reserves, fuel use or per-timepoint
    cost/policy terms on them; thermal dispatch up to nameplate x prm_avail_frac)."""
    f = (p.get("stress_days") or {}).get("formulation", "full")
    if f not in FORMULATIONS:
        raise ValueError(f"s0_production.prm.stress_days.formulation must be one of {FORMULATIONS}, not {f!r}")
    return f


RESERVE_ROWS = ("hourly", "compact")


def reserve_rows(p: dict) -> str:
    """prm.reserve_rows (CHANGES §69): hourly (each capacity-credit unit's capacity and the zone's shortfall in every
    stress-hour reserve row) or compact (prm_compact_capacity = 1: one accredited-capacity variable per zone, period
    and derate group, defined once from capacity x prm_avail_frac and used in that group's hourly rows, with one
    shortfall variable per group linked once to the zone's shortfall). Same optimum; fewer nonzeros and far fewer
    columns appearing in many rows (the factorisation fill-in the VM traced the slowdown to)."""
    r = p.get("reserve_rows", "hourly")
    if r not in RESERVE_ROWS:
        raise ValueError(f"s0_production.prm.reserve_rows must be one of {RESERVE_ROWS}, not {r!r}")
    return r


def days_label(cal: pd.DataFrame, d: int) -> str:
    x = cal.loc[d]
    return f"{int(x.year):04d}-{int(x.month):02d}-{int(x.dom):02d}"


def add_stress_days(results: dict, representative_point: pd.DataFrame, weights, period_lc: pd.DataFrame,
                    period_variability: pd.DataFrame, period_gens: pd.DataFrame, year_settings: dict,
                    diag_dir: Path, variable_resources_only=True):
    """Append the model year's stress days (zero weight) to a time-sampling result (both PowerGenome k-means and
    the fleet-independent selector). Returns (results, representative_point, weights, n_stress)."""
    p = year_prm(year_settings)
    s0 = year_settings.get("s0_production") or {}
    ts = s0.get("time_sampling") or {}
    wl = s0.get("wind_loss") or {}
    wf = float(wl.get("factor", (1 - 0.134) / (1 - 0.017))) if wl.get("enabled") else 1.0
    regions = zone_regions(list(period_lc.columns), year_settings.get("_zone_map"))
    rule = p["stress_days"].get("rule", "greedy")
    wind = {} if rule == "guaranteed" else None
    L0, R0 = period_lc.reset_index(drop=True), period_variability.reset_index(drop=True)
    load, net = region_series(L0, R0, period_gens, regions, wf, ts.get("stylised_fleet"), wind)
    ic = None
    if rule == "cover_plus_interconnect_wind":
        ic_wind = {}
        ic_load, _ = region_series(L0, R0, period_gens, zone_interconnects(list(period_lc.columns),
                                                                           year_settings.get("_zone_map")),
                                   wf, ts.get("stylised_fleet"), ic_wind)
        ic = (ic_load, {k: v for k, v in ic_wind.items() if not isinstance(k, tuple)})
    days, cov, tol = select_stress_days(load, net, p["stress_days"], wind, ic)
    year = int(year_settings["model_year"])
    days["TIMESERIES"] = [f"{year}_{s}_prm" for s in days.slot]
    td = p["thermal_derate"]
    days["season"] = [season_of_month(m, td) for m in days.month]
    diag_dir = Path(diag_dir)
    diag_dir.mkdir(parents=True, exist_ok=True)
    days.to_csv(diag_dir / "stress_days.csv", index=False)
    cov.to_csv(diag_dir / "stress_coverage.csv", index=False)
    (diag_dir / "stress_info.txt").write_text(
        f"{len(days)} stress days for {year} (rule {rule}"
        + (f"; cover tolerance {tol:.3f}" if rule != "guaranteed" else
           f"; top-load days for the low-wind need: {p['stress_days'].get('top_load_share', 0.01):g} of the record")
        + f"); {len(load)} regions x 3 needs; not covered: {int((cov.covered_by == 'NOT COVERED').sum())}"
        + (f"; interconnection low-wind days: {int((cov.get('added', pd.Series(dtype=str)) == 'yes').sum())} added, "
           f"{int(cov.get('added', pd.Series(dtype=str)).astype(str).str.startswith('no').sum())} already in the set"
           if rule == "cover_plus_interconnect_wind" else "") + "\n")
    hrs = np.concatenate([np.arange(d * 24, d * 24 + 24) for d in days.day])
    L = period_lc.reset_index(drop=True).iloc[hrs].reset_index(drop=True)
    R = period_variability.reset_index(drop=True).iloc[hrs].reset_index(drop=True)
    if variable_resources_only:
        const = period_variability.std() == 0
        R.loc[:, const[const].index] = 1.0
    out = dict(results)
    lp = results["load_profiles"]
    out["load_profiles"] = pd.concat([lp.reset_index(drop=True), L[lp.columns]], ignore_index=True)
    rp = results["resource_profiles"]
    out["resource_profiles"] = pd.concat([rp.reset_index(drop=True), R[rp.columns]], ignore_index=True)
    out["ClusterWeights"] = list(results.get("ClusterWeights", weights)) + [0.0] * len(days)
    rep = pd.concat([representative_point.reset_index(drop=True), pd.DataFrame({"slot": list(days.slot)})],
                    ignore_index=True)
    return out, rep, list(weights) + [0.0] * len(days), len(days)


def rename_stress_rows(timeseries_df: pd.DataFrame, timepoints_df: pd.DataFrame, n: int):
    """The last n timeseries are stress days: give them their own ids (a stress day may also be a sample day):
    timeseries <year>_pN_prm, timepoint ids 9<original> (numeric, distinct from every sample timepoint)."""
    if not n:
        return timeseries_df, timepoints_df
    ts, tp = timeseries_df.copy(), timepoints_df.copy()
    stress = list(ts.timeseries.iloc[-n:])
    ren = {s: f"{s}_prm" for s in stress}
    ts.loc[ts.index[-n:], "timeseries"] = [ren[s] for s in stress]
    m = tp.timeseries.isin(stress) & (np.arange(len(tp)) >= len(tp) - int(ts.ts_num_tps.iloc[-n:].sum()))
    tp.loc[m, "timeseries"] = tp.loc[m, "timeseries"].map(ren)
    tp.loc[m, "timepoint_id"] = "9" + tp.loc[m, "timepoint_id"].astype(str)
    tp.loc[m, "timestamp"] = tp.loc[m, "timepoint_id"]
    return ts, tp


# ---------------------------------------------------------------------------------------------------
# hourly temperatures (optional): ReEDS inputs/profiles_temperature/temperature_state.h5
# ---------------------------------------------------------------------------------------------------
def state_temperatures(h5path: Path, years, tz_out: str) -> pd.DataFrame:
    """Hourly temperatures by state (deg C) for the weather years, index in tz_out (ReEDS reads the same file in
    reeds.io.get_temperatures: groups index_<year> and <year>, a `columns` dataset of state codes)."""
    import h5py
    out = []
    with h5py.File(h5path, "r") as f:
        cols = [c.decode() if isinstance(c, bytes) else str(c) for c in f["columns"][:]]
        for y in years:
            if str(y) not in f:
                continue
            idx = pd.to_datetime(pd.Series(f[f"index_{y}"][:]).map(lambda x: x.decode() if isinstance(x, bytes) else x))
            df = pd.DataFrame(f[str(y)][:], index=idx, columns=cols)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            out.append(df.tz_convert(tz_out))
    return pd.concat(out).sort_index()


def zone_states(zones) -> dict:
    h = pd.read_csv(REPO / "hierarchy.csv")
    return {z: st for z, st in zip(h.ba, h.st) if z in set(zones)}


# ---------------------------------------------------------------------------------------------------
# case inputs
# ---------------------------------------------------------------------------------------------------
def benchmark_rows(cfg: dict) -> pd.DataFrame:
    """ISO capacity-credit values to compare with the implied capacity credit (config accreditation blocks)."""
    a, t = cfg["accreditation"], cfg["thermal_derate"]["benchmark_class_accreditation"]
    rows = []

    def add(region, bench, cls, v):
        if isinstance(v, str):
            v = float(v.replace("~", ""))
        rows.append({"PRM_REGION": region, "prm_class": cls, "benchmark": bench, "benchmark_value": float(v)})
    pjm = a["PJM_2029_30"]
    for k, c in (("onshore_wind", "onshore_wind"), ("offshore_wind", "offshore_wind"), ("solar_tracking", "solar"),
                 ("storage_4h", "storage_4h"), ("storage_6h", "storage_6h"), ("storage_8h", "storage_8h"),
                 ("storage_10h", "storage_10h"), ("dr", "dr")):
        add("PJM", "PJM_2029_30", c, pjm[k])
    for k, c in (("nuclear", "nuclear"), ("gas_cc", "gas_cc"), ("gas_ct", "gas_ct"), ("coal", "coal"),
                 ("oil_ct", "oil"), ("steam", "steam")):
        add("PJM", "PJM_2029_30", c, t["PJM_2029_30"][k])
    for b in ("NYISO_2026_27_ROS", "NYISO_2026_27_NYC"):
        for k, v in a[b].items():
            add("NPCC_NY", b, {"scr_dr": "dr"}.get(k, k), v)
    for i, season in enumerate(("summer", "winter")):
        for k, v in a["ISONE_prelim_summer_winter"].items():
            add("NPCC_NE", f"ISONE_prelim_{season}", {"active_dr": "dr"}.get(k, k), v[i])
        for k, cls in (("gas_only", ("gas_cc", "gas_ct")), ("oil_dual", ("oil",)),
                       ("nuclear_other_thermal", ("nuclear", "coal", "steam"))):
            for c in cls:
                add("NPCC_NE", f"ISONE_prelim_{season}", c, t["ISONE_prelim_summer_winter"][k][i])
        for k, v in a["SPP_2024_summer_winter"].items():
            add("SPP", f"SPP_2024_{season}", {"wind": "onshore_wind"}.get(k, k), v[i])
    return pd.DataFrame(rows)


def write_case_inputs(folder: Path, s0: dict, scen_settings_dict: dict, log) -> None:
    """prm_regional inputs for one case folder (all its model years)."""
    p = prm_settings(s0)
    if p is None:
        return
    from s0_workflow.production import _read
    folder = Path(folder)
    cfg = config(p)
    first = next(iter(scen_settings_dict.values()))
    gi = _read(folder, "gen_info.csv", na_values=".")
    gbp = _read(folder, "gen_build_predetermined.csv", na_values=".")
    tp = _read(folder, "timepoints.csv")
    tsr = _read(folder, "timeseries.csv")
    lz = _read(folder, "load_zones.csv")
    periods = sorted(int(x) for x in _read(folder, "periods.csv").INVESTMENT_PERIOD)
    regions = zone_regions(list(lz.LOAD_ZONE), first.get("_zone_map"))
    pd.DataFrame({"LOAD_ZONE": list(regions), "PRM_REGION": list(regions.values())}).to_csv(
        folder / "prm_zones.csv", index=False)
    classes = classify(gi)
    m = margins(cfg, regions, periods, gi, gbp, classes, p)
    m.to_csv(folder / "prm_margin_basis.csv", index=False)
    m[["PRM_REGION", "PERIOD", "prm_margin", "prm_import_share"]].to_csv(
        folder / "prm_region_periods.csv", index=False, na_rep=".")
    stress = tsr[tsr.timeseries.astype(str).str.endswith("_prm")]
    if stress.empty:
        raise RuntimeError(f"s0_production.prm regional: no stress timeseries (<year>_pN_prm) in {folder}/timeseries.csv")
    pd.DataFrame({"TIMESERIES": stress.timeseries}).to_csv(folder / "prm_timeseries.csv", index=False)
    form = stress_formulation(p)
    rrows = reserve_rows(p)
    if form == "light":        # §69: no commitment, ramping, operating reserves, fuel or cost terms on stress days
        pd.DataFrame({"TIMESERIES": stress.timeseries}).to_csv(folder / "stress_light_timeseries.csv", index=False)
    classes[["GENERATION_PROJECT", "prm_credit", "prm_class"]].to_csv(folder / "prm_gen_credit.csv", index=False)

    # stress-day calendar: <diag_dir>/<year>/stress_days.csv from the time sampling
    td = p["thermal_derate"]
    info = []
    for y in scen_settings_dict:
        f = folder / p["stress_days"]["diag_dir"] / str(y) / "stress_days.csv"
        if f.exists():
            info.append(pd.read_csv(f))
    info = pd.concat(info, ignore_index=True) if info else pd.DataFrame(columns=["TIMESERIES", "season", "date"])
    season = dict(zip(info.TIMESERIES, info.season))
    date = dict(zip(info.TIMESERIES, info.date))
    stp = tp[tp.timeseries.isin(set(stress.timeseries))].copy()
    stp["season"] = stp.timeseries.map(season).fillna("shoulder")
    cap = classes[classes.prm_credit == "capacity"].merge(gi[["GENERATION_PROJECT", "gen_load_zone"]])
    rows = []
    hourly = td["method"] == "hourly"
    temps = None
    if hourly:
        if not td.get("temperature_h5") or not (REPO / td["temperature_h5"]).exists():
            raise FileNotFoundError(f"s0_production.prm.thermal_derate.method hourly needs temperature_h5 "
                                    f"(ReEDS inputs/profiles_temperature/temperature_state.h5); got {td.get('temperature_h5')!r}")
        first_y = int(p["stress_days"]["first_weather_year"])
        temps = state_temperatures(REPO / td["temperature_h5"], range(first_y - 1, first_y + 8), td["temperature_tz"])
        zst = zone_states(regions)
    for t in stp.itertuples():
        hh = int(str(t.timepoint_id)[-2:])
        for g in cap.itertuples():
            if hourly and g.curve is not None and t.timeseries in date:
                st = zst.get(g.gen_load_zone)
                stamp = pd.Timestamp(f"{date[t.timeseries]} {hh:02d}:00").tz_localize(td["temperature_tz"])
                if st in temps.columns and stamp in temps.index:
                    f_ = float(min(curve_for(cfg, g.curve, temps.at[stamp, st]), 0.4))
                else:
                    f_ = stress_for(cfg, g.prm_class, g.curve, t.season, td)
            else:
                f_ = stress_for(cfg, g.prm_class, g.curve, t.season, td)
            rows.append((g.GENERATION_PROJECT, t.timepoint_id, round(1 - f_, 6)))
    pd.DataFrame(rows, columns=["GENERATION_PROJECT", "TIMEPOINT", "prm_avail_frac"]).to_csv(
        folder / "prm_gen_availability.csv", index=False)
    dollar_year = int(first.get("target_usd_year", 2024))
    pen, how = penalty_per_mw_yr(p, dollar_year)
    params = {"prm_new_tx_derate": [float(p["new_tx_derate"])], "prm_shortfall_cost_per_mw_yr": [round(pen, 2)],
              "prm_import_cap_all_hours": [1]}
    allowance = float(p["imports"].get("new_tx_allowance") or 0.0)
    if allowance > 0:          # bill cases (§60): imports may also use this share of new interregional capacity
        params["prm_import_new_tx_allowance"] = [allowance]
    if rrows == "compact":
        params["prm_compact_capacity"] = [1]
    pd.DataFrame(params).to_csv(folder / "prm_params.csv", index=False)
    benchmark_rows(cfg).to_csv(folder / "prm_benchmarks.csv", index=False)
    log(f"prm regional: {len(set(regions.values()))} regions; margins "
        + "; ".join(f"{r.PRM_REGION} {r.PERIOD} {r.prm_margin:.4f} (RML {r.rml:.4f}, FOR_w {r.for_w:.4f})"
                    for r in m.itertuples())
        + f"; imports {p['imports']['mode']}; {len(stress)} stress timeseries ({len(stp)} timepoints, thermal derate "
          f"{td['method']}); shortfall penalty {how}; stress-day formulation {form}; reserve rows {rrows}"
        + (" (stress_light_timeseries.csv: dispatch, storage, transmission and the reserve test only)"
           if form == "light" else ""))
