"""Deterministic file ranking and budget selection for `bundle`.

Everything in this module is pure. The same parse cache and the same arguments always
give the same order and the same selection.

Budget units: the budget is counted in characters. The token estimate is
``ceil(characters / 3)``, which is a rough, labelled approximation. Because
``ceil(c / 3) <= n`` is equivalent to ``c <= 3 * n``, a bundle that fits the character
budget also fits the token estimate exactly.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import pathspec

CHARS_PER_TOKEN = 3
"""Characters per token in the approximate estimate. Documented as an approximation."""

OMITTED_LIST_LIMIT = 100
"""Maximum number of omitted paths listed at the end of a bundle."""

OMITTED_TITLE = "## Omitted by budget"
_MORE_LINE_RESERVE = 32  # room for "- ... and N more" plus its join separator

ENTRY_BASENAMES = frozenset(
    {
        "main.py",
        "__main__.py",
        "app.py",
        "server.py",
        "manage.py",
        "cli.py",
        "index.js",
        "index.ts",
        "main.js",
        "main.ts",
        "server.js",
        "server.ts",
        "app.js",
        "app.ts",
        "main.go",
        "main.rs",
        "lib.rs",
        "Main.java",
        "App.java",
        "Application.java",
        "Program.cs",
    }
)

_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec)/"
    r"|(^|/)test_[^/]*$"
    r"|_test\.[^/]*$"
    r"|\.(test|spec)\.[^/]*$"
    r"|Test\.java$"
)
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_QUERY_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def estimate_tokens(chars: int) -> int:
    """Approximate token count: characters / 3, rounded up."""
    return -(-chars // CHARS_PER_TOKEN)


def budget_to_chars(max_tokens: int) -> int:
    """Largest character count whose token estimate is at most ``max_tokens``."""
    return max_tokens * CHARS_PER_TOKEN


@dataclass(frozen=True)
class ContextFile:
    """The facts about one parsed file that ranking needs. Built from the parse cache."""

    path: str
    module: str
    language: str
    symbols: tuple[str, ...]
    details: tuple[str, ...]
    dependencies: tuple[str, ...]
    has_main: bool
    has_endpoints: bool
    is_test: bool

    @property
    def depth(self) -> int:
        return self.path.count("/")

    @property
    def basename(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Ranked:
    file: ContextFile
    score: int


def context_file_from_cache(path: str, module: str, parsed: dict) -> ContextFile:
    """Build a ContextFile from one cache entry's serialised ParsedFile."""
    symbols: list[str] = []
    for cls in parsed.get("classes", []):
        symbols.append(cls.get("name", ""))
        symbols.extend(m.get("name", "") for m in cls.get("methods", []))
    functions = parsed.get("functions", [])
    symbols.extend(fn.get("name", "") for fn in functions)

    details: list[str] = list(parsed.get("imports", [])) + list(parsed.get("exports", []))
    docstring = parsed.get("module_docstring")
    if docstring:
        details.append(docstring)

    return ContextFile(
        path=path,
        module=module,
        language=parsed.get("language", ""),
        symbols=tuple(s for s in symbols if s),
        details=tuple(details),
        dependencies=tuple(sorted(set(parsed.get("internal_dependencies", [])))),
        has_main=any(fn.get("name") == "main" for fn in functions),
        has_endpoints=bool(parsed.get("database_endpoints")) or bool(parsed.get("external_urls")),
        is_test=bool(_TEST_PATH_RE.search(path)),
    )


def query_terms(query: str | None) -> tuple[str, ...]:
    """Lowercase terms of at least two characters. camelCase in the query is split first."""
    if not query:
        return ()
    spaced = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", query)
    tokens = {t for t in _QUERY_TOKEN_RE.findall(spaced.lower()) if len(t) >= 2}
    return tuple(sorted(tokens))


def _match_weight(file: ContextFile, terms: Sequence[str]) -> int:
    """Name hits (path, symbols) weigh 3 per term; detail hits (imports, exports, docstring) weigh 1."""
    name_text = (file.path + " " + " ".join(file.symbols)).lower()
    detail_text = " ".join(file.details).lower()
    weight = 0
    for term in terms:
        if term in name_text:
            weight += 3
        elif term in detail_text:
            weight += 1
    return weight


def _base_score(file: ContextFile, in_degree: int) -> int:
    score = 0
    if file.basename in ENTRY_BASENAMES:
        score += 3
    if file.has_main:
        score += 2
    score += min(in_degree, 10)
    if file.has_endpoints:
        score += 1
    if file.is_test:
        score -= 4
    return score


def rank_files(files: Sequence[ContextFile], query: str | None = None) -> list[Ranked]:
    """Order files most relevant first. Ties break on depth, then path.

    Without a query: entry points, `main` functions, in-degree (how many files import
    this one, capped at 10), endpoints, and a penalty for test files.

    With a query: each matching term adds to the score, and the direct internal
    dependencies of matched files get a one-hop boost of 5.
    """
    in_degree: Counter[str] = Counter()
    for f in files:
        in_degree.update(set(f.dependencies))

    terms = query_terms(query)
    weights = {f.path: _match_weight(f, terms) for f in files} if terms else {}
    by_path = {f.path: f for f in files}
    neighbour_boost: set[str] = set()
    if terms:
        for f in files:
            if weights[f.path]:
                neighbour_boost.update(d for d in f.dependencies if d in by_path)

    ranked: list[Ranked] = []
    for f in files:
        score = _base_score(f, in_degree[f.path])
        if terms:
            score += 10 * weights[f.path]
            if f.path in neighbour_boost and not weights[f.path]:
                score += 5
        ranked.append(Ranked(file=f, score=score))

    ranked.sort(key=lambda r: (-r.score, r.file.depth, r.file.path))
    return ranked


def filter_by_paths(files: Iterable[ContextFile], patterns: Sequence[str]) -> list[ContextFile]:
    """Keep files whose repo-relative path matches any gitignore-style pattern. Empty means keep all."""
    if not patterns:
        return list(files)
    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    return [f for f in files if spec.match_file(f.path)]


def render_omitted(paths: Sequence[str]) -> str:
    """The omitted-files section. Lists at most OMITTED_LIST_LIMIT paths."""
    shown = paths[:OMITTED_LIST_LIMIT]
    lines = [f"- {p}" for p in shown]
    if len(paths) > OMITTED_LIST_LIMIT:
        lines.append(f"- ... and {len(paths) - OMITTED_LIST_LIMIT} more")
    return OMITTED_TITLE + "\n" + "\n".join(lines)


def omitted_reserve(count: int, max_path_len: int) -> int:
    """Upper bound on the characters `render_omitted` adds for `count` omitted files.

    Includes the two-character separator that joins the section to the rest of the bundle.
    Each path line plus its join is at most ``3 + max_path_len``; the "more" line is
    covered by ``_MORE_LINE_RESERVE``.
    """
    if count == 0:
        return 0
    shown = min(count, OMITTED_LIST_LIMIT)
    size = 2 + len(OMITTED_TITLE) + 1 + shown * (3 + max_path_len)
    if count > OMITTED_LIST_LIMIT:
        size += _MORE_LINE_RESERVE
    return size


class BudgetError(ValueError):
    """The budget cannot hold the fixed text plus the list of omitted files."""


def select_blocks(
    sizes: Sequence[int],
    budget_chars: int | None,
    fixed_chars: int,
    reserve: Callable[[int], int],
) -> list[bool]:
    """Choose which blocks to include, in rank order, within the budget.

    A block that does not fit is skipped, and later smaller blocks may still fit. The
    omitted-section reserve depends on how many blocks are dropped, so the count is
    iterated to a fixed point. Each pass can only raise the count, which is bounded by
    the number of blocks, so the loop always ends. The returned mask never exceeds the
    budget.

    Raises:
        BudgetError: If the fixed text plus the reserve for the omitted list do not fit.
    """
    if budget_chars is None:
        return [True] * len(sizes)

    guess = 0
    while True:
        remaining = budget_chars - fixed_chars - reserve(guess)
        if remaining < 0:
            raise BudgetError("budget is too small to hold the header, the index and the omitted list")
        mask: list[bool] = []
        used = 0
        for size in sizes:
            if used + size <= remaining:
                used += size
                mask.append(True)
            else:
                mask.append(False)
        omitted = mask.count(False)
        if omitted <= guess:
            return mask
        guess = omitted
