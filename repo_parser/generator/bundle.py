"""Build a single LLM context bundle from `.rag_kb/`.

The bundle is ordered by relevance (see `context.rank_files`). With `max_tokens`, whole
module files are included in rank order until the next one would exceed the budget. The
omitted files are listed at the end. The output is deterministic for the same cache and
arguments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from repo_parser import __version__
from repo_parser.cache import CACHE_VERSION, load_cache
from repo_parser.generator.context import (
    BudgetError,
    budget_to_chars,
    context_file_from_cache,
    estimate_tokens,
    filter_by_paths,
    omitted_reserve,
    rank_files,
    render_omitted,
    select_blocks,
)

logger = logging.getLogger(__name__)

BUNDLE_FILENAME = "full_repo_context.md"
INDEX_FILENAME = "project_index.md"

FILE_SEPARATOR = """
---
# 📄 FILE: {relative_path}
---

"""


class BundleError(Exception):
    """Raised when bundling cannot proceed."""


@dataclass(frozen=True)
class BundleResult:
    path: Path
    characters: int
    max_tokens: int | None
    included: tuple[str, ...]
    omitted: tuple[str, ...]
    candidates: int

    @property
    def tokens_estimate(self) -> int:
        return estimate_tokens(self.characters)

    @property
    def budget_label(self) -> str:
        if self.max_tokens is None:
            return "unlimited"
        return f"{self.max_tokens} tokens (approximate: characters / 3)"


@dataclass
class _Block:
    path: str
    text: str = field(repr=False)


def validate_output_name(name: str) -> str:
    """`--output` must be a bare `*.md` file name directly inside `.rag_kb/`."""
    if not name or name in {".", ".."} or any(sep in name for sep in ("/", "\\", ":")):
        raise BundleError(f"--output must be a plain file name inside .rag_kb/, not {name!r}.")
    if not name.lower().endswith(".md"):
        raise BundleError(f"--output must end in .md, not {name!r}.")
    if name == INDEX_FILENAME:
        raise BundleError(f"--output cannot be {INDEX_FILENAME}; that file is the project index.")
    return name


def _header(root: Path, max_tokens: int | None, query: str | None, paths: tuple[str, ...]) -> str:
    budget = "unlimited" if max_tokens is None else f"{max_tokens} tokens (approximate: characters / 3)"
    lines = [
        "<!-- VegaParser full repository context bundle -->",
        f"<!-- Source: {root} -->",
        "<!-- Generated from .rag_kb/ — inject into LLMs with large context windows -->",
        f"<!-- Budget: {budget} -->",
    ]
    if query:
        safe = " ".join(query.split()).replace("--", "- -")
        lines.append(f'<!-- Ranking: relevance to query "{safe}" -->')
    else:
        lines.append("<!-- Ranking: relevance (entry points, in-degree, endpoints, path) -->")
    if paths:
        safe_paths = ", ".join(p.replace("--", "- -") for p in paths)
        lines.append(f"<!-- Path filter: {safe_paths} -->")
    return "\n".join(lines)


def _module_block(module: str, text: str) -> str:
    return FILE_SEPARATOR.format(relative_path=f"modules/{module}").rstrip() + "\n\n" + text.rstrip()


def bundle_knowledge_base(
    root: Path,
    output_name: str = BUNDLE_FILENAME,
    *,
    max_tokens: int | None = None,
    query: str | None = None,
    paths: tuple[str, ...] | list[str] | None = None,
) -> BundleResult:
    """Write `.rag_kb/<output_name>` from the project index and the module files.

    Args:
        root: Repository root containing `.rag_kb/`.
        output_name: Bare `*.md` file name inside `.rag_kb/`.
        max_tokens: Approximate token budget (characters / 3). None means unlimited.
        query: Optional free-text query. Matching files and their direct dependencies rank higher.
        paths: Optional gitignore-style patterns. Only matching modules are considered.

    Raises:
        BundleError: If the output name is invalid, the knowledge base or cache is missing,
            or the budget cannot fit the header and project index.
    """
    validate_output_name(output_name)
    if max_tokens is not None and max_tokens < 1:
        raise BundleError("--max-tokens must be at least 1.")

    root = root.resolve()
    rag_kb = root / ".rag_kb"
    if not rag_kb.is_dir():
        raise BundleError(
            f"No knowledge base found at {rag_kb}\n"
            "Run `repo-parser init` first to generate `.rag_kb/`."
        )

    payload = load_cache(root)
    if payload is None or payload.get("version") != CACHE_VERSION or payload.get("tool_version") != __version__:
        raise BundleError(
            "No current parse cache under .rag_kb/ (missing, or written by another VegaParser version).\n"
            "Run `repo-parser init` to regenerate `.rag_kb/`."
        )

    modules_dir = rag_kb / "modules"
    contexts = []
    for rel, entry in payload.get("files", {}).items():
        module = entry.get("module")
        parsed = entry.get("parsed")
        if not module or parsed is None or not (modules_dir / module).is_file():
            continue
        contexts.append(context_file_from_cache(rel, module, parsed))

    selected = filter_by_paths(contexts, tuple(paths or ()))
    ranked = rank_files(selected, query)
    ranked_files = [r.file for r in ranked]

    index_path = rag_kb / INDEX_FILENAME
    index_text: str | None = None
    if index_path.is_file():
        index_text = index_path.read_text(encoding="utf-8").rstrip()
    else:
        logger.warning("Missing %s — bundle will omit the global index", index_path)

    blocks = [
        _Block(path=f.path, text=_module_block(f.module, (modules_dir / f.module).read_text(encoding="utf-8")))
        for f in ranked_files
    ]

    header = _header(root, max_tokens, query, tuple(paths or ()))
    fixed = len(header) + 1  # trailing newline
    if index_text is not None:
        fixed += len(index_text) + 2  # index part and its separator

    budget_chars = None if max_tokens is None else budget_to_chars(max_tokens)
    if budget_chars is not None and fixed > budget_chars:
        raise BundleError(
            f"--max-tokens {max_tokens} is too small: the header and project index alone need "
            f"about {estimate_tokens(fixed)} tokens."
        )

    max_path_len = max((len(f.path) for f in ranked_files), default=0)

    def reserve(count: int) -> int:
        return omitted_reserve(count, max_path_len)

    try:
        mask = select_blocks([len(b.text) + 2 for b in blocks], budget_chars, fixed, reserve)
    except BudgetError as exc:
        raise BundleError(
            f"--max-tokens {max_tokens} is too small to list the modules left out. "
            "Raise the budget, or narrow the bundle with --path or --query."
        ) from exc
    included_blocks = [b for b, keep in zip(blocks, mask) if keep]
    omitted_paths = tuple(b.path for b, keep in zip(blocks, mask) if not keep)

    parts = [header]
    if index_text is not None:
        parts.append(index_text)
    parts.extend(b.text for b in included_blocks)
    if omitted_paths:
        parts.append(render_omitted(omitted_paths))
    bundle_text = "\n\n".join(parts) + "\n"

    if budget_chars is not None and len(bundle_text) > budget_chars:
        # Unreachable by construction; keep the guarantee explicit rather than silently exceeding it.
        raise BundleError("Internal error: bundle exceeded its budget. Please report this.")

    output_path = rag_kb / output_name
    output_path.write_text(bundle_text, encoding="utf-8")

    logger.info(
        "Bundled %d of %d modules (%d chars, ~%d tokens) → %s",
        len(included_blocks),
        len(blocks),
        len(bundle_text),
        estimate_tokens(len(bundle_text)),
        output_path,
    )
    return BundleResult(
        path=output_path,
        characters=len(bundle_text),
        max_tokens=max_tokens,
        included=tuple(b.path for b in included_blocks),
        omitted=omitted_paths,
        candidates=len(blocks),
    )


def bundle_stats(bundle_path: Path) -> dict[str, int | str]:
    """Return size and count statistics for a bundle file."""
    text = bundle_path.read_text(encoding="utf-8")
    char_count = len(text)
    word_count = len(text.split())
    byte_count = len(text.encode("utf-8"))
    return {
        "characters": char_count,
        "words": word_count,
        "bytes": byte_count,
        "size_human": _format_size(byte_count),
        "tokens_estimate": estimate_tokens(char_count),
    }


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"
