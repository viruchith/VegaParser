"""Tree-sitter parsing engine orchestration."""

from __future__ import annotations

import logging

from tree_sitter_language_pack import get_parser, has_language

from repo_parser.models import ParsedFile
from repo_parser.parser.extractors.endpoints import enrich_parsed_file
from repo_parser.parser.queries.common_queries import PROFILES, parse_common
from repo_parser.parser.queries.docker_queries import parse_dockerfile
from repo_parser.parser.queries.env_queries import parse_env
from repo_parser.parser.queries.hcl_queries import parse_hcl
from repo_parser.parser.queries.javascript_queries import parse_javascript
from repo_parser.parser.queries.java_fallback_queries import parse_java_fallback
from repo_parser.parser.queries.python_queries import parse_python
from repo_parser.parser.queries.shell_queries import parse_shell
from repo_parser.parser.queries.sql_queries import parse_sql
from repo_parser.parser.queries.yaml_queries import parse_yaml
from repo_parser.parser.registry import detect_language
from repo_parser.parser.ts_compat import ParserAdapter

logger = logging.getLogger(__name__)


def _make_common(lang: str):
    return lambda fp, src, parser: parse_common(fp, src, parser, lang)


PARSERS = {
    "python": lambda fp, src, parser: parse_python(fp, src, parser),
    "javascript": lambda fp, src, parser: parse_javascript(fp, src, parser, "javascript"),
    "typescript": lambda fp, src, parser: parse_javascript(fp, src, parser, "typescript"),
    "dockerfile": lambda fp, src, parser: parse_dockerfile(fp, src, parser),
    "yaml": lambda fp, src, parser: parse_yaml(fp, src, parser),
    "kubernetes": lambda fp, src, parser: parse_yaml(fp, src, parser),
    "sql": lambda fp, src, parser: parse_sql(fp, src, parser),
    "plsql": lambda fp, src, parser: parse_sql(fp, src, parser),
    "hcl": lambda fp, src, parser: parse_hcl(fp, src, parser, "hcl"),
    "terraform": lambda fp, src, parser: parse_hcl(fp, src, parser, "terraform"),
    "bash": lambda fp, src, parser: parse_shell(fp, src, parser, "bash"),
    "shell": lambda fp, src, parser: parse_shell(fp, src, parser, "shell"),
    "env": lambda fp, src, parser: parse_env(fp, src, parser),
    "properties": lambda fp, src, parser: parse_env(fp, src, parser),
    "ini": lambda fp, src, parser: parse_env(fp, src, parser),
}

for _lang in PROFILES:
    PARSERS[_lang] = _make_common(_lang)

# tree-sitter-java may hard-crash on some legacy Java sources in native code.
# Keep Java parsing in pure Python so a single file cannot terminate the process.
PARSERS["java"] = lambda fp, src, parser: parse_java_fallback(fp, src, parser)


class ParserEngine:
    """Parse source files using tree-sitter."""

    def __init__(self) -> None:
        self._parser_cache: dict[str, object] = {}

    def _get_parser(self, lang_name: str):
        if lang_name not in self._parser_cache:
            grammar = lang_name
            if lang_name == "kubernetes":
                grammar = "yaml"
            elif lang_name == "plsql":
                grammar = "sql"
            elif lang_name == "shell":
                grammar = "bash"
            elif lang_name == "csharp":
                grammar = "csharp"
            if not has_language(grammar):
                logger.warning("Tree-sitter grammar not available for: %s", grammar)
                return None
            try:
                self._parser_cache[lang_name] = ParserAdapter(get_parser(grammar))
            except Exception as exc:
                logger.warning("Could not load tree-sitter parser for %s: %s", grammar, exc)
                return None
        return self._parser_cache[lang_name]

    def parse_file(self, filepath: str, source: str) -> ParsedFile | None:
        lang_name = detect_language(filepath)
        if lang_name is None:
            return None

        parser_fn = PARSERS.get(lang_name)
        if parser_fn is None:
            logger.debug("No parser implementation for language: %s", lang_name)
            return None

        # Config-only parsers don't need tree-sitter
        if lang_name in ("env", "properties", "ini", "java"):
            try:
                result = parser_fn(filepath, source, None)
                return result
            except Exception as exc:
                logger.error("Failed to parse config file %s: %s", filepath, exc)
                return None

        parser = self._get_parser(lang_name)
        if parser is None:
            return None

        try:
            result = parser_fn(filepath, source, parser)
            if result is not None:
                enrich_parsed_file(result, source)
                logger.debug(
                    "Parsed %s [%s]: %d classes, %d functions",
                    filepath,
                    lang_name,
                    len(result.classes),
                    len(result.functions),
                )
            else:
                logger.warning("No parse result for %s (%s)", filepath, lang_name)
            return result
        except Exception as exc:
            logger.error(
                "Failed to parse %s (%s): %s",
                filepath,
                lang_name,
                exc,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            return None

    def infer_internal_dependencies(self, parsed_files: list[ParsedFile]) -> None:
        """Link import statements to internal module paths where possible."""
        module_paths = {pf.filepath for pf in parsed_files}
        stem_map: dict[str, str] = {}
        for path in module_paths:
            from pathlib import Path

            p = Path(path)
            stem_map[p.stem] = path
            if p.suffix:
                stem_map[p.stem + p.suffix.replace(".", "_")] = path

        for pf in parsed_files:
            deps: set[str] = set()
            for imp in pf.imports:
                for token in imp.replace(",", " ").split():
                    token = token.strip().strip("'\"")
                    if token.startswith("."):
                        resolved = self._resolve_relative(pf.filepath, token)
                        if resolved and resolved in module_paths:
                            deps.add(resolved)
                    elif token in stem_map:
                        deps.add(stem_map[token])
            pf.internal_dependencies = sorted(deps)

    @staticmethod
    def _resolve_relative(from_path: str, import_path: str) -> str | None:
        from pathlib import Path

        base = Path(from_path).parent
        target = (base / import_path).with_suffix(".py")
        return target.as_posix()
