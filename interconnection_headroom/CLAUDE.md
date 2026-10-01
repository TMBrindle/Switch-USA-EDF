# interconnection_headroom (inside Switch-USA-EDF)

Zonal interconnection (network-upgrade) supply curves for the 134 ReEDS zones, estimated from
LBNL project-level interconnection costs. The Switch module that uses them is
`../switch/study_modules/interconnection_headroom.py`; `pg_to_switch.py` writes its case inputs
when `pg/settings/interconnection_headroom.yml` has `enabled: true`. Owner: Tom
Brindle (EDF). The method and current status are in `docs/project_doc.html`; read it before
changing the method.

## Commands

```
bash scripts/fetch_data.sh            # EIA-860M + any LBNL workbooks uploaded to the project
python -m icsc.cli panel              # zone saturation panel (real data, no LBNL needed)
python -m icsc.cli inspect-lbnl       # how LBNL headers resolved; fix config.yaml lbnl.columns
python -m icsc.cli fit-weights        # proxy x tech weights x reuse share, ranked by AIC
python -m icsc.cli run --start-year 2026
SWITCH_SRC=/opt/switch-src pytest -q  # all tests, incl. a Switch toy solve with HiGHS
```

Run these from `interconnection_headroom/`. Commits go on a branch off `edf-baseline` (never
directly on `edf-baseline`, which is protected); record method or pipeline changes as a new
numbered section in `../CHANGES.md`.

## Rules

- **Synthetic data is never a result.** `python -m icsc.cli synthetic` writes `SYNTHETIC_*.xlsx`
  into `data/raw/lbnl/` for testing only. Delete them before any real run, and never quote numbers
  from a run that included them. `fetch_data.sh` reports how many real workbooks are present.
- If a real input is missing (LBNL workbooks, a newer EIA-860M month), say exactly what is missing
  and stop. Don't substitute, mock or guess values.
- Keep the saturation weights and the Switch weights the same: both come from
  `saturation.tech_weights` in `config.yaml`. Don't hard-code weights in code.
- Tranche costs must stay non-decreasing within each zone (the LP relies on it). Tests check this.
- Flag anything priced by extrapolation (the `extrapolated` column). Never present it as estimated.
- Placeholder parameters (GETs and reconductoring costs and caps, proactive-planning multiplier,
  CPI table) are marked in `config.yaml` and `data/reference/`. Replace them only with sourced
  values, and record the source in a comment.
- Units: costs in `config.yaml` and `tranches_*.csv` are real $/kW in `dollar_year`;
  Switch inputs are $/MW. Capacity is nameplate MW unless a column says `_weighted`.

## Checking work

Before calling a change done: run `pytest -q`, run the pipeline step you changed on real data
where it exists, and paste the summary lines (test count, match rates, n and R² from
`run_summary.json`) in your final message. If you change the method, update
`docs/project_doc.html` in the same branch.

## Layout

`icsc/` pipeline package (`icsc/switch_case.py` is the part pg_to_switch imports) ·
`../switch/study_modules/interconnection_headroom.py` the Switch module · `tests/` pytest suite and
Switch toy inputs · `data/reference/` committed reference inputs (ReEDS commit in
`REEDS_COMMIT.txt`) · `data/raw/` downloaded inputs (gitignored) · `outputs/` (gitignored).

## Not available in cloud sessions

Switch-USA-PG case inputs, PowerGenome data and commercial solvers (Gurobi, COPT) live on
energyVm1. Full Switch-USA-PG runs happen there, not in the cloud. Cloud work stops at
producing `outputs/{zones,tranches,uprates}_<scenario>.csv` and testing the module on the toy model.
