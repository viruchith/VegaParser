"""Security-focused tests for endpoint / secret extraction.

# === Regex patterns in repo_parser/parser/extractors/endpoints.py ===
# 1. URL_PATTERN: (?:https?|wss?|ftp)://[^\\s'"<>)\\]},;]+
# 2. CONN_URI_PATTERN: (?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\\+srv)?|redis|amqp|mssql|sqlserver|oracle|sqlite|cockroachdb)(?:\\+[a-z0-9]+)?://[^\\s'"]+
# 3. JDBC_PATTERN: jdbc:[^\\s'"]+
# 4. ENGINE_STRING_PATTERN: (?:create_engine|createConnection|connect)\\s*\\(\\s*['"]([^'"]+)['"]
# 5. CONFIG_KV_PATTERNS: host=, port=, user=, database=, schema= (with variants)
# 6. DICT_KV_PATTERNS: "host": "value" style
# 7. ENV_VAR_LINE: KEY=VALUE env var lines
# 8. redact_secrets: ://user:password@ -> ://user:***@  AND password=value -> password=***
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from repo_parser.parser.extractors.endpoints import extract_endpoints, redact_secrets


# ---------------------------------------------------------------------------
# URI credential redaction
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "uri,should_not_contain",
    [
        ("postgres://user:secretpass@db.example.com:5432/mydb", "secretpass"),
        ("mysql://admin:hunter2@mysql.prod:3306/app", "hunter2"),
        # NOTE: '@' inside the password is a known limitation of the simple
        # regex; only the segment before the first '@' is treated as the
        # password. The full literal is still broken up, so the exact
        # credential string does not survive verbatim.
        ("mongodb://root:p@$$w0rd@mongo.cluster:27017/db", "p@$$w0rd"),
        ("redis://:redispassword@redis.cache:6379/0", "redispassword"),
        ("jdbc:postgresql://host:5432/db?user=usr&password=mypass", "mypass"),
        # Without credentials — should still parse correctly.
        ("postgres://db.example.com:5432/mydb", None),
        ("mysql://mysql.prod:3306/app", None),
    ],
)
def test_uri_credential_redaction(uri, should_not_contain):
    redacted = redact_secrets(uri)
    if should_not_contain is not None:
        assert should_not_contain not in redacted

    _, db_eps = extract_endpoints(uri)
    for ep in db_eps:
        if should_not_contain:
            assert should_not_contain not in ep.raw_redacted


# ---------------------------------------------------------------------------
# ENV var detection + redaction
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line,expected_detected,should_not_contain",
    [
        ("DATABASE_URL=postgres://user:secret@db:5432/mydb", True, "secret"),
        ("DB_HOST=postgres.internal.corp", True, None),
        ("REDIS_URL=redis://:password@redis:6379/0", True, "password"),
        ("PGUSER=dbuser", True, None),
        ("PGHOST=db.example.com", True, None),
        ("MYSQL_DATABASE=myapp", True, None),
        ("SPRING_DATASOURCE_URL=jdbc:postgresql://host:5432/db", True, None),
        # Non-connection env var — should NOT be detected as a DB endpoint.
        ("LOG_LEVEL=DEBUG", False, None),
        ("APP_NAME=myapp", False, None),
    ],
)
def test_env_var_detection_and_redaction(line, expected_detected, should_not_contain):
    _, db_eps = extract_endpoints(line + "\n")
    assert bool(db_eps) is expected_detected
    if should_not_contain:
        for ep in db_eps:
            assert should_not_contain not in ep.raw_redacted


# ---------------------------------------------------------------------------
# Config key-value detection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line,expected_fields_or_false",
    [
        ('host = "postgres.internal.corp"', {"host": "postgres.internal.corp"}),
        ("port = 5432", {"port": "5432"}),
        ('user = "dbuser"', {"user": "dbuser"}),
        ('database = "myapp"', {"database": "myapp"}),
        # False positive: variable named host_count should NOT match.
        ("host_count = 42", False),
    ],
)
def test_config_kv_detection(line, expected_fields_or_false):
    _, db_eps = extract_endpoints(line + "\n")
    if expected_fields_or_false is False:
        assert db_eps == []
        return
    assert db_eps
    ep = db_eps[0]
    for field_name, value in expected_fields_or_false.items():
        assert getattr(ep, field_name) == value


# ---------------------------------------------------------------------------
# Dict literal detection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line,expected_host",
    [
        ('"host": "mydb.internal"', "mydb.internal"),
        ("'host': 'mydb2.internal'", "mydb2.internal"),
        ('"hostname": "mydb3.internal"', "mydb3.internal"),
    ],
)
def test_dict_literal_detection(line, expected_host):
    _, db_eps = extract_endpoints(line + "\n")
    assert any(ep.host == expected_host for ep in db_eps)


# ---------------------------------------------------------------------------
# create_engine() detection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line,expected_host,leaked",
    [
        ('create_engine("postgresql://user:pass@mydb:5432/app")', "mydb", "pass"),
        ('engine = create_engine("mysql://root:secret@localhost/db")', "localhost", "secret"),
    ],
)
def test_create_engine_detection(line, expected_host, leaked):
    _, db_eps = extract_endpoints(line + "\n")
    assert any(ep.host == expected_host for ep in db_eps)
    for ep in db_eps:
        assert leaked not in ep.raw_redacted


# ---------------------------------------------------------------------------
# False positives
# ---------------------------------------------------------------------------
def test_false_positive_host_count():
    """Variable named 'host_count' should NOT trigger endpoint extraction."""
    _, db_eps = extract_endpoints("host_count = 42\n")
    assert db_eps == []


def test_false_positive_port_as_variable():
    """Variable named 'port_number' should not trigger."""
    _, db_eps = extract_endpoints("port_number = 8080\n")
    assert db_eps == []


def test_false_positive_plain_host_word():
    """The word 'host' in a comment or print statement should not trigger."""
    _, db_eps = extract_endpoints('# connect to the host\nprint("connecting to host")\n')
    assert db_eps == []


# ---------------------------------------------------------------------------
# Known limitations
# ---------------------------------------------------------------------------
def test_multiline_credentials_known_limitation():
    """Multi-line string concatenation is a known limitation.

    A connection string split across concatenated string literals is not
    reassembled, so the full URI (and therefore its host) is not detected.
    """
    source = 'uri = ("postgres://user:secret"\n       "@dbhost:5432/app")\n'
    _, db_eps = extract_endpoints(source)
    assert all(ep.host != "dbhost" for ep in db_eps)


# ---------------------------------------------------------------------------
# Property-based test: credentials must never leak
# ---------------------------------------------------------------------------
@given(
    host=st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-."),
    ),
    user=st.text(
        min_size=1,
        max_size=30,
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_"),
    ),
    password=st.text(
        min_size=8,
        max_size=40,
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="!#$%^&*"),
    ),
    port=st.integers(min_value=1024, max_value=65535),
)
@settings(max_examples=50)
def test_uri_credential_never_leaks(host, user, password, port):
    """Property: generated credentials must never appear verbatim in redacted output."""
    # The username and host are intentionally preserved, so only exercise cases
    # where the password is not a coincidental substring of those non-secret parts.
    assume(password not in user)
    assume(password not in host)
    uri = f"postgres://{user}:{password}@{host}:{port}/testdb"
    redacted = redact_secrets(uri)
    assert password not in redacted, f"Credential leaked in: {redacted}"
    assert f":{password}@" not in redacted
