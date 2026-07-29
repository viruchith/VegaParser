"""Persistence helpers for incremental repository indexing."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from repo_parser.models import ClassInfo, DatabaseEndpoint, ExternalCall, ExternalUrl, FunctionInfo, ParsedFile

CACHE_FILENAME = "parse_cache.json"
CACHE_VERSION = 1

# ── dict ↔ dataclass converters ──────────────────────────────────────────────

def _dict_to_parsed_file(data: dict) -> ParsedFile:
    """Public alias for parsed_file_from_dict (kept for backward compatibility)."""
    return parsed_file_from_dict(data)


def _parsed_file_to_dict(parsed: ParsedFile) -> dict:
    """Serialise a ParsedFile to a plain dict (used by tests and CLI)."""
    return asdict(parsed)


def compute_hash(content: str) -> str:
    """Return a SHA-256 hex digest of *content*."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ── cache helpers ────────────────────────────────────────────────────────────

def cache_path(root: Path) -> Path:
    return root.resolve() / ".rag_kb" / CACHE_FILENAME


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


# ── IndexCache ───────────────────────────────────────────────────────────────

class IndexCache:
    """Incremental indexing cache backed by ``.rag_kb/.cache/manifest.json``.

    Tracks known file paths and per-file content hashes so that files whose
    content hasn't changed are skipped on subsequent runs.
    """

    MANIFEST_FILENAME = "manifest.json"

    def __init__(self, rag_kb_dir: Path) -> None:
        self.rag_kb_dir = rag_kb_dir.resolve()
        self.manifest_path = self.rag_kb_dir / self.MANIFEST_FILENAME

    def load(self) -> None:
        """Load the manifest from disk."""
        if not self.manifest_path.is_file():
            return
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for rel_path, record in manifest.get("files", {}).items():
            module_file = self._module_file_for(rel_path)
            if module_file.exists() and record.get("content_hash") == record.get("content_hash"):
                # cached parsed file may already exist
                pass

    def save(self) -> None:
        """Persist the current manifest to disk."""
        manifest: dict[str, Any] = {"files": {}}
        for rel_str in self.known_paths():
            module_file = self._module_file_for(rel_str)
            manifest["files"][rel_str] = {
                "content_hash": self._compute_hash_for(rel_str),
                "parsed": _parsed_file_to_dict(self._load_parsed(rel_str)),
            }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _compute_hash_for(self, rel_str: str) -> str:
        """Compute SHA-256 hash of the source file at repo root."""
        path = self.rag_kb_dir.parent / rel_str
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ""

    def _load_parsed(self, rel_str: str) -> ParsedFile:
        """Load a ParsedFile from the module file in the cache."""
        module_file = self._module_file_for(rel_str)
        if not module_file.is_file():
            return ParsedFile(
                filepath=rel_str,
                language="unknown",
                module_docstring=None,
                imports=[],
                exports=[],
                classes=[],
                functions=[],
                external_calls=[],
                external_urls=[],
                database_endpoints=[],
                internal_dependencies=[],
            )
        return ParsedFile(
            filepath=rel_str,
            language="unknown",
            module_docstring=None,
            imports=[],
            exports=[],
            classes=[],
            functions=[],
            external_calls=[],
            external_urls=[],
            database_endpoints=[],
            internal_dependencies=[],
        )

    def known_paths(self) -> set[str]:
        """Return the set of file paths tracked by the cache."""
        if not self.manifest_path.is_file():
            return set()
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return set(manifest.get("files", {}).keys())

    def remove(self, rel_path: str) -> None:
        """Remove a tracked path from the cache."""
        if not self.manifest_path.is_file():
            return
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        manifest["files"].pop(rel_path, None)
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def is_cached(self, rel_path: str, content_hash: str, module_file: Path) -> bool:
        """Return ``True`` if *rel_path* is in the cache and its content hash matches."""
        if not self.manifest_path.is_file():
            return False
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        record = manifest.get("files", {}).get(rel_path)
        if not record:
            return False
        return record.get("content_hash") == content_hash

    def get_cached_parsed_file(self, rel_path: str) -> ParsedFile | None:
        """Return the cached ``ParsedFile`` for *rel_path*, or ``None``."""
        if not self.manifest_path.is_file():
            return None
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        record = manifest.get("files", {}).get(rel_path)
        if not record:
            return None
        try:
            return parsed_file_from_dict(record["parsed"])
        except KeyError:
            return None

    def update(self, rel_path: str, content_hash: str, parsed_dict: dict) -> None:
        """Record *rel_path* in the cache with *content_hash* and *parsed_dict*."""
        if not self.manifest_path.is_file():
            manifest = {"files": {}}
        else:
            try:
                manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = {"files": {}}
        manifest["files"][rel_path] = {
            "content_hash": content_hash,
            "parsed": parsed_dict,
        }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    # ── helpers ────────────────────────────────────────────────────────────

    def _module_file_for(self, rel_path: str) -> Path:
        return self.rag_kb_dir / "modules" / sanitize_filename(rel_path)


def sanitize_filename(name: str) -> str:
    """Return a filesystem-safe filename derived from *name*."""
    sanitized = "".join(c for c in name if c not in "/\\:*\\?" and ord(c) > 31)
    if not sanitized:
        sanitized = "unnamed"
    return sanitized
