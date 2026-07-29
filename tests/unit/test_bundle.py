"""Unit tests for the bundle generator."""

from __future__ import annotations

import pytest

from repo_parser.generator.bundle import (
    BUNDLE_FILENAME,
    FILE_SEPARATOR,
    BundleError,
    bundle_knowledge_base,
    bundle_stats,
)


def _make_kb(root, index="# Index\n", modules=None):
    rag_kb = root / ".rag_kb"
    modules_dir = rag_kb / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    if index is not None:
        (rag_kb / "project_index.md").write_text(index, encoding="utf-8")
    for name, content in (modules or {}).items():
        (modules_dir / name).write_text(content, encoding="utf-8")
    return rag_kb


def test_bundle_raises_when_rag_kb_missing(tmp_path):
    with pytest.raises(BundleError):
        bundle_knowledge_base(tmp_path)


def test_bundle_raises_when_rag_kb_empty(tmp_path):
    (tmp_path / ".rag_kb").mkdir()
    with pytest.raises(BundleError):
        bundle_knowledge_base(tmp_path)


def test_bundle_orders_index_first_then_modules_alphabetically(tmp_path):
    _make_kb(
        tmp_path,
        index="# Project Index\nINDEX_MARKER\n",
        modules={
            "b_module.md": "B_MARKER\n",
            "a_module.md": "A_MARKER\n",
        },
    )
    bundle_path = bundle_knowledge_base(tmp_path)
    assert bundle_path.name == BUNDLE_FILENAME
    text = bundle_path.read_text(encoding="utf-8")

    idx_pos = text.index("INDEX_MARKER")
    a_pos = text.index("A_MARKER")
    b_pos = text.index("B_MARKER")
    assert idx_pos < a_pos < b_pos


def test_bundle_contains_file_separator(tmp_path):
    _make_kb(tmp_path, modules={"a_module.md": "content\n"})
    bundle_path = bundle_knowledge_base(tmp_path)
    text = bundle_path.read_text(encoding="utf-8")
    separator_marker = FILE_SEPARATOR.format(relative_path="modules/a_module.md").strip()
    assert separator_marker in text


def test_bundle_stats_non_zero(tmp_path):
    _make_kb(tmp_path, index="# Index\nsome words here\n", modules={"m.md": "more content\n"})
    bundle_path = bundle_knowledge_base(tmp_path)
    stats = bundle_stats(bundle_path)
    assert stats["characters"] > 0
    assert stats["words"] > 0
    assert stats["bytes"] > 0
    assert stats["tokens_estimate"] > 0
