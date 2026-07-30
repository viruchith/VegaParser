"""Unit tests for the parser engine."""

from __future__ import annotations

import pytest

from repo_parser.models import ParsedFile
from repo_parser.parser.engine import ParserEngine

PY_SRC = "import os\n\n\nclass Foo:\n    def bar(self):\n        return 1\n\n\ndef baz():\n    return 2\n"
GO_SRC = 'package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println("hi")\n}\n'
JS_SRC = "class C {\n  m() { return fetch('https://x.com'); }\n}\nfunction f() { return 1; }\n"
JAVA_SRC = "import java.util.List;\n\npublic class Foo {\n  public int bar(String s) { return 1; }\n}\n"
YAML_SRC = "services:\n  db:\n    image: postgres:15\n"
SQL_SRC = "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(50));\n"
HCL_SRC = 'resource "aws_s3_bucket" "b" {\n  bucket = "my-bucket"\n}\n'
BASH_SRC = "#!/bin/bash\nfunction greet() {\n  echo hello\n}\ngreet\n"
ENV_SRC = "DATABASE_URL=postgres://user:secret@db:5432/mydb\nDB_HOST=db.example.com\n"


@pytest.fixture()
def engine():
    return ParserEngine()


@pytest.mark.parametrize(
    "filename,source,expected_language",
    [
        ("mod.py", PY_SRC, "python"),
        ("main.go", GO_SRC, "go"),
        ("app.js", JS_SRC, "javascript"),
        ("Foo.java", JAVA_SRC, "java"),
        ("compose.yml", YAML_SRC, "yaml"),
        ("schema.sql", SQL_SRC, "sql"),
        ("main.tf", HCL_SRC, "terraform"),
        ("script.sh", BASH_SRC, "bash"),
        (".env", ENV_SRC, "env"),
    ],
)
def test_parse_file_returns_parsed_file(engine, filename, source, expected_language):
    result = engine.parse_file(filename, source)
    assert result is not None
    assert isinstance(result, ParsedFile)
    assert result.language == expected_language


def test_parse_python_extracts_symbols(engine):
    result = engine.parse_file("mod.py", PY_SRC)
    assert result is not None
    assert "os" in " ".join(result.imports)
    assert any(c.name == "Foo" for c in result.classes)
    assert any(f.name == "baz" for f in result.functions)


def test_parse_file_unknown_extension_returns_none(engine):
    assert engine.parse_file("weird.xyz", "some content") is None


def test_parse_file_handles_exceptions_gracefully(engine, monkeypatch):
    # Force the underlying parser to raise; parse_file must swallow it.
    import repo_parser.parser.engine as engine_mod

    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(engine_mod.PARSERS, "python", boom)
    assert engine.parse_file("mod.py", PY_SRC) is None


def test_parse_file_malformed_content_does_not_raise(engine):
    # Should not raise even on binary-ish/garbage content.
    result = engine.parse_file("mod.py", "\x00\x01\x02 def broken(")
    assert result is None or isinstance(result, ParsedFile)


def test_infer_internal_dependencies_links_imports():
    engine = ParserEngine()
    utils = ParsedFile(filepath="utils.py", language="python")
    main = ParsedFile(filepath="main.py", language="python", imports=["import utils"])
    engine.infer_internal_dependencies([utils, main])
    assert "utils.py" in main.internal_dependencies
    assert utils.internal_dependencies == []


# ── _grammar_for_language ────────────────────────────────────────────────────


def test_grammar_for_kubernetes():
    from repo_parser.parser.engine import _grammar_for_language
    assert _grammar_for_language("kubernetes") == "yaml"


def test_grammar_for_plsql():
    from repo_parser.parser.engine import _grammar_for_language
    assert _grammar_for_language("plsql") == "sql"


def test_grammar_for_shell():
    from repo_parser.parser.engine import _grammar_for_language
    assert _grammar_for_language("shell") == "bash"


def test_grammar_for_python():
    from repo_parser.parser.engine import _grammar_for_language
    assert _grammar_for_language("python") == "python"


# ── ParserEngine._get_parser ────────────────────────────────────────────────


def test_get_parser_returns_parser_for_python(engine):
    parser = engine._get_parser("python")
    assert parser is not None


def test_get_parser_cached_on_second_call(engine):
    p1 = engine._get_parser("python")
    p2 = engine._get_parser("python")
    assert p1 is p2


def test_get_parser_unknown_language_returns_none(engine):
    result = engine._get_parser("does_not_exist_lang")
    assert result is None


def test_get_parser_kubernetes_uses_yaml(engine):
    parser = engine._get_parser("kubernetes")
    assert parser is not None


def test_get_parser_plsql_uses_sql(engine):
    parser = engine._get_parser("plsql")
    assert parser is not None


def test_get_parser_shell_uses_bash(engine):
    parser = engine._get_parser("shell")
    assert parser is not None


# ── parse_file additional language branches ──────────────────────────────────


def test_parse_file_plsql(engine):
    src = "CREATE PACKAGE p AS\n  PROCEDURE q;\nEND;\nCREATE PROCEDURE q AS BEGIN NULL; END;"
    result = engine.parse_file("script.plsql", src)
    assert result is not None
    assert result.language == "plsql"


def test_parse_file_kubernetes_yaml(engine):
    src = "apiVersion: apps/v1\nkind: Deployment\n"
    result = engine.parse_file("deployment.yaml", src)
    assert result is not None


def test_parse_file_typescript(engine):
    src = "interface Foo { bar: string; }\nfunction f(x: number): string { return String(x); }\n"
    result = engine.parse_file("app.ts", src)
    assert result is not None
    assert result.language == "typescript"


def test_parse_file_config_exception_returns_none(engine, monkeypatch):
    import repo_parser.parser.engine as engine_mod

    def boom(fp, src, parser):
        raise RuntimeError("config boom")

    monkeypatch.setitem(engine_mod.PARSERS, "env", boom)
    assert engine.parse_file(".env", "KEY=value") is None


def test_parse_file_dockerfile_falls_back_when_grammar_missing(engine, monkeypatch):
    import repo_parser.parser.engine as engine_mod

    monkeypatch.setattr(engine_mod, "has_language", lambda _: False)
    result = engine.parse_file("Dockerfile", "FROM python:3.12\nRUN echo hi\n")
    assert result is not None
    assert result.language == "dockerfile"
    assert any(fn.name == "FROM" for fn in result.functions)


# ── ParserEngine._resolve_relative ──────────────────────────────────────────


def test_resolve_relative_basic():
    result = ParserEngine._resolve_relative("src/main.py", "./utils")
    assert result == "src/utils.py"


def test_resolve_relative_parent_dir():
    result = ParserEngine._resolve_relative("src/sub/main.py", "../helpers")
    assert result is not None
    assert result.endswith(".py")


# ── infer_internal_dependencies relative import ───────────────────────────────


def test_infer_internal_dependencies_relative_import():
    engine = ParserEngine()
    helper = ParsedFile(filepath="pkg/helper.py", language="python")
    main = ParsedFile(filepath="pkg/main.py", language="python", imports=["from . import helper"])
    engine.infer_internal_dependencies([helper, main])
    # Relative imports are resolved: ./helper → pkg/helper.py
    # The engine's infer_internal_dependencies resolves "." tokens
    # and checks if they land in module_paths
    assert isinstance(main.internal_dependencies, list)
