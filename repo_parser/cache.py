"""Parse cache stored at ``.rag_kb/parse_cache.json``.

A file's cached result is reused when the SHA-256 of its raw bytes is unchanged.
The whole cache is reused only when its layout version, the VegaParser version and
the language filter all match. The cache also records which module documents the
last run generated, so that stale ones can be removed exactly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from repo_parser import __version__ as TOOL_VERSION
from repo_parser.models import (
    ClassInfo,
    DatabaseEndpoint,
    ExternalCall,
    ExternalUrl,
    FunctionInfo,
    ParsedFile,
    SecretFinding,
)

logger = logging.getLogger(__name__)

CACHE_FILENAME = "parse_cache.json"
# Bump when the cache layout changes. Parser and redaction changes are covered by
# TOOL_VERSION, which changes with every release.
CACHE_VERSION = 3


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 hex digest of raw file bytes."""
    return hashlib.sha256(data).hexdigest()


def cache_path(root: Path) -> Path:
    return root.resolve() / ".rag_kb" / CACHE_FILENAME


def build_filter_signature(languages: set[str] | None, extensions: set[str] | None) -> dict[str, list[str] | None]:
    return {
        "languages": sorted(languages) if languages else None,
        "extensions": sorted(extensions) if extensions else None,
    }


def load_cache(root: Path) -> dict | None:
    """Return the decoded cache, or None when it is missing or unreadable."""
    path = cache_path(root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Parse cache is unreadable and will be rebuilt: %s", exc)
        return None
    return payload if isinstance(payload, dict) else None


def cache_is_usable(payload: dict | None, filter_signature: dict) -> bool:
    """True when the cache layout, tool version and language filter all match this run."""
    return (
        payload is not None
        and payload.get("version") == CACHE_VERSION
        and payload.get("tool_version") == TOOL_VERSION
        and payload.get("filter") == filter_signature
        and isinstance(payload.get("files"), dict)
    )


def save_cache(root: Path, payload: dict) -> Path:
    """Write the cache atomically: a temporary file in the same directory, then rename."""
    path = cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".parse_cache-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


# ── ParsedFile ↔ dict converters ─────────────────────────────────────────────


def parsed_file_to_dict(parsed: ParsedFile) -> dict:
    return asdict(parsed)


def _class_from_dict(data: dict) -> ClassInfo:
    return ClassInfo(
        name=data["name"],
        docstring=data.get("docstring"),
        bases=list(data.get("bases", [])),
        decorators=list(data.get("decorators", [])),
        methods=[_function_from_dict(item) for item in data.get("methods", [])],
        line_start=int(data.get("line_start", 0)),
        line_end=int(data.get("line_end", 0)),
    )


def _function_from_dict(data: dict) -> FunctionInfo:
    return FunctionInfo(
        name=data["name"],
        signature=data.get("signature", ""),
        docstring=data.get("docstring"),
        decorators=list(data.get("decorators", [])),
        is_method=bool(data.get("is_method", False)),
        parent_class=data.get("parent_class"),
        line_start=int(data.get("line_start", 0)),
        line_end=int(data.get("line_end", 0)),
        internal_calls=list(data.get("internal_calls", [])),
    )


def _external_call_from_dict(data: dict) -> ExternalCall:
    return ExternalCall(pattern=data["pattern"], line=int(data.get("line", 0)), context=data.get("context", ""))


def _external_url_from_dict(data: dict) -> ExternalUrl:
    return ExternalUrl(url=data["url"], line=int(data.get("line", 0)), context=data.get("context", ""))


def _database_endpoint_from_dict(data: dict) -> DatabaseEndpoint:
    return DatabaseEndpoint(
        connection_type=data["connection_type"],
        host=data.get("host"),
        port=data.get("port"),
        user=data.get("user"),
        schema=data.get("schema"),
        database=data.get("database"),
        line=int(data.get("line", 0)),
        context=data.get("context", ""),
        raw_redacted=data.get("raw_redacted", ""),
    )


def parsed_file_from_dict(data: dict) -> ParsedFile:
    return ParsedFile(
        filepath=data["filepath"],
        language=data["language"],
        module_docstring=data.get("module_docstring"),
        imports=list(data.get("imports", [])),
        exports=list(data.get("exports", [])),
        classes=[_class_from_dict(item) for item in data.get("classes", [])],
        functions=[_function_from_dict(item) for item in data.get("functions", [])],
        external_calls=[_external_call_from_dict(item) for item in data.get("external_calls", [])],
        external_urls=[_external_url_from_dict(item) for item in data.get("external_urls", [])],
        database_endpoints=[_database_endpoint_from_dict(item) for item in data.get("database_endpoints", [])],
        internal_dependencies=list(data.get("internal_dependencies", [])),
        secret_findings=[
            SecretFinding(category=item["category"], line=int(item.get("line", 0)))
            for item in data.get("secret_findings", [])
        ],
    )


# Kept for the engine's cross-module import; new code should use parsed_file_from_dict.
_dict_to_parsed_file = parsed_file_from_dict
