"""Integration tests for the init + bundle pipeline (through the real CLI)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repo_parser.cli import app
from repo_parser.generator.bundle import bundle_knowledge_base, bundle_stats

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
runner = CliRunner()


def run_init(root: Path, languages: str | None = None) -> None:
    """Run `init` through the CLI with logs on the console (no log file written)."""
    args = ["init", str(root), "--log-target", "console"]
    if languages:
        args += ["--languages", languages]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output


@pytest.fixture()
def fixtures_copy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURES_DIR, dest)
    return dest


def _module_names(root: Path) -> list[str]:
    modules_dir = root / ".rag_kb" / "modules"
    return sorted(p.name for p in modules_dir.glob("*.md"))


def test_init_creates_modules_and_index(fixtures_copy):
    run_init(fixtures_copy)

    modules_dir = fixtures_copy / ".rag_kb" / "modules"
    assert modules_dir.is_dir()
    assert list(modules_dir.glob("*.md"))
    assert (fixtures_copy / ".rag_kb" / "project_index.md").is_file()


def test_init_module_names_snapshot(fixtures_copy, snapshot):
    run_init(fixtures_copy)
    assert _module_names(fixtures_copy) == snapshot


def test_bundle_after_init(fixtures_copy):
    run_init(fixtures_copy)
    bundle_path = bundle_knowledge_base(fixtures_copy).path
    assert bundle_path.is_file()
    assert bundle_path.name == "full_repo_context.md"

    stats = bundle_stats(bundle_path)
    assert stats["bytes"] > 0
    assert stats["tokens_estimate"] > 0


def test_languages_filter_only_indexes_python(fixtures_copy, snapshot):
    run_init(fixtures_copy, languages="python")

    names = _module_names(fixtures_copy)
    assert names
    assert all(name.endswith("_py.md") for name in names)
    assert names == snapshot


def test_init_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    shutil.copytree(FIXTURES_DIR, first)
    shutil.copytree(FIXTURES_DIR, second)

    run_init(first)
    run_init(second)

    first_modules = {p.name: p.read_bytes() for p in (first / ".rag_kb" / "modules").glob("*.md")}
    second_modules = {p.name: p.read_bytes() for p in (second / ".rag_kb" / "modules").glob("*.md")}

    assert first_modules == second_modules


def test_init_twice_same_root_is_stable(fixtures_copy):
    run_init(fixtures_copy)
    first = {p.name: p.read_bytes() for p in (fixtures_copy / ".rag_kb" / "modules").glob("*.md")}
    run_init(fixtures_copy)
    second = {p.name: p.read_bytes() for p in (fixtures_copy / ".rag_kb" / "modules").glob("*.md")}
    assert first == second
