"""Tree-sitter node helpers for tree-sitter-language-pack."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache


@lru_cache(maxsize=4)
def _encode(source: str) -> bytes:
    return source.encode("utf-8")


def node_text(source: str, node) -> str:
    if node is None:
        return ""
    # A single file's source is re-encoded on every node lookup (imports,
    # classes, functions, calls, ...). Caching by source avoids O(file size)
    # work per call; str hashing is cheap here since CPython caches the hash
    # of a str object after it's first computed.
    source_bytes = _encode(source)
    byte_range = node.byte_range()
    return source_bytes[byte_range.start : byte_range.end].decode("utf-8", errors="replace")


def node_kind(node) -> str:
    return node.kind()


def iter_nodes(node, kind: str | None = None) -> Iterator:
    """Depth-first traversal, optionally filtering by node kind."""
    stack = [node]
    while stack:
        current = stack.pop()
        if kind is None or node_kind(current) == kind:
            yield current
        for i in range(current.child_count() - 1, -1, -1):
            stack.append(current.child(i))


def find_child_by_kind(node, kind: str):
    for i in range(node.child_count()):
        child = node.child(i)
        if node_kind(child) == kind:
            return child
    return None


def strip_docstring_quotes(text: str) -> str:
    text = text.strip()
    for prefix in ('"""', "'''", '"', "'"):
        if text.startswith(prefix) and text.endswith(prefix) and len(text) >= 2 * len(prefix):
            return text[len(prefix) : -len(prefix)].strip()
    return text


def line_number(node) -> int:
    return node.start_position().row + 1


def line_end(node) -> int:
    return node.end_position().row + 1


def build_python_signature(source: str, node, name: str) -> str:
    params_node = node.child_by_field_name("parameters")
    return_type_node = node.child_by_field_name("return_type")
    params = node_text(source, params_node) if params_node else "()"
    ret = node_text(source, return_type_node).strip() if return_type_node else ""
    if ret:
        return f"def {name}{params} -> {ret}"
    return f"def {name}{params}"


def build_js_signature(source: str, node, name: str) -> str:
    params_node = node.child_by_field_name("parameters")
    params = node_text(source, params_node) if params_node else "()"
    return_type = node.child_by_field_name("return_type")
    ret = node_text(source, return_type).strip() if return_type else ""
    if ret:
        return f"function {name}{params}: {ret}"
    return f"function {name}{params}"
