"""Incremental indexing cache manifest management."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from repo_parser.models import ClassInfo, DatabaseEndpoint, ExternalCall, ExternalUrl, FunctionInfo, ParsedFile

logger = logging.getLogger(__name__)

CACHE_DIR = ".cache"
MANIFEST_FILE = "manifest.json"


def compute_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _parsed_file_to_dict(pf: ParsedFile) -> dict:
    return dataclasses.asdict(pf)


def _dict_to_parsed_file(data: dict) -> ParsedFile:
    """Reconstruct ParsedFile from a serialized dict."""
    data = dict(data)
    data["classes"] = [
        ClassInfo(
            name=c["name"],
            docstring=c.get("docstring"),
            bases=c.get("bases", []),
            decorators=c.get("decorators", []),
            methods=[
                FunctionInfo(**m) for m in c.get("methods", [])
            ],
            line_start=c.get("line_start", 0),
            line_end=c.get("line_end", 0),
        )
        for c in data.get("classes", [])
    ]
    data["functions"] = [FunctionInfo(**f) for f in data.get("functions", [])]
    data["external_calls"] = [ExternalCall(**e) for e in data.get("external_calls", [])]
    data["external_urls"] = [ExternalUrl(**e) for e in data.get("external_urls", [])]
    data["database_endpoints"] = [DatabaseEndpoint(**e) for e in data.get("database_endpoints", [])]
    return ParsedFile(**data)


class IndexCache:
    """Manages a per-repo manifest for incremental re-indexing."""

    def __init__(self, rag_kb_dir: Path) -> None:
        self.cache_dir = rag_kb_dir / CACHE_DIR
        self.manifest_path = self.cache_dir / MANIFEST_FILE
        self._manifest: dict[str, dict] = {}

    def load(self) -> None:
        if self.manifest_path.is_file():
            try:
                self._manifest = json.loads(self.manifest_path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Could not load cache manifest: %s", exc)
                self._manifest = {}
        else:
            self._manifest = {}

    def save(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(self._manifest, indent=2), "utf-8")

    def is_cached(self, rel_path: str, content_hash: str, module_file: Path) -> bool:
        entry = self._manifest.get(rel_path)
        if entry is None:
            return False
        return entry.get("hash") == content_hash and module_file.is_file()

    def get_cached_parsed_file(self, rel_path: str) -> ParsedFile | None:
        entry = self._manifest.get(rel_path)
        if entry is None or "parsed" not in entry:
            return None
        try:
            return _dict_to_parsed_file(entry["parsed"])
        except Exception as exc:
            logger.warning("Could not deserialize cached ParsedFile for %s: %s", rel_path, exc)
            return None

    def update(self, rel_path: str, content_hash: str, parsed_data: dict | None = None) -> None:
        entry: dict[str, Any] = {"hash": content_hash}
        if parsed_data is not None:
            entry["parsed"] = parsed_data
        self._manifest[rel_path] = entry

    def remove(self, rel_path: str) -> None:
        self._manifest.pop(rel_path, None)

    def known_paths(self) -> set[str]:
        return set(self._manifest.keys())
