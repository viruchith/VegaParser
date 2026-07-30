# Contributing to VegaParser

Thank you for your interest in contributing. This document is the **technical reference** for developers — it covers the full module architecture, data flow, how to add new languages, how to write tests, and the contribution workflow.

---

## Table of Contents

1. [Development Setup](#development-setup)
2. [Architecture Overview](#architecture-overview)
3. [Module Reference](#module-reference)
   - [models.py](#modelspy)
   - [traversal/scanner.py](#traversalscannerpy)
   - [parser/registry.py](#parserregistrypy)
   - [parser/engine.py](#parserenginepy)
   - [parser/queries/base.py](#parserqueriesbasepy)
   - [parser/queries — language extractors](#parserqueries--language-extractors)
   - [parser/extractors/endpoints.py](#parserextractorsendpointspy)
   - [generator/markdown.py](#generatormarkdownpy)
   - [generator/bundle.py](#generatorbundlepy)
   - [generator/templates](#generatortemplates)
   - [stack/detector.py](#stackdetectorpy)
   - [cache.py](#cachepy)
   - [cli.py](#clipy)
   - [scripts/benchmark_vegaparser.py](#scriptsbenchmark_vegaparserpy)
   - [ui/](#ui)
4. [Data Flow in Detail](#data-flow-in-detail)
5. [Adding a New Language](#adding-a-new-language)
6. [Adding a New Endpoint Pattern](#adding-a-new-endpoint-pattern)
7. [Testing Guide](#testing-guide)
8. [Contribution Workflow](#contribution-workflow)
9. [Code Style](#code-style)
10. [Known Limitations & Future Work](#known-limitations--future-work)

---

## Development Setup

```bash
git clone https://github.com/viruchith/VegaParser.git
cd VegaParser

python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1

pip install -r requirements.txt -r requirements-dev.txt
pip install -e .                 # editable install for the console script

# Verify
pytest                           # 342 tests should pass
repo-parser --help
```

### Runtime dependencies (`requirements.txt`)

| Package | Role |
|---------|------|
| `typer` | CLI framework — `app.command()` decorators, argument parsing |
| `rich` | Terminal output — `Console`, progress bar, spinner |
| `tree-sitter` | Tree-sitter Python bindings |
| `tree-sitter-language-pack` | Pre-built grammars for 30+ languages in one wheel |
| `pathspec` | `.gitignore` pattern matching |
| `Jinja2` | Markdown template rendering |
| `PyYAML` | YAML parsing in tests (frontmatter validation) |

### Dev dependencies (`requirements-dev.txt`)

| Package | Role |
|---------|------|
| `pytest` | Test runner |
| `pytest-cov` | Coverage reporting |
| `syrupy` | Snapshot testing (chosen over `pytest-snapshot` for better pytest integration) |
| `hypothesis` | Property-based testing for the security test suite |

---

## Architecture Overview

```
CLI (cli.py)
  │
  ├── RepositoryScanner          traversal/scanner.py
  │     • pathspec .gitignore
  │     • skip-dir rules
  │     • binary detection
  │     └─▶ list[Path]
  │
  ├── parse_cache helpers        cache.py
  │     • parse_cache.json payload (version/filter/files)
  │     • serialised ParsedFile + file signatures
  │     └─▶ cache hit/miss per file
  │
  ├── ParserEngine               parser/engine.py
  │     • language detection via registry.py
  │     • dispatches to queries/*_queries.py
  │     • calls enrich_parsed_file() on every result
  │     └─▶ ParsedFile
  │
  ├── enrich_parsed_file         parser/extractors/endpoints.py
  │     • regex scan, language-agnostic
  │     • populates external_urls + database_endpoints
  │     • redacts secrets
  │
  ├── infer_internal_dependencies parser/engine.py
  │     • links import tokens to known module paths
  │
  └── MarkdownGenerator          generator/markdown.py
        • Jinja2 render via module.md.j2
        • Jinja2 render via project_index.md.j2
        └─▶ .rag_kb/modules/*.md
            .rag_kb/project_index.md

bundle_knowledge_base            generator/bundle.py
  • reads .rag_kb/
  └─▶ .rag_kb/full_repo_context.md
```

---

## Module Reference

### `models.py`

All shared data classes. No logic — pure `@dataclass` definitions.

| Class | Key fields | Purpose |
|-------|-----------|---------|
| `ParsedFile` | `filepath`, `language`, `imports`, `exports`, `classes`, `functions`, `external_calls`, `external_urls`, `database_endpoints`, `internal_dependencies` | One instance per parsed source file; the central unit of data |
| `ClassInfo` | `name`, `bases`, `decorators`, `methods`, `docstring`, `line_start`, `line_end` | One class/struct/interface extracted from AST |
| `FunctionInfo` | `name`, `signature`, `docstring`, `decorators`, `is_method`, `parent_class`, `line_start`, `line_end`, `internal_calls` | One function/method |
| `ExternalCall` | `pattern`, `line`, `context` | SDK call pattern match (e.g. `requests.get`) |
| `ExternalUrl` | `url`, `line`, `context` | HTTP/WS/FTP URL found in source |
| `DatabaseEndpoint` | `connection_type`, `host`, `port`, `user`, `schema`, `database`, `line`, `context`, `raw_redacted` | DB/broker connection detail; password always redacted |

**Serialisation note:** `dataclasses.asdict()` is used in `cache.py` to serialise `ParsedFile` to JSON. Add new fields to all dataclasses carefully — the cache deserialiser in `_dict_to_parsed_file()` must be updated in sync.

---

### `traversal/scanner.py`

`RepositoryScanner` is the entry point for file discovery.

```python
scanner = RepositoryScanner(root, languages={"python", "go"}, extensions={".py", ".go"})
files = scanner.discover()   # list[Path] — repo-relative
content = scanner.read_file(files[0])
```

**Key internals:**

- `SKIP_DIRS` — set of directory names that are always skipped (`.git`, `node_modules`, `venv`, `.rag_kb`, `__pycache__`, etc.)
- `_should_skip_dir(rel_dir_posix, name)` — also skips hidden dirs and prunes `.gitignore`-matched directories before descent
- `_is_binary(path)` — checks `BINARY_EXTENSIONS` set first; falls back to null-byte heuristic (reads first 8 KB)
- `_load_gitignore(root)` — loads `.gitignore` with `pathspec.PathSpec.from_lines("gitwildmatch", ...)`
- `_is_selected_file(path, rel_posix)` — calls `detect_language()` then checks `self.languages` and `self.extensions` filters
- `MAX_FILE_BYTES` — skips files larger than 512 KB to avoid generated/minified artifacts dominating parse time

**Important:** `discover()` uses `os.walk()` and prunes skip/ignored directories in-place (`dirnames[:] = ...`) so the walk does not descend into excluded trees. This is a major traversal speedup on large repos.

---

### `parser/registry.py`

Maps file extensions and special filenames to language identifiers.

```python
detect_language("src/app.py")         # → "python"
detect_language(".env.production")     # → "env"
detect_language("Dockerfile")          # → "dockerfile"
detect_language("unknown.xyz")         # → None

extensions_for_languages({"python", "go"})  # → {".py", ".pyw", ".go"}
normalize_language_filter("py,k8s,tf")      # → {"python", "kubernetes", "terraform"}
```

**Three lookup tables:**

1. `EXTENSION_TO_LANGUAGE` — `{".py": "python", ".go": "go", …}` (47 entries)
2. `FILENAME_TO_LANGUAGE` — `{"dockerfile": "dockerfile", "docker-compose.yml": "yaml", …}` (case-insensitive)
3. `LANGUAGE_ALIASES` — normalises user-supplied `--languages` strings (`"py"→"python"`, `"k8s"→"kubernetes"`, …)

**Special `.env` handling** — `detect_language` checks for `.env` prefix before the extension table, so `.env`, `.env.test`, `.env.production` all return `"env"`.

---

### `parser/engine.py`

`ParserEngine` orchestrates parsing and wires together all components.

```python
engine = ParserEngine()
result = engine.parse_file("src/app.py", source_text)  # ParsedFile | None
engine.infer_internal_dependencies(parsed_files)
```

**`parse_file` flow:**

1. `detect_language(filepath)` → language string or `None`
2. Lookup `PARSERS[language]` → language-specific callable
3. Config/heuristic languages (`env`, `properties`, `ini`, `java`, `sql`, `plsql`) bypass tree-sitter and call the parser directly
4. Tree-sitter languages use `_parse_file_isolated()` with grammar aliases and per-thread parser reuse via `_get_ts_parser()`
5. Call `parser_fn(filepath, source, parser)` → `ParsedFile`
6. `_parse_file_isolated()` enriches each parsed result with URLs + DB endpoints
7. Catch all exceptions, log failures, return `None` (never raises to caller)

**Thread-safety note:** tree-sitter parsers are not shared across threads. Each worker thread keeps its own parser cache.

**`infer_internal_dependencies`** builds a stem map of all known module paths, then for each `ParsedFile`, tries to resolve each import token against that map. Relative imports (starting with `.`) are resolved relative to the file's directory. Only exact matches are linked — this is intentionally conservative.

**`PARSERS` dict** — populated at module level; also extended with `PROFILES` entries from `common_queries.py`:

```python
PARSERS = {
    "python": lambda fp, src, parser: parse_python(fp, src, parser),
    "go":     lambda fp, src, parser: parse_common(fp, src, parser, "go"),
    # …
}
```

---

### `parser/queries/base.py`

Low-level tree-sitter node helpers used by all language extractors. These abstract over the tree-sitter API surface so extractors don't call tree-sitter directly.

| Function | Signature | Notes |
|----------|-----------|-------|
| `node_text(source, node)` | `(str, Node) → str` | Byte-range slice with UTF-8 decode |
| `node_kind(node)` | `(Node) → str` | Returns `node.kind()` |
| `iter_nodes(node, kind=None)` | `(Node, str?) → Iterator[Node]` | Depth-first; filters by kind when given |
| `find_child_by_kind(node, kind)` | `(Node, str) → Node?` | First direct child matching kind |
| `line_number(node)` | `(Node) → int` | 1-based start line |
| `line_end(node)` | `(Node) → int` | 1-based end line |
| `strip_docstring_quotes(text)` | `(str) → str` | Strips `"""`, `'''`, `"`, `'` wrappers |
| `build_python_signature(source, node, name)` | → `str` | `def name(params) -> return_type` |
| `build_js_signature(source, node, name)` | → `str` | `function name(params): return_type` |

> **tree-sitter API note:** VegaParser uses the manual AST traversal approach (`iter_nodes` depth-first). The `ts_compat.py` compatibility shim handles `Parser.parse()` API changes between tree-sitter versions. See `docs/tree-sitter-query-api-evaluation.md` for a benchmarked comparison with the Query API.

---

### `parser/queries` — language extractors

Each extractor receives `(filepath: str, source: str, parser)` and returns a `ParsedFile`.

#### `python_queries.py` — dedicated extractor

Most feature-complete extractor. Handles:
- Module-level docstrings (first `expression_statement` child of `module`)
- `import_statement` and `import_from_statement` at module level
- `class_definition` nodes with bases, decorators, docstrings, and all methods
- `function_definition` nodes, skipping nested functions via `_is_nested_function`
- `_detect_external_calls` scans `call` nodes against `EXTERNAL_CALL_PATTERNS` (requests, httpx, boto3, openai, cursor.execute, etc.)
- Exports list: all top-level functions + classes

#### `javascript_queries.py` — JS/TS extractor

Handles `javascript` and `typescript` (passed as `lang` argument). Extracts:
- `import_statement` and `import_declaration`
- `class_declaration` with methods
- `function_declaration`, `arrow_function`, `method_definition`
- `fetch(...)` / `axios.*` patterns as external calls

#### `common_queries.py` — profile-driven extractor

Drives extraction for Go, Rust, Java, Ruby, C, C++, C#, PHP, Kotlin, Scala, Swift via `LanguageProfile` dataclasses. Each profile declares:

```python
@dataclass(frozen=True)
class LanguageProfile:
    language: str
    import_kinds: tuple[str, ...]      # AST node types for imports
    class_kinds: tuple[str, ...]       # class/struct/interface declarations
    function_kinds: tuple[str, ...]    # top-level functions
    method_kinds: tuple[str, ...]      # methods (may overlap with function_kinds)
    struct_kinds: tuple[str, ...]      # struct-only types (Go, C, Rust)
    module_kinds: tuple[str, ...]      # root/source file node kinds
    comment_kinds: tuple[str, ...]     # comment node kinds (for module docstring)
    skip_nested_functions: bool        # whether to skip nested fn definitions
```

`parse_with_profile` is the generic driver that uses a profile to traverse the AST. Ruby requires a special case: `require` statements are matched via line-prefix scan (not AST), because the tree-sitter Ruby grammar treats `require` as a method call.

#### `docker_queries.py`

Parses Dockerfile instruction nodes: `FROM`, `RUN`, `COPY`, `CMD`, `EXPOSE`, `ENV`, `ARG`. Produces a `ParsedFile` with instructions listed as functions.

#### `yaml_queries.py`

Detects Kubernetes manifests by checking for `apiVersion` key in the YAML AST. If found, extracts `kind`, `metadata.name`, `metadata.namespace`, and container `image` fields. Non-K8s YAML is parsed with metadata only.

#### `sql_queries.py`

Regex + AST hybrid. Extracts `CREATE TABLE` and `CREATE PROCEDURE`/`CREATE FUNCTION`/`CREATE PACKAGE` statements. Detects PL/SQL dialect via `EXECUTE IMMEDIATE`, `BEGIN`/`END`, and `PRAGMA` keywords.

#### `hcl_queries.py`

Parses Terraform/HCL via AST for `resource`, `module`, `variable`, `output`, and `data` blocks. Block type and name are extracted as class/function names.

#### `shell_queries.py`

Extracts bash function definitions. Also pattern-matches lines for known CLI tool invocations (`curl`, `wget`, `docker`, `kubectl`, `aws`, `gcloud`, etc.) and records them as external calls.

#### `env_queries.py`

Scans `.env`, `.properties`, and `.ini` files line by line. Extracts all `KEY=VALUE` assignments. Does not use tree-sitter (no grammar for these formats). Endpoint enrichment runs afterward via the standard `enrich_parsed_file` path.

---

### `parser/extractors/endpoints.py`

Runs **on every parsed file** after language-specific extraction, regardless of language. The scan is line-by-line.

**Regex patterns (in order of evaluation per line):**

1. **`ENV_VAR_LINE`** — `^(?:export )?([A-Z][A-Z0-9_]*)\s*=\s*(.+)$`
   If the variable name is in `ENV_CONNECTION_VARS`, the value is parsed as a URI or stored as a partial endpoint. Variable names not in the set are silently ignored.

2. **`CONFIG_KV_PATTERNS`** — five patterns for `host=`, `port=`, `user=`, `database=`, `schema=` with variations (`db_host`, `hostname`, `db_user`, `username`, etc.). Multi-field lines are coalesced into one `DatabaseEndpoint`.

3. **`DICT_KV_PATTERNS`** — Python/JSON dict-style: `"host": "value"`, `'port': 5432`.

4. **`CONN_URI_PATTERN`** — matches `postgres://`, `mysql://`, `mongodb://`, `redis://`, `amqp://`, `mssql://`, `sqlite://`, `cockroachdb://`, plus driver suffixes like `postgresql+psycopg2://`.

5. **`JDBC_PATTERN`** — matches `jdbc:postgresql://...`, `jdbc:mysql://...`, etc.

6. **`ENGINE_STRING_PATTERN`** — matches `create_engine("...")`, `createConnection("...")`, `connect("...")`.

7. **`URL_PATTERN`** — HTTP/WS/FTP URLs; skips localhost/127.0.0.1/0.0.0.0; skips URIs already matched by `CONN_URI_PATTERN`.

**`redact_secrets(value)`** — two-pass redaction:
```python
# Pass 1: ://user:password@  →  ://user:***@
re.sub(r"://([^:/@]*):([^@/]+)@", r"://\1:***@", value)
# Pass 2: password=value  →  password=***
re.sub(r"(?i)(password|passwd|pwd|secret|token)\s*[=:]\s*['\"]?[^'\"#\s,]+", r"\1=***", value)
```

**Known limitations** (documented in `DESIGN.md`):
- Multi-line string concatenation (e.g. `"postgres://" + password + "@host"`) — not detected
- F-string interpolation (`f"postgres://{user}:{password}@host"`) — not redacted
- Commented-out lines (`# host = db.example.com`) — **are** currently captured (intentional)

---

### `generator/markdown.py`

`MarkdownGenerator` writes the knowledge base files.

```python
gen = MarkdownGenerator(root)   # root = repo root Path
index_path = gen.generate(parsed_files)   # returns Path to project_index.md
```

**`sanitize_filename(filepath)`** — converts a repo-relative path to a safe `.md` filename:
- Replaces all non-`[a-zA-Z0-9_./-]` characters with `_`
- Replaces `/` with `_` and `.` with `_`
- Appends `.md`

Examples:
- `"src/app.py"` → `"src_app_py.md"`
- `"k8s/deployment.yaml"` → `"k8s_deployment_yaml.md"`
- `".env.production"` → `"_env_production.md"`

**Template context variables** passed to `module.md.j2`:
`filepath`, `language`, `imports`, `exports`, `internal_dependencies`, `external_calls`, `external_urls`, `database_endpoints`, `module_docstring`, `classes`, `functions`

**Template context variables** passed to `project_index.md.j2`:
`root`, `project_name`, `file_count`, `language_counts`, `dependency_edges`, `file_index`, `stack`, `external_urls`, `database_endpoints`

---

### `generator/bundle.py`

`bundle_knowledge_base(root, output_name)` concatenates `.rag_kb/` into a single file.

**Ordering:** HTML comment header → `project_index.md` → `modules/*.md` (alphabetical by filename).

**Separator** between each module file:
```
---
# 📄 FILE: modules/src_app_py.md
---
```

`bundle_stats(bundle_path)` returns `{characters, words, bytes, size_human, tokens_estimate}` where `tokens_estimate = char_count // 4`.

Raises `BundleError` (not `SystemExit`) when `.rag_kb/` is missing or contains no Markdown files.

---

### `generator/templates`

#### `module.md.j2`

Produces per-file knowledge base documents:
1. YAML frontmatter (filepath, language, imports, exports, optional: internal_dependencies, external_calls, external_urls, database_endpoints)
2. `# File: filepath`
3. `## Overview` — module docstring or `*No module-level documentation found.*`
4. `## Imports` — bullet list
5. `## Classes` — each with bases, decorators, docstring, line range, and `#### Methods` subsection
6. `## Functions` — each with signature code block, decorators, docstring, internal calls, line range
7. `## External Service Calls` — table
8. `## External URLs` — table
9. `## Database & Connection Endpoints` — table

#### `project_index.md.j2`

Produces the global project map:
1. YAML frontmatter (title, root, generated_by, file_count)
2. Tech stack sections (Python, Node, Go, Rust)
3. Files by language table
4. Module dependency graph (import→module edges)
5. External URLs project-wide
6. Database endpoints project-wide
7. File index table (with links to module docs)
8. Quick navigation links

---

### `stack/detector.py`

`detect_stack(root)` returns:
```python
{
    "languages": [],          # reserved for future use
    "python_packages": [...], # from requirements.txt
    "node_packages": [...],   # from package.json (deps + devDeps, sorted)
    "go_modules": [...],      # from go.mod (first 50)
    "rust_crates": [...],     # from Cargo.toml [dependencies]
    "other": [...],           # e.g. "pyproject.toml detected"
}
```

Parsing is line-by-line for all formats; `json.loads` for `package.json`. Missing files produce empty lists (never raises).

---

### `cache.py`

Incremental caching is persisted to `.rag_kb/parse_cache.json`.

**Cache payload format:**
```json
{
  "version": 1,
  "filter": {
    "languages": ["python"],
    "extensions": [".py", ".pyw"]
  },
  "files": {
    "src/app.py": {
      "meta": { "mtime_ns": 0, "size": 0 },
      "parsed": { "filepath": "...", "language": "python", "classes": [] }
    }
  }
}
```

**Public API:**

| Method | Description |
|--------|-------------|
| `cache_path(root)` | Returns `<root>/.rag_kb/parse_cache.json` |
| `build_filter_signature(languages, extensions)` | Produces deterministic cache filter metadata |
| `load_cache(root)` | Loads JSON cache payload; returns `None` when missing/invalid |
| `save_cache(root, payload)` | Writes cache payload to disk |
| `file_signature(path)` | Returns stat signature (`mtime_ns`, `size`) for cache-hit checks |
| `parsed_file_to_dict(parsed)` / `parsed_file_from_dict(data)` | Dataclass ↔ dict conversion for cached parsed payloads |

> `IndexCache` remains in `cache.py` for backward compatibility, but the active CLI path uses `load_cache` / `save_cache` and `parse_cache.json`.

---

### `cli.py`

Typer application with two commands: `init` and `bundle`.

**`init` flow:**
1. `setup_logging(verbose)` — configures file handler at `INFO` or `DEBUG`
2. `RepositoryScanner(root, languages, extensions)` — initialise scanner
3. `scanner.discover()` wrapped in `run_with_spinner` — shows spinner during discovery
4. `load_cache(root)` — load incremental cache payload when `version` and `filter` match
5. Determine worker count via `--workers/-j` (`0` = auto `min(cpu_count, 8)`)
6. `parsing_progress(total_steps)` context manager — shows progress bar
7. `_parse_files(...)` — cache-aware parse pipeline; sequential for `workers=1`, `ThreadPoolExecutor` for `workers>1`
8. `engine.infer_internal_dependencies(parsed_files)`
9. `save_cache(root, payload)` with per-file `meta` + serialized `parsed`
10. `MarkdownGenerator(root).generate(parsed_files)`
11. Print summary and log path

**`bundle` flow:**
1. `setup_logging(verbose)`
2. `bundle_knowledge_base(root, output_name)` wrapped in `run_with_spinner`
3. `bundle_stats(bundle_path)` → print size/words/tokens/warning

---

### `scripts/benchmark_vegaparser.py`

Central benchmark orchestrator for the full language suite.

- Runs benchmark targets from one script with per-target language filters
- Supports target-level parallelism via `--workers`
- Caps heavy-target concurrency via `--max-heavy-workers`
- Emits timestamped CLI log lines at every verbosity level
- Handles `Ctrl+C` gracefully by setting a shutdown event, draining in-flight tasks, and marking unfinished targets as `cancelled`
- Snapshots each target's `.rag_kb` and `repo-parser.log` to `.benchmark-artifacts/<target-id>/` and records them as `artifact_rag_kb` / `artifact_log` in JSON output

Use this script for reproducible benchmark runs and per-target artifact audits.

---

### `ui/`

| Module | Exports | Notes |
|--------|---------|-------|
| `console.py` | `console` — shared `rich.Console` instance | All non-log output goes through this |
| `logging_config.py` | `setup_logging(verbose, log_dir=None, log_target='file') → Path \\| None` | Supports file/console/both handlers; CLI defaults to file logging |
| `progress.py` | `run_with_spinner`, `parsing_progress`, `truncate_filepath` | `run_with_spinner` runs a callable while showing a spinner; `parsing_progress` is a context manager that yields an `update` callable |

---

## Data Flow in Detail

```
CLI init(path, languages, workers, verbose)
  │
  ├── setup_logging(verbose)
  │     → repo-parser.log (FileHandler only)
  │
  ├── RepositoryScanner(root)
  │     → .gitignore loaded via pathspec
  │     → discover() → sorted list[rel_path]
  │
  ├── load_cache(root)
  │     → parse_cache.json (version/filter/files)
  │
  ├── _parse_files(..., workers)
  │     → sequential (workers=1) or ThreadPoolExecutor (workers>1)
  │
  ├── For each rel_path:
  │     content = scanner.read_file(rel_path)    # UTF-8 with error replace
  │     → cache hit: parsed_file_from_dict(...)
  │     → cache miss: engine.parse_file(...)
  │
  ├── engine.infer_internal_dependencies(parsed_files)
  │     → populates ParsedFile.internal_dependencies
  │
  ├── save_cache(root, payload)
  │
  ├── MarkdownGenerator(root).generate(parsed_files)
  │     → modules_dir.mkdir(parents=True, exist_ok=True)
  │     → For each ParsedFile:
  │           module.md.j2.render(...) → modules/<name>.md
  │     → project_index.md.j2.render(...) → project_index.md
  │
  └── write .rag_kb/modules/*.md + project_index.md
```

---

## Adding a New Language

### Step 1 — Add the extension mapping

In `repo_parser/parser/registry.py`:

```python
# In EXTENSION_TO_LANGUAGE
".zig": "zig",

# In LANGUAGE_ALIASES (optional, for --languages flag)
"zig": "zig",
```

### Step 2 — Verify the grammar is available

```python
from tree_sitter_language_pack import has_language
has_language("zig")   # must return True
```

If the grammar isn't in `tree-sitter-language-pack`, the engine will log a warning and skip the file — no crash.

### Step 3 — Write an extractor

Create `repo_parser/parser/queries/zig_queries.py`:

```python
"""Tree-sitter extraction for Zig."""
from __future__ import annotations

from repo_parser.models import FunctionInfo, ParsedFile
from repo_parser.parser.queries.base import iter_nodes, line_end, line_number, node_kind, node_text


def parse_zig(filepath: str, source: str, parser) -> ParsedFile:
    tree = parser.parse(source)
    root = tree.root_node()

    parsed = ParsedFile(filepath=filepath, language="zig")

    for node in iter_nodes(root, "function_declaration"):
        name_node = node.child_by_field_name("name")
        if name_node:
            name = node_text(source, name_node)
            parsed.functions.append(
                FunctionInfo(
                    name=name,
                    signature=f"fn {name}(...)",
                    line_start=line_number(node),
                    line_end=line_end(node),
                )
            )

    return parsed
```

**Tips:**
- Use `iter_nodes(root, "node_kind_here")` to find all nodes of a type. To discover node kinds, parse a sample file and print `node.kind()` for all nodes.
- Call `node.child_by_field_name("name")` for named AST fields (check the tree-sitter grammar's node-types.json for field names).
- Always return a `ParsedFile` even if extraction is minimal — never return `None`.

### Step 4 — Register the extractor in the engine

In `repo_parser/parser/engine.py`, add to `PARSERS`:

```python
from repo_parser.parser.queries.zig_queries import parse_zig

PARSERS["zig"] = lambda fp, src, parser: parse_zig(fp, src, parser)
```

Alternatively, if the language follows the common profile pattern, add a `LanguageProfile` in `common_queries.py` and the engine loop will pick it up automatically.

### Step 5 — Add a fixture and tests

1. Add a minimal fixture file: `tests/fixtures/main.zig` with at least one function and import.
2. Add tests in `tests/unit/test_engine.py`:

```python
def test_parse_zig():
    engine = ParserEngine()
    source = 'const std = @import("std");\npub fn main() void {}\n'
    result = engine.parse_file("main.zig", source)
    assert result is not None
    assert result.language == "zig"
    assert any(f.name == "main" for f in result.functions)
```

---

## Adding a New Endpoint Pattern

All endpoint patterns live in `repo_parser/parser/extractors/endpoints.py`.

### Adding a new connection URI scheme

Add the scheme to `CONN_URI_PATTERN`:

```python
CONN_URI_PATTERN = re.compile(
    r"(?:postgres(?:ql)?|mysql|…|neo4j)"   # add "neo4j" here
    r"(?:\+[a-z0-9]+)?://[^\s'\"]+",
    re.IGNORECASE,
)
```

Also add a prefix check in `_infer_conn_type`:

```python
for prefix in ("postgresql", "postgres", "mysql", …, "neo4j"):
    if prefix in lower:
        return prefix
```

### Adding a new ENV variable

Add to `ENV_CONNECTION_VARS`:

```python
ENV_CONNECTION_VARS = {
    …
    "NEO4J_URI",
    "NEO4J_USERNAME",
}
```

### Write security tests

Any new pattern **must** have a corresponding test in `tests/unit/test_endpoints_security.py` covering:
- Detection (endpoint is found)
- Redaction (password is not in `raw_redacted` if credentials are embedded)
- A false-positive check if the pattern is ambiguous

---

## Testing Guide

### Test structure

```
tests/
├── conftest.py              # shared fixtures (if any)
├── unit/
│   ├── test_scanner.py
│   ├── test_registry.py
│   ├── test_engine.py
│   ├── test_endpoints.py
│   ├── test_endpoints_security.py
│   ├── test_markdown.py
│   ├── test_bundle.py
│   ├── test_detector.py
│   ├── test_logging_config.py
│   ├── test_query_api_spike.py
│   ├── test_dependencies.py      ← import-resolution helpers (all languages)
│   ├── test_base_queries.py      ← tree-sitter node helpers
│   ├── test_sql_queries.py       ← SQL/PL/SQL extraction
│   ├── test_java_queries.py      ← Java fallback + tree-sitter parsers
│   └── test_cache_extended.py    ← IndexCache lifecycle & serialisation
└── integration/
    ├── test_cli.py
    ├── test_init_bundle.py
    └── test_incremental_indexing.py
```

### Key patterns

**Parametrised table tests** — use for anything with multiple input/output pairs:

```python
@pytest.mark.parametrize("ext,expected", [
    (".py", "python"),
    (".go", "go"),
    (".unknown", None),
])
def test_detect_language(ext, expected):
    assert detect_language(f"file{ext}") == expected
```

**Temp directories** — always use `tmp_path` (pytest built-in) for file operations:

```python
def test_something(tmp_path):
    (tmp_path / "myfile.py").write_text("x = 1")
    result = engine.parse_file("myfile.py", "x = 1")
    assert result is not None
```

**Snapshot tests** — use syrupy for generated Markdown where hand-writing expected strings is impractical:

```python
def test_module_file_matches_snapshot(tmp_path, snapshot):
    # ... generate output ...
    content = (tmp_path / ".rag_kb" / "modules" / "config_sample_py.md").read_text()
    assert content == snapshot
```

To update snapshots: `pytest --snapshot-update`

**Property tests** — use hypothesis for security-sensitive code:

```python
from hypothesis import given, settings
from hypothesis import strategies as st

@given(password=st.text(min_size=8, max_size=40))
@settings(max_examples=200)
def test_password_never_leaks(password):
    uri = f"postgres://user:{password}@host:5432/db"
    assert password not in redact_secrets(uri)
```

### Running tests

```bash
pytest                                    # all tests
pytest tests/unit/ -v                     # unit tests with verbose output
pytest tests/unit/test_engine.py -v       # single module
pytest -k "test_parse_python"             # by name pattern
pytest --cov=repo_parser --cov-report=html  # HTML coverage report
pytest --snapshot-update                  # regenerate snapshots
```

---

## Contribution Workflow

1. **Fork** the repo and create a feature branch: `git checkout -b feat/my-feature`
2. **Make your changes** — see the module reference above for where things live
3. **Write or update tests** — every new code path needs a test
4. **Run the full suite:** `pytest --cov=repo_parser --cov-fail-under=91`
5. **Run against a real repo:** `repo-parser init /some/project --verbose`
6. **Commit with a clear message** following the convention:
   - `feat(module): short description` for new features
   - `fix(module): short description` for bug fixes
   - `docs: short description` for documentation
   - `test: short description` for test-only changes
   - `chore: short description` for maintenance
7. **Open a pull request** targeting `main` with a description covering:
   - What the change does and why
   - Which languages/patterns are affected
   - Test coverage added

### Branch naming

`feat/<thing>`, `fix/<thing>`, `docs/<thing>`, `chore/<thing>`

---

## Code Style

- **Python 3.10+ syntax** — use `X | Y` union types, `match`/`case` where appropriate
- **`from __future__ import annotations`** — required in all source files
- **Type hints everywhere** — return types, parameter types, local variables where non-obvious
- **Dataclasses** for all data structures — no ad-hoc dicts passed between modules
- **Logging over print** — `logger = logging.getLogger(__name__)` in every module; no `print()` outside `ui/`
- **`pathlib.Path`** everywhere — no raw string path manipulation
- **Guard exceptions** — catch at the engine level; never let a single file failure propagate up

---

## Known Limitations & Future Work

| Area | Limitation | Possible fix |
|------|-----------|-------------|
| Endpoint extraction | Multi-line string concatenation not detected | Dataflow analysis (out of scope for regex) |
| Endpoint extraction | F-string interpolation not redacted | AST-level credential tracing |
| Endpoint extraction | Commented-out lines captured (intentional, but noisy) | Add `--no-comments` flag |
| Caching | `project_index.md` is always regenerated | Could be skipped if no file hashes changed |
| Tree-sitter | Manual traversal — see evaluation doc | Precompiled queries once API is stable across all grammars |
| Dependency inference | Only token-level matching (no import resolution) | Integration with language-specific package resolvers |
| Binary detection | Reads first 8 KB for null-byte heuristic | Could use `libmagic` for accuracy |
| Kubernetes detection | Only checks for `apiVersion` key | Could check `kind:` field for more accurate classification |
