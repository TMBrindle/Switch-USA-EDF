"""
National cap on discretionary transmission additions, and optional minimum transfer floors (S0 production
transmission policy, CHANGES §60; Guides and documentation/transmission_bill_scenarios.md).

Discretionary additions are new transmission (BuildTx, MW of transfer capability, i.e. nameplate) on every line
except forced lines in their forced period (tx_cap_exempt.csv: those are required by trans_build_minimum and
capped at it). They are measured in MW-km (BuildTx x trans_length_km) and capped per period:

    sum over non-exempt lines of BuildTx[tx, p] x trans_length_km[tx]
        <= tx_cap_tw_mi_per_yr[p] x MW_KM_PER_TW_MI x period_length_years[p]

applied nationally to intra-region and interregional additions together (reported separately). A second cap
holds for the lines flagged no-bill (tx_cap_no_bill = 1, e.g. ERCOT ties and ERCOT-internal lines), at the
no-bill trajectory, so those lines keep the no-bill values when a bill raises the national cap:

    sum over non-exempt no-bill lines of BuildTx x km <= tx_cap_no_bill_tw_mi_per_yr[p] x MW_KM_PER_TW_MI x years

A blank or missing cap means no cap. 1 TW-mi = 1e6 MW x 1.609344 km = 1.609344e6 MW-km.

Transfer floor (optional sensitivity): for each floor group g (a region pair) and period p, the transfer
capability of its lines must reach the floor:

    sum over lines in g of TxCapacityNameplate[tx, p] >= tx_floor_mw[g, p]

TxCapacityNameplate includes existing capacity (in a chain, what earlier stages built).

The interregional moratorium is not in this module: the case build writes it as trans_path_expansion_limit rows
of 0 for interregional lines before their first allowed period.

Inputs:
    tx_cap_periods.csv    PERIOD, tx_cap_tw_mi_per_yr*, tx_cap_no_bill_tw_mi_per_yr*
    tx_cap_lines.csv      TRANSMISSION_LINE, tx_cap_class (intra | inter), tx_cap_no_bill (0 | 1)
    tx_cap_exempt.csv*    TRANSMISSION_LINE, PERIOD (forced lines in their forced period)
    tx_floor.csv*         TX_FLOOR, PERIOD, tx_floor_mw
    tx_floor_lines.csv*   TX_FLOOR, TRANSMISSION_LINE
Output: tx_build_cap.csv (by period: cap, intra- and interregional additions in MW-km and TW-mi/yr, no-bill
additions, exempt (forced) additions, duals) and tx_floor.csv when floors are set.
"""
import os

import pandas as pd
from pyomo.environ import Any, Constraint, Expression, NonNegativeReals, Param, Set, value

dependencies = (
    "switch_model.timescales",
    "switch_model.financials",
    "switch_model.transmission.transport.build",
)

MW_KM_PER_TW_MI = 1.0e6 * 1.609344


def define_components(m):
    m.tx_cap_tw_mi_per_yr = Param(m.PERIODS, within=NonNegativeReals, default=float("inf"))
    m.tx_cap_no_bill_tw_mi_per_yr = Param(m.PERIODS, within=NonNegativeReals, default=float("inf"))
    m.tx_cap_class = Param(m.TRANSMISSION_LINES, within=Any, default="intra",
                           validate=lambda m, v, tx: v in ("intra", "inter"))
    m.tx_cap_no_bill = Param(m.TRANSMISSION_LINES, within=NonNegativeReals, default=0)
    m.TX_CAP_EXEMPT = Set(dimen=2, within=m.TRANSMISSION_LINES * m.PERIODS)

    def additions(m, p, cls=None, no_bill=False, exempt=False):
        return sum(  # 0 when there are no terms (constraints skip those periods)
            m.BuildTx[tx, p] * m.trans_length_km[tx]
            for tx in m.TRANSMISSION_LINES
            if (tx, p) in m.TRANS_BLD_YRS
            and ((tx, p) in m.TX_CAP_EXEMPT) == exempt
            and (cls is None or m.tx_cap_class[tx] == cls)
            and (not no_bill or value(m.tx_cap_no_bill[tx]) > 0)
        )

    m.TxCapIntraMWkm = Expression(m.PERIODS, rule=lambda m, p: additions(m, p, "intra"))
    m.TxCapInterMWkm = Expression(m.PERIODS, rule=lambda m, p: additions(m, p, "inter"))
    m.TxCapNoBillMWkm = Expression(m.PERIODS, rule=lambda m, p: additions(m, p, no_bill=True))
    m.TxCapExemptMWkm = Expression(m.PERIODS, rule=lambda m, p: additions(m, p, exempt=True))

    def cap_mw_km(m, rate, p):
        r = value(rate[p])
        return r * MW_KM_PER_TW_MI * value(m.period_length_years[p]) if r != float("inf") else None

    def has_terms(m, p, no_bill=False):
        return any((tx, p) in m.TRANS_BLD_YRS and (tx, p) not in m.TX_CAP_EXEMPT
                   and (not no_bill or value(m.tx_cap_no_bill[tx]) > 0) for tx in m.TRANSMISSION_LINES)

    m.Tx_Build_Cap = Constraint(
        m.PERIODS,
        rule=lambda m, p: Constraint.Skip
        if cap_mw_km(m, m.tx_cap_tw_mi_per_yr, p) is None or not has_terms(m, p)
        else m.TxCapIntraMWkm[p] + m.TxCapInterMWkm[p] <= cap_mw_km(m, m.tx_cap_tw_mi_per_yr, p),
    )
    m.Tx_Build_Cap_No_Bill = Constraint(
        m.PERIODS,
        rule=lambda m, p: Constraint.Skip
        if cap_mw_km(m, m.tx_cap_no_bill_tw_mi_per_yr, p) is None or not has_terms(m, p, True)
        else m.TxCapNoBillMWkm[p] <= cap_mw_km(m, m.tx_cap_no_bill_tw_mi_per_yr, p),
    )

    # minimum transfer floors (sensitivity)
    m.TX_FLOOR_PERIODS = Set(dimen=2)
    m.tx_floor_mw = Param(m.TX_FLOOR_PERIODS, within=NonNegativeReals)
    m.TX_FLOOR_LINES = Set(dimen=2)
    def floor_rule(m, g, p):
        lines = [tx for (gg, tx) in m.TX_FLOOR_LINES if gg == g]
        if not lines:
            raise ValueError(f"tx_floor.csv: floor {g!r} has no lines in tx_floor_lines.csv")
        return sum(m.TxCapacityNameplate[tx, p] for tx in lines) >= m.tx_floor_mw[g, p]

    m.Tx_Transfer_Floor = Constraint(m.TX_FLOOR_PERIODS, rule=floor_rule)


def load_inputs(m, switch_data, inputs_dir):
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tx_cap_periods.csv"),
        optional=True,
        param=(m.tx_cap_tw_mi_per_yr, m.tx_cap_no_bill_tw_mi_per_yr),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tx_cap_lines.csv"),
        optional=True,
        param=(m.tx_cap_class, m.tx_cap_no_bill),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tx_cap_exempt.csv"),
        optional=True,
        set=m.TX_CAP_EXEMPT,
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tx_floor.csv"),
        optional=True,
        index=m.TX_FLOOR_PERIODS,
        param=(m.tx_floor_mw,),
    )
    switch_data.load_aug(
        filename=os.path.join(inputs_dir, "tx_floor_lines.csv"),
        optional=True,
        set=m.TX_FLOOR_LINES,
    )


def _dual(m, con, idx):
    d = getattr(m, "dual", None)
    if d is None or idx not in con:
        return float("nan")
    try:
        return float(d.get(con[idx], float("nan")))
    except (KeyError, TypeError, ValueError):
        return float("nan")


def post_solve(m, outdir):
    rows = []
    for p in m.PERIODS:
        yrs = value(m.period_length_years[p])
        intra, inter = value(m.TxCapIntraMWkm[p]), value(m.TxCapInterMWkm[p])
        to_rate = lambda x: x / (MW_KM_PER_TW_MI * yrs)  # noqa: E731
        cap = value(m.tx_cap_tw_mi_per_yr[p])
        nb = value(m.tx_cap_no_bill_tw_mi_per_yr[p])
        rows.append({
            "PERIOD": p, "period_years": yrs,
            "cap_tw_mi_per_yr": cap if cap != float("inf") else float("nan"),
            "cap_mw_km": cap * MW_KM_PER_TW_MI * yrs if cap != float("inf") else float("nan"),
            "intra_mw_km": intra, "inter_mw_km": inter, "total_mw_km": intra + inter,
            "intra_tw_mi_per_yr": to_rate(intra), "inter_tw_mi_per_yr": to_rate(inter),
            "total_tw_mi_per_yr": to_rate(intra + inter),
            "no_bill_cap_tw_mi_per_yr": nb if nb != float("inf") else float("nan"),
            "no_bill_mw_km": value(m.TxCapNoBillMWkm[p]), "no_bill_tw_mi_per_yr": to_rate(value(m.TxCapNoBillMWkm[p])),
            "exempt_forced_mw_km": value(m.TxCapExemptMWkm[p]),
            "cap_dual": _dual(m, m.Tx_Build_Cap, p), "no_bill_cap_dual": _dual(m, m.Tx_Build_Cap_No_Bill, p),
        })
    pd.DataFrame(rows).to_csv(os.path.join(outdir, "tx_build_cap.csv"), index=False)
    if len(m.TX_FLOOR_PERIODS):
        pd.DataFrame([{"TX_FLOOR": g, "PERIOD": p, "tx_floor_mw": value(m.tx_floor_mw[g, p]),
                       "transfer_mw": sum(value(m.TxCapacityNameplate[tx, p]) for (gg, tx) in m.TX_FLOOR_LINES
                                          if gg == g),
                       "dual": _dual(m, m.Tx_Transfer_Floor, (g, p))} for (g, p) in m.TX_FLOOR_PERIODS]
                     ).to_csv(os.path.join(outdir, "tx_floor.csv"), index=False)
