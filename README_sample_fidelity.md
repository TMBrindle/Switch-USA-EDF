# Sample fidelity and time-zone checks, S0 v3.1 (11 Oct 2026)

These follow `README_cf_basis_checks.md`. They cover S0 v3.1 (S0prod_A, model inputs `s0v31_tt`) at every stage, 2028, 2030, 2035, 2040 and 2045. The analysis was read-only.

## Method
- **Model weights:** each sampled day carries the model's own weight, `ts_scale_to_period × ts_duration_of_tp`. Stress days (`_prm`) have weight 0.
- **Full record:** all 61,320 hours (weather years 2007–2013, 7 × 8,760).
- **Wind and solar clusters:** the profiles and MW are PowerGenome's own, taken from its cluster cache for the s0v31 resource groups (`pg/extra_inputs/cluster_assignments/38aa2729417c51b9`).
  - Every group in the cache is named `..._UTC-5`. The profiles are already in model time.
  - Wind is multiplied by the S0 wind-loss factor 0.88098 (`s0_production.yml`). This does not change any ratio.
  - Each cluster reproduces exactly one model project's `variable_capacity_factors.csv`: 674 clusters in every stage, with a mean absolute difference of 0.
  - The PV profiles are as modelled, so the zeroed site-years are included (see `README_zeroed_pv_profiles.md`).
- **Load:**
  - **Rebuilt:** each zone's full-record hourly load is rebuilt from the model's sources. These are the ReEDS 2023 base load (`reeds_2023_loads.csv.zip`), plus `load_growth` for the stage, plus `us_exports` (`load_adjustments_edf_epri_med.csv.zip`).
  - **Check:** the rebuild matches the model's `loads.csv` to within 1 MW (integer rounding) at every sampled hour, in all zones and stages.
- **Thresholds:** the "outside" flag means a ratio of sampled-day CF (or load) to full-record CF (or load) outside 0.95–1.05. Regions are ReEDS `transreg`.

## Files
- `sample_fidelity_vre.csv`: one row per zone × technology × stage × cluster, plus a `zone` row that is MW-weighted over the clusters.
  - CF over the full record, and CF on the sampled days (rebuilt, and from the model's own capacity factors).
  - The ratio, the flag and the match error.
- `sample_fidelity_load.csv`: one row per zone × stage.
  - Mean load over the full record and on the sampled days (rebuilt, and from `loads.csv`).
  - The ratio and the flag; the full-record peak and the peak on the sampled days; the rebuild error.
- `sample_fidelity_summary.csv`: one row per region (plus ALL) × stage × series.
  - The weighted mean ratio, by MW for wind and solar and by MWh for load.
  - The minimum and maximum ratio, with the zones they occur in.
  - The number of zones outside 0.95–1.05.
- `tz_check_by_zone.csv`: per zone, in model hours (UTC−5) and in local standard time (by the zone's state).
  - Load peak hours (annual, summer, winter) and the trough hour, from the full-record base load and from the model's `loads.csv` on sampled days.
  - The PV output centroid hour.

## Findings (zone level)
| Series | Zone-stages | Outside 0.95–1.05 | Share of MW outside | Median \|ratio − 1\| | Range |
|---|---|---|---|---|---|
| Load (control) | 670 | 1 (p42 2028: 0.933) | – | 0.010 | 0.933–1.041 |
| Utility PV | 670 | 110 | 16% | 0.027 | 0.81–1.12 |
| Onshore wind | 660 | 422 | 63% | 0.070 | 0.66–1.28 |
| Offshore wind (fixed) | 180 | 127 | 65% | 0.104 | 0.64–1.40 |

- **National ratios are near 1:** MW-weighted over all zones they are 0.99–1.01 for PV, onshore wind and load in every stage, and 0.95–1.00 for offshore wind. The errors largely cancel across zones.
  - Regional and stage detail is in `sample_fidelity_summary.csv`.
  - PJM PV in 2030–2045 and FRCC and PJM offshore wind run consistently low. NYISO and MISO offshore wind run consistently high.
- **PJM PV:** the p122, p124 and p125 values reproduce `pjm_cf_basis.csv` to four decimals, which is an independent rebuild from the site profiles.

**Duplicate offshore projects (side finding, not investigated further):** in all 36 zones with offshore wind, the "fixed pref-site 0" and "fixed pref-site 1" groups have identical CPA sets, MW and profiles.
- Each holds every eligible offshore CPA, floating included (in p1, 65.8 of 68.3 GW is floating).
- The model therefore carries two identical offshore projects per cluster, each with the full capacity limit. The turbine-type and pref-site filters in `resources.yml` are not being applied.
- The CSVs keep both groups; their rows are identical.

## Time zone
- **Design:** loads and profiles are on the same basis by design. PowerGenome shifts all stored profiles from UTC to `utc_offset: −5` (the cache names say `UTC-5`). `make_study_loads.py` rolls the ReEDS load by the same −5 hours before writing the user load file, which PowerGenome then applies unshifted. The load growth is derived from that rolled load, and the exports are averaged by hour in model time.
- **Assumption:** this is right only if the ReEDS load table in PG_DB is in UTC.
- **Data test:** the data support that assumption. Every time zone shows the same local pattern:

  | Zones (local standard time) | Annual load peak | Winter peak | Summer peak | Overnight trough | PV centroid |
  |---|---|---|---|---|---|
  | Pacific (p1, p10, p11, …) | 17–19 h | 18–19 h | 16–17 h | 02–03 h | 11.5–12.6 h |
  | Mountain | 18–19 h | 18–19 h | 14–18 h | 02–03 h | 12.3–12.6 h |
  | Central | 14–18 h | 18–19 h* | 13–16 h | 01–03 h | 11.9–13.5 h |
  | Eastern (p122, p124, p125, p127, …) | 15–19 h | 17–19 h* | 13–16 h | 01–03 h | 12.1–13.9 h |

  \* A few zones have a winter morning peak (06–08 h).
- **What this rules out:** treating local-time or Central-time load as UTC, or missing the shift, would move the western zones 2–8 hours against the eastern ones. That is not seen.
- **Limit of the test:** it cannot resolve an offset of an hour or so, for example an hour-ending versus hour-beginning convention. Summer peaks at 14–16 h local standard (15–17 h daylight time) are within normal range but on the early side.
- **Edge effect:** `make_study_loads.py` rolls the load within each weather year, so the last 5 hours of each year wrap back to that year's first hours. How PowerGenome treats the year boundary for profiles was not checked. Either way, this affects at most 5 hours a year, at year end.
