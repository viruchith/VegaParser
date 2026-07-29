"""Unit tests for repo_parser.cache (IndexCache and helpers)."""

from __future__ import annotations

import json

import pytest

from repo_parser.cache import (
    IndexCache,
    build_filter_signature,
    cache_path,
    compute_hash,
    file_signature,
    load_cache,
    parsed_file_from_dict,
    parsed_file_to_dict,
    sanitize_filename,
    save_cache,
)
from repo_parser.models import (
    ClassInfo,
    DatabaseEndpoint,
    ExternalCall,
    ExternalUrl,
    FunctionInfo,
    ParsedFile,
)


# ── standalone helpers ───────────────────────────────────────────────────────


def test_compute_hash_deterministic():
    h1 = compute_hash("hello")
    h2 = compute_hash("hello")
    assert h1 == h2
    assert len(h1) == 64


def test_compute_hash_differs():
    assert compute_hash("foo") != compute_hash("bar")


def test_cache_path(tmp_path):
    path = cache_path(tmp_path)
    assert path.name == "parse_cache.json"
    assert ".rag_kb" in str(path)


def test_build_filter_signature_with_values():
    sig = build_filter_signature({"python", "go"}, {".py"})
    assert sig["languages"] == ["go", "python"]
    assert sig["extensions"] == [".py"]


def test_build_filter_signature_none():
    sig = build_filter_signature(None, None)
    assert sig["languages"] is None
    assert sig["extensions"] is None


def test_save_and_load_cache(tmp_path):
    payload = {"version": 1, "files": {"a.py": {"hash": "abc"}}}
    save_cache(tmp_path, payload)
    loaded = load_cache(tmp_path)
    assert loaded == payload


def test_load_cache_missing_file(tmp_path):
    assert load_cache(tmp_path) is None


def test_load_cache_corrupt_json(tmp_path):
    path = cache_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("NOT JSON", encoding="utf-8")
    assert load_cache(tmp_path) is None


def test_file_signature(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("hello", encoding="utf-8")
    sig = file_signature(f)
    assert "mtime_ns" in sig
    assert "size" in sig
    assert sig["size"] == 5


def test_sanitize_filename_normal():
    assert sanitize_filename("src/main.py") == "srcmain.py"


def test_sanitize_filename_special_chars():
    result = sanitize_filename("a/b\\c:d*e?f")
    assert "/" not in result
    assert "\\" not in result
    assert ":" not in result
    assert "*" not in result
    assert "?" not in result


def test_sanitize_filename_empty_becomes_unnamed():
    assert sanitize_filename("") == "unnamed"


# ── parsed_file_from_dict / parsed_file_to_dict ──────────────────────────────


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
                methods=[
                    FunctionInfo(name="baz", signature="baz()", line_start=5, line_end=7)
                ],
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
    )


def test_roundtrip_parsed_file():
    pf = _make_parsed_file()
    d = parsed_file_to_dict(pf)
    pf2 = parsed_file_from_dict(d)
    assert pf2.filepath == pf.filepath
    assert pf2.language == pf.language
    assert pf2.imports == pf.imports
    assert pf2.exports == pf.exports
    assert len(pf2.classes) == 1
    assert pf2.classes[0].name == "Foo"
    assert len(pf2.classes[0].methods) == 1
    assert pf2.classes[0].methods[0].name == "baz"
    assert pf2.functions[0].name == "main"
    assert pf2.external_calls[0].pattern == "requests.get"
    assert pf2.external_urls[0].url == "https://example.com"
    assert pf2.database_endpoints[0].connection_type == "postgres"
    assert pf2.internal_dependencies == ["utils.py"]


def test_parsed_file_from_dict_minimal():
    pf = parsed_file_from_dict({"filepath": "f.py", "language": "python"})
    assert pf.filepath == "f.py"
    assert pf.imports == []


# ── IndexCache ───────────────────────────────────────────────────────────────


@pytest.fixture()
def cache_dir(tmp_path):
    d = tmp_path / ".rag_kb"
    d.mkdir()
    return d


def test_index_cache_known_paths_empty(cache_dir):
    cache = IndexCache(cache_dir)
    assert cache.known_paths() == set()


def test_index_cache_update_and_known_paths(cache_dir):
    cache = IndexCache(cache_dir)
    pf = ParsedFile(filepath="a.py", language="python")
    from dataclasses import asdict
    cache.update("a.py", "hash123", asdict(pf))
    assert "a.py" in cache.known_paths()


def test_index_cache_is_cached_true(cache_dir):
    cache = IndexCache(cache_dir)
    pf = ParsedFile(filepath="a.py", language="python")
    from dataclasses import asdict
    cache.update("a.py", "hash123", asdict(pf))
    assert cache.is_cached("a.py", "hash123", cache_dir / "modules" / "a.py") is True


def test_index_cache_is_cached_wrong_hash(cache_dir):
    cache = IndexCache(cache_dir)
    pf = ParsedFile(filepath="a.py", language="python")
    from dataclasses import asdict
    cache.update("a.py", "hash123", asdict(pf))
    assert cache.is_cached("a.py", "different_hash", cache_dir / "modules" / "a.py") is False


def test_index_cache_is_cached_missing_path(cache_dir):
    cache = IndexCache(cache_dir)
    assert cache.is_cached("missing.py", "hash", cache_dir / "modules" / "missing.py") is False


def test_index_cache_get_cached_parsed_file(cache_dir):
    cache = IndexCache(cache_dir)
    pf = ParsedFile(filepath="a.py", language="python", imports=["import os"])
    from dataclasses import asdict
    cache.update("a.py", "hash123", asdict(pf))
    result = cache.get_cached_parsed_file("a.py")
    assert result is not None
    assert result.filepath == "a.py"
    assert result.imports == ["import os"]


def test_index_cache_get_cached_missing(cache_dir):
    cache = IndexCache(cache_dir)
    assert cache.get_cached_parsed_file("nope.py") is None


def test_index_cache_remove(cache_dir):
    cache = IndexCache(cache_dir)
    pf = ParsedFile(filepath="a.py", language="python")
    from dataclasses import asdict
    cache.update("a.py", "hash123", asdict(pf))
    cache.remove("a.py")
    assert "a.py" not in cache.known_paths()


def test_index_cache_remove_nonexistent_no_crash(cache_dir):
    cache = IndexCache(cache_dir)
    # Should not raise even if key doesn't exist
    cache.update("a.py", "h", {})
    cache.remove("nonexistent.py")


def test_index_cache_remove_no_manifest(cache_dir):
    cache = IndexCache(cache_dir)
    # Calling remove when manifest doesn't exist should not raise
    cache.remove("a.py")


def test_index_cache_update_creates_manifest(cache_dir):
    cache = IndexCache(cache_dir)
    assert not cache.manifest_path.exists()
    cache.update("b.py", "hashX", {"filepath": "b.py", "language": "python"})
    assert cache.manifest_path.exists()


def test_index_cache_update_appends(cache_dir):
    cache = IndexCache(cache_dir)
    from dataclasses import asdict
    pf1 = ParsedFile(filepath="a.py", language="python")
    pf2 = ParsedFile(filepath="b.py", language="python")
    cache.update("a.py", "h1", asdict(pf1))
    cache.update("b.py", "h2", asdict(pf2))
    paths = cache.known_paths()
    assert "a.py" in paths
    assert "b.py" in paths


def test_index_cache_known_paths_corrupt_manifest(cache_dir):
    cache = IndexCache(cache_dir)
    cache.manifest_path.write_text("NOT JSON", encoding="utf-8")
    assert cache.known_paths() == set()


def test_index_cache_is_cached_corrupt_manifest(cache_dir):
    cache = IndexCache(cache_dir)
    cache.manifest_path.write_text("BAD", encoding="utf-8")
    assert cache.is_cached("a.py", "h", cache_dir / "a.py") is False


def test_index_cache_get_cached_corrupt_manifest(cache_dir):
    cache = IndexCache(cache_dir)
    cache.manifest_path.write_text("BAD", encoding="utf-8")
    assert cache.get_cached_parsed_file("a.py") is None


def test_index_cache_load_no_manifest(cache_dir):
    cache = IndexCache(cache_dir)
    # Should not raise
    cache.load()


def test_index_cache_load_with_manifest(cache_dir):
    cache = IndexCache(cache_dir)
    from dataclasses import asdict
    pf = ParsedFile(filepath="a.py", language="python")
    cache.update("a.py", "hash", asdict(pf))
    # load should not raise
    cache.load()


def test_index_cache_get_cached_missing_parsed_key(cache_dir):
    """Record exists but has no 'parsed' key → returns None."""
    cache = IndexCache(cache_dir)
    manifest = {"files": {"a.py": {"content_hash": "h"}}}
    cache.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert cache.get_cached_parsed_file("a.py") is None


def test_index_cache_compute_hash_for_existing_file(cache_dir, tmp_path):
    cache = IndexCache(cache_dir)
    # Create file relative to cache parent
    src = cache_dir.parent / "test.py"
    src.write_text("content", encoding="utf-8")
    h = cache._compute_hash_for("test.py")
    assert len(h) == 64


def test_index_cache_compute_hash_for_missing_file(cache_dir):
    cache = IndexCache(cache_dir)
    h = cache._compute_hash_for("does_not_exist.py")
    assert h == ""
