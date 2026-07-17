"""
Re-insert the p18-p19 transmission line that is missing from the NARIS2024
source data used by PowerGenome. The line parameters come from an earlier
(pre-NARIS2024) version of the model. This script is idempotent — it does
nothing if 18-19 is already present.
"""
import sys
import pandas as pd
from pathlib import Path

P18_P19_ROW = {
    "TRANSMISSION_LINE": "18-19",
    "trans_lz1": "p18",
    "trans_lz2": "p19",
    "trans_length_km": 333.777191,
    "trans_efficiency": 0.966393,
    "existing_trans_cap": 262.0,
    "trans_dbid": 309,
    "trans_derating_factor": 1.0,
    "trans_terrain_multiplier": 2.054553,
    "trans_new_build_allowed": 1,
}

in_dir = Path(sys.argv[1])
tx_path = in_dir / "transmission_lines.csv"

if not tx_path.exists():
    print(f"fix_p18p19: {tx_path} not found, skipping.")
    sys.exit(0)

tx = pd.read_csv(tx_path)

if "18-19" in tx["TRANSMISSION_LINE"].values:
    print("fix_p18p19: 18-19 already present, nothing to do.")
    sys.exit(0)

# Insert between 17-20 and 18-20
anchor = tx.index[tx["TRANSMISSION_LINE"] == "17-20"]
if anchor.empty:
    # fallback: append at end
    insert_at = len(tx)
else:
    insert_at = anchor[0] + 1

new_row = pd.DataFrame([P18_P19_ROW])
tx = pd.concat([tx.iloc[:insert_at], new_row, tx.iloc[insert_at:]], ignore_index=True)
tx.to_csv(tx_path, index=False)
print(f"fix_p18p19: added 18-19 to {tx_path}")
