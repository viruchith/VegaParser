"""Unit tests for Java parsing (fallback regex and tree-sitter)."""

from __future__ import annotations

import pytest

from repo_parser.parser.queries.java_fallback_queries import parse_java_fallback


SIMPLE_SRC = """\
import java.util.List;
import java.util.Map;

public class Foo {
    public int bar(String s) {
        return 1;
    }

    private void baz() {
        // empty
    }
}
"""

INTERFACE_SRC = """\
public interface IFoo {
    void doThing();
}
"""

ENUM_SRC = """\
public enum Color {
    RED, GREEN, BLUE;
}
"""

DOCSTRING_SRC = """\
/*
 * This is a module comment.
 */
public class Doc {}
"""

MULTICLASS_SRC = """\
public class Outer {
    public static class Inner {
        public void inner_method() {}
    }
    public void outer_method() {}
}
"""

CONTROL_NAMES_SRC = """\
public class Guard {
    public void process(List<String> items) {
        if (items != null) {
            for (String item : items) {
                return;
            }
        }
    }
}
"""


# ── parse_java_fallback ──────────────────────────────────────────────────────


def test_parse_java_fallback_imports():
    result = parse_java_fallback("Foo.java", SIMPLE_SRC)
    assert result.language == "java"
    assert any("java.util.List" in imp for imp in result.imports)
    assert any("java.util.Map" in imp for imp in result.imports)


def test_parse_java_fallback_class_export():
    result = parse_java_fallback("Foo.java", SIMPLE_SRC)
    assert "Foo" in result.exports
    assert any(c.name == "Foo" for c in result.classes)


def test_parse_java_fallback_methods():
    result = parse_java_fallback("Foo.java", SIMPLE_SRC)
    foo_class = next(c for c in result.classes if c.name == "Foo")
    method_names = [m.name for m in foo_class.methods]
    assert "bar" in method_names
    assert "baz" in method_names


def test_parse_java_fallback_interface():
    result = parse_java_fallback("IFoo.java", INTERFACE_SRC)
    assert any(c.name == "IFoo" for c in result.classes)


def test_parse_java_fallback_enum():
    result = parse_java_fallback("Color.java", ENUM_SRC)
    assert any(c.name == "Color" for c in result.classes)


def test_parse_java_fallback_docstring():
    result = parse_java_fallback("Doc.java", DOCSTRING_SRC)
    assert result.module_docstring is not None
    assert "module comment" in result.module_docstring


def test_parse_java_fallback_no_docstring():
    result = parse_java_fallback("Foo.java", SIMPLE_SRC)
    # No /* */ at start
    assert result.module_docstring is None


def test_parse_java_fallback_control_words_not_extracted_as_methods():
    result = parse_java_fallback("Guard.java", CONTROL_NAMES_SRC)
    all_method_names = [m.name for m in result.functions]
    for name in ("if", "for", "while", "return", "switch", "catch"):
        assert name not in all_method_names


def test_parse_java_fallback_nested_class():
    result = parse_java_fallback("Outer.java", MULTICLASS_SRC)
    class_names = [c.name for c in result.classes]
    assert "Outer" in class_names
    assert "Inner" in class_names


def test_parse_java_fallback_empty_source():
    result = parse_java_fallback("Empty.java", "")
    assert result.language == "java"
    assert result.classes == []
    assert result.imports == []


def test_parse_java_fallback_line_tracking():
    result = parse_java_fallback("Foo.java", SIMPLE_SRC)
    foo = next(c for c in result.classes if c.name == "Foo")
    assert foo.line_start > 0
    assert foo.line_end >= foo.line_start


def test_parse_java_fallback_method_is_method_flag():
    result = parse_java_fallback("Foo.java", SIMPLE_SRC)
    foo = next(c for c in result.classes if c.name == "Foo")
    for m in foo.methods:
        assert m.is_method is True
        assert m.parent_class == "Foo"


# ── parse_java (tree-sitter) ─────────────────────────────────────────────────


def test_parse_java_tree_sitter_if_available():
    """parse_java uses tree-sitter; skip if java grammar unavailable."""
    try:
        from tree_sitter_language_pack import get_parser, has_language
        from repo_parser.parser.ts_compat import ParserAdapter
        from repo_parser.parser.queries.java_queries import parse_java
    except ImportError:
        pytest.skip("tree_sitter_language_pack not available")

    if not has_language("java"):
        pytest.skip("java grammar not available")

    parser = ParserAdapter(get_parser("java"))
    result = parse_java("Foo.java", SIMPLE_SRC, parser)
    assert result is not None
    assert result.language == "java"
    assert any("java.util.List" in imp for imp in result.imports)


def test_parse_java_tree_sitter_package_export():
    try:
        from tree_sitter_language_pack import get_parser, has_language
        from repo_parser.parser.ts_compat import ParserAdapter
        from repo_parser.parser.queries.java_queries import parse_java
    except ImportError:
        pytest.skip("tree_sitter_language_pack not available")

    if not has_language("java"):
        pytest.skip("java grammar not available")

    src = "package com.example;\n" + SIMPLE_SRC
    parser = ParserAdapter(get_parser("java"))
    result = parse_java("Foo.java", src, parser)
    assert any("package:com.example" in e for e in result.exports)
    assert any("com.example.Foo" in e for e in result.exports)
