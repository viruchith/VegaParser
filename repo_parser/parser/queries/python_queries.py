"""Tree-sitter extraction logic for Python."""

from __future__ import annotations

import re

from repo_parser.models import ClassInfo, ExternalCall, FunctionInfo, ParsedFile
from repo_parser.parser.queries.base import (
    build_python_signature,
    child_count,
    find_child_by_kind,
    iter_nodes,
    line_end,
    line_number,
    node_kind,
    node_parent,
    node_text,
    parse_root,
    strip_docstring_quotes,
)

EXTERNAL_CALL_PATTERNS = [
    (re.compile(r"\brequests\.(get|post|put|patch|delete|head|request)\b"), "HTTP (requests)"),
    (re.compile(r"\bhttpx\.(get|post|put|patch|delete|head|request|AsyncClient)\b"), "HTTP (httpx)"),
    (re.compile(r"\burllib\.(request|urlopen)\b"), "HTTP (urllib)"),
    (re.compile(r"\baiohttp\.(ClientSession|request)\b"), "HTTP (aiohttp)"),
    (re.compile(r"\bcursor\.execute\b"), "Database (cursor.execute)"),
    (re.compile(r"\bsession\.(query|execute|commit)\b"), "Database (ORM session)"),
    (re.compile(r"\b(Session|engine)\.(query|execute)\b"), "Database (SQLAlchemy)"),
    (re.compile(r"\bMongoClient\b"), "Database (MongoDB)"),
    (re.compile(r"\bredis\.(Redis|StrictRedis)\b"), "Database (Redis)"),
    (re.compile(r"\bboto3\.(client|resource)\b"), "AWS SDK (boto3)"),
    (re.compile(r"\bopenai\.\w+\b"), "API (OpenAI SDK)"),
    (re.compile(r"\banthropic\.\w+\b"), "API (Anthropic SDK)"),
]


def _is_module_level(node) -> bool:
    parent = node_parent(node)
    return parent is not None and node_kind(parent) == "module"


def _extract_module_docstring(root, source: str) -> str | None:
    for i in range(child_count(root)):
        child = root.child(i)
        if node_kind(child) == "expression_statement":
            string_node = find_child_by_kind(child, "string")
            if string_node:
                return strip_docstring_quotes(node_text(source, string_node))
        if node_kind(child) not in ("comment", "future_import_statement"):
            break
    return None


def _extract_decorators(source: str, node) -> list[str]:
    decorators = []
    for i in range(child_count(node)):
        child = node.child(i)
        if node_kind(child) == "decorator":
            decorators.append(node_text(source, child).strip())
    return decorators


def _extract_block_docstring(source: str, body) -> str | None:
    if body is None:
        return None
    for i in range(child_count(body)):
        child = body.child(i)
        if node_kind(child) == "expression_statement":
            string_node = find_child_by_kind(child, "string")
            if string_node:
                return strip_docstring_quotes(node_text(source, string_node))
        if node_kind(child) != "comment":
            break
    return None


def _extract_bases(source: str, class_node) -> list[str]:
    arg_list = class_node.child_by_field_name("superclasses")
    if not arg_list:
        return []
    bases = []
    for i in range(child_count(arg_list)):
        child = arg_list.child(i)
        if node_kind(child) in ("identifier", "attribute", "call"):
            bases.append(node_text(source, child))
    return bases


def _extract_internal_calls(source: str, func_node) -> list[str]:
    calls: set[str] = set()
    for node in iter_nodes(func_node, "call"):
        func = node.child_by_field_name("function")
        if func:
            name = node_text(source, func)
            if re.match(r"^[a-zA-Z_]\w*$", name):
                calls.add(name)
    return sorted(calls)


def _is_nested_function(func_node) -> bool:
    """Skip function definitions nested inside other functions."""
    parent = node_parent(func_node)
    while parent:
        if node_kind(parent) == "function_definition":
            return True
        if node_kind(parent) in ("module", "class_definition", "decorated_definition"):
            return False
        parent = node_parent(parent)
    return False


def _is_method(func_node) -> bool:
    parent = node_parent(func_node)
    while parent:
        if node_kind(parent) == "class_definition":
            return True
        if node_kind(parent) in ("module", "decorated_definition"):
            return False
        parent = node_parent(parent)
    return False


def _parent_class_name(func_node, source: str) -> str | None:
    parent = node_parent(func_node)
    while parent:
        if node_kind(parent) == "class_definition":
            name_node = parent.child_by_field_name("name")
            return node_text(source, name_node) if name_node else None
        parent = node_parent(parent)
    return None


def parse_python(filepath: str, source: str, parser) -> ParsedFile:
    _tree, root = parse_root(parser, source)

    parsed = ParsedFile(
        filepath=filepath,
        language="python",
        module_docstring=_extract_module_docstring(root, source),
    )

    class_methods: dict[str, list[FunctionInfo]] = {}
    external_seen: set[tuple[int, str]] = set()

    for node in iter_nodes(root):
        kind = node_kind(node)
        if kind in ("import_statement", "import_from_statement") and _is_module_level(node):
            imp = node_text(source, node).strip()
            if imp:
                parsed.imports.append(imp)
            continue

        if kind == "class_definition":
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            class_name = node_text(source, name_node)
            class_info = ClassInfo(
                name=class_name,
                docstring=_extract_block_docstring(source, node.child_by_field_name("body")),
                bases=_extract_bases(source, node),
                decorators=_extract_decorators(source, node),
                line_start=line_number(source, node),
                line_end=line_end(source, node),
            )
            parsed.classes.append(class_info)
            class_methods[class_name] = class_info.methods
            continue

        if kind == "function_definition":
            if _is_nested_function(node):
                continue
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            func_name = node_text(source, name_node)
            is_method = _is_method(node)
            parent = _parent_class_name(node, source)
            func_info = FunctionInfo(
                name=func_name,
                signature=build_python_signature(source, node, func_name),
                docstring=_extract_block_docstring(source, node.child_by_field_name("body")),
                decorators=_extract_decorators(source, node),
                is_method=is_method,
                parent_class=parent,
                line_start=line_number(source, node),
                line_end=line_end(source, node),
                internal_calls=_extract_internal_calls(source, node),
            )

            if is_method and parent and parent in class_methods:
                class_methods[parent].append(func_info)
            else:
                parsed.functions.append(func_info)

            if not is_method:
                parsed.exports.append(func_name)
            continue

        if kind == "call":
            func_node = node.child_by_field_name("function")
            if not func_node:
                continue
            call_text = node_text(source, func_node)
            line = line_number(source, node)

            for pattern, label in EXTERNAL_CALL_PATTERNS:
                if pattern.search(call_text):
                    key = (line, label)
                    if key not in external_seen:
                        external_seen.add(key)
                        context = node_text(source, node).strip()[:120]
                        parsed.external_calls.append(ExternalCall(pattern=label, line=line, context=context))
                    break

    for cls in parsed.classes:
        parsed.exports.append(cls.name)

    return parsed
