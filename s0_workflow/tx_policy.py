"""S0 transmission policy (CHANGES §60): interregional moratorium, national cap on discretionary transmission additions,
and the transmission sensitivity hooks. Settings: s0_production.tx_policy (pg/settings/s0_production.yml); scenarios:
Guides and documentation/transmission_bill_scenarios.md.

mode legacy (default): the case's trans_expansion_policy as before; nothing here runs except the capex multiplier and
the transfer floor when set (both off by default).
mode national_cap (S0_tx and the bill cases), after pg_to_switch has written the transmission files:
  * lines are interregional when their ends are in different transmission planning regions (hierarchy.csv transreg),
    intra-region otherwise; lines with an end in a no-bill region (ERCOT) are flagged no-bill;
  * interregional lines may be built (trans_new_build_allowed 1; the constrained policy had blocked the ones not in
    the forced list) from their moratorium period on (moratorium_first_period, or the no-bill value for no-bill
    lines); before it their trans_path_expansion_limit is 0;
  * the per-line limits of trans_expansion_policy (zero: 0 everywhere) are replaced by the national cap
    (study_modules.tx_build_cap): no other per-line limit, except a forced line in its forced period, capped at its
    minimum as before (forced_tx_expansion_limit: minimum); forced lines in their forced period don't count against
    the cap and aren't held back by the moratorium;
  * the cap in TW-mi/yr by period (each key holds until the next; earlier periods take the first value) for all
    lines, and the no-bill trajectory for no-bill lines.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd

from s0_workflow.day_selection import zone_transreg

REPO = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "mode": "legacy",
    "moratorium_first_period": 2040,
    "cap_tw_mi_per_yr": {2028: 0.0, 2030: 1.4},
    "no_bill": {"regions": ["ERCOT"], "moratorium_first_period": 2040, "cap_tw_mi_per_yr": {2028: 0.0, 2030: 1.4}},
    "capex_multiplier": 1.0,
    "transfer_floor": None,
}
MODES = ("legacy", "national_cap")
FLOOR_COLUMNS = ["region_a", "region_b", "PERIOD", "min_transfer_mw"]


def tx_settings(s0: dict) -> dict:
    t = copy.deepcopy(DEFAULTS)
    over = (s0 or {}).get("tx_policy") or {}
    for k, v in over.items():
        t[k] = ({**t[k], **v} if isinstance(v, dict) and isinstance(t.get(k), dict) and k == "no_bill" else v)
    if t["mode"] not in MODES:
        raise ValueError(f"s0_production.tx_policy.mode must be one of {MODES}, not {t['mode']!r}")
    if float(t["capex_multiplier"]) <= 0:
        raise ValueError("s0_production.tx_policy.capex_multiplier must be positive")
    return t


def active(s0: dict) -> bool:
    t = tx_settings(s0)
    return t["mode"] != "legacy" or float(t["capex_multiplier"]) != 1.0 or bool(t["transfer_floor"])


def needs_module(s0: dict) -> bool:
    t = tx_settings(s0)
    return t["mode"] == "national_cap" or bool(t["transfer_floor"])


def step_value(table: dict, year: int):
    """The value of a {year: value} table in a year: each key holds until the next; earlier years take the first."""
    keys = sorted(int(k) for k in table)
    tab = {int(k): v for k, v in table.items()}
    val = tab[keys[0]]
    for k in keys:
        if int(year) >= k:
            val = tab[k]
    return val


def line_classes(tl: pd.DataFrame, zone_map: dict | None, no_bill_regions) -> pd.DataFrame:
    """TRANSMISSION_LINE, transreg of each end, tx_cap_class (intra | inter), tx_cap_no_bill."""
    zones = sorted(set(tl.trans_lz1) | set(tl.trans_lz2))
    tr = zone_transreg(zones, zone_map)
    missing = [z for z in zones if z not in tr]
    if missing:
        raise ValueError(f"tx_policy: no transmission planning region (transreg) for zones {missing[:10]}")
    out = pd.DataFrame({"TRANSMISSION_LINE": tl.TRANSMISSION_LINE.astype(str), "tr1": tl.trans_lz1.map(tr),
                        "tr2": tl.trans_lz2.map(tr)})
    out["tx_cap_class"] = ["inter" if a != b else "intra" for a, b in zip(out.tr1, out.tr2)]
    nb = set(no_bill_regions or [])
    out["tx_cap_no_bill"] = [int(a in nb or b in nb) for a, b in zip(out.tr1, out.tr2)]
    return out


def write_case_inputs(folder: Path, s0: dict, scen_settings_dict: dict, log) -> None:
    t = tx_settings(s0)
    if not active(s0):
        return
    folder = Path(folder)
    first = next(iter(scen_settings_dict.values()))
    mult = float(t["capex_multiplier"])
    if mult != 1.0:
        tp = pd.read_csv(folder / "trans_params.csv", na_values=["."])
        tp["trans_capital_cost_per_mw_km"] = tp["trans_capital_cost_per_mw_km"] * mult
        tp.to_csv(folder / "trans_params.csv", index=False, na_rep=".")
        log(f"tx_policy: transmission capex x{mult:g} (trans_params.trans_capital_cost_per_mw_km)")
    tl = pd.read_csv(folder / "transmission_lines.csv", na_values=["."])
    years = sorted(int(y) for y in pd.read_csv(folder / "periods.csv").INVESTMENT_PERIOD)
    cls = line_classes(tl, first.get("_zone_map"), t["no_bill"]["regions"])
    if t["mode"] == "national_cap":
        _national_cap(folder, t, first, tl, cls, years, log)
    if t["transfer_floor"]:
        _transfer_floor(folder, t["transfer_floor"], cls, years, log)


def _national_cap(folder, t, first, tl, cls, years, log):
    fmin = {}
    if (folder / "trans_build_minimum.csv").exists():
        bm = pd.read_csv(folder / "trans_build_minimum.csv")
        fmin = {(str(a), int(b)): float(v) for a, b, v in
                zip(bm.TRANSMISSION_LINE, bm.PERIOD, bm.trans_build_minimum_mw)}
    cap_forced = first.get("forced_tx_expansion_limit") == "minimum"
    c = cls.set_index("TRANSMISSION_LINE")
    inter = c.tx_cap_class == "inter"
    blocked = tl.TRANSMISSION_LINE.isin(c.index[inter]) & (tl.trans_new_build_allowed != 1)
    tl.loc[blocked, "trans_new_build_allowed"] = 1
    tl.to_csv(folder / "transmission_lines.csv", index=False, na_rep=".")
    allowed = set(tl.loc[tl.trans_new_build_allowed == 1, "TRANSMISSION_LINE"].astype(str))
    rows, n_mor = [], 0
    for line in tl.TRANSMISSION_LINE.astype(str):
        if line not in allowed:
            continue
        first_ok = int(t["no_bill"]["moratorium_first_period"] if c.at[line, "tx_cap_no_bill"]
                       else t["moratorium_first_period"])
        for p in years:
            if (line, p) in fmin:
                if cap_forced:
                    rows.append((line, p, fmin[(line, p)]))
            elif c.at[line, "tx_cap_class"] == "inter" and p < first_ok:
                rows.append((line, p, 0.0))
                n_mor += 1
    pd.DataFrame(rows, columns=["TRANSMISSION_LINE", "PERIOD", "trans_path_expansion_limit_mw"]).to_csv(
        folder / "trans_path_expansion_limit.csv", index=False)
    pd.DataFrame({"PERIOD": years,
                  "tx_cap_tw_mi_per_yr": [float(step_value(t["cap_tw_mi_per_yr"], p)) for p in years],
                  "tx_cap_no_bill_tw_mi_per_yr": [float(step_value(t["no_bill"]["cap_tw_mi_per_yr"], p))
                                                  for p in years]}).to_csv(folder / "tx_cap_periods.csv", index=False)
    cls[["TRANSMISSION_LINE", "tx_cap_class", "tx_cap_no_bill"]].to_csv(folder / "tx_cap_lines.csv", index=False)
    pd.DataFrame(sorted(fmin), columns=["TRANSMISSION_LINE", "PERIOD"]).to_csv(folder / "tx_cap_exempt.csv", index=False)
    log(f"tx_policy national_cap: {int((~inter).sum())} intra-region and {int(inter.sum())} interregional lines "
        f"({int(blocked.sum())} unblocked for building), {int(cls.tx_cap_no_bill.sum())} no-bill "
        f"({', '.join(t['no_bill']['regions'])}); moratorium: interregional from {t['moratorium_first_period']} "
        f"(no-bill {t['no_bill']['moratorium_first_period']}), {n_mor} zero-limit rows; cap TW-mi/yr "
        + ", ".join(f"{p}: {step_value(t['cap_tw_mi_per_yr'], p)} (no-bill "
                    f"{step_value(t['no_bill']['cap_tw_mi_per_yr'], p)})" for p in years)
        + f"; {len(fmin)} forced line-periods exempt")


def _transfer_floor(folder, path, cls, years, log):
    f = pd.read_csv(REPO / path if not Path(path).is_absolute() else path)
    if list(f.columns) != FLOOR_COLUMNS:
        raise ValueError(f"tx_policy.transfer_floor {path}: columns must be {FLOOR_COLUMNS}, not {list(f.columns)}")
    f = f[f.PERIOD.isin(years)]
    if f.empty:
        raise ValueError(f"tx_policy.transfer_floor {path}: no floor rows for periods {years} (placeholder file?)")
    floors, members = [], []
    for r in f.itertuples():
        g = "-".join(sorted([r.region_a, r.region_b]))
        lines = cls[cls.apply(lambda x: {x.tr1, x.tr2} == {r.region_a, r.region_b}, axis=1)].TRANSMISSION_LINE
        if lines.empty:
            raise ValueError(f"tx_policy.transfer_floor: no line between {r.region_a} and {r.region_b}")
        floors.append({"TX_FLOOR": g, "PERIOD": int(r.PERIOD), "tx_floor_mw": float(r.min_transfer_mw)})
        members += [{"TX_FLOOR": g, "TRANSMISSION_LINE": x} for x in lines]
    pd.DataFrame(floors).to_csv(folder / "tx_floor.csv", index=False)
    pd.DataFrame(members).drop_duplicates().to_csv(folder / "tx_floor_lines.csv", index=False)
    log(f"tx_policy transfer floor ({path}): {len(floors)} region-pair floors")
