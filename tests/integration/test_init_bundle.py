"""Integration tests for the init + bundle pipeline (Python API)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from repo_parser.generator.bundle import bundle_knowledge_base, bundle_stats
from repo_parser.generator.markdown import MarkdownGenerator, sanitize_filename
from repo_parser.parser.engine import ParserEngine
from repo_parser.parser.registry import detect_language
from repo_parser.traversal.scanner import RepositoryScanner

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def run_init(root: Path, lang_filter: set[str] | None = None) -> list:
    """Run the init pipeline directly via the Python API."""
    scanner = RepositoryScanner(root, languages=lang_filter)
    files = scanner.discover()
    engine = ParserEngine()

    parsed_files = []
    for rel_path in files:
        rel_str = rel_path.as_posix()
        if lang_filter:
            detected = detect_language(rel_str)
            if detected not in lang_filter:
                continue
        content = scanner.read_file(rel_path)
        if content is None:
            continue
        result = engine.parse_file(rel_str, content)
        if result is not None:
            parsed_files.append(result)

    engine.infer_internal_dependencies(parsed_files)
    generator = MarkdownGenerator(root)
    generator.generate(parsed_files)
    return parsed_files


@pytest.fixture()
def fixtures_copy(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURES_DIR, dest)
    return dest


def _module_names(root: Path) -> list[str]:
    modules_dir = root / ".rag_kb" / "modules"
    return sorted(p.name for p in modules_dir.glob("*.md"))


def test_init_creates_modules_and_index(fixtures_copy):
    parsed = run_init(fixtures_copy)
    assert parsed

    modules_dir = fixtures_copy / ".rag_kb" / "modules"
    assert modules_dir.is_dir()
    assert list(modules_dir.glob("*.md"))
    assert (fixtures_copy / ".rag_kb" / "project_index.md").is_file()


def test_init_module_names_snapshot(fixtures_copy, snapshot):
    run_init(fixtures_copy)
    assert _module_names(fixtures_copy) == snapshot


def test_bundle_after_init(fixtures_copy):
    run_init(fixtures_copy)
    bundle_path = bundle_knowledge_base(fixtures_copy)
    assert bundle_path.is_file()
    assert bundle_path.name == "full_repo_context.md"

    stats = bundle_stats(bundle_path)
    assert stats["bytes"] > 0
    assert stats["tokens_estimate"] > 0


def test_languages_filter_only_indexes_python(fixtures_copy, snapshot):
    parsed = run_init(fixtures_copy, lang_filter={"python"})
    assert parsed
    assert all(pf.language == "python" for pf in parsed)

    names = _module_names(fixtures_copy)
    assert all(name.endswith("_py.md") for name in names)
    assert names == snapshot


def test_init_is_deterministic(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    shutil.copytree(FIXTURES_DIR, first)
    shutil.copytree(FIXTURES_DIR, second)

    run_init(first)
    run_init(second)

    first_modules = {p.name: p.stat().st_size for p in (first / ".rag_kb" / "modules").glob("*.md")}
    second_modules = {p.name: p.stat().st_size for p in (second / ".rag_kb" / "modules").glob("*.md")}

    assert first_modules == second_modules


def test_init_twice_same_root_is_stable(fixtures_copy):
    run_init(fixtures_copy)
    first = {p.name: p.stat().st_size for p in (fixtures_copy / ".rag_kb" / "modules").glob("*.md")}
    run_init(fixtures_copy)
    second = {p.name: p.stat().st_size for p in (fixtures_copy / ".rag_kb" / "modules").glob("*.md")}
    assert first == second
