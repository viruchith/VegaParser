"""
EXPERIMENTAL: Python extractor using the tree-sitter Query API.
This is a spike for evaluation purposes — NOT production code.
Do not import from production modules.
See: docs/tree-sitter-query-api-evaluation.md

This module re-implements the Python extraction found in
``python_queries.py`` using tree-sitter's S-expression Query API
(``tree_sitter.Query`` + ``tree_sitter.QueryCursor``) instead of manual
recursive AST traversal. It targets ``tree-sitter`` >= 0.25 where
``captures()``/``matches()`` live on ``QueryCursor`` and ``Query`` is
constructed as ``Query(language, source)``.
"""

from __future__ import annotations

import tree_sitter as ts
from tree_sitter_language_pack import get_language, get_parser

from repo_parser.models import ClassInfo, FunctionInfo, ParsedFile

# --- S-expression query patterns -------------------------------------------
IMPORT_QUERY = "(import_statement) @import\n(import_from_statement) @import_from"
CLASS_QUERY = "(class_definition name: (identifier) @class_name) @class"
FUNCTION_QUERY = "(function_definition name: (identifier) @func_name) @func"


def _node_text(node) -> str:
    return node.text.decode("utf-8", errors="replace")


def _captures(language, source_query: str, root):
    query = ts.Query(language, source_query)
    cursor = ts.QueryCursor(query)
    return cursor.captures(root)


def _enclosing_class_name(node) -> str | None:
    parent = node.parent
    while parent is not None:
        if parent.type == "class_definition":
            name = parent.child_by_field_name("name")
            return _node_text(name) if name is not None else None
        parent = parent.parent
    return None


def parse_python_query_api(filepath: str, source: str) -> ParsedFile:
    """Parse Python source using the tree-sitter Query API."""
    language = get_language("python")
    parser = get_parser("python")
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    root = tree.root_node

    parsed = ParsedFile(filepath=filepath, language="python")

    # Imports (module-level only).
    import_caps = _captures(language, IMPORT_QUERY, root)
    import_nodes = import_caps.get("import", []) + import_caps.get("import_from", [])
    for node in sorted(import_nodes, key=lambda n: n.start_byte):
        if node.parent is not None and node.parent.type == "module":
            text = _node_text(node).strip()
            if text:
                parsed.imports.append(text)

    # Classes.
    class_caps = _captures(language, CLASS_QUERY, root)
    class_methods: dict[str, list[FunctionInfo]] = {}
    class_nodes = class_caps.get("class", [])
    name_by_class = {c.id: n for c, n in zip(class_nodes, class_caps.get("class_name", []))}
    for class_node in sorted(class_nodes, key=lambda n: n.start_byte):
        name_node = name_by_class.get(class_node.id)
        class_name = _node_text(name_node) if name_node is not None else None
        if not class_name:
            continue
        class_info = ClassInfo(
            name=class_name,
            line_start=class_node.start_point.row + 1,
            line_end=class_node.end_point.row + 1,
        )
        parsed.classes.append(class_info)
        class_methods[class_name] = class_info.methods
        parsed.exports.append(class_name)

    # Functions and methods.
    func_caps = _captures(language, FUNCTION_QUERY, root)
    func_nodes = func_caps.get("func", [])
    name_by_func = {f.id: n for f, n in zip(func_nodes, func_caps.get("func_name", []))}
    for func_node in sorted(func_nodes, key=lambda n: n.start_byte):
        name_node = name_by_func.get(func_node.id)
        func_name = _node_text(name_node) if name_node is not None else None
        if not func_name:
            continue
        parent_class = _enclosing_class_name(func_node)
        params_node = func_node.child_by_field_name("parameters")
        params = _node_text(params_node) if params_node is not None else "()"
        func_info = FunctionInfo(
            name=func_name,
            signature=f"def {func_name}{params}",
            is_method=parent_class is not None,
            parent_class=parent_class,
            line_start=func_node.start_point.row + 1,
            line_end=func_node.end_point.row + 1,
        )
        if parent_class and parent_class in class_methods:
            class_methods[parent_class].append(func_info)
        else:
            parsed.functions.append(func_info)
            parsed.exports.append(func_name)

    return parsed
