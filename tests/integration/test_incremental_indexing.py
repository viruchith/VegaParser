import shutil
from pathlib import Path

import pytest

from repo_parser.cache import IndexCache, compute_hash
from repo_parser.generator.markdown import MarkdownGenerator, sanitize_filename
from repo_parser.parser.engine import ParserEngine
from repo_parser.traversal.scanner import RepositoryScanner


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def run_init(root: Path, force: bool = False) -> list:
    """Run the init process directly (no CLI)."""
    from repo_parser.cache import IndexCache, _parsed_file_to_dict, compute_hash
    from repo_parser.generator.markdown import sanitize_filename

    scanner = RepositoryScanner(root)
    files = scanner.discover()
    engine = ParserEngine()

    rag_kb_dir = root / ".rag_kb"
    modules_dir = rag_kb_dir / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)

    cache = IndexCache(rag_kb_dir)
    if not force:
        cache.load()

    # Remove deleted files
    current_rel_paths = {rel.as_posix() for rel in files}
    for stale_path in list(cache.known_paths() - current_rel_paths):
        module_file = modules_dir / sanitize_filename(stale_path)
        if module_file.exists():
            module_file.unlink()
        cache.remove(stale_path)

    parsed_files = []
    parsed_fresh = []

    for rel_path in files:
        rel_str = rel_path.as_posix()
        content = scanner.read_file(rel_path)
        if content is None:
            continue
        content_hash = compute_hash(content)
        module_file = modules_dir / sanitize_filename(rel_str)

        if not force and cache.is_cached(rel_str, content_hash, module_file):
            cached_pf = cache.get_cached_parsed_file(rel_str)
            if cached_pf is not None:
                parsed_files.append(cached_pf)
                continue

        result = engine.parse_file(rel_str, content)
        if result is not None:
            cache.update(rel_str, content_hash, _parsed_file_to_dict(result))
            parsed_files.append(result)
            parsed_fresh.append(rel_str)

    engine.infer_internal_dependencies(parsed_files)
    generator = MarkdownGenerator(root)
    generator.generate(parsed_files)
    cache.save()

    return parsed_fresh


def test_second_run_reparses_zero_files(tmp_path):
    """Second run with no changes should reparse zero files."""
    shutil.copytree(FIXTURES_DIR, tmp_path / "fixtures", dirs_exist_ok=True)
    root = tmp_path / "fixtures"

    # First run
    first_parsed = run_init(root)
    assert len(first_parsed) > 0, "First run should parse files"

    # Second run
    second_parsed = run_init(root)
    assert len(second_parsed) == 0, "Second run should reparse zero files"


def test_modified_file_reparsed(tmp_path):
    """Modifying one file should only reparse that file."""
    shutil.copytree(FIXTURES_DIR, tmp_path / "fixtures", dirs_exist_ok=True)
    root = tmp_path / "fixtures"

    first_parsed = run_init(root)
    assert len(first_parsed) > 0

    # Modify one file
    py_file = root / "config_sample.py"
    py_file.write_text(py_file.read_text() + "\n# modified\n", encoding="utf-8")

    second_parsed = run_init(root)
    assert len(second_parsed) == 1
    assert "config_sample.py" in second_parsed[0]


def test_new_file_parsed(tmp_path):
    """Adding a new file causes it to be parsed."""
    shutil.copytree(FIXTURES_DIR, tmp_path / "fixtures", dirs_exist_ok=True)
    root = tmp_path / "fixtures"

    first_parsed = run_init(root)

    # Add a new Python file
    new_file = root / "new_module.py"
    new_file.write_text("def new_func(): pass\n", encoding="utf-8")

    second_parsed = run_init(root)
    assert any("new_module.py" in p for p in second_parsed)


def test_deleted_file_removed(tmp_path):
    """Deleting a file removes its module file."""
    shutil.copytree(FIXTURES_DIR, tmp_path / "fixtures", dirs_exist_ok=True)
    root = tmp_path / "fixtures"

    run_init(root)

    # Verify module file exists
    modules_dir = root / ".rag_kb" / "modules"
    config_module = modules_dir / sanitize_filename("config_sample.py")
    assert config_module.exists()

    # Delete the source file
    (root / "config_sample.py").unlink()

    run_init(root)

    # Module file should be removed
    assert not config_module.exists()


def test_force_flag_full_reparse(tmp_path):
    """--force/--no-cache always does a full reparse."""
    shutil.copytree(FIXTURES_DIR, tmp_path / "fixtures", dirs_exist_ok=True)
    root = tmp_path / "fixtures"

    first_parsed = run_init(root)
    assert len(first_parsed) > 0

    # Force reparse
    force_parsed = run_init(root, force=True)
    assert len(force_parsed) > 0
    assert len(force_parsed) >= len(first_parsed)
