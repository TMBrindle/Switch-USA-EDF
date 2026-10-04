# `forced_tx_status_review.csv` — method note

Replaces the header-only placeholder (CHANGES §60) that `s0_workflow/production.py`'s
`reeds_certain_plus_A` / `reeds_certain_plus_AB` forced-transmission options read via
`status_review_rows()`. Columns are fixed by that function's `STATUS_REVIEW_COLUMNS` check
(`from_zone, to_zone, project_name, status_class, new_cap_mw, mw_basis, new_cap_year,
trans_length_km, trans_efficiency, source, notes`); every row passes that function's validation
(`mw_basis == "transfer_capability"` and no nulls in `new_cap_mw`/`new_cap_year`/
`trans_length_km`/`trans_efficiency`), confirmed by running it directly.

## Starting point

Built from `pg/extra_inputs/transmission/transmission_connections.csv`'s 72 rows with
`new_cap_mw > 0` (the same source used by every other named-project forced-transmission case),
cross-checked against the `forced_projects.csv` project-level rollup from an earlier review pass.

## Classes

- **Class A** — in service by 2035 very likely.
- **Class B** — everything else kept (later in-service years, or less certain).
- **Dropped entirely, not in this file** (11 source rows): Plains & Eastern Clean Line (3 rows),
  "Mountain View-Lambert" (unidentified), the Humboldt/Fern Road/Shaffer bundle, Gateway South
  Aeolus-Clover (already in service), four unidentified PJM-area codes (s2853.2a/b, n8376, n7147,
  n7204), and one intra-zone row (b3800.212, both ends effectively p99).

## MW rule

Published transfer-capability figures are used directly where Tom supplied one (Grain Belt,
Southern Spirit, SOO Green, Holcomb-Sidney, SWIP-North, Gateway West Midpoint-Hemingway, Propel
NY). Otherwise: **HVDC lines at 100% of rating; AC lines at 50% of thermal rating**, after
rebuilding each row's nameplate to remove duplicate-named components (e.g. "Howard to Solstice
765kV" and "Solstice to Howard New 765 kV line" are the same corridor, counted once) or split
bundled multi-project rows Tom called out explicitly (LRTP-14 + Coffeen-Roxford reconductor;
LRTP-42 + LRTP-16). The rule applied, and any de-duplication/split arithmetic, is recorded per row
in `notes`.

**Where an individual component's own MW wasn't separately available** (true for the de-dup/split
cases above), an even-share estimate is used and flagged in `notes` as an assumption — these are
estimates, not verified per-component figures.

## MISO Tranche numbering

Tranche 1 projects (#1, 2, 4, 6, 9, 10, 11, 14, 15, 16, 17, 18) are Class A per Tom's list.
Tranche 2.1 projects (#22, 23, 24, 26, 30, 31, 33, 35, 37, 39, 40, 41) are Class B, dated per
Tom's instruction (#33 → 2033; the rest → 2034 or 2032 per his two sub-groups). Two source rows
bundle a listed Tranche number with an *unlisted* one (#40 with LRTP-13; #30 with LRTP-28) — not
split (Tom asked only for the #14/Coffeen-Roxford and #42/#16 splits), so the whole bundled row
takes the listed number's year; flagged in `notes`.

## Totals (read directly from the file)

| | Rows | MW | MW-km | Interregional MW (share) |
|---|---|---|---|---|
| Class A | 21 | 28,170.7 | 7,270,072 | 5,376.0 (19.1%) |
| Class B | 42 | 77,837.0 | 21,785,154 | 24,080.0 (30.9%) |
| **Total** | **63** | **106,007.7** | **29,055,227** | **29,456.0 (27.8%)** |

Interregional = `from_zone`/`to_zone` in different `hierarchy.csv` transregs.

## Flagged / not fully resolved

- **p60-p61** (Howard-Solstice + Drill Hole-Riverton): nameplate split between the 765kV line and
  the 345kV DCKT is an equal-share estimate (2/3 of the bundled total, after removing the
  duplicate-named 765kV entry), not a verified per-line figure.
- **p81-p83** (LRTP-14 / Coffeen-Roxford reconductor): split evenly (50/50) between the two
  bundled projects; no independent MW for either.
- **p70-p81** (LRTP-40, bundled with unlisted LRTP-13) and **p76-p79** (LRTP-30, bundled with
  unlisted LRTP-28): whole bundled row dated to the listed Tranche 2.1 number's year; not split.
- **p122-p123** (b3800.31): n6559 and b3780.2, bundled in the same source row, could not be
  independently identified.
- **p107-p105** (n5034) and **p100-p99** (b3800.119, "Woodside-Aspen"): Tom corrected the zone
  pair from the source's listed pair; `trans_length_km`/`trans_efficiency` are carried over from
  the source's (uncorrected) row since a corrected-pair distance isn't available.
- **p15-p16** (Gateway West Midpoint-Hemingway): length/efficiency are the source's full-corridor
  values, not segment-specific.
- **p127-p128** ("Propel NY Energy") and **p51-p55** ("probably Delaware-Monett 345 kV"):
  identifications are probable/tentative per Tom, not confirmed.
- **"Doubs-Aspen" (2030)**, one of three segments Tom named alongside Beckham County-Potter and
  Potter-Crossroads-Phantom, could not be matched to any row in `transmission_connections.csv` —
  **not included in this file**, reported as unresolved rather than invented.
- **"Ten West Link"** (named in the exclusion list as already in service) does not appear in
  `transmission_connections.csv` at all — nothing to exclude, no action needed.

## A data-structure limitation in the consuming code, not in this file

Two zone pairs legitimately carry two rows each (p80-p105: Tranche-1 #16 + Tranche-2.1 #42;
p81-p83: Tranche-1 #14 + the Coffeen-Roxford reconductor), because Tom asked for those two source
rows to be split by class. `pg_to_switch.py`'s forced-line loop builds `planned_with_year` as a
plain `{frozenset(zone_pair): (mw, year)}` dict, so when **both** rows for the same pair are
selected together (the `reeds_certain_plus_AB` case, which includes every class), the second row
processed silently overwrites the first — only one of the two circuits' (MW, year) actually
reaches `trans_build_minimum`. This is a pre-existing limitation of the forced-line merge logic
(it has no way to carry two independent minimums on one zone pair), not something introduced by
this file, and not something this task was scoped to fix — flagging it here so whoever runs
`reeds_certain_plus_AB` knows to check `trans_build_minimum.csv` for p80-p105 and p81-p83
specifically.
