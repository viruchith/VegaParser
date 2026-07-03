"""Concatenate .rag_kb Markdown files into a single LLM context bundle."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BUNDLE_FILENAME = "full_repo_context.md"

FILE_SEPARATOR = """
---
# 📄 FILE: {relative_path}
---

"""


class BundleError(Exception):
    """Raised when bundling cannot proceed."""


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def _rough_token_estimate(char_count: int) -> int:
    """Rough heuristic: ~4 characters per token for English/code mix."""
    return char_count // 4


def bundle_knowledge_base(root: Path, output_name: str = BUNDLE_FILENAME) -> Path:
    """
    Concatenate project_index.md and all modules/*.md into one Markdown file.

    Args:
        root: Repository root containing `.rag_kb/`.
        output_name: Output filename inside `.rag_kb/`.

    Returns:
        Path to the written bundle file.

    Raises:
        BundleError: If `.rag_kb/` is missing or contains nothing to bundle.
    """
    root = root.resolve()
    rag_kb = root / ".rag_kb"

    if not rag_kb.is_dir():
        raise BundleError(
            f"No knowledge base found at {rag_kb}\n"
            "Run `repo-parser init` (or `python main.py init`) first to generate `.rag_kb/`."
        )

    index_path = rag_kb / "project_index.md"
    modules_dir = rag_kb / "modules"

    parts: list[str] = []
    bundled_files: list[str] = []

    header = (
        f"<!-- VegaParser full repository context bundle -->\n"
        f"<!-- Source: {root} -->\n"
        f"<!-- Generated from .rag_kb/ — inject into LLMs with large context windows -->\n"
    )
    parts.append(header)

    if index_path.is_file():
        content = index_path.read_text(encoding="utf-8")
        parts.append(content.rstrip())
        bundled_files.append("project_index.md")
        logger.debug("Bundled %s", index_path)
    else:
        logger.warning("Missing %s — bundle will omit the global index", index_path)

    module_files: list[Path] = []
    if modules_dir.is_dir():
        module_files = sorted(modules_dir.glob("*.md"), key=lambda p: p.name.lower())

    for module_path in module_files:
        rel = f"modules/{module_path.name}"
        parts.append(FILE_SEPARATOR.format(relative_path=rel).rstrip())
        content = module_path.read_text(encoding="utf-8")
        parts.append(content.rstrip())
        bundled_files.append(rel)
        logger.debug("Bundled %s", module_path)

    if not bundled_files:
        raise BundleError(
            f"No Markdown files found in {rag_kb}\n"
            "Run `repo-parser init` to generate the knowledge base first."
        )

    bundle_text = "\n\n".join(parts) + "\n"
    output_path = rag_kb / output_name
    output_path.write_text(bundle_text, encoding="utf-8")

    logger.info(
        "Bundled %d files (%s) → %s",
        len(bundled_files),
        _format_size(len(bundle_text.encode("utf-8"))),
        output_path,
    )
    return output_path


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
        "tokens_estimate": _rough_token_estimate(char_count),
    }
