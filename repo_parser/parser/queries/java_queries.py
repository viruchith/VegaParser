"""Tree-sitter extraction logic for Java."""

from __future__ import annotations

import re

from repo_parser.models import ParsedFile
from repo_parser.parser.queries.base import (
    iter_nodes,
    line_end,
    line_number,
    node_kind,
    node_parent,
    node_text,
    parse_root,
)
from repo_parser.parser.queries.common_queries import (
    PROFILES,
    LanguageProfile,
    parse_with_profile,
)

JAVA_PROFILE = LanguageProfile(
    language="java",
    import_kinds=("import_declaration",),
    class_kinds=("class_declaration", "interface_declaration", "enum_declaration"),
    function_kinds=("method_declaration", "constructor_declaration"),
    method_kinds=("method_declaration", "constructor_declaration"),
    module_kinds=("program", "source_file"),
    comment_kinds=("line_comment", "block_comment"),
)

PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", re.MULTILINE)


def parse_java(filepath: str, source: str, parser) -> ParsedFile:
    profile = JAVA_PROFILE
    parsed = parse_with_profile(filepath, source, parser, profile)

    # Re-collect imports at program scope (tree-sitter + regex fallback for reliability)
    parsed.imports.clear()
    _tree, root = parse_root(parser, source)
    for node in iter_nodes(root, "import_declaration"):
        parent = node_parent(node)
        if parent and node_kind(parent) in ("program", "module_declaration", "source_file"):
            text = node_text(source, node).strip()
            if text:
                parsed.imports.append(text)

    for match in IMPORT_RE.finditer(source):
        stmt = match.group(0).strip()
        if stmt not in parsed.imports:
            parsed.imports.append(stmt)

    package_match = PACKAGE_RE.search(source)
    if package_match:
        pkg = package_match.group(1)
        parsed.exports.insert(0, f"package:{pkg}")

    # Export fully-qualified class names
    if package_match:
        pkg = package_match.group(1)
        fq_exports = [f"{pkg}.{c.name}" for c in parsed.classes]
        parsed.exports.extend(fq_exports)

    return parsed
