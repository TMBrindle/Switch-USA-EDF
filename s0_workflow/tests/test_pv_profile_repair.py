"""Repair of zeroed reV solar profiles (CHANGES §111; s0_workflow/pv_profile_repair.py, production.apply_pv_profile_repair,
scripts/repair_pv_profiles.py). Hand-built profile files with short "weather years": fixtures, not results."""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

pytest.importorskip("pyarrow")
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from s0_workflow import production as s0prod, pv_profile_repair as pr  # noqa: E402

H = 24                                    # hours per "weather year" in the fixture
YEARS = list(range(2007, 2014))
NAME = "solar_rev_profiles_20240801.parquet"
ZEROED = ["105", "106", "107"]            # all zero 2007-2011, as the 5,390 sites
DEAD = "199"                              # all zero in every year


def _profiles(tmp_path, dead_capacity=10.0, scale_big=False):
    rng = np.random.default_rng(1)
    src = tmp_path / "profiles"
    src.mkdir()
    day = np.clip(np.sin(np.linspace(0, np.pi, H)), 0, None)
    cols, sites = {}, []
    for k in range(100, 120):
        site = str(k)
        lat, lon = 40 + (k % 5) * 0.1, -100 + (k // 5) * 0.1
        f = 0.6 + 0.02 * (k - 100)
        prof = np.concatenate([day * f * (0.8 + 0.03 * i) * rng.uniform(0.9, 1.0, H) for i in range(7)])
        if site in ZEROED:
            prof[: 5 * H] = 0.0
            if scale_big and site == "105":
                prof[5 * H:] = np.minimum(1.0, prof[5 * H:] * 1.6)
        cols[site] = prof.astype("float32")
        sites.append({"site": site, "latitude": lat, "longitude": lon, "capacity_mw": 50.0})
    cols[DEAD] = np.zeros(7 * H, dtype="float32")
    sites.append({"site": DEAD, "latitude": 40.2, "longitude": -99.9, "capacity_mw": dead_capacity})
    df = pd.DataFrame({"hour": np.arange(7 * H, dtype="int64"), **cols})
    df.to_parquet(src / NAME, index=False, row_group_size=50)
    (src / "onshorewind_rev_profiles_20240801.parquet").write_bytes(b"wind file stand-in")
    return src, pd.DataFrame(sites), df


def _repair(src, out, sites, batch_rows=10):
    return pr.repair_folder(src, out, NAME, sites, first_year=2007, hours=H, batch_rows=batch_rows, log=lambda m: None)


def test_neighbour_fill_same_weather_year_scaled(tmp_path):
    src, sites, before = _profiles(tmp_path, dead_capacity=0.0)
    out = tmp_path / "fixed"
    man = _repair(src, out, sites)
    after = pd.read_parquet(out / NAME)
    assert list(after.columns) == list(before.columns) and (after.dtypes == before.dtypes).all()
    assert after["hour"].equals(before["hour"])                                  # non-site column untouched
    log = pd.read_csv(out / pr.LOG, dtype={"site": str, "neighbour": str})
    filled = log[log.status == "filled"]
    assert sorted(set(filled.site)) == ZEROED and len(filled) == 3 * 5            # 3 sites x 2007-2011
    s = sites.set_index("site")
    clean = [c for c in before.columns[1:] if c not in ZEROED + [DEAD]]
    for r in filled.itertuples():
        # nearest unaffected site (great circle between the fixture coordinates)
        d = {c: np.hypot(s.at[c, "latitude"] - s.at[r.site, "latitude"],
                         (s.at[c, "longitude"] - s.at[r.site, "longitude"]) * np.cos(np.radians(40.2))) for c in clean}
        assert r.neighbour == min(d, key=d.get)
        ref = slice(5 * H, 7 * H)
        scale = before[r.site].iloc[ref].astype(float).mean() / before[r.neighbour].iloc[ref].astype(float).mean()
        assert r.scale == pytest.approx(scale, rel=1e-6)
        rows = slice((r.year - 2007) * H, (r.year - 2006) * H)                     # the SAME weather year
        want = np.clip(before[r.neighbour].iloc[rows].to_numpy(float) * scale, 0, 1)
        np.testing.assert_allclose(after[r.site].iloc[rows].to_numpy(float), want, rtol=1e-6, atol=1e-7)
    for c in ZEROED:                                                             # reference years untouched
        np.testing.assert_array_equal(after[c].iloc[5 * H:].to_numpy(), before[c].iloc[5 * H:].to_numpy())
    for c in clean + [DEAD]:
        np.testing.assert_array_equal(after[c].to_numpy(), before[c].to_numpy())
    assert man["site_years_zero_before"] == 3 * 5 and man["sites_zero_before"] == 3
    assert man["site_years_filled"] == 15 and man["zero_site_years_with_capacity_after"] == 0
    assert man["site_years_unfillable"] == 7                                    # the dead site, capacity 0: not checked
    assert man["other_files_linked"] + man["other_files_copied"] == ["onshorewind_rev_profiles_20240801.parquet"]
    assert (src / NAME).stat().st_size == man["source"]["size"]                 # the source is not changed
    assert pr.check_folder(out)["method"] == "neighbour_fill"
    pr.check_folder(out, sites, full=True)


def test_batches_and_row_groups_do_not_matter(tmp_path):
    src, sites, _ = _profiles(tmp_path, dead_capacity=0.0)
    a = pd.read_parquet(_repair(src, tmp_path / "a", sites, batch_rows=7) and tmp_path / "a" / NAME)
    b = pd.read_parquet(_repair(src, tmp_path / "b", sites, batch_rows=1000) and tmp_path / "b" / NAME)
    pd.testing.assert_frame_equal(a, b)


def test_site_with_capacity_still_zero_fails_the_check(tmp_path):
    src, sites, _ = _profiles(tmp_path, dead_capacity=10.0)                     # the dead site has capacity
    out = tmp_path / "fixed"
    man = _repair(src, out, sites)
    assert man["zero_site_years_with_capacity_after"] == 7
    log = pd.read_csv(out / pr.LOG, dtype={"site": str})
    assert (log[log.site == DEAD].status == "unfilled: all weather years zero: no reference mean").all()
    with pytest.raises(ValueError, match="still all zero"):
        pr.check_folder(out)


def test_clipping_is_counted(tmp_path):
    src, sites, _ = _profiles(tmp_path, dead_capacity=0.0, scale_big=True)
    man = _repair(src, tmp_path / "fixed", sites)
    assert man["clipped_hours"] > 0
    assert pd.read_parquet(tmp_path / "fixed" / NAME)["105"].max() <= 1.0


def _s0(mode, d=None, verify="manifest"):
    return {"s0_production": {"enabled": True, "pv_profile_repair": {"mode": mode, "profiles_dir": d, "verify": verify}}}


def test_build_flag(tmp_path):
    s0 = yaml.safe_load(open(REPO / "pg/settings/s0_production.yml"))["s0_production"]
    assert s0["pv_profile_repair"]["mode"] == "off"                              # v3.1 until approved
    src, sites, _ = _profiles(tmp_path, dead_capacity=0.0)
    out = tmp_path / "fixed"
    _repair(src, out, sites)
    s0prod._PV_REPAIR_CHECKED.clear()
    s = {"RESOURCE_GROUP_PROFILES": str(src)}                                    # off: unchanged
    s0prod.apply_pv_profile_repair(s, _s0("off")["s0_production"])
    assert s["RESOURCE_GROUP_PROFILES"] == str(src)
    with pytest.raises(ValueError, match="is off but RESOURCE_GROUP_PROFILES"):  # off with a repaired folder: refused
        s0prod.apply_pv_profile_repair({"RESOURCE_GROUP_PROFILES": str(out)}, _s0("off")["s0_production"])
    s = {"RESOURCE_GROUP_PROFILES": str(src)}
    s0prod.apply_pv_profile_repair(s, _s0("neighbour_fill", str(out))["s0_production"])
    assert s["RESOURCE_GROUP_PROFILES"] == str(out.resolve())
    case = tmp_path / "case"
    case.mkdir()
    logs = []
    s0prod.write_pv_repair_log(case, _s0("neighbour_fill", str(out))["s0_production"], logs.append)
    assert (case / pr.LOG).read_bytes() == (out / pr.LOG).read_bytes() and "15 site-years filled" in logs[0]
    with pytest.raises(ValueError, match="not found"):
        s0prod.apply_pv_profile_repair({}, _s0("neighbour_fill", str(src))["s0_production"])
    os.utime(out / NAME, ns=(1, 1))                                              # changed since the repair
    s0prod._PV_REPAIR_CHECKED.clear()
    with pytest.raises(ValueError, match="changed since the repair"):
        s0prod.apply_pv_profile_repair({}, _s0("neighbour_fill", str(out))["s0_production"])
    with pytest.raises(ValueError, match="pv_profile_repair.mode"):
        s0prod.pv_repair_settings({"pv_profile_repair": {"mode": "fill"}})


def test_cli_sites_from_metadata(tmp_path):
    from s0_workflow.scripts import repair_pv_profiles as cli
    meta = pd.DataFrame({"cpa": [1, 2, 3], "lat": [40.0, 41.0, 45.0], "lon": [-100.0, -100.0, -90.0],
                         "mw": [10.0, 30.0, 5.0]})
    sm = pd.DataFrame({"cpa": [1, 2, 3], "site_id": [7, 7, 8]})
    s = cli.sites_from(meta, sm, "cpa", "site_id", "lat", "lon", "mw").set_index("site")
    assert s.at["7", "capacity_mw"] == 40.0 and s.at["7", "latitude"] == pytest.approx(40.75)
    assert s.at["8", "longitude"] == -90.0


def test_rev_cf_reassess():
    from s0_workflow.scripts import rev_cf_reassess as ra
    m = pd.DataFrame({"zone": ["p1", "p11", "p9", "p1"], "technology": ["solar_pv", "solar_pv", "solar_pv", "wind_onshore"],
                      "model_cf": [0.1242, 0.2416, 0.2, 0.3], "rev_cf": [0.2316, 0.3123, 0.25, 0.35]})
    r = ra.reassess(m).set_index(["zone", "technology"])
    assert r.at[("p1", "solar_pv"), "model_cf_corrected"] == pytest.approx(0.1242 / 0.70)       # ~0.177
    assert r.at[("p11", "solar_pv"), "ratio_corrected"] == pytest.approx(0.2416 / 0.79 / 0.3123)
    assert r.at[("p9", "solar_pv"), "ratio_corrected"] == pytest.approx(0.8)                   # no share given
    assert r.at[("p1", "wind_onshore"), "ratio_corrected"] == pytest.approx(0.3 / 0.35)         # wind unaffected
    z = ra.reassess(m, pd.DataFrame({"zone": ["p9"], "pv_cf_understatement": [0.2]})).set_index(["zone", "technology"])
    assert z.at[("p9", "solar_pv"), "ratio_corrected"] == pytest.approx(1.0)
    assert z.at[("p1", "solar_pv"), "ratio_corrected"] == pytest.approx(0.1242 / 0.2316)        # not in the table


def test_neighbour_is_chosen_per_weather_year(tmp_path):
    """As in the real file, blocks lose different years: a site zero only in 2010-11 fills a neighbour's 2008 (it has
    data then) but not its 2010, which comes from the next site with data in 2010."""
    src = tmp_path / "p"
    src.mkdir()
    day = np.sin(np.linspace(0, np.pi, H))
    prof = lambda f, i: day * f * (0.8 + 0.05 * i)                                         # noqa: E731
    cols = {s: np.concatenate([prof(f, i) for i in range(7)]) for s, f in (("a", 0.5), ("b", 0.6), ("c", 0.7))}
    cols["a"][: 5 * H] = 0.0                                   # block 0: zero 2007-2011
    cols["b"][3 * H: 5 * H] = 0.0                              # blocks 4-9: zero 2010-2011
    pd.DataFrame(cols).astype("float32").to_parquet(src / NAME, index=False)
    sites = pd.DataFrame({"site": ["a", "b", "c"], "latitude": [40.0, 40.01, 41.0], "longitude": [-100.0] * 3,
                          "capacity_mw": [1.0] * 3})
    zeroed = pd.DataFrame({"site": ["a", "b"], "years_zero": ["2007;2008;2009;2010;2011", "2010;2011"]})
    man = pr.repair_folder(src, tmp_path / "o", NAME, sites, first_year=2007, hours=H, batch_rows=5,
                           log=lambda m: None, zeroed=zeroed)
    log = pd.read_csv(tmp_path / "o" / pr.LOG, dtype={"site": str, "neighbour": str}).set_index(["site", "year"])
    assert log.at[("a", 2008), "neighbour"] == "b" and log.at[("a", 2010), "neighbour"] == "c"
    assert log.at[("b", 2010), "neighbour"] == "c"             # a has no data in 2010 either
    assert man["zeroed_list_check"]["in_both"] == 7 and not man["zeroed_list_check"]["listed_not_found"]
    assert man["fill_summary"]["distinct_neighbours"] == 2 and man["zero_site_years_with_capacity_after"] == 0
    out = pd.read_parquet(tmp_path / "o" / NAME)
    scale = cols["a"][5 * H:].mean() / cols["b"][5 * H:].mean()
    np.testing.assert_allclose(out["a"][H:2 * H], cols["b"][H:2 * H] * scale, rtol=1e-5)


def test_cli_sites_default_columns():
    from s0_workflow.scripts import repair_pv_profiles as cli
    meta = pd.DataFrame({"cpa_id": [1, 2], "Latitude": [40.0, 42.0], "LONGITUDE": [-100.0, -102.0], "CPA_MW": [0.0, 0.0]})
    sm = pd.DataFrame({"CPA_ID": [1, 2], "Site": [5, 5]})
    s = cli.sites_from(meta, sm).set_index("site")                                         # names matched in any case
    assert s.at["5", "latitude"] == 41.0 and s.at["5", "capacity_mw"] == 0.0               # plain mean at 0 MW
    with pytest.raises(SystemExit, match="no column 'cpa_mw'"):
        cli.sites_from(meta.rename(columns={"CPA_MW": "mw"}), sm)


# ---- §131: neighbours by daily correlation (no coordinates) ----

HC = 24 * 20                              # 20 days per "weather year"


def _corr_profiles(tmp_path):
    """Zone A: weather groups x and y (sites share their group's daily cloud factor); zone B: weather x. Site a1 (group
    x) is zero 2007-11; its best donor is a2 (group x, zone A), not a3 (group y, zone A) nor b1 (group x, zone B, a
    closer match but another zone). Site a9 (zone A) is zero in 2007 and the only other zone-A sites lack 2007 too, so
    it is filled outside its zone."""
    rng = np.random.default_rng(3)
    day = np.clip(np.sin(np.linspace(0, np.pi, 24)), 0, None)
    weather = {g: rng.uniform(0.2, 1.0, 7 * 20) for g in ("x", "y")}

    def prof(g, f, noise):
        cloud = weather[g] * rng.uniform(1 - noise, 1 + noise, 7 * 20)
        return (np.tile(day, 7 * 20) * np.repeat(cloud, 24) * f).astype("float32")
    cols = {"a1": prof("x", 0.7, 0.05), "a2": prof("x", 0.8, 0.10), "a3": prof("y", 0.8, 0.0),
            "b1": prof("x", 0.8, 0.0)}
    cols["a1"][: 5 * HC] = 0.0
    src = tmp_path / "pc"
    src.mkdir()
    pd.DataFrame(cols).to_parquet(src / NAME, index=False, row_group_size=97)
    sites = pd.DataFrame({"site": ["a1", "a2", "a3", "b1"], "zone": ["A", "A", "A", "B"], "capacity_mw": [10.0] * 4})
    return src, sites, cols


def test_correlation_neighbour_same_zone_same_weather(tmp_path):
    src, sites, cols = _corr_profiles(tmp_path)
    man = pr.repair_folder(src, tmp_path / "o", NAME, sites, first_year=2007, hours=HC, batch_rows=211,
                           log=lambda m: None, neighbour_method="correlation")
    log = pd.read_csv(tmp_path / "o" / pr.LOG, dtype={"site": str, "neighbour": str})
    assert set(log.neighbour) == {"a2"} and (log.scope == "zone").all() and log.distance_km.isna().all()
    assert (log.correlation > 0.9).all()
    assert man["neighbour_method"] == "correlation" and man["zero_site_years_with_capacity_after"] == 0
    assert man["fill_summary"]["site_years_filled_outside_zone"] == 0 and "distance_km_median" not in man["fill_summary"]
    out = pd.read_parquet(tmp_path / "o" / NAME)
    scale = cols["a1"][5 * HC:].mean() / cols["a2"][5 * HC:].mean()
    np.testing.assert_allclose(out["a1"][2 * HC:3 * HC], np.clip(cols["a2"][2 * HC:3 * HC] * scale, 0, 1), rtol=1e-5)
    json.loads((tmp_path / "o" / pr.MANIFEST).read_text())                            # strict JSON (no NaN)


def test_correlation_daily_sums_match_hourly(tmp_path):
    src, sites, cols = _corr_profiles(tmp_path)
    st = pr.scan(src / NAME, list(cols), first_year=2007, hours=HC, batch_rows=211, daily_years=(2012, 2013))
    want = cols["a3"][5 * HC:].reshape(-1, 24).sum(axis=1)
    np.testing.assert_allclose(st["daily"]["a3"].to_numpy(), want, rtol=1e-5)


def test_correlation_outside_zone_and_errors(tmp_path):
    src, sites, cols = _corr_profiles(tmp_path)
    sites = sites.assign(zone=["A", "C", "C", "B"])                  # a1 alone in zone A: filled from another zone
    man = pr.repair_folder(src, tmp_path / "o", NAME, sites, first_year=2007, hours=HC, batch_rows=500,
                           log=lambda m: None, neighbour_method="correlation")
    log = pd.read_csv(tmp_path / "o" / pr.LOG, dtype={"site": str, "neighbour": str})
    assert (log.status == "filled outside zone").all() and set(log.neighbour) <= {"a2", "b1"}
    assert man["site_years_filled"] == 5 and man["fill_summary"]["site_years_filled_outside_zone"] == 5
    with pytest.raises(ValueError, match="zone column"):
        pr.repair_folder(src, tmp_path / "o2", NAME, sites.drop(columns="zone"), first_year=2007, hours=HC,
                         log=lambda m: None, neighbour_method="correlation")
    with pytest.raises(ValueError, match="neighbour_method"):
        pr.repair_folder(src, tmp_path / "o3", NAME, sites, first_year=2007, hours=HC, log=lambda m: None,
                         neighbour_method="nearest")


def test_cli_correlation_sites_from_metadata(tmp_path):
    from s0_workflow.scripts import repair_pv_profiles as cli
    meta = pd.DataFrame({"CPA_ID": [1, 2, 3, 4], "ipm_region": ["p1", "p1", "p2", "p3"], "cpa_mw": [10.0, 30.0, 50.0, 0.0]})
    sm = pd.DataFrame({"CPA_ID": [1, 2, 3, 4], "Site": [7, 7, 7, 8]})
    s = cli.sites_zone_from(meta, sm).set_index("site")
    assert s.at["7", "zone"] == "p2" and s.at["7", "capacity_mw"] == 90.0 and s.at["7", "zones"] == 2   # most MW
    assert s.at["8", "zone"] == "p3" and s.at["8", "capacity_mw"] == 0.0
    with pytest.raises(SystemExit, match="no column 'ipm_region'"):
        cli.sites_zone_from(meta.rename(columns={"ipm_region": "zone_x"}), sm)
    src, sites, cols = _corr_profiles(tmp_path)
    pd.DataFrame({"CPA_ID": [1, 2, 3, 4], "region": ["A", "A", "A", "B"], "cpa_mw": [1.0] * 4}).to_csv(
        tmp_path / "meta.csv", index=False)
    pd.DataFrame({"CPA_ID": [1, 2, 3, 4], "Site": ["a1", "a2", "a3", "b1"]}).to_csv(tmp_path / "map.csv", index=False)
    rc = cli.main(["--profiles-dir", str(src), "--out-dir", str(tmp_path / "out"), "--metadata", str(tmp_path / "meta.csv"),
                   "--site-map", str(tmp_path / "map.csv"), "--region-col", "region", "--neighbour-method", "correlation",
                   "--hours", str(HC), "--no-links", "--hash"])
    man = json.loads((tmp_path / "out" / pr.MANIFEST).read_text())
    assert rc == 0 and man["neighbour_method"] == "correlation" and "sha256" in man["output"]
    assert (tmp_path / "out_sites.csv").exists()
