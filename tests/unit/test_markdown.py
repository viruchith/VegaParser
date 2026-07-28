"""Unit tests for the Markdown generator."""

from __future__ import annotations

import yaml

from repo_parser.generator.markdown import MarkdownGenerator, sanitize_filename
from repo_parser.models import ClassInfo, FunctionInfo, ParsedFile


def test_sanitize_filename_simple():
    assert sanitize_filename("src/app.py") == "src_app_py.md"


def test_sanitize_filename_nested():
    assert sanitize_filename("a/b/c/module.py") == "a_b_c_module_py.md"
    assert sanitize_filename("main.go") == "main_go.md"


def _sample_parsed_files():
    return [
        ParsedFile(
            filepath="src/app.py",
            language="python",
            imports=["import os"],
            classes=[ClassInfo(name="App", line_start=1, line_end=5)],
            functions=[FunctionInfo(name="run", signature="def run()", line_start=7, line_end=9)],
        ),
        ParsedFile(filepath="lib/util.py", language="python", imports=["import sys"]),
    ]


def test_generate_creates_modules_directory(tmp_path):
    generator = MarkdownGenerator(tmp_path)
    generator.generate(_sample_parsed_files())
    assert (tmp_path / ".rag_kb" / "modules").is_dir()


def test_generate_writes_module_files(tmp_path):
    generator = MarkdownGenerator(tmp_path)
    generator.generate(_sample_parsed_files())
    modules_dir = tmp_path / ".rag_kb" / "modules"
    assert (modules_dir / "src_app_py.md").is_file()
    assert (modules_dir / "lib_util_py.md").is_file()


def test_generate_writes_project_index(tmp_path):
    generator = MarkdownGenerator(tmp_path)
    index_path = generator.generate(_sample_parsed_files())
    assert index_path == tmp_path / ".rag_kb" / "project_index.md"
    assert index_path.is_file()


def _extract_frontmatter(text: str) -> dict:
    assert text.startswith("---")
    end = text.index("\n---", 3)
    return yaml.safe_load(text[3:end])


def test_module_frontmatter_is_valid_yaml(tmp_path):
    generator = MarkdownGenerator(tmp_path)
    generator.generate(_sample_parsed_files())
    module_text = (tmp_path / ".rag_kb" / "modules" / "src_app_py.md").read_text(
        encoding="utf-8"
    )
    data = _extract_frontmatter(module_text)
    assert data["filepath"] == "src/app.py"
    assert data["language"] == "python"


def test_project_index_frontmatter_is_valid_yaml(tmp_path):
    generator = MarkdownGenerator(tmp_path)
    index_path = generator.generate(_sample_parsed_files())
    data = _extract_frontmatter(index_path.read_text(encoding="utf-8"))
    assert data["file_count"] == 2
    assert data["generated_by"] == "vegaparser"
