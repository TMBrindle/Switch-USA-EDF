# %% setup
"""
Forward the end-of-period allowance bank balance from one myopic solve's outputs
into the next period's carbon_policies_banking_*.csv input files.

Usage:
    python adjust/pass_bank_balance.py <current_out_dir> <next_in_dir>

Where:
    current_out_dir — output directory for the just-completed period
                      (must contain allowance_bank_balance.csv)
    next_in_dir     — input directory for the next period to be solved
                      (must contain carbon_policies_banking_*.csv files to update)

Called automatically by prepare_next_stage.py when allowance_bank_balance.csv
is present in the current output directory.
"""
import sys
import argparse
from pathlib import Path
import pandas as pd


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Forward allowance bank balance from one myopic period to the next."
    )
    parser.add_argument("current_out_dir", help="Output directory of the completed period")
    parser.add_argument("next_in_dir", help="Input directory of the next period")
    return parser.parse_args()


def main():
    args = parse_arguments()
    out_dir = Path(args.current_out_dir)
    in_dir = Path(args.next_in_dir)

    bank_file = out_dir / "allowance_bank_balance.csv"
    if not bank_file.exists():
        print(f"pass_bank_balance: {bank_file} not found; skipping bank forwarding.")
        return

    bank_df = pd.read_csv(bank_file)
    if bank_df.empty:
        print("pass_bank_balance: allowance_bank_balance.csv is empty; skipping.")
        return

    # Extract end-of-period balance for each program
    end_balances = {
        str(row.CO2_PROGRAM): float(row.bank_end_tco2)
        for row in bank_df.itertuples()
    }

    # Find all carbon_policies_banking_*.csv files in next period's input directory
    banking_files = list(in_dir.glob("carbon_policies_banking_*.csv"))
    if not banking_files:
        print(
            f"pass_bank_balance: no carbon_policies_banking_*.csv in {in_dir}; "
            "skipping bank forwarding."
        )
        return

    for bf in banking_files:
        df = pd.read_csv(bf)
        if df.empty or "CO2_PROGRAM" not in df.columns:
            continue
        updated = False
        for idx, row in df.iterrows():
            prog = str(row["CO2_PROGRAM"])
            if prog in end_balances:
                df.at[idx, "initial_bank_tco2"] = int(end_balances[prog])
                updated = True
        if updated:
            df.to_csv(bf, index=False)
            print(f"pass_bank_balance: updated {bf.name} with end-period bank balances.")

    print(
        f"pass_bank_balance: bank balances from {out_dir.name} → {in_dir.name}: "
        + ", ".join(f"{p}={v:,.0f} tCO2" for p, v in end_balances.items())
    )


if __name__ == "__main__" and "ipykernel" not in sys.argv[0]:
    main()

# %%
