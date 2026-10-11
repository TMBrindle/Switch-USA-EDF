"""v3.2 (CHANGES §109): make_generator_variability is called directly, not through the joblib cache. The cache cost
about 6.5 GB per build for about 2.3 s per stage and was never reused (its key followed generator columns that never
reach the written files). kmeans_time_clustering keeps its cache."""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _module_assignments():
    tree = ast.parse((REPO / "pg_to_switch.py").read_text())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = ast.unparse(node.value)
    return tree, out


def test_make_generator_variability_not_cached():
    tree, assigns = _module_assignments()
    assert "make_generator_variability" not in assigns                     # the imported PowerGenome function itself
    assert assigns["kmeans_time_clustering"] == "memory.cache(kmeans_time_clustering)"   # unchanged
    imported = {a.name for n in tree.body if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "make_generator_variability" in imported
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == "make_generator_variability"]
    assert len(calls) == 1 and ast.unparse(calls[0]) == "make_generator_variability(period_gens)"
    assert "cache(make_generator_variability" not in (REPO / "pg_to_switch.py").read_text()
