"""Write rps_requirements.ic_v2.csv next to rps_requirements.csv: identical, plus an
rps_acp_per_mwh column set for ESR_NY_rps only ("." = hard requirement everywhere else).

ACP: $45.39/MWh = NYSERDA's 2024 compliance-year Tier 1 ACP, the last one published (from 2025
the Tier 1 obligation is a load-share charge with no ACP). Source:
https://www.nyserda.ny.gov/All-Programs/Clean-Energy-Standard/LSE-Obligations/2024-Compliance-Year
Note: ESR_NY_ces (and every other program) stays a hard requirement (no ACP).
usage: python s0_workflow/case_aliases/make_rps_acp_alias.py <case dir> [<case dir> ...]
"""
import sys
from pathlib import Path

import pandas as pd

ACP = {"ESR_NY_rps": 45.39}
for case in sys.argv[1:]:
    case = Path(case)
    out = case / "rps_requirements.ic_v2.csv"
    if out.exists():
        raise FileExistsError(out)
    raw = pd.read_csv(case / "rps_requirements.csv", dtype=str, keep_default_na=False)
    assert "rps_acp_per_mwh" not in raw
    raw["rps_acp_per_mwh"] = raw.RPS_PROGRAM.map(lambda p: repr(ACP[p]) if p in ACP else ".")
    raw.to_csv(out, index=False)
    with open(case / "patch_log.ic_v2.txt", "a") as f:
        f.write(f"\nmake_rps_acp_alias.py: rps_requirements.ic_v2.csv = rps_requirements.csv plus "
                f"rps_acp_per_mwh {ACP} ('.' elsewhere); source NYSERDA 2024 Tier 1 ACP\n"
                f"solve with: --input-alias rps_requirements.csv=rps_requirements.ic_v2.csv\n")
    print(case, (raw.rps_acp_per_mwh != ".").sum(), "rows with ACP")
