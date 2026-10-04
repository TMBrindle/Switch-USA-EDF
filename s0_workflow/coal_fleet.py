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


def retirement_option(s0: dict) -> str:
    """s0_production.retirements_pre2030: block_all (S0 default) | planned_only | unrestricted | legacy."""
    o = (s0 or {}).get("retirements_pre2030", "block_all") or "block_all"
    if o not in cs.RETIREMENT_OPTIONS + ("legacy",):
        raise ValueError(f"s0_production.retirements_pre2030 must be one of {cs.RETIREMENT_OPTIONS}, not {o!r}")
    return o


def pre2030_rule(settings: dict) -> dict | None:
    """The predetermined-retirement push that applies to coal in this case: the window rule among the case's
    predetermined_retirement_override settings whose technologies match coal (block_all sets fedpol's
    blocked_2030_coal_gas rule; planned_only and unrestricted remove it)."""
    val = settings.get("predetermined_retirement_override")
    for r in (val if isinstance(val, list) else [val] if val else []):
        if r.get("mode", "window") == "window" and any(t.lower() in cs.CSC.lower() for t in r["technologies"]):
            return r
    return None


def expected_path(cfg: dict, key: str, default: str, option: str) -> Path:
    return REPO / str(cfg.get(key, default)).format(option=option)


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


def apply_overrides(units: pd.DataFrame, final: dict, ov: pd.DataFrame, capacity_cols=("winter_capacity_mw",),
                    rule: dict | None = None) -> pd.DataFrame:
    """Unit-level edits before clustering (§2.2, §2.3, §3.5); returns a copy. Only units with an override row change
    (a unit the model basis already retires by its 860M retirement keeps its basis year). With `rule` (pre-2030 option
    block_all), coal-group and held units with a retirement year in the rule's window are then pushed
    (coal_spec.pushed_year), except the out-of-service removals (rev. 2.1: physical status, removed in every option).
    The case build pushes gas as well (unit_hooks, push_pre2030)."""
    df = units.copy()
    kn = pd.Series(cs.keys(df.plant_id_eia, df.generator_id), index=df.index)
    conv = ov[ov.action == A_CONVERT].set_index("kn") if len(ov) else pd.DataFrame()
    rows = set(ov.kn) if len(ov) else set()
    none_year = _no_retirement_year(df)
    for k, (kind, y) in final.items():
        m = kn == k
        if k not in rows or not m.any():
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
    df = drop_removals(df, removal_keys(ov))[0]
    if rule is not None:
        td = df.technology_description.astype(str)
        coal = df.technology_description.isin(cs.COAL_GROUP) | td.str.startswith(HOLD_TECH)
        df = push_pre2030(df, rule, td.where(~df.technology_description.isin(cs.COAL_GROUP), cs.CSC).where(coal, ""))
    return df


def removal_keys(ov: pd.DataFrame) -> set:
    """Units the spec removes as out of service (rev. 2.1: physical status, removed in every option)."""
    return set(ov.loc[ov.action == A_REMOVE, "kn"]) if len(ov) else set()


EXPECTED_REMOVALS = 5      # coal_spec.md §2.2: Sandy Creek S01, Big Cajun 2-1, Merrimack 2, Warrick 2, Biron Mill GEN5


def spec_removals(path: Path | None = None) -> pd.DataFrame:
    """The spec's out-of-service removals (coal_spec_overrides.csv, action remove): kn, plant, generator, zone, MW."""
    e = pd.read_csv(path or REPO / "s0_workflow/specs/coal/coal_spec_overrides.csv", dtype={"generator_id": str})
    e = e[e.action == A_REMOVE]
    return pd.DataFrame({"kn": cs.keys(e.plant_id_eia, e.generator_id), "plant_id_eia": e.plant_id_eia.astype(int).values,
                         "generator_id": e.generator_id.values, "plant_name": e.plant_name.values, "zone": e.zone.values,
                         "winter_MW": e.model_winter_MW.values})


def _frame_keys(df: pd.DataFrame) -> pd.Series | None:
    """plant|generator keys of a PowerGenome frame (plant_id_eia and generator_id as columns or index levels), or
    None for a frame without generator ids (plant-level tables)."""
    names = set(df.columns) | {n for n in df.index.names if n}
    if not {"plant_id_eia", "generator_id"} <= names:
        return None
    col = lambda c: df[c].values if c in df.columns else df.index.get_level_values(c)  # noqa: E731
    return pd.Series(cs.keys(pd.Series(col("plant_id_eia")), pd.Series(col("generator_id")))).set_axis(df.index)


def drop_removals(df: pd.DataFrame, keys) -> tuple[pd.DataFrame, set]:
    """A PowerGenome unit table without the out-of-service removals (rows deleted before clustering, so neither
    the clusters nor fedpol's apply_predetermined_retirement_override, which runs on the clusters, ever see them).
    Returns (table, the keys that were in it)."""
    kn = _frame_keys(df)
    if kn is None:
        return df, set()
    m = kn.isin(set(keys)).values
    return df.loc[~m].copy(), set(kn[m])


T860, T860M = "EIA-860 units", "860M new generators"


def removals_record(spec: pd.DataFrame, derived: set, where: dict) -> pd.DataFrame:
    """One row per removal (the spec's, plus any the build derives that the spec doesn't list) with the PowerGenome
    tables it was deleted from: its EIA-860 units (the table the overrides are derived on) and the 860M generators
    PowerGenome adds for 860M Operating units missing from that table (which a deleted unit would otherwise come
    back as, and a plant without a plant-map region only ever enters as); none: never in either."""
    r = spec.copy()
    extra = sorted(set(derived) - set(r.kn))
    if extra:
        r = pd.concat([r, pd.DataFrame({"kn": extra})], ignore_index=True)
    r["in_spec"] = r.kn.isin(set(spec.kn))
    r["derived_override"] = r.kn.isin(set(derived))
    r["status"] = ["deleted from " + " and ".join(where[k]) if where.get(k) else "not in PowerGenome's unit tables"
                   for k in r.kn]
    return r


def removals_problems(r: pd.DataFrame) -> list[str]:
    """The removal count check: exactly EXPECTED_REMOVALS, all in the spec, none left in the unit table."""
    p = []
    if len(r) != EXPECTED_REMOVALS:
        p.append(f"{len(r)} out-of-service removals, expected {EXPECTED_REMOVALS} (coal_spec.md §2.2)")
    if (~r.in_spec).any():
        p.append("removals not in coal_spec_overrides.csv: " + ", ".join(r.kn[~r.in_spec]))
    return p


def removals_line(r: pd.DataFrame) -> str:
    """The build log's removal line: "coal removals: 5 removed (...)" with where each was deleted."""
    by = r.groupby("status", sort=False).kn.apply(list)
    return (f"coal removals: {len(r)} removed before clustering (" + "; ".join(
        f"{len(v)} {st}: " + ", ".join(v) for st, v in by.items()) + ")")


def push_pre2030(df: pd.DataFrame, rule: dict, cluster_tech: pd.Series, exclude=()) -> pd.DataFrame:
    """block_all's push on PowerGenome's unit table, before clustering: units whose cluster technology (after
    PowerGenome's grouping, as fedpol's apply_predetermined_retirement_override matches it) contains one of the rule's
    technologies and whose retirement year is in the rule's window get target + 1 (coal_spec.pushed_year: through the
    2030 stage in Switch and in PowerGenome's model year 2030). `exclude`: unit keys never pushed. Returns a copy."""
    df = df.copy()
    techs = [t.lower() for t in rule["technologies"]]
    m = cluster_tech.astype(str).str.lower().apply(lambda x: any(t in x for t in techs))
    m &= ~pd.Series(cs.keys(df.plant_id_eia, df.generator_id), index=df.index).isin(set(exclude))
    df.loc[m, "retirement_year"] = [cs.pushed_year(y, rule) for y in df.loc[m, "retirement_year"]]
    return df


# ---------------------------------------------------------------------------------------------- model MW (§1.5)
def model_mw(units: pd.DataFrame, edited: pd.DataFrame, stage: int, capacity_col: str = "winter_capacity_mw",
             rule: dict | None = None) -> tuple[pd.Series, pd.Series]:
    """Model coal MW in service in `stage` by zone: before the overrides (the model basis, with the pre-2030 push
    if any) and after (`edited`, from apply_overrides: coal-group technologies only, so held and converted units
    are out). In service in stage p: encoded retirement year >= p (Switch --retire early)."""
    def mw(df, push):
        u = df[df.technology_description.isin(cs.COAL_GROUP) & df.model_region.notna()]
        if "operational_status_code" in u:
            u = u[u.operational_status_code.astype(str) != "OS"]
        y = pd.to_numeric(u.retirement_year, errors="coerce")
        if push is not None:
            y = y.map(lambda v: cs.pushed_year(v, push))
        alive = y.isna() | (y >= stage)
        return pd.to_numeric(u.loc[alive, capacity_col], errors="coerce").groupby(u.loc[alive, "model_region"]).sum()
    return mw(units, rule), mw(edited, None)


def hold_years(edited: pd.DataFrame) -> dict:
    """Encoded retirement year (None: beyond the horizon) of every hold project in the edited unit table."""
    h = edited[edited.technology_description.astype(str).str.startswith(HOLD_TECH)]
    return {k: _eff(y) for k, y in zip(cs.keys(h.plant_id_eia, h.generator_id), h.retirement_year)}


# ---------------------------------------------------------------------------------------------- PowerGenome hooks
class _State:
    """Per (case, model year): the derived overrides, final states and the unit table after the edits."""
    store: dict = {}


def state(case: str, year: int) -> dict | None:
    return _State.store.get((str(case), int(year)))


@contextlib.contextmanager
def unit_hooks(settings: dict):
    """Around gc.create_all_generators() for one S0 case/year with the coal spec or retirements_pre2030 block_all:
    unit-level edits in PowerGenome's group_technologies step (the unit table still has EIA technologies there) and
    the converted units' heat rates in atb_fixed_var_om_existing. block_all's push of coal, held and gas units dated
    2026-29 is encoded there as 2031 (push_pre2030; fedpol's own code then leaves them alone). No-op for other cases,
    including the legacy regression case."""
    s0 = (settings.get("s0_production") or {})
    rule = pre2030_rule(settings) if s0.get("enabled") and retirement_option(s0) == "block_all" else None
    if not (s0.get("enabled") and (spec_settings(s0) or rule)):
        yield None
        return
    import powergenome.generators as pgg

    cfg = spec_settings(s0) or {}
    op, rt = load_fleet860m(REPO / cfg.get("fleet_table", "s0_workflow/data/coal_fleet_860m.csv"))
    holds = scenario_holds(s0)
    removals = spec_removals(REPO / cfg["expected_overrides"] if cfg.get("expected_overrides") else None)
    st = cs.read_table(REPO / cfg.get("plant_fuel_table", "s0_workflow/data/coal_plant_st_fuel.csv"))
    cap_col = settings.get("capacity_col", "capacity_mw")
    rec = {"case": settings.get("case_id"), "year": int(settings["model_year"]), "rule": rule}
    derived, where = set(), {}
    orig_group, orig_om = pgg.group_technologies, pgg.atb_fixed_var_om_existing

    def group_technologies(df, *a, **k):
        if "technology_description" in df and "plant_id_eia" in df and "retirement_year" in df and "done" not in rec:
            rec["done"] = True                                  # the existing-unit table: the first such frame
            if cfg.get("enabled") and df.technology_description.isin(cs.COAL_GROUP).any():
                ov, final = derive_overrides(df, op, rt, holds, cap_col)
                rec.update(overrides=ov, final=final, units=df.copy())
                derived.update(removal_keys(ov))
            # the out-of-service removals: deleted in every pre-2030 option, before clustering (so fedpol's push,
            # which runs on the clusters afterwards, has nothing of theirs to move into 2030)
            df, gone = drop_removals(df, set(removals.kn) | derived)
            for key in gone:
                where.setdefault(key, []).append(T860)
            if "final" in rec:
                df = apply_overrides(df, rec["final"], rec["overrides"],
                                     tuple(c for c in (cap_col, "capacity_mw") if c in df))
                ov = rec["overrides"]
                logger.info("coal spec %s/%s: %d overrides applied before clustering (%s)", rec["case"], rec["year"],
                            len(ov), ov.action.str.split(r" \(|:").str[0].value_counts().to_dict() if len(ov) else {})
            if rule is not None:
                cluster_tech = orig_group(df.copy(), *a, **k).technology_description     # as fedpol's rule sees it
                before = pd.to_numeric(df.retirement_year, errors="coerce")
                df = push_pre2030(df, rule, cluster_tech)
                moved = pd.to_numeric(df.retirement_year, errors="coerce") != before
                rec["pushed"] = df.loc[moved, ["plant_id_eia", "generator_id", "technology_description"]].assign(
                    cluster_technology=cluster_tech[moved], from_year=before[moved], to_year=df.retirement_year[moved])
                logger.info("retirements_pre2030 block_all %s/%s: %d units dated %s-%s encoded %s (%s)", rec["case"],
                            rec["year"], int(moved.sum()), *rule["window"], int(rule["target_year"]) + 1,
                            cluster_tech[moved].value_counts().to_dict())
            if "final" in rec:
                rec["edited"] = df.copy()
        elif "done" in rec:
            # later unit tables: the 860M Operating units PowerGenome adds because they aren't in its EIA-860 units
            # (import_new_generators; the removals deleted above, and plants without a plant-map region), proposed
            df, gone = drop_removals(df, set(removals.kn) | derived)
            for key in gone:
                where.setdefault(key, []).append(T860M)
            kn = _frame_keys(df)
            if kn is not None and "technology_description" in df:
                c = df.technology_description.isin(cs.COAL_GROUP).values
                if c.any():                                     # reported by write_case_inputs (not edited)
                    mw = df[cap_col] if cap_col in df else pd.Series(np.nan, index=df.index)
                    rec.setdefault("new_860m_coal", []).extend(
                        f"{k} {t} {float(w):.1f} MW" for k, t, w in zip(kn[c], df.technology_description[c], mw[c]))
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
        if "done" in rec:
            rec["removals"] = removals_record(removals, derived, where)
            logger.info("%s/%s: %s", rec["case"], rec["year"], removals_line(rec["removals"]))
        if "final" in rec:
            _State.store[(str(rec["case"]), rec["year"])] = rec
    bad = removals_problems(rec["removals"]) if "removals" in rec else []
    if bad:
        raise ValueError(f"coal spec {rec['case']}/{rec['year']}: " + "; ".join(bad))


# ---------------------------------------------------------------------------------------------- case inputs and checks
def _hold_class(a) -> str:
    return "hold online" if isinstance(a, str) and a.startswith("hold online") else a


def check_overrides(ov: pd.DataFrame, expected: pd.DataFrame, scenario: str, absent=(), units: pd.DataFrame | None = None,
                    capacity_col: str = "winter_capacity_mw", plant_map: dict | None = None) -> pd.DataFrame:
    """Applied overrides vs coal_spec_overrides.csv (§5: same plant/generator/action set, effective year exact,
    MW +-0.1). Hold actions compare as one class; persist-only rows are expected only in holds_persist. A spec row
    with no override in the build passes when (Tom, 2026-10-04):
    - `absent`: it is a removal that isn't in PowerGenome's EIA-860 units (deleted from the 860M generators
      PowerGenome adds, or never in the model);
    - its plant isn't in reeds_plant_map.csv, so it never enters PowerGenome's EIA-860 units (cs.NOT_IN_MODEL);
    - "already satisfied": `units` (PowerGenome's unit table before the edits) already has what a retire / retire
      later / keep online row wants (the same year, or both gone before the first stage; no date for keep online),
      so the override is a no-op (e.g. Brandon Shores 1 / 2 2029, Stanton 1 with no date)."""
    e = expected.copy()
    if scenario == "s0":
        e = e[e.action != A_HOLD_PERSIST]
    e["kn"] = cs.keys(e.plant_id_eia, e.generator_id)
    m = ov[["kn", "plant_id_eia", "generator_id", "zone", "action", "effective_year", "model_winter_MW"]].merge(
        e[["kn", "plant_id_eia", "generator_id", "zone", "action", "effective_year", "model_winter_MW"]].rename(
            columns={"plant_id_eia": "_p", "generator_id": "_g", "zone": "_z"}),
        on="kn", how="outer", suffixes=("", "_spec"), indicator=True)
    for c, x in (("plant_id_eia", "_p"), ("generator_id", "_g"), ("zone", "_z")):
        m[c] = m[c].where(m[c].notna(), m[x])
    m = m.drop(columns=["_p", "_g", "_z"])
    eq = lambda a, b: (a == b) | (pd.isna(a) & pd.isna(b))  # noqa: E731
    m["ok"] = ((m._merge == "both") & eq(m.action.map(_hold_class), m.action_spec.map(_hold_class))
               & eq(pd.to_numeric(m.effective_year), pd.to_numeric(m.effective_year_spec))
               & (((m.model_winter_MW - m.model_winter_MW_spec).abs() <= 0.1)
                  | (m.model_winter_MW.isna() & m.model_winter_MW_spec.isna())))
    m["status"] = np.where(m._merge == "left_only", "only in build", np.where(m._merge == "right_only", "only in spec",
                                                                              np.where(m.ok, "ok", "differs")))
    spec_only = m._merge == "right_only"
    gone = spec_only & (m.action_spec == A_REMOVE) & m.kn.isin(set(absent))
    m.loc[gone, "ok"], m.loc[gone, "status"] = True, "ok (removal: not in PowerGenome's EIA-860 units)"
    unmapped = spec_only & ~gone & ~cs.in_plant_map(m.plant_id_eia.astype(float).astype(int), plant_map)
    m.loc[unmapped, "ok"], m.loc[unmapped, "status"] = True, cs.NOT_IN_MODEL
    if units is not None:
        u = model_units(units, capacity_col).drop_duplicates("kn").set_index("kn")
        for i in m.index[spec_only & ~gone & ~unmapped & m.action_spec.isin([A_RETIRE, A_LATER, A_KEEP])
                         & m.kn.isin(u.index)]:
            y, want = _eff(u.at[m.at[i, "kn"], "base"]), _eff(m.at[i, "effective_year_spec"])   # (None is NaN there)
            if y == want or (y is not None and want is not None and max(y, want) < cs.STAGES[0]):
                m.loc[i, ["ok", "status", "model_winter_MW"]] = [
                    True, f"ok (already satisfied: model {'no date' if y is None else y})", u.at[m.at[i, "kn"], "mw"]]
    return m.drop(columns=["_merge"]).sort_values("kn")


def holds_by_stage(years_held: dict, holds_all: pd.DataFrame, stages, scenario: str, expected: pd.DataFrame
                   ) -> pd.DataFrame:
    """Held / retired by unit and stage vs the hold-by-stage table (exact). years_held: hold project -> encoded
    retirement year (None: beyond the horizon), from hold_years()."""
    e = expected.copy()
    e["kn"] = cs.keys(e.plant_id_eia, e.generator_id)
    col = "S0" if scenario == "s0" else "holds_persist"
    rows = []
    for _, h in holds_all[holds_all.in_S0 | holds_all.in_holds_persist].iterrows():
        for p in stages:
            y = years_held.get(h.kn, -1)
            held = h.kn in years_held and (y is None or y >= p)
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
    hist, nat = cs.stage_history(cu, stage, held, rec.get("rule"))
    before, after = model_mw(rec["units"], rec["edited"], stage, capacity_col, rec.get("rule"))
    zv = cs.apply_rule(hist, before, after, nat)
    zv["stage"] = stage
    if expected is not None:
        x = expected[expected.stage == stage].set_index("zone")
        zv = zv.join(x[["expected_cap", "rule", "hist_MW", "model_MW_after_overrides"]].add_suffix("_spec"), how="outer")
        zv["stage"], zv["national_N"] = stage, nat
        zv["cap_diff"] = zv.expected_cap - zv.expected_cap_spec
        zv["M_diff"] = zv.model_MW_after_overrides.fillna(0) - zv.model_MW_after_overrides_spec.fillna(0)
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
    cu = cs.cap_unit_set(cs.load_cap_units(REPO / cfg.get("cap_units_table", "s0_workflow/data/coal_cap_units_860m.csv")),
                         cfg.get("cap_statuses", ["OP", "SB"]))
    holds_all = hold_table(s0)
    held = cs.held_keys(holds_all)
    option = retirement_option(s0)
    exp_caps = pd.read_csv(expected_path(cfg, "expected_caps", EXPECTED_CAPS, option))
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

    # checks: removals, overrides (from the first stage's unit table; the same basis every year), holds by stage
    rem = recs[years[0]]["removals"]
    rem.to_csv(folder / "coal_removals.csv", index=False)
    ov = check_overrides(recs[years[0]]["overrides"],
                         pd.read_csv(REPO / cfg.get("expected_overrides", "s0_workflow/specs/coal/coal_spec_overrides.csv"),
                                     dtype={"generator_id": str}), scen, absent=set(rem.kn[~rem.derived_override]),
                         units=recs[years[0]]["units"], capacity_col=cap_col)
    ov.to_csv(folder / "coal_overrides_applied.csv", index=False)
    nim = cs.not_in_model()
    nim.to_csv(folder / "coal_not_in_model.csv", index=False)
    hb = holds_by_stage(hold_years(recs[years[0]]["edited"]), holds_all, years, scen,
                        pd.read_csv(expected_path(cfg, "expected_holds", EXPECTED_HOLDS, option), dtype={"generator_id": str}))
    hb.to_csv(folder / "coal_holds_by_stage.csv", index=False)
    hr = recs[years[0]].get("heat_rates", {})
    bad_hr = pd.DataFrame()
    if hr:
        H = pd.DataFrame([{"kn": k, "heat_rate": v, "source": s, "flag": f} for k, (v, s, f) in hr.items()])
        g = pd.read_csv(REPO / cfg.get("expected_converted", "s0_workflow/specs/coal/coal_spec_converted_gas_units.csv"),
                        dtype={"generator_id": str})
        H["spec_heat_rate"] = H.kn.map(dict(zip(cs.keys(g.plant_id_eia, g.generator_id), g.convert_heat_rate)))
        H["ok"] = (H.heat_rate - H.spec_heat_rate).abs() <= 0.01            # rev. 2.1: latest EIA-923 (+-0.01)
        H.to_csv(folder / "coal_converted_heat_rates.csv", index=False)
        bad_hr = H[~H.ok]

    bad_caps = T[~T.ok.fillna(False)] if "ok" in T else T.iloc[0:0]
    bad_ov, bad_h = ov[~ov.ok.astype(bool)], hb[~hb.ok]
    m_diff = T[T.M_diff.abs() > M_TOL] if "M_diff" in T else T.iloc[0:0]
    for y in years:
        t = T[T.stage == y]
        log(f"coal caps {y}: N {t.national_N.iloc[0]:.4f}; {int((t.model_MW_after_overrides > 0).sum())} zones with coal "
            f"({t.model_MW_after_overrides.sum() / 1e3:.2f} GW after overrides, {t.model_MW_before.sum() / 1e3:.2f} GW "
            f"before); rules {t.rule.str.replace(NO_COAL_LABEL, '', regex=False).value_counts().to_dict()}; "
            f"vs spec: {int(t.ok.sum())}/{len(t)} zones ok (cap +-{tol}, rule, H +-1 MW)")
    log(removals_line(rem))
    g48 = nim[nim.county_zone.notna()]
    log(f"coal: {len(nim)} coal-group units ({nim.winter_capacity_mw.sum():.1f} MW) {cs.NOT_IN_MODEL}; outside "
        f"Alaska {len(g48)} units, {g48.winter_capacity_mw.sum():.1f} MW by county zone "
        f"{g48.groupby('county_zone').winter_capacity_mw.sum().round(1).to_dict()} (coal_not_in_model.csv)")
    if recs[years[0]].get("new_860m_coal"):
        log(f"coal: {len(recs[years[0]]['new_860m_coal'])} coal-group units PowerGenome added from the 860M "
            f"(not in its EIA-860 units; not in the caps' model MW; reported): "
            + "; ".join(recs[years[0]]["new_860m_coal"]))
    log(f"coal overrides: {int(ov.ok.sum())}/{len(ov)} as coal_spec_overrides.csv "
        f"({recs[years[0]]['overrides'].action.map(_hold_class).value_counts().to_dict()}); holds ({scen}): "
        f"{int(hb.ok.sum())}/{len(hb)} unit-stages as coal_spec_hold_by_stage.csv; "
        f"{int(is_hold.sum())} hold projects in the case")
    if len(m_diff):
        log(f"coal caps: model MW differs from the spec's reconstruction by > {M_TOL} MW in {len(m_diff)} zone-stages "
            f"(reported, not failed; coal_spec.md addendum, model basis): "
            + "; ".join(f"{r.zone} {r.stage} {r.M_diff:+.1f}" for r in m_diff.itertuples()))
    problems = removals_problems(rem)
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
    if len(bad_hr):
        problems.append(f"{len(bad_hr)} converted-unit heat rates differ from coal_spec_converted_gas_units.csv: "
                        + "; ".join(f"{r.kn} {r.heat_rate} vs {r.spec_heat_rate}" for r in bad_hr.itertuples()))
    if problems:
        for p in problems:
            log("COAL SPEC CHECK FAILED: " + p)
        log.write()
        if cfg.get("fail_on_mismatch", True):
            raise ValueError(f"coal spec check failed for {case} {years} (see {folder}/coal_*.csv):\n" + "\n".join(problems))


NO_COAL_LABEL = cs.NO_COAL
M_TOL = 0.1             # model MW by zone vs the expected tables: reported above 0.1 MW (§1.5 reports M to 0.1 MW)
EXPECTED_CAPS = "s0_workflow/specs/coal/by_option/coal_spec_expected_caps_by_stage.{option}.csv"
EXPECTED_HOLDS = "s0_workflow/specs/coal/by_option/coal_spec_hold_by_stage.{option}.csv"


# ---------------------------------------------------------------------------------------------- per-option tables
CAPS_COLUMNS = ["zone", "model_MW_before", "model_MW_after_overrides", "hist_MW", "n_units", "own_cap", "coverage",
                "rule", "expected_cap", "stage", "national_N"]


def option_tables(option: str, stages=cs.STAGES) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """The coal spec's per-stage validation tables under one pre-2030 retirement option, from the committed public
    data (cap units, 860M fleet extract, holds) and the public model-basis reconstruction:
    (expected caps by stage, holds by stage, stage summary). planned_only and unrestricted have the same tables
    (economic retirement is a solve outcome); block_all pushes coal-group units dated 2026-29 (and the S0 holds'
    encoded 2029) through the 2030 stage, in the model fleet and in the cap unit set."""
    if option not in cs.RETIREMENT_OPTIONS:
        raise ValueError(option)
    rule = cs.BLOCK_RULE if option == "block_all" else None
    basis = cs.load_model_basis()
    op, rt = load_fleet860m()
    cu = cs.cap_unit_set(cs.load_cap_units())
    holds_all = cs.load_holds()
    held = cs.held_keys(holds_all)
    edited, hyears = {}, {}
    for scen in ("s0", "holds_persist"):
        ov, final = derive_overrides(basis, op, rt, scenario_holds({"coal_holds": {"scenario": scen}}))
        edited[scen] = apply_overrides(basis, final, ov, rule=rule)
        hyears[scen] = hold_years(edited[scen])
    caps, summ = [], []
    for p in stages:
        hist, nat = cs.stage_history(cu, p, held, rule)
        before, after = model_mw(basis, edited["s0"], p, rule=rule)
        e = cs.apply_rule(hist, before, after, nat).reset_index()
        e["stage"] = p
        caps.append(e[CAPS_COLUMNS])
        w = e.model_MW_after_overrides
        has = w > 0
        held_mw = {s: sum(holds_all.set_index("kn").winter_mw.get(k, 0) for k, y in hyears[s].items()
                          if y is None or y >= p) for s in hyears}
        summ.append({"option": option, "stage": p, "national_N": round(nat, 4), "zones": len(e),
                     "zones_with_model_coal": int(has.sum()),
                     "model_GW_before": round(e.model_MW_before.sum() / 1e3, 2), "model_GW_after_overrides": round(w.sum() / 1e3, 2),
                     "model_MW_weighted_cap": round(float(np.average(e.expected_cap[has], weights=w[has])), 4),
                     "zones_own_history": int((e.rule == cs.RULE_OWN).sum()), "zones_national": int((e.rule == cs.RULE_NAT).sum()),
                     "zones_blend": int((e.rule == cs.RULE_BLEND).sum()), "zones_no_coal_left": int(e.rule.str.endswith(cs.NO_COAL).sum()),
                     "held_GW_S0": round(held_mw["s0"] / 1e3, 2), "held_GW_holds_persist": round(held_mw["holds_persist"] / 1e3, 2)})
    hs = []
    for _, h in holds_all[holds_all.in_S0 | holds_all.in_holds_persist].iterrows():
        for p in stages:
            st = {}
            for scen, col in (("s0", "S0"), ("holds_persist", "holds_persist")):
                y = hyears[scen].get(h.kn, -1)
                if h.kn in hyears[scen] and (y is None or y >= p):
                    st[col] = "held"
                else:
                    st[col] = "retired" if (h.in_S0 if scen == "s0" else h.in_holds_persist) else "retired (not held in S0)"
            hs.append({"plant_id_eia": h.plant_id_eia, "generator_id": h.generator_id, "plant_name": h.plant_name,
                       "zone": h.zone, "stage": p, "S0": st["S0"], "holds_persist": st["holds_persist"],
                       "cap_while_held": h.hold_cap, "winter_MW": h.winter_mw})
    return pd.concat(caps, ignore_index=True), pd.DataFrame(hs), pd.DataFrame(summ)
