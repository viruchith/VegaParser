"""Unit tests for repo_parser.parser.queries.base helpers."""

from __future__ import annotations

import pytest

from repo_parser.parser.queries.base import (
    build_js_signature,
    build_python_signature,
    child_count,
    find_child_by_kind,
    iter_nodes,
    line_end,
    line_number,
    node_kind,
    node_parent,
    node_text,
    strip_docstring_quotes,
)


# ── Mock node helpers ────────────────────────────────────────────────────────


class MockNode:
    """Minimal tree-sitter node mock (property-based API, no walk)."""

    def __init__(self, kind: str, children=None, start_byte: int = 0, end_byte: int = 0, parent=None):
        self.type = kind
        self._children: list[MockNode] = children or []
        self.start_byte = start_byte
        self.end_byte = end_byte
        self._parent = parent

    @property
    def child_count(self) -> int:
        return len(self._children)

    def child(self, index: int):
        return self._children[index] if 0 <= index < len(self._children) else None

    def child_by_field_name(self, name: str):
        return None

    @property
    def parent(self):
        return self._parent


class MockNodeCallable:
    """Mock node where child_count and parent are callables (method API)."""

    def __init__(self, kind: str, children=None, start_byte: int = 0, end_byte: int = 0, parent=None):
        self._type = kind
        self._children: list = children or []
        self.start_byte = start_byte
        self.end_byte = end_byte
        self._parent = parent

    def kind(self) -> str:
        return self._type

    def child_count(self) -> int:
        return len(self._children)

    def child(self, index: int):
        return self._children[index] if 0 <= index < len(self._children) else None

    def child_by_field_name(self, name: str):
        return None

    def parent(self):
        return self._parent


# ── node_text ────────────────────────────────────────────────────────────────


def test_node_text_none_returns_empty():
    assert node_text("hello world", None) == ""


def test_node_text_basic():
    source = "hello world"
    node = MockNode("identifier", start_byte=6, end_byte=11)
    assert node_text(source, node) == "world"


def test_node_text_start_zero():
    source = "abc"
    node = MockNode("x", start_byte=0, end_byte=3)
    assert node_text(source, node) == "abc"


def test_node_text_negative_start_clamped():
    source = "abc"
    node = MockNode("x", start_byte=-5, end_byte=2)
    assert node_text(source, node) == "ab"


def test_node_text_end_before_start_clamped():
    source = "abc"
    node = MockNode("x", start_byte=2, end_byte=1)
    assert node_text(source, node) == ""


def test_node_text_start_beyond_source_clamped():
    source = "abc"
    node = MockNode("x", start_byte=100, end_byte=200)
    assert node_text(source, node) == ""


def test_node_text_end_beyond_source_clamped():
    source = "abc"
    node = MockNode("x", start_byte=1, end_byte=100)
    assert node_text(source, node) == "bc"


# ── node_kind ────────────────────────────────────────────────────────────────


def test_node_kind_with_type_attribute():
    node = MockNode("identifier")
    assert node_kind(node) == "identifier"


def test_node_kind_with_kind_method():
    node = MockNodeCallable("function_definition")
    assert node_kind(node) == "function_definition"


# ── child_count ──────────────────────────────────────────────────────────────


def test_child_count_property():
    node = MockNode("root", children=[MockNode("a"), MockNode("b")])
    assert child_count(node) == 2


def test_child_count_callable():
    node = MockNodeCallable("root", children=[MockNodeCallable("a")])
    assert child_count(node) == 1


# ── node_parent ──────────────────────────────────────────────────────────────


def test_node_parent_property():
    parent = MockNode("root")
    child = MockNode("leaf", parent=parent)
    assert node_parent(child) is parent


def test_node_parent_callable():
    parent = MockNodeCallable("root")
    child = MockNodeCallable("leaf", parent=parent)
    result = node_parent(child)
    assert result is parent


def test_node_parent_none():
    node = MockNode("root", parent=None)
    assert node_parent(node) is None


# ── iter_nodes ───────────────────────────────────────────────────────────────


def test_iter_nodes_stack_no_filter():
    child1 = MockNode("a")
    child2 = MockNode("b")
    root = MockNode("root", children=[child1, child2])

    nodes = list(iter_nodes(root))
    kinds = [node_kind(n) for n in nodes]
    assert "root" in kinds
    assert "a" in kinds
    assert "b" in kinds


def test_iter_nodes_stack_with_filter():
    child1 = MockNode("identifier")
    child2 = MockNode("keyword")
    root = MockNode("root", children=[child1, child2])

    nodes = list(iter_nodes(root, "identifier"))
    assert len(nodes) == 1
    assert node_kind(nodes[0]) == "identifier"


def test_iter_nodes_empty_children():
    root = MockNode("root")
    nodes = list(iter_nodes(root))
    assert len(nodes) == 1


def test_iter_nodes_nested():
    grandchild = MockNode("grandchild")
    child = MockNode("child", children=[grandchild])
    root = MockNode("root", children=[child])

    nodes = list(iter_nodes(root))
    kinds = {node_kind(n) for n in nodes}
    assert kinds == {"root", "child", "grandchild"}


# ── find_child_by_kind ───────────────────────────────────────────────────────


def test_find_child_by_kind_found():
    child = MockNode("identifier")
    root = MockNode("root", children=[MockNode("keyword"), child])
    result = find_child_by_kind(root, "identifier")
    assert result is child


def test_find_child_by_kind_not_found():
    root = MockNode("root", children=[MockNode("keyword")])
    result = find_child_by_kind(root, "identifier")
    assert result is None


def test_find_child_by_kind_empty():
    root = MockNode("root")
    assert find_child_by_kind(root, "anything") is None


# ── strip_docstring_quotes ───────────────────────────────────────────────────


@pytest.mark.parametrize("input_text,expected", [
    ('"""Hello, world"""', "Hello, world"),
    ("'''Hello'''", "Hello"),
    ('"single"', "single"),
    ("'single'", "single"),
    ("no quotes", "no quotes"),
    ('"""  spaced  """', "spaced"),
    ('""', ""),
    ("''", ""),
    ('"""x"""', "x"),
    # Single-char delimited but content starts same as delimiter
    ('"hello"', "hello"),
    # Mismatched quotes — not stripped
    ('"""only-start', '"""only-start'),
])
def test_strip_docstring_quotes(input_text, expected):
    assert strip_docstring_quotes(input_text) == expected


# ── line_number and line_end ─────────────────────────────────────────────────


def test_line_number_first_line():
    source = "line1\nline2\nline3"
    node = MockNode("x", start_byte=0, end_byte=5)
    assert line_number(source, node) == 1


def test_line_number_second_line():
    source = "line1\nline2\nline3"
    node = MockNode("x", start_byte=6, end_byte=11)
    assert line_number(source, node) == 2


def test_line_end_same_line():
    source = "line1\nline2"
    node = MockNode("x", start_byte=0, end_byte=5)
    assert line_end(source, node) == 1


def test_line_end_clamps_negative():
    source = "abc"
    node = MockNode("x", start_byte=0, end_byte=0)
    # end_byte - 1 = -1 → clamped to 0
    result = line_end(source, node)
    assert result >= 1


# ── build_python_signature ───────────────────────────────────────────────────


class MockFieldNode(MockNode):
    """MockNode that supports child_by_field_name lookup."""

    def __init__(self, kind, children=None, start_byte=0, end_byte=0, fields=None):
        super().__init__(kind, children, start_byte, end_byte)
        self._fields: dict[str, MockNode | None] = fields or {}

    def child_by_field_name(self, name: str):
        return self._fields.get(name)


def test_build_python_signature_no_return():
    source = "def foo(x, y): pass"
    params_node = MockNode("parameters", start_byte=7, end_byte=13)
    func_node = MockFieldNode("function_definition", fields={"parameters": params_node, "return_type": None})
    sig = build_python_signature(source, func_node, "foo")
    assert sig == "def foo(x, y)"


def test_build_python_signature_with_return_type():
    source = "def foo(x) -> int: pass"
    params_node = MockNode("parameters", start_byte=7, end_byte=10)
    ret_node = MockNode("type", start_byte=14, end_byte=17)
    func_node = MockFieldNode("function_definition", fields={"parameters": params_node, "return_type": ret_node})
    sig = build_python_signature(source, func_node, "foo")
    assert "-> int" in sig


def test_build_python_signature_no_params_node():
    source = "def foo(): pass"
    func_node = MockFieldNode("function_definition", fields={"parameters": None, "return_type": None})
    sig = build_python_signature(source, func_node, "foo")
    assert sig == "def foo()"


# ── build_js_signature ───────────────────────────────────────────────────────


def test_build_js_signature_no_return():
    source = "function foo(x) {}"
    params_node = MockNode("formal_parameters", start_byte=12, end_byte=15)
    func_node = MockFieldNode("function_declaration", fields={"parameters": params_node, "return_type": None})
    sig = build_js_signature(source, func_node, "foo")
    assert sig == "function foo(x)"


def test_build_js_signature_with_return_type():
    source = "function foo(x): string {}"
    params_node = MockNode("formal_parameters", start_byte=12, end_byte=15)
    ret_node = MockNode("type_annotation", start_byte=17, end_byte=23)
    func_node = MockFieldNode("function_declaration", fields={"parameters": params_node, "return_type": ret_node})
    sig = build_js_signature(source, func_node, "foo")
    assert "string" in sig


def test_build_js_signature_no_params_node():
    source = "function foo() {}"
    func_node = MockFieldNode("function_declaration", fields={"parameters": None, "return_type": None})
    sig = build_js_signature(source, func_node, "foo")
    assert sig == "function foo()"
