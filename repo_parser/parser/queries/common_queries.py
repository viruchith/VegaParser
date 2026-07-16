"""Profile-driven tree-sitter extraction for C-family and OO languages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from repo_parser.models import ClassInfo, FunctionInfo, ParsedFile
from repo_parser.parser.queries.base import (
    iter_nodes,
    line_end,
    line_number,
    node_kind,
    node_text,
    strip_docstring_quotes,
)

NAME_FIELDS = ("name", "declarator", "identifier")


@dataclass(frozen=True)
class LanguageProfile:
    language: str
    import_kinds: tuple[str, ...] = ()
    class_kinds: tuple[str, ...] = ()
    function_kinds: tuple[str, ...] = ()
    method_kinds: tuple[str, ...] = ()
    struct_kinds: tuple[str, ...] = ()
    module_kinds: tuple[str, ...] = ("source_file", "program", "module")
    comment_kinds: tuple[str, ...] = ("comment", "line_comment", "block_comment", "doc_comment")
    skip_nested_functions: bool = True


def _get_name(source: str, node) -> str | None:
    for field_name in NAME_FIELDS:
        child = node.child_by_field_name(field_name)
        if child is not None:
            text = node_text(source, child).strip()
            if text:
                return text.lstrip("#").strip()
    for i in range(node.child_count()):
        child = node.child(i)
        kind = node_kind(child)
        if kind in ("identifier", "type_identifier", "property_identifier", "field_identifier", "name"):
            text = node_text(source, child).strip()
            if text and text not in ("func", "fn", "def", "class", "struct", "interface", "public", "private"):
                return text
    return None


def _is_module_level(node, module_kinds: tuple[str, ...]) -> bool:
    parent = node.parent()
    while parent:
        if node_kind(parent) in module_kinds:
            return True
        if node_kind(parent) in ("class_declaration", "class_definition", "class", "class_body", "declaration_list", "impl_item", "block"):
            return False
        parent = parent.parent()
    return False


def _is_nested_function(node) -> bool:
    parent = node.parent()
    while parent:
        if node_kind(parent) in ("function_declaration", "function_definition", "function_item", "method_declaration", "function"):
            return True
        if node_kind(parent) in ("source_file", "program", "module", "class_declaration", "class_definition", "class", "impl_item"):
            return False
        parent = parent.parent()
    return False


def _parent_class(source: str, node) -> str | None:
    parent = node.parent()
    while parent:
        kind = node_kind(parent)
        if kind in ("class_declaration", "class_definition", "class", "class_specifier", "struct_item", "impl_item"):
            name = _get_name(source, parent)
            if name:
                return name
        parent = parent.parent()
    return None


def _extract_leading_comments(root, source: str, comment_kinds: tuple[str, ...]) -> str | None:
    comments: list[str] = []
    for i in range(root.child_count()):
        child = root.child(i)
        kind = node_kind(child)
        if kind in comment_kinds:
            text = node_text(source, child).strip().lstrip("/").lstrip("#").strip("* ").strip()
            if text.startswith("/**"):
                return re.sub(r"^/\*\*|\*/$|^\s*\*\s?", "", node_text(source, child), flags=re.MULTILINE).strip()
            comments.append(text)
        elif kind in ("import_declaration", "import", "import_statement", "package_clause", "package", "use_declaration"):
            continue
        else:
            break
    return "\n".join(comments) if comments else None


def _build_signature(source: str, node, name: str, language: str) -> str:
    params = node.child_by_field_name("parameters") or node.child_by_field_name("params")
    ret = node.child_by_field_name("return_type") or node.child_by_field_name("type")
    param_text = node_text(source, params).strip() if params else "()"
    ret_text = node_text(source, ret).strip() if ret else ""
    if language == "go":
        return f"func {name}{param_text}" + (f" {ret_text}" if ret_text else "")
    if language == "rust":
        return f"fn {name}{param_text}" + (f" -> {ret_text}" if ret_text else "")
    if language in ("java", "kotlin", "scala", "swift", "csharp"):
        return f"{name}{param_text}" + (f": {ret_text}" if ret_text else "")
    return f"{name}{param_text}"


def parse_with_profile(filepath: str, source: str, parser, profile: LanguageProfile) -> ParsedFile:
    tree = parser.parse(source)
    root = tree.root_node()

    parsed = ParsedFile(
        filepath=filepath,
        language=profile.language,
        module_docstring=_extract_leading_comments(root, source, profile.comment_kinds),
    )

    for node in iter_nodes(root):
        kind = node_kind(node)
        if kind in profile.import_kinds and _is_module_level(node, profile.module_kinds):
            text = node_text(source, node).strip()
            if text:
                parsed.imports.append(text)

    class_methods: dict[str, list[FunctionInfo]] = {}

    all_class_kinds = profile.class_kinds + profile.struct_kinds
    for node in iter_nodes(root):
        kind = node_kind(node)
        if kind not in all_class_kinds:
            continue
        name = _get_name(source, node)
        if not name:
            continue
        class_info = ClassInfo(
            name=name,
            line_start=line_number(node),
            line_end=line_end(node),
        )
        parsed.classes.append(class_info)
        class_methods[name] = class_info.methods
        parsed.exports.append(name)

    func_kinds = set(profile.function_kinds + profile.method_kinds)
    for node in iter_nodes(root):
        kind = node_kind(node)
        if kind not in func_kinds:
            continue
        if profile.skip_nested_functions and kind in profile.function_kinds and _is_nested_function(node):
            continue
        name = _get_name(source, node)
        if not name:
            continue
        is_method = kind in profile.method_kinds or _parent_class(source, node) is not None
        parent = _parent_class(source, node)
        func_info = FunctionInfo(
            name=name,
            signature=_build_signature(source, node, name, profile.language),
            is_method=is_method,
            parent_class=parent,
            line_start=line_number(node),
            line_end=line_end(node),
        )
        if is_method and parent and parent in class_methods:
            class_methods[parent].append(func_info)
        else:
            parsed.functions.append(func_info)
            parsed.exports.append(name)

    return parsed


PROFILES: dict[str, LanguageProfile] = {
    "go": LanguageProfile(
        language="go",
        import_kinds=("import_declaration", "import_spec"),
        class_kinds=("type_declaration",),
        struct_kinds=(),
        function_kinds=("function_declaration", "method_declaration"),
        method_kinds=("method_declaration",),
        module_kinds=("source_file",),
        comment_kinds=("comment",),
    ),
    "rust": LanguageProfile(
        language="rust",
        import_kinds=("use_declaration",),
        class_kinds=("struct_item", "enum_item", "trait_item"),
        function_kinds=("function_item",),
        method_kinds=("function_item",),
        module_kinds=("source_file",),
        comment_kinds=("line_comment", "block_comment", "doc_comment"),
    ),
    "ruby": LanguageProfile(
        language="ruby",
        import_kinds=(),  # require handled separately
        class_kinds=("class", "module"),
        function_kinds=("method",),
        method_kinds=("method",),
        comment_kinds=("comment",),
    ),
    "cpp": LanguageProfile(
        language="cpp",
        import_kinds=("preproc_include",),
        class_kinds=("class_specifier", "struct_specifier"),
        function_kinds=("function_definition",),
        comment_kinds=("comment",),
    ),
    "c": LanguageProfile(
        language="c",
        import_kinds=("preproc_include",),
        class_kinds=("struct_specifier",),
        function_kinds=("function_definition",),
        comment_kinds=("comment",),
    ),
    "csharp": LanguageProfile(
        language="csharp",
        import_kinds=("using_directive",),
        class_kinds=("class_declaration", "struct_declaration", "interface_declaration"),
        function_kinds=("method_declaration", "constructor_declaration"),
        method_kinds=("method_declaration", "constructor_declaration"),
        comment_kinds=("comment",),
    ),
    "php": LanguageProfile(
        language="php",
        import_kinds=("namespace_use_declaration",),
        class_kinds=("class_declaration", "interface_declaration", "trait_declaration"),
        function_kinds=("function_definition",),
        method_kinds=("method_declaration",),
        comment_kinds=("comment",),
    ),
    "kotlin": LanguageProfile(
        language="kotlin",
        import_kinds=("import_header",),
        class_kinds=("class_declaration", "object_declaration"),
        function_kinds=("function_declaration",),
        method_kinds=("function_declaration",),
        comment_kinds=("line_comment", "multiline_comment"),
    ),
    "scala": LanguageProfile(
        language="scala",
        import_kinds=("import_declaration",),
        class_kinds=("class_definition", "object_definition", "trait_definition"),
        function_kinds=("function_definition",),
        comment_kinds=("line_comment", "block_comment"),
    ),
    "swift": LanguageProfile(
        language="swift",
        import_kinds=("import_declaration",),
        class_kinds=("class_declaration", "struct_declaration", "enum_declaration", "protocol_declaration"),
        function_kinds=("function_declaration", "initializer_declaration"),
        method_kinds=("function_declaration", "initializer_declaration"),
        comment_kinds=("line_comment", "multiline_comment"),
    ),
}


def parse_common(filepath: str, source: str, parser, lang_name: str) -> ParsedFile:
    profile = PROFILES[lang_name]
    parsed = parse_with_profile(filepath, source, parser, profile)

    if lang_name == "ruby":
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("require ") or stripped.startswith("require_relative "):
                parsed.imports.append(stripped)

    return parsed
