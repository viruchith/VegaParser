"""Extract external URLs and strict database connection strings."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from tree_sitter_language_pack import get_parser, has_language

from repo_parser.models import DatabaseEndpoint, ExternalUrl, ParsedFile
from repo_parser.parser.queries.base import iter_nodes, line_number, node_kind, node_text

logger = logging.getLogger(__name__)
# HTTP(S), WebSocket — service endpoints only (filtered downstream)
URL_PATTERN = re.compile(
    r"(?:https?|wss?)://[^\s'\"<>\)\]\},;]+",
    re.IGNORECASE,
)

# Strict connection URI schemes only
STRICT_URI_PATTERN = re.compile(
    r"(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|mssql|sqlserver|oracle)"
    r"(?:\+[a-z0-9]+)?://[^\s'\"<>]+",
    re.IGNORECASE,
)

# Strict JDBC — must be a complete jdbc:subprotocol:... string in quotes or after =
STRICT_JDBC_PATTERN = re.compile(
    r"jdbc:(?:oracle|mysql|postgresql|postgres|sqlserver|mariadb|sqlite|db2|sybase|h2)"
    r"(?::[^'\"\s;]+|://[^'\"\s;]+)",
    re.IGNORECASE,
)

CONFIG_FILE_LANGUAGES = frozenset({"env", "properties", "ini"})

# Tree-sitter node kinds that carry URL string literals (not comments/docstrings)
URL_LITERAL_KINDS = frozenset({"string_fragment", "string", "string_literal"})

COMMENT_NODE_KINDS = frozenset({
    "comment",
    "line_comment",
    "block_comment",
    "html_comment",
})

GRAMMAR_ALIASES = {
    "kubernetes": "yaml",
    "plsql": "sql",
    "shell": "bash",
    "properties": "properties",
    "ini": "ini",
    "csharp": "csharp",
}

# Only in .env / .properties — known datasource keys
CONFIG_DB_KEYS = frozenset({
    "DATABASE_URL", "DB_URL", "DB_URI", "DATABASE_URI",
    "JDBC_URL", "SPRING_DATASOURCE_URL", "SPRING_DATASOURCE_JDBC-URL",
    "SPRING_DATASOURCE_USERNAME", "SPRING_DATASOURCE_PASSWORD",
    "SPRING_DATASOURCE_DRIVER-CLASS-NAME",
    "POSTGRES_URL", "MYSQL_URL", "MONGODB_URI", "REDIS_URL",
})

SPRING_DATASOURCE_KEY = re.compile(
    r"(?i)^(?:spring\.)?datasource\.(url|jdbc-url|username|password|driver-class-name)\s*[=:]\s*(.+)$"
)

ENV_VAR_LINE = re.compile(r"^(?:export\s+)?([A-Za-z][\w.-]*)\s*=\s*(.+)$")

SKIP_URL_HOST_SUFFIXES = (
    "w3.org",
    "xmlsoap.org",
    "xmlns.com",
    "gnu.org",
    "apache.org",
    "opensource.org",
    "github.com/licenses",
    "eclipse.org",
    "springframework.org",
    "maven.apache.org",
    "schemas.microsoft.com",
    "java.sun.com",
    "xmlns.jcp.org",
    "oid-info.com",
)

SKIP_URL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})

SKIP_URL_PATH_KEYWORDS = (
    "/license",
    "/LICENSE",
    "xmlschema",
    "XMLSchema",
    "DTD/",
    ".dtd",
    "apache.org/licenses",
)


def redact_secrets(value: str) -> str:
    value = re.sub(r"://([^:/@]+):([^@/]+)@", r"://\1:***@", value)
    return value


def _is_plausible_connection(raw: str) -> bool:
    """Reject regex fragments, placeholders, and documentation examples."""
    if len(raw) < 16:
        return False
    lowered = raw.lower()
    if any(ch in raw for ch in ("...", "|", "(?:", "\\", "${", "{{")):
        return False
    if lowered in ("null", "none", ""):
        return False
    # JDBC must include a host delimiter, not just a subprotocol name
    if lowered.startswith("jdbc:"):
        if "//" not in raw and "@" not in raw and ":thin:" not in lowered:
            return False
        if re.search(r"jdbc:\w+:(?:oracle|mysql|postgres|sqlserver|mariadb|sqlite)\b", lowered):
            return False
    return True


def _is_valid_db_user(user: str | None) -> bool:
    if not user:
        return True
    invalid = ("null", "none", "true", "false")
    if user.lower() in invalid:
        return False
    if re.search(r"[();{}\s]", user):
        return False
    if "." in user and not re.match(r"^[\w.-]+$", user):
        return False
    return len(user) <= 128


def _parse_strict_connection(raw: str, line: int, context: str) -> DatabaseEndpoint | None:
    raw = raw.strip().strip("'\"")
    if not raw or raw.lower() in ("null", "none", ""):
        return None
    if not _is_plausible_connection(raw):
        return None
    if not (STRICT_URI_PATTERN.search(raw) or STRICT_JDBC_PATTERN.search(raw)):
        return None

    conn_type = "jdbc" if raw.lower().startswith("jdbc:") else "uri"
    host = port = user = database = schema = None

    if raw.lower().startswith("jdbc:"):
        conn_type = "jdbc"
        m = re.search(
            r"jdbc:(?:\w+:)??(?:thin:@|//)?([^/:;]+)(?::(\d+))?(?:[:/]([^?;]*))?",
            raw,
            re.I,
        )
        if m:
            host, port, database = m.group(1), m.group(2), m.group(3)
    else:
        try:
            parsed = urlparse(raw)
            host = parsed.hostname
            port = str(parsed.port) if parsed.port else None
            user = parsed.username
            database = (parsed.path or "").lstrip("/").split("?")[0] or None
            if user and not _is_valid_db_user(user):
                user = None
        except Exception:
            return None

    if user and not _is_valid_db_user(user):
        user = None

    return DatabaseEndpoint(
        connection_type=conn_type,
        host=host,
        port=port,
        user=user,
        schema=schema,
        database=database,
        line=line,
        context=context[:160],
        raw_redacted=redact_secrets(raw),
    )


def _extract_db_from_config_file(source: str) -> list[DatabaseEndpoint]:
    """Extract DB config only from .env / .properties style files."""
    results: list[DatabaseEndpoint] = []
    seen: set[str] = set()

    for line_no, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        context = stripped[:160]

        spring_match = SPRING_DATASOURCE_KEY.match(stripped)
        if spring_match:
            value = spring_match.group(2).strip().strip("'\"")
            if "jdbc:" in value.lower() or "://" in value:
                ep = _parse_strict_connection(value, line_no, context)
                if ep and ep.raw_redacted not in seen:
                    seen.add(ep.raw_redacted)
                    results.append(ep)
            continue

        env_match = ENV_VAR_LINE.match(stripped)
        if not env_match:
            continue
        var_name = env_match.group(1).upper().replace(".", "_").replace("-", "_")
        var_value = env_match.group(2).strip().strip("'\"")

        if var_name in CONFIG_DB_KEYS or "DATASOURCE_URL" in var_name or var_name.endswith("_URL"):
            if "://" in var_value or var_value.lower().startswith("jdbc:"):
                ep = _parse_strict_connection(var_value, line_no, context)
                if ep and ep.raw_redacted not in seen:
                    seen.add(ep.raw_redacted)
                    results.append(ep)

    return results


def _extract_strict_connections_from_source(source: str) -> list[DatabaseEndpoint]:
    """Only literal JDBC/URI strings embedded in source code."""
    results: list[DatabaseEndpoint] = []
    seen: set[str] = set()

    for line_no, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        code = stripped.split("#", 1)[0].strip()
        if not code:
            continue
        context = code[:160]
        for pattern in (STRICT_JDBC_PATTERN, STRICT_URI_PATTERN):
            for match in pattern.finditer(code):
                ep = _parse_strict_connection(match.group(0), line_no, context)
                if ep and ep.raw_redacted not in seen:
                    seen.add(ep.raw_redacted)
                    results.append(ep)
        for match in re.finditer(r"""['"](jdbc:[^'"]+)['"]""", code, re.I):
            ep = _parse_strict_connection(match.group(1), line_no, context)
            if ep and ep.raw_redacted not in seen:
                seen.add(ep.raw_redacted)
                results.append(ep)

    return results


def _should_keep_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in SKIP_URL_HOSTS:
        return False
    for suffix in SKIP_URL_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return False
    path = (parsed.path or "").lower()
    for kw in SKIP_URL_PATH_KEYWORDS:
        if kw.lower() in path or kw.lower() in url.lower():
            return False
    # Skip XML namespace style URLs
    if "xmlns" in url or "xmlschema" in url.lower():
        return False
    return True


def _grammar_name(language: str) -> str:
    return GRAMMAR_ALIASES.get(language, language)


def _unwrap_string_literal(text: str) -> str:
    text = text.strip()
    for quote in ('"""', "'''", '"', "'", "`"):
        if text.startswith(quote) and text.endswith(quote) and len(text) >= 2 * len(quote):
            return text[len(quote) : -len(quote)]
    return text


def _has_string_fragment_child(node) -> bool:
    for i in range(node.child_count()):
        if node_kind(node.child(i)) == "string_fragment":
            return True
    return False


def _is_python_docstring(node) -> bool:
    if node_kind(node) != "string":
        return False
    parent = node.parent()
    if parent is None or node_kind(parent) != "expression_statement":
        return False
    block = parent.parent()
    if block is None:
        return False
    if node_kind(block) == "module":
        return True
    if node_kind(block) != "block":
        return False
    for i in range(block.child_count()):
        child = block.child(i)
        if node_kind(child) in (":", "{", "}", "(", ")"):
            continue
        return child == parent
    return False


def _is_url_literal_node(node, language: str) -> bool:
    kind = node_kind(node)
    if kind == "string_fragment":
        return True
    if kind in ("string", "string_literal"):
        if _has_string_fragment_child(node):
            return False
        if language == "python" and _is_python_docstring(node):
            return False
        return True
    return False


def _urls_from_text(text: str, line: int, context: str, seen: set[str]) -> list[ExternalUrl]:
    found: list[ExternalUrl] = []
    for match in URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,;)")
        if STRICT_URI_PATTERN.match(url):
            continue
        if not _should_keep_url(url):
            continue
        if url not in seen:
            seen.add(url)
            found.append(ExternalUrl(url=url, line=line, context=context))
    return found


def _extract_urls_from_ast(source: str, language: str) -> list[ExternalUrl] | None:
    grammar = _grammar_name(language)
    if not has_language(grammar):
        return None
    try:
        parser = get_parser(grammar)
        root = parser.parse(source).root_node()
    except Exception as exc:
        logger.debug("AST URL extraction unavailable for %s: %s", language, exc)
        return None

    urls: list[ExternalUrl] = []
    seen: set[str] = set()
    for node in iter_nodes(root):
        if node_kind(node) in COMMENT_NODE_KINDS:
            continue
        if not _is_url_literal_node(node, language):
            continue
        literal = _unwrap_string_literal(node_text(source, node))
        if not literal:
            continue
        line = line_number(node)
        context = literal[:160]
        urls.extend(_urls_from_text(literal, line, context, seen))
    return urls


def _strip_c_style_comments(source: str) -> str:
    """Remove // and /* */ comments for regex fallback."""
    out: list[str] = []
    i = 0
    n = len(source)
    in_string: str | None = None
    while i < n:
        ch = source[i]
        if in_string:
            out.append(ch)
            if ch == in_string and (i == 0 or source[i - 1] != "\\"):
                in_string = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_string = ch
            out.append(ch)
            i += 1
            continue
        if source.startswith("/*", i):
            i = source.find("*/", i + 2)
            i = i + 2 if i != -1 else n
            continue
        if source.startswith("//", i):
            i = source.find("\n", i)
            i = i if i != -1 else n
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _extract_urls_from_lines(source: str, *, strip_comments: bool = False, language: str | None = None) -> list[ExternalUrl]:
    urls: list[ExternalUrl] = []
    seen: set[str] = set()
    text = source
    if strip_comments and language in ("javascript", "typescript", "java", "kotlin", "scala", "c", "cpp", "csharp", "go", "rust", "css"):
        text = _strip_c_style_comments(source)

    for line_no, line in enumerate(text.splitlines(), 1):
        if strip_comments and line.strip().startswith("#"):
            continue
        context = line.strip()[:160]
        urls.extend(_urls_from_text(line, line_no, context, seen))
    return urls


def extract_urls(source: str, language: str | None = None) -> list[ExternalUrl]:
    """Extract service URLs from string literals; skip comment/docstring noise."""
    if language in CONFIG_FILE_LANGUAGES:
        return _extract_urls_from_lines(source)

    if language:
        ast_urls = _extract_urls_from_ast(source, language)
        if ast_urls is not None:
            return ast_urls

    return _extract_urls_from_lines(source, strip_comments=True, language=language)


def enrich_parsed_file(parsed: ParsedFile, source: str) -> None:
    """Attach extracted URLs and strict DB endpoints to a parsed file."""
    parsed.external_urls.extend(extract_urls(source, parsed.language))

    if parsed.language in CONFIG_FILE_LANGUAGES:
        parsed.database_endpoints.extend(_extract_db_from_config_file(source))
    elif parsed.language == "java":
        # Java: only strict JDBC literals — no loose property regex
        parsed.database_endpoints.extend(_extract_strict_connections_from_source(source))
    else:
        parsed.database_endpoints.extend(_extract_strict_connections_from_source(source))
