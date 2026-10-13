# Recipe L (v3.2): offshore split, cluster cache, PowerGenome import

**Commit:** the hash in `s0_workflow/runcards/v32/READY_S0_V32`. **Source:** CHANGES §134, §137.
Run all of it before the S0 v3.2 chain (`s0_v32.md`). Flags-off build 2 (`flagsoff_build2.md`) needs steps 0 and 6
only.

## RULE (incident, 13 Oct 2026; CHANGES §137)

**Never write into, or around, a folder another build scans.**
- **The roots:** every worktree's `pg/settings/env.yml` points `RESOURCE_GROUPS` at the shared
  `Switch-USA-PG-ReEDS\pg_data\resource_groups`, and PowerGenome loads every `**/*.json` under it. The same goes for
  `RESOURCE_GROUP_PROFILES` (`pg_data\profiles`).
- **What happened:** from 04:37 to about 10:40 UTC the old card had the split written inside
  `pg_data\resource_groups`. Any build on the shared data could have crashed on `offshore_split_manifest.json`, or
  counted offshore twice.
- **Where things go now:**
  - The split goes to a full, hard-linked copy of the root beside it: `pg_data\resource_groups_v32`.
  - Its manifest goes to `pg_data\resource_groups_v32__meta`, outside anything PowerGenome globs.
- **What enforces it:**
  - The script refuses any output path that overlaps `--groups-dir`, the `env.yml` roots, or any `--shared-root`.
  - The build refuses a `resource_groups_dir` that overlaps the roots the build itself scans.
- **`env.yml`: leave every worktree's as it is.**
  - The v3.2 build points `RESOURCE_GROUPS` at the copy through `s0_production.offshore_filter_fix.resource_groups_dir`.
    That applies only to S0 cases with the flag on, so nothing else ever sees the copy.
  - Pointing a worktree's `env.yml` at the copy would put it under that worktree's flags-off builds too. The build
    refuses that combination ("is off but RESOURCE_GROUPS … is a split folder").
- **The held folder:** don't restore `ic_test_fedpol_work\recipeL_held\` into `pg_data\resource_groups`. It was made by
  the old script (manifest inside it), so discard it once step 2 below has passed.

## 0. Submodule at the pin

```bat
git submodule update --init PowerGenome
git -C PowerGenome rev-parse HEAD
```
Expect `c4c433cfefd0ae11f5b3f47ae05e3b9d7c90a691`.

## 1. Survey (read only)

Use the `RESOURCE_GROUPS` root from `env.yml`, the shared `pg_data\resource_groups`:
```bat
"<ic-pipeline python>" s0_workflow/scripts/split_offshore_groups.py --survey --groups-dir <pg_data>\resource_groups
```
- **Expect:** four JSONs, `ReEDS-cpas-patched/offshorewind_{fixed,floating}_{false,true}.json`, all with
  `metadata: offshorewind_lcoe_ReEDS.csv`.
- **CSVs listed:** the shared file has 39,005 rows; the per-type files have 13,108, 4,215, 20,054 and 1,628 rows.
- **Pairing:** match each JSON with the per-type file of the same turbine type and pref site.

## 2. Split into a copy BESIDE the root

```bat
"<ic-pipeline python>" s0_workflow/scripts/split_offshore_groups.py ^
    --groups-dir <pg_data>\resource_groups ^
    --out-dir <pg_data>\resource_groups_v32 ^
    --map ReEDS-cpas-patched/offshorewind_fixed_true.json=ReEDS-cpas-patched/<fixed/true per-type csv> ^
    --map ReEDS-cpas-patched/offshorewind_fixed_false.json=ReEDS-cpas-patched/<fixed/false per-type csv> ^
    --map ReEDS-cpas-patched/offshorewind_floating_false.json=ReEDS-cpas-patched/<floating/false per-type csv> ^
    --map ReEDS-cpas-patched/offshorewind_floating_true.json=ReEDS-cpas-patched/<floating/true per-type csv>
```
- **Where `<pg_data>` is:** the shared `pg_data`. The worktree's `pg_data` path must reach the same folder, because
  `resource_groups_dir: pg_data/resource_groups_v32` is resolved from the worktree root (as `pg_data/profiles_pvfix`
  is).
- **What the copy is:** the whole root, every other group and the existing resource groups included, hard-linked. The
  four JSONs are rewritten to point at new metadata files, which hold the shared, patched rows of each type's CPAs.
- **Refusals:** the script stops unless the per-type files partition the shared file exactly, every JSON using the
  shared file is mapped, and no JSON references a missing file.
- **How references are resolved, as PowerGenome does:** `metadata` must sit next to its JSON. Profiles and site maps
  may sit next to it or in `RESOURCE_GROUP_PROFILES`, read from `env.yml`; add `--profiles-root` if they live elsewhere.

## 3. Check (report these)

- **The log line:**
  - four groups, each with CPAs and GW at LCOE ≤ 125, adding up to "whole 2,985.5 GW in 36 zones";
  - "partition exact (39,005 CPAs)";
  - "no broken references".
  - Floating/true should be about 147.8 GW (the VM's measurement).
- **The manifest folder:** `<pg_data>\resource_groups_v32__meta` holds `offshore_split_manifest.json` and
  `offshore_true_resource_by_zone.csv`.
  - `dir /s /b <pg_data>\resource_groups_v32\offshore_split_manifest.json` must find nothing.
  - Report `zone_differs_from_shared` per group. It should be non-zero only where the patch moved CPAs to p123/p133.
- **The source is untouched:** `<pg_data>\resource_groups` has no new files, and its dates are still 22 Dec 2025.
- **The fourth group is offered:** the build adds a floating/pref-site-1 cluster entry for every floating/pref-site-0
  entry. The log line reads `... N floating/pref-site-1 cluster entries added`, with N > 0.
  - Each case then has `OffShoreWind_..._floating_1` technologies.
  - The offshore check requires all four groups and stops if any group with resource is missing.
  - `resources.yml` is unchanged, so v3.1 and flags-off builds are unaffected.

## 4. Cluster cache (`pg\extra_inputs\cluster_assignments`, per worktree)

- **Does the key change on its own?** Not known from this repo, so treat the answer as no.
- **The rule:** move the whole folder aside before the first v3.2 build in this worktree, and keep it for flags-off
  builds:
  ```bat
  move pg\extra_inputs\cluster_assignments pg\extra_inputs\cluster_assignments.v31
  ```
- **What the build enforces:**
  - A v3.2 build needs an empty or missing cache the first time, and writes `S0_V32_CACHE.json`: a fingerprint of the
    PV sha256 and the split manifest.
  - Later v3.2 builds need the same fingerprint.
  - A flags-off build refuses that cache.
- **One kind of case per build command:** don't build v3.2 and flags-off cases in the same `pg_to_switch.py` call.

## 5. PV folder

`pg_data\profiles_pvfix` must be in place (CHANGES §132). The build checks the manifest, the method and the full
sha256.

## 6. Import PowerGenome from the submodule (V32-04)

```bat
set PYTHONPATH=<worktree>\PowerGenome
"<switch-pg-reeds-fedpol python>" -c "import powergenome; print(powergenome.__file__)"
```
- **The import:** it must print `<worktree>\PowerGenome\powergenome\__init__.py`.
- **The log:** `PowerGenome imported from <path> (commit <sha>; repo submodule: True; AEO_2026_regional_cost_corrections.csv present: True)`.
- **Each case folder:** `powergenome_provenance.json`.
- **Enforcement:** the v3.2 build stops on any other commit (`powergenome_commit`).
