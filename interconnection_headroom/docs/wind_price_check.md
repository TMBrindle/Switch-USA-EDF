# Wind's interconnection charge against the data

**Date:** 2026-10-10. This is analysis only: no defaults change (CHANGES §99).

**Inputs:**
- The S0 v3.1 headroom inputs, tag `s0-v3.1`. The `interconnection_headroom/` code and config are the same at the tag and on `tom/s0-v3.1-credits`.
- A fresh pipeline run: `python -m icsc.cli run --start-year 2026 --scenarios atts_s0`. It gives n_estimation 2,787, R² 0.341 and BA match 0.990.

**Script:**
```
python scripts/wind_price_check.py --outputs <run outputs>
```
It writes `wind_price_observed.csv`, `wind_price_model.csv` and `wind_price_refits.csv`.

## The question

Switch prices every headroom step at the reference solar curve (`reference_tech: solar`, completed, 150 MW, NRIS). A MW of wind uses 0.25 MW of room (`saturation.tech_weights`), so new wind pays 0.25 × the step price per MW of nameplate. That is 0.25× solar.

The cost fit's wind effect is +0.357 at the tag (×1.43, s.e. 0.165, 95% CI ×1.03–1.97). The +0.41 quoted in the request is not what the s0-v3.1 config gives. The fit therefore says a wind project costs about 1.4× a solar project per kW at the same fullness. The model and the fit differ by a factor of about 5.7.

## 1. Observed, modelled and fitted $/kW, by regime

**Definitions:**
- **Observed:** LBNL network-upgrade cost, real $/kW per kW of project nameplate, from the estimation sample.
- **Cell format:** median (p25–p75); mean; n.
- **Ratios:** shown only where both technologies have n ≥ 5.
- **Mean column:** the fit (PPML) models the mean, not the median.
- **Non-ISO pool:** PacifiCorp, BPA, FRCC, DEP and DEC.

### a. Completed projects

All queue years:

| Regime | Wind | Solar | Wind/solar median | Wind/solar mean |
|---|---|---|---|---|
| PJM | 11.3 (0.7–30.9); 23.8; n 59 | 26.8 (6.3–93.2); 67.1; n 158 | 0.42 | 0.36 |
| MISO | 5.5 (0.2–61.9); 72.5; n 147 | 5.5 (0.0–28.1); 35.1; n 106 | 1.00 | 2.06 |
| SPP | 0.0 (0.0–2.8); 13.3; n 185 | 6.0 (0.0–41.3); 37.5; n 30 | 0.00 | 0.36 |
| NYISO | 38.8 (2.5–88.6); 52.6; n 18 | 59.7 (29.9–202.2); 134.8; n 3 | – | – |
| ISO-NE | 0.0 (0.0–30.0); 68.0; n 5 | 7.4; n 1 | – | – |
| non-ISO pool | 11.4 (1.3–19.4); 22.6; n 23 | 25.3 (0.7–86.0); 91.6; n 74 | 0.45 | 0.25 |

Queue year 2018 or later:

| Regime | Wind | Solar | Wind/solar median | Wind/solar mean |
|---|---|---|---|---|
| PJM | – (n 0) | 19.9 (3.8–84.4); 78.7; n 69 | – | – |
| MISO | 2.4 (0.0–36.6); 19.4; n 9 | 6.9 (0.0–18.7); 18.2; n 42 | 0.35 | 1.07 |
| SPP, NYISO, ISO-NE | – (n 0) | – (n 0) | – | – |
| non-ISO pool | – (n 0) | 82.8 (44.5–309.7); 169.7; n 17 | – | – |

Pooled wind/solar mean ratio, completed: 0.61 for all years and 0.27 for 2018+ (MISO only for wind).

### b. Active projects

All queue years:

| Regime | Wind | Solar | Wind/solar median | Wind/solar mean |
|---|---|---|---|---|
| PJM | 122.1 (50.9–174.2); 185.7; n 15 | 77.1 (20.9–177.1); 196.9; n 353 | 1.58 | 0.94 |
| MISO | 15.6 (1.8–70.2); 78.3; n 36 | 49.6 (6.9–105.8); 100.8; n 150 | 0.31 | 0.78 |
| SPP | 20.4 (0.0–41.0); 91.3; n 70 | 20.8 (0.0–58.2); 46.6; n 108 | 0.98 | 1.96 |
| NYISO | 26.4 (7.9–66.5); 43.1; n 20 | 35.8 (20.3–67.2); 60.7; n 80 | 0.74 | 0.71 |
| ISO-NE | 0.0; n 1 | 21.7 (0.0–215.1); 235.2; n 16 | – | – |
| non-ISO pool | 24.2 (0.8–158.6); 174.8; n 43 | 170.0 (32.3–337.6); 291.9; n 316 | 0.14 | 0.60 |

Queue year 2018 or later:

| Regime | Wind | Solar | Wind/solar median | Wind/solar mean |
|---|---|---|---|---|
| PJM | 122.1 (50.9–174.2); 185.7; n 15 | 83.9 (22.3–193.6); 209.4; n 325 | 1.45 | 0.89 |
| MISO | 23.4 (9.0–79.2); 97.3; n 27 | 52.6 (8.5–108.8); 105.0; n 144 | 0.44 | 0.93 |
| SPP | 29.3 (0.0–122.0); 166.8; n 33 | 30.2 (1.7–78.7); 56.1; n 84 | 0.97 | 2.97 |
| NYISO | 54.8 (29.4–86.6); 59.1; n 3 | 35.2 (20.5–55.5); 52.9; n 49 | – | – |
| ISO-NE | 0.0; n 1 | 13.2 (0.0–176.2); 247.0; n 13 | – | – |
| non-ISO pool | 10.0 (0.0–268.2); 224.4; n 31 | 199.9 (37.4–374.1); 317.7; n 284 | 0.05 | 0.71 |

Pooled wind/solar mean ratio, active: 0.59 for all years and 0.80 for 2018+.

### c–d. The model's charge and the fit's prediction at 2026 start fullness

**Method:**
- **Model columns:** the zone's first step price × the tech weight (solar 1.0, wind 0.25), in $/kW of nameplate.
- **Fit columns:** the fit's predicted $/kW for a completed 150 MW NRIS project, using the wind effect, at the zone's start fullness and trend year, with the regime's own completed effect.
- **Averaging:** zone values are averaged within each regime, weighted by base capacity.

| Regime | Zones | Start fullness | Model solar | Model wind | Fit solar | Fit wind |
|---|---|---|---|---|---|---|
| PJM | 22 | 0.164 | 99.0 | 24.7 | 89.7 | 128.1 |
| MISO | 32 | 0.171 | 121.7 | 30.4 | 119.5 | 170.8 |
| SPP | 17 | 0.204 | 42.0 | 10.5 | 43.5 | 62.2 |
| NYISO | 2 | 0.506 | 149.5 | 37.4 | 287.7 | 411.1 |
| ISO-NE | 6 | 0.358 | 181.6 | 45.4 | 192.1 | 274.6 |
| non-ISO pool | 14 | 0.277 | 145.9 | 36.5 | 146.3 | 209.1 |

The model's solar step and the fit's solar prediction differ in NYISO and PJM. The curves cap the status effect at 0.5–2× the pooled one (`status_pricing: capped`):
- **NYISO:** the own completed effect of +1.283 is priced at +0.619, which is ×0.51.
- **PJM:** −0.856 is priced at −0.767, which is ×1.09.

Everywhere else the two agree to within the step's width.

## 2. Ratios: wind/solar per kW of nameplate

| Regime | Observed completed, all years (median / mean) | Observed active 2018+ (median / mean) | Model charge | Fit prediction |
|---|---|---|---|---|
| PJM | 0.42 / 0.36 | 1.45 / 0.89 | 0.25 | 1.43 |
| MISO | 1.00 / 2.06 | 0.44 / 0.93 | 0.25 | 1.43 |
| SPP | 0.00 / 0.36 | 0.97 / 2.97 | 0.25 | 1.43 |
| NYISO | – (solar n 3) | – (wind n 3) | 0.25 | 1.43 |
| ISO-NE | – (solar n 1) | – (wind n 1) | 0.25 | 1.43 |
| non-ISO pool | 0.45 / 0.25 | 0.05 / 0.71 | 0.25 | 1.43 |
| Pooled (mean) | 0.61 | 0.80 | 0.25 | 1.43 |

The observed ratios are raw and are not adjusted for fullness, size or vintage. The fit's ratio is exp(0.357), the same in every regime.

## 3. Refits

The wind effect is shown relative to solar. "As fitted" is the s0-v3.1 fit.

| Fit | n | Wind term | Coefficient (s.e.) | × solar | 95% CI |
|---|---|---|---|---|---|
| As fitted | 2,787 | wind | +0.357 (0.165) | 1.43 | 1.03–1.97 |
| Split by status | 2,787 | wind × completed | +0.689 (0.215) | 1.99 | 1.31–3.04 |
| | | wind × active | +0.249 (0.209) | 1.28 | 0.85–1.93 |
| SPP + MISO + PJM | 1,892 | wind | +0.392 (0.169) | 1.48 | 1.06–2.06 |
| SPP + MISO + PJM, split | 1,892 | wind × completed | +0.550 (0.222) | 1.73 | 1.12–2.68 |
| | | wind × active | +0.318 (0.202) | 1.37 | 0.92–2.04 |

The sample has 437 completed and 185 active wind projects. 391 completed and 121 active of them are in SPP, MISO and PJM.

**The premium comes mainly from completed projects:**
- Completed wind is ×2.0 (×1.7 in the wind regimes).
- Active wind is ×1.3–1.4, and its interval includes 1.
- Restricting the fit to the wind regimes barely moves the pooled effect (+0.39 against +0.36).

**The completed-wind premium is not credible as a current price.** Completed wind is a different vintage from completed solar:

| | n | Mean queue year (range) | Mean fullness |
|---|---|---|---|
| Completed wind | 437 | 2011.2 (2001–2018) | 0.033 |
| Completed solar | 372 | 2016.7 (2009–2023) | 0.111 |
| Active wind | 185 | 2018.2 (2008–2023) | 0.089 |
| Active solar | 1,023 | 2019.8 (2012–2023) | 0.163 |

- The raw completed ratios are below 1 almost everywhere (pooled mean 0.61).
- The fit turns that into ×2.0 only after removing the trend (+0.115 a year, about ×0.53 over the 5.5-year gap) and the fullness spline near zero.
- Most completed wind (2001–2008) predates any completed solar in the sample. Its premium is therefore an extrapolation of the trend term, not a like-for-like comparison.
- The active projects overlap in vintage and fullness. They are the better guide to what a new wind project pays.

## 4. Recommendation: option (ii), a wind price factor of 1.0 (range 0.8–1.3)

Wind should pay about solar's step price per MW of nameplate (factor 1.0). The 0.25 room weight stays as it is. The current charge is 0.25.

**Why 1.0:**
- **The current charge understates wind by about 3–5×.**
  - The best like-for-like estimate, the active-only fit, gives ×1.28 (CI 0.85–1.93), or ×1.37 in the wind regimes.
  - Recent active projects in the two regimes with real wind samples give mean ratios of 0.89 (PJM) and 0.93 (MISO).
  - The pooled active 2018+ ratio is 0.80.
  - Every active mean ratio with at least 15 wind projects is between 0.60 (non-ISO, all years) and 2.97 (SPP 2018+), well above 0.25.
  - The medians scatter more widely (0.05–1.58) because many wind projects report about zero.
- **The fit's ×1.43 overstates wind.** It is pulled up by the old completed wind (×2.0).
- **1.0 sits inside every credible range:**
  - the active refit CIs (0.85–1.93 and 0.92–2.04);
  - between the raw recent ratios (about 0.8–0.93) and the vintage- and fullness-adjusted active estimate (1.28–1.37).
- **Sensitivities:** 0.8 (raw active ratio) and 1.3 (active refit).

**What it changes:** at 2026 start fullness, wind's first-step charge rises to the solar column of table c:
- PJM: from 24.7 to 99.0 $/kW
- MISO: from 30.4 to 121.7 $/kW
- SPP: from 10.5 to 42.0 $/kW
- NYISO: from 37.4 to 149.5 $/kW
- ISO-NE: from 45.4 to 181.6 $/kW
- non-ISO pool: from 36.5 to 145.9 $/kW

These are increases of $31–136/kW. Wind still uses 0.25 MW of room, so it fills headroom at a quarter of solar's rate.

**Why not (i), keep the current charge:** it prices wind at 0.25× solar. Every active mean ratio with at least 15 wind projects is 0.60 or more. The refits put wind at or above solar. Only the zero-heavy medians in MISO and the non-ISO pool (0.31–0.44 and 0.05–0.14) come near 0.25, and the PPML fit models means, not medians.

**Why not (iii), recalibrate the reference level:**
- The solar level is consistent with the data. For example, PJM's first step is 99 $/kW against an active 2018+ solar median of 84 and mean of 209, and SPP's is 42 against 30 and 56.
- Moving the level would move solar, storage and wind together. It would leave wind at 0.25× solar, which is the actual discrepancy.

**Implementation (not done here; analysis only):**
- **Faithful form:** wind takes its own share of each step. It uses 0.25 MW of room per MW and is charged factor × step price per MW (a wind-specific step variable in `interconnection_headroom.py`). The steps stay non-decreasing, so wind still fills them in order.
- **Simpler approximation:** a per-MW adder of (factor − 0.25) × the zone's current step price on new wind. With the first step's price, this is exact while the zone is in its first step and understates wind later.
- **Either way:** the factor belongs in `config.yaml` next to `tech_weights`, and is written to the case by `switch_case.py`. It stays separate from the room weight, which `fit-weights` estimates from queue behaviour, not cost.

## Does the answer differ by region?

No. Use one national factor.

- **Sample size:** only PJM (15), MISO (27), SPP (33) and the non-ISO pool (31) have more than a handful of active 2018+ wind projects. NYISO has 3 and ISO-NE has 1.
- **SPP's high mean ratio (2.97) comes from its tail.** The median ratio is 0.97, and wind's p75 is 122 against solar's 79.
- **The non-ISO pool is lower** (median 0.05, mean 0.71). It mixes five utilities with different cost-allocation rules, and its wind n is 31.
- **No region's interval clearly excludes 1.0.** A regional factor would mostly fit noise in thin samples.
- **To revisit:** if a later LBNL release adds active wind in SPP, the SPP-only active refit is the first check, because SPP is where most new wind would be built.

## Caveats

- **Active costs are study estimates**, not settled costs. They also include projects that will withdraw, which are likely the expensive ones.
- **Cost allocation differs by regime and era.** For example, SPP's and MISO's participant funding changed over the sample, and network-upgrade costs are assigned differently.
- **Costs are per kW of nameplate**, not per kW of accredited or delivered capacity. That basis matches the Switch charge.
- **The active wind sample is small** (185, of which 121 are in SPP, MISO and PJM). The factor should be treated as a central value with the 0.8–1.3 sensitivity, not as an estimate to two digits.
- **The fit with the status split** was run only to diagnose the wind effect. The production fit is unchanged.
