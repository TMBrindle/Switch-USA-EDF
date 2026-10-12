# Runbook: repaired PV profiles (`pg_data/profiles_pvfix`) and the fidelity-scan rerun

**For:** the dashboards session, on the VM. **Code:** `tom/s0-v3.2` (CHANGES §111, §116, §117, §131).

**What it does:** writes a repaired copy of the reV solar profiles folder. Each all-zero site-year (5,390 sites,
2007–2011) is filled from the nearest site with data in the same weather year, scaled by the two sites' 2012–13 mean
ratio. It never changes `pg_data/profiles`. Then it reruns the sample-fidelity scan on the repaired profiles.

## 1. Before you start
- **Branch:** a worktree of `tom/s0-v3.2` (see `s0_workflow/VM_RECIPES.md`, "S0 v3.1 and v3.2 worktrees"). The
  `switch-pg-reeds` environment has pyarrow, pandas and scipy.
- **Neighbour method: `correlation` (CHANGES §131).** The VM has no site coordinates. None of the 11 copies of
  `solar_lcoe_ReEDS.csv` has latitude/longitude, and neither does the site mapping (CPA_ID, Site, profile_dist) or
  the profile file. So the distance method can't run.
  - Each zeroed site-year is filled from the site in the same zone whose daily output over 2012–13 correlates best
    with the zeroed site's own 2012–13 output.
  - The donor must have data in that weather year.
  - Only if no site in the zone has data in that year, the best-correlated site anywhere is used (status
    `filled outside zone`).
  - The scale is unchanged: the two sites' 2012–13 mean ratio.
- **Inputs (read only):**
  - `pg_data/profiles/solar_rev_profiles_20240801.parquet` (61,320 rows × 68,923 columns, weather years 2007–2013);
  - `pg_data/profiles/solar_site_mapping_20240801.parquet` (`CPA_ID` → `Site`);
  - `pg_data/resource_groups/ReEDS-cpas-patched/solar_lcoe_ReEDS.csv` (CPA zone and `cpa_mw`);
  - `s0_workflow/data/pv_profile_repair/zeroed_pv_sites.csv` (the dashboards' list, data/rev-cf-mismatch 9383b09).
- **The zone column:** before running, print the metadata's columns and pick the one whose values are the model
  zones (`p1` … `p134`). The default is `ipm_region`; pass `--region-col <name>` if it differs. A missing column stops
  the run and lists the columns there are.
  ```bash
  python -c "import pandas as pd; d=pd.read_csv('pg_data/resource_groups/ReEDS-cpas-patched/solar_lcoe_ReEDS.csv', nrows=3); print(d.T)"
  ```
- **Disk:** copies, not links (`--no-links`). The output folder takes the repaired solar file (about the size of the
  source) plus full copies of the folder's other files. Check with `du -sh pg_data/profiles/*` and keep about 1.2 ×
  the solar file plus the others free in the scratch volume.
- **Memory:** about 3 GB peak with the default `--batch-rows 2190`, plus about 1 GB for the correlation (daily sums,
  730 × 68,923). Use `--batch-rows 1095` to halve the batch part.
- **Runtime:** an estimate; please record the real time. The file is read three times (scan, write, rescan of the
  output), and `--hash` adds two more full reads for the sha256 of source and output. Expect roughly 45–120 minutes.

## 2. Run (output to the dashboards session's scratch folder)
```bash
cd "<worktree>"
OUT="<dashboards scratch>/profiles_pvfix"
time python s0_workflow/scripts/repair_pv_profiles.py \
    --profiles-dir pg_data/profiles \
    --out-dir "$OUT" \
    --neighbour-method correlation \
    --metadata pg_data/resource_groups/ReEDS-cpas-patched/solar_lcoe_ReEDS.csv \
    --site-map pg_data/profiles/solar_site_mapping_20240801.parquet \
    --region-col <zone column> \
    --zeroed s0_workflow/data/pv_profile_repair/zeroed_pv_sites.csv \
    --hash --no-links \
    2>&1 | tee pv_repair.log
```
- **Optional arguments:**
  - `--profile` (default `solar_rev_profiles_20240801.parquet`);
  - `--ref-years 2012 2013`; `--first-year 2007`; `--hours 8760`;
  - `--batch-rows 2190`; `--warn-corr 0.8` (the log warns about fills from a neighbour correlated below this);
  - `--cpa-col/--site-col/--cap-col` if the metadata's or the mapping's column names differ (defaults `CPA_ID`,
    `Site`, `cpa_mw`);
  - `--neighbour-method distance` with `--sites` (site, latitude, longitude, capacity_mw) or `--lat-col/--lon-col`:
    only if a coordinate source turns up later.
- **Outputs:**
  - in `$OUT/`: the repaired `solar_rev_profiles_20240801.parquet`, `pv_profile_repair_log.csv` (one row per zeroed
    site-year: neighbour, correlation, scope, scale, clipped hours, status), `pv_profile_repair_manifest.json` (with
    `neighbour_method: correlation` and the sha256 of source and output) and copies of the folder's other files;
  - the sites table it built (site, zone, capacity_mw, number of zones), written beside the folder as
    `$OUT_sites.csv`.
- **Exit code:** 0 when no site-year with capacity is still all zero, else 1.

## 3. What the manifest should show (pass / fail)
The values come from `zeroed_pv_sites.csv`:
- 5,390 sites and 14,553 site-years in total.
- 5,330 sites (14,350 site-years) have CPA capacity.
- 60 sites (203 site-years) have no CPA mapped. They have no zone or capacity, so they are left as they are and aren't
  checked; PowerGenome never uses them.

| Field | Expected | Pass if |
|---|---|---|
| `neighbour_method` | `correlation` | = `correlation` |
| `zero_site_years_with_capacity_after` | 0 | **= 0** (else exit 1; the build refuses the folder) |
| `site_years_zero_before` | 14,350 | = 14,350 (a larger number means zeros the dashboards didn't list: investigate) |
| `sites_zero_before` | 5,330 | = 5,330 |
| `site_years_filled` | 14,350 | = `site_years_zero_before` (filled in zone + filled outside zone) |
| `site_years_unfillable` | 0 | = 0 (any row in the log with `status` starting "unfilled" fails) |
| `zeroed_list_check.listed_site_years` | 14,553 | = 14,553 |
| `zeroed_list_check.in_both` | 14,350 | = 14,350 |
| `zeroed_list_check.found_not_listed` | [] | empty |
| `zeroed_list_check.listed_not_found` | the 203 no-CPA site-years (first 50 shown) | only sites marked "no CPA mapped" in the list |
| `fill_summary.correlation_p10` | not known in advance | ≥ 0.8; **review** if `correlation_min` < 0.6 (the log warns below 0.8) |
| `fill_summary.site_years_filled_outside_zone` | 0 expected | **review** any: zones whose sites all lack a year |
| `fill_summary.max_site_years_from_one_neighbour` | — | review if > 50: one site fills a whole block |
| `fill_summary.scale_min` / `scale_max` | median about 0.98 (p1 block-0 sites vs neighbours, fd4a3a1) | `scale_share_0.7_1.3` ≥ 0.99; review the outliers in the log |
| `clipped_hours` | small | < 0.1% of filled hours (14,350 × 8,760) |
| `other_files_copied` | every other file in `pg_data/profiles` | none missing (`other_files_linked` empty with `--no-links`) |
| `source.sha256`, `output.sha256` | present | both present (`--hash`) |

**Checks to report back:**
1. Neighbour correlations for p1 cluster 2 (81 sites, 32 GW): filter the log to the p1 sites in `zeroed_pv_sites.csv`.
   Compare with `p1_block0_vs_neighbours.csv` (the dashboards' own correlation-chosen neighbours, fd4a3a1).
2. p1's profile CF over the full record after the repair. It should rise from 0.125 to about 0.178 (the years-with-data
   CF).
3. The real runtime, peak memory and disk use.

## 4. Use it in a build
In the case's settings (or `pg/settings/s0_production.yml`, once Tom approves):
```yaml
s0_production:
  pv_profile_repair: {mode: neighbour_fill, profiles_dir: <the repaired folder>, verify: manifest}
```
- **What the build does:** checks the manifest, stopping if the file changed since the repair or any site-year with
  capacity is still zero. It then points `RESOURCE_GROUP_PROFILES` at the folder and copies the fill log into each case.
  `verify: full` (with `sites: <folder>_sites.csv`) rescans the file instead, which takes one full read.
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
