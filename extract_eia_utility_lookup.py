"""
Extract a lookup table mapping EIA utility IDs to utility names from the PUDL
SQLite database. Output is saved to eia_utility_lookup.csv in the current
directory.

Usage:
    python extract_eia_utility_lookup.py <path_to_pudl.sqlite>

If no path is given, the script reads PUDL_DB from pg/settings/env.yml.
"""

import sys
from pathlib import Path

import pandas as pd
import sqlalchemy


def get_pudl_path():
    """Return PUDL_DB path from env.yml if not supplied on command line."""
    env_yml = Path(__file__).parent / "pg" / "settings" / "env.yml"
    import yaml
    with open(env_yml) as f:
        env = yaml.safe_load(f)
    return env["PUDL_DB"]


def main():
    if len(sys.argv) >= 2:
        pudl_path = sys.argv[1]
    else:
        pudl_path = get_pudl_path()

    print(f"Connecting to PUDL database: {pudl_path}")
    engine = sqlalchemy.create_engine(f"sqlite:///{pudl_path}")

    # List available tables for reference
    with engine.connect() as con:
        tables = sqlalchemy.inspect(engine).get_table_names()

    utility_tables = [t for t in tables if "utilit" in t.lower()]
    print(f"Utility-related tables found: {utility_tables}")

    # Try the standard PUDL utilities_eia table first, then fall back
    for table in ["utilities_eia", "utility_eia", "utilities_eia860"]:
        if table in tables:
            df = pd.read_sql_table(table, engine)
            print(f"Read {len(df)} rows from '{table}'. Columns: {df.columns.tolist()}")
            break
    else:
        raise ValueError(
            f"No recognised utility table found. Available utility tables: {utility_tables}"
        )

    # Identify the ID and name columns (handle slight schema variations)
    id_col = next(
        (c for c in df.columns if c in ("utility_id_eia", "utility_id")), None
    )
    name_col = next(
        (c for c in df.columns if c in ("utility_name", "utility_name_eia", "name")),
        None,
    )
    if id_col is None or name_col is None:
        raise ValueError(
            f"Could not identify ID/name columns. Columns available: {df.columns.tolist()}"
        )

    # Keep useful identifying columns if present
    extra_cols = [c for c in ("state", "entity_type") if c in df.columns]
    out_cols = [id_col, name_col] + extra_cols

    result = (
        df[out_cols]
        .drop_duplicates(subset=[id_col])
        .sort_values(id_col)
        .reset_index(drop=True)
    )
    result = result.rename(columns={id_col: "utility_id_eia", name_col: "utility_name"})

    out_path = Path(__file__).parent / "eia_utility_lookup.csv"
    result.to_csv(out_path, index=False)
    print(f"Saved {len(result)} utilities to {out_path}")

    # Print the top-5 CO2 utilities identified in the test run for quick reference
    top5 = [195, 6452, 7140, 18642, 19876]
    print("\nTop-5 CO2 utilities from s4x1_caelp_ut5clust run:")
    print(result[result["utility_id_eia"].isin(top5)].to_string(index=False))


if __name__ == "__main__":
    main()
