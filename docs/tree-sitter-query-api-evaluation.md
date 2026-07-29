# Tree-sitter Query API Evaluation

**Status:** Spike / evaluation — no production code changed.
**Spike module:** [`repo_parser/parser/queries/python_queries_query_api.py`](../repo_parser/parser/queries/python_queries_query_api.py)

## 1. Environment / versions

| Package | Version |
|---------|---------|
| `tree-sitter` | 0.26.0 |
| `tree-sitter-language-pack` | 1.12.5 |
| Python | 3.14 (CI matrix: 3.10–3.12) |

## 2. Compatibility analysis

The tree-sitter Python Query API changed significantly across recent releases.
The patterns most tutorials show (`language.query(...)`, `query.captures(node)`)
**do not work on `tree-sitter` 0.26**:

| Concern | ≤ 0.22 | 0.23 – 0.24 | **0.25 – 0.26 (installed)** |
|---------|--------|-------------|------------------------------|
| Build a query | `language.query(src)` | `language.query(src)` | `Query(language, src)` |
| Run captures | `query.captures(node)` → list of `(node, name)` | `query.captures(node)` → **dict** `{name: [nodes]}` | `QueryCursor(query).captures(node)` → **dict** |
| Run matches | `query.matches(node)` | `query.matches(node)` | `QueryCursor(query).matches(node)` |

On 0.26 specifically:

* `Language.query()` was **removed** — construct with `tree_sitter.Query(language, source)`.
* `captures()` / `matches()` moved from `Query` onto a new `tree_sitter.QueryCursor`.
* `captures()` returns a `dict[str, list[Node]]` keyed by capture name.
* `matches()` returns `list[tuple[int, dict[str, list[Node]]]]`.

This churn is the main risk of adopting the Query API: the calling convention
is version-sensitive and would need a compatibility shim similar to the
existing [`ts_compat.py`](../repo_parser/parser/ts_compat.py) adapter.

## 3. Code comparison

**Manual traversal (current production, `python_queries.py`):**

```python
for func_node in iter_nodes(root, "function_definition"):
    name_node = func_node.child_by_field_name("name")
    func_name = node_text(source, name_node)
    ...
```

**Query API (spike, `python_queries_query_api.py`):**

```python
query = ts.Query(language, "(function_definition name: (identifier) @func_name) @func")
caps = ts.QueryCursor(query).captures(root)          # {"func": [...], "func_name": [...]}
for func_node, name_node in zip(caps["func"], caps["func_name"]):
    func_name = name_node.text.decode()
    ...
```

The Query API is more declarative and removes hand-written recursion, but the
returned captures are **flat, per-capture lists** — correlating a `@func` node
with its matching `@func_name` requires either `matches()` (grouped per match)
or node-identity bookkeeping (`node.id`). The spike uses `matches()`-style
correlation via `node.id` maps.

Both implementations produce **identical** extraction results on the fixtures:

```
TRAVERSAL  classes=[]  funcs=['fetch_status']  imports=2
QUERY_API  classes=[]  funcs=['fetch_status']  imports=2
```

## 4. Benchmark

`tests/fixtures/config_sample.py`, 100 iterations, Python 3.14, Windows.

| Approach | Time / iter |
|----------|-------------|
| Manual traversal (`parse_python`) | **~0.43 ms** |
| Query API — queries compiled **per call** (naive spike) | ~7.4 ms |
| Query API — captures only, queries **precompiled/cached** | **~0.09 ms** |
| Cost of a single `Query(...)` construction | ~1.79 ms |

Key insight: `Query()` construction is expensive (~1.8 ms each). Compiling the
three queries (imports/classes/functions) on **every** parse dominates runtime
and makes the naive spike ~15× slower than traversal. When queries are compiled
**once** and reused, the capture step is actually **faster** than manual
traversal (~0.09 ms vs ~0.43 ms).

## 5. Recommendation

**Do not adopt the Query API for production right now, but keep it in reserve.**

Reasons:

1. **Performance is only competitive with caching.** A Query-API rewrite would
   *have* to precompile and cache `Query` objects at module import to avoid a
   large regression. The naive form is ~15× slower.
2. **Version fragility.** The `Query`/`QueryCursor` API moved twice in three
   minor releases. Production already needs [`ts_compat.py`](../repo_parser/parser/ts_compat.py)
   to bridge the node API; adding query-object churn increases that surface.
3. **Correlation ergonomics.** `captures()` returning flat per-name lists means
   matching `@class` ↔ `@class_name` needs `matches()` or `node.id` maps —
   comparable in complexity to the current `child_by_field_name` traversal.
4. **Marginal upside.** The current traversal is already fast, well-tested, and
   language-generic via `common_queries.py` profiles.

**If** adopted later, do so behind a cached-query layer and a version-aware shim,
and migrate one language at a time (Python first) with parity tests against the
traversal implementation.
