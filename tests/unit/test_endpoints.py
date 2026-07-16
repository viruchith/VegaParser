"""Basic unit tests for endpoint extraction."""

from __future__ import annotations

import pytest

from repo_parser.parser.extractors.endpoints import extract_endpoints, redact_secrets


def test_extract_endpoints_returns_tuple():
    result = extract_endpoints("x = 1\n")
    assert isinstance(result, tuple)
    assert len(result) == 2
    urls, db_endpoints = result
    assert isinstance(urls, list)
    assert isinstance(db_endpoints, list)


def test_url_extraction_finds_https_urls():
    urls, _ = extract_endpoints('api = "https://api.example.com/v1/data"\n')
    assert any(u.url == "https://api.example.com/v1/data" for u in urls)


@pytest.mark.parametrize(
    "uri,expected_type",
    [
        ("postgres://db.host:5432/app", "postgres"),
        ("mysql://db.host:3306/app", "mysql"),
        ("redis://cache.host:6379/0", "redis"),
        ("mongodb://mongo.host:27017/app", "mongodb"),
    ],
)
def test_db_uri_extraction(uri, expected_type):
    _, db_endpoints = extract_endpoints(f'conn = "{uri}"\n')
    assert any(ep.connection_type == expected_type for ep in db_endpoints)


def test_redact_secrets_replaces_password():
    redacted = redact_secrets("postgres://user:supersecret@db:5432/mydb")
    assert "supersecret" not in redacted
    assert "user" in redacted
    assert "db" in redacted


def test_env_database_url_detected():
    _, db_endpoints = extract_endpoints(
        "DATABASE_URL=postgres://user:secret@db.example.com:5432/mydb\n"
    )
    assert db_endpoints
    assert any(ep.host == "db.example.com" for ep in db_endpoints)


def test_localhost_urls_skipped():
    urls, _ = extract_endpoints('u = "https://localhost:8000/health"\n')
    assert urls == []
