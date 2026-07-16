"""Parity test for the tree-sitter Query API spike (evaluation only)."""

from __future__ import annotations

from repo_parser.parser.queries.python_queries_query_api import parse_python_query_api

SRC = (
    "import os\n"
    "from sys import path\n"
    "\n"
    "class Widget:\n"
    "    def render(self):\n"
    "        return 1\n"
    "\n"
    "def build():\n"
    "    return Widget()\n"
)


def test_query_api_spike_extracts_symbols():
    parsed = parse_python_query_api("mod.py", SRC)
    assert parsed.language == "python"
    assert any("import os" in imp for imp in parsed.imports)
    assert any(c.name == "Widget" for c in parsed.classes)
    assert any(f.name == "build" for f in parsed.functions)

    widget = next(c for c in parsed.classes if c.name == "Widget")
    assert any(m.name == "render" and m.is_method for m in widget.methods)
