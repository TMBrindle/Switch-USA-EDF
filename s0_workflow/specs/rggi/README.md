# Virginia RGGI budget (CHANGES §66)

`va_budget.csv`: Virginia's CO2 budget by year, short tons (`year, va_budget_short_tons`). The S0 cases add it to
ETS 1 (on top of the RGGI10 3PR cap, `pg/extra_inputs/rggi_carbon/rggicon_3pr.csv`) from 2028
(`s0_production.rggi.virginia`; `s0_workflow/production.py` `write_va_rggi`). After the last year the value is held.

Initial values (Oct 2026): Virginia's 2026 full-year budget, 22,960,000 short tons, scaled by the RGGI10 3PR cap
path relative to 2026 (constant share of the RGGI10 cap): budget(y) = 22.96M x cap10(y) / cap10(2026), 2026-2037,
held after 2037 like the RGGI10 cap. RGGI confirms that Virginia's budget and CCR are additional to the published
RGGI10 volumes.

Replace the rows with DEQ's figures when Revision D26 is adopted. For reference:

- DEQ's reported proposed 2027 budget: 20,408,889 short tons (unverified; the file has 20,413,052, +0.02%);
- the current regulation (9VAC5-140): 22.12M short tons (2027) to 19.60M (2030); the file's 2030 value, 12.76M,
  follows the much steeper 3PR path.

Virginia's cost containment reserve: 10% of its budget per tier (as in 2026), added to the ETS 1 CCR pools.
