"""Tree-sitter node helpers for tree-sitter-language-pack."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator
from functools import lru_cache


@lru_cache(maxsize=32)
def _encode(source: str) -> bytes:
    return source.encode("utf-8")


@lru_cache(maxsize=32)
def _newline_offsets(source: str) -> tuple[int, ...]:
    source_bytes = _encode(source)
    offsets: list[int] = []
    idx = source_bytes.find(b"\n")
    while idx != -1:
        offsets.append(idx)
        idx = source_bytes.find(b"\n", idx + 1)
    return tuple(offsets)


def node_text(source: str, node) -> str:
    if node is None:
        return ""
    # A single file's source is re-encoded on every node lookup (imports,
    # classes, functions, calls, ...). Caching by source avoids O(file size)
    # work per call; str hashing is cheap here since CPython caches the hash
    # of a str object after it's first computed.
    source_bytes = _encode(source)
    start = int(node.start_byte)
    end = int(node.end_byte)
    if start < 0:
        start = 0
    if end < start:
        end = start
    max_len = len(source_bytes)
    if start > max_len:
        start = max_len
    if end > max_len:
        end = max_len
    return source_bytes[start:end].decode("utf-8", errors="replace")


def node_kind(node) -> str:
    kind = getattr(node, "type", None)
    if kind is not None:
        return kind
    return node.kind()


def parse_root(parser, source: str):
    tree = parser.parse(_encode(source))
    root = getattr(tree, "root_node")
    return tree, (root() if callable(root) else root)


def child_count(node) -> int:
    count = getattr(node, "child_count", None)
    return count() if callable(count) else count


def node_parent(node):
    parent = getattr(node, "parent")
    return parent() if callable(parent) else parent


def iter_nodes(node, kind: str | None = None) -> Iterator:
    """Depth-first traversal, optionally filtering by node kind."""
    if hasattr(node, "walk"):
        cursor = node.walk()
        reached_root = False
        while not reached_root:
            current = cursor.node
            if kind is None or node_kind(current) == kind:
                yield current
            if cursor.goto_first_child():
                continue
            if cursor.goto_next_sibling():
                continue
            while True:
                if not cursor.goto_parent():
                    reached_root = True
                    break
                if cursor.goto_next_sibling():
                    break
        return

    stack = [node]
    while stack:
        current = stack.pop()
        if kind is None or node_kind(current) == kind:
            yield current
        for i in range(child_count(current) - 1, -1, -1):
            stack.append(current.child(i))


def find_child_by_kind(node, kind: str):
    for i in range(child_count(node)):
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


def line_number(source: str, node) -> int:
    start = int(node.start_byte)
    return bisect_right(_newline_offsets(source), start) + 1


def line_end(source: str, node) -> int:
    end = int(node.end_byte) - 1
    if end < 0:
        end = 0
    return bisect_right(_newline_offsets(source), end) + 1


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
