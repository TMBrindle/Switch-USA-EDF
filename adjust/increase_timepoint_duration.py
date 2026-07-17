# %% setup
import sys
import argparse
from pathlib import Path
import pandas as pd

# files with timepoint columns (from add_extreme_days.py script)
timepoint_files = {
    # "hydro_timepoints.csv": "timepoint_id",  # not used for this project
    "loads.csv": "TIMEPOINT",
    "variable_capacity_factors.csv": "TIMEPOINT",
    "water_node_tp_flows.csv": "TIMEPOINTS",
    "dr_data.csv": "TIMEPOINT",
    "ee_data.csv": "TIMEPOINT",
}


def parse_arguments():
    # process command line options (name of inputs dir and )
    parser = argparse.ArgumentParser()
    parser.add_argument("in_dir", help="Path to the input directory")
    options = parser.parse_args()
    return options


def find_scenario_inputs_csv(in_dir, max_levels=8):
    """Walk up from in_dir looking for pg/extra_inputs/scenario_inputs.csv.

    This avoids hardcoding the project root at a fixed depth below in_dir, so it
    works regardless of how deeply nested the inputs dir is (e.g. the normal
    .../switch/in/{year}/{case_id}, a foresight build at .../switch/in/foresight/{case_id},
    or a test-only tree like .../switch/in_tests/{year}/{case_id}).
    """
    check = in_dir
    for _ in range(max_levels):
        candidate = check / "pg" / "extra_inputs" / "scenario_inputs.csv"
        if candidate.exists():
            return candidate
        parent = check.parent
        if parent == check:
            break
        check = parent
    raise FileNotFoundError(
        f"Could not find pg/extra_inputs/scenario_inputs.csv by walking up from {in_dir} "
        f"(searched up to {max_levels} parent directories)."
    )


def get_tp_duration_hours(in_dir):
    """Look up tp_duration_hours for this case (and year, if available) from
    scenario_inputs.csv.

    Handles both per-year layouts (.../in/{year}/{case_id}, where year is parsed
    from the parent directory name) and foresight/multi-period layouts
    (.../in/foresight/{case_id}, where there's no single year in the path — in
    that case all scenario_inputs.csv rows for case_id must agree on
    tp_duration_hours, since the built inputs apply one resolution across all
    periods).
    """
    in_dir = Path(in_dir).resolve()
    case_id = in_dir.name

    try:
        year = int(in_dir.parent.name)
    except ValueError:
        year = None  # no year in path (e.g. a foresight build) — match on case_id only

    si_path = find_scenario_inputs_csv(in_dir)
    si = pd.read_csv(si_path)
    if "tp_duration_hours" not in si.columns:
        raise KeyError(
            "tp_duration_hours column missing from scenario_inputs.csv; "
            "add a column with values 1 or 2 for each case."
        )

    if year is not None:
        rows = si[(si["case_id"] == case_id) & (si["year"] == year)]
        if rows.empty:
            raise KeyError(
                f"No row found for case_id={case_id!r}, year={year} in scenario_inputs.csv"
            )
    else:
        rows = si[si["case_id"] == case_id]
        if rows.empty:
            raise KeyError(f"No rows found for case_id={case_id!r} in scenario_inputs.csv")

    values = rows["tp_duration_hours"].unique()
    if len(values) > 1:
        raise ValueError(
            f"scenario_inputs.csv has inconsistent tp_duration_hours values {sorted(values)} "
            f"for case_id={case_id!r}"
            + (f", year={year}" if year is not None else " across its periods")
            + " — a single built inputs dir can only use one resolution."
        )

    return int(values[0])


# %% main code


# for testing:
# zsh: cp -R -p in/limit_nuclear_fixed/2030/s4x1/ /tmp/test_in/
# sys.argv = ['script', '/tmp/test_in']
def main():
    options = parse_arguments()
    in_dir = Path(options.in_dir)
    out_dir = in_dir
    new_tp_duration = get_tp_duration_hours(in_dir)

    if new_tp_duration == 1:
        print(f"tp_duration_hours=1 for {in_dir.parent.name}/{in_dir.name}; skipping timepoint merge.")
        return

    def read(file):
        return pd.read_csv(in_dir / file, na_values=".")

    def write(df, file):
        df.to_csv(out_dir / file, na_rep=".", index=False)
        print(f"saved {out_dir / file}.")

    ############
    # extend duration and reduce number of timepoints per timeseries
    ts = read("timeseries.csv")

    # Make sure files can be updated to the new interval as expected
    assert (
        ts["ts_duration_of_tp"] == 1
    ).all(), "Original timeseries.csv has some ts_duration_of_tp != 1"
    assert new_tp_duration == int(
        new_tp_duration
    ), f"New timepoint duration {new_tp_duration} is not an integer"
    new_num_tps_float = ts["ts_num_tps"] / new_tp_duration
    new_num_tps_int = new_num_tps_float.astype(int)
    assert (
        new_num_tps_float == new_num_tps_int
    ).all(), f"Some ts_num_tps values in timeseries.csv are not integer multiples of {new_tp_duration}."

    ts["ts_duration_of_tp"] = new_tp_duration
    ts["ts_num_tps"] = new_num_tps_int
    write(ts, "timeseries.csv")

    #########
    # downsample all files with timepoint indexes

    # In theory, a valid timepoint.csv file could have different timeseries mixed
    # together, as long as the timepoints in each timeseries show up in the right
    # order in the file. That would be pretty weird, so rather than prepare for it,
    # we just require that all the timepoints in the same timeseres are contiguous,
    # which makes the downsampling simpler.
    # assert (
    #     # only one timeseries change per timeseries
    #     (tp["timeseries"] != tp["timeseries"].shift()).sum() == tp["timeseries"].nunique()
    # ), "rows in timeseries.csv are not grouped by timeseries"

    # make sure rows are grouped by timeseries (in correct order) and sorted
    # by timepoint sequence within each group
    tp = (
        read("timepoints.csv")
        .reset_index()
        .rename(columns={"index": "tp_order"})
        .merge(
            ts[["timeseries"]].reset_index().rename(columns={"index": "ts_order"}),
            on="timeseries",
        )
        .sort_values(["ts_order", "tp_order"], axis=0)
        .drop(columns=["ts_order", "tp_order"])
    )
    # keep every nth timepoint
    tp = tp.iloc[::new_tp_duration, :]
    write(tp, "timepoints.csv")

    # keep only matching rows from timepoint files
    remaining_timepoints = tp["timepoint_id"]
    for file, col in timepoint_files.items():
        df = read(file).query(f"{col}.isin(@remaining_timepoints)")
        write(df, file)


if __name__ == "__main__" and "ipykernel" not in sys.argv[0]:
    # running as a script, not from a jupyter environment
    main()

# %%
