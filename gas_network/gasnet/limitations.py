"""Known limitation: production areas are labelled whole by their main basin (CHANGES §102).

Quantified with the proved-reserves report YE2024: Table 9 shale-play production by subdivision against Table 8 total
production. The non-shale part of an area is conventional/tight gas. Whether labelling it with the area's main basin
moves gas between basins depends on the area: in RRC Districts 1, 2 and 4 the conventional gas (Gulf Coast basin:
Vicksburg, Frio, Wilcox, Lobo) is not Eagle Ford, so it moves from Barnett/other TX into Eagle Ford; elsewhere the
non-shale gas is in the same geological basin as the label (Permian conventional in 7C/8/8A, Anadarko Granite Wash
in District 10, East Texas Cotton Valley in 5/6, North Louisiana in LA North), so nothing moves between basins.
"""
from __future__ import annotations

import pandas as pd

from . import eia

# areas whose non-shale gas belongs to a different basin than the label: area -> basin it would move to
CROSS_BASIN = {"TX_D1": "barnett_other_tx", "TX_D2": "barnett_other_tx", "TX_D4": "barnett_other_tx"}


def district_mixing(inp, supply: pd.DataFrame) -> pd.DataFrame:
    arr_path = inp.raw / "eia" / "ARR_2024_TABLES_ALL.xlsx"
    tot = inp.arr.assign(sub=inp.arr.subdivision.str.replace(" Onshore", "", regex=False))
    sh = eia.arr_shale_production(arr_path)
    m = tot.merge(sh, left_on=["state", "sub"], right_on=["state", "subdivision"], how="left",
                  suffixes=("", "_t9")).fillna({"shale_bcf": 0.0})
    areas = inp.areas[inp.areas.arr_subdivision.str.len() > 0]
    areas = areas[~areas.arr_subdivision.str.startswith("HSM:")]
    dry = supply[supply.kind == "production"].groupby("area").value.sum()
    rows = []
    for a in areas.itertuples():
        subs = [s.replace(" Onshore", "") for s in a.arr_subdivision.split(";")]
        g = m[(m.state == a.state) & m["sub"].isin(subs)]
        t8, t9 = float(g.bcf.sum()), float(g.shale_bcf.sum())
        non = 1 - t9 / t8 if t8 else 0.0
        moved_to = CROSS_BASIN.get(a.area, "")
        rows.append(dict(area=a.area, label_basin=a.basin, arr_t8_production_bcf=t8, arr_t9_shale_bcf=t9,
                         non_shale_pct=100 * non, dry_2024_mmcf=float(dry.get(a.area, 0.0)),
                         non_shale_dry_mmcf=non * float(dry.get(a.area, 0.0)),
                         moves_between_basins=bool(moved_to), would_move_to=moved_to,
                         moved_dry_mmcf=non * float(dry.get(a.area, 0.0)) if moved_to else 0.0))
    return pd.DataFrame(rows)


def metadata(cfg: dict, default, mix: pd.DataFrame, co_sj: dict, label: str) -> dict:
    moved = mix[mix.moves_between_basins]
    over30 = mix[mix.non_shale_pct > 30]
    return {
        "file": "zone_basin_shares.csv",
        "method": label,
        "methods": {
            "trace_eia": "2024: proportional sharing (full mixing) on EIA Natural Gas Annual 2024 state-to-state movements",
            label: "model years: 2024 network; production indexed to AEO2026 (Tables 59/60); interregional bundles and "
                   "Canada arcs = 2024 + (Table 64 level(y) - level(2025)), floored at 0; intra-region arcs, GOM and "
                   "intra-Texas arcs then rebalanced (least L1 change) so no hub ships more than it has and each keeps "
                   "its AEO-grown local disposition where the network allows",
            "sensitivity": "zone_basin_shares_sensitivity_ratio.csv: the same with ratio scaling (2024 x Table 64(y)/(2025))"},
        "known_limitations": [
            {"id": "district_to_basin_labelling",
             "text": "Each production area (Texas RRC district, NM East/West, LA North/South, a state) is labelled "
                     "whole with its main basin. Non-shale gas in an area keeps the area's label.",
             "quantified": {"areas_over_30pct_non_shale": {r.area: round(r.non_shale_pct, 1) for r in over30.itertuples()},
                            "moves_between_basins_dry_bcf_2024": {r.area: round(r.moved_dry_mmcf / 1000, 1)
                                                                  for r in moved.itertuples()},
                            "eagle_ford_label_non_eagle_ford_bcf_2024": round(moved.moved_dry_mmcf.sum() / 1000, 1),
                            "note": "Only RRC Districts 1, 2, 4 move gas between basins (conventional Gulf Coast gas "
                                    "labelled Eagle Ford instead of Barnett/other TX). Districts 3, 7B, 8A, 10 are >30% "
                                    "non-shale but their non-shale gas is in the labelled basin. Source: EIA proved "
                                    "reserves YE2024 Tables 8 and 9; outputs/district_basin_mixing.csv."}},
            {"id": "colorado_san_juan_model_based",
             "text": "Colorado San Juan share from AEO2026 HSM producing-project first-year volumes (model-based, EIA); "
                     "observed county production (ECMC) not reachable from cloud sessions.",
             "quantified": {"co_san_juan_share_pct": round(100 * co_sj["san_juan"] / (co_sj["san_juan"] + co_sj["other"]), 1)}},
            {"id": "full_mixing", "text": "Gas is fully mixed at each hub; contract paths and displacement ignored."},
            {"id": "annual", "text": "Annual flows; no seasonal routing."},
            {"id": "texas_routing", "text": "Texas sub-hub routing is an assumption (NGMM border mapping, least-flow "
                                            "intra-Texas transfers on NGMM 2023 capacities)."},
            {"id": "projected_arcs_scaled", "text": "Projected arcs are scaled and rebalanced, not solved by a "
                                                    "network model; Stage 2 replaces them."}],
        "basins": cfg["basins"]}


def unmet_local_use(cfg: dict, nets: dict, raw) -> pd.DataFrame:
    """States whose local use stays below its AEO-grown floor after balancing (default variant), with gas-fired
    capacity (EIA-860M operating NG nameplate; Switch's existing gas fleet is built from EIA-860 via PowerGenome) and
    the Switch zones in the state. Which state inside a region takes a shortfall can be a tie between equivalent
    optima; regional totals are the robust number."""
    h = pd.read_csv(cfg["paths"]["hierarchy"])
    h = h[h.country == "USA"]
    zones_by_state = h.groupby("st").ba.apply(lambda s: " ".join(sorted(s, key=lambda z: int(z[1:]))))
    mw = eia.gas_capacity_by_state(raw / "eia" / "august_generator2026.xlsx")
    rows = []
    for y, n in nets.items():
        for hub, v in n.get("balance", {}).get("slack_hubs", {}).items():
            st = "TX" if hub.startswith("TX_") else hub
            fl = n["balance"]["floor_hubs"].get(hub, float("nan"))
            rows.append(dict(year=y, state=st, hub=hub, shortfall_bcf=round(v / 1000, 1), floor_bcf=round(fl / 1000, 1),
                             shortfall_pct_of_floor=round(100 * v / fl, 1),
                             gas_mw_eia860m=round(float(mw.get(st, 0.0))),
                             has_gas_generation=bool(mw.get(st, 0.0) > 0),
                             switch_zones=zones_by_state.get(st, "")))
    return pd.DataFrame(rows, columns=["year", "state", "hub", "shortfall_bcf", "floor_bcf", "shortfall_pct_of_floor",
                                       "gas_mw_eia860m",
                                       "has_gas_generation", "switch_zones"])


def share_fallback(z: pd.DataFrame, w: pd.DataFrame, unmet: pd.DataFrame, deg_zone: pd.DataFrame, base_year: int,
                   unmet_pct: float, deg_pp: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """CHANGES §104 (Tom): a projected zone-year falls back to the zone's base-year shares when
    (a) a hub the zone draws on (zone_hub_weights weight > 0) has unmet local use above unmet_pct % of its floor, or
    (b) the zone's shares differ by more than deg_pp (total-variation distance) between two equally good balancing
    optima (deg_zone: year, zone, tvd_pp).
    Returns the shares with method 'fallback_2024', the base-year vintage and a 'fallback' reason column ('' when
    kept), and the list of switches (year, zone, reason)."""
    reasons: dict = {}
    for r in unmet[unmet.shortfall_pct_of_floor > unmet_pct].itertuples():
        for zone in w[(w.hub == r.hub) & (w.weight > 0)].zone:
            reasons.setdefault((zone, int(r.year)), []).append(
                f"unmet local use {r.hub} {r.shortfall_pct_of_floor:.1f}% > {unmet_pct:g}%")
    for r in deg_zone[deg_zone.tvd_pp > deg_pp].itertuples():
        reasons.setdefault((r.zone, int(r.year)), []).append(f"balance degeneracy {r.tvd_pp:.1f} pp > {deg_pp:g} pp")
    out = z.copy()
    out["fallback"] = ""
    base = z[z.year == base_year]
    rows = []
    for (zone, y), rs in sorted(reasons.items(), key=lambda kv: (kv[0][1], int(kv[0][0][1:]))):
        m = (out.zone == zone) & (out.year == y)
        b = base[base.zone == zone]
        out.loc[m, "share"] = out.loc[m, "basin"].map(b.set_index("basin").share).fillna(0.0).values
        out.loc[m, "method"] = "fallback_2024"
        out.loc[m, "source_vintage"] = b.source_vintage.iloc[0]
        out.loc[m, "fallback"] = "; ".join(rs)
        rows.append(dict(year=y, zone=zone, reason="; ".join(rs)))
    return out, pd.DataFrame(rows, columns=["year", "zone", "reason"])
