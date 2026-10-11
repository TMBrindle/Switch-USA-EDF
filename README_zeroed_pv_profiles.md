# Zeroed PV profile years (reV 20240801)

**Profile file:** `pg_data/profiles/solar_rev_profiles_20240801.parquet`, reV vintage 20240801. It has 68,923 site columns, labelled `0`–`68922`, and 61,320 hourly rows (weather years 2007–2013, 8,760 rows each). It is mapped to supply-curve project areas by `solar_site_mapping_20240801.parquet` (`CPA_ID` → `Site`). The metadata is `resource_groups/ReEDS-cpas-patched/solar_lcoe_ReEDS.csv`.

**Finding:** 5,390 site columns (7.8%) are all zero for whole weather years, always within 2007–2011 and never in 2012 or 2013.
- **Pattern:** they form 10 blocks of exactly **539 consecutive columns, one every 6,468 columns**, each block being columns `6468·k` to `6468·k + 538` for k = 0–9.
  - Block 0 (columns 0–538) is zero in all five years 2007–2011.
  - Blocks 1–2 are zero in 2007, 2010 and 2011.
  - Block 3 is zero in 2007, 2009, 2010 and 2011.
  - Blocks 4–9 are zero in 2010 and 2011.
- **Missing data, not mismatched sites:** in the years with data, each affected column's location signature (solar-noon hour, June day length) matches its own other years, the same as unaffected columns. The zero years are missing data, not another site's profile. Re-matching sites by ID cannot recover them; the 2007–2011 data for these blocks needs re-extracting from reV.
- **Wind is unaffected:** the onshore and offshore wind profile files (same vintage) have no zero years.

**Effect in S0 v3.1:** the model samples days from all seven weather years, so PV in affected zones runs on understated capacity factors.
- 101 of 134 zones have eligible PV on zeroed columns.
- The profile CF as modelled is below the years-with-data CF by 30% in p1 (WA), 24% in p5, 21% in p11, 18% in p14, about 12% in p26 and p79, and 1–8% in most others.

**Files:**
- `zeroed_pv_sites.csv`: one row per zeroed site column and zone.
  - `site`: the profile column, which is the `Site` value in the site mapping. The file holds no reV gid.
  - `cluster`: the PowerGenome cluster of the site's eligible project areas. Eligible means cpa_mw > 0 and site LCOE ≤ $50/MWh; each zone's eligible areas are split into two halves by capacity-weighted LCOE.
  - `eligible_mw`, `all_cpa_mw`, `n_cpa`: the eligible MW, all MW and number of project areas mapped to the site.
  - `years_zero`: the zeroed years.
  - `block`, `column_in_block`: the block index (0–9) and the column's position within its 6,468-column span.
  - `cf_years_with_data`: the column's mean CF over its non-zero years.
  - `no CPA mapped`: marks site columns that no project area uses.
- `zones_affected.csv`: per zone.
  - Eligible GW, and the share of eligible MW on zeroed columns.
  - Supply-curve CF, the profile CF as modelled (7 years) and the profile CF over years with data only.
  - The percentage by which the modelled CF is understated.
- `rev_cf_mismatch_118.csv`: the earlier CF check. Its PV ratios are partly explained by these zeros and will move once the profiles are fixed.

Analysis was read-only, on the VM, 11 Oct 2026.
