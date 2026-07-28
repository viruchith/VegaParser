"""Persistence helpers for incremental repository indexing."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from repo_parser.models import ClassInfo, DatabaseEndpoint, ExternalCall, ExternalUrl, FunctionInfo, ParsedFile

CACHE_FILENAME = "parse_cache.json"
CACHE_VERSION = 1


def cache_path(root: Path) -> Path:
    return root.resolve() / ".rag_kb" / CACHE_FILENAME


def build_filter_signature(languages: set[str] | None, extensions: set[str] | None) -> dict[str, list[str] | None]:
    return {
        "languages": sorted(languages) if languages else None,
        "extensions": sorted(extensions) if extensions else None,
    }


def load_cache(root: Path) -> dict | None:
    path = cache_path(root)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_cache(root: Path, payload: dict) -> Path:
    path = cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    return path


def file_signature(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


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
    )
