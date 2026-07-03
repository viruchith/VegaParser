"""Tree-sitter extraction for HCL and Terraform."""

from __future__ import annotations

from repo_parser.models import ClassInfo, FunctionInfo, ParsedFile
from repo_parser.parser.queries.base import iter_nodes, line_end, line_number, node_kind, node_text

BLOCK_TYPES = {
    "resource", "data", "module", "provider", "variable", "output",
    "locals", "terraform", "backend", "moved", "import", "check",
}


def parse_hcl(filepath: str, source: str, parser, lang_name: str = "hcl") -> ParsedFile:
    tree = parser.parse(source)
    root = tree.root_node()

    parsed = ParsedFile(filepath=filepath, language=lang_name)

    for node in iter_nodes(root, "block"):
        children_text = []
        block_type = None
        block_name = None

        for i in range(node.child_count()):
            child = node.child(i)
            kind = node_kind(child)
            text = node_text(source, child).strip().strip('"')
            if kind == "identifier" and block_type is None:
                block_type = text
            elif kind in ("string_lit", "identifier") and block_type and block_name is None:
                if text and text not in BLOCK_TYPES:
                    block_name = text
            children_text.append(text)

        if not block_type:
            continue

        signature = node_text(source, node).split("{")[0].strip()
        full_name = f"{block_type}.{block_name}" if block_name else block_type

        parsed.classes.append(
            ClassInfo(
                name=full_name,
                docstring=f"{block_type} block",
                line_start=line_number(node),
                line_end=line_end(node),
            )
        )
        parsed.functions.append(
            FunctionInfo(
                name=block_type,
                signature=signature[:200],
                line_start=line_number(node),
                line_end=line_end(node),
            )
        )
        parsed.exports.append(full_name)

        if block_type == "module" and block_name:
            parsed.imports.append(f'module "{block_name}"')
        if block_type == "provider" and block_name:
            parsed.imports.append(f'provider "{block_name}"')

    if parsed.classes:
        parsed.module_docstring = (
            f"Infrastructure as Code ({lang_name}): "
            + ", ".join(c.name for c in parsed.classes[:10])
        )

    return parsed
