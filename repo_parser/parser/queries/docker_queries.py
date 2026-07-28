"""Tree-sitter extraction for Dockerfiles."""

from __future__ import annotations

from repo_parser.models import FunctionInfo, ParsedFile
from repo_parser.parser.queries.base import iter_nodes, line_end, line_number, node_kind, node_text

INSTRUCTION_LABELS = {
    "from_instruction": "FROM",
    "run_instruction": "RUN",
    "cmd_instruction": "CMD",
    "entrypoint_instruction": "ENTRYPOINT",
    "copy_instruction": "COPY",
    "add_instruction": "ADD",
    "expose_instruction": "EXPOSE",
    "env_instruction": "ENV",
    "arg_instruction": "ARG",
    "workdir_instruction": "WORKDIR",
    "user_instruction": "USER",
    "volume_instruction": "VOLUME",
    "label_instruction": "LABEL",
    "healthcheck_instruction": "HEALTHCHECK",
    "shell_instruction": "SHELL",
    "onbuild_instruction": "ONBUILD",
    "stopsignal_instruction": "STOPSIGNAL",
}


def parse_dockerfile(filepath: str, source: str, parser) -> ParsedFile:
    tree = parser.parse(source)
    root = tree.root_node()

    parsed = ParsedFile(filepath=filepath, language="dockerfile")
    instructions: list[str] = []

    for node in iter_nodes(root):
        kind = node_kind(node)
        if kind in INSTRUCTION_LABELS:
            text = node_text(source, node).strip()
            label = INSTRUCTION_LABELS[kind]
            instructions.append(f"{label}: {text}")
            parsed.functions.append(
                FunctionInfo(
                    name=label,
                    signature=text,
                    line_start=line_number(node),
                    line_end=line_end(node),
                )
            )
            parsed.exports.append(label)
        elif kind == "from_instruction":
            parsed.imports.append(node_text(source, node).strip())
        elif kind == "expose_instruction":
            parsed.exports.append(node_text(source, node).strip())

    if instructions:
        parsed.module_docstring = "Dockerfile build instructions:\n" + "\n".join(instructions[:20])

    return parsed
