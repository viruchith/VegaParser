"""End-to-end CLI tests via Typer's CliRunner."""

from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from repo_parser.cli import app

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
runner = CliRunner()


def _copy_fixtures(tmp_path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURES_DIR, dest)
    return dest


def test_cli_init_generates_knowledge_base(tmp_path):
    repo = _copy_fixtures(tmp_path)
    result = runner.invoke(app, ["init", str(repo)])
    assert result.exit_code == 0, result.output
    assert (repo / ".rag_kb" / "project_index.md").is_file()
    assert list((repo / ".rag_kb" / "modules").glob("*.md"))


def test_cli_init_with_language_filter(tmp_path):
    repo = _copy_fixtures(tmp_path)
    result = runner.invoke(app, ["init", str(repo), "--languages", "python"])
    assert result.exit_code == 0, result.output
    module_names = [p.name for p in (repo / ".rag_kb" / "modules").glob("*.md")]
    assert module_names
    assert all(name.endswith("_py.md") for name in module_names)


def test_cli_init_then_bundle(tmp_path):
    repo = _copy_fixtures(tmp_path)
    init_result = runner.invoke(app, ["init", str(repo), "--verbose"])
    assert init_result.exit_code == 0, init_result.output

    bundle_result = runner.invoke(app, ["bundle", str(repo)])
    assert bundle_result.exit_code == 0, bundle_result.output
    assert (repo / ".rag_kb" / "full_repo_context.md").is_file()


def test_cli_bundle_without_init_fails(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    result = runner.invoke(app, ["bundle", str(repo)])
    assert result.exit_code == 1
