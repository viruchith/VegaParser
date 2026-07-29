"""Compatibility adapter for py-tree-sitter.

The query modules in this package were written against a method-based tree
API (``node.kind()``, ``node.child_count()``, ``tree.root_node()``,
``parser.parse(str)``). Modern ``py-tree-sitter`` (>= 0.22) exposes these as
properties and requires ``bytes`` for parsing. These thin adapters bridge the
two so the query modules keep working unchanged.
"""

from __future__ import annotations

from collections import namedtuple

_ByteRange = namedtuple("_ByteRange", ["start", "end"])


class NodeAdapter:
    """Wrap a tree-sitter ``Node`` exposing a method-based API."""

    __slots__ = ("_node",)

    def __init__(self, node) -> None:
        self._node = node

    def kind(self) -> str:
        return self._node.type

    def child_count(self) -> int:
        return self._node.child_count

    def child(self, index: int):
        child = self._node.child(index)
        return NodeAdapter(child) if child is not None else None

    def child_by_field_name(self, name: str):
        child = self._node.child_by_field_name(name)
        return NodeAdapter(child) if child is not None else None

    def parent(self):
        parent = self._node.parent
        return NodeAdapter(parent) if parent is not None else None

    def byte_range(self) -> _ByteRange:
        return _ByteRange(self._node.start_byte, self._node.end_byte)

    @property
    def start_byte(self) -> int:
        return self._node.start_byte

    @property
    def end_byte(self) -> int:
        return self._node.end_byte

    def start_position(self):
        return self._node.start_point

    def end_position(self):
        return self._node.end_point


class TreeAdapter:
    """Wrap a tree-sitter ``Tree`` exposing ``root_node()`` as a method."""

    __slots__ = ("_tree",)

    def __init__(self, tree) -> None:
        self._tree = tree

    def root_node(self) -> NodeAdapter:
        return NodeAdapter(self._tree.root_node)


class ParserAdapter:
    """Wrap a tree-sitter ``Parser`` accepting ``str`` sources."""

    __slots__ = ("_parser",)

    def __init__(self, parser) -> None:
        self._parser = parser

    def parse(self, source) -> TreeAdapter:
        if isinstance(source, str):
            source = source.encode("utf-8")
        return TreeAdapter(self._parser.parse(source))
