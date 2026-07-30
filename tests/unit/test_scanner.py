"""Unit tests for the repository scanner."""

from __future__ import annotations

from pathlib import Path

from repo_parser.traversal.scanner import (
    BINARY_EXTENSIONS,
    SKIP_DIRS,
    RepositoryScanner,
    _is_binary,
)


def _write(path: Path, content: str = "x = 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _discovered_names(root: Path, **kwargs) -> set[str]:
    scanner = RepositoryScanner(root, **kwargs)
    return {p.as_posix() for p in scanner.discover()}


def test_gitignore_is_respected(tmp_path):
    _write(tmp_path / "keep.py")
    _write(tmp_path / "drop.py")
    (tmp_path / ".gitignore").write_text("drop.py\n", encoding="utf-8")

    names = _discovered_names(tmp_path)
    assert "keep.py" in names
    assert "drop.py" not in names


def test_skip_dirs_are_ignored(tmp_path):
    _write(tmp_path / "main.py")
    for skip in [".git", "node_modules", "venv", ".rag_kb"]:
        assert skip in SKIP_DIRS or skip.startswith(".")
        _write(tmp_path / skip / "inside.py")

    names = _discovered_names(tmp_path)
    assert "main.py" in names
    for skip in [".git", "node_modules", "venv", ".rag_kb"]:
        assert f"{skip}/inside.py" not in names


def test_binary_files_are_skipped(tmp_path):
    _write(tmp_path / "text.py")
    # A .py file containing a null byte should be treated as binary.
    (tmp_path / "binary.py").write_bytes(b"import os\x00\x01\x02\n")

    names = _discovered_names(tmp_path)
    assert "text.py" in names
    assert "binary.py" not in names


def test_languages_filter_only_returns_matching_files(tmp_path):
    _write(tmp_path / "a.py")
    _write(tmp_path / "b.go", "package main\n")

    names = _discovered_names(tmp_path, languages={"python"})
    assert "a.py" in names
    assert "b.go" not in names


def test_special_filename_handling(tmp_path):
    _write(tmp_path / "Dockerfile", "FROM python:3.12\n")
    _write(tmp_path / "docker-compose.yml", "services: {}\n")
    _write(tmp_path / ".env", "DB_HOST=localhost\n")
    _write(tmp_path / ".env.production", "DB_HOST=prod\n")

    names = _discovered_names(tmp_path)
    assert "Dockerfile" in names
    assert "docker-compose.yml" in names
    assert ".env" in names
    assert ".env.production" in names


def test_dockerfile_language_filter_includes_extensionless_dockerfile(tmp_path):
    _write(tmp_path / "Dockerfile", "FROM python:3.12\n")
    _write(tmp_path / "main.py")

    names = _discovered_names(tmp_path, languages={"dockerfile"}, extensions={".dockerfile"})
    assert "Dockerfile" in names
    assert "main.py" not in names


def test_is_binary_by_extension(tmp_path):
    for ext in [".png", ".zip", ".exe"]:
        assert ext in BINARY_EXTENSIONS
        p = tmp_path / f"file{ext}"
        p.write_bytes(b"not really binary but extension says so")
        assert _is_binary(p) is True


def test_is_binary_by_null_byte(tmp_path):
    p = tmp_path / "data.txt"
    p.write_bytes(b"hello\x00world")
    assert _is_binary(p) is True


def test_is_binary_returns_false_for_text(tmp_path):
    p = tmp_path / "code.py"
    p.write_text("def f():\n    return 1\n", encoding="utf-8")
    assert _is_binary(p) is False
