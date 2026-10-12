# Runbook: repaired PV profiles (`pg_data/profiles_pvfix`) and the fidelity-scan rerun

**For:** the dashboards session, on the VM. **Code:** `tom/s0-v3.2` (CHANGES §111, §116, §117).

**What it does:** writes a repaired copy of the reV solar profiles folder. Each all-zero site-year (5,390 sites,
2007–2011) is filled from the nearest site with data in the same weather year, scaled by the two sites' 2012–13 mean
ratio. It never changes `pg_data/profiles`. Then it reruns the sample-fidelity scan on the repaired profiles.

## 1. Before you start
- **Branch:** a worktree of `tom/s0-v3.2` (see `s0_workflow/VM_RECIPES.md`, "S0 v3.1 and v3.2 worktrees"). The
  `switch-pg-reeds` environment has pyarrow, pandas and scipy.
- **Inputs (read only):**
  - `pg_data/profiles/solar_rev_profiles_20240801.parquet` (61,320 rows × 68,923 columns, weather years 2007–2013);
  - `pg_data/profiles/solar_site_mapping_20240801.parquet` (`CPA_ID` → `Site`);
  - `pg_data/resource_groups/ReEDS-cpas-patched/solar_lcoe_ReEDS.csv` (CPA `latitude`, `longitude`, `cpa_mw`);
  - `s0_workflow/data/pv_profile_repair/zeroed_pv_sites.csv` (the dashboards' list, data/rev-cf-mismatch 9383b09).
- **Disk:** the output parquet is about the size of the source. The folder's other files (wind profiles, site
  mappings) are hard-linked, which takes no space if `pg_data/profiles_pvfix` is on the same volume. If linking fails,
  they are copied and listed under `other_files_copied` in the manifest. Free space needed: about 1.2 × the solar file,
  plus the size of the other files if they get copied (check with `du -sh pg_data/profiles/*`).
- **Memory:** about 3 GB peak with the default `--batch-rows 2190` (one batch is 2,190 × 68,923 float32, about 0.6 GB,
  plus copies). Use `--batch-rows 1095` to halve it.
- **Runtime:** an estimate; please record the real time. The file is read three times (scan, write, rescan of the
  output), each pass streaming about 17 GB of values in about 200 batches. Expect roughly 30–90 minutes, mostly
  pyarrow ↔ numpy conversion.

## 2. Run
```bash
cd "<worktree>"
time python s0_workflow/scripts/repair_pv_profiles.py \
    --profiles-dir pg_data/profiles \
    --out-dir pg_data/profiles_pvfix \
    --metadata pg_data/resource_groups/ReEDS-cpas-patched/solar_lcoe_ReEDS.csv \
    --site-map pg_data/profiles/solar_site_mapping_20240801.parquet \
    --zeroed s0_workflow/data/pv_profile_repair/zeroed_pv_sites.csv \
    2>&1 | tee pv_repair.log
```
- **Optional arguments:**
  - `--profile` (default `solar_rev_profiles_20240801.parquet`);
  - `--ref-years 2012 2013`; `--first-year 2007`; `--hours 8760`;
  - `--batch-rows 2190`; `--warn-km 100`;
  - `--hash` (sha256 of source and output in the manifest; adds two full reads);
  - `--no-links` (copy instead of linking);
  - `--cpa-col/--site-col/--lat-col/--lon-col/--cap-col` if the metadata's column names differ. A missing column stops
    the run and lists the columns there are.
- **Outputs:**
  - in `pg_data/profiles_pvfix/`: the repaired `solar_rev_profiles_20240801.parquet`, `pv_profile_repair_log.csv`
    (one row per zeroed site-year: neighbour, distance, scale, clipped hours, status), `pv_profile_repair_manifest.json`
    and the linked or copied other files;
  - the sites table it built, written beside the folder as `pg_data/profiles_pvfix_sites.csv`.
- **Exit code:** 0 when no site-year with capacity is still all zero, else 1.

## 3. What the manifest should show (pass / fail)
The values come from `zeroed_pv_sites.csv`:
- 5,390 sites and 14,553 site-years in total.
- 5,330 sites (14,350 site-years) have CPA capacity.
- 60 sites (203 site-years) have no CPA mapped. They have no coordinates or capacity, so they are left as they are and
  aren't checked; PowerGenome never uses them.

| Field | Expected | Pass if |
|---|---|---|
| `zero_site_years_with_capacity_after` | 0 | **= 0** (else exit 1; the build refuses the folder) |
| `site_years_zero_before` | 14,350 | = 14,350 (a larger number means zeros the dashboards didn't list: investigate) |
| `sites_zero_before` | 5,330 | = 5,330 |
| `site_years_filled` | 14,350 | = `site_years_zero_before` |
| `site_years_unfillable` | 0 | = 0 (any row in the log with `status` starting "unfilled" fails) |
| `zeroed_list_check.listed_site_years` | 14,553 | = 14,553 |
| `zeroed_list_check.in_both` | 14,350 | = 14,350 |
| `zeroed_list_check.found_not_listed` | [] | empty |
| `zeroed_list_check.listed_not_found` | the 203 no-CPA site-years (first 50 shown) | only sites marked "no CPA mapped" in the list |
| `fill_summary.distance_km_p90` | not known in advance | ≤ 50 km; **review** if `distance_km_p99` > 100 km (the log warns) |
| `fill_summary.max_site_years_from_one_neighbour` | — | review if > 50: one site fills a whole block |
| `fill_summary.scale_min` / `scale_max` | median about 0.98 (p1 block-0 sites vs neighbours, fd4a3a1) | 99% of fills within 0.7–1.3; review the outliers in the log |
| `clipped_hours` | small | < 0.1% of filled hours (14,350 × 8,760) |
| `other_files_linked` + `other_files_copied` | every other file in `pg_data/profiles` | none missing |

**Checks to report back:**
1. Fill distances for p1 cluster 2 (81 sites, 32 GW): filter the log to the p1 sites in `zeroed_pv_sites.csv`.
2. p1's profile CF over the full record after the repair. It should rise from 0.125 to about 0.178 (the years-with-data
   CF).
3. The real runtime, peak memory and disk use.

## 4. Use it in a build
In the case's settings (or `pg/settings/s0_production.yml`, once Tom approves):
```yaml
s0_production:
  pv_profile_repair: {mode: neighbour_fill, profiles_dir: pg_data/profiles_pvfix, verify: manifest}
```
- **What the build does:** checks the manifest, stopping if the file changed since the repair or any site-year with
  capacity is still zero. It then points `RESOURCE_GROUP_PROFILES` at the folder and copies the fill log into each case.
  `verify: full` (with `sites: pg_data/profiles_pvfix_sites.csv`) rescans the file instead, which takes one full read.
- **Cluster cache:** PowerGenome's cluster cache (`pg/extra_inputs/cluster_assignments/`) holds profiles. Make sure the
  repaired build doesn't reuse a cache built from the unrepaired profiles: compare the PV capacity factors (step 5), and
  clear or bypass the cache if they are unchanged.

## 5. Rerun the sample-fidelity scan (dbc3637 method)
1. **Build** S0prod_A (all stages) from `tom/s0-v3.2` with `pv_profile_repair: neighbour_fill` and
   `time_sampling.cf_fidelity: {mode: "off"}`, into a fresh folder.
2. **Rerun the scan** (`README_sample_fidelity.md` method: model weights, full record 2007–2013, the case's cluster
   cache, wind × the loss factor) for PV, onshore and offshore wind and load. Expect:
   - PV zone ratios to move. The zeroed years no longer drag on either the targets or the sampled days, so the 110 of
     670 PV zone-stages outside 0.95–1.05 should fall.
   - Wind and load unchanged (control).
   - PJM PV (p122, p124, p125) still 0.80–0.90 in 2030 and 2035 if the day selection, not the zeros, causes it.
3. **Build again** with `cf_fidelity: {mode: rescale}`. Check `vcf_fidelity.csv` in each case:
   - `residual` about 0 for every cluster;
   - the scale distribution by technology, and how many are flagged outside 0.8–1.25;
   - `capped_share_of_sample_hours`.

   Rerun the scan: every PV, onshore and offshore zone-stage should then be within 0.95–1.05 (MW-weighted over the
   zone's clusters).
4. **Report:** the scan summary (before and after repair; after repair with rescale), plus the build time with and
   without rescale. Rescale should add seconds, not minutes.
