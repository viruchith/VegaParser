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
