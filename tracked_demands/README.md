# tracked_demands

Hourly clean-energy matching for specific loads (data centres pursuing 24/7 CFE, electrolysers under 45V), with
on-site generation and storage and flexibility obligations. The Switch module is
`../switch/study_modules/tracked_demands.py`. It is opt-in and not in `switch/modules.txt`. The guide is
`../Guides and documentation/tracked_demands.md`; read sections 11–13 before using it in a case. Owner: Tom
Brindle (EDF). History: CHANGES §94.

## Layout

- `tests/`: pytest suite with toy solves (HiGHS, Switch's 3-zone example; set `SWITCH_SRC`). `td_toy.py` builds the
  toy tracked-demand inputs. They are fixtures, not results.
- `scripts/`: the May 2026 data-centre study scripts, unchanged apart from the move. They read a case inputs folder
  and solved outputs (`--inputs-dir`; defaults name the old `in/2035/s4x1_caelp_parclust_zoned` case).
  - `prepare_dc_onsite_techs.py`: per-zone on-site tech menus, build caps and storage files from a case's
    `gen_info.csv` / `gen_build_costs.csv`.
  - `setup_dc_scenario_inputs.py`: the scenario files (1 GW data centre, 250/900 MW soft grid caps, siting,
    summer-peak curtailment bounds, solar/non-solar time blocks).
  - `analyze_dc_scenarios.py`: summary tables from solved scenarios.
  - `plots/`: figures from those summaries.
  - `legacy/`: the PowerShell runners for the old full-case matrix (`run_td_tests.ps1`, groups A–F, now ported to
    `tests/test_td_toy_suite.py`) and the data-centre scenario batch (`run_dc_scenarios.ps1`). They hard-code a
    Windows path and the old case.
- `docs/DC_OnSite_Power_Scenarios.docx`: the data-centre grid-constraint scenario design (SC/MC families, six
  jurisdictions).

```
SWITCH_SRC=/opt/switch-src pytest -q tracked_demands/tests
```

## Left on `tom/tracked-demands` (d18fef1), not brought across

- Outputs of the May study: `switch/dc_analysis_*.csv`, `switch/dc_*_batch_run_log.txt`, `switch/dc_*.png`.
- One-off scripts with hard-coded Windows paths for other work: `check_ratio_violations.py`,
  `compare_capacity_scenarios.py`, `compare_gen_scenarios.py` (gen_zone_ratio and capacity comparisons).
- Changes to shared files in e60d215 that belong to other work or are superseded:
  - `switch/modules.txt` (`study_modules.tracked_demands` added): loading is now opt-in;
  - `make_study_loads.py` (a second `demand_response_file` assignment): the current version removed it;
  - `adjust/define_scenarios.py` (`scenario_extra_options.txt` read into scenario lines): S0 scenario lines are
    written by `pg_to_switch.py`;
  - `pg/extra_inputs/network_costs_ReEDS.csv` (a p18–p19 row): not tracked-demand work;
  - `pg_to_switch.py` and `scenario_management.yml` docstring/comment edits on gen_zone_ratio: older wording,
    superseded on edf-baseline;
  - the PowerGenome submodule pointer (4d7427d): the current pointer is kept.
- Not on any branch: the design documents of April–May (spec v1, the 15 design decisions, the 10-stage
  implementation handoff). The scenario design is here as `docs/DC_OnSite_Power_Scenarios.docx`.
