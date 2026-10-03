# Load growth settings (`load_growth` axis)

The `load_growth` axis in `pg/settings/scenario_management.yml` selects the load-growth case. Each value adds the
growth as a flexible-demand resource (`load_growth`) per model year, with `fraction_shiftable: 1.0`; shifting is
limited separately by the `load_growth_flexibility` axis.

| Value | Use | Load file |
|---|---|---|
| `edf_epri_med` | **baseline** for Scenarios 1-3 | `load_adjustments_edf_epri_med.csv.zip`, the default `demand_response_fn` in `pg/settings/flexible_load.yml` |
| `epri_high` | high-demand sensitivity | `load_adjustments_epri_high.csv.zip`, set by the axis value (`demand_response_fn`) |
| `icf`, `caelp`, `low` | earlier cases | as set in the axis |
| `none` | no added growth (historical cases) | |

Notes moved here from `scenario_management.yml` (Oct 2026):
- **Baseline data:** `make_study_loads.py` has `growth_case = "edf_epri_med"` and `growth_path =
  "growth_rates/epri_med"`. `switch/Growth_Profiles/edf_epri_med_targets.csv` has the growth targets through
  2050 for every region. The source data (`growth_rates/epri_med/`) and the load file are local to the VM
  (gitignored), not in the repository.
- **High-demand file not generated yet:** `load_adjustments_epri_high.csv.zip` doesn't exist. Generate it with
  `make_study_loads.py` (`growth_case = "epri_high"`, `growth_path = "growth_rates/epri_high"`) on the VM before
  running an `epri_high` case. No growth numbers were written in the settings.

## Model years

PowerGenome needs a `flexible_demand_resources` entry for every model year it builds, both in
`flexible_load.yml` (`us_exports`) and in the selected `load_growth` value (`load_growth`). Each entry points
to that year's profile in `demand_response_fn`. The 2045 S0 production stage needed:
- `edf_epri_med`: 2045;
- `epri_high`: 2040 and 2045;
- `flexible_load.yml`: 2040 and 2045.

These were added in Oct 2026 (CHANGES §48), following the existing pattern. PowerGenome is not patched.

**Follow-up (open):** generate these year entries from the model-year list (`model_definition.yml`
`model_year`), for example in `pg_to_switch.py` or a settings check, so extending the horizon can't break a
build again. Until then, adding a model year means adding an entry in each place above.
