# PV capacity-factor basis checks (11 Oct 2026)

These are follow-ups to `README_zeroed_pv_profiles.md`. Sources: `solar_rev_profiles_20240801.parquet` (weather years 2007–2013), `solar_site_mapping_20240801.parquet`, `ReEDS-cpas-patched/solar_lcoe_ReEDS.csv`, and the S0 v3.1 inputs (s0v31_tt; stages 2028, 2030 and 2035). The analysis was read-only.

**Alignment:** the model's hour h of sampled day n (timeseries `<period>_p<n>`) is profile row (n − 1)·24 + h + 5. The profiles are in UTC and the model's hours are UTC−5. With this offset, the capacity-weighted average of the rebuilt cluster's site profiles reproduces the model's `variable_capacity_factors.csv` exactly. The largest mean absolute difference is 0.00015, and the correlation is 1.0 for every PJM cluster and stage. Stress days (`_prm`, weight 0) are left out of the sampled-day CFs.

**Files:**
- `pv_site_years_p1_p5.csv`: one row per PV profile site and weather year, for every site that a p1 or p5 project area uses.
  - CF for the year, against the site's mean over years with data.
  - `below_50pct` flags a year below half of that mean; `whole_year_zero` marks a year with no output at all.
  - `days_without_output` counts days with no output, to catch gaps shorter than a year.
- `p1_block0_vs_neighbours.csv`: the 91 p1 block-0 sites with eligible MW, which have data only for 2012–2013.
  - Their 2012 and 2013 CFs, against their supply-curve CF.
  - The same for their 5 neighbours: the unaffected p1 sites most correlated with them hour by hour in 2012–2013.
  - Neighbours' 2007–2011 CF, which estimates what the missing years would have been.
- `pjm_cf_basis.csv`: PV in p122, p124 and p125, which have no zeroed sites, per cluster and per zone (MW-weighted).
  - Supply-curve CF.
  - Full-record profile CF: all hours 2007–2013, MW-weighted over the eligible sites.
  - CF on the model's sampled days, weighted by each timeseries' scale: once rebuilt from the profiles and once from the model's own capacity factors.
