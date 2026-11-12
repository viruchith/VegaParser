"""Unit tests for repo_parser.redaction and the surfaces that rely on it."""

from __future__ import annotations

import logging

import pytest

from repo_parser.models import ClassInfo, ExternalUrl, FunctionInfo, ParsedFile
from repo_parser.parser.extractors.endpoints import extract_endpoints
from repo_parser.redaction import REDACTED, redact_parsed_file, redact_text, scan_source
from repo_parser.ui.logging_config import setup_logging

GITHUB_TOKEN = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
AWS_KEY = "AKIAQ7X3M2N8P5R1T4V6"
STRIPE_KEY = "sk_live_Zq8Lm3Rt7Vb2Nx9Kp4Wc6Yd1"
SLACK_TOKEN = "xoxb-1234567890-abcdefghijkl"
GOOGLE_KEY = "AIza" + "S" * 35
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


@pytest.mark.parametrize(
    "line,secret,category",
    [
        ("DATABASE_URL=postgresql://app:Pa55@word@db.internal:5432/orders", "Pa55@word", "url-credentials"),
        ("https://svc:Tok3nValueXyz@api.internal.example/v1", "Tok3nValueXyz", "url-credentials"),
        ("redis://:RedisPw9999@cache:6379/0", "RedisPw9999", "url-credentials"),
        ("https://api.partner.example/items?api_key=PartnerKey0987654321", "PartnerKey0987654321", "url-query-secret"),
        ("https://x.example/cb?access_token=abc123def456", "abc123def456", "url-query-secret"),
        (f"AWS = '{AWS_KEY}'", AWS_KEY, "aws-access-key"),
        (f"gh = '{GITHUB_TOKEN}'", GITHUB_TOKEN, "github-token"),
        (f"slack = '{SLACK_TOKEN}'", SLACK_TOKEN, "slack-token"),
        (f"maps = '{GOOGLE_KEY}'", GOOGLE_KEY, "google-api-key"),
        (f"stripe = '{STRIPE_KEY}'", STRIPE_KEY, "stripe-live-key"),
        (f"session = {JWT}", JWT, "jwt"),
        ("-----BEGIN RSA PRIVATE KEY-----", "BEGIN RSA PRIVATE KEY", "private-key"),
        ('password = "hunter2hunter2"', "hunter2hunter2", "credential-assignment"),
        ("DB_PASSWORD=Hunter2Hunter2", "Hunter2Hunter2", "credential-assignment"),
        ('"password": "Passw0rd!Longer"', "Passw0rd!Longer", "credential-assignment"),
        ("client_secret: s3cr3t-value-123", "s3cr3t-value-123", "credential-assignment"),
    ],
)
def test_secret_is_removed_and_category_reported(line, secret, category):
    redacted = redact_text(line)
    assert secret not in redacted
    findings = scan_source(line)
    assert [f.category for f in findings] == [category]


def test_password_containing_at_sign_is_fully_removed_and_host_kept():
    redacted = redact_text("postgresql://app:p@ss@db.internal:5432/orders")
    assert redacted == f"postgresql://app:{REDACTED}@db.internal:5432/orders"


@pytest.mark.parametrize(
    "line",
    [
        'password = "changeme123"',
        'API_TOKEN = "your-token-here"',
        "token: ${TOKEN_VALUE}",
        'password = os.environ.get("PASSWORD_VALUE")',
        "self.password = self.default_password",
        'secret = "xxxxxxxxxxxx"',
        'password: "<your-password>"',
        "token = None",
        "host_count = 42",
        "max_tokens = 4096",
        "tokenizer_name = 'bert-base-uncased'",
    ],
)
def test_placeholders_and_non_secrets_are_kept(line):
    assert redact_text(line) == line
    assert scan_source(line) == []


def test_redaction_is_idempotent():
    samples = [
        "postgresql://app:p@ss@db:5432/x",
        f"{AWS_KEY} and password = hunter2hunter2",
        "https://h.example/x?token=TokenValue123",
    ]
    for sample in samples:
        once = redact_text(sample)
        assert redact_text(once) == once


def test_context_is_redacted_before_truncation():
    """A credential straddling the 160-character cut-off must not leak its tail."""
    line = "x" * 130 + ' = "postgresql://app:StraddlePw1234@db.internal:5432/orders"'
    _, db_endpoints = extract_endpoints(line + "\n")
    assert db_endpoints
    for ep in db_endpoints:
        assert len(ep.context) <= 160
        assert "Straddle" not in ep.context
        assert "Straddle" not in ep.raw_redacted


def test_redact_parsed_file_covers_nested_fields():
    parsed = ParsedFile(
        filepath="app/settings.py",
        language="python",
        module_docstring=f"Staging key {AWS_KEY}",
        imports=["import os"],
        classes=[
            ClassInfo(
                name="Settings",
                docstring="password = hunter2hunter2",
                methods=[FunctionInfo(name="connect", signature=f'def connect(key="{STRIPE_KEY}")')],
            )
        ],
        external_urls=[
            ExternalUrl(url="https://h.example/x?token=TokenValue123", line=3, context="https://h.example/x?token=TokenValue123")
        ],
    )
    redact_parsed_file(parsed)
    dump = repr(parsed)
    for secret in (AWS_KEY, "hunter2hunter2", STRIPE_KEY, "TokenValue123"):
        assert secret not in dump
    # Identifiers used for lookups must stay exact.
    assert parsed.filepath == "app/settings.py"
    assert parsed.imports == ["import os"]
    assert parsed.external_urls[0].url == f"https://h.example/x?token={REDACTED}"


def test_scan_reports_category_and_line_never_the_value():
    source = "x = 1\nDB = 'postgresql://app:Pa55word1@db:5432/x'\n"
    findings = scan_source(source)
    assert [(f.category, f.line) for f in findings] == [("url-credentials", 2)]
    assert "Pa55word1" not in repr(findings)


@pytest.fixture
def file_logging(tmp_path):
    yield tmp_path
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)


def test_log_messages_and_tracebacks_are_redacted(file_logging):
    setup_logging(verbose=True, log_dir=file_logging)
    logger = logging.getLogger("repo_parser.test")
    logger.warning("bad dsn %s", "postgresql://app:LogPw12345@db:5432/x")
    try:
        raise ValueError("token=LogTokenValue99")
    except ValueError:
        logger.exception("failed")
    text = (file_logging / "repo-parser.log").read_text(encoding="utf-8")
    assert "LogPw12345" not in text
    assert "LogTokenValue99" not in text
    assert "postgresql://app:***@db:5432/x" in text
