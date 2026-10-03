# Coal specification: producing scripts (reference only)

These are the EDF Power Data Compiler's scripts that produced the validation tables in `..` (see `../coal_spec.md` §5). They are kept for provenance only: they are not part of the S0 workflow, nothing imports them, and they are byte-identical to the versions that wrote the committed tables (rev. 2, 2026-10-03).

## Run order and environment

Run from the Switch repo root in the Data Compiler's `us-power-data` conda env, with default arguments:

1. `coalcap_adjudicate.py`: builds the cap unit sets `coal_cap_units_all.csv` and `coal_cap_units_adjudicated.csv`, and the zonal cap adjudication against the cloud table.
2. `coal_holdopen.py`: builds the hold tables `coal_spec_hold_online.csv` and `_monthly.csv` (§3).
3. `coal_spec_build.py`: builds the overrides, converted gas units, expected caps (2035 and by stage) and `coal_spec_hold_by_stage.csv` (§1.5, §2, §4). It also adds `plant` and `zone_final` columns to `coal_cap_units_all.csv` in place (`zone_final` is the zone from PowerGenome's plant map, falling back to the county-based zone).

The steps depend on each other in both directions:
* `coal_holdopen.py` reads `coal_spec_overrides.csv` and `coal_spec_model_units_2035.csv` from an earlier `coal_spec_build.py` run.
* `coal_spec_build.py` reads the hold table that `coal_holdopen.py` writes.

On a clean start, run `coal_spec_build.py` once before step 2, then run the three steps in the order above.

## Paths and inputs that are not in this repo

Running the scripts as written needs the VM layout:
* **Outputs:** the scripts write to `switch/out_ictest/2035/report_tables/emissions/`, relative to the repo root. In this repo the committed copies live in `s0_workflow/specs/coal/`.
* **Raw EIA files:** EIA-860 / EIA-923 2021–24, EIA-923 2025 final and 2026 year-to-date, and EIA-860M August 2026. They are downloaded from eia.gov (URLs in the scripts and in `coal_spec.md` §1) into a sibling folder, `../ic_test_fedpol_work/coalcap_cache/`. This cache is not committed.
* **Session tables (VM only, not committed):** `b8_units.csv`, `b8_coal_cluster_caps.csv` and `coal_caps_cloud_vs_b8.csv`, built from PUDL in the VM test session.
* **PowerGenome data from the repo's gitignored areas:** `pg_data/pudl.2025_08.sqlite`, and PowerGenome's cached `july_generator2025.xlsx` (`PowerGenome/data/eia/860m/`).
* **Absolute path:** `coalcap_adjudicate.py` reads the Data Compiler's PUDL parquet cache from `PDC = D:\EDF Power Data Compiler` (line 21). The extra checks it supports need that machine; edit `PDC` to run elsewhere.
* **Cloud table:** `coalcap_adjudicate.py` reads the cloud's `s0_workflow/coal_cf.py` and its cap table with `git show origin/tom/s0-prod-scripts:...` (no checkout).

All data used are public (EIA-860, EIA-860M, EIA-923, PUDL). No IPM content.
