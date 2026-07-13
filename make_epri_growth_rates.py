"""
Generates EPRI-Medium DC-load growth rates and AEO-2026 non-DC growth rates,
split by ReEDS BA, for the three model blocks (2023-2030, 2030-2040,
2040-2050). Writes growth_rates/epri_med/dc/zone_growth_<block>.csv and
.../nondc/zone_growth_<block>.csv in the load_zone,avg_growth,peak_growth
format expected by make_study_loads.py.

Key assumptions (confirm/adjust before trusting results):
  - AEO has no 2023/2024 data; the 2023-2030 block rate is approximated by
    the 2025-2030 AEO trend (no earlier anchor exists).
  - AEO's Commercial sector is replaced with the average of Residential and
    Industrial growth, since Commercial embeds AEO's own (likely stale)
    data center growth assumptions that we want to replace with EPRI's.
  - EPRI's Medium scenario only runs to 2030; DC load added beyond that is
    extrapolated by shrinking the 2023-2030 absolute TWh/GW added by
    EPRI_DECAY per later block (data center buildout tapers as markets
    saturate).
  - DC avg_growth is computed as TWh added (state Medium-2030 minus state
    Historical-2023), downscaled to each BA's share, then expressed as a
    rate relative to the BA's *total* baseline load (Switch_2023_baseline.csv)
    -- not the DC segment's own prior load, since many states have ~0
    historical DC load and a self-referential CAGR would be undefined there.
  - DC peak_growth is computed analogously from EPRI's per-state Peak Load
    (GW) additions, expressed relative to the BA's baseline average load
    (same denominator as avg_growth). Because data centers run at near-100%
    capacity factor (added_peak ≈ added_avg), the resulting peak_growth is
    close to avg_growth and naturally produces a near-flat hourly DC addition.
  - Non-DC peak_growth is assumed to equal non-DC avg_growth (no separate
    peak signal available from AEO sector totals).
  - Non-DC BA-level rates are a load_share_of_ba-weighted blend across the
    EMM zones a BA overlaps, per crosswalk_v7.
"""
import pandas as pd
from pathlib import Path

OUT_BASE = Path("growth_rates/epri_med")
BLOCKS = [(2023, 2030), (2030, 2040), (2040, 2050)]
EPRI_SCENARIO = "Medium"
EPRI_DECAY = 0.75  # shrink factor applied to DC growth rate per block beyond 2030

ZONE_TO_EMM = {
    "Florida Reliability Coordinating Council": "FRCC",
    "Midcontinent / Central": "MISC",
    "Midcontinent / East": "MISE",
    "Midcontinent / South": "MISS",
    "Midcontinent / West": "MISW",
    "Northeast Power Coordinating Council / New England": "ISNE",
    "Northeast Power Coordinating Council / New York City and Long Island": "NYCW",
    "Northeast Power Coordinating Council / Upstate New York": "NYUP",
    "PJM / Commonwealth Edison": "PJMC",
    "PJM / Dominion": "PJMD",
    "PJM / East": "PJME",
    "PJM / West": "PJMW",
    "SERC Reliability Corporation / Central": "SRCA",
    "SERC Reliability Corporation / East": "SRCE",
    "SERC Reliability Corporation / Southeastern": "SRSE",
    "Southwest Power Pool / Central": "SPPC",
    "Southwest Power Pool / North": "SPPN",
    "Southwest Power Pool / South": "SPPS",
    "Texas Reliability Entity": "TRE",
    "Western Electricity Coordinating Council / Basin": "BASN",
    "Western Electricity Coordinating Council / California North": "CANO",
    "Western Electricity Coordinating Council / California South": "CASO",
    "Western Electricity Coordinating Council / Northwest Power Pool Area": "NWPP",
    "Western Electricity Coordinating Council / Rockies": "RMRG",
    "Western Electricity Coordinating Council / Southwest": "SRSG",
}


def cagr(start, end, n_years):
    return (end / start) ** (1 / n_years) - 1


# ------------------------------------------------------------------ #
# Stage 1: AEO non-DC growth rate by EMM zone, with commercial-sector
# correction (commercial replaced by avg of residential + industrial
# growth, since AEO's commercial forecast embeds its own DC assumptions)
# ------------------------------------------------------------------ #
def aeo_nondc_growth():
    aeo = pd.read_csv("growth_rates/AEO_2026_Projection_by_Zone.csv")
    aeo = aeo[aeo["Scenario"] == "Counterfactual Baseline case"]
    aeo["emm"] = aeo["Zone"].map(ZONE_TO_EMM)
    missing = aeo.loc[aeo["emm"].isna(), "Zone"].unique()
    if len(missing):
        raise ValueError(f"Unmapped AEO zones: {missing}")

    pivot = aeo.pivot_table(
        index=["emm", "Year"], columns="Sector", values="DemandTWh", aggfunc="sum"
    ).reset_index()

    rows = []
    for emm, grp in pivot.groupby("emm"):
        grp = grp.set_index("Year").sort_index()
        for y0, y1 in BLOCKS:
            # AEO starts in 2025; use the nearest available year as a
            # stand-in for 2023 (no earlier AEO data exists)
            ya = max(y0, grp.index.min())
            yb = min(y1, grp.index.max())
            n = yb - ya
            res_g = cagr(grp.loc[ya, "Residential"], grp.loc[yb, "Residential"], n)
            ind_g = cagr(grp.loc[ya, "Industrial"], grp.loc[yb, "Industrial"], n)
            comm_g = (res_g + ind_g) / 2  # corrected commercial growth

            start_total = grp.loc[
                ya, ["Residential", "Commercial", "Industrial", "Transportation"]
            ].sum()
            end_total = (
                grp.loc[yb, "Residential"]
                + grp.loc[ya, "Commercial"] * (1 + comm_g) ** n
                + grp.loc[yb, "Industrial"]
                + grp.loc[yb, "Transportation"]
            )
            growth = cagr(start_total, end_total, n)
            rows.append({"emm": emm, "block": f"{y0}-{y1}", "avg_growth": growth})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ #
# Stage 2: EPRI Medium-scenario DC load added by state, per block
# (absolute TWh/GW added, not a self-referential growth rate -- many states
# have ~0 historical DC load, which would make a CAGR undefined)
# ------------------------------------------------------------------ #
def epri_dc_added():
    epri = pd.read_csv("growth_rates/EPRI_Powering_Intelligence_2026-04-14.csv")
    epri = epri[epri["State"] != "US"]
    hist = epri[epri["Scenario"] == "Historical"]
    med = epri[epri["Scenario"] == EPRI_SCENARIO]

    hist_2023 = hist[hist["Year"] == 2023].set_index("State")["Annual Energy (TWh)"]
    med_2030 = med[med["Year"] == 2030].set_index("State")["Annual Energy (TWh)"]
    hist_2023_peak = hist[hist["Year"] == 2023].set_index("State")["Peak Load (GW)"]
    med_2030_peak = med[med["Year"] == 2030].set_index("State")["Peak Load (GW)"]

    states = sorted(set(hist_2023.index) | set(med_2030.index))
    added_2023_2030 = {
        s: med_2030.get(s, 0) - hist_2023.get(s, 0) for s in states
    }
    added_2023_2030_peak = {
        s: med_2030_peak.get(s, 0) - hist_2023_peak.get(s, 0) for s in states
    }

    rows = []
    for s in states:
        added = added_2023_2030[s]
        added_pk = added_2023_2030_peak[s]
        rows.append({"state": s, "block": "2023-2030", "added_twh": added, "added_peak_gw": added_pk})
        added_next = added * EPRI_DECAY
        added_pk_next = added_pk * EPRI_DECAY
        rows.append({"state": s, "block": "2030-2040", "added_twh": added_next, "added_peak_gw": added_pk_next})
        rows.append({"state": s, "block": "2040-2050", "added_twh": added_next * EPRI_DECAY, "added_peak_gw": added_pk_next * EPRI_DECAY})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ #
# Stage 3: blend EMM rates / state DC additions down to ReEDS BAs and
# write output. DC growth is expressed relative to each BA's *total*
# baseline load (Switch_2023_baseline.csv), not the DC segment's own
# prior load, since many states have ~0 historical DC load.
#
# DC peak_growth uses EPRI's per-state Peak Load (GW) additions,
# expressed on the same denominator (baseline average MW) as avg_growth.
# Because data centers run at near-100% capacity factor, added_peak_gw ≈
# added_avg_gw, so peak_growth ≈ avg_growth — producing a near-flat hourly
# addition without needing a manually tuned national peak/energy ratio.
# ------------------------------------------------------------------ #
def write_outputs(nondc_emm, dc_added):
    cw = pd.read_csv("growth_rates/crosswalk_v7.csv")
    cw = cw[["ba", "abbr", "states", "load_share_of_ba", "ba_load_mwh"]]
    # weight for downscaling a *state's* added DC TWh to a ba: the ba's
    # share of the state's total tracked load (not load_share_of_ba, which
    # is the reverse -- the state's share of the ba's own load)
    cw["state_load_share"] = cw["ba_load_mwh"] / cw.groupby("states")["ba_load_mwh"].transform("sum")
    baseline = pd.read_csv("growth_rates/Switch_2023_baseline.csv").rename(
        columns={"zone": "ba"}
    )

    for y0, y1 in BLOCKS:
        block = f"{y0}-{y1}"
        n = y1 - y0

        nondc_block = nondc_emm[nondc_emm["block"] == block]
        nondc_ba = (
            cw.merge(nondc_block, left_on="abbr", right_on="emm")
            .assign(weighted=lambda d: d["load_share_of_ba"] * d["avg_growth"])
            .groupby("ba")["weighted"].sum()
            .reset_index()
            .rename(columns={"weighted": "avg_growth", "ba": "load_zone"})
        )
        nondc_ba["peak_growth"] = nondc_ba["avg_growth"]
        out_dir = OUT_BASE / "nondc"
        out_dir.mkdir(parents=True, exist_ok=True)
        nondc_ba.to_csv(out_dir / f"zone_growth_{block}.csv", index=False)

        dc_block = dc_added[dc_added["block"] == block]
        ba_added = (
            cw.merge(dc_block, left_on="states", right_on="state")
            .assign(
                weighted_twh=lambda d: d["state_load_share"] * d["added_twh"],
                weighted_peak=lambda d: d["state_load_share"] * d["added_peak_gw"],
            )
            .groupby("ba")[["weighted_twh", "weighted_peak"]].sum()
            .reset_index()
            .rename(columns={"weighted_twh": "added_twh", "weighted_peak": "added_peak_gw"})
        )
        dc_ba = ba_added.merge(baseline, on="ba")
        # avg_growth: express added annual energy as a CAGR relative to baseline
        dc_ba["avg_growth"] = (
            1 + dc_ba["added_twh"] / dc_ba["DemandTWh"]
        ) ** (1 / n) - 1
        # peak_growth: express added peak (GW) on the same denominator (baseline
        # average MW = DemandTWh * 1e6 / 8760), so that when build_growth_timeseries
        # computes (peak_targ - avg_targ) / (peak_base - avg_base), the result is
        # near zero — i.e., a flat hourly DC addition — because for data centers
        # added_peak_gw ≈ added_avg_gw (capacity factor ≈ 100%).
        dc_ba["peak_growth"] = (
            1 + dc_ba["added_peak_gw"] * 1000 * 8760 / 1e6 / dc_ba["DemandTWh"]
        ) ** (1 / n) - 1
        dc_ba = dc_ba.rename(columns={"ba": "load_zone"})[
            ["load_zone", "avg_growth", "peak_growth"]
        ]
        out_dir = OUT_BASE / "dc"
        out_dir.mkdir(parents=True, exist_ok=True)
        dc_ba.to_csv(out_dir / f"zone_growth_{block}.csv", index=False)

        print(f"  Wrote {block}: {len(nondc_ba)} nondc BAs, {len(dc_ba)} dc BAs")


if __name__ == "__main__":
    print("Stage 1: AEO non-DC growth by EMM zone...")
    nondc_emm = aeo_nondc_growth()

    print("Stage 2: EPRI DC load added by state...")
    dc_added = epri_dc_added()

    print("Stage 3: blending to ReEDS BAs and writing output...")
    write_outputs(nondc_emm, dc_added)

    print("Done.")
