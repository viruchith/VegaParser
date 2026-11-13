"""Cache lifecycle through the real `init` command (CliRunner, no reimplementation).

The parser is counted by wrapping ``ParserEngine.parse_file`` so that tests can assert
exactly which files were reparsed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repo_parser import __version__
from repo_parser.cache import CACHE_VERSION, cache_path
from repo_parser.cli import app
from repo_parser.generator.markdown import sanitize_filename
from repo_parser.parser.engine import ParserEngine

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
runner = CliRunner()
QUIET = ["--log-target", "console"]


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A writable copy of the fixtures. The CWD is the tmp dir so no log file lands in the repo."""
    monkeypatch.chdir(tmp_path)
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURES_DIR, dest)
    return dest


@pytest.fixture()
def parsed(monkeypatch):
    """Record every file path passed to ParserEngine.parse_file."""
    calls: list[str] = []
    original = ParserEngine.parse_file

    def counting(self, filepath, content):
        calls.append(filepath)
        return original(self, filepath, content)

    monkeypatch.setattr(ParserEngine, "parse_file", counting)
    return calls


def _init(root: Path, *extra: str):
    result = runner.invoke(app, ["init", str(root), *QUIET, *extra])
    assert result.exit_code == 0, result.output
    return result


def _modules(root: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in (root / ".rag_kb" / "modules").glob("*.md")}


def _count_source_files(root: Path) -> int:
    from repo_parser.traversal.scanner import RepositoryScanner

    return len(RepositoryScanner(root).discover())


# ── reuse ────────────────────────────────────────────────────────────────────


def test_second_init_on_unchanged_repo_parses_nothing(repo, parsed):
    _init(repo)
    first_calls = len(parsed)
    assert first_calls == _count_source_files(repo) > 0

    parsed.clear()
    result = _init(repo)
    assert parsed == []
    assert "0 parsed" in result.output


def _generated_bytes(root: Path) -> dict[str, bytes]:
    kb = root / ".rag_kb"
    out = {f"modules/{p.name}": p.read_bytes() for p in (kb / "modules").glob("*.md")}
    out["project_index.md"] = (kb / "project_index.md").read_bytes()
    return out


def test_cache_holds_resolved_dependencies_on_fresh_and_cached_runs(tmp_path, monkeypatch):
    """Dependency edges are computed after parsing, so they must be saved after inference."""
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "small"
    repo.mkdir()
    (repo / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (repo / "main.py").write_text("import helper\n\n\ndef main():\n    return helper.helper()\n", encoding="utf-8")

    def cached_deps() -> list[str]:
        payload = json.loads(cache_path(repo).read_text(encoding="utf-8"))
        return payload["files"]["main.py"]["parsed"]["internal_dependencies"]

    _init(repo)
    assert cached_deps() == ["helper.py"]

    _init(repo)  # main.py now comes from the cache
    assert cached_deps() == ["helper.py"]


def test_warm_cache_output_matches_full_reparse(repo):
    _init(repo)
    _init(repo)  # warm: every file comes from the cache
    warm = _generated_bytes(repo)

    _init(repo, "--force")  # cold: every file reparsed
    cold = _generated_bytes(repo)

    assert warm == cold


def test_edited_file_is_the_only_file_reparsed(repo, parsed):
    _init(repo)
    target = repo / "config_sample.py"
    target.write_text(target.read_text(encoding="utf-8") + "\ndef edited_marker():\n    pass\n", encoding="utf-8")

    parsed.clear()
    _init(repo)

    assert parsed == ["config_sample.py"]
    module = _modules(repo)[sanitize_filename("config_sample.py")]
    assert "edited_marker" in module


def test_new_file_is_parsed_and_indexed(repo, parsed):
    _init(repo)
    (repo / "new_module.py").write_text("def new_func():\n    pass\n", encoding="utf-8")

    parsed.clear()
    _init(repo)

    assert parsed == ["new_module.py"]
    assert sanitize_filename("new_module.py") in _modules(repo)


# ── deletes and renames ──────────────────────────────────────────────────────


def test_deleted_file_module_is_removed_and_others_kept(repo):
    _init(repo)
    before = set(_modules(repo))
    victim = sanitize_filename("config_sample.py")
    assert victim in before

    (repo / "config_sample.py").unlink()
    result = _init(repo)

    after = set(_modules(repo))
    assert victim not in after
    assert after == before - {victim}
    assert "1 stale module(s) removed" in result.output


def test_renamed_file_replaces_old_module(repo, parsed):
    _init(repo)
    old_name = sanitize_filename("config_sample.py")
    new_name = sanitize_filename("renamed_sample.py")

    (repo / "config_sample.py").rename(repo / "renamed_sample.py")
    parsed.clear()
    _init(repo)

    modules = _modules(repo)
    assert old_name not in modules
    assert new_name in modules
    assert parsed == ["renamed_sample.py"]


def test_user_file_in_modules_dir_is_not_touched(repo):
    _init(repo)
    modules_dir = repo / ".rag_kb" / "modules"
    user_note = modules_dir / "my_notes.md"
    user_note.write_text("# my own notes\n", encoding="utf-8")

    (repo / "config_sample.py").unlink()
    _init(repo)

    assert user_note.read_text(encoding="utf-8") == "# my own notes\n"


# ── force / no-cache ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("flag", ["--force", "--no-cache"])
def test_force_flags_reparse_everything(repo, parsed, flag):
    _init(repo)
    expected = _count_source_files(repo)

    parsed.clear()
    _init(repo, flag)
    assert len(parsed) == expected


# ── cache invalidation ───────────────────────────────────────────────────────


def test_corrupt_cache_is_rebuilt(repo, parsed):
    _init(repo)
    cache_path(repo).write_text("{ not json", encoding="utf-8")

    parsed.clear()
    _init(repo)

    assert len(parsed) == _count_source_files(repo)
    assert json.loads(cache_path(repo).read_text(encoding="utf-8"))["version"] == CACHE_VERSION


def test_cache_from_other_tool_version_is_rebuilt(repo, parsed):
    _init(repo)
    path = cache_path(repo)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tool_version"] = "0.0.0-old"
    path.write_text(json.dumps(payload), encoding="utf-8")

    parsed.clear()
    _init(repo)
    assert len(parsed) == _count_source_files(repo)


def test_changed_language_filter_invalidates_cache(repo, parsed):
    _init(repo)

    # Unfiltered cache holds python files, so a reused cache would parse nothing here.
    parsed.clear()
    _init(repo, "--languages", "python")
    assert parsed, "a changed filter must not reuse the unfiltered cache"
    assert all(name.endswith(".py") for name in parsed)

    parsed.clear()
    _init(repo, "--languages", "python")
    assert parsed == []


def test_cache_records_tool_version_and_layout(repo):
    _init(repo)
    payload = json.loads(cache_path(repo).read_text(encoding="utf-8"))
    assert payload["version"] == CACHE_VERSION
    assert payload["tool_version"] == __version__
    assert payload["files"]
    for entry in payload["files"].values():
        assert len(entry["sha256"]) == 64


# ── logging target ───────────────────────────────────────────────────────────


def test_log_target_console_writes_no_log_file(repo, tmp_path):
    _init(repo)
    assert not (tmp_path / "repo-parser.log").exists()


def test_log_target_file_writes_log_file(repo, tmp_path):
    result = runner.invoke(app, ["init", str(repo), "--log-target", "file"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "repo-parser.log").is_file()
