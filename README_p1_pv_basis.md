# p1 PV basis checks (11 Oct 2026)

These follow `README_zeroed_pv_profiles.md` and `README_cf_basis_checks.md`. The analysis was read-only.

**Sources:**
- `pg_data/resource_groups/ReEDS-cpas-patched/solar_lcoe_ReEDS.csv` (all 405,555 CPAs with cpa_mw > 0).
- `pg_data/profiles/solar_rev_profiles_20240801.parquet` and `solar_site_mapping_20240801.parquet`.

## 1. Supply-curve columns (`supply_curve_by_zone.csv`)
- **No basis columns:** `solar_lcoe_ReEDS.csv` has no AC or DC capacity factor, DC/AC ratio, AC or DC capacity, tracking, tilt or loss columns.
  - Its resource columns are `cf`, `cpa_mw`, `incap` and `Area`, the cost columns (`resource_annuity`, `resource_fom`, `interconnect_annuity`, `interconnect_capex_mw`) and `lcoe`.
  - `path` holds [CPA_ID, an id].
- **What the zone table shows:** for each zone, the MW-weighted `cf` and its spread, capacity densities, annuity and FOM per MW, and a check of the identity lcoe = (resource_annuity + resource_fom + interconnect_annuity) / (8.76 × cf).
- **p1 against the other zones:**
  - p1 has the **lowest supply-curve cf of all 134 zones**: MW-weighted 0.230, against a median of 0.266. p2–p4 (WA) are 0.256–0.271 and p5 (OR) is 0.249.
  - Its densities are not unusual. `incap` is 9 MW/km² in every zone. cpa_mw/incap is 0.74 in p1, 0.73 in p5, 0.67–0.79 in p9–p11 and 0.93–0.97 in p2–p4.
  - Costs are the same basis as its neighbours: one resource_annuity of 48,359.91 $/MW-yr (p4 identical) and resource_fom 15,221.57.
  - The lcoe identity holds exactly in every zone, so `lcoe` is built from `cf`.

## 2. Peak hourly CF (`site_peak_cf.csv`, `zone_peak_cf.csv`)
- **What is computed:** for each profile site, over its years with data:
  - the 99.9th-percentile and maximum hourly CF;
  - the share of producing hours within 0.5% of the maximum (a flat top means inverter clipping);
  - the mean CF.
- **No cap near 0.77 in p1 or anywhere:**
  - The 99.9th percentile is 1.0 at the median site in every zone. In p1 the 5th-percentile site is at 0.986.
  - No zone has any site with a 99.9th percentile in 0.74–0.80.
  - The profiles reach 1.0 everywhere, so they are AC-normalised. They are not DC-normalised in p1.
- **What does differ in p1:** it seldom reaches the AC limit. The median share of producing hours at the maximum is 0.4%, the lowest of all zones (zone median 6.9%, maximum 20.8%; p5 2.9%, p2 10.7%).

## 3. Same reV run? (provenance and consistency)
**Provenance:**
- All six copies of `solar_lcoe_ReEDS.csv` on the VM are byte-identical (md5 6acee3b16df7…). Every copy of the profile file is the same 4,586,074,285 bytes, dated 2024-08-05.
- `patch_pg_resource_groups.py` changes only offshore wind and existing-resource files. The solar supply curve is as distributed in `ReEDS-cpas-patched.zip`.
- The parquet files carry no run metadata, only pyarrow 14.0.2 schema metadata.
- The documentation is inconsistent: `pg_data.yml` says the reV profiles were made in May 2025, but the files are named 20240801 and dated 5 Aug 2024.
- The files cannot settle which run produced which; the data can say whether they agree.

**Consistency, supply-curve cf against profile CF (years with data):**
- Every one of the 405,555 CPAs maps to a profile site.
- **Nationally they agree only loosely:**
  - Site level: Pearson 0.61, Spearman 0.57.
  - Zone level: Pearson 0.82, with fit profile = 0.988 × sc − 0.011 and residual SD 0.013.
  - Within zones, the median Spearman is 0.05. Outside p1 (0.69) and p5 (0.92) the supply-curve cf does not rank sites the way the profiles do.
  - So the `cf` column does not appear to be computed from these profiles at site level in any zone.
- **p1 is the outlier:**
  - Zone ratio 0.771, the lowest of 134 zones (median 0.945, IQR 0.92–0.97). The next lowest are p5 0.843, p125 0.856 and p121 0.865.
  - Its residual from the zone fit is −0.039, or −3.1 SD.
  - Across p1's 365 sites the ratio is **uniform**: 5th–95th percentile 0.74–0.83, median 0.776.
  - The ratio is the same for sites with only 2012–13 data (0.761) and with all 7 years (0.782), so it is not the zeroed blocks.
  - A uniform shortfall is what you would expect if the zone's profiles and supply curve were produced on different bases or inputs, rather than mismatched site by site.
  - p1's sites are profile columns 0–1,860; a third of them fall in zeroed block 0 (columns 0–538).
