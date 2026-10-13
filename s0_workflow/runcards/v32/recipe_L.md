# Recipe L (v3.2): offshore split, cluster cache, PowerGenome import

**Commit:** the hash in `s0_workflow/runcards/v32/READY_S0_V32`. **Source:** CHANGES §134 (also in `s0_workflow/VM_RECIPES.md`,
recipe L). Run all of it before the S0 v3.2 chain (`s0_v32.md`) and before flags-off build 2 (`flagsoff_build2.md`;
that build needs steps 0–3 and 6 only, not step 4).

## What changed in the code (§134)

- **`split_offshore_groups.py`, `--map JSON=CSV` (one per JSON) and `--survey`.** This is ReEDS-cpas-patched's real
  layout: four JSONs `offshorewind_{fixed,floating}_{false,true}.json` share one `offshorewind_lcoe_ReEDS.csv`.
  - The per-type CSVs are used as membership lists. The script stops unless they partition the shared file exactly
    (every CPA_ID once), and unless every JSON that uses the shared file is mapped.
  - Each JSON is pointed at a new file holding the shared, patched rows of its CPAs. The per-type CSVs predate
    `patch_pg_resource_groups.py`, so they lack its MD/RI zone moves (p123/p133).
  - The old one-`--json` mode is kept, but it can't handle this layout.
- **Missing-metadata check** (`offshore_groups.check_references`): every JSON under the output folder must reference
  files that exist. PowerGenome loads every `**/*.json`. The check runs at the end of the split (stop) and in the
  build's `check_folder` (stop), so a broken group can't reach a build.
- **The build's offshore check** compares each zone's limits with the true resource of the groups the case uses (from
  the `_fixed_1` / `_fixed_0` / `_floating_0` technology names).
- **The cluster-cache guard and the PowerGenome commit check** (`s0_workflow/build_guard.py`): steps 4 and 6.


Run once in the v3.2 worktree (`tom/s0-v3.2` at the build commit). Steps 1–5 take minutes. Commands are for the VM's
Windows prompt (cmd). In PowerShell use `$env:PYTHONPATH="..."` in place of `set PYTHONPATH=...`.

**0. Submodule at the pin.**
```bat
git submodule update --init PowerGenome
git -C PowerGenome rev-parse HEAD
```
Expect `c4c433cfefd0ae11f5b3f47ae05e3b9d7c90a691`.

**1. Survey the offshore groups (read only).**
```bat
"<ic-pipeline python>" s0_workflow/scripts/split_offshore_groups.py --survey --groups-dir pg_data/resource_groups/ReEDS-cpas-patched
```
Expect four JSONs, `offshorewind_{fixed,floating}_{false,true}.json`, each with its own `turbine_type` and `pref_site`
and all with `metadata: offshorewind_lcoe_ReEDS.csv`. The listing also shows the `offshorewind*.csv` files: the shared
file has 39,005 rows, and the four per-type files have 13,108, 4,215, 20,054 and 1,628 rows. Pair each JSON with the
per-type file of the same turbine type and pref site, by file name. If a name doesn't say, open the file's first rows.

**2. Split.**
- **What it does:** each JSON is pointed at a new metadata file holding the shared, patched rows of the CPAs in its
  per-type file.
- **Why not the per-type CSVs as they are:** they predate `patch_pg_resource_groups.py`, so they lack its MD/RI zone
  moves (p123/p133) and lat/lon.
- **Refusals:** the script stops unless the four per-type files partition the shared file exactly (every CPA_ID once),
  every JSON that uses the shared file is mapped, and every JSON in the output folder references files that exist.
- **Not changed:** the source folder. Every other file is hard-linked.
```bat
"<ic-pipeline python>" s0_workflow/scripts/split_offshore_groups.py ^
    --groups-dir pg_data/resource_groups/ReEDS-cpas-patched ^
    --out-dir pg_data/resource_groups/ReEDS-cpas-patched-offshore-split ^
    --map offshorewind_fixed_true.json=<fixed/true per-type csv> ^
    --map offshorewind_fixed_false.json=<fixed/false per-type csv> ^
    --map offshorewind_floating_false.json=<floating/false per-type csv> ^
    --map offshorewind_floating_true.json=<floating/true per-type csv>
```
- **Column names:** `--id-col CPA_ID`, `--region-col ipm_region`, `--mw-col mw` and `--lcoe-col lcoe` are the
  defaults. A missing column stops the run and lists the columns there are.
- **Folder name:** the output folder is the one `s0_production.offshore_filter_fix.resource_groups_dir` names.

**3. Check (report these):**
- **The log line:** four groups with CPAs and GW at LCOE ≤ 125 each; "whole 2,985.5 GW in 36 zones" (the dry check's
  numbers); "partition exact (39,005 CPAs)"; "no broken references".
- **`offshore_split_manifest.json`:** `zone_differs_from_shared` per group. It should be non-zero only where the patch
  moved CPAs to p123/p133. Report the counts.
- **References, again:**
  ```bat
  "<ic-pipeline python>" -c "import sys; sys.path.insert(0,'.'); from s0_workflow import offshore_groups as o; print(o.check_references('pg_data/resource_groups/ReEDS-cpas-patched-offshore-split'))"
  ```
  This must print `[]`. The build runs the same check (`check_folder`) and stops on any missing file.
- **FOR TOM — floating preferred sites:** `resources.yml` has cluster entries for fixed/1, fixed/0 and floating/0
  only. The floating/true group (1,628 CPAs) is in no entry, so after the split those sites are not offered at all. In
  v3.1 they were inside every group. Report the group's GW at LCOE ≤ 125 from the log. Adding a floating/1 entry to
  `resources.yml` is Tom's call; it isn't made here.

**4. Cluster cache (`pg/extra_inputs/cluster_assignments`).**
- **Does the key change on its own?** Not known from this repo. The cache folders are named by a hash this repo can't
  check against the data, and the repo README's rule is to clear the cache whenever resource groups change. So treat
  the answer as no.
- **The rule:** no need to identify the offshore folders. Move the whole folder aside before the first v3.2 build in
  this worktree, and keep it for flags-off builds:
  ```bat
  move pg\extra_inputs\cluster_assignments pg\extra_inputs\cluster_assignments.v31
  ```
- **What the build enforces** (`s0_workflow/build_guard.py`):
  - A v3.2 build (PV repair or offshore split on) needs an empty or missing cache folder the first time. It writes
    `S0_V32_CACHE.json` there: a fingerprint of the PV file's sha256 and the split manifest.
  - Later v3.2 builds need the same fingerprint.
  - A flags-off build refuses a cache with that marker.
  - Any mismatch stops the build before PowerGenome clusters, with the folder named.
  - Note: the PV repair changes the solar clusters' profiles too, so the move is needed for the PV repair as well as
    the offshore split.
- **One build command, one kind of case:** don't build v3.2 cases and flags-off cases (e.g. the regression row) in
  the same `pg_to_switch.py` call. They would share the cache, and the guard stops the second kind.
- **Switching between kinds of build in one worktree:** rename the folders, e.g. `cluster_assignments` →
  `cluster_assignments.v32` and `cluster_assignments.v31` → `cluster_assignments`. A worktree used only for v3.2
  builds keeps its own cache.

**5. PV folder:** `pg_data/profiles_pvfix` in place (CHANGES §132; the build checks the manifest, method and full
sha256).

**6. Import PowerGenome from the submodule (V32-04).**
- **Why:** the build environment otherwise imports the
  "Q3 Federal Policy analysis\SWITCH-USA-EDF\PowerGenome" checkout. Put the worktree's submodule first on the path
  for every v3.2 build command (`pg_to_switch.py` and anything else that imports PowerGenome):
  ```bat
  set PYTHONPATH=<worktree>\PowerGenome
  "<switch-pg-reeds-fedpol python>" -c "import powergenome; print(powergenome.__file__)"
  ```
  The import must print `<worktree>\PowerGenome\powergenome\__init__.py`. `PYTHONPATH` comes before site-packages
  and before the environment's editable-install entry, so it wins whichever way the Q3 checkout is installed.
- **Proof in the build:**
  - The log has a line `PowerGenome imported from <path> (commit <sha>; repo submodule: True; AEO_2026_regional_cost_corrections.csv present: True)`.
  - Each case folder has `powergenome_provenance.json`: path, commit, uncommitted changes, and the cost-multiplier
    file's presence and sha256.
  - `s0_production.powergenome_commit` is c4c433c… in v3.2, so the build stops at once if any other PowerGenome is
    imported. The flags-off patch sets it to `~` (record only).
