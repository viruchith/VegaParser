"""Unit tests for repo_parser.parser.queries.sql_queries."""

from __future__ import annotations

import pytest

from repo_parser.models import ParsedFile
from repo_parser.parser.queries.sql_queries import (
    _detect_plsql,
    _extract_plsql_objects,
    _extract_sql_fallback,
    parse_sql,
)


# ── _detect_plsql ────────────────────────────────────────────────────────────


def test_detect_plsql_package_and_procedure():
    # Two separate CREATE statements each matching a different PLSQL pattern
    src = "CREATE PACKAGE p; CREATE PROCEDURE q AS BEGIN NULL; END;"
    assert _detect_plsql(src) is True


def test_detect_plsql_dbms_package():
    src = "BEGIN DBMS_OUTPUT.PUT_LINE('hi'); END;"
    assert _detect_plsql(src) is True


def test_detect_plsql_execute_immediate():
    src = "EXECUTE IMMEDIATE 'SELECT 1';"
    assert _detect_plsql(src) is True


def test_detect_plsql_plain_sql_is_false():
    src = "SELECT id, name FROM users WHERE id = 1;"
    assert _detect_plsql(src) is False


def test_detect_plsql_score_below_threshold():
    # Only one pattern: DECLARE alone = score 1 (< 2)
    src = "DECLARE x INT;"
    # DECLARE scores 1; need >= 2 or DBMS_/UTL_/EXECUTE IMMEDIATE
    # Since no DBMS_ etc., score is 1 → False
    assert _detect_plsql(src) is False


# ── _extract_sql_fallback ────────────────────────────────────────────────────


def test_extract_sql_fallback_create_table():
    parsed = ParsedFile(filepath="schema.sql", language="sql")
    _extract_sql_fallback("CREATE TABLE users (id INT);", parsed)
    labels = [f.name for f in parsed.functions]
    assert "TABLE" in labels


def test_extract_sql_fallback_create_view():
    parsed = ParsedFile(filepath="schema.sql", language="sql")
    _extract_sql_fallback("CREATE VIEW v AS SELECT 1;", parsed)
    labels = [f.name for f in parsed.functions]
    assert "VIEW" in labels


def test_extract_sql_fallback_select():
    parsed = ParsedFile(filepath="query.sql", language="sql")
    _extract_sql_fallback("SELECT id FROM users;", parsed)
    labels = [f.name for f in parsed.functions]
    assert "SELECT" in labels


def test_extract_sql_fallback_multiple_statements():
    parsed = ParsedFile(filepath="schema.sql", language="sql")
    src = "CREATE TABLE a (id INT);\nCREATE TABLE b (name TEXT);\nSELECT * FROM a;"
    _extract_sql_fallback(src, parsed)
    labels = [f.name for f in parsed.functions]
    assert labels.count("TABLE") == 2
    assert "SELECT" in labels


def test_extract_sql_fallback_no_duplicates_at_same_position():
    parsed = ParsedFile(filepath="q.sql", language="sql")
    src = "SELECT 1;"
    _extract_sql_fallback(src, parsed)
    # Same position should not be duplicated
    selects = [f for f in parsed.functions if f.name == "SELECT"]
    assert len(selects) == 1


# ── _extract_plsql_objects ───────────────────────────────────────────────────


def test_extract_plsql_objects_procedure():
    parsed = ParsedFile(filepath="proc.sql", language="plsql")
    _extract_plsql_objects("CREATE PROCEDURE my_proc AS BEGIN NULL; END;", parsed)
    names = [c.name for c in parsed.classes]
    assert any("PROCEDURE" in n for n in names)


def test_extract_plsql_objects_function():
    parsed = ParsedFile(filepath="fn.sql", language="plsql")
    _extract_plsql_objects("CREATE FUNCTION my_func RETURN NUMBER IS BEGIN RETURN 1; END;", parsed)
    names = [c.name for c in parsed.classes]
    assert any("FUNCTION" in n for n in names)


def test_extract_plsql_objects_package():
    parsed = ParsedFile(filepath="pkg.sql", language="plsql")
    _extract_plsql_objects("CREATE PACKAGE my_pkg AS\nEND my_pkg;", parsed)
    names = [c.name for c in parsed.classes]
    assert any("PACKAGE" in n for n in names)


def test_extract_plsql_objects_no_match():
    parsed = ParsedFile(filepath="plain.sql", language="sql")
    _extract_plsql_objects("SELECT 1;", parsed)
    assert parsed.classes == []


# ── parse_sql (parser=None path) ─────────────────────────────────────────────


def test_parse_sql_plain_no_parser():
    result = parse_sql("schema.sql", "CREATE TABLE users (id INT);", None)
    assert result is not None
    assert result.language == "sql"
    assert any(f.name == "TABLE" for f in result.functions)


def test_parse_sql_plsql_no_parser():
    src = "CREATE PACKAGE p AS\n  PROCEDURE q;\nEND;\nCREATE PROCEDURE q AS BEGIN NULL; END;"
    result = parse_sql("script.sql", src, None)
    assert result is not None
    assert result.language == "plsql"
    assert result.module_docstring is not None


def test_parse_sql_plsql_extension():
    result = parse_sql("script.pls", "SELECT 1;", None)
    assert result.language == "plsql"


def test_parse_sql_plsql_pkb_extension():
    result = parse_sql("pkg.pkb", "SELECT 1;", None)
    assert result.language == "plsql"


def test_parse_sql_exports_populated():
    result = parse_sql("q.sql", "SELECT id FROM t; INSERT INTO t VALUES (1);", None)
    assert "SELECT" in result.exports or "INSERT" in result.exports


# ── parse_sql (parser not None) ──────────────────────────────────────────────


def test_parse_sql_with_real_parser():
    """Test the tree-sitter parser path (lines 124-164)."""
    try:
        from tree_sitter_language_pack import get_parser, has_language
        from repo_parser.parser.ts_compat import ParserAdapter

        if not has_language("sql"):
            pytest.skip("sql grammar not available")

        parser = ParserAdapter(get_parser("sql"))
        result = parse_sql("schema.sql", "SELECT 1;", parser)
        assert result is not None
        assert result.language == "sql"
    except ImportError:
        pytest.skip("tree_sitter_language_pack not available")


def test_parse_sql_plsql_with_real_parser():
    """PL/SQL with a real parser should still enrich with plsql objects."""
    try:
        from tree_sitter_language_pack import get_parser, has_language
        from repo_parser.parser.ts_compat import ParserAdapter

        if not has_language("sql"):
            pytest.skip("sql grammar not available")

        parser = ParserAdapter(get_parser("sql"))
        src = "CREATE PACKAGE my_pkg AS\n  PROCEDURE do_it;\nEND;\nCREATE PROCEDURE do_it AS BEGIN NULL; END;"
        result = parse_sql("script.sql", src, parser)
        assert result is not None
        # plsql detection adds objects
        assert result.language == "plsql"
    except ImportError:
        pytest.skip("tree_sitter_language_pack not available")
