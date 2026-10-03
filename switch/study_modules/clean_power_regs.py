"""
SCAFFOLDING ONLY -- NOT IMPLEMENTED. See the `clean_power_regs` axis in
pg/settings/scenario_management.yml for the full STOP-AND-ASK writeup.

This module is meant to eventually represent the EPA Clean Air Act 111(b)/(d)
rule ("Clean Power Plan 2.0"): a new-gas CCS build requirement bundled with
the existing-coal compliance pathway it drives (both come from the same
rulemaking, so they are meant to toggle together via the single
`clean_power_regs` scenario axis rather than two independent switches).

Before any real logic goes here, the repo owner needs to confirm:
  1. Which version/source of the rule to encode (the 2024 finalized rule, a
     specific EPA guidance document, etc.) -- provide a citation.
  2. Interpretation: encode the rule AS FINALIZED, or a more conservative
     reading given the rule's litigation status as of the "Jan 2025 policy"
     baseline this scenario is meant to represent?
  3. Exact compliance thresholds, dates, and unit subcategories.

Until that's answered, this module is intentionally a no-op: it is NOT
included in switch/modules.txt, and `clean_power_regs: caa_2024_rule` in
scenario_management.yml does not set any settings that would activate it.

Once implemented, the expected shape (subject to revision once the above is
answered):
  - A new-gas CCS requirement: either (a) restrict new NaturalGas atb_new_gen
    entries to CCS-equipped technologies only after the compliance date (via
    atb_new_gen / new_gen_not_available-style settings), or (b) a capacity
    constraint analogous to MaxCapTag_Ban applied to unabated new gas after a
    threshold year.
  - An existing-coal compliance pathway: likely expressed as forced
    retirement-by-date or a required CCS retrofit, layered on top of (and
    consistent with) the `retirement_policy` axis rather than duplicating it
    -- coordinate with retirement_policy so the two axes don't fight over
    Can_Retire / retirement timing for the same units.
  - Reads a settings key (e.g. `clean_power_regs_rule: caa_2024`) rather than
    hard-coding thresholds in this file, so the axis in
    scenario_management.yml stays the single source of truth for scenario
    composition.
"""

# Intentionally left unimplemented pending repo-owner input -- see docstring.
# def define_components(m):
#     ...
#
# def load_inputs(m, switch_data, inputs_dir):
#     ...
