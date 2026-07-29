"""Extract external URLs and database connection details from source text."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from repo_parser.models import DatabaseEndpoint, ExternalUrl, ParsedFile

# HTTP(S), WebSocket, FTP URLs — exclude common false positives
URL_PATTERN = re.compile(
    r"(?:https?|wss?|ftp)://[^\s'\"<>\)\]\},;]+",
    re.IGNORECASE,
)

# Database / message-broker connection URIs
CONN_URI_PATTERN = re.compile(
    r"(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|mssql|sqlserver|oracle|sqlite|cockroachdb)"
    r"(?:\+[a-z0-9]+)?://[^\s'\"]+",
    re.IGNORECASE,
)

JDBC_PATTERN = re.compile(r"jdbc:[^\s'\"]+", re.IGNORECASE)

# SQLAlchemy-style: create_engine("postgresql://...")
ENGINE_STRING_PATTERN = re.compile(
    r"(?:create_engine|createConnection|connect)\s*\(\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)

# Key-value config (YAML, .env, properties, Python dicts) — keys must be standalone tokens
CONFIG_KV_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(?:^|[\s,])(?:db[_-]?)?host(?:name)?\s*[=:]\s*['\"]?([^'\"#\s,]+)"), "host"),
    (re.compile(r"(?i)(?:^|[\s,])(?:db[_-]?)?port\s*[=:]\s*['\"]?(\d{1,5})"), "port"),
    (re.compile(r"(?i)(?:^|[\s,])(?:db[_-]?)?(?:user(?:name)?|uid)\s*[=:]\s*['\"]?([^'\"#\s,]+)"), "user"),
    (re.compile(r"(?i)(?:^|[\s,])(?:db[_-]?)?(?:database|dbname|db)\s*[=:]\s*['\"]?([^'\"#\s,]+)"), "database"),
    (re.compile(r"(?i)(?:^|[\s,])schema\s*[=:]\s*['\"]?([^'\"#\s,]+)"), "schema"),
]

# Python / JSON dict-style: "host": "value"
DICT_KV_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"""(?i)["'](?:host|hostname)["']\s*:\s*["']([^"']+)["']"""), "host"),
    (re.compile(r"""(?i)["']port["']\s*:\s*(\d{1,5})"""), "port"),
    (re.compile(r"""(?i)["'](?:user|username)["']\s*:\s*["']([^"']+)["']"""), "user"),
    (re.compile(r"""(?i)["'](?:database|dbname|db)["']\s*:\s*["']([^"']+)["']"""), "database"),
    (re.compile(r"""(?i)["']schema["']\s*:\s*["']([^"']+)["']"""), "schema"),
]

# Well-known environment variable names
ENV_CONNECTION_VARS = {
    "DATABASE_URL",
    "DB_URL",
    "DB_URI",
    "DATABASE_URI",
    "POSTGRES_URL",
    "POSTGRESQL_URL",
    "MYSQL_URL",
    "MONGODB_URI",
    "MONGO_URL",
    "REDIS_URL",
    "REDIS_URI",
    "AMQP_URL",
    "RABBITMQ_URL",
    "SQLALCHEMY_DATABASE_URI",
    "SQLALCHEMY_BINDS",
    "JDBC_URL",
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "DB_USERNAME",
    "DB_NAME",
    "DB_DATABASE",
    "DB_SCHEMA",
    "PGHOST",
    "PGPORT",
    "PGUSER",
    "PGDATABASE",
    "MYSQL_HOST",
    "MYSQL_USER",
    "MYSQL_DATABASE",
    "MONGODB_HOST",
    "ORACLE_HOST",
    "SPRING_DATASOURCE_URL",
    "SPRING_DATASOURCE_USERNAME",
}

ENV_VAR_LINE = re.compile(
    r"^(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.+)$",
    re.IGNORECASE,
)

SKIP_URL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0"})


def redact_secrets(value: str) -> str:
    """Redact passwords in connection strings while preserving host/user/db."""
    value = re.sub(r"://([^:/@]+):([^@/]+)@", r"://\1:***@", value)
    value = re.sub(
        r"(?i)(password|passwd|pwd|secret|token)\s*[=:]\s*['\"]?[^'\"#\s,]+",
        r"\1=***",
        value,
    )
    return value


def _line_context(source: str, line_no: int) -> str:
    lines = source.splitlines()
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1].strip()[:160]
    return ""


def _infer_conn_type(value: str) -> str:
    lower = value.lower()
    for prefix in (
        "postgresql", "postgres", "mysql", "mariadb", "mongodb", "redis",
        "amqp", "mssql", "sqlserver", "oracle", "sqlite", "jdbc", "cockroachdb",
    ):
        if prefix in lower:
            return prefix.replace("postgresql", "postgres")
    if "postgres" in lower:
        return "postgres"
    return "generic"


def _parse_uri(value: str, line: int, context: str) -> DatabaseEndpoint | None:
    raw = value.strip().strip("'\"")
    if not raw or raw in ("*", "null", "None", ""):
        return None

    conn_type = _infer_conn_type(raw)
    host = port = user = database = schema = None

    try:
        if raw.startswith("jdbc:"):
            conn_type = "jdbc"
            # jdbc:postgresql://host:port/db
            jdbc_match = re.search(r"jdbc:\w+://([^/:@]+)(?::(\d+))?(?:/([^?;\s]+))?", raw, re.I)
            if jdbc_match:
                host, port, database = jdbc_match.group(1), jdbc_match.group(2), jdbc_match.group(3)
        else:
            parsed = urlparse(raw)
            host = parsed.hostname
            port = str(parsed.port) if parsed.port else None
            user = parsed.username
            database = parsed.path.lstrip("/").split("?")[0] or None
            if parsed.query:
                from urllib.parse import parse_qs
                qs = parse_qs(parsed.query)
                if "schema" in qs:
                    schema = qs["schema"][0]
    except Exception:
        pass

    return DatabaseEndpoint(
        connection_type=conn_type,
        host=host,
        port=port,
        user=user,
        schema=schema,
        database=database,
        line=line,
        context=context,
        raw_redacted=redact_secrets(raw),
    )


def _collect_kv_from_line(line: str, line_no: int, skip_if_uri: bool = False) -> DatabaseEndpoint | None:
    if skip_if_uri and "://" in line:
        return None

    fields: dict[str, str] = {}
    for patterns in (CONFIG_KV_PATTERNS, DICT_KV_PATTERNS):
        for pattern, field_name in patterns:
            match = pattern.search(line)
            if match:
                val = match.group(1).strip().strip("'\"")
                if val and val not in ("*", "null", "None"):
                    fields[field_name] = val

    if not fields:
        return None

    return DatabaseEndpoint(
        connection_type="config",
        host=fields.get("host"),
        port=fields.get("port"),
        user=fields.get("user"),
        schema=fields.get("schema"),
        database=fields.get("database"),
        line=line_no,
        context=line.strip()[:160],
        raw_redacted=redact_secrets(line.strip()),
    )


def extract_endpoints(source: str) -> tuple[list[ExternalUrl], list[DatabaseEndpoint]]:
    """Scan source text for URLs and database connection details."""
    urls: list[ExternalUrl] = []
    db_endpoints: list[DatabaseEndpoint] = []
    seen_urls: set[tuple[str, int]] = set()
    seen_db: set[tuple] = set()

    lines = source.splitlines()

    for line_no, line in enumerate(lines, 1):
        context = line.strip()[:160]
        stripped = line.strip()
        has_conn_signal = "://" in line or "=" in line or ":" in line or "{" in line or "}" in line

        if not has_conn_signal and "http" not in line.lower() and "db_" not in line.lower() and "host" not in line.lower():
            continue

        # Environment variables
        env_handled = False
        env_match = ENV_VAR_LINE.match(stripped)
        if env_match:
            var_name = env_match.group(1).upper()
            var_value = env_match.group(2).strip().strip("'\"").strip()
            if var_name in ENV_CONNECTION_VARS and var_value:
                env_handled = True
                if "://" in var_value or var_name.endswith(("_URL", "_URI")):
                    ep = _parse_uri(var_value, line_no, context)
                    if ep:
                        key = (ep.host, ep.user, ep.database, ep.port, line_no)
                        if key not in seen_db:
                            seen_db.add(key)
                            db_endpoints.append(ep)
                elif var_name.endswith("_HOST") or var_name in ("PGHOST", "MYSQL_HOST", "MONGODB_HOST", "ORACLE_HOST"):
                    key = (var_value, line_no)
                    if key not in seen_db:
                        seen_db.add(key)
                        db_endpoints.append(
                            DatabaseEndpoint(
                                connection_type="env",
                                host=var_value,
                                line=line_no,
                                context=context,
                                raw_redacted=redact_secrets(f"{var_name}={var_value}"),
                            )
                        )
                elif var_name.endswith(("_USER", "_USERNAME")) or var_name in ("PGUSER", "MYSQL_USER"):
                    db_endpoints.append(
                        DatabaseEndpoint(
                            connection_type="env",
                            user=var_value,
                            line=line_no,
                            context=context,
                            raw_redacted=redact_secrets(f"{var_name}={var_value}"),
                        )
                    )
                elif var_name.endswith(("_DATABASE", "_NAME", "_DB")) or var_name in ("PGDATABASE", "MYSQL_DATABASE"):
                    db_endpoints.append(
                        DatabaseEndpoint(
                            connection_type="env",
                            database=var_value,
                            line=line_no,
                            context=context,
                            raw_redacted=redact_secrets(f"{var_name}={var_value}"),
                        )
                    )
                elif var_name.endswith("_SCHEMA"):
                    db_endpoints.append(
                        DatabaseEndpoint(
                            connection_type="env",
                            schema=var_value,
                            line=line_no,
                            context=context,
                            raw_redacted=redact_secrets(f"{var_name}={var_value}"),
                        )
                    )
                elif var_name.endswith("_PORT") or var_name == "PGPORT":
                    db_endpoints.append(
                        DatabaseEndpoint(
                            connection_type="env",
                            port=var_value,
                            line=line_no,
                            context=context,
                            raw_redacted=redact_secrets(f"{var_name}={var_value}"),
                        )
                    )

        # Key-value config lines (skip when line is a connection URI or env var)
        has_uri = "://" in line
        if not env_handled:
            kv_ep = _collect_kv_from_line(line, line_no, skip_if_uri=has_uri)
            if kv_ep:
                key = (kv_ep.host, kv_ep.user, kv_ep.database, kv_ep.port, line_no)
                if key not in seen_db:
                    seen_db.add(key)
                    db_endpoints.append(kv_ep)

        # Connection URIs in line
        if "://" in line or "jdbc:" in line.lower():
            for pattern in (CONN_URI_PATTERN, JDBC_PATTERN):
                for match in pattern.finditer(line):
                    ep = _parse_uri(match.group(0), line_no, context)
                    if ep:
                        key = (ep.host, ep.user, ep.database, ep.port, line_no)
                        if key not in seen_db:
                            seen_db.add(key)
                            db_endpoints.append(ep)

        # SQLAlchemy / driver connection strings
        if "create_engine" in line or "connect(" in line or "createConnection" in line:
            for match in ENGINE_STRING_PATTERN.finditer(line):
                ep = _parse_uri(match.group(1), line_no, context)
                if ep:
                    key = (ep.host, ep.user, ep.database, ep.port, line_no)
                    if key not in seen_db:
                        seen_db.add(key)
                        db_endpoints.append(ep)

        # External URLs
        if "http" in line.lower() or "ftp://" in line.lower() or "wss://" in line.lower():
            for match in URL_PATTERN.finditer(line):
                url = match.group(0).rstrip(".,;)")
                # Skip DB URIs already captured
                if CONN_URI_PATTERN.match(url):
                    continue
                try:
                    host = urlparse(url).hostname or ""
                except Exception:
                    host = ""
                if host in SKIP_URL_HOSTS:
                    continue
                key = (url, line_no)
                if key not in seen_urls:
                    seen_urls.add(key)
                    urls.append(ExternalUrl(url=url, line=line_no, context=context))

    return urls, db_endpoints


def enrich_parsed_file(parsed: ParsedFile, source: str) -> None:
    """Attach extracted URLs and DB endpoints to a parsed file."""
    urls, db_eps = extract_endpoints(source)
    parsed.external_urls.extend(urls)
    parsed.database_endpoints.extend(db_eps)
