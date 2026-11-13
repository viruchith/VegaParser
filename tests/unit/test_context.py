"""Unit and property tests for ranking and budget selection (repo_parser.generator.context)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from repo_parser.generator.context import (
    CHARS_PER_TOKEN,
    OMITTED_LIST_LIMIT,
    BudgetError,
    ContextFile,
    budget_to_chars,
    context_file_from_cache,
    estimate_tokens,
    filter_by_paths,
    omitted_reserve,
    query_terms,
    rank_files,
    render_omitted,
    select_blocks,
)


def _file(path, symbols=(), details=(), deps=(), has_main=False, has_endpoints=False):
    from repo_parser.generator.context import _TEST_PATH_RE

    return ContextFile(
        path=path,
        module=path.replace("/", "_") + ".md",
        language="python",
        symbols=tuple(symbols),
        details=tuple(details),
        dependencies=tuple(deps),
        has_main=has_main,
        has_endpoints=has_endpoints,
        is_test=bool(_TEST_PATH_RE.search(path)),
    )


# ── tokens ──────────────────────────────────────────────────────────────────


def test_estimate_is_chars_over_three_rounded_up():
    assert CHARS_PER_TOKEN == 3
    assert estimate_tokens(0) == 0
    assert estimate_tokens(1) == 1
    assert estimate_tokens(3) == 1
    assert estimate_tokens(4) == 2
    assert estimate_tokens(300) == 100


@given(st.integers(min_value=0, max_value=10**6), st.integers(min_value=1, max_value=10**5))
def test_budget_in_chars_is_exact_equivalent_of_token_estimate(chars, max_tokens):
    """ceil(c/3) <= n  <=>  c <= 3n. Checking characters is therefore exact."""
    assert (estimate_tokens(chars) <= max_tokens) == (chars <= budget_to_chars(max_tokens))


# ── query terms ─────────────────────────────────────────────────────────────


def test_query_terms_lowercase_split_and_drop_short_tokens():
    assert query_terms("Create Order, a DB") == ("create", "db", "order")


def test_query_terms_split_camel_case():
    assert query_terms("chargeInvoice") == ("charge", "invoice")


def test_empty_query_has_no_terms():
    assert query_terms(None) == ()
    assert query_terms("") == ()


# ── ranking ─────────────────────────────────────────────────────────────────


def test_entry_point_outranks_plain_module():
    files = [_file("pkg/util.py"), _file("app/main.py")]
    assert [r.file.path for r in rank_files(files)] == ["app/main.py", "pkg/util.py"]


def test_in_degree_raises_rank():
    files = [_file("a/leaf.py"), _file("a/core.py"), _file("b/x.py", deps=("a/core.py",)), _file("b/y.py", deps=("a/core.py",))]
    order = [r.file.path for r in rank_files(files)]
    assert order.index("a/core.py") < order.index("a/leaf.py")


def test_test_files_rank_below_source_files():
    files = [_file("tests/test_core.py"), _file("core/service.py")]
    assert [r.file.path for r in rank_files(files)] == ["core/service.py", "tests/test_core.py"]


def test_query_match_boosts_and_neighbours_follow():
    files = [
        _file("main.py", has_main=True),
        _file("billing/invoice.py", symbols=("charge_invoice",), deps=("billing/tax.py",)),
        _file("billing/tax.py", symbols=("rate",)),
        _file("zzz/other.py", symbols=("noop",)),
    ]
    order = [r.file.path for r in rank_files(files, query="invoice")]
    assert order[0] == "billing/invoice.py"
    assert order.index("billing/tax.py") < order.index("zzz/other.py")


def test_query_name_hit_outweighs_detail_hit():
    files = [
        _file("a.py", details=("parses invoices",)),
        _file("b.py", symbols=("invoice_total",)),
    ]
    assert [r.file.path for r in rank_files(files, query="invoice")][0] == "b.py"


def test_ranking_is_deterministic_and_ties_break_on_depth_then_path():
    files = [_file("z/b.py"), _file("a/b.py"), _file("top.py")]
    order = [r.file.path for r in rank_files(files)]
    assert order == ["top.py", "a/b.py", "z/b.py"]
    assert order == [r.file.path for r in rank_files(list(reversed(files)))]


def test_context_file_from_cache_collects_symbols_and_flags():
    parsed = {
        "language": "python",
        "classes": [{"name": "Billing", "methods": [{"name": "charge"}]}],
        "functions": [{"name": "main"}],
        "imports": ["import os"],
        "exports": ["Billing"],
        "module_docstring": "Invoices.",
        "internal_dependencies": ["b.py", "b.py"],
        "database_endpoints": [],
        "external_urls": [{"url": "https://x.example.com", "line": 1, "context": ""}],
    }
    cf = context_file_from_cache("app/billing.py", "app_billing.md", parsed)
    assert set(cf.symbols) == {"Billing", "charge", "main"}
    assert cf.has_main
    assert cf.has_endpoints
    assert cf.dependencies == ("b.py",)
    assert "Invoices." in cf.details


# ── path filter ─────────────────────────────────────────────────────────────


def test_path_filter_keeps_matching_paths_only():
    files = [_file("src/a.py"), _file("src/sub/b.py"), _file("tests/c.py")]
    kept = [f.path for f in filter_by_paths(files, ["src/**"])]
    assert kept == ["src/a.py", "src/sub/b.py"]


def test_empty_path_filter_keeps_everything():
    files = [_file("a.py"), _file("b.py")]
    assert filter_by_paths(files, []) == files


# ── omitted section ─────────────────────────────────────────────────────────


def test_render_omitted_lists_paths_and_caps_the_list():
    paths = [f"d/f{i}.py" for i in range(OMITTED_LIST_LIMIT + 5)]
    text = render_omitted(paths)
    assert text.count("\n- d/") == OMITTED_LIST_LIMIT
    assert "- ... and 5 more" in text


@given(st.lists(st.text(alphabet="abc/._", min_size=1, max_size=40), min_size=1, max_size=250))
@settings(max_examples=60, deadline=None)
def test_omitted_reserve_bounds_the_rendered_section(paths):
    """The reserve must never be smaller than the section it reserves for."""
    rendered = render_omitted(paths)
    max_len = max(len(p) for p in paths)
    assert len(rendered) + 2 <= omitted_reserve(len(paths), max_len)


# ── selection ───────────────────────────────────────────────────────────────


def test_select_unlimited_takes_everything():
    assert select_blocks([10, 20, 30], None, 0, lambda k: 0) == [True, True, True]


def test_select_skips_block_that_does_not_fit_and_keeps_smaller_later_ones():
    mask = select_blocks([50, 80, 20], budget_chars=100, fixed_chars=0, reserve=lambda k: 0)
    assert mask == [True, False, True]


@given(
    sizes=st.lists(st.integers(min_value=1, max_value=400), min_size=0, max_size=60),
    budget_tokens=st.integers(min_value=1, max_value=400),
    fixed=st.integers(min_value=0, max_value=200),
)
@settings(max_examples=200, deadline=None)
def test_selection_never_exceeds_budget_including_omitted_reserve(sizes, budget_tokens, fixed):
    """Property: included blocks plus fixed text plus the omitted reserve fit the budget."""
    budget = budget_to_chars(budget_tokens)
    max_len = 30

    def reserve(count):
        return omitted_reserve(count, max_len)

    if fixed > budget:
        return  # the caller rejects this case before selecting
    try:
        mask = select_blocks(sizes, budget, fixed, reserve)
    except BudgetError:
        return  # rejected rather than exceeding the budget
    used = fixed + sum(s for s, keep in zip(sizes, mask) if keep)
    omitted = mask.count(False)
    assert used + reserve(omitted) <= budget
