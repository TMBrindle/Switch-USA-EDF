"""
Prepare inputs for the next model stage when running a series of single-year
(myopic) models. This requires the following:

* --inputs-dir and --outputs-dir for this model are in the form
  <something>/<year>/<case_name>
* <year> is 2030 or 2040 and matches the period of this model. These will be
  passed forward to 2040 or 2050 respectively.
* an inputs dir for the next period already exists and contains gen_build_costs.csv

Then, for chained models, alternative versions of gen_build_predetermined.csv,
gen_build_costs.csv and transmission_lines.csv will be stored in the next inputs
directory, with the filename changed to <filename>.chained.<case_name>.csv.

Bounded foresight (stage_info.csv, written by pg_to_switch.py for S0 production chains; see
s0_workflow/production.py): when this stage's inputs dir has stage_info.csv
(stage, commit_period, next_stage), the next stage's inputs dir is
<inputs root>/<next_stage>/<case> instead of the fixed year list below, and only what this stage
committed is handed on:
  * builds with a build year after the end of commit_period (later periods of a rolling window, and
    predetermined builds dated after it, which the next stage supplies itself) are dropped;
  * retirements (SuspendGen) only in periods up to commit_period;
  * transmission built only in periods up to commit_period;
  * interconnection headroom used, uprates built and released headroom as of commit_period;
  * the build-rate ramp history from commit_period.
With commit_period = this stage's last period (a myopic stage, or the last window) this is the
same as before. Without stage_info.csv nothing changes.
"""

import os, sys
from pathlib import Path
import numpy as np
import pandas as pd


def post_solve(m, outdir):
    # note: this function uses previously written model outputs instead of
    # built-in model info, so the code can be reused without running the
    # model if needed.

    # how to choose the next year in the chain
    model_years = [2027, 2030, 2035, 2040, 2045, 2050]
    next_year_dict = dict(zip(model_years[:-1], model_years[1:]))

    # we can tell which period we're dealing with by looking at m.PERIODS
    # or outdir, which should be be <root>/year/case
    in_path = Path(m.options.inputs_dir)
    out_path = Path(m.options.outputs_dir)

    year_name, case_name = out_path.parts[-2:]

    stage = read_stage_info(in_path)
    if stage is not None:
        if stage["next_stage"] in (None, "", "."):
            return   # last stage of the chain
        next_in_path = Path(*in_path.parts[:-2], str(stage["next_stage"]), in_path.parts[-1])
        if not next_in_path.exists():
            raise FileNotFoundError(f"{__name__}: next stage inputs {next_in_path} (stage_info.csv) not found")
        commit = int(stage["commit_period"])
        periods = pd.read_csv(in_path / "periods.csv").set_index("INVESTMENT_PERIOD")
        commit_end = int(periods.loc[commit, "period_end"])
        return chain_stage(m, in_path, out_path, next_in_path, case_name, commit, commit_end)

    # sanity checks on directory names
    if year_name not in {str(y) for y in next_year_dict.keys()}:
        try:
            year = int(year_name)
            if 2020 <= year <= 2060:
                raise NotImplementedError(
                    f"{__name__} needs to be updated to handle model year {year}."
                )
        except:
            pass
        raise ValueError(
            f"{__name__} requires --outputs-dir in the form <root>/<year>/<case>; "
            f"'{out_path}' is not recognized."
        )
    if year_name != in_path.parts[-2]:
        raise ValueError(
            f"Year '{in_path.parts[-2]}' in --inputs-dir doesn't match '{year_name}' in --outputs-dir."
        )

    year = int(year_name)
    next_year = next_year_dict[year]

    # input dir for the first model in the chain (used as starting point)
    next_in_path = Path(
        *in_path.parts[:-2], str(next_year_dict[year]), in_path.parts[-1]
    )

    return chain_stage(m, in_path, out_path, next_in_path, case_name, None, None)


def read_stage_info(in_path):
    """stage_info.csv in a stage's inputs dir as a dict, or None (legacy myopic chains)."""
    p = Path(in_path) / "stage_info.csv"
    if not p.exists():
        return None
    return pd.read_csv(p, dtype=str, keep_default_na=False).iloc[0].to_dict()


def existing_fom_from_next_stage(costs, next_costs, projects):
    """gen_fixed_om of the listed (existing-only) projects from the next stage's own gen_build_costs.csv (one value
    per project there: pg_to_switch gives every build year of an existing cluster the same fixed O&M). Projects the
    next stage has no own row for (e.g. a cluster PowerGenome no longer counts in that model year while Switch
    still runs it there) keep this stage's value. Returns (costs, number of projects updated)."""
    g, nc = costs.columns[0], next_costs.rename(columns={next_costs.columns[0]: costs.columns[0]})
    own = nc.dropna(subset=["gen_fixed_om"]).drop_duplicates(subset=[g]).set_index(g)["gen_fixed_om"]
    m = costs[g].astype(str).isin(projects) & costs[g].isin(own.index)
    costs = costs.copy()
    costs.loc[m, "gen_fixed_om"] = costs.loc[m, g].map(own)
    return costs, int(costs.loc[m, g].nunique())


def chain_stage(m, in_path, out_path, next_in_path, case_name, commit=None, commit_end=None):
    """Write the next stage's chained inputs. commit / commit_end: the period this stage commits and
    its last calendar year (None = everything this stage built, the legacy behaviour)."""
    # finished preparing and validating year, year_name, case_name, in_path (this
    # model's inputs dir), out_path (this model's outputs dir) and next_in_path (
    # inputs dir for next model in the chain)

    # note: in_path may be shared between multiple cases and/or this script may
    # be run multiple times, so we generate new input files with
    # ".chained.{case_name}" appended. We also read those in as the starting
    # point for the next step in the chain when available. This means we start
    # with a 2030 predetermined build plan (and costs), then add 2030
    # construction to that, then add 2040 construction to that, not to the 2040
    # predetermined build. So some old plants may be in
    # 2040/data_case/gen_build_predetermined.chained.{scenario}.csv that aren't
    # in 2040/data_case/gen_build_predetermined.csv. This is consistent with the
    # multi-period (foresight) models, the extra plants will be ignored
    # after their retirement date, and this is easier than starting with a
    # 2050 predetermined-build file, then going back and adding 2030 and 2040
    # construction to it.

    def chained(*parts):
        """
        Return file path, joined together if needed, with fname.csv
        converted to fname.chained.{case_name}.csv.
        """
        path = Path(*parts)
        return Path(path.parent, f"{path.stem}.chained.{case_name}{path.suffix}")

    def possibly_chained(*parts):
        """
        Return file path, joined together if needed, with fname.csv
        converted to fname.chained.{case_name}.csv if that file exists.
        """
        path = Path(*parts)
        new_path = chained(path)
        return new_path if new_path.exists() else path

    def read_csv(*parts):
        return pd.read_csv(Path(*parts), na_values=["."])

    def to_csv(df, *parts):
        path = Path(*parts)
        # path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False, na_rep=".")

    """
    df = predet
    filename = "gen_build_predetermined.csv"
    """

    def merge_build_data(df, filename):
        """
        Use construction data from the current dataframe with later construction
        data from the specified table in the next stage.

        df and <filename> should have generation project and build year as first
        two columns.

        This uses all data from df plus any data from <filename> that occurs
        after the start of the next period. However, if use_later_records()
        returns True, it will always drop the row from df and always use data
        from <filename>.

        Splitting in time ensures that future stages are consistent with the
        past, including any retirements (which may get dropped completely from
        the model.)
        """
        next_period_start = read_csv(next_in_path, "periods.csv")["period_start"].min()
        next_df = read_csv(next_in_path, filename)
        # make sure column names are consistent
        next_df = next_df.rename(columns=dict(zip(next_df.columns[:2], df.columns[:2])))
        # drop any use_next_record() records from incoming df
        df = df.loc[~use_next_stage_records(df), :]
        # drop data from next stage prior to the start of that stage
        next_df = next_df.loc[
            (next_df.iloc[:, 1] >= next_period_start)
            | (use_next_stage_records(next_df)),
            :,
        ]
        df = pd.concat([df, next_df], ignore_index=True)
        # The first two columns should be ["GENERATION_PROJECT", "BUILD_YEAR"]
        # but spelling/capitalization may differ.
        dup_cols = df.columns[:2]
        # save any duplicate gen/build_year with different capacities (hopefully none)
        dups = df.loc[df.duplicated(subset=dup_cols, keep=False), :]
        if not dups.empty:
            # Keep the data from the selected period, but save a copy of the
            # duplicates in case the user wants to inspect them (these are
            # fairly common because unit sizes vary between periods due to
            # derating with a capacity factor that may vary between periods, and
            # fixed O&M rises over time for some technologies in PG; retirements
            # also create differences between predetermined capacity in the
            # dataframe and in the next model).
            df = df.drop_duplicates(subset=dup_cols, keep="first")
            # diagnostic only (Switch doesn't read it): kept for inspection
            to_csv(dups, chained(next_in_path, "dup." + filename))
            print(f"{__name__}: {dups[list(dup_cols)].drop_duplicates().shape[0]} {filename} rows in both stages "
                  f"(kept this stage's; diagnostic copy {chained(next_in_path, 'dup.' + filename).name})")

        return df

    def use_next_stage_records(df):
        """
        Accept a dataframe with generation project names in the first column and
        return a vector of True or False indicating whether we should _always_
        use data for this generator from the next stage (True) or prefer data
        from the current stage (False).

        This is only used to identify distributed generation (usually rooftop
        solar). PowerGenome doesn't have construction years for this capacity,
        so pg_to_switch.py calculates a suitable schedule. But this schedule may
        differ from one stage of a myopic model to the next, so we drop anything
        from the earlier stage and just use the schedule calculated for the
        later stage (generally constructed in the reference year of the period.)
        Note: this makes it impossible to carry distributed gen retirements
        through to later stages, but that is not allowed anyway.
        """
        return df.iloc[:, 0].str.contains("distributed_generation")

    # use actual construction from current model (includes both predetermined and
    # optimized resources) as the predetermined construction for the next stage.
    build_mw = read_csv(out_path, "BuildGen.csv").rename(
        columns={
            "GEN_BLD_YRS_1": "GENERATION_PROJECT",
            "GEN_BLD_YRS_2": "build_year",
            "BuildGen": "build_gen_predetermined",
        }
    )
    build_mwh = read_csv(out_path, "BuildStorageEnergy.csv").rename(
        columns={
            "STORAGE_GEN_BLD_YRS_1": "GENERATION_PROJECT",
            "STORAGE_GEN_BLD_YRS_2": "build_year",
            "BuildStorageEnergy": "build_gen_energy_predetermined",
        }
    )
    predet = build_mw.merge(build_mwh, how="left")
    if commit_end is not None:
        # bounded foresight: hand on only builds up to the end of the committed period; later
        # optimised builds were not committed, and predetermined builds dated later come from the
        # next stage's own gen_build_predetermined.csv (merge_build_data below)
        predet = predet[predet["build_year"] <= commit_end]

    # Treat any retired capacity as if it was never built.
    # Note: this will not carry forward capital costs of retired plants, so it
    # should only be used with plants with no capital costs. That is the
    # approach in MIP, where it only applies to existing plants, which are shown
    # with $0 capital cost.
    retire = read_csv(out_path, "SuspendGen.csv").rename(
        columns={
            "GEN_BLD_SUSPEND_YRS_1": "GENERATION_PROJECT",
            "GEN_BLD_SUSPEND_YRS_2": "build_year",
            "GEN_BLD_SUSPEND_YRS_3": "retire_year",
            "SuspendGen": "retire_mw",
        }
    )
    gen_info = read_csv(possibly_chained(in_path, "gen_info.csv"))
    # note: retire will have rows if gen_can_retire_early or gen_can_suspend
    # are set, but it is only binding for the future if gen_can_suspend is _not_
    # set but there is still a value (implying gen_can_retire_early was set).
    if not "gen_can_suspend" in gen_info.columns:
        gen_info["gen_can_suspend"] = 0
    must_retire_gens = gen_info.query("gen_can_suspend == 0").iloc[:, 0]
    retire = retire.loc[
        retire["GENERATION_PROJECT"].isin(must_retire_gens)
        & (retire["retire_mw"] > 1e-6),
        :,
    ]
    if commit is not None:
        # only retirements taken in committed periods
        retire = retire[(retire["retire_year"] <= commit) & (retire["build_year"] <= commit_end)]
    # handle chained models with multi-period stages (possible in the future)
    retire = (
        retire.groupby(["GENERATION_PROJECT", "build_year"])[["retire_mw"]]
        .sum()
        .reset_index()
    )
    predet_cols = predet.columns
    predet = predet.merge(retire, how="left")
    predet["build_gen_predetermined"] -= predet["retire_mw"].fillna(0)
    predet = predet[predet_cols]

    # drop any that don't appear in the next stage (this should never occur
    # in principle, but in practice in the MIP project there are some
    # inconsistencies and this is the only way to resolve them)
    next_gen_info = read_csv(next_in_path, "gen_info.csv")
    predet = predet.merge(next_gen_info["GENERATION_PROJECT"])

    # Merge with the predetermined construction data from the next stage.
    predet = merge_build_data(predet, "gen_build_predetermined.csv")
    # drop any that are zero or very small
    predet = predet.query(
        "build_gen_predetermined > 0.001 or build_gen_energy_predetermined > 0.001"
    )
    to_csv(predet, chained(next_in_path, "gen_build_predetermined.csv"))

    # use this model's costs for everything that was built and next model's
    # costs for anything in the next period (or later, if we eventually chain
    # multi-period models). This maintains consistency with the new
    # gen_build_predetermined.
    costs = read_csv(possibly_chained(in_path, "gen_build_costs.csv"))
    # drop any that don't match up with capacity being carried forward
    # (e.g., predetermined capacity == 0 or BuildGen == 0)
    # (match using first two cols, however they're capitalized)
    costs = costs.merge(
        predet,
        left_on=costs.columns[:2].to_list(),
        right_on=predet.columns[:2].to_list(),
        how="inner",
    )[costs.columns]
    # drop any that don't appear in the next stage
    costs = costs.merge(next_gen_info["GENERATION_PROJECT"])
    # merge cost data from this model with cost data from the next model
    # (this will use data from this model for projects/build_years from this
    # model and data from the next model for additional projects/build_years)
    costs = merge_build_data(costs, "gen_build_costs.csv")
    # S0 existing_fixed_om by_period (CHANGES §66): the existing generators the next stage lists in
    # existing_fom_by_period.csv take the next stage's own fixed O&M (its period's value) instead of this stage's
    if Path(next_in_path, "existing_fom_by_period.csv").exists():
        costs, n = existing_fom_from_next_stage(
            costs, read_csv(next_in_path, "gen_build_costs.csv"),
            set(read_csv(next_in_path, "existing_fom_by_period.csv").iloc[:, 0].astype(str)))
        print(f"{__name__}: fixed O&M of {n} existing generators from the next stage's own gen_build_costs.csv "
              f"(existing_fom_by_period.csv)")
    # Switch's GEN_BLD_YRS validation only accepts (gen, build_year) pairs that
    # are either in the predetermined set or whose build_year is one of the
    # next stage's own model periods. merge_build_data() above pulls forward
    # *all* of the next stage's own gen_build_costs.csv rows (including
    # zero-capacity placeholder build years from PowerGenome that aren't a
    # real model period), so drop any row here that doesn't satisfy that same
    # rule, to avoid an orphaned build_year that fails validation downstream.
    next_periods = set(read_csv(next_in_path, "periods.csv")["INVESTMENT_PERIOD"])
    predet_keys = set(map(tuple, predet.iloc[:, :2].values))
    build_year_col = costs.columns[1]
    costs = costs[
        costs[build_year_col].isin(next_periods)
        | costs.iloc[:, :2].apply(tuple, axis=1).isin(predet_keys)
    ]
    to_csv(costs, chained(next_in_path, "gen_build_costs.csv"))

    chain_build_rate_inputs(m, in_path, next_in_path, chained, possibly_chained, read_csv, to_csv, commit)

    # combine starting transmission for this case with transmission expansion
    trans = read_csv(possibly_chained(in_path, "transmission_lines.csv"))
    trans_built = read_csv(out_path, "BuildTx.csv")
    if commit is not None:
        trans_built = trans_built[trans_built["TRANS_BLD_YRS_2"] <= commit]
    trans_built = (
        trans_built
        .rename(columns={"TRANS_BLD_YRS_1": "TRANSMISSION_LINE"})
        .groupby("TRANSMISSION_LINE")[["BuildTx"]]
        .sum()
        .reset_index()
    )
    # left join: most lines have no entry in BuildTx.csv (e.g. when expansion
    # is disabled or a line isn't a candidate for expansion), and must still
    # be carried forward with their existing capacity unchanged, not dropped.
    trans = trans.merge(trans_built, how="left")
    trans["existing_trans_cap"] += trans["BuildTx"].fillna(0)
    # round very small capacities to zero (generally occur due to solver
    # rounding and may cause numerical warnings in next stage)
    trans.loc[trans["existing_trans_cap"] < 0.001, "existing_trans_cap"] = 0
    trans = trans.drop(columns=["BuildTx"])
    to_csv(trans, chained(next_in_path, "transmission_lines.csv"))

    # forced transmission: never re-force capacity the chain already built (S0 stages only)
    if commit is not None:
        chain_forced_tx(in_path, next_in_path, trans_built, chained, read_csv, to_csv)

    # carry interconnection headroom forward (study_modules.interconnection_headroom)
    chain_ic_inputs(in_path, out_path, next_in_path, case_name, commit)


def chain_forced_tx(in_path, next_in_path, trans_built, chained, read_csv, to_csv):
    """Safeguard for forced lines in S0 chains (pg_to_switch forces each line only in its own period).

    Keeps trans_built_to_date.chained.<case>.csv (TRANSMISSION_LINE, trans_built_to_date_mw), the new transmission
    each line has had over the chain (committed builds; prm_regional derates it as new), and writes the next stage's trans_build_minimum and trans_path_expansion_limit as
    .chained.<case>.csv: each minimum less what the line already has (floored at 0), and in the forced
    period a limit equal to that minimum (the cap-at-minimum rows) reduced by the same amount. Total new
    capacity on a forced line then never exceeds its forced MW because of a repeated minimum. Nothing is
    written when the next stage has no trans_build_minimum.csv."""
    prev_path = chained(in_path, "trans_built_to_date.csv")          # absent in the first stage
    col = "trans_built_to_date_mw"                 # also read by study_modules.prm_regional (new-line derate)
    to_date = (read_csv(prev_path) if prev_path.exists() else pd.DataFrame(columns=["TRANSMISSION_LINE", col]))
    to_date = (pd.concat([to_date, trans_built[["TRANSMISSION_LINE", "BuildTx"]].rename(columns={"BuildTx": col})],
                         ignore_index=True)
               .groupby("TRANSMISSION_LINE", as_index=False)[col].sum())
    to_csv(to_date, chained(next_in_path, "trans_built_to_date.csv"))
    min_path = Path(next_in_path, "trans_build_minimum.csv")
    if not min_path.exists():
        return
    built = dict(zip(to_date["TRANSMISSION_LINE"].astype(str), to_date[col].astype(float)))
    mins = read_csv(min_path)
    orig = {(str(line), int(pr)): float(v) for line, pr, v in
            zip(mins["TRANSMISSION_LINE"], mins["PERIOD"], mins["trans_build_minimum_mw"])}
    mins["trans_build_minimum_mw"] = [
        max(0.0, float(v) - built.get(str(line), 0.0)) for line, v in zip(mins["TRANSMISSION_LINE"], mins["trans_build_minimum_mw"])]
    mins.loc[mins["trans_build_minimum_mw"] < 0.001, "trans_build_minimum_mw"] = 0.0
    to_csv(mins, chained(next_in_path, "trans_build_minimum.csv"))
    for (line, pr), v in orig.items():
        if built.get(line, 0.0) > 0.001:
            print(f"{__name__}: forced line {line} ({pr}): minimum {v:.1f} MW less {built[line]:.1f} MW already built")
    lim_path = Path(next_in_path, "trans_path_expansion_limit.csv")
    if lim_path.exists():
        lim = read_csv(lim_path)
        new = []
        for line, pr, v in zip(lim["TRANSMISSION_LINE"], lim["PERIOD"], lim["trans_path_expansion_limit_mw"]):
            k = (str(line), int(pr))
            if k in orig and pd.notna(v) and abs(float(v) - orig[k]) < 1e-6:
                v = max(0.0, float(v) - built.get(k[0], 0.0))
            new.append(v)
        lim["trans_path_expansion_limit_mw"] = new
        to_csv(lim, chained(next_in_path, "trans_path_expansion_limit.csv"))


def chain_ic_inputs(in_path, out_path, next_in_path, case_name, commit=None):
    """
    Write ic_zones/ic_tranches/ic_uprates.chained.<case>.csv for the next stage:
    network capacity grows by the stretch-mode uprates built (if any; GETs and
    reconductoring stretch only in the atts_stretch sensitivity), each step keeps only
    its unused width at the new capacity, uprate caps shrink by what was built, and the
    starting saturation includes the headroom bought this stage on the existing network.
    Host-mode uprates (the default for GETs, advanced conductors and conventional
    reinforcement; caps in hosted MW) don't change H: the
    headroom they host and this stage left unused carries forward as
    ic_hosted_headroom_mw (free next stage). Builds from this stage become
    predetermined next stage, so they no longer count against headroom. Does nothing
    when the interconnection_headroom module wasn't used. The *_built.csv outputs are
    cumulative by period; `commit` picks the period handed on (default: the last one in
    the files, the legacy behaviour).
    """
    in_path, out_path, next_in_path = Path(in_path), Path(out_path), Path(next_in_path)

    def src(name):
        p = in_path / f"{name}.chained.{case_name}.csv"
        return p if p.exists() else in_path / f"{name}.csv"

    needed = [src("ic_zones"), src("ic_tranches"), out_path / "ic_tranches_built.csv",
              out_path / "ic_release_built.csv"]
    if not all(p.exists() for p in needed):
        return
    rd = lambda p: pd.read_csv(p, na_values=["."])

    def at_commit(df):
        """Rows of a cumulative *_built.csv for the committed period (default: its last period)."""
        if "period" not in df or not len(df):
            return df
        p = commit if commit is not None else df["period"].max()
        return df[df["period"] == p]

    zones, steps = rd(src("ic_zones")), rd(src("ic_tranches"))
    used = at_commit(rd(out_path / "ic_tranches_built.csv")).set_index("ic_tranche")["used_mw"]
    released = at_commit(rd(out_path / "ic_release_built.csv")).set_index("ic_zone")["released_mw"]
    uprates = rd(src("ic_uprates")) if src("ic_uprates").exists() else None
    up_built = (at_commit(rd(out_path / "ic_uprates_built.csv")).set_index("ic_uprate")["built_mw"]
                if (out_path / "ic_uprates_built.csv").exists() else pd.Series(dtype=float))

    added = pd.Series(0.0, index=zones["IC_ZONE"])
    if uprates is not None and len(uprates):
        b = uprates["IC_UPRATE"].map(up_built).fillna(0)
        mode = uprates["ic_uprate_mode"].fillna("stretch") if "ic_uprate_mode" in uprates else "stretch"
        stretch = b.where(pd.Series(mode, index=uprates.index) == "stretch", 0.0)
        added = added.add(stretch.groupby(uprates["ic_uprate_zone"]).sum(), fill_value=0)
        uprates["ic_uprate_max_mw"] = (uprates["ic_uprate_max_mw"] - b).clip(lower=0).round(3)
        uprates.to_csv(next_in_path / f"ic_uprates.chained.{case_name}.csv", index=False, na_rep=".")

    h0 = zones.set_index("IC_ZONE")["ic_base_capacity_mw"]
    h1 = h0 + added.reindex(h0.index).fillna(0)
    step_used = steps["IC_TRANCHE"].map(used).fillna(0)
    bought = step_used.groupby(steps["ic_tranche_zone"]).sum().reindex(h0.index).fillna(0) \
        + released.reindex(h0.index).fillna(0)
    s0 = zones.set_index("IC_ZONE")["ic_start_saturation"]
    zones = zones.set_index("IC_ZONE")
    zones["ic_base_capacity_mw"] = h1.round(3)
    zones["ic_start_saturation"] = ((s0 * h0 + bought) / h1.where(h1 > 0)).fillna(0).round(6)
    hosted_file = out_path / "ic_hosted_built.csv"
    if hosted_file.exists():
        unused = at_commit(rd(hosted_file)).set_index("ic_zone")["unused_mw"]
        zones["ic_hosted_headroom_mw"] = unused.reindex(zones.index).fillna(0).clip(lower=0).round(3)
    zones.reset_index().to_csv(next_in_path / f"ic_zones.chained.{case_name}.csv", index=False, na_rep=".")

    hz = steps["ic_tranche_zone"].map(h1)
    steps["ic_tranche_width"] = (steps["ic_tranche_width"] - step_used / hz.where(hz > 0)).fillna(
        steps["ic_tranche_width"]).clip(lower=0).round(6)
    steps.to_csv(next_in_path / f"ic_tranches.chained.{case_name}.csv", index=False, na_rep=".")

    # headroom scenario switch between stages (S0 levels_by_period; ic_scenario_switch.csv in the next stage)
    if (next_in_path / "ic_scenario_switch.csv").exists():
        switch_ic_scenario(in_path, next_in_path, case_name, uprates, rd)


def switch_ic_scenario(in_path, next_in_path, case_name, uprates_after, rd):
    """The next stage uses another headroom scenario (ic_scenario_switch.csv). The ATTS scenarios share the zones and
    the empirical curve (ic_zones / ic_tranches) and differ in the deliberate uprates, so the network state carried
    above stands and the next stage gets ITS scenario's uprates, each less what the chain has built of it so far
    (matched by IC_UPRATE; built so far = this stage's starting cap (base file) - its cap after this stage). A switch
    between scenarios whose zones or curve differ is not supported: it stops rather than mix two curves."""
    for name in ("ic_zones", "ic_tranches"):
        a, b = rd(in_path / f"{name}.csv"), rd(next_in_path / f"{name}.csv")
        if not a.reset_index(drop=True).equals(b.reset_index(drop=True)):
            sw = rd(next_in_path / "ic_scenario_switch.csv").iloc[0]
            raise NotImplementedError(
                f"{__name__}: headroom scenario switch {sw.from_scenario} -> {sw.to_scenario}: {name}.csv differs between "
                f"the two scenarios; only switches between scenarios with the same zones and curve are supported")
    if not (next_in_path / "ic_uprates.csv").exists():
        return
    new = rd(next_in_path / "ic_uprates.csv")
    if uprates_after is not None and (in_path / "ic_uprates.csv").exists():
        base = rd(in_path / "ic_uprates.csv").set_index("IC_UPRATE")["ic_uprate_max_mw"]
        after = uprates_after.set_index("IC_UPRATE")["ic_uprate_max_mw"]
        built = (base - after.reindex(base.index).fillna(base)).clip(lower=0)
        new["ic_uprate_max_mw"] = (new["ic_uprate_max_mw"] - new["IC_UPRATE"].map(built).fillna(0)).clip(lower=0).round(3)
    new.to_csv(next_in_path / f"ic_uprates.chained.{case_name}.csv", index=False, na_rep=".")

class Test:
    """
    generic object that can be assigned any attributes needed
    """

    pass


if __name__ == "__main__":
    # called from command line or run interactively in VS Code
    m = Test()
    m.options = Test()
    if len(sys.argv) == 3:
        # called from command line with inputs-dir and outputs-dir
        # run a test case using the specified inputs and outputs directories
        m.options.inputs_dir = sys.argv[1]
        m.options.outputs_dir = sys.argv[2]
        post_solve(m, m.options.outputs_dir)
    elif (
        len(sys.argv) == 2 and os.path.basename(sys.argv[0]) == "ipykernel_launcher.py"
    ):
        # running interactively from VS Code
        io_dir = "2024-08-04"
        year = "2030"
        case = "base_short_retire"
        root = os.path.join(os.path.dirname(__file__), "..", io_dir)
        m.options.inputs_dir = os.path.join(root, "in", year, case)
        m.options.outputs_dir = os.path.join(root, "out", year, case)
        outdir = m.options.outputs_dir
        print("Run the contents of post_solve() interactively to see results.")
    else:
        raise RuntimeError(
            "usage: python prepare_next_stage.py <inputs-dir> <outputs-dir>"
        )


def chain_build_rate_inputs(m, in_path, next_in_path, chained, possibly_chained, read_csv, to_csv,
                            commit=None):
    """Carry the best build rate achieved so far into the next myopic stage (study_modules.build_rate).

    Writes build_rate_prev_build.chained.<case>.csv in the next stage's inputs dir with, per group,
    the larger of this stage's new build per year in its last period (bounded foresight: the
    committed period) and any rate chained in from earlier stages, so the ramp bound never falls
    below the best rate already achieved. No-op unless the build_rate module is active.
    """
    if not hasattr(m, "BR_GROUP_PERIODS") or not len(m.BR_GROUP_PERIODS):
        return
    from pyomo.environ import value

    last = m.PERIODS.last() if commit is None else commit
    rates = {grp: value(m.BRNewBuild[grp, p]) / value(m.br_window_years[p])
             for (grp, p) in m.BR_GROUP_PERIODS if p == last}
    prev_path = possibly_chained(in_path, "build_rate_prev_build.csv")
    if prev_path.exists():
        for _, r in read_csv(prev_path).iterrows():
            rates[r["BR_GROUP"]] = max(rates.get(r["BR_GROUP"], 0.0), float(r["br_prev_rate_mw_per_yr"]))
    to_csv(pd.DataFrame({"BR_GROUP": list(rates), "br_prev_rate_mw_per_yr": list(rates.values())}),
           chained(next_in_path, "build_rate_prev_build.csv"))

