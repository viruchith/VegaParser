"""Safe Java extractor that avoids tree-sitter native parser crashes.

This fallback is intentionally conservative: it extracts imports, classes, and
method signatures using regex so large/legacy Java files cannot crash the
indexing process.
"""

from __future__ import annotations

import re

from repo_parser.models import ClassInfo, FunctionInfo, ParsedFile

IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z0-9_.*]+)\s*;\s*$")
CLASS_RE = re.compile(
    r"^\s*(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed)\s+)*"
    r"(class|interface|enum)\s+([A-Za-z_]\w*)"
)
METHOD_RE = re.compile(
    r"^\s*(?:(?:public|protected|private)\s+)?(?:(?:static|final|abstract|synchronized|native|default)\s+)*"
    r"(?:[A-Za-z_][\w<>\[\],.? ]+\s+)?([A-Za-z_]\w*)\s*\(([^{};]*)\)\s*"
    r"(?:throws\s+[^{]+)?\s*\{?\s*$"
)
CONTROL_NAMES = {"if", "for", "while", "switch", "catch", "return", "throw", "new"}


def _extract_module_docstring(source: str) -> str | None:
    text = source.lstrip()
    if text.startswith("/*"):
        end = text.find("*/")
        if end != -1:
            return text[2:end].strip()
    return None


def parse_java_fallback(filepath: str, source: str, _parser=None) -> ParsedFile:
    parsed = ParsedFile(
        filepath=filepath,
        language="java",
        module_docstring=_extract_module_docstring(source),
    )

    class_index: dict[str, ClassInfo] = {}
    class_stack: list[tuple[str, int]] = []
    brace_depth = 0

    for line_no, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()

        import_match = IMPORT_RE.match(line)
        if import_match:
            parsed.imports.append(stripped)

        class_match = CLASS_RE.match(line)
        if class_match:
            class_name = class_match.group(2)
            class_info = ClassInfo(name=class_name, line_start=line_no, line_end=line_no)
            parsed.classes.append(class_info)
            parsed.exports.append(class_name)
            class_index[class_name] = class_info
            open_braces = line.count("{")
            body_depth = brace_depth + open_braces if open_braces else brace_depth + 1
            class_stack.append((class_name, body_depth))

        method_match = METHOD_RE.match(line)
        if method_match:
            name = method_match.group(1)
            params = method_match.group(2).strip()
            if name not in CONTROL_NAMES:
                parent_class = class_stack[-1][0] if class_stack else None
                is_method = parent_class is not None
                signature = f"{name}({params})"
                method = FunctionInfo(
                    name=name,
                    signature=signature,
                    is_method=is_method,
                    parent_class=parent_class,
                    line_start=line_no,
                    line_end=line_no,
                )
                if is_method and parent_class in class_index:
                    class_index[parent_class].methods.append(method)
                else:
                    parsed.functions.append(method)
                    parsed.exports.append(name)

        brace_depth += line.count("{")
        brace_depth -= line.count("}")

        while class_stack and brace_depth < class_stack[-1][1]:
            class_name, _ = class_stack.pop()
            class_index[class_name].line_end = line_no

    if class_stack:
        end_line = len(source.splitlines()) or 1
        for class_name, _ in class_stack:
            class_index[class_name].line_end = end_line

    return parsed
