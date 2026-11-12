"""Unit tests for the bundle generator."""

from __future__ import annotations

import json

import pytest

from repo_parser import __version__
from repo_parser.cache import CACHE_VERSION, cache_path
from repo_parser.generator.bundle import (
    BUNDLE_FILENAME,
    FILE_SEPARATOR,
    BundleError,
    bundle_knowledge_base,
    bundle_stats,
    validate_output_name,
)


def _make_kb(root, index="# Index\n", modules=None, parsed=None, cache=True):
    """Create .rag_kb with an index, module files and (by default) a current parse cache.

    `modules` maps module file name -> text. `parsed` maps repo path -> serialised ParsedFile
    overrides; the module name is derived as `<stem>.md` unless given as (module, parsed).
    """
    rag_kb = root / ".rag_kb"
    modules_dir = rag_kb / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    if index is not None:
        (rag_kb / "project_index.md").write_text(index, encoding="utf-8")
    for name, content in (modules or {}).items():
        (modules_dir / name).write_text(content, encoding="utf-8")
    if cache:
        files = {}
        for rel, parsed_entry in (parsed or {}).items():
            module = parsed_entry.get("module") if isinstance(parsed_entry, dict) and "module" in parsed_entry else None
            body = parsed_entry.get("parsed", {})
            files[rel] = {
                "sha256": "0" * 64,
                "module": module,
                "parsed": {"filepath": rel, "language": "python", **body},
            }
        write_cache(root, files)
    return rag_kb


def write_cache(root, files):
    payload = {
        "version": CACHE_VERSION,
        "tool_version": __version__,
        "filter": {"languages": None, "extensions": None},
        "files": files,
    }
    cache_path(root).parent.mkdir(parents=True, exist_ok=True)
    cache_path(root).write_text(json.dumps(payload), encoding="utf-8")


def _simple_kb(root, count=3, body_chars=200):
    """A KB with `count` modules a0.py..aN.py, each with a body of `body_chars` characters."""
    modules = {}
    parsed = {}
    for i in range(count):
        name = f"m{i}.md"
        modules[name] = f"module {i} " + "x" * body_chars + "\n"
        parsed[f"m{i}.py"] = {"module": name, "parsed": {}}
    _make_kb(root, index="# Index\n", modules=modules, parsed=parsed)


# ── existing behaviour ──────────────────────────────────────────────────────


def test_bundle_raises_when_rag_kb_missing(tmp_path):
    with pytest.raises(BundleError):
        bundle_knowledge_base(tmp_path)


def test_bundle_raises_without_parse_cache(tmp_path):
    _make_kb(tmp_path, modules={"a.md": "A\n"}, parsed={"a.py": {"module": "a.md"}}, cache=False)
    with pytest.raises(BundleError, match="init"):
        bundle_knowledge_base(tmp_path)


def test_bundle_raises_on_cache_from_other_version(tmp_path):
    _make_kb(tmp_path, modules={"a.md": "A\n"}, parsed={"a.py": {"module": "a.md"}})
    payload = json.loads(cache_path(tmp_path).read_text(encoding="utf-8"))
    payload["tool_version"] = "0.0.0-old"
    cache_path(tmp_path).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BundleError, match="init"):
        bundle_knowledge_base(tmp_path)


def test_bundle_orders_index_first_then_modules_by_rank(tmp_path):
    _make_kb(
        tmp_path,
        index="# Project Index\nINDEX_MARKER\n",
        modules={"b_module.md": "B_MARKER\n", "a_module.md": "A_MARKER\n"},
        parsed={"a.py": {"module": "a_module.md"}, "b.py": {"module": "b_module.md"}},
    )
    result = bundle_knowledge_base(tmp_path)
    assert result.path.name == BUNDLE_FILENAME
    text = result.path.read_text(encoding="utf-8")

    idx_pos = text.index("INDEX_MARKER")
    a_pos = text.index("A_MARKER")
    b_pos = text.index("B_MARKER")
    # Equal scores tie-break on path, so a.py comes before b.py.
    assert idx_pos < a_pos < b_pos


def test_bundle_contains_file_separator(tmp_path):
    _make_kb(tmp_path, modules={"a_module.md": "content\n"}, parsed={"a.py": {"module": "a_module.md"}})
    text = bundle_knowledge_base(tmp_path).path.read_text(encoding="utf-8")
    separator_marker = FILE_SEPARATOR.format(relative_path="modules/a_module.md").strip()
    assert separator_marker in text


def test_bundle_stats_non_zero(tmp_path):
    _make_kb(
        tmp_path,
        index="# Index\nsome words here\n",
        modules={"m.md": "more content\n"},
        parsed={"m.py": {"module": "m.md"}},
    )
    stats = bundle_stats(bundle_knowledge_base(tmp_path).path)
    assert stats["characters"] > 0
    assert stats["words"] > 0
    assert stats["bytes"] > 0
    assert stats["tokens_estimate"] > 0


# ── output name ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "../x.md", "sub/x.md", "sub\\x.md", "C:x.md", "x.txt", "x", "project_index.md"],
)
def test_invalid_output_names_are_rejected(name):
    with pytest.raises(BundleError):
        validate_output_name(name)


def test_valid_output_name_is_written_inside_rag_kb(tmp_path):
    _simple_kb(tmp_path, count=1)
    result = bundle_knowledge_base(tmp_path, output_name="context_for_gpt.md")
    assert result.path == tmp_path.resolve() / ".rag_kb" / "context_for_gpt.md"
    assert result.path.is_file()


# ── budget ──────────────────────────────────────────────────────────────────


def test_unlimited_budget_includes_every_module(tmp_path):
    _simple_kb(tmp_path, count=4, body_chars=500)
    result = bundle_knowledge_base(tmp_path)
    assert result.max_tokens is None
    assert result.budget_label == "unlimited"
    assert len(result.included) == 4
    assert result.omitted == ()
    assert "<!-- Budget: unlimited -->" in result.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("max_tokens", [150, 300, 500, 1000, 3000])
def test_bundle_never_exceeds_budget(tmp_path, max_tokens):
    _simple_kb(tmp_path, count=6, body_chars=300)
    result = bundle_knowledge_base(tmp_path, max_tokens=max_tokens)
    assert result.characters <= 3 * max_tokens
    assert result.tokens_estimate <= max_tokens


def test_budget_too_small_for_index_is_an_error(tmp_path):
    _make_kb(
        tmp_path,
        index="# Index\n" + "y" * 2000 + "\n",
        modules={"m.md": "m\n"},
        parsed={"m.py": {"module": "m.md"}},
    )
    with pytest.raises(BundleError, match="too small"):
        bundle_knowledge_base(tmp_path, max_tokens=100)


def test_omitted_files_are_listed_at_the_end(tmp_path):
    _simple_kb(tmp_path, count=5, body_chars=400)
    result = bundle_knowledge_base(tmp_path, max_tokens=300)
    assert result.omitted, "expected some files to be dropped at this budget"
    text = result.path.read_text(encoding="utf-8")
    assert "## Omitted by budget" in text
    for rel in result.omitted:
        assert f"- {rel}" in text
    for rel in result.included:
        assert f"- {rel}" not in text.split("## Omitted by budget")[-1]


def test_bundle_is_deterministic_for_same_inputs(tmp_path):
    _simple_kb(tmp_path, count=5, body_chars=250)
    first = bundle_knowledge_base(tmp_path, max_tokens=250).path.read_bytes()
    second = bundle_knowledge_base(tmp_path, max_tokens=250).path.read_bytes()
    assert first == second


# ── query and path ──────────────────────────────────────────────────────────


def _ranked_kb(tmp_path):
    parsed = {
        "app/billing.py": {
            "module": "billing.md",
            "parsed": {"functions": [{"name": "charge_invoice", "signature": "charge_invoice()"}]},
        },
        "app/misc.py": {"module": "misc.md", "parsed": {"functions": [{"name": "helper", "signature": "helper()"}]}},
        "app/zeta.py": {"module": "zeta.md", "parsed": {"functions": [{"name": "other", "signature": "other()"}]}},
    }
    modules = {name: f"{name} body\n" for name in ("billing.md", "misc.md", "zeta.md")}
    _make_kb(tmp_path, modules=modules, parsed=parsed)


def test_query_moves_matching_module_first(tmp_path):
    _ranked_kb(tmp_path)
    result = bundle_knowledge_base(tmp_path, query="invoice")
    assert result.included[0] == "app/billing.py"


def test_query_matching_is_case_insensitive_and_splits_camel_case(tmp_path):
    _ranked_kb(tmp_path)
    result = bundle_knowledge_base(tmp_path, query="ChargeInvoice")
    assert result.included[0] == "app/billing.py"


def test_path_filter_restricts_candidates(tmp_path):
    _ranked_kb(tmp_path)
    result = bundle_knowledge_base(tmp_path, paths=("app/misc.py",))
    assert result.included == ("app/misc.py",)
    assert result.candidates == 1


def test_path_filter_matching_nothing_still_writes_index(tmp_path):
    _ranked_kb(tmp_path)
    result = bundle_knowledge_base(tmp_path, paths=("nope/**",))
    assert result.included == ()
    assert result.candidates == 0
    assert result.path.is_file()


def test_header_states_query_and_path_filter(tmp_path):
    _ranked_kb(tmp_path)
    result = bundle_knowledge_base(tmp_path, query="invoice", paths=("app/*.py",))
    text = result.path.read_text(encoding="utf-8")
    assert 'relevance to query "invoice"' in text
    assert "Path filter: app/*.py" in text
