# Interconnection headroom: network-upgrade supply curves for ReEDS zones

Builds a rising, stepwise cost curve for interconnection headroom in each of the 134 ReEDS
zones, and a Switch module that makes new wind, solar and storage buy into it. The aim is to
represent the constraint that actually binds today — network upgrades a few hops from the point
of interconnection — which zonal capacity expansion models mostly miss, and to test
interconnection policy (planning regime, GETs, reconductoring, proactive planning) as changes
to that curve.

The full method write-up is in the project doc. This README covers running the code.

## Status (Sept 2026)

| Step | State |
|---|---|
| County → ReEDS zone mapping (EIA, LBNL) | Working. 99.3% of EIA-860M MW mapped. |
| Zone headroom proxy + saturation panel 2000–2030 | Working on real EIA-860M (Aug 2026) and ReEDS/NARIS 2024 transfer limits. |
| LBNL cost ingestion | Written and tested on synthetic workbooks only. Real files must be downloaded (emp.lbl.gov was blocked from the build environment). Header mapping will need checking. |
| Technology weights | All new generation (incl. gas) uses headroom at a per-tech weight; `fit-weights` estimates the weights from LBNL costs. |
| Saturation regression | Working; recovers known parameters from synthetic data. |
| Tranches + policy scenarios | Working; costs are monotone by construction. |
| Switch module | Draft. Solves on the Switch 2.0.9 `3zone_toy` example with HiGHS. Not yet run in Switch-USA-PG. |

**No result in `outputs/` is meaningful until the real LBNL workbooks are in place.**

## Setup

```
conda create -n icsc python=3.11 && conda activate icsc
pip install -r requirements.txt
```

Reference inputs already in `data/reference/` (copied from NREL ReEDS-2.0 at the commit in
`REEDS_COMMIT.txt`): `county2zone.csv`, `hierarchy.csv`, the NARIS 2024 county and BA AC transfer
limits, and ReEDS's 2026–30 queue file. `cpi_u_annual.csv` was typed in from reference values —
check it against BLS.

You need to add:
- `data/raw/august_generator2026.xlsx` — EIA-860M (or a newer month; update `paths.eia860m`)
- `data/raw/lbnl/*.xlsx` — LBNL interconnection cost workbooks (see `data/raw/lbnl/README.md`)

## Running

```
python -m icsc.cli panel                 # zone saturation panel from EIA-860M + transfer limits
python -m icsc.cli inspect-lbnl          # check LBNL headers resolved; edit config.yaml if not
python -m icsc.cli fit-weights           # proxy x tech weights x reuse share, ranked by AIC
python -m icsc.cli run --start-year 2026 # full pipeline
pytest -q                                # tests (Switch test needs ../switch-src clone)
```

`run` writes to `outputs/`:

- `zone_panel.csv`: zone × year headroom proxy, IBR additions, retirements and saturation
- `start_saturation.csv`: where each zone's curve starts (last EIA year plus planned changes)
- `lbnl_projects_clean.csv`, `estimation_sample.csv`
- `coefficients.csv`, `zone_regimes.csv`, `run_summary.json`
- `zones_<scenario>.csv`: base network capacity, starting saturation and release cost per zone
- `tranches_<scenario>.csv`: curve steps per zone (width in saturation units, $/kW, flagged if extrapolated)
- `uprates_<scenario>.csv`: network capacity options Switch may build (MW cap, $/kW of network, first year)
- `switch/<scenario>/ic_*.csv`: standalone Switch inputs

## Using it in Switch-USA-EDF

This folder lives inside Switch-USA-EDF. The Switch module is
`../switch/study_modules/interconnection_headroom.py`, and `pg_to_switch.py` writes its case
inputs when `pg/settings/interconnection_headroom.yml` has `enabled: true`. See
`../Guides and documentation/interconnection_headroom.md` and section 25 of `../CHANGES.md`.

`outputs/switch/<scenario>/` (from `icsc/switch_writer.py`) is a standalone export for other
Switch models; Switch-USA-EDF cases use `icsc/switch_case.py` instead.

## Finishing the build in the cloud

See `docs/cloud_setup.md` for the Claude Code cloud environment, setup script and project
instructions. `CLAUDE.md` holds the rules every session follows.

## Layout

```
icsc/geo.py            county name normalisation, county -> ReEDS zone
icsc/eia.py            EIA-860M -> zone panel (weighted additions and retirements by tech, saturation)
icsc/network.py        NARIS 2024 transfer limits -> headroom proxy
icsc/lbnl.py           LBNL workbook ingestion, cleaning, deflation
icsc/estimate.py       spline regression of log(1+NU $/kW) on saturation + controls + regime FE
icsc/tranches.py       zone tranches, best-regime counterfactual, scenario transforms
icsc/switch_writer.py  Switch input CSVs
icsc/synthetic.py      SYNTHETIC LBNL-style files for testing
icsc/switch_case.py    case inputs for pg_to_switch.py (pandas only)
tests/                 pytest suite incl. Switch toy run
```
