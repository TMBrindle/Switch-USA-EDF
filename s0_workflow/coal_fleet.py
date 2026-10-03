"""Coal specification rev. 2 in the case build (s0_workflow/specs/coal/coal_spec.md §1.5, §2, §3): fleet
overrides and order holds on PowerGenome's unit table, before clustering, and the zonal coal CF caps by stage.

Runs inside pg_to_switch.py for S0 cases with s0_production.coal_spec.enabled (the new-defaults cases):

  apply_settings(settings)      per case/year, before PowerGenome: a technology per held unit, so each is its own
                                single-unit project ("Conventional Steam Coal Hold <plant> <gen>"), with the coal
                                entries of the technology-keyed settings copied to it; and coal-group units exempt
                                from the predetermined-retirement override (blocked_2030_*), so they retire in the
                                year the spec encodes
  unit_hooks(settings)          context manager around gc.create_all_generators(): wraps PowerGenome's
                                group_technologies (unit-level edits: dated retirements, removals, keep online,
                                conversions to gas steam, holds) and atb_fixed_var_om_existing (converted units'
                                heat rates). Nothing in PowerGenome is edited on disk.
  write_case_inputs(folder, ...) after the case is written: zonal caps by stage (H, own, N from the cap units; M from
                                the edited unit table), hold caps, and the checks against the spec's validation
                                tables: caps by stage, applied overrides, holds by stage. A mismatch stops the build.

The rules are in coal_spec.py; this module adapts them to PowerGenome's unit table, whose columns at the hook are
plant_id_eia, generator_id, technology_description (EIA technology, before grouping), model_region, the capacity
column (winter_capacity_mw), retirement_year (the model basis: PUDL 2024 planned dates, July-2025 860M retired),
retirement_age and the operating date columns.
"""
from __future__ import annotations

import contextlib
import copy
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from s0_workflow import coal_spec as cs

logger = logging.getLogger(__name__)
REPO = cs.REPO
HOLD_TECH = "Conventional Steam Coal Hold"
GAS_STEAM = "Natural Gas Steam Turbine"
NO_RETIREMENT = None

A_RETIRE, A_LATER, A_REMOVE = "retire", "retire later than model basis", "remove (out of service, not returning)"
A_KEEP = "keep online (planned retirement withdrawn or after 2045)"
A_CONVERT = "convert to gas"
A_HOLD_S0, A_HOLD_PERSIST = "hold online (S0: 2028 stage only)", "hold online (sensitivity holds_persist only; not in S0)"
A_IGCC = ("no change: IGCC partly recoded (CTs NG combined cycle, ST IGCC) - wait for consistent 860M coding "
          "(Tom 2026-10-03)")


def A_MEMBER(tech):
    return (f"tech-group member ({tech}): stays in coal cluster with the coal cap (Tom 2026-10-03; split out in "
            f"full refresh)")


def A_REVIEW(tech, esc):
    return f"REVIEW: now '{tech}' ({esc})"


A_ABSENT = "REVIEW: not in latest 860M"


# ---------------------------------------------------------------------------------------------- settings
def spec_settings(s0: dict) -> dict | None:
    c = (s0 or {}).get("coal_spec") or {}
    return c if c.get("enabled") else None


def hold_scenario(s0: dict) -> str:
    sc = ((s0 or {}).get("coal_holds") or {}).get("scenario", "s0") or "s0"
    if sc not in ("s0", "holds_persist"):
        raise ValueError(f"s0_production.coal_holds.scenario must be s0 or holds_persist, not {sc!r}")
    return sc


def hold_table(s0: dict) -> pd.DataFrame:
    h = cs.load_holds(REPO / ((s0.get("coal_holds") or {}).get("table") or "s0_workflow/data/coal_holds.csv"))
    return h


def scenario_holds(s0: dict) -> pd.DataFrame:
    """Held units of the case's scenario, with their encoded retirement year (None: no retirement in the horizon)."""
    if not ((s0.get("coal_holds") or {}).get("enabled", True)):
        return hold_table(s0).iloc[0:0]
    h = hold_table(s0)
    if hold_scenario(s0) == "s0":
        h = h[h.in_S0].copy()
        h["encoded_year"] = h.S0_encoded_retirement_year.astype(int)
        h["last_stage"] = h.S0_last_stage.astype(int)
    else:
        h = h[h.in_holds_persist].copy()
        h["encoded_year"] = None
        h["last_stage"] = h.persist_last_stage.astype(int)
    return h


def hold_tech(plant, gen) -> str:
    return f"{HOLD_TECH} {int(plant)} {cs.norm_gen(gen)}"


def apply_settings(s: dict, s0: dict) -> list[str]:
    """In place for one case/year: a PowerGenome technology per held unit (copying the "Conventional Steam Coal"
    entry of every technology-keyed settings dict; num_clusters 1), and the exemption of coal-group units from the
    predetermined-retirement override. Returns the hold technologies added."""
    techs = [hold_tech(p, g) for p, g in zip(*[scenario_holds(s0)[c] for c in ("plant_id_eia", "generator_id")])]
    for key, val in list(s.items()):
        if key == "tech_groups" or not isinstance(val, dict) or cs.CSC not in val:
            continue
        d = copy.deepcopy(val)
        for t in techs:
            d[t] = 1 if key == "num_clusters" else copy.deepcopy(val[cs.CSC])
        s[key] = d
    s["predetermined_retirement_override_exempt"] = ["coal"]
    return techs


# ---------------------------------------------------------------------------------------------- overrides (§2.2)
def _eff(y):
    """A date after the horizon counts as no retirement."""
    return int(y) if y is not None and pd.notna(y) and int(y) <= cs.HORIZON else None


def load_fleet860m(path: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    f = cs.read_table(path or REPO / "s0_workflow/data/coal_fleet_860m.csv", dtype={"Generator ID": str})
    op = f[f.sheet == "Operating"].drop_duplicates("kn").set_index("kn")
    rt = f[f.sheet == "Retired"].drop_duplicates("kn").set_index("kn")
    return op, rt


def model_units(units: pd.DataFrame, capacity_col: str = "winter_capacity_mw") -> pd.DataFrame:
    """Coal-group units of PowerGenome's unit table with a zone (and not out of service in the basis year)."""
    u = units[units.technology_description.isin(cs.COAL_GROUP) & units.model_region.notna()].copy()
    if "operational_status_code" in u:
        u = u[u.operational_status_code.astype(str) != "OS"]
    u["kn"] = cs.keys(u.plant_id_eia, u.generator_id)
    u["mw"] = pd.to_numeric(u[capacity_col], errors="coerce")
    u["base"] = [_eff(y) for y in u.retirement_year]
    return u


def derive_overrides(units: pd.DataFrame, op: pd.DataFrame, rt: pd.DataFrame, holds: pd.DataFrame,
                     capacity_col: str = "winter_capacity_mw") -> tuple[pd.DataFrame, dict]:
    """§2.2 actions for every coal-group unit of the model fleet against the latest 860M, in the spec's precedence
    (hold > Retired > OS > conversion > planned date > keep online). Returns (override rows, final) with
    final[kn] = (kind, encoded retirement year or None), kind coal | gas | held."""
    u = model_units(units, capacity_col)
    hk = dict(zip(holds.kn, holds.encoded_year)) if len(holds) else {}
    rows, final = [], {}
    for _, x in u.iterrows():
        k, base = x.kn, _eff(x.base)                               # (pandas holds None as NaN)
        r = {"plant_id_eia": int(x.plant_id_eia), "generator_id": str(x.generator_id), "kn": k, "zone": x.model_region,
             "model_winter_MW": x.mw, "model_technology": x.technology_description, "model_ret_year": base}
        o = op.loc[k] if k in op.index else None
        if k in hk:
            continue                                               # hold rows below
        if k in rt.index:
            y = int(rt.at[k, "Retirement Year"])
            final[k] = ("coal", y)
            if base is not None and base <= y:
                continue
            r.update(action=A_RETIRE, effective_year=y)
        elif o is None:
            final[k] = ("coal", base)
            r.update(action=A_ABSENT, effective_year=np.nan)
        elif o.status == "OS":
            final[k] = ("coal", 2026)
            if base is not None and base <= 2026:
                continue
            r.update(action=A_REMOVE, effective_year=2026)
        elif x.technology_description == cs.IGCC and o.Technology != cs.IGCC:
            final[k] = ("coal", base)
            r.update(action=A_IGCC, effective_year=np.nan)
        elif o.Technology not in cs.COAL_GROUP and o["Energy Source Code"] == "NG":
            R = _eff(o["Planned Retirement Year"])
            final[k] = ("gas", R)
            r.update(action=A_CONVERT, effective_year=o.conversion_year, convert_winter_MW=o["Net Winter Capacity (MW)"],
                     convert_planned_ret=R)
        elif o.Technology not in cs.COAL_GROUP:
            final[k] = ("coal", base)
            r.update(action=A_REVIEW(o.Technology, o["Energy Source Code"]), effective_year=np.nan)
        else:
            R = _eff(o["Planned Retirement Year"])
            final[k] = ("coal", R)
            if R == base:
                if x.technology_description in (cs.IGCC, cs.PETCOKE):
                    r.update(action=A_MEMBER(x.technology_description), effective_year=np.nan)
                else:
                    continue
            elif R is None:
                r.update(action=A_KEEP, effective_year=np.nan)
            else:
                r.update(action=A_RETIRE if (base is None or R < base) else A_LATER, effective_year=R)
        rows.append(r)
    ub = u.set_index("kn")
    for _, h in holds.iterrows():
        k = h.kn
        if k not in ub.index:
            raise ValueError(f"coal hold {h.plant_name} {h.generator_id} ({k}) is not in PowerGenome's unit table, so "
                             f"it can't be held: check the model basis (PUDL 2024, 860M July 2025)")
        final[k] = ("held", None if pd.isna(h.encoded_year) else int(h.encoded_year))
        rows.append({"plant_id_eia": int(h.plant_id_eia), "generator_id": str(h.generator_id), "kn": k,
                     "zone": ub.at[k, "model_region"], "model_winter_MW": h.winter_mw,
                     "model_technology": ub.at[k, "technology_description"], "model_ret_year": _eff(ub.at[k, "base"]),
                     "action": A_HOLD_S0 if h.in_S0 and pd.notna(h.encoded_year) else A_HOLD_PERSIST,
                     "effective_year": 2026, "hold_cap": h.hold_cap,
                     "encoded_retirement_year": None if pd.isna(h.encoded_year) else int(h.encoded_year)})
    return pd.DataFrame(rows), final


def _no_retirement_year(df: pd.DataFrame) -> pd.Series:
    """retirement_year that encodes "no retirement in the horizon" the way PowerGenome does for a unit without a
    planned date: operating year + retirement age (so pg_to_switch's build year = the operating year)."""
    age = pd.to_numeric(df.get("retirement_age", pd.Series(500, index=df.index)), errors="coerce").fillna(500)
    op = None
    for c in ("operating_date", "generator_operating_date", "current_planned_operating_date",
              "original_planned_operating_date"):
        if c in df:
            v = df[c]
            yr = v.dt.year if hasattr(v, "dt") else pd.to_numeric(v, errors="coerce")
            op = yr if op is None else op.fillna(yr)
    op = (op if op is not None else pd.Series(np.nan, index=df.index)).fillna(cs.HORIZON + 1 - 500)
    return (op + age).clip(lower=cs.HORIZON + 1)


def apply_overrides(units: pd.DataFrame, final: dict, ov: pd.DataFrame, capacity_cols=("winter_capacity_mw",)
                    ) -> pd.DataFrame:
    """Unit-level edits before clustering (§2.2, §2.3, §3.5). Returns a copy."""
    df = units.copy()
    kn = pd.Series(cs.keys(df.plant_id_eia, df.generator_id), index=df.index)
    conv = ov[ov.action == A_CONVERT].set_index("kn") if len(ov) else pd.DataFrame()
    none_year = _no_retirement_year(df)
    for k, (kind, y) in final.items():
        m = kn == k
        if not m.any():
            continue
        df.loc[m, "retirement_year"] = none_year[m] if y is None else y
        if kind == "gas":
            df.loc[m, "technology_description"] = GAS_STEAM
            if "energy_source_code_1" in df:
                df.loc[m, "energy_source_code_1"] = "NG"
            for c in capacity_cols:
                if c in df:
                    df.loc[m, c] = float(conv.at[k, "convert_winter_MW"])
        elif kind == "held":
            p, g = k.split("|")
            df.loc[m, "technology_description"] = hold_tech(p, g)
    return df


# ---------------------------------------------------------------------------------------------- model MW (§1.5)
def model_mw(units: pd.DataFrame, final: dict, stage: int, capacity_col: str = "winter_capacity_mw"
             ) -> tuple[pd.Series, pd.Series]:
    """Model coal MW in service in `stage` by zone, before and after the overrides (coal clusters only: held and
    converted units excluded after). In service in p: encoded retirement year >= p or none."""
    u = model_units(units, capacity_col)
    alive = lambda y: y is None or y >= stage  # noqa: E731
    before = u[[alive(_eff(b)) for b in u.base]].groupby("model_region").mw.sum()
    keep = [k for k, (kind, y) in final.items() if kind == "coal" and alive(y)]
    after = u[u.kn.isin(keep)].groupby("model_region").mw.sum()
    return before, after


# ---------------------------------------------------------------------------------------------- PowerGenome hooks
class _State:
    """Per (case, model year): the derived overrides, final states and the unit table after the edits."""
    store: dict = {}


def state(case: str, year: int) -> dict | None:
    return _State.store.get((str(case), int(year)))


@contextlib.contextmanager
def unit_hooks(settings: dict):
    """Around gc.create_all_generators() for one case/year with s0_production.coal_spec enabled: unit-level edits
    in PowerGenome's group_technologies step (the unit table still has EIA technologies there) and the converted
    units' heat rates in atb_fixed_var_om_existing. No-op for other cases."""
    s0 = (settings.get("s0_production") or {})
    if not (s0.get("enabled") and spec_settings(s0)):
        yield None
        return
    import powergenome.generators as pgg

    cfg = spec_settings(s0)
    op, rt = load_fleet860m(REPO / cfg.get("fleet_table", "s0_workflow/data/coal_fleet_860m.csv"))
    holds = scenario_holds(s0)
    st = cs.read_table(REPO / cfg.get("plant_fuel_table", "s0_workflow/data/coal_plant_st_fuel.csv"))
    cap_col = settings.get("capacity_col", "capacity_mw")
    rec = {"case": settings.get("case_id"), "year": int(settings["model_year"])}
    orig_group, orig_om = pgg.group_technologies, pgg.atb_fixed_var_om_existing

    def group_technologies(df, *a, **k):
        if "technology_description" in df and "plant_id_eia" in df and "retirement_year" in df and \
                df.technology_description.isin(cs.COAL_GROUP).any() and "final" not in rec:
            ov, final = derive_overrides(df, op, rt, holds, cap_col)
            rec.update(overrides=ov, final=final, units=df.copy())
            df = apply_overrides(df, final, ov, tuple(c for c in (cap_col, "capacity_mw") if c in df))
            logger.info("coal spec %s/%s: %d overrides applied before clustering (%s)", rec["case"], rec["year"],
                        len(ov), ov.action.str.split(r" \(|:").str[0].value_counts().to_dict() if len(ov) else {})
        return orig_group(df, *a, **k)

    def atb_fixed_var_om_existing(units, *a, **k):
        if "final" in rec:
            u = units.reset_index()
            kn = pd.Series(cs.keys(u.plant_id_eia, u.generator_id), index=u.index)
            hr = {}
            for key, (kind, _y) in rec["final"].items():
                if kind != "gas":
                    continue
                plant = int(key.split("|")[0])
                coal = st[(st.plant == plant) & (st.fuel == "COAL") & (st.year == 2024)]
                coal_hr = coal.mmbtu.sum() / coal.mwh.sum() if coal.mwh.sum() > 0 else None
                v, src, flag = cs.plant_gas_heat_rate(st, plant, coal_hr, cfg.get("heat_rate_data_through"))
                m = kn == key
                if pd.isna(v):
                    v, src = float(u.loc[m, "heat_rate_mmbtu_mwh"].iloc[0]), "PowerGenome unit heat rate kept (flag)"
                u.loc[m, "heat_rate_mmbtu_mwh"] = v
                hr[key] = (v, src, flag)
            rec["heat_rates"] = hr
            units = u.set_index(units.index.names)
        return orig_om(units, *a, **k)

    pgg.group_technologies, pgg.atb_fixed_var_om_existing = group_technologies, atb_fixed_var_om_existing
    try:
        yield rec
    finally:
        pgg.group_technologies, pgg.atb_fixed_var_om_existing = orig_group, orig_om
        if "final" in rec:
            _State.store[(str(rec["case"]), rec["year"])] = rec


# ---------------------------------------------------------------------------------------------- case inputs and checks
def _hold_class(a) -> str:
    return "hold online" if isinstance(a, str) and a.startswith("hold online") else a


def check_overrides(ov: pd.DataFrame, expected: pd.DataFrame, scenario: str) -> pd.DataFrame:
    """Applied overrides vs coal_spec_overrides.csv (§5: same plant/generator/action set, effective year exact,
    MW +-0.1). Hold actions compare as one class; persist-only rows are expected only in holds_persist."""
    e = expected.copy()
    if scenario == "s0":
        e = e[e.action != A_HOLD_PERSIST]
    e["kn"] = cs.keys(e.plant_id_eia, e.generator_id)
    m = ov[["kn", "plant_id_eia", "generator_id", "zone", "action", "effective_year", "model_winter_MW"]].merge(
        e[["kn", "action", "effective_year", "model_winter_MW"]], on="kn", how="outer", suffixes=("", "_spec"),
        indicator=True)
    eq = lambda a, b: (a == b) | (pd.isna(a) & pd.isna(b))  # noqa: E731
    m["ok"] = ((m._merge == "both") & eq(m.action.map(_hold_class), m.action_spec.map(_hold_class))
               & eq(pd.to_numeric(m.effective_year), pd.to_numeric(m.effective_year_spec))
               & (((m.model_winter_MW - m.model_winter_MW_spec).abs() <= 0.1)
                  | (m.model_winter_MW.isna() & m.model_winter_MW_spec.isna())))
    m["status"] = np.where(m._merge == "left_only", "only in build", np.where(m._merge == "right_only", "only in spec",
                                                                              np.where(m.ok, "ok", "differs")))
    return m.drop(columns=["_merge"]).sort_values("kn")


def holds_by_stage(final: dict, holds_all: pd.DataFrame, stages, scenario: str, expected: pd.DataFrame) -> pd.DataFrame:
    """Held / retired by unit and stage vs coal_spec_hold_by_stage.csv (exact)."""
    e = expected.copy()
    e["kn"] = cs.keys(e.plant_id_eia, e.generator_id)
    col = "S0" if scenario == "s0" else "holds_persist"
    rows = []
    for _, h in holds_all[holds_all.in_S0 | holds_all.in_holds_persist].iterrows():
        kind, y = final.get(h.kn, ("not held", None))
        for p in stages:
            held = kind == "held" and (y is None or y >= p)
            got = "held" if held else ("retired" if h["in_S0" if scenario == "s0" else "in_holds_persist"]
                                       else "retired (not held in S0)")
            x = e[(e.kn == h.kn) & (e.stage == p)]
            want = x[col].iloc[0] if len(x) else None
            rows.append({"plant_id_eia": h.plant_id_eia, "generator_id": h.generator_id, "plant_name": h.plant_name,
                         "zone": h.zone, "stage": p, "scenario": scenario, "build": got, "spec": want,
                         "cap_while_held": h.hold_cap if held else np.nan,
                         "spec_cap": x.cap_while_held.iloc[0] if len(x) else np.nan,
                         "ok": got == want and (not held or abs(h.hold_cap - x.cap_while_held.iloc[0]) < 1e-9)})
    return pd.DataFrame(rows)


def stage_caps(cu: pd.DataFrame, held: set, rec: dict, stage: int, capacity_col: str, members: dict | None,
               expected: pd.DataFrame | None, tol: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per-BA table with the comparison, per-load-zone caps) for one stage."""
    hist, nat = cs.stage_history(cu, stage, held)
    before, after = model_mw(rec["units"], rec["final"], stage, capacity_col)
    zv = cs.apply_rule(hist, before, after, nat)
    zv["stage"] = stage
    if expected is not None:
        x = expected[expected.stage == stage].set_index("zone")
        zv = zv.join(x[["expected_cap", "rule", "hist_MW", "model_MW_after_overrides"]].add_suffix("_spec"), how="outer")
        zv["stage"], zv["national_N"] = stage, nat
        zv["cap_diff"] = zv.expected_cap - zv.expected_cap_spec
        zv["M_diff"] = zv.model_MW_after_overrides - zv.model_MW_after_overrides_spec.fillna(0)
        zv["ok"] = ((zv.cap_diff.abs() <= tol) & (zv.rule == zv.rule_spec)
                    & ((zv.hist_MW.fillna(0) - zv.hist_MW_spec.fillna(0)).abs() <= 1.0))
        # no model coal after the overrides on either side: no coal clusters, no cap to compare (§1.5)
        zv["ok"] |= ((zv.model_MW_after_overrides.fillna(0) <= 0) & (zv.model_MW_after_overrides_spec.fillna(0) <= 0)
                     & ((zv.hist_MW.fillna(0) - zv.hist_MW_spec.fillna(0)).abs() <= 1.0))
    lz = cs.aggregate_rule(zv, members, nat) if members else zv
    return zv.reset_index(), lz


def write_case_inputs(folder: Path, s0: dict, scen_settings_dict: dict, log) -> None:
    """Zonal coal caps by stage, hold caps, and the checks (§1.5, §2, §3, §5). Raises on a mismatch."""
    from s0_workflow.production import _read  # noqa: PLC0415
    cfg = spec_settings(s0)
    if cfg is None:
        return
    folder = Path(folder)
    first = next(iter(scen_settings_dict.values()))
    case, cap_col = first.get("case_id"), first.get("capacity_col", "capacity_mw")
    years = sorted(int(y) for y in scen_settings_dict)
    tol, scen = float(cfg.get("tolerance", 0.001)), hold_scenario(s0)
    cu = cs.load_cap_units(REPO / cfg.get("cap_units_table", "s0_workflow/data/coal_cap_units_860m.csv"))
    cu = cu[cu.status.isin(cfg.get("cap_statuses", ["OP", "SB"]))]
    holds_all = hold_table(s0)
    held = cs.held_keys(holds_all)
    exp_caps = pd.read_csv(REPO / cfg.get("expected_caps", "s0_workflow/specs/coal/coal_spec_expected_caps_by_stage.csv"))
    recs = {}
    for y in years:
        r = state(case, y)
        if r is None:
            raise RuntimeError(f"coal spec: no PowerGenome unit table recorded for {case}/{y}; the unit hook "
                               f"(coal_fleet.unit_hooks) did not run")
        recs[y] = r
    zone_map = first.get("_zone_map") or {}
    members = {}
    for ba, z in zone_map.items():
        members.setdefault(z, []).append(ba)
    tables, caps = [], {}
    for y in years:
        t, lz = stage_caps(cu, held, recs[y], y, cap_col, members or None, exp_caps, tol)
        tables.append(t)
        caps[y] = lz
    T = pd.concat(tables, ignore_index=True)
    T.round(6).to_csv(folder / "coal_caps_by_stage.csv", index=False)

    # caps on the coal clusters (gen_energy_source coal, not hold projects) and on the hold projects
    gi = _read(folder, "gen_info.csv", dtype=str, keep_default_na=False)
    tech = gi.get("gen_tech", pd.Series("", index=gi.index)).astype(str)
    is_hold = tech.str.startswith(HOLD_TECH)
    coal = (gi.gen_energy_source.str.lower() == "coal") & ~is_hold
    forced = pd.to_numeric(gi.get("gen_forced_outage_rate"), errors="coerce").fillna(0)
    if "gen_max_annual_availability" not in gi:
        gi["gen_max_annual_availability"] = "."
    by_period = []
    for i in gi.index[coal]:
        z = gi.at[i, "gen_load_zone"]
        for y in years:
            c = caps[y].expected_cap.get(z, caps[y].national_N.iloc[0])
            by_period.append((gi.at[i, "GENERATION_PROJECT"], y, cs.availability(c, forced[i])))
    hcap = dict(zip([hold_tech(p, g) for p, g in zip(holds_all.plant_id_eia, holds_all.generator_id)], holds_all.hold_cap))
    for i in gi.index[is_hold]:
        c = hcap.get(tech[i])
        if c is None:
            raise ValueError(f"coal hold project {gi.at[i, 'GENERATION_PROJECT']} ({tech[i]}) not in the hold table")
        for y in years:
            by_period.append((gi.at[i, "GENERATION_PROJECT"], y, cs.availability(c, forced[i])))
    if "gen_can_retire_early" in gi:
        gi.loc[is_hold, "gen_can_retire_early"] = "0"           # §3.5: no economic retirement while held
    bp = pd.DataFrame(by_period, columns=["GENERATION_PROJECT", "PERIOD", "gen_max_annual_availability_by_period"])
    first_p = bp[bp.PERIOD == years[0]].set_index("GENERATION_PROJECT")["gen_max_annual_availability_by_period"]
    m = gi.GENERATION_PROJECT.isin(first_p.index)
    gi.loc[m, "gen_max_annual_availability"] = gi.loc[m, "GENERATION_PROJECT"].map(first_p).round(6).map(repr)
    gi.to_csv(folder / "gen_info.csv", index=False)
    if len(years) > 1:                                             # one cap per period (mode B windows)
        bp.round(6).to_csv(folder / "gen_max_annual_availability_by_period.csv", index=False)

    # checks: overrides (from the first stage's unit table; the same basis every year), holds by stage
    ov = check_overrides(recs[years[0]]["overrides"],
                         pd.read_csv(REPO / cfg.get("expected_overrides", "s0_workflow/specs/coal/coal_spec_overrides.csv"),
                                     dtype={"generator_id": str}), scen)
    ov.to_csv(folder / "coal_overrides_applied.csv", index=False)
    hb = holds_by_stage(recs[years[0]]["final"], holds_all, years, scen,
                        pd.read_csv(REPO / cfg.get("expected_holds", "s0_workflow/specs/coal/coal_spec_hold_by_stage.csv"),
                                    dtype={"generator_id": str}))
    hb.to_csv(folder / "coal_holds_by_stage.csv", index=False)
    hr = recs[years[0]].get("heat_rates", {})
    if hr:
        pd.DataFrame([{"kn": k, "heat_rate": v, "source": s, "flag": f} for k, (v, s, f) in hr.items()]).to_csv(
            folder / "coal_converted_heat_rates.csv", index=False)

    bad_caps = T[~T.ok.fillna(False)] if "ok" in T else T.iloc[0:0]
    bad_ov, bad_h = ov[ov.status != "ok"], hb[~hb.ok]
    m_diff = T[T.M_diff.abs() > 1.0] if "M_diff" in T else T.iloc[0:0]
    for y in years:
        t = T[T.stage == y]
        log(f"coal caps {y}: N {t.national_N.iloc[0]:.4f}; {int((t.model_MW_after_overrides > 0).sum())} zones with coal "
            f"({t.model_MW_after_overrides.sum() / 1e3:.2f} GW after overrides, {t.model_MW_before.sum() / 1e3:.2f} GW "
            f"before); rules {t.rule.str.replace(NO_COAL_LABEL, '', regex=False).value_counts().to_dict()}; "
            f"vs spec: {int(t.ok.sum())}/{len(t)} zones ok (cap +-{tol}, rule, H +-1 MW)")
    log(f"coal overrides: {int((ov.status == 'ok').sum())}/{len(ov)} as coal_spec_overrides.csv "
        f"({recs[years[0]]['overrides'].action.map(_hold_class).value_counts().to_dict()}); holds ({scen}): "
        f"{int(hb.ok.sum())}/{len(hb)} unit-stages as coal_spec_hold_by_stage.csv; "
        f"{int(is_hold.sum())} hold projects in the case")
    if len(m_diff):
        log(f"coal caps: model MW differs from the spec's reconstruction by > 1 MW in {len(m_diff)} zone-stages "
            f"(reported, not failed; coal_spec.md §2.1): "
            + "; ".join(f"{r.zone} {r.stage} {r.M_diff:+.0f}" for r in m_diff.itertuples()))
    problems = []
    if len(bad_caps):
        problems.append(f"{len(bad_caps)} zone-stage caps differ from coal_spec_expected_caps_by_stage.csv: "
                        + "; ".join(f"{r.zone} {r.stage}: {r.expected_cap:.4f} vs {r.expected_cap_spec:.4f} "
                                    f"({r.rule} / {r.rule_spec})" for r in bad_caps.head(10).itertuples()))
    if len(bad_ov):
        problems.append(f"{len(bad_ov)} overrides differ from coal_spec_overrides.csv: "
                        + "; ".join(f"{r.kn} {r.status}: {r.action} {r.effective_year} vs {r.action_spec} "
                                    f"{r.effective_year_spec}" for r in bad_ov.head(10).itertuples()))
    if len(bad_h):
        problems.append(f"{len(bad_h)} hold unit-stages differ from coal_spec_hold_by_stage.csv: "
                        + "; ".join(f"{r.plant_name} {r.generator_id} {r.stage}: {r.build} vs {r.spec}"
                                    for r in bad_h.head(10).itertuples()))
    if problems:
        for p in problems:
            log("COAL SPEC CHECK FAILED: " + p)
        log.write()
        if cfg.get("fail_on_mismatch", True):
            raise ValueError(f"coal spec check failed for {case} {years} (see {folder}/coal_*.csv):\n" + "\n".join(problems))


NO_COAL_LABEL = cs.NO_COAL
