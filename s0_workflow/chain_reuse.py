"""Reuse unchanged leading stages of a mode-A chain from a reference chain (CHANGES §71).

A scenario chain (e.g. BILL_central) often has 2028 and 2030 stages identical to its reference's (S0prod_A). Instead of
re-solving them, `reuse()` checks each leading stage in order and, while it matches exactly, copies the reference's
stage outputs into the scenario's outputs folder and hands over to the next stage as if it had been solved. The chain
runner then starts at the first differing stage (a scenarios file holding only the remaining lines is written).

A stage matches only if all of these hold:
- **inputs:** every file of the stage's inputs folder (top level; build logs and scenario files aside, see
  EXCLUDE) has the same content (ORDER_INSENSITIVE program files: the same rows in any order), under the same name once the case name in `*.chained.<case>.*` is normalised. This
  includes the chained files the previous stage handed over;
- **aliases:** the same `--input-alias(es)` pairs (normalised the same way), so the same files are read;
- **options:** the rest of the scenario line is the same (modules, flags), apart from `--scenario-name`,
  `--inputs-dir` and `--outputs-dir`;
- **code:** the same git head as the reference's recorded one, or every commit since then leaves CODE_PATHS
  (Switch modules, module lists, input writing) alone, and the working tree has no changes there. Opt-in
  `code_scope="model"` (`--code-check model`, §80) checks only MODEL_CODE_PATHS (Switch modules, module lists,
  options): input-writing code is covered by the input comparison;
- **solver:** the same solver version and solver arguments as the reference's record.

The reference's code head, solver version and arguments come from `chain_provenance.json` in each of its stage
outputs folders, written by `record()` (run once for a solved reference chain).

Handover after a reused stage: study_modules.prepare_next_stage runs on the copied outputs (it works from written
outputs, not the model), in the Python the `switch` command runs under (pandas versions order rows differently;
`python=` to choose), writing the next stage's `*.chained.<scenario>.csv`. The one model-dependent file,
`build_rate_prev_build.chained.<case>.csv` (from the solved model's BRNewBuild), is copied from the reference's next
stage, renamed: it depends only on the reused stage's solution, which is the reference's.

Provenance: `reuse_provenance.json` in each reused stage's outputs folder, and one row per stage (reused, or the
first to solve) in `handoff_runs.csv` (default: the scenario's outputs root, next to the stage folders).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SWITCH_DIR = REPO / "switch"
# not inputs: the case build's log and scenario lists in the inputs folder, run logs, this tool's records
EXCLUDE = ("s0_production_log.txt", "scenarios*.txt", "*.log", "reuse_provenance.json", "chain_provenance.json")
# code a reused solution depends on: Switch modules and module lists, and everything that writes inputs
CODE_PATHS = ("switch/study_modules/", "switch/modules.txt", "switch/options.txt", "pg_to_switch.py", "s0_workflow/",
              "build_rate/", "interconnection_headroom/", "adjust/", "pg/settings/", "pg/extra_inputs/",
              "conversion_functions.py", "utilities.py", "PowerGenome/")
CODE_EXEMPT = ("*/tests/*", "*.md", "s0_workflow/scripts/*")
# code_scope="model": only the code that builds and solves the model from the inputs (Switch modules, module lists and
# options). Input-writing code (pg_to_switch, s0_workflow, build_rate, settings...) can be left out of the check
# because every input file is compared by content: a change there that alters a stage changes its inputs. Opt-in
# (§80); the default ("all") is §71's rule
MODEL_CODE_PATHS = ("switch/study_modules/", "switch/modules.txt", "switch/options.txt")
PROVENANCE = "chain_provenance.json"
REUSED = "reuse_provenance.json"
MODEL_DEPENDENT = ("build_rate_prev_build",)   # chained files prepare_next_stage writes from model values
# files that are sets of rows (program membership and limits): compared with their data rows sorted, so a different
# row order from an unordered write (e.g. max_cap_generators.csv before pg_to_switch sorted it) still matches. Files
# whose row order defines the model (timepoints, timeseries) are compared byte for byte
ORDER_INSENSITIVE = ("max_cap_generators.csv", "max_cap_requirements.csv", "min_cap_generators.csv",
                     "min_cap_requirements.csv", "rps_generators.csv", "rps_requirements.csv")
# solver arguments that name where temporary files go, not how the model is solved: left out of the comparison
SOLVER_ARGS_IGNORED = ("--tempdir",)


# --------------------------------------------------------------------------------------------- scenario lines
def parse_line(line: str) -> dict:
    """One scenario line -> inputs/outputs dirs, scenario name, alias pairs and the other options (in order)."""
    tok, out, aliases, i = shlex.split(line), {"options": []}, [], 0
    while i < len(tok):
        t = tok[i]
        if t in ("--scenario-name", "--inputs-dir", "--outputs-dir"):
            out[t[2:].replace("-", "_")] = tok[i + 1]
            i += 2
        elif t in ("--input-alias", "--input-aliases"):
            i += 1
            while i < len(tok) and not tok[i].startswith("--"):
                aliases.append(tok[i])
                i += 1
        else:
            out["options"].append(t)
            i += 1
    out["aliases"] = dict(a.split("=", 1) for a in aliases)
    return out


def read_chain(scenarios_file: Path, switch_dir: Path = SWITCH_DIR) -> list[dict]:
    """The stages of a chain from its scenarios file (one line per stage, in order). Paths in the lines are
    relative to switch/ (as pg_to_switch writes them)."""
    stages = []
    for line in Path(scenarios_file).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        p = parse_line(line)
        inp, out = Path(switch_dir) / p["inputs_dir"], Path(switch_dir) / p["outputs_dir"]
        stages.append(dict(p, line=line.strip(), inputs=inp, outputs=out, stage=inp.parent.name, case=out.name))
    return stages


def _norm_name(name: str, case: str) -> str:
    return name.replace(f".chained.{case}.", ".chained.{case}.")


def _excluded(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def content_hash(path: Path) -> str:
    """sha256 of the file; for ORDER_INSENSITIVE files, of the header and the sorted data rows (line endings
    normalised)."""
    if Path(path).name not in ORDER_INSENSITIVE:
        return sha256(path)
    lines = Path(path).read_bytes().replace(b"\r\n", b"\n").split(b"\n")
    lines = [ln for ln in lines if ln.strip()]
    body = lines[:1] + sorted(lines[1:])
    return "rows:" + hashlib.sha256(b"\n".join(body)).hexdigest()


def fingerprint(stage: dict, chained: bool = True) -> dict:
    """{file: hash} of the stage's inputs (top-level files, names normalised), its alias pairs and other options.
    chained=False leaves out the chained files (they don't exist yet before the previous stage is handed over)."""
    files = {}
    for f in sorted(Path(stage["inputs"]).iterdir()):
        if f.is_file() and not _excluded(f.name) and (chained or ".chained." not in f.name):
            files[_norm_name(f.name, stage["case"])] = content_hash(f)
    aliases = {k: _norm_name(v, stage["case"]) for k, v in stage["aliases"].items()
               if chained or ".chained." not in v}
    return {"files": files, "aliases": aliases, "options": stage["options"]}


def first_difference(a: dict, b: dict) -> str | None:
    """The first difference between two fingerprints (scenario a, reference b), or None."""
    if a["options"] != b["options"]:
        return f"options: {' '.join(a['options'])!r} vs {' '.join(b['options'])!r}"
    for k in sorted(set(a["aliases"]) | set(b["aliases"])):
        if a["aliases"].get(k) != b["aliases"].get(k):
            return f"alias {k}: {a['aliases'].get(k)} vs {b['aliases'].get(k)}"
    for k in sorted(set(a["files"]) | set(b["files"])):
        if k not in a["files"]:
            return f"file {k}: only in the reference"
        if k not in b["files"]:
            return f"file {k}: only in the scenario"
        if a["files"][k] != b["files"][k]:
            return f"file {k}: contents differ"
    return None


def digest(fp: dict) -> str:
    return hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------------------------- code and solver
def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def is_code(path: str, scope: str = "all") -> bool:
    paths = MODEL_CODE_PATHS if scope == "model" else CODE_PATHS
    return path.startswith(paths) and not any(fnmatch.fnmatch(path, p) for p in CODE_EXEMPT)


def code_check(ref_head: str, scope: str = "all") -> tuple[bool, str, list[str]]:
    """(ok, reason, commits since ref_head). OK when HEAD is ref_head, or ref_head is an ancestor and no commit since
    touches the code paths (scope "all": CODE_PATHS; "model": MODEL_CODE_PATHS); and the working tree has no
    uncommitted change there."""
    if scope not in ("all", "model"):
        raise ValueError(f"code scope must be all or model, not {scope!r}")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True,
                            check=True).stdout
    dirty = [ln[3:] for ln in status.splitlines() if is_code(ln[3:].strip('"'), scope)]
    if dirty:
        return False, f"uncommitted changes in code paths: {', '.join(dirty[:5])}", []
    head = _git("rev-parse", "HEAD")
    if not ref_head:
        return False, "the reference has no recorded code head (run `record` for it)", []
    ref_full = _git("rev-parse", ref_head)
    if ref_full == head:
        return True, "same code head", []
    if subprocess.run(["git", "merge-base", "--is-ancestor", ref_full, head], cwd=REPO).returncode != 0:
        return False, f"the reference's head {ref_head[:9]} is not an ancestor of HEAD {head[:9]}", []
    commits = _git("log", "--format=%h %s", f"{ref_full}..{head}").splitlines()
    touched = [f for f in _git("diff", "--name-only", ref_full, head).splitlines() if is_code(f, scope)]
    what = "model code paths" if scope == "model" else "code paths"
    if touched:
        return False, f"commits since {ref_head[:9]} change {what}: {', '.join(touched[:5])}", commits
    return True, f"{len(commits)} commit(s) since {ref_head[:9]}, none in {what}", commits


def detect_solver_version() -> str | None:
    try:
        import gurobipy
        return "gurobi " + ".".join(str(x) for x in gurobipy.gurobi.version())
    except Exception:
        return None


def _norm_args(s: str | None) -> str:
    """Solver arguments as one normalised string, without SOLVER_ARGS_IGNORED (--tempdir X or --tempdir=X: where
    temporary files go differs between runs and machines)."""
    tok, out, i = shlex.split(s or ""), [], 0
    while i < len(tok):
        t = tok[i]
        if t in SOLVER_ARGS_IGNORED:
            i += 2
            continue
        if any(t.startswith(a + "=") for a in SOLVER_ARGS_IGNORED):
            i += 1
            continue
        out.append(t)
        i += 1
    return " ".join(out)


def record(scenarios_file: Path, git_head: str | None = None, solver_version: str | None = None,
           solver_args: str = "", switch_dir: Path = SWITCH_DIR) -> list[Path]:
    """Write chain_provenance.json in each solved stage's outputs folder of a chain, for later reuse: code head
    (default: the current HEAD; give the head the chain was solved at for an older chain), solver version (default:
    detected) and solver arguments (the --solver/--solver-options-string the runner adds), and the input digest."""
    head = _git("rev-parse", git_head or "HEAD")
    version = solver_version or detect_solver_version()
    written = []
    for st in read_chain(scenarios_file, switch_dir):
        if not (st["outputs"] / "total_cost.txt").exists():
            continue
        rec = {"git_head": head, "solver_version": version, "solver_args": _norm_args(solver_args),
               "stage": st["stage"], "case": st["case"], "input_digest": digest(fingerprint(st)),
               "recorded": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        (st["outputs"] / PROVENANCE).write_text(json.dumps(rec, indent=1))
        written.append(st["outputs"] / PROVENANCE)
    return written


# --------------------------------------------------------------------------------------------- handover
HANDOVER = r"""
import importlib.util, sys
from types import SimpleNamespace
spec = importlib.util.spec_from_file_location("prepare_next_stage_reuse", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
m = SimpleNamespace(options=SimpleNamespace(inputs_dir=sys.argv[2], outputs_dir=sys.argv[3]))
mod.post_solve(m, sys.argv[3])
"""


def switch_python() -> str:
    """The Python the `switch` command runs under (its shebang), so the handover writes exactly what a solve's
    prepare_next_stage would (pandas versions order and format rows differently); else this interpreter."""
    exe = shutil.which("switch")
    if exe:
        try:
            first = Path(exe).read_text(errors="ignore").splitlines()[0]
            if first.startswith("#!") and Path(first[2:].strip().split()[0]).exists():
                return first[2:].strip().split()[0]
        except (OSError, IndexError):
            pass
    import sys
    return sys.executable


def run_prepare_next_stage(inputs: Path, outputs: Path, python: str | None = None) -> None:
    r = subprocess.run([python or switch_python(), "-c", HANDOVER, str(SWITCH_DIR / "study_modules/prepare_next_stage.py"),
                        str(inputs), str(outputs)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"prepare_next_stage on {outputs} failed:\n{r.stderr[-3000:]}")


def hand_over(st: dict, nxt: dict, ref_nxt: dict, python: str | None = None) -> list[str]:
    """prepare_next_stage on the (copied) outputs of st into nxt's inputs, in Switch's Python, plus the
    model-dependent chained files copied from the reference's next stage. Returns the files written or copied."""
    before = {f.name: f.stat().st_mtime_ns for f in nxt["inputs"].iterdir()}
    run_prepare_next_stage(st["inputs"], st["outputs"], python)
    done = sorted(f.name for f in nxt["inputs"].iterdir()
                  if ".chained." in f.name and before.get(f.name) != f.stat().st_mtime_ns)
    for stem in MODEL_DEPENDENT:
        src = ref_nxt["inputs"] / f"{stem}.chained.{ref_nxt['case']}.csv"
        if src.exists():
            dst = nxt["inputs"] / f"{stem}.chained.{nxt['case']}.csv"
            shutil.copyfile(src, dst)
            done.append(dst.name + " (copied from the reference)")
    return done


def _copy_outputs(src: Path, dst: Path, link: bool) -> None:
    if dst.exists() and any(dst.iterdir()):
        raise FileExistsError(f"{dst} exists and is not empty: reuse writes into fresh output folders only")
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.rglob("*"):
        if f.name == PROVENANCE:
            continue
        t = dst / f.relative_to(src)
        if f.is_dir():
            t.mkdir(parents=True, exist_ok=True)
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        if link:
            try:
                os.link(f, t)
                continue
            except OSError:
                pass
        shutil.copy2(f, t)


def _append_runs(path: Path, rows: list[dict]) -> None:
    new = pd.DataFrame(rows)
    if path.exists():
        new = pd.concat([pd.read_csv(path, dtype=str, keep_default_na=False), new.astype(str)], ignore_index=True)
    new.to_csv(path, index=False)


# --------------------------------------------------------------------------------------------- reuse
def reuse(scenarios_file: Path, reference_file: Path, through: int | None = None, solver_args: str = "",
          solver_version: str | None = None, link: bool = False, dry_run: bool = False,
          handoff_runs: Path | None = None, switch_dir: Path = SWITCH_DIR, log=print, python: str | None = None,
          code_scope: str = "all") -> dict:
    """Reuse the reference chain's leading stages that match the scenario's exactly (see the module docstring).
    Returns {"reused": [stage, ...], "start": first stage to solve or None, "reason": why reuse stopped,
    "remaining_file": scenarios file with the lines from `start` on}."""
    scen, ref = read_chain(scenarios_file, switch_dir), read_chain(reference_file, switch_dir)
    out = {"reused": [], "start": scen[0]["stage"] if scen else None, "reason": "", "remaining_file": None,
           "commits_since": []}

    def stop(reason):
        out["reason"] = reason
        log(f"reuse stops at {out['start']}: {reason}")

    rec = {}
    if ref and (ref[0]["outputs"] / PROVENANCE).exists():
        rec = json.loads((ref[0]["outputs"] / PROVENANCE).read_text())
    ok, why, commits = code_check(rec.get("git_head", ""), code_scope)
    out["commits_since"] = commits
    version = solver_version or detect_solver_version()
    runs, k = [], 0
    if not rec:
        stop(f"no {PROVENANCE} in {ref[0]['outputs'] if ref else reference_file} (run `record` for the reference)")
    elif not ok:
        stop(f"code: {why}")
    elif (version or "") != (rec.get("solver_version") or ""):
        stop(f"solver version {version} vs the reference's {rec.get('solver_version')}")
    elif _norm_args(solver_args) != _norm_args(rec.get("solver_args", "")):   # records may hold a --tempdir
        stop(f"solver arguments {_norm_args(solver_args)!r} vs the reference's {rec.get('solver_args')!r}")
    else:
        log(f"code: {why}; solver {version} {_norm_args(solver_args)!r}")
        for k, (s, r) in enumerate(zip(scen, ref)):
            out["start"] = s["stage"]
            if through is not None and int(str(s["stage"]).split("_")[0]) > int(through):
                stop(f"--reuse-through {through}")
                break
            if s["stage"] != r["stage"]:
                stop(f"stage {s['stage']} vs the reference's {r['stage']}")
                break
            rrec = (json.loads((r["outputs"] / PROVENANCE).read_text()) if (r["outputs"] / PROVENANCE).exists()
                    else {})
            if not (r["outputs"] / "total_cost.txt").exists() or rrec.get("git_head") != rec.get("git_head"):
                stop(f"the reference's {r['stage']} is not solved and recorded ({r['outputs']})")
                break
            # dry run: chained inputs are not handed over yet; they follow from the identical earlier stages
            fs, fr = fingerprint(s, chained=not dry_run or k == 0), fingerprint(r, chained=not dry_run or k == 0)
            diff = first_difference(fs, fr)
            if diff:
                stop(f"first difference: {diff}")
                break
            if digest(fr) != rrec.get("input_digest") and not dry_run:
                stop(f"the reference's {r['stage']} inputs changed since it was solved")
                break
            out["reused"].append(s["stage"])
            if dry_run:
                log(f"{s['stage']}: inputs match ({len(fs['files'])} files) — would reuse")
                continue
            _copy_outputs(r["outputs"], s["outputs"], link)
            prov = {"reused_from": str(r["outputs"]), "reference_case": r["case"], "stage": s["stage"],
                    "input_digest": digest(fs), "files_compared": len(fs["files"]), "code_scope": code_scope,
                    "git_head": _git("rev-parse", "HEAD"),
                    "reference_git_head": rec["git_head"], "commits_since": commits, "solver_version": version,
                    "solver_args": _norm_args(solver_args), "linked": link,
                    "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if k + 1 < len(scen) and k + 1 < len(ref):
                prov["handed_over"] = hand_over(s, scen[k + 1], ref[k + 1], python)
            (s["outputs"] / REUSED).write_text(json.dumps(prov, indent=1))
            # the reused stage's own record, so this chain can serve as a reference in turn
            (s["outputs"] / PROVENANCE).write_text(json.dumps(dict(rrec, case=s["case"], input_digest=digest(
                fingerprint(s)), reused_from=str(r["outputs"])), indent=1))
            runs.append({"scenario": s["case"], "stage": s["stage"], "action": "reused", "reference": r["case"],
                         "reference_outputs": str(r["outputs"]), "input_digest": prov["input_digest"],
                         "git_head": prov["git_head"], "reference_git_head": rec["git_head"],
                         "solver_version": version, "time": prov["time"], "note": ""})
            log(f"{s['stage']}: reused from {r['outputs']} ({len(fs['files'])} input files identical)")
        else:
            k = len(scen)
            if len(scen) > len(ref):
                k = len(ref)
                out["start"] = scen[k]["stage"]
                stop("the reference has no further stages")
            else:
                out["start"], out["reason"] = None, "every stage reused"
    if out["start"] is not None and not dry_run:
        idx = next(i for i, s in enumerate(scen) if s["stage"] == out["start"])
        rem = Path(scenarios_file).with_name(f"{Path(scenarios_file).stem}.from_{out['start']}.txt")
        rem.write_text("".join(s["line"] + "\n" for s in scen[idx:]))
        out["remaining_file"] = rem
        if out["reused"]:
            runs.append({"scenario": scen[idx]["case"], "stage": out["start"], "action": "solve",
                         "reference": ref[0]["case"] if ref else "", "reference_outputs": "", "input_digest": "",
                         "git_head": _git("rev-parse", "HEAD"), "reference_git_head": rec.get("git_head", ""),
                         "solver_version": version, "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "note": out["reason"]})
    if runs:
        path = handoff_runs or (scen[0]["outputs"].parents[1] / "handoff_runs.csv")
        _append_runs(Path(path), runs)
        out["handoff_runs"] = Path(path)
    return out


# --------------------------------------------------------------------------------------------- expected reuse
def _merged(ax: dict, year_ax: dict, row: pd.Series, cols: list[str], base_s0: dict, year: int) -> dict:
    """The settings a scenario_inputs row gives in a model year: s0_production.yml, then each axis value's all-years
    and year overrides (as pg_to_switch merges them), then (as production.apply_settings) s0_production.settings over
    them and levels_by_period applied for the year."""
    from s0_workflow import production as s0prod
    out = {"s0_production": json.loads(json.dumps(base_s0, default=str))}
    for c in cols:
        v = row[c]
        key = None if pd.isna(v) else (int(v) if isinstance(v, float) and v.is_integer() else v)
        for sec in (ax, year_ax):
            opts = sec.get(c)
            if isinstance(opts, dict):
                for k in (key, str(key)):
                    if k in opts and opts[k]:
                        out = s0prod.deep_merge(out, json.loads(json.dumps(opts[k], default=str)))
                        break
    s0 = out["s0_production"]
    if s0.get("enabled"):          # as production.apply_settings: s0_production.settings over the case's settings
        out = s0prod.deep_merge(out, json.loads(json.dumps(s0.get("settings") or {}, default=str)))
        s0 = out["s0_production"]
    s0prod.apply_levels_by_period(out, s0, year=year)
    s0.pop("levels_by_period", None)
    return out


def _is_year(k) -> bool:
    return str(k).isdigit() and 2000 <= int(k) <= 2100


def _at_year(x, year: int, key: str = ""):
    """Collapse period-keyed mappings ({2028: a, 2035: b}: each holds until the next, earlier years take the first)
    to the value in `year`, and *_first_period / *from_period years to whether they are in force in `year`."""
    if isinstance(x, dict):
        ks = list(x)
        if ks and all(_is_year(k) for k in ks):
            le = [k for k in ks if int(k) <= year]
            pick = max(le, key=int) if le else min(ks, key=int)
            return _at_year(x[pick], year, key)
        return {k: _at_year(v, year, str(k)) for k, v in x.items()}
    if isinstance(x, list):
        return [_at_year(v, year) for v in x]
    if key.endswith(("first_period", "from_period")) and _is_year(x):
        return f"in force ({x})" if int(x) <= year else "not yet"
    return x


def _diff_paths(a, b, path="") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            out += _diff_paths(a.get(k), b.get(k), f"{path}.{k}" if path else str(k))
        return out
    return [] if a == b else [f"{path}: {b!r} -> {a!r}"]


# settings that act on the whole chain at case build (so an early stage can differ because of a later year's value)
CHAIN_WIDE = ("forced_tx",)


def expected_reuse(scenario_inputs: Path, management: Path, reference: str = "S0prod_A",
                   cases: list[str] | None = None, s0_yml: Path = REPO / "pg/settings/s0_production.yml") -> pd.DataFrame:
    """From the case definitions: for each chain case with the reference's years and each stage, whether its settings
    in that year equal the reference's (s0_production.yml plus the case's axis values, levels_by_period and
    period-keyed values taken at the year). Expected reusable stages are the leading run of matches. A case with a
    chain-wide setting (CHAIN_WIDE, e.g. forced_tx: the forced period is chosen over the whole chain) that differs in
    any year matches in no stage. Runtime hashing decides; this is the plan."""
    import yaml
    si = pd.read_csv(scenario_inputs)
    sm = yaml.safe_load(open(management))["settings_management"]
    base = yaml.safe_load(open(s0_yml))["s0_production"]
    ax = sm.get("all_years", {})
    cols = [c for c in si.columns if c not in ("case_id", "year")]
    ref = si[si.case_id == reference].set_index("year")
    years = sorted(int(y) for y in ref.index)
    rows = []
    cases = cases or [c for c in si.case_id.unique() if c != reference
                      and sorted(si[si.case_id == c].year) == years]
    for c in cases:
        sc = si[si.case_id == c].set_index("year")
        if sorted(int(y) for y in sc.index) != years:
            rows.append({"case": c, "stage": "", "same_settings": False, "differences": "not the reference's years",
                         "expected_reuse": False})
            continue
        chain = sorted({k for y in years for k in CHAIN_WIDE if str(sc.loc[y, k]) != str(ref.loc[y, k])})
        prefix = True
        for y in years:
            a = _at_year(_merged(ax, sm.get(y, {}), sc.loc[y], cols, base, y), y)
            b = _at_year(_merged(ax, sm.get(y, {}), ref.loc[y], cols, base, y), y)
            d = _diff_paths(a, b) + [f"{k} (chain-wide)" for k in chain]
            prefix = prefix and not d
            rows.append({"case": c, "stage": y, "same_settings": not d, "differences": "; ".join(d[:6]),
                         "expected_reuse": prefix})
    return pd.DataFrame(rows)
