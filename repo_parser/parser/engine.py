"""Tree-sitter parsing engine orchestration."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict
import logging

from tree_sitter_language_pack import get_parser, has_language

from repo_parser.cache import _dict_to_parsed_file
from repo_parser.models import ParsedFile
from repo_parser.parser.dependencies import infer_internal_dependencies as infer_file_dependencies
from repo_parser.parser.extractors.endpoints import enrich_parsed_file
from repo_parser.parser.queries.common_queries import PROFILES, parse_common
from repo_parser.parser.queries.docker_queries import parse_dockerfile
from repo_parser.parser.queries.env_queries import parse_env
from repo_parser.parser.queries.hcl_queries import parse_hcl
from repo_parser.parser.queries.java_queries import parse_java
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


def _grammar_for_language(lang_name: str) -> str:
    if lang_name == "kubernetes":
        return "yaml"
    if lang_name == "plsql":
        return "sql"
    if lang_name == "shell":
        return "bash"
    return lang_name


def _parse_file_isolated(filepath: str, source: str, lang_name: str) -> dict | None:
    parser_fn = PARSERS.get(lang_name)
    if parser_fn is None:
        return None
    grammar = _grammar_for_language(lang_name)
    if not has_language(grammar):
        return None
    parser = ParserAdapter(get_parser(grammar))
    result = parser_fn(filepath, source, parser)
    if result is None:
        return None
    enrich_parsed_file(result, source)
    return asdict(result)


PARSERS = {
    "python": lambda fp, src, parser: parse_python(fp, src, parser),
    "javascript": lambda fp, src, parser: parse_javascript(fp, src, parser, "javascript"),
    "typescript": lambda fp, src, parser: parse_javascript(fp, src, parser, "typescript"),
    "java": lambda fp, src, parser: parse_java(fp, src, parser),
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
        self._isolated_executor: ProcessPoolExecutor | None = None

    def _ensure_executor(self) -> ProcessPoolExecutor:
        if self._isolated_executor is None:
            self._isolated_executor = ProcessPoolExecutor(max_workers=1)
        return self._isolated_executor

    def _reset_executor(self) -> None:
        if self._isolated_executor is not None:
            self._isolated_executor.shutdown(wait=False, cancel_futures=True)
            self._isolated_executor = None

    def _parse_with_isolation(self, filepath: str, source: str, lang_name: str) -> ParsedFile | None:
        executor = self._ensure_executor()
        try:
            parsed_data = executor.submit(_parse_file_isolated, filepath, source, lang_name).result()
        except BrokenProcessPool:
            logger.error(
                "Native parser crashed while parsing %s (%s); skipping file safely.",
                filepath,
                lang_name,
            )
            self._reset_executor()
            return None
        except Exception as exc:
            logger.error(
                "Failed to parse %s (%s): %s",
                filepath,
                lang_name,
                exc,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            return None

        if parsed_data is None:
            return None

        try:
            result = _dict_to_parsed_file(parsed_data)
        except Exception as exc:
            logger.error("Failed to deserialize parsed output for %s: %s", filepath, exc)
            return None

        logger.debug(
            "Parsed %s [%s]: %d classes, %d functions",
            filepath,
            lang_name,
            len(result.classes),
            len(result.functions),
        )
        return result

    def parse_file(self, filepath: str, source: str) -> ParsedFile | None:
        lang_name = detect_language(filepath)
        if lang_name is None:
            return None

        parser_fn = PARSERS.get(lang_name)
        if parser_fn is None:
            logger.debug("No parser implementation for language: %s", lang_name)
            return None

        # Config-only parsers don't need tree-sitter
        if lang_name in ("env", "properties", "ini", "java", "sql", "plsql"):
            try:
                result = parser_fn(filepath, source, None)
                return result
            except Exception as exc:
                logger.error("Failed to parse config file %s: %s", filepath, exc)
                return None

        result = self._parse_with_isolation(filepath, source, lang_name)
        if result is None:
            logger.warning("No parse result for %s (%s)", filepath, lang_name)
        return result

    def infer_internal_dependencies(
        self,
        parsed_files: list[ParsedFile],
        sources: dict[str, str] | None = None,
    ) -> None:
        infer_file_dependencies(parsed_files, sources)

    @staticmethod
    def _resolve_relative(from_path: str, import_path: str) -> str | None:
        from pathlib import Path

        base = Path(from_path).parent
        target = (base / import_path).with_suffix(".py")
        return target.as_posix()
