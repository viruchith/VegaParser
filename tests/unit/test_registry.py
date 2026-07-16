"""Unit tests for the language registry."""

from __future__ import annotations

import pytest

from repo_parser.parser.registry import (
    EXTENSION_TO_LANGUAGE,
    FILENAME_TO_LANGUAGE,
    detect_language,
    extensions_for_languages,
    normalize_language_filter,
)


@pytest.mark.parametrize("ext,expected", sorted(EXTENSION_TO_LANGUAGE.items()))
def test_every_extension_maps_to_expected_language(ext, expected):
    assert detect_language(f"somefile{ext}") == expected


@pytest.mark.parametrize("filename,expected", sorted(FILENAME_TO_LANGUAGE.items()))
def test_every_special_filename_maps_to_expected_language(filename, expected):
    assert detect_language(filename) == expected


@pytest.mark.parametrize("name", [".env", ".env.test", ".env.production"])
def test_env_files_detected_as_env(name):
    assert detect_language(name) == "env"


@pytest.mark.parametrize("name", ["Dockerfile", "dockerfile", "DOCKERFILE"])
def test_dockerfile_case_insensitive(name):
    assert detect_language(name) == "dockerfile"


def test_unknown_extension_returns_none():
    assert detect_language("mystery.xyz") is None
    assert detect_language("noextension") is None


def test_extensions_for_languages():
    assert extensions_for_languages({"python"}) == {".py", ".pyw"}
    assert extensions_for_languages({"go"}) == {".go"}
    combined = extensions_for_languages({"python", "go"})
    assert {".py", ".pyw", ".go"} <= combined


def test_extensions_for_languages_unknown_returns_none():
    assert extensions_for_languages({"nonexistent-language"}) is None


def test_normalize_language_filter_normalizes_aliases():
    assert normalize_language_filter("py, js, golang") == {
        "python",
        "javascript",
        "go",
    }
    assert normalize_language_filter("c#,cpp") == {"csharp", "cpp"}
    assert normalize_language_filter("") == set()
