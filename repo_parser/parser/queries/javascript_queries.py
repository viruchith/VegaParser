"""Tree-sitter extraction logic for JavaScript/TypeScript."""

from __future__ import annotations

import re

from repo_parser.models import ClassInfo, ExternalCall, FunctionInfo, ParsedFile
from repo_parser.parser.queries.base import (
    build_js_signature,
    find_child_by_kind,
    iter_nodes,
    line_end,
    line_number,
    node_kind,
    node_text,
    strip_docstring_quotes,
)

EXTERNAL_CALL_PATTERNS = [
    (re.compile(r"\bfetch\s*\("), "HTTP (fetch)"),
    (re.compile(r"\baxios\.(get|post|put|patch|delete|request)\b"), "HTTP (axios)"),
    (re.compile(r"\bhttp\.(get|post|request)\b"), "HTTP (node http)"),
    (re.compile(r"\bhttps\.(get|post|request)\b"), "HTTP (node https)"),
    (re.compile(r"\bpg\.(query|connect)\b"), "Database (PostgreSQL)"),
    (re.compile(r"\bmongoose\.(connect|model)\b"), "Database (Mongoose)"),
    (re.compile(r"\bprisma\.\w+\b"), "Database (Prisma)"),
    (re.compile(r"\bsupabase\.\w+\b"), "API (Supabase)"),
    (re.compile(r"\bopenai\.\w+\b"), "API (OpenAI SDK)"),
]


def _extract_leading_comment(root, source: str) -> str | None:
    comments = []
    for i in range(root.child_count()):
        child = root.child(i)
        if node_kind(child) == "comment":
            text = node_text(source, child).strip()
            if text.startswith("/**"):
                return re.sub(r"^/\*\*|\*/$|^\s*\*\s?", "", text, flags=re.MULTILINE).strip()
            comments.append(text.lstrip("/").strip())
        elif node_kind(child) in ("import_statement", "import_clause", "lexical_declaration"):
            continue
        else:
            break
    return "\n".join(comments) if comments else None


def _extract_class_docstring(source: str, class_node) -> str | None:
    for i in range(class_node.child_count()):
        child = class_node.child(i)
        if node_kind(child) == "comment":
            text = node_text(source, child)
            if "/**" in text:
                return strip_docstring_quotes(text)
    return None


def _is_exported(node) -> bool:
    parent = node.parent()
    while parent:
        if node_kind(parent) in ("export_statement", "export_declaration"):
            return True
        if node_kind(parent) == "program":
            return False
        parent = parent.parent()
    return False


def _is_require_call(node, source: str) -> bool:
    if node_kind(node) != "call_expression":
        return False
    func = node.child_by_field_name("function")
    return func is not None and node_text(source, func) == "require"


def _parent_class_name(node, source: str) -> str | None:
    parent = node.parent()
    while parent:
        if node_kind(parent) == "class_declaration":
            name_node = parent.child_by_field_name("name")
            return node_text(source, name_node) if name_node else None
        parent = parent.parent()
    return None


def parse_javascript(filepath: str, source: str, parser, lang_name: str = "javascript") -> ParsedFile:
    tree = parser.parse(source)
    root = tree.root_node()

    parsed = ParsedFile(
        filepath=filepath,
        language=lang_name,
        module_docstring=_extract_leading_comment(root, source),
    )

    class_methods: dict[str, list[FunctionInfo]] = {}
    external_seen: set[tuple[int, str]] = set()

    for node in iter_nodes(root):
        kind = node_kind(node)
        if kind == "import_statement":
            imp = node_text(source, node).strip()
            if imp:
                parsed.imports.append(imp)
            continue

        if _is_require_call(node, source):
            imp = node_text(source, node).strip()
            if imp:
                parsed.imports.append(imp)
            continue

        if kind == "class_declaration":
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            class_name = node_text(source, name_node)
            class_info = ClassInfo(
                name=class_name,
                docstring=_extract_class_docstring(source, node),
                methods=class_methods.setdefault(class_name, []),
                line_start=line_number(node),
                line_end=line_end(node),
            )
            parsed.classes.append(class_info)
            parsed.exports.append(class_name)
            continue

        if kind == "method_definition":
            class_name = _parent_class_name(node, source)
            name_node = node.child_by_field_name("name")
            if not class_name or not name_node:
                continue
            method_name = node_text(source, name_node)
            class_methods.setdefault(class_name, []).append(
                FunctionInfo(
                    name=method_name,
                    signature=build_js_signature(source, node, method_name),
                    is_method=True,
                    parent_class=class_name,
                    line_start=line_number(node),
                    line_end=line_end(node),
                )
            )
            continue

        if kind == "function_declaration":
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            func_name = node_text(source, name_node)
            parsed.functions.append(
                FunctionInfo(
                    name=func_name,
                    signature=build_js_signature(source, node, func_name),
                    line_start=line_number(node),
                    line_end=line_end(node),
                )
            )
            if _is_exported(node):
                parsed.exports.append(func_name)
            continue

        if kind == "call_expression":
            func_node = node.child_by_field_name("function")
            if not func_node:
                continue
            call_text = node_text(source, func_node)
            full_call = node_text(source, node)
            line = line_number(node)
            for pattern, label in EXTERNAL_CALL_PATTERNS:
                if pattern.search(call_text) or pattern.search(full_call):
                    key = (line, label)
                    if key not in external_seen:
                        external_seen.add(key)
                        parsed.external_calls.append(
                            ExternalCall(pattern=label, line=line, context=full_call.strip()[:120])
                        )
                    break

    return parsed
