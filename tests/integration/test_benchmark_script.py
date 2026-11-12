"""Benchmark script guarantees: empty output is a failure, local runs are measured, memory is recorded."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location(
        "benchmark_vegaparser", REPO_ROOT / "scripts" / "benchmark_vegaparser.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look up their module in sys.modules when loaded from a file path.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_empty_repository_is_reported_as_failure(bench, tmp_path):
    source = tmp_path / "empty_repo"
    source.mkdir()
    (source / "notes.txt").write_text("nothing to parse\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    target = bench.local_target(source)
    row = bench.run_target(target, workspace, repeat=1, warm=False, refresh=False)

    assert row["status"].startswith("empty output"), row
    assert row["modules"] == 0


def test_local_repository_is_copied_not_modified(bench, tmp_path):
    source = tmp_path / "src_repo"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "mod.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (source / ".venv").mkdir()
    (source / ".venv" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    row = bench.run_target(bench.local_target(source), workspace, repeat=1, warm=False, refresh=False)

    assert row["status"] == "ok", row
    assert row["modules"] >= 1
    assert not (source / ".rag_kb").exists(), "the user's tree must not be written to"
    copied = Path(row["workspace"])
    assert (copied / "pkg" / "mod.py").is_file()
    assert not (copied / ".venv").exists(), "virtualenvs are excluded from the copy"


def test_run_records_output_size_and_peak_memory(bench, tmp_path):
    source = tmp_path / "src_repo"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "mod.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    row = bench.run_target(bench.local_target(source), workspace, repeat=1, warm=False, refresh=False)

    assert row["output_bytes"] > 0
    if sys.platform == "win32" or Path("/proc/self/status").is_file():
        # Memory sampling is supported here; a positive peak proves the child was measured.
        assert row["peak_rss_bytes"] is not None and row["peak_rss_bytes"] > 0
