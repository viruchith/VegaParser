"""Guards for the offline orientation evaluation (scripts/evaluate_orientation.py)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "evaluation"


@pytest.fixture(scope="module")
def evaluator():
    spec = importlib.util.spec_from_file_location(
        "evaluate_orientation", REPO_ROOT / "scripts" / "evaluate_orientation.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look up their module in sys.modules when loaded from a file path.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def truth():
    return json.loads((EVAL_DIR / "ground_truth.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def questions():
    return json.loads((EVAL_DIR / "questions.json").read_text(encoding="utf-8"))


def test_ground_truth_points_at_existing_corpus_files(truth):
    for name, repo in truth.items():
        root = REPO_ROOT / repo["root"]
        assert root.is_dir(), f"{name}: missing corpus root {root}"
        files = set(repo["files"])
        for rel in files:
            assert (root / rel).is_file(), f"{name}: expected file missing: {rel}"
        for key in repo["symbols"]:
            assert key.split("::")[0] in files, f"{name}: symbol outside file list: {key}"
        for src, dst in repo["dependencies"]:
            assert src in files and dst in files, f"{name}: dependency outside file list: {src} -> {dst}"
        for path, _url in repo["urls"]:
            assert path in files, f"{name}: URL outside file list: {path}"


def test_questions_are_well_formed(questions, truth):
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids)), "question ids must be unique"
    assert 10 <= len(questions) <= 20, "workstream 1 asks for 10-20 orientation questions"
    for q in questions:
        assert q["repo"] in truth, f"{q['id']}: unknown repo {q['repo']}"
        files = set(truth[q["repo"]]["files"])
        assert q["expected_files"], f"{q['id']}: needs at least one expected file"
        assert set(q["expected_files"]) <= files, f"{q['id']}: expected file not in corpus"
        for key in q["expected_symbols"]:
            assert key.split("::")[0] in files, f"{q['id']}: symbol outside corpus: {key}"


def test_score_counts_and_ratios(evaluator):
    s = evaluator.score({"a", "b"}, {"b", "c"})
    assert (s.tp, s.fp, s.fn) == (1, 1, 1)
    assert s.precision == pytest.approx(0.5)
    assert s.recall == pytest.approx(0.5)


def test_score_with_nothing_produced_is_zero_not_error(evaluator):
    s = evaluator.score(set(), {"a"})
    assert s.precision == 0.0
    assert s.recall == 0.0


def test_keyword_pack_is_deterministic_and_bounded(evaluator):
    root = REPO_ROOT / "tests" / "eval_corpus" / "python_app"
    parsed, _ = evaluator.parse_repo(root)
    first = evaluator.keyword_pack("Where are orders created and stored?", parsed, k=3)
    second = evaluator.keyword_pack("Where are orders created and stored?", parsed, k=3)
    assert first == second
    assert 0 < len(first) <= 3
