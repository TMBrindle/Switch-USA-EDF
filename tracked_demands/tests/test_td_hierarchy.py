"""hierarchy.csv lookup (Stage 10 regions, the NY state rule): a case's own copy wins; otherwise the working
directory's, then the repo root's (found from the module's folder), so a run that starts in switch/ with its case
folder outside the repo still finds it (§140; the VM mechanics test)."""
from __future__ import annotations

import importlib.util
import os

import pytest

from td_toy import REPO


@pytest.fixture(scope="module")
def td_module():
    spec = importlib.util.spec_from_file_location("td_repo_module", REPO / "switch/study_modules/tracked_demands.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repo_root_from_a_case_outside_the_repo(td_module, tmp_path, monkeypatch):
    case = tmp_path / "runs" / "in" / "2035" / "S0prod_A"
    case.mkdir(parents=True)
    monkeypatch.chdir(REPO / "switch")            # where a run starts
    assert td_module._find_hierarchy_file(str(case)) == str(REPO / "hierarchy.csv")
    monkeypatch.chdir(tmp_path)                   # even from elsewhere: the module's own folder reaches the repo root
    assert td_module._find_hierarchy_file(str(case)) == str(REPO / "hierarchy.csv")
    assert td_module._find_hierarchy_file(None) == str(REPO / "hierarchy.csv")


def test_a_case_copy_wins(td_module, tmp_path, monkeypatch):
    case = tmp_path / "in" / "2035" / "case"
    case.mkdir(parents=True)
    (tmp_path / "hierarchy.csv").write_text("ba,st\nz1,NY\n")
    monkeypatch.chdir(REPO / "switch")
    assert td_module._find_hierarchy_file(str(case)) == str(tmp_path / "hierarchy.csv")


def test_relative_inputs_dir_from_switch(td_module, monkeypatch):
    """switch/'s relative inputs dir, as the scenario lines give it."""
    monkeypatch.chdir(REPO / "switch")
    assert os.path.samefile(td_module._find_hierarchy_file("in/2035/no_such_case"), REPO / "hierarchy.csv")
