"""`bundle` command behaviour through the CLI: budget reporting, output validation, determinism."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repo_parser.cli import app

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
runner = CliRunner()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURES_DIR, dest)
    result = runner.invoke(app, ["init", str(dest), "--log-target", "console"])
    assert result.exit_code == 0, result.output
    return dest


def _bundle(repo: Path, *extra: str):
    return runner.invoke(app, ["bundle", str(repo), "--log-target", "console", *extra])


def test_bundle_reports_unlimited_budget_by_default(repo):
    result = _bundle(repo)
    assert result.exit_code == 0, result.output
    assert "Budget:      unlimited" in result.output
    assert "<!-- Budget: unlimited -->" in (repo / ".rag_kb" / "full_repo_context.md").read_text(encoding="utf-8")


def test_bundle_reports_and_honours_max_tokens(repo):
    result = _bundle(repo, "--max-tokens", "2500")
    assert result.exit_code == 0, result.output
    assert "2500 tokens" in result.output
    text = (repo / ".rag_kb" / "full_repo_context.md").read_text(encoding="utf-8")
    assert len(text) <= 3 * 2500
    assert "<!-- Budget: 2500 tokens" in text


def test_bundle_refuses_budget_smaller_than_header_and_index(repo):
    # The project index is always included, so a budget below it is an error, not an overrun.
    result = _bundle(repo, "--max-tokens", "1000")
    assert result.exit_code == 1
    assert "too small" in result.output


def test_bundle_lists_omitted_modules_when_budget_is_tight(repo):
    result = _bundle(repo, "--max-tokens", "2500")
    assert result.exit_code == 0, result.output
    text = (repo / ".rag_kb" / "full_repo_context.md").read_text(encoding="utf-8")
    assert "omitted by budget" in result.output
    assert "## Omitted by budget" in text


def test_bundle_is_byte_identical_across_runs(repo):
    assert _bundle(repo, "--max-tokens", "4000").exit_code == 0
    first = (repo / ".rag_kb" / "full_repo_context.md").read_bytes()
    assert _bundle(repo, "--max-tokens", "4000").exit_code == 0
    second = (repo / ".rag_kb" / "full_repo_context.md").read_bytes()
    assert first == second


@pytest.mark.parametrize("bad", ["../escape.md", "sub/x.md", "notes.txt", "project_index.md"])
def test_bundle_rejects_bad_output_names(repo, bad):
    result = _bundle(repo, "--output", bad)
    assert result.exit_code == 1, result.output
    assert not (repo / ".rag_kb" / "escape.md").exists()


def test_bundle_accepts_custom_md_output(repo):
    result = _bundle(repo, "--output", "for_gpt.md")
    assert result.exit_code == 0, result.output
    assert (repo / ".rag_kb" / "for_gpt.md").is_file()


def test_bundle_query_and_path_are_reflected_in_header(repo):
    result = _bundle(repo, "--query", "config", "--path", "*.py")
    assert result.exit_code == 0, result.output
    text = (repo / ".rag_kb" / "full_repo_context.md").read_text(encoding="utf-8")
    assert 'relevance to query "config"' in text
    assert "Path filter: *.py" in text


def test_bundle_rejects_zero_budget(repo):
    result = _bundle(repo, "--max-tokens", "0")
    assert result.exit_code == 2
