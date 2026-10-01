"""Zone load statistics by year (peak, median, p10, annual MWh) for load-based headroom proxies.

Sources (raw files in data/raw/load/, gitignored; `python -m icsc.cli load-stats` rebuilds
data/reference/zone_load_stats.csv):

  nrel     2016-2023  NREL hourly county load (OEDI 8562, doi 10.25984/3366592), MW, timestamps
                      in UTC; counties summed to zones with county2zone.csv (DC -> p123, MD).
  zenodo   2010-2013  ReEDS state hourly load (Zenodo 18462671, doi 10.5281/zenodo.18462671),
                      MWh/h hour-ending, CST (UTC-6), Dec 31 dropped in leap years; split to zones by
                      each zone's share of its state's load by month x hour-of-day, pooled over
                      2016-2018 from the NREL data.
  interpolated  2014-2015  linear between 2013 and 2016, times EIA state MWh / interpolated EIA MWh.
  eia_scaled_2023  2024   2023 times EIA state MWh growth 2023->2024.
  held_2023        2025   2023 values (EIA_loadbystate.csv has no 2025).
  held_latest      2026-2030  the 2025 values.

Everything is put on CST (UTC-6) before computing calendar years and month-hour shares. NREL's
timestamp convention (hour-beginning vs hour-ending) is not documented, so the NREL series is
shifted by the whole-hour lag that best matches Zenodo's state series (`align_lag_hours`).
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

YEARS_NREL = range(2016, 2024)
YEARS_ZENODO = range(2010, 2014)
SHARE_YEARS = (2016, 2017, 2018)
COLS = ["ba", "year", "peak_mw", "median_mw", "p10_mw", "annual_mwh", "source"]


def _to_cst(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return idx.tz_convert("Etc/GMT+6").tz_localize(None)   # Etc/GMT+6 is UTC-6, no DST


def nrel_hourly(path: Path, c2z: pd.DataFrame, cache: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(zone, state) hourly MW from the NREL county file, index in CST (unshifted), UTC-labelled source."""
    import h5py  # only needed to rebuild zone_load_stats.csv from the raw h5 files
    if cache and cache.exists():
        return pickle.load(open(cache, "rb"))
    with h5py.File(path, "r") as f:
        fips = pd.Series(f["data/block0_items"][:]).str.decode("utf-8").str.lstrip("p").str.zfill(5)
        idx = pd.to_datetime(f["data/axis1"][:], utc=True)
        m = c2z.set_index("FIPS").reindex(fips)
        if m["ba"].isna().any():
            raise ValueError(f"NREL counties missing from county2zone: {fips[m['ba'].isna().values].tolist()[:10]}")
        zones, zi = np.unique(m["ba"].values, return_inverse=True)
        states, si = np.unique(m["state"].values, return_inverse=True)
        Z = np.zeros((len(fips), len(zones))); Z[np.arange(len(fips)), zi] = 1
        S = np.zeros((len(fips), len(states))); S[np.arange(len(fips)), si] = 1
        v = f["data/block0_values"]
        zrows, srows = [], []
        for a in range(0, v.shape[0], 8760):
            block = v[a:a + 8760, :]
            zrows.append(block @ Z)
            srows.append(block @ S)
    cst = _to_cst(idx)
    out = (pd.DataFrame(np.vstack(zrows), index=cst, columns=zones),
           pd.DataFrame(np.vstack(srows), index=cst, columns=states))
    if cache:
        pickle.dump(out, open(cache, "wb"))
    return out


def zenodo_hourly(path: Path) -> pd.DataFrame:
    """State hourly MWh/h, index in CST (naive)."""
    import h5py  # only needed to rebuild zone_load_stats.csv from the raw h5 files
    with h5py.File(path, "r") as f:
        cols = pd.Series(f["columns"][:]).str.decode("utf-8")
        idx = pd.to_datetime(pd.Series(f["index_0"][:]).str.decode("utf-8"), utc=True)
        return pd.DataFrame(f["data"][:].astype(float), index=_to_cst(pd.DatetimeIndex(idx)), columns=cols)


def align_lag_hours(nrel_state: pd.DataFrame, zen_state: pd.DataFrame, lags=range(-3, 4)) -> tuple[int, dict]:
    """Whole-hour shift of NREL that best matches Zenodo (national sum, 2016-2023), and corr by lag."""
    n = nrel_state.sum(axis=1)
    z = zen_state.sum(axis=1)
    z = z[(z.index.year >= 2016)]
    corr = {}
    for k in lags:
        s = n.copy(); s.index = s.index + pd.Timedelta(hours=k)
        j = pd.concat([s, z], axis=1, join="inner").dropna()
        corr[k] = float(j.corr().iat[0, 1])
    return max(corr, key=corr.get), corr


def stats(hourly: pd.DataFrame, years, calendar_energy: bool) -> pd.DataFrame:
    """Per column and year: coincident peak, median, p10 (MW) and annual MWh.

    calendar_energy: annual MWh = mean MW x hours in the calendar year (fills hours lost to the
    UTC->CST shift); otherwise the plain sum (Zenodo is already calibrated to EIA annual MWh)."""
    rows = []
    for y in years:
        h = hourly[hourly.index.year == y]
        hours = (8784 if pd.Timestamp(y, 12, 31).dayofyear == 366 else 8760) if calendar_energy else len(h)
        energy = h.mean() * hours if calendar_energy else h.sum()
        rows.append(pd.DataFrame({"ba": h.columns, "year": y, "peak_mw": h.max().values,
                                  "median_mw": h.median().values, "p10_mw": h.quantile(0.10).values,
                                  "annual_mwh": energy.values}))
    return pd.concat(rows, ignore_index=True)


def month_hour_shares(zone_h: pd.DataFrame, state_h: pd.DataFrame, zone_state: pd.Series, years) -> pd.DataFrame:
    """Zone share of its state's load by (month, hour-of-day), pooled over `years`."""
    keep = zone_h.index.year.isin(years)
    zh, sh = zone_h[keep], state_h[keep]
    key = [zh.index.month, zh.index.hour]
    zsum = zh.groupby(key).sum()
    ssum = sh.groupby(key).sum()
    return zsum / ssum[zone_state.reindex(zsum.columns).values].values


def split_to_zones(state_h: pd.DataFrame, shares: pd.DataFrame, zone_state: pd.Series) -> pd.DataFrame:
    """Zone hourly = state hourly x the zone's (month, hour) share."""
    mh = pd.MultiIndex.from_arrays([state_h.index.month, state_h.index.hour])
    sh = shares.reindex(mh).values
    st = state_h[zone_state.reindex(shares.columns).values].values
    return pd.DataFrame(sh * st, index=state_h.index, columns=shares.columns)


def _pct(a: pd.Series, b: pd.Series) -> pd.Series:
    return 100 * (a - b) / b


def build(cfg: dict, c2z: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Return (zone_load_stats, checks)."""
    lp = cfg["load"]
    root = Path(cfg["_root"])
    zone_state = c2z.drop_duplicates("ba").set_index("ba")["state"]
    nz, ns = nrel_hourly(root / lp["nrel_county_h5"], c2z, root / lp["nrel_cache"])
    zen = zenodo_hourly(root / lp["zenodo_h5"])
    missing = sorted(set(zone_state.unique()) - set(zen.columns))
    if missing:
        raise ValueError(f"Zenodo has no column for states {missing}")
    lag, corr = align_lag_hours(ns, zen)
    nz = nz.copy(); ns = ns.copy()
    nz.index = nz.index + pd.Timedelta(hours=lag); ns.index = ns.index + pd.Timedelta(hours=lag)
    checks = {"nrel_shift_hours_to_match_zenodo": lag, "corr_by_shift": {k: round(v, 5) for k, v in corr.items()}}

    nrel = stats(nz, YEARS_NREL, calendar_energy=True).assign(source="nrel")
    shares = month_hour_shares(nz, ns, zone_state, SHARE_YEARS)
    zen_z = split_to_zones(zen[zen.index.year.isin(YEARS_ZENODO)], shares, zone_state)
    zst = stats(zen_z, YEARS_ZENODO, calendar_energy=False).assign(source="zenodo")

    # out-of-sample check of the month-hour split: 2016-18 shares applied to NREL state load 2019-23
    pred = split_to_zones(ns[ns.index.year.isin(range(2019, 2024))], shares, zone_state)
    pk = stats(pred, range(2019, 2024), True).set_index(["ba", "year"])["peak_mw"]
    act = nrel.set_index(["ba", "year"])["peak_mw"]
    err = _pct(pk, act.reindex(pk.index))
    checks["split_oos_peak_pct"] = err

    # Zenodo vs NREL summed to states, 2016-2023
    ss = stats(ns, YEARS_NREL, True).set_index(["ba", "year"])
    zs = stats(zen, YEARS_NREL, False).set_index(["ba", "year"])
    checks["zenodo_vs_nrel_state"] = pd.DataFrame({k: _pct(zs[k], ss[k]) for k in ["annual_mwh", "peak_mw", "p10_mw", "median_mw"]})

    eia = pd.read_csv(root / lp["eia_state_mwh"]).pivot(index="year", columns="st", values="MWh")
    base = pd.concat([zst, nrel], ignore_index=True).set_index(["ba", "year"])
    stat_cols = ["peak_mw", "median_mw", "p10_mw", "annual_mwh"]
    rows = []
    for ba, st in zone_state.items():
        a, b = base.loc[(ba, 2013), stat_cols], base.loc[(ba, 2016), stat_cols]
        for y in (2014, 2015):
            f = (y - 2013) / 3
            interp_eia = eia.at[2013, st] + f * (eia.at[2016, st] - eia.at[2013, st])
            v = (a + f * (b - a)) * eia.at[y, st] / interp_eia
            rows.append({"ba": ba, "year": y, **v.to_dict(), "source": "interpolated"})
        last = base.loc[(ba, 2023), stat_cols]
        if 2024 in eia.index:
            rows.append({"ba": ba, "year": 2024, **(last * eia.at[2024, st] / eia.at[2023, st]).to_dict(),
                         "source": "eia_scaled_2023"})
        else:
            rows.append({"ba": ba, "year": 2024, **last.to_dict(), "source": "held_2023"})
        y2025 = last if 2025 not in eia.index else last * eia.at[2025, st] / eia.at[2023, st]
        rows.append({"ba": ba, "year": 2025, **y2025.to_dict(),
                     "source": "held_2023" if 2025 not in eia.index else "eia_scaled_2023"})
        for y in range(2026, 2031):
            rows.append({"ba": ba, "year": y, **y2025.to_dict(), "source": "held_latest"})
    out = pd.concat([zst, nrel, pd.DataFrame(rows)], ignore_index=True)[COLS].sort_values(["ba", "year"])
    for c in stat_cols:
        out[c] = out[c].round(1)
    return out.reset_index(drop=True), checks


# hierarchy.csv hurdlereg -> EIA-930 balancing authority. LINED_UP: zone sets that match the BA's
# footprint closely enough to compare (ISOs and BAs whose zones are not shared with other BAs);
# the rest are reported for information only (a hurdlereg is the zones a utility sits in).
EIA930_BA = {"CAISO": "CISO", "ERCOT": "ERCO", "ISONE": "ISNE", "NYISO": "NYIS", "PJM": "PJM", "MISO": "MISO",
             "SPP": "SWPP", "Bonneville_Power_Administration": "BPAT", "PacifiCorp_East": "PACE",
             "PacifiCorp_West": "PACW", "Duke_Energy_Carolinas_LLC": "DUK", "Duke_Energy_Progress_East": "CPLE",
             "Florida_Power_and_Light": "FPL", "Tennessee_Valley_Authority": "TVA",
             "Southern_Co_Services_Inc": "SOCO", "Nevada_Power_Co": "NEVP", "Idaho_Power_Co": "IPCO"}
LINED_UP = {"PJM", "NYIS", "ISNE", "ERCO", "NEVP", "DUK"}


def eia930_check(cfg: dict, zone_h: pd.DataFrame, hierarchy: pd.DataFrame) -> pd.DataFrame:
    """NREL zone load summed over a BA's zones vs EIA-930 demand (adjusted where present), by year."""
    d930 = Path(cfg["_root"]) / cfg["load"]["eia930_dir"]
    files = sorted(d930.glob("EIA930_BALANCE_*.csv"))
    if not files:
        raise FileNotFoundError(f"No EIA-930 BALANCE files in {d930}")
    use = ["Balancing Authority", "UTC Time at End of Hour", "Demand (MW)", "Demand (MW) (Adjusted)"]
    e = pd.concat([pd.read_csv(f, usecols=use, thousands=",", low_memory=False) for f in files])
    e = e[e["Balancing Authority"].isin(EIA930_BA.values())]
    # UTC end of hour -> CST hour ending, the convention of the zone series
    e["t"] = pd.to_datetime(e["UTC Time at End of Hour"], format="%m/%d/%Y %I:%M:%S %p") - pd.Timedelta(hours=6)
    mw = pd.to_numeric(e["Demand (MW) (Adjusted)"], errors="coerce").fillna(pd.to_numeric(e["Demand (MW)"], errors="coerce"))
    w = e.assign(mw=mw)[mw > 0].pivot_table(index="t", columns="Balancing Authority", values="mw", aggfunc="first")
    zones = hierarchy.groupby("hurdlereg")["ba"].apply(list)
    rows = []
    for hr, code in EIA930_BA.items():
        n = zone_h[zones[hr]].sum(axis=1)
        for y in YEARS_NREL:
            a = w[code][w.index.year == y].dropna() if code in w else pd.Series(dtype=float)
            if len(a) < 0.95 * (n.index.year == y).sum():
                continue
            j = pd.concat([a, n[n.index.year == y]], axis=1, join="inner").set_axis(["eia", "nrel"], axis=1)
            rows.append({"hurdlereg": hr, "eia930_ba": code, "lined_up": code in LINED_UP, "year": y,
                         "energy_pct": 100 * (j["nrel"].sum() / j["eia"].sum() - 1),
                         "peak_pct": 100 * (j["nrel"].max() / j["eia"].max() - 1),
                         "p10_pct": 100 * (j["nrel"].quantile(0.1) / j["eia"].quantile(0.1) - 1)})
    return pd.DataFrame(rows)


def summarize(d: pd.DataFrame, cols) -> pd.DataFrame:
    """Median |%| and the worst signed % (largest magnitude) for each column."""
    return pd.DataFrame({"median_abs_pct": d[cols].abs().median(),
                         "worst_pct": d[cols].apply(lambda c: c.loc[c.abs().idxmax()])}).round(2)
