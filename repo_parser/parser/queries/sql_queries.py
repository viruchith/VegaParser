"""Tree-sitter extraction for SQL and PL/SQL."""

from __future__ import annotations

import re

from repo_parser.models import ClassInfo, FunctionInfo, ParsedFile
from repo_parser.parser.queries.base import (
    child_count,
    iter_nodes,
    line_end,
    line_number,
    node_kind,
    node_text,
    parse_root,
)

PLSQL_PATTERNS = [
    (re.compile(r"\bCREATE\s+(OR\s+REPLACE\s+)?PACKAGE\b", re.I), "PACKAGE"),
    (re.compile(r"\bCREATE\s+(OR\s+REPLACE\s+)?PROCEDURE\b", re.I), "PROCEDURE"),
    (re.compile(r"\bCREATE\s+(OR\s+REPLACE\s+)?FUNCTION\b", re.I), "FUNCTION"),
    (re.compile(r"\bCREATE\s+(OR\s+REPLACE\s+)?TRIGGER\b", re.I), "TRIGGER"),
    (re.compile(r"\bCREATE\s+(OR\s+REPLACE\s+)?VIEW\b", re.I), "VIEW"),
    (re.compile(r"\bDECLARE\b", re.I), "PL/SQL_BLOCK"),
    (re.compile(r"\bBEGIN\b", re.I), "PL/SQL_BLOCK"),
    (re.compile(r"\bEXECUTE\s+IMMEDIATE\b", re.I), "DYNAMIC_SQL"),
    (re.compile(r"\bDBMS_\w+\b", re.I), "ORACLE_DBMS"),
    (re.compile(r"\bUTL_\w+\b", re.I), "ORACLE_UTL"),
]

SQL_NODE_KINDS = {
    "create_table": "TABLE",
    "create_view": "VIEW",
    "create_index": "INDEX",
    "create_procedure": "PROCEDURE",
    "create_function": "FUNCTION",
    "create_trigger": "TRIGGER",
    "alter_table": "ALTER_TABLE",
    "drop_table": "DROP_TABLE",
    "select": "SELECT",
    "insert": "INSERT",
    "update": "UPDATE",
    "delete": "DELETE",
}

SQL_FALLBACK_PATTERNS = [
    (re.compile(r"\bCREATE\s+TABLE\b", re.I), "TABLE"),
    (re.compile(r"\bCREATE\s+VIEW\b", re.I), "VIEW"),
    (re.compile(r"\bCREATE\s+INDEX\b", re.I), "INDEX"),
    (re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\b", re.I), "PROCEDURE"),
    (re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b", re.I), "FUNCTION"),
    (re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\b", re.I), "TRIGGER"),
    (re.compile(r"\bALTER\s+TABLE\b", re.I), "ALTER_TABLE"),
    (re.compile(r"\bDROP\s+TABLE\b", re.I), "DROP_TABLE"),
    (re.compile(r"\bSELECT\b", re.I), "SELECT"),
    (re.compile(r"\bINSERT\b", re.I), "INSERT"),
    (re.compile(r"\bUPDATE\b", re.I), "UPDATE"),
    (re.compile(r"\bDELETE\b", re.I), "DELETE"),
]

CREATE_TABLE_NAME_RE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)",
    re.I,
)


def _extract_sql_fallback(source: str, parsed: ParsedFile) -> None:
    """Extract SQL statements using regex fallback when tree-sitter is unavailable."""
    seen_stmts: set[str] = set()
    for pattern, label in SQL_FALLBACK_PATTERNS:
        for match in pattern.finditer(source):
            stmt = match.group(0).strip()[:200]
            key = f"{label}:{match.start()}"
            if key in seen_stmts:
                continue
            seen_stmts.add(key)
            line = source[:match.start()].count("\n") + 1
            parsed.functions.append(
                FunctionInfo(name=label, signature=stmt, line_start=line, line_end=line)
            )
            parsed.exports.append(label)


def _detect_plsql(source: str) -> bool:
    score = 0
    for pattern, _ in PLSQL_PATTERNS:
        if pattern.search(source):
            score += 1
    return score >= 2 or bool(re.search(r"\b(DBMS_|UTL_|EXECUTE\s+IMMEDIATE)\b", source, re.I))


def _extract_plsql_objects(source: str, parsed: ParsedFile) -> None:
    for pattern, obj_type in PLSQL_PATTERNS[:6]:
        for match in pattern.finditer(source):
            line = source[: match.start()].count("\n") + 1
            snippet = source[match.start() : match.start() + 120].split("\n")[0].strip()
            name_match = re.search(
                r"(?:PACKAGE|PROCEDURE|FUNCTION|TRIGGER|VIEW)\s+(\w+)",
                snippet,
                re.I,
            )
            name = name_match.group(1) if name_match else obj_type
            parsed.classes.append(
                ClassInfo(
                    name=f"{obj_type}:{name}",
                    docstring=snippet,
                    line_start=line,
                    line_end=line,
                )
            )
            parsed.exports.append(f"{obj_type} {name}")

def parse_sql(filepath: str, source: str, parser) -> ParsedFile:
    is_plsql = _detect_plsql(source) or filepath.lower().endswith((".plsql", ".pls", ".pkb", ".pks"))
    parsed = ParsedFile(filepath=filepath, language="plsql" if is_plsql else "sql")

    if parser is None:
        _extract_sql_fallback(source, parsed)
        if is_plsql:
            _extract_plsql_objects(source, parsed)
            parsed.module_docstring = "Oracle PL/SQL script with packages, procedures, or blocks."
        return parsed

    tree = parser.parse(source)
    root = tree.root_node()

    seen_stmts: set[str] = set()
    for node in iter_nodes(root):
        kind = node_kind(node)
        if kind in SQL_NODE_KINDS:
            text = node_text(source, node).strip()
            label = SQL_NODE_KINDS[kind]
            key = f"{label}:{line_number(source, node)}"
            if key in seen_stmts:
                continue
            seen_stmts.add(key)
            parsed.functions.append(
                FunctionInfo(
                    name=label,
                    signature=text[:200],
                    line_start=line_number(source, node),
                    line_end=line_end(source, node),
                )
            )
            parsed.exports.append(label)
        elif kind == "create_table":
            for child in (node.child(i) for i in range(child_count(node))):
                if node_kind(child) in ("identifier", "object_reference", "table_reference"):
                    table = node_text(source, child).strip()
                    if table:
                        parsed.classes.append(
                            ClassInfo(
                                name=table,
                                docstring="Database table",
                                line_start=line_number(source, node),
                                line_end=line_end(source, node),
                            )
                        )

    if is_plsql:
        _extract_plsql_objects(source, parsed)
        parsed.module_docstring = "Oracle PL/SQL script with packages, procedures, or blocks."

    return parsed
