# Flags-off build 2 (F2): v3.2 code, flags off, PowerGenome from the submodule (c4c433c)

**Purpose:** isolate the PowerGenome pin bump (V32-04, CHANGES §128) on the v3.1 → v3.2 bridge.
- F1 is the first flags-off build: v3.2 code, flags off, PowerGenome as v3.1 (the Q3 checkout).
- F2 is F1 with the submodule imported instead. Nothing else differs.

**Commit:** the hash in `s0_workflow/runcards/v32/READY_S0_V32`, the same as F1's.

## Steps

1. **Scratch worktree, submodule, patch:**
   ```bat
   git worktree add <scratch> <commit from READY_S0_V32>
   cd <scratch>
   git submodule update --init PowerGenome
   git -C PowerGenome rev-parse HEAD
   git apply s0_workflow/data/v32/v32_flags_off.patch
   ```
   - The submodule must be at `c4c433cfefd0ae11f5b3f47ae05e3b9d7c90a691`.
   - The patch makes 16 changes (§245–§247 added 7) and sets every v3.2 flag back to v3.1, including `powergenome_commit: ~` (record
     only). Don't commit it.
2. **Cluster cache:** use the same v3.1 cache as F1, with no `S0_V32_CACHE.json` marker. The flags-off build refuses a
   cache that has the marker (recipe L step 4).
3. **Import the submodule and build:**
   ```bat
   set PYTHONPATH=<scratch>\PowerGenome
   "<switch-pg-reeds-fedpol python>" -c "import powergenome; print(powergenome.__file__)"
   "<switch-pg-reeds-fedpol python>" pg_to_switch.py pg/settings switch/in/s0prod_A_F2 --case-id S0prod_A ^
       --year 2028 --year 2030 --year 2035 --year 2040 --year 2045
   ```
   The import must print `<scratch>\PowerGenome\powergenome\__init__.py`.
4. **Proof of the import:**
   - The log line is `PowerGenome imported from <scratch>\PowerGenome (commit c4c433c...; repo submodule: True;
     AEO_2026_regional_cost_corrections.csv present: True)`.
   - Every case folder has `powergenome_provenance.json` with that commit.
   - F1's provenance file shows the Q3 checkout's commit.
5. **Compare F2's inputs with F1's,** stage by stage. Use the input comparison of
   `s0_workflow/scripts/reuse_chain_stages.py`, or a recursive diff of `switch/in/s0prod_A_F1` against
   `switch/in/s0prod_A_F2`.

## Expected input differences

- **None.** c4c433c is 5ecb454 plus one data file, `data/cost_multipliers/AEO_2026_regional_cost_corrections.csv`
  (checked against the remote). If the Q3 checkout is 5ecb454 with the same CSV, F2 equals F1 byte for byte, apart
  from `powergenome_provenance.json`.
- **Where any difference would come from:** compare the two provenance files (`commit`, `dirty`,
  `cost_multiplier_sha256`).
  - **Different CSV sha256:** the regional capex multipliers differ, so expect differences in `gen_build_costs.csv`
    (capex) and anything derived from new-build capex, in the regions where the multipliers differ.
  - **Different Q3 commit or uncommitted changes:** anything PowerGenome writes can differ.
- **Report:**
  - Q3's commit and its `dirty` flag;
  - the two CSV hashes;
  - per stage, "identical" or the first differing file;
  - the run time.

F2 doesn't block S0 v3.2. If time is short, run S0 v3.2 first and F2 afterwards, on the v3.1 cache (renamed back;
recipe L step 4).
