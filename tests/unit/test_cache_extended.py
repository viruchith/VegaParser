"""Unit tests for repo_parser.cache (single versioned parse cache and helpers)."""

from __future__ import annotations

import json

import pytest

from repo_parser import __version__
from repo_parser.cache import (
    CACHE_VERSION,
    build_filter_signature,
    cache_is_usable,
    cache_path,
    load_cache,
    parsed_file_from_dict,
    parsed_file_to_dict,
    save_cache,
    sha256_bytes,
)
from repo_parser.cli import _restore_cached
from repo_parser.models import (
    ClassInfo,
    DatabaseEndpoint,
    ExternalCall,
    ExternalUrl,
    FunctionInfo,
    ParsedFile,
    SecretFinding,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def test_sha256_bytes_is_deterministic_and_content_sensitive():
    assert sha256_bytes(b"hello") == sha256_bytes(b"hello")
    assert sha256_bytes(b"hello") != sha256_bytes(b"hello ")
    assert len(sha256_bytes(b"")) == 64


def test_cache_path_is_inside_rag_kb(tmp_path):
    path = cache_path(tmp_path)
    assert path.name == "parse_cache.json"
    assert path.parent.name == ".rag_kb"


def test_build_filter_signature_with_values():
    sig = build_filter_signature({"python", "go"}, {".py"})
    assert sig == {"languages": ["go", "python"], "extensions": [".py"]}


def test_build_filter_signature_none():
    assert build_filter_signature(None, None) == {"languages": None, "extensions": None}


# ── load / save ──────────────────────────────────────────────────────────────


def test_save_and_load_cache_roundtrip(tmp_path):
    payload = {"version": CACHE_VERSION, "files": {"a.py": {"sha256": "abc"}}}
    save_cache(tmp_path, payload)
    assert load_cache(tmp_path) == payload


def test_save_cache_leaves_no_temp_files(tmp_path):
    save_cache(tmp_path, {"version": CACHE_VERSION, "files": {}})
    save_cache(tmp_path, {"version": CACHE_VERSION, "files": {"a": {}}})
    names = sorted(p.name for p in cache_path(tmp_path).parent.iterdir())
    assert names == ["parse_cache.json"]


def test_save_cache_overwrites_existing(tmp_path):
    save_cache(tmp_path, {"version": CACHE_VERSION, "files": {"old": {}}})
    save_cache(tmp_path, {"version": CACHE_VERSION, "files": {"new": {}}})
    assert load_cache(tmp_path)["files"] == {"new": {}}


def test_load_cache_missing_file(tmp_path):
    assert load_cache(tmp_path) is None


def test_load_cache_corrupt_json(tmp_path):
    path = cache_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("NOT JSON", encoding="utf-8")
    assert load_cache(tmp_path) is None


def test_load_cache_non_object_json(tmp_path):
    path = cache_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_cache(tmp_path) is None


# ── cache_is_usable ──────────────────────────────────────────────────────────


def _payload(**overrides):
    base = {
        "version": CACHE_VERSION,
        "tool_version": __version__,
        "filter": build_filter_signature(None, None),
        "files": {},
    }
    base.update(overrides)
    return base


def test_cache_usable_when_all_keys_match():
    assert cache_is_usable(_payload(), build_filter_signature(None, None))


def test_cache_unusable_when_layout_version_differs():
    assert not cache_is_usable(_payload(version=CACHE_VERSION - 1), build_filter_signature(None, None))


def test_cache_unusable_when_tool_version_differs():
    assert not cache_is_usable(_payload(tool_version="0.0.0"), build_filter_signature(None, None))


def test_cache_unusable_when_filter_differs():
    assert not cache_is_usable(_payload(), build_filter_signature({"python"}, {".py"}))


def test_cache_unusable_when_missing_or_malformed():
    sig = build_filter_signature(None, None)
    assert not cache_is_usable(None, sig)
    assert not cache_is_usable(_payload(files=[]), sig)


# ── ParsedFile converters ────────────────────────────────────────────────────


def _make_parsed_file():
    return ParsedFile(
        filepath="main.py",
        language="python",
        module_docstring="A module",
        imports=["import os"],
        exports=["main"],
        classes=[
            ClassInfo(
                name="Foo",
                docstring="Foo class",
                bases=["Bar"],
                decorators=["@dataclass"],
                methods=[FunctionInfo(name="baz", signature="baz()", line_start=5, line_end=7)],
                line_start=3,
                line_end=10,
            )
        ],
        functions=[FunctionInfo(name="main", signature="main()", line_start=12, line_end=15)],
        external_calls=[ExternalCall(pattern="requests.get", line=1, context="x = requests.get(url)")],
        external_urls=[ExternalUrl(url="https://example.com", line=2, context="url = ...")],
        database_endpoints=[
            DatabaseEndpoint(
                connection_type="postgres",
                host="localhost",
                port="5432",
                user="admin",
                schema="public",
                database="mydb",
                line=3,
                context="conn = ...",
                raw_redacted="postgres://...",
            )
        ],
        internal_dependencies=["utils.py"],
        secret_findings=[SecretFinding(category="password", line=4)],
    )


def test_roundtrip_parsed_file():
    pf = _make_parsed_file()
    restored = parsed_file_from_dict(parsed_file_to_dict(pf))
    assert restored == pf


def test_parsed_file_from_dict_minimal():
    pf = parsed_file_from_dict({"filepath": "f.py", "language": "python"})
    assert pf.filepath == "f.py"
    assert pf.imports == []
    assert pf.secret_findings == []


# ── cache entry restore (a bad entry is a miss, never an error) ──────────────


def test_restore_hit_on_matching_digest():
    pf = _make_parsed_file()
    entry = {"sha256": "d1", "module": "m.md", "parsed": parsed_file_to_dict(pf)}
    hit, restored = _restore_cached(entry, "d1")
    assert hit is True
    assert restored == pf


def test_restore_miss_on_different_digest():
    entry = {"sha256": "d1", "module": None, "parsed": None}
    assert _restore_cached(entry, "d2") == (False, None)


def test_restore_hit_for_file_that_produced_no_result():
    entry = {"sha256": "d1", "module": None, "parsed": None}
    assert _restore_cached(entry, "d1") == (True, None)


@pytest.mark.parametrize("entry", [None, "x", [], {"sha256": "d1", "parsed": {"language": "python"}}])
def test_restore_malformed_entry_is_a_miss(entry):
    assert _restore_cached(entry, "d1") == (False, None)
