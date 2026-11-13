"""Secret redaction for every output surface.

Applied once per parsed file (see ``ParserEngine.parse_file``), so module docs,
the project index, the parse cache and the bundle never contain a raw secret.
The logging layer applies the same rules as a second line of defence.

Only values are removed. Host, port, user and database names stay so the
output remains useful for orientation.

Categories reported by ``init --report-secrets`` (values are never reported):

  url-credentials         scheme://user:password@host (the password may contain '@')
  url-query-secret        ?key=, ?token=, ?secret=, ?password=, ?sig=, ?api_key=,
                          ?access_token=, ?client_secret= and similar query parameters
  aws-access-key          AKIA or ASIA followed by 16 uppercase/digit characters
  github-token            gh[pousr]_ followed by 36 or more characters
  slack-token             xox[baprs]- tokens
  google-api-key          AIza followed by 35 characters
  stripe-live-key         sk_live_ / rk_live_ keys
  jwt                     three base64url segments, the first two starting eyJ
  private-key             PEM "BEGIN ... PRIVATE KEY" header lines
  credential-assignment   password / passwd / pwd / secret / token / api_key /
                          access_key / client_secret assigned a value of 8+ characters

Placeholders such as ``changeme``, ``your-...``, ``example``, ``xxxxxxxx``,
``${VAR}``, ``<value>``, ``None`` and dotted code references such as
``self.default_password`` are not treated as secrets.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass

from repo_parser.models import ParsedFile, SecretFinding

REDACTED = "***"


@dataclass(frozen=True)
class _Rule:
    category: str
    pattern: re.Pattern[str]
    replace: Callable[[re.Match[str]], str | None]


def _mask(_match: re.Match[str]) -> str:
    return REDACTED


def _url_userinfo(match: re.Match[str]) -> str:
    return f"{match.group('scheme')}{match.group('user')}:{REDACTED}@"


def _query_secret(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{REDACTED}"


_PLACEHOLDER = re.compile(
    r"(?i)x+|\*+|\.+|-+|changeme\w*|your[\w-]*|example\w*|placeholder\w*|dummy\w*|redacted\w*|"
    r"none|null|true|false|undefined"
)
_DOTTED_REFERENCE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")


def _assignment(match: re.Match[str]) -> str | None:
    value = match.group("value")
    if _PLACEHOLDER.fullmatch(value) or _DOTTED_REFERENCE.fullmatch(value):
        return None
    whole = match.string[match.start():match.end()]
    value_start = match.start("value") - match.start()
    value_end = match.end("value") - match.start()
    return whole[:value_start] + REDACTED + whole[value_end:]


_KEY_FORMATS: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe-live-key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")),
]

_URL_USERINFO = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<user>[^\s:/@'\"]*):(?P<password>[^\s/'\"]+)@"
)

_QUERY_SECRET = re.compile(
    r"(?i)(?P<prefix>[?&;](?:key|token|secret|password|passwd|pwd|sig|api_key|apikey|"
    r"access_token|access_key|client_secret)=)(?P<value>[^&\s'\"#;]+)"
)

_ASSIGNMENT = re.compile(
    r"(?i)(?<![a-z0-9])(?P<key>[a-z0-9_\-]*(?:password|passwd|pwd|secret|token|api[_-]?key|"
    r"access[_-]?key|client[_-]?secret))[\"']?\s*[:=]\s*(?P<quote>[\"']?)"
    r"(?P<value>[^\s\"',;#()\[\]{}<>]{8,})(?P=quote)(?=[\s\"',;#)\]}]|$)"
)

_RULES: list[_Rule] = [
    *(_Rule(category, pattern, _mask) for category, pattern in _KEY_FORMATS),
    _Rule("url-credentials", _URL_USERINFO, _url_userinfo),
    _Rule("url-query-secret", _QUERY_SECRET, _query_secret),
    _Rule("credential-assignment", _ASSIGNMENT, _assignment),
]


def _rewrite(text: str) -> tuple[str, list[str]]:
    """Return the redacted text and the category of every value that was replaced."""
    found: list[str] = []
    for rule in _RULES:
        def _sub(match: re.Match[str], rule: _Rule = rule) -> str:
            replacement = rule.replace(match)
            if replacement is None:
                return match.group(0)
            found.append(rule.category)
            return replacement

        text = rule.pattern.sub(_sub, text)
    return text, found


def redact_text(text: str) -> str:
    """Return *text* with likely secret values replaced by ``***``."""
    return _rewrite(text)[0]


def scan_source(source: str) -> list[SecretFinding]:
    """Locate likely secrets in source text. Returns category and line only, never values."""
    findings: list[SecretFinding] = []
    for line_no, line in enumerate(source.splitlines(), 1):
        for category in _rewrite(line)[1]:
            findings.append(SecretFinding(category=category, line=line_no))
    return findings


# Fields holding identifiers or paths that must stay exact for lookups and links.
_SKIP_FIELDS = frozenset({"filepath", "language", "connection_type", "internal_dependencies", "secret_findings"})


def redact_parsed_file(parsed: ParsedFile) -> None:
    """Redact every string in a parsed file in place, including nested dataclasses."""
    _redact_object(parsed)


def _redact_object(obj: object) -> None:
    for field_info in fields(obj):
        if field_info.name in _SKIP_FIELDS:
            continue
        setattr(obj, field_info.name, _redact_value(getattr(obj, field_info.name)))


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _redact_value(item)
        return value
    if is_dataclass(value) and not isinstance(value, type):
        _redact_object(value)
    return value
