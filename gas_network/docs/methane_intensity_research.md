# Upstream methane intensity by production basin (IDEA-01, research only)

Opened 2026-10-11. No code and no model change, so no CHANGES section (§104–106 stay reserved). The data file is
`data/basin_methane_intensity.csv`. Stage 1/2a outputs are untouched. The older empty template
`data/basin_intensity.csv` is left as it is.

**How the sources were reached.** The session's proxy blocks most primary hosts (403 at the proxy or DNS failure):
ipcc.ch, epa.gov (all subdomains), federalregister.gov, regulations.gov, unfccc.int, canada.ca, boem.gov, edf.org,
iea.org, nature.com, pnas.org, pubs.acs.org, copernicus.org, doi.org and others (full list in §9). The
reachable hosts were eia.gov, github, pypi and zenodo.org. So:

- **[fetched]** values were computed here from downloaded files.
- **[snippet]** values come from search-engine summaries of the publication. They could not be opened, so they
  carry the confidence stated.
- **Two EPA datasets** (source: EPA) were read in this session from copies on Zenodo, because epa.gov is blocked
  here. The record of origin is the EPA originals held on the VM in US-Power-Sector-Data
  `data/raw/gas_network/`, with a manifest (URL, date, sha256). The VM verified both copies against EPA
  (2026-10-11):
  - **GHGRP Subpart W RY2015–2023** (EPA Envirofacts `ef_w_*` tables; session copy Zenodo record 19671411). It
    matches EPA's originals in content: all 661 table-years and all 120 natural gas segment-year totals. Two
    Zenodo file names differ from EPA's: `ef_w_comp_workovers_frc` is EPA's `EF_W_COMP_WORKOVERS_FRAC`, and
    `ef_w_dehydrators_dessicant` is EPA's `EF_W_DEHYDRATORS_DESICCANT`. Neither is used here; the values come
    from `ef_w_emissions_source_ghg` and `ef_w_facility_overview`.
  - **GHGI 1990–2022** chapter tables (EPA 430-R-24-004; session copy Zenodo record 22925436). Byte-identical to
    EPA's files.
  - **Not published by EPA:** RY2024 Subpart W, and a final GHGI 1990–2023.

## 1. Definitions and units

- **`pct_gross_gas`**: methane emitted as a percentage of gross gas production, the convention of the measurement
  literature. Studies differ in the denominator:
  - Alvarez 2018, Gautam 2026, Varon and TROPOMI use gross production;
  - MethaneSAT uses marketed gas;
  - some use the CH4 content of production;
  - GHGRP rows here are CH4 volume ÷ gas for sales.

  These differ by about 10–20%, inside every range below. The ranges are not adjusted for this.
- **`kg_ch4_per_mmbtu_delivered`** = 17.59 × L/(1−L), with L as a fraction.
  - 17.59 = 0.95 × 19.2 kg CH4/Mcf ÷ 1.037 MMBtu/Mcf.
  - 19.2 kg/Mcf is the CH4 density EPA uses at 60 °F.
  - 1,037 Btu/cf is the EIA 2024 heat content of deliveries to consumers.
  - **ASSUMPTION:** delivered gas is 95% CH4 by volume, and lease/plant fuel and NGL shrink are ignored.
  - Example: 1% ≈ 0.178 kg CH4/MMBtu.
- **Segments:**
  - Upstream: `production` (well sites), `gathering_boosting`, `processing`, and `upstream_total`. Basin-level
    measurements (aircraft, satellite) see all three together, so `upstream_total` is the primary value.
  - Path-based, national per MMBtu moved: `transmission_storage` and `distribution` (§5).
- **CSV columns:** `basin, segment, year, low, central, high, unit, source, flag, notes, path`.
  - `flag` is one of:
    - `measurement`
    - `inventory`
    - `derived` (measured total × national measured segment share)
    - `assumption`
    - `projection`
  - **`path`** is an extra column I added so the projections can sit in the same file. Its values are
    `observed`, `current_rules` and `no_further_reductions`.
  - Short source keys are expanded in §8.
- **Year:** 2024 rows are the reference estimate for 2024, built from data years 2018–2025 (stated in `notes`).
  The `inventory` rows carry their own data year (2022 or 2023).

## 2. Upstream intensity by basin, 2024 (measurement-based) and the inventory comparison

`upstream_total` covers production, G&B and processing. Basin labels follow `zone_basin_shares` (Stage 1).

| Basin | Low | Central | High | kg CH4/MMBtu (central) | Main evidence (data year) | GHGRP prod+G&B 2023 | Measured ÷ GHGRP |
|---|---|---|---|---|---|---|---|
| Appalachia | 0.3 | 0.6 | 1.0 | 0.106 | MethaneSAT 0.6% of marketed (2024–25); AMI 0.52% (0.30–0.62); TROPOMI 0.8%; MethaneAIR ~1% (2023) | 0.060 | 10× |
| Haynesville | 0.4 | 0.7 | 1.0 | 0.124 | Varon 0.6→0.4% (2022→23); TROPOMI 0.7%; MethaneAIR ~1% | 0.098 | 7× |
| Permian | 1.5 | 2.5 | 3.5 | 0.451 | MethaneSAT 2.4–2.6% (1.9–3.5); MethaneAIR 2.4% (1.5–3.2) 2023; Varon 3.2% 2023 (TX 3.6, NM 2.1); TROPOMI 2.3% | 0.242 | 10× |
| Eagle Ford (shale) | 0.9 | 1.5 | 2.4 | 0.268 | Varon 1.0% (2023); TROPOMI 0.9%; MethaneAIR ~2.0%, MethaneSAT ~2.4% (both low confidence) | 0.372 (basin 220, incl. conventional) | 4× |
| Barnett/other TX | 1.6 | 2.4 | 3.1 | 0.433 | Varon 2.5%; TROPOMI 3.1%; MethaneAIR ~1.6% | 0.587 | 4× |
| Mid-Continent/Anadarko | 1.4 | 1.7 | 2.1 | 0.304 | Varon 2.0%; TROPOMI 1.6%; MethaneAIR ~1.4% | 0.403 | 4× |
| San Juan | 1.5 | 2.2 | 2.9 | 0.396 | TROPOMI 2.2% (2019–24) only; ±30% ASSUMPTION | 0.917 | 2.4× |
| Rockies (other) | 1.0 | 3.2 | 4.7 | 0.581 | production-weighted (see below) | 0.527 | 6× |
| Bakken | 1.5 | 2.1 | 4.2 | 0.377 | MethaneAIR ~2.1% (2023, low confidence); Peischl 2016 ≥4.2% of CH4 produced (2014, before capture targets) | 0.333 | 6× |
| Fed. GOM (offshore) | 2.2 | 2.9 | 3.8 | 0.525 | Gorchov Negron 2020: 2.9% (2.2–3.8), 2018 aircraft | 0.35 (GHGRP 2023); 1.36 (GHGI 2022) | 8× / 2.1× |
| W. Canada | 0.9 | 1.2 | 1.5 | 0.214 | Conrad 2023 AB conventional ~1.75% (~1.5× NIR); Johnson 2023 BC ~0.42%; CER 2024 weights | — | ~1.5–1.9× NIR |
| E. Canada | 0.6 | 0.9 | 1.25 | 0.160 | ASSUMPTION: 50/50 mix of W. Canada and Appalachia | — | — |
| LNG imports | 1.1 | 1.3 | 1.8 | 0.232 | 2024: Trinidad 10.4 Bcf (no source; US national assumed), Norway 5.0 Bcf (~0.015%); **low confidence** | — | — |
| other | 1.6 | 1.9 | 2.6 | 0.341 | national measured: 1.9% (TROPOMI 2024), 1.6% (MethaneAIR 2023), 2.6% (Omara 2021) | 0.225 (national) | 8× |

Notes on the table:

- **Range method.** Low/high are the spread of the independent measurement studies, not each study's own
  confidence interval. Where only one study exists, a ±30% band is used (ASSUMPTION), which is the relative
  uncertainty Varon 2023 reports for the Permian.
- **Trend.** The measured series fall over time, e.g. Permian 5.2% (2019) → 3.2% (2023) in Varon 2025 and US
  2.3% → 1.9% (2019–24). The 2024 central therefore leans on the 2023–25 data.
- **Rockies (other)** is weighted by GHGRP 2023 gas for sales (WY, UT, CO excluding San Juan):
  - Denver/DJ, 1,087 Bcf: 1.08/1.2/1.3 (Sherwin 2024, 2021 data; MethaneAIR ~1.3%).
  - Piceance (429 Bcf) and Uinta (229 Bcf): 1.7/7.0/9.0. These measurements conflict:
    - TROPOMI Uinta–Piceance 1.7%;
    - MethaneAIR >7% (2023);
    - Lin 2021 Uinta 6–8%;
    - MethaneSAT Uinta ~9%.
  - Green River, Powder River, Wind River, Overthrust and Raton (808 Bcf) have no recent measurement:
    - low 0.18% (Robertson 2017);
    - central 5 × GHGRP 0.575% = 2.9% (ASSUMPTION: 5× is the median measured/GHGRP ratio of the basins that are
      measured);
    - high 10×.

  California takes 24% of its gas from this label, so this weighting matters (§7). Kern River gas is mostly from
  Wyoming (Opal/Green River). If that part were known, the CA value would move toward the lower Green River
  numbers. This is not resolved here.
- **W. Canada:**
  - Central: AB 1.75% × 0.614 + BC 0.42% × 0.386 (CER 2024 production shares; SK and other provinces at the AB
    rate).
  - Low: AB at its inventory level (1.75/1.5).
  - High: AB at Chan 2020's 1.9× NIR ratio.
  - The ECCC NIR 2026 (1990–2024) segment and province tables are blocked. [snippet] O&G CH4 is down 46% from the
    2014 peak. The EDF tabulation of NIR 2023 (1,856 kt O&G CH4) implies ~1.5% of Canadian gas production, but that
    includes oil (heavy-oil venting), so it is an upper bound for gas.
  - Chan 2024 [snippet] reports that the NIR now integrates aerial data, with the gap down to about 10%.
- **E. Canada.** Eastern Canadian production is negligible. Gas entering the US at the eastern crossings is
  - WCSB gas on the TC Mainline, plus
  - US-origin gas cycled through Dawn: Waddington 175 Bcf, Pittsburg NH 131, St. Clair 121, Grand Island 52 and
    Calais 31 Bcf in 2024, per EIA.

  The 50/50 mix is an assumption.
- **LNG imports.**
  - Upstream only. Liquefaction, shipping and regasification CH4 are not included.
  - The only chain figure found is SEA-LNG/Rystad (industry): 13.9 g CO2e/MJ well-to-tank (2024), mostly CO2, with
    no CH4 split.
  - The volumes are tiny in every zone (largest: VA 0.07%).

### Where inventory and measurement disagree

- **Basin by basin.** Measurement-based totals are **2.4× to 10×** the GHGRP Subpart W rates for the same basins.
  The gap is widest in the low-intensity, high-volume basins:
  - Appalachia 10×, Permian 10×, Haynesville 7×.
  - San Juan has the smallest gap (2.4×).
- **Why GHGRP is low:**
  - It covers only facilities above 25,000 t CO2e.
  - It uses engineering factors.
  - It misses large intermittent sources (super-emitters, malfunctioning flares, tanks).
- **National totals:**
  - GHGI 2022 natural gas exploration + production + G&B + processing is 3,748 kt, 0.50% of marketed gas
    [fetched]. Adding petroleum-system production (1,381 kt) gives 0.67%.
  - Measured national values are 1.6–2.6%.
  - So measurement is **~3–4× the GHGI** and **~8× GHGRP** (0.23%).
  - Alvarez 2018 put the 2015 gap at 1.6× (13 vs 8 Tg; production 7.6 vs 3.5 Tg).
  - Gautam 2026 says ~5× EPA for 2023 (EDF's press release says ">4×").
- **None of these are averaged.** The CSV keeps measured and inventory values in separate rows.
- **Canada:** measurement is 1.25–1.9× the NIR (Johnson 2017, MacKay 2021, Chan 2020, Conrad 2023). The gap is
  narrowing as the NIR adopts aerial data.
- **GOM:** measurement is 2.1× the GHGI offshore estimate (gas + petroleum systems) and 8× GHGRP offshore.
- **Industry-reported benchmarks are lower still**:
  - ERM/Ceres/CATF 2024: 0.36–0.63% of methane produced nationally (2022);
  - ONE Future: 0.331% (2023);
  - EOG: 0.04%.

  These figures are cited in the note but are not in the CSV.

### Segment split

- **No basin-level measured segment split exists.** The `derived` rows apply Alvarez 2018's national measured
  shares to each basin's measured total: production 69.6%, G&B 23.8%, processing 6.6% (7.6 / 2.6 / 0.72 Tg, 2015).
  Oil-heavy basins (Permian, Bakken) likely have a larger production/flaring share.
- **The GHGRP `inventory` rows give the reported production and G&B split by basin.** Examples:
  - Appalachia 0.035 / 0.025%;
  - Permian 0.139 / 0.103%.
- **Processing** is only national:
  - GHGI 2022: 541 kt (0.07%);
  - GHGRP RY2023: 95 kt.
- **Area totals can overlap transmission.** Basin-area measurements include any transmission compressor stations
  inside the area, so adding the T&S segment double-counts slightly (likely small; not quantified).

## 3. Federal GOM offshore vs onshore Gulf Coast

`fed_gom` is offshore only.
- **Measured:** 2.9% (2.2–3.8%) in 2018 [snippet]. Shallow-water facilities are ~6–7% and deep-water ~0.2%.
- **PNAS 2023** puts federal Gulf platforms at 0.60 Tg/yr (0.41–0.81), >2× inventories, driven by shallow-water
  hubs. In CO2e per MJ it gives deep 1.1 g, shallow federal 16 g and shallow state 43 g.
- **GHGRP offshore** 2023: 49,863 t CH4 on 753 Bcf handled, 0.35% [fetched].
- **EIA federal GOM gross withdrawals:** 785 Bcf (2022), 750 (2023), 685 (2024) [fetched].
- **Not modelled:** shallow-water decline may lower the average intensity over time.

Onshore Gulf Coast conventional gas is not offshore. In Stage 1 it sits under `eagle_ford` (D1/D2/D4),
`barnett_other_tx` (D3) and `other` (LA South).

## 4. Conventional vs shale under the Eagle Ford label (Stage 1 interaction)

**Do they differ enough to matter? Probably yes per well, but it is unmeasured for this region, and small per MWh.**

- **Evidence that conventional and marginal gas is much more methane-intensive:**
  - Omara 2022 (Nat. Commun. 13:2085) [snippet]: low-production well sites (≤15 boe/d, 80% of US sites, 6% of
    2019 output) emit about half of well-site CH4 (37–75%). Their loss rate is >10%, 6–12× the 1.5% all-site mean.
  - Appalachian Methane Initiative (industry-funded) [snippet]: conventional wells 18.3% vs unconventional 0.09%.
    Conventional wells produce ~2% of the gas but 63.5% of the emissions.
- **No measurement separates Gulf Coast conventional (Vicksburg, Frio, Wilcox, Lobo) from Eagle Ford.** The GHGRP
  basin 220 (Gulf Coast LA+TX) mixes both, at 0.37%. And D4's conventional output includes sizeable fields, not
  only marginal wells.
- **Conventional value (`eagle_ford_conventional`, flag `assumption`, LOW CONFIDENCE):**
  - Low 1.5% (no difference).
  - Central 3.9% (geometric midpoint, ASSUMPTION).
  - High 10% (all of it from low-production sites).
- **Materiality** (525 Bcf is 24% of the Eagle Ford label in 2024):
  - At the central value, the label's effective intensity rises 1.5% → 2.1%.
  - Per MWh: east TX +0.22 kg CH4/MWh (+8%); FL +0.05 (+2%).
- **The same issue, not quantified:**
  - `barnett_other_tx` is 36% D3 Upper Gulf Coast conventional;
  - `haynesville` includes D5/D6 conventional and tight gas (23–25% non-shale);
  - `other` is mostly LA South conventional.

## 5. Path-based segments: transmission/storage and distribution

| Segment | Low | Central | High | Basis |
|---|---|---|---|---|
| Transmission & storage (kg CH4 per MMBtu moved) | 0.0056 | 0.0376 | 0.0483 | central GHGI 2022 1,413 kt (incl. LNG terminals ~1%) ÷ 2022 dry production; low = GHGRP RY2023 reporters only (219 kt); high = central × 1.29 (Alvarez 1.8 vs GHGI 1.4 Tg) |
| as % of gas moved | | 0.21% | | |
| Distribution (kg CH4 per MMBtu, res + com deliveries) | 0.0553 | 0.0622 | — | GHGI 2022 544 kt ÷ 2022 residential + commercial deliveries (8.44 Tcf); low = GHGRP RY2023 448 kt; urban measurement studies not collected |

**On the power-plant path:**
- **Transmission and storage are on the path for essentially all power-plant gas.** Their per-MMBtu burden rises
  with haul distance and compression. Here it is a national average; Stage 2a arc flows could scale it by
  pipeline-km later (not done).
- **Distribution is mostly not on the path.** Large combined-cycle and steam plants take gas from interstate or
  intrastate transmission laterals.
- **Plants supplied through local distribution company (LDC) systems are the exceptions.** These are
  - older urban plants inside LDC territories (e.g. New York City, served by Con Edison/National Grid);
  - small peakers and CHP on LDC mains;
  - plants on utilities that run both transmission and distribution (PG&E, SoCalGas): most large plants there
    connect at backbone/transmission pressure, but some take gas off distribution lines.

  The share is not quantified here; EIA-176 and plant-level EIA-923 delivery data could identify it.
- **Post-meter emissions** (GHGI 477 kt) are excluded.

## 6. Projection to 2045

### Federal and state rule status as of 2026-10-11

All items are [snippet]: federalregister.gov and epa.gov are blocked. Dates and document numbers are as reported
in the snippets.

**NSPS OOOOb / EG OOOOc (2024 rule)**
- **Deadline extension.** An interim final rule (FR 2025-14531, 31 Jul 2025) and a final rule (FR 2025-21788, 3 Dec
  2025) extended key compliance deadlines to **22 Jan 2027**. OOOOc state plans are due **22 Jan 2027**.
- **Phase 1 reconsideration**, final 9 Apr 2026 (FR 2026-06808): associated-gas flaring flexibility (24 h → 72 h)
  and a net-heating-value change. EPA issued a flaring memo on 30 Apr 2026.
- **Phase 2 proposal**, announced 7 Oct 2026 and not yet in the FR: rescind the Super Emitter Program and change
  marginal-well standards.
- **Litigation:** Texas v. EPA is in abeyance; EDF v. Zeldin, No. 26-1152.

**Waste Emissions Charge**
- CRA disapproval of the implementing rule signed 14 Mar 2025 (P.L. 119-2).
- Part 99 removed 19 May 2025.
- OBBBA (P.L. 119-21, 4 Jul 2025) delays the charge to 2034 emissions, with payments from 2035.
- **There is no charge before 2034.**

**GHGRP Subpart W**
- The 2024 revisions (89 FR 42062) apply from RY2025; RY2025 reports are due 30 Oct 2026.
- A 16 Sep 2025 proposal would suspend Subpart W until RY2034. It is not finalized.

**States**
- **NM:**
  - OCD 98% gas capture by 31 Dec 2026;
  - NMED 20.2.50 in force;
  - new 20.2.51 methane rule petitioned 13 Aug 2026, hearing ~30 Nov 2026 (zero-emission pneumatics 2031).
- **CO:** Reg 7 intensity targets; first full OOOOc-equivalent adopted 18 Sep 2026.
- **PA:** 2022 RACT; OOOOc plan in drafting, rules to EQB 2027.
- **ND:** 91% capture target.
- **TX:** no methane rule beyond RRC Rule 32 flaring.
- **WY, UT:** limited.

**International**
- **EU 2024/1787 methane regulation:** MRV equivalence for importers from 1 Jan 2027; intensity limits from 2030
  (softened through recommendations). This bears on US export supply chains and may pull practices along in
  exporting basins.
- **Canada:** amended federal regulations final (Canada Gazette II, 31 Dec 2025), ~72% below 2012 by 2030. The
  Alberta MOU allows 75% below 2012 by 2035.

### Paths in the CSV (multipliers on the 2024 measured low/central/high)

**`no_further_reductions`:** every basin is held at its 2024 value through 2045.

**`current_rules`** (ASSUMPTIONS stated):
- **US onshore basins:**
  - Multiplier 0.9625^(year−2024) to 2035, flat after. That is 2028 0.858, 2030 0.795, 2035 0.657.
  - 0.9625/yr is the measured national trend 2019–2024 (2.3% → 1.9%, Estrada/Runkle 2026). It is continued
    while OOOOb (new sources, compliance from 2027) and OOOOc (existing sources through state plans due Jan 2027,
    compliance about 2029–2030) phase in.
  - It is held flat after 2035 because no federal rule, and no charge before 2034, drives further cuts.
  - The Phase 2 rollback (super-emitter programme, marginal wells) argues for the slower end. NM and CO rules argue
    for faster cuts in the NM Permian, San Juan and DJ. These are not separated, because the label basins straddle
    states (e.g. Permian is 36% NM).
- **W. Canada:**
  - A linear fall from 2024 to 75% below 2012 in 2035 (Alberta MOU timing), flat after: 2030 0.707, 2035 0.463.
  - ASSUMPTION: 2012 ≈ the 2014 peak, so 2024 = 0.54 of the 2012 level (NIR: −46% from peak).
  - This tracks inventory outcomes; measurement may not fall as fast.
- **E. Canada:** the average of the W. Canada and US multipliers.
- **Fed. GOM and LNG imports:** flat. OOOOb/c do not cover Gulf OCS platforms west of 87.5°W, where air quality is under DOI (BOEM) under OCSLA §328 rather than EPA; no import-side rule applies.

Neither path re-weights basins. Zone shares change only through the Stage 1 projected shares, which are
indicative.

## 7. GWP and the worked example

- **GWP** (IPCC AR6 WG1 Ch. 7, Table 7.15, p. 1017; confirmed on the VM):
  - CH4 fossil (including CO2 from oxidation): GWP20 = 82.5 ± 25.8, GWP100 = 29.8 ± 11. Used here.
  - CH4 non-fossil: GWP20 = 79.7, GWP100 = 27.0. Not used.
- **AR5 for comparison:** 28 / 84. EPA's GHGI uses GWP100 = 28; GHGRP used AR4 25 until RY2025.

**Worked example: 2024 upstream CH4 per MWh of gas generation**
- **Inputs:**
  - 2024 Stage 1 shares (`zone_basin_shares.csv`);
  - heat rate 7,754 Btu/kWh, EIA Electric Power Annual Table 8.1, natural gas 2024 [fetched];
  - upstream intensities from §2 plus national T&S (0.0376 kg/MMBtu central);
  - distribution excluded.
- **Combustion comparison:** 116.65 lb CO2/MMBtu (EIA) = 52.91 kg/MMBtu → **410 kg CO2/MWh**.

| Zone (2024 supply) | Upstream kg CH4/MMBtu, central (L–H) | kg CH4/MWh incl. T&S, central (L–H) | t CO2e/MWh GWP100 | t CO2e/MWh GWP20 | vs combustion CO2 (GWP100 / GWP20) | Inventory-based kg CH4/MWh |
|---|---|---|---|---|---|---|
| FL p101 (Appalachia 34%, Haynesville 31%, Permian 9%, Midcon 8%, GOM 7%) | 0.212 (0.136–0.306) | 1.94 (1.10–2.75) | 0.058 (0.033–0.082) | 0.160 (0.090–0.226) | +14% / +39% | 0.53 |
| VA p99 (Appalachia 99.9%) | 0.106 (0.053–0.178) | 1.12 (0.45–1.75) | 0.033 (0.014–0.052) | 0.092 (0.038–0.145) | +8% / +22% | 0.38 |
| east TX p64 (Permian 39%, Eagle Ford 26%, Haynesville 23%) | 0.319 (0.195–0.459) | 2.76 (1.55–3.94) | 0.082 (0.046–0.117) | 0.228 (0.128–0.325) | +20% / +56% | 0.66 |
| CA p10 (Permian 33%, W. Canada 29%, Rockies 24%, San Juan 8%) | 0.402 (0.216–0.566) | 3.41 (1.72–4.77) | 0.102 (0.051–0.142) | 0.281 (0.142–0.393) | +25% / +69% | 1.01 |

How to read the table:
- **"vs combustion"** is upstream + T&S CO2e as a share of the plant's stack CO2.
- **"Inventory-based"** uses GHGRP 2023 basin rates (prod + G&B) plus the same T&S. Basins without GHGRP use the
  measured value, and W. Canada uses its NIR-level value. Measured values are **2.9–4.2× higher**.
- **Sensitivity, Eagle Ford conventional at 3.9%:** east TX +0.22 kg/MWh (+8%); FL +0.05 (+2%).
- **`current_rules` path with 2024 shares and heat rate held, kg CH4/MWh for 2030 → 2035–2045:**
  - FL 1.66 → 1.47;
  - VA 0.95 → 0.83;
  - east TX 2.25 → 1.91;
  - CA 2.72 → 2.23.

**Size of the effect.** At GWP100 this adds about 8–25% to gas generation's CO2, and at GWP20 about 22–69%. Across
zones it varies about 3×, driven by basin mix:
- **Appalachian supply (VA)** is lowest.
- **Permian/Rockies/Canada supply (CA)** is highest.
- **The CA value is the least certain:** Rockies weighting, plus Canadian measured-vs-NIR.
- **The low–high range within a zone (about 2.5×) is as large as the spread between zones.**

## 8. Sources

| Key | Citation | Accessed as |
|---|---|---|
| MSAT | MethaneSAT/MethaneAIR team, ACP 26:5961 (2026); MethaneSAT global assessment (Feb 2026; Permian 2.4%, Appalachia 0.6% of marketed). MethaneSAT lost contact 20 Jun 2025, so data cover Mar 2024–Jun 2025 | snippet |
| MAIR | Gautam et al., ACP 26:1179 (2026), https://acp.copernicus.org/articles/26/1179/2026/ — 2023 MethaneAIR, 70% of US onshore production, 1.6% of gross | snippet |
| VARON25 | Varon et al., "Seasonality and Declining Intensity of Methane Emissions from the Permian and Nearby US Oil and Gas Basins", ES&T 60(1), pub. 2025-12-22 (LBNL record eta-publications.lbl.gov) | snippet |
| TROPOMI / EST26 | Estrada/Runkle et al., ACP (2026), TROPOMI 2019–24 national and basin inversions (US 2.3% → 1.9%) | snippet |
| AMI | Appalachian Methane Initiative aerial campaign results (2025, industry-funded) | snippet |
| ZHANG20 | Zhang et al., Sci. Adv. 6:eaaz5120 (2020), Permian 3.7% (2018–19) | snippet |
| SHERWIN | Sherwin et al., Nat. Commun. (2024): NM Permian 9.6% (2018–20), DJ 1.08% (2021) | snippet |
| LIN21 / ROB17 / PEISCHL16 | Lin et al. 2021 (Uinta); Robertson et al. ES&T 2017 (Green River); Peischl et al. JGR 2016 (Bakken) | snippet |
| GN20 / GN23 | Gorchov Negron et al., ES&T 54:5112 (2020), doi:10.1021/acs.est.0c00179; PNAS 120:e2215275120 (2023) | snippet |
| CONRAD23 / JOHNSON23 / CHAN20 | Alberta conventional and BC aerial surveys (2023); Chan et al. ES&T 2020 | snippet |
| CER | Canada Energy Regulator, 2024 marketable gas production (AB 59.7%, BC 38.6%) | snippet |
| OMARA24 / OMARA22 | Omara et al., ESSD (2024) 2021 national 2.6%; Nat. Commun. 13:2085 (2022) low-production sites | snippet |
| ALV18 | Alvarez et al., Science 361:186 (2018), Table 1; author manuscript https://pmc.ncbi.nlm.nih.gov/articles/PMC6223263 | snippet |
| NOR | Norwegian offshore emissions 2024 (10,871 t CH4 over 126 bcm); Foulds et al. ACP (2022) | snippet |
| EPA_GHGRP_W | EPA, Greenhouse Gas Reporting Program Subpart W, RY2015–2023, Envirofacts `ef_w_*` tables (`ef_w_emissions_source_ghg`, `ef_w_facility_overview`). Record of origin: EPA originals on the VM (US-Power-Sector-Data `data/raw/gas_network/`, manifest with URL, date, sha256); session copy Zenodo 19671411, content-verified against EPA. RY2024 not published | fetched |
| EPA_GHGI_1990_2022 | EPA, Inventory of U.S. Greenhouse Gas Emissions and Sinks 1990–2022 (EPA 430-R-24-004, 18 Apr 2024): natural gas systems Tables 3-72 to 3-78 (CH4 by segment: 3-73 MMT CO2e, 3-74 kt); petroleum systems CH4 Table 3-45; abandoned wells Table 3-110. Record of origin: EPA files on the VM (as above); session copy Zenodo 22925436, byte-identical | fetched |
| EIA_PROD | EIA dnav gross/marketed/dry production and consumption by end use, released 2026-09-30 (pinned in `data/SOURCES.yml`) | fetched |
| EIA_LNG | EIA U.S. LNG imports by country and point of entry, 2024 | fetched |
| EIA EPA 8.1 | https://www.eia.gov/electricity/annual/html/epa_08_01.html — natural gas 2024 7,754 Btu/kWh | fetched 2026-10-11 |
| EIA CO2 | https://www.eia.gov/environment/emissions/co2_vol_mass.php — 116.65 lb CO2/MMBtu | fetched 2026-10-11 |
| GWP | IPCC AR6 WG1 Ch. 7, Table 7.15, p. 1017 | verified on the VM |

**EPA GHGI status.**
- No EPA 1990–2024 edition exists [snippet, EDF blog 15 Apr 2026]. The US missed the 2025 UNFCCC submission and
  has announced withdrawal effective Feb 2027.
- No final 1990–2023 edition is published. An unpublished version was released to EDF via FOIA (EDF library, blocked); snippet values
  from it are inconsistent, so they are not used. The latest published edition is 1990–2022, used here.
- UMD CGS published an unofficial 1990–2024 inventory; it is blocked, and no numbers could be obtained.

## 9. Blocked or paywalled, listed rather than worked around

- **IPCC and GWP:** ipcc.ch (AR6 Table 7.15, since confirmed on the VM); ghgprotocol.org; ghginstitute.org.
- **EPA:**
  - epa.gov (GHGI pages and Annex 3.5/3.6 spreadsheets, Subpart W sector profile); the GHGI 1990–2022 tables and
    Subpart W RY2015–2023 used here are held on the VM;
  - ghgdata.epa.gov (FLIGHT);
  - data.epa.gov and enviro.epa.gov (Envirofacts; RY2024 Subpart W is not published in any case);
  - 19january2025snapshot.epa.gov.
- **EDF and other inventories:**
  - library.edf.org (unpublished GHGI 1990–2023 released via FOIA);
  - blogs.edf.org, edf.org;
  - cgs.umd.edu (UMD 1990–2024 inventory).
- **BOEM:** boem.gov, espis.boem.gov (OCS AQS 2021 inventory, OCS Study BOEM 2023-023), data.boem.gov.
- **Rules and dockets:** federalregister.gov, regulations.gov, govinfo.gov.
- **Canada:** canada.ca, unfccc.int (NIR 2026 tables, US CRT).
- **Benchmarking and company reports:** cdn.catf.us / catf.us, erm.com, ceres.org, onefuture.us, miq.org,
  rystadenergy.com, iea.org (Methane Tracker, which would give Trinidad), environmentalintegrity.org (RY2024 FOIA
  data).
- **Journals:** nature.com, pnas.org, pubs.acs.org, copernicus.org (acp/egusphere), doi.org. Every journal value
  above is therefore [snippet] and should be checked against the paper before anything is published.
- **Files that would firm this up:**
  - GHGI 1990–2022 Annex 3.5/3.6 (natural gas activity data and emission factors);
  - the natural gas systems tables of a final GHGI 1990–2023, once EPA publishes one;
  - UMD 1990–2024 O&G tables;
  - RY2024 Subpart W, once EPA publishes it;
  - BOEM 2021 OCS AQS database;
  - ECCC NIR 2026 Part 3 / CRT tables by province;
  - the basin tables in Varon 2025, Gautam 2026 and the MethaneSAT ACP paper.

## 10. Open points for Tom

1. Whether to use measured `upstream_total` as the default basis for IDEA-01, with inventory as a sensitivity (the
   recommendation here).
2. Rockies (other) weighting for CA flows (Kern River = Green River-heavy), and the 50/50 E. Canada assumption.
