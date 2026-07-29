"""Tree-sitter extraction for Bash and shell scripts."""

from __future__ import annotations

import re

from repo_parser.models import ExternalCall, FunctionInfo, ParsedFile
from repo_parser.parser.queries.base import iter_nodes, line_end, line_number, node_kind, node_text, parse_root

SHELL_FUNCTION_KINDS = ("function_definition", "command_name")

EXTERNAL_PATTERNS = [
    (re.compile(r"\bcurl\b"), "HTTP (curl)"),
    (re.compile(r"\bwget\b"), "HTTP (wget)"),
    (re.compile(r"\bkubectl\b"), "Kubernetes (kubectl)"),
    (re.compile(r"\bdocker\b"), "Docker CLI"),
    (re.compile(r"\bpsql\b"), "Database (psql)"),
    (re.compile(r"\bmysql\b"), "Database (mysql)"),
    (re.compile(r"\baws\b"), "AWS CLI"),
    (re.compile(r"\bgcloud\b"), "GCP CLI"),
    (re.compile(r"\baz\b"), "Azure CLI"),
    (re.compile(r"\bterraform\b"), "Terraform CLI"),
    (re.compile(r"\bhelm\b"), "Helm CLI"),
    (re.compile(r"\bssh\b"), "SSH"),
    (re.compile(r"\bscp\b"), "SCP"),
    (re.compile(r"\bnc\b"), "Netcat"),
    (re.compile(r"\bnpm\b"), "npm"),
    (re.compile(r"\bpip\b"), "pip"),
    (re.compile(r"\bgit\b"), "git"),
]


def parse_shell(filepath: str, source: str, parser, lang_name: str = "bash") -> ParsedFile:
    _tree, root = parse_root(parser, source)

    parsed = ParsedFile(filepath=filepath, language=lang_name)

    for node in iter_nodes(root, "function_definition"):
        name = node.child_by_field_name("name")
        func_name = node_text(source, name).strip() if name else "anonymous"
        parsed.functions.append(
            FunctionInfo(
                name=func_name,
                signature=node_text(source, node).split("{")[0].strip()[:120],
                line_start=line_number(source, node),
                line_end=line_end(source, node),
            )
        )
        parsed.exports.append(func_name)

    # Detect sourced scripts
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("source ") or stripped.startswith(". "):
            parsed.imports.append(stripped)

    seen: set[tuple[int, str]] = set()
    for line_no, line in enumerate(source.splitlines(), 1):
        for pattern, label in EXTERNAL_PATTERNS:
            if pattern.search(line):
                key = (line_no, label)
                if key not in seen:
                    seen.add(key)
                    parsed.external_calls.append(
                        ExternalCall(pattern=label, line=line_no, context=line.strip()[:120])
                    )

    if parsed.external_calls:
        tools = sorted({c.pattern for c in parsed.external_calls})
        parsed.module_docstring = "Shell script using: " + ", ".join(tools)

    return parsed
