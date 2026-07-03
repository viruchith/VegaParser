"""Parse .env and config files for connection metadata."""

from __future__ import annotations

from repo_parser.models import ParsedFile
from repo_parser.parser.extractors.endpoints import enrich_parsed_file


def parse_env(filepath: str, source: str, _parser=None) -> ParsedFile:
    parsed = ParsedFile(
        filepath=filepath,
        language="env",
        module_docstring="Environment / configuration file",
    )
    for line in source.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            parsed.exports.append(stripped.split("=")[0] if "=" in stripped else stripped)
    enrich_parsed_file(parsed, source)
    return parsed
