# Changelog

All notable changes to VegaParser are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Fixed

#### Parser stability on large repositories
- Eliminated tree-sitter segmentation faults during repository indexing by:
  - keeping parse-tree lifetimes valid across full AST traversal
  - removing unstable node-position access paths that could dereference invalid native state
  - switching line-number derivation to safe byte-offset mapping in shared query helpers
- Unified parser/query compatibility helpers so mixed tree-sitter API shapes no longer cause
  crashes or empty-module runs.

#### Benchmark reliability
- Restored benchmark correctness for light-tier runs after parser stability fixes:
  - representative targets now complete with non-zero module counts (for example:
    `python-light=37`, `php-light=217`, `java-light=85`)
  - benchmark runs no longer report pervasive false `modules=0` due to parser crashes.
- Fixed `OSError [Errno 63] File name too long` crash on repos with deeply-nested paths (e.g.
  JetBrains/kotlin): `sanitize_filename` now truncates long stems to ≤180 bytes and appends a
  12-hex-char SHA-1 suffix to preserve uniqueness.
- Fixed `modules=0` for `c-heavy` and `cpp-heavy` targets: removed `--filter=blob:none` from
  the git clone command — the flag created blobless clones with empty working trees; replaced the
  unreachable targets (`torvalds/linux`, `llvm/llvm-project`) with practical alternatives
  (`curl/curl` for C, `grpc/grpc` for C++).
- Fixed `modules=0` for all `plsql` targets: PL/SQL-specific extensions (`.plsql`, `.pls`,
  `.pkb`, `.pks`) now return `"plsql"` from `detect_language` instead of `"sql"`, so
  `--languages plsql` correctly filters PL/SQL source files. The `plsql-heavy` and `plsql-light`
  benchmark targets now also scan `.sql` files (`("plsql", "sql")` language tuple) because most
  PL/SQL repos use the `.sql` extension.

### Changed

#### Benchmark guide
- Expanded `README.md` benchmark documentation with:
  - prerequisites and quick-start commands
  - a dedicated "Example runs" section (full suite, heavy-only, Java A/B, multi-language, custom workspace)
  - option reference table and output-column interpretation
  - recommended workflow for repeatable performance comparisons

#### Benchmark runner UX
- Updated `scripts/benchmark_vegaparser.py` to print live progress by default:
  - run banner with selected target count and options
  - per-target progress (`[i/N]`) and completion summaries
  - final summary table
- Added `--verbose` for detailed step logs (clone/cache actions, per-run timings, and failure stderr tail).

---

## [0.2.0] — 2026-07-16

### Added

#### Test suite & CI
- `requirements-dev.txt` — `pytest ≥8`, `pytest-cov ≥5`, `syrupy ≥4`, `hypothesis ≥6.100`
  (syrupy chosen over pytest-snapshot for better pytest integration and active maintenance)
- `tests/unit/` — full unit coverage for every source module:
  - `test_scanner.py` — gitignore enforcement, skip-dir rules, binary detection, language filter, special filenames
  - `test_registry.py` — all extension/filename mappings, language aliases, `extensions_for_languages`
  - `test_engine.py` — parse_file for each language family, graceful failure on malformed input
  - `test_endpoints.py` — URL extraction, DB URI detection, env-var handling, localhost skip
  - `test_endpoints_security.py` — full security matrix (see below)
  - `test_markdown.py` — `sanitize_filename`, module file generation, YAML frontmatter validity
  - `test_bundle.py` — `BundleError` on missing/empty `.rag_kb/`, ordering, delimiter presence, stats
  - `test_detector.py` — `detect_stack` for requirements.txt, package.json, go.mod, Cargo.toml
- `tests/integration/test_init_bundle.py` — end-to-end `init` + `bundle` with syrupy snapshots,
  `--languages` filter, idempotent double-run assertion
- `tests/integration/test_incremental_indexing.py` — zero-reparse on unchanged repo,
  single-file re-parse on change, new file detection, deleted file cleanup, `--force` full reparse
- 10 new language fixture files: `app.js`, `app.ts`, `App.java`, `lib.rs`, `app.rb`,
  `script.sh`, `app.cs`, `schema.sql`, `docker-compose.yml`, `config.ini`
- `.github/workflows/ci.yml` — matrix build on Python 3.10, 3.11, 3.12;
  `pytest --cov=repo_parser --cov-fail-under=80`; fails on any test failure or coverage < 80 %

#### Incremental indexing / caching
- `repo_parser/cache.py` — `IndexCache` class; SHA-256 manifest at `.rag_kb/.cache/manifest.json`
  - Serialises/deserialises `ParsedFile` dataclass via `dataclasses.asdict` + custom reconstruction
  - `is_cached(rel_path, hash, module_file)` — returns `True` only when hash matches **and** module `.md` exists
  - `get_cached_parsed_file()` — restores full `ParsedFile` from manifest; falls back to re-parse on corruption
  - `known_paths()` — used to detect and purge deleted source files
  - `save()` / `load()` — atomic JSON read/write
- `--force` / `--no-cache` flag added to `init` command
- Stale module `.md` files are deleted when their source files are removed from the repo
- `cli.py` updated: cache loaded before parse loop, `fresh_count` / `cached_count` reported in console output

#### Endpoint extraction hardening
- `tests/unit/test_endpoints_security.py` — table-driven parametrised matrix covering:
  - All 5 connection URI schemes with and without embedded credentials
  - 13 well-known ENV connection variable names (redaction + no-leak assertion)
  - Config KV patterns including multi-KV on one line and commented-out lines
  - Python/JSON dict-literal patterns
  - `create_engine()` / `createConnection()` driver strings
  - False-positive checks: `host_count`, `port_number`, `"connecting to host"` in comments
  - Multi-line/f-string limitation documented as known behaviour
- Hypothesis property test: 200 random `(host, user, password, port)` combinations — asserts
  the generated password never appears verbatim in `redact_secrets()` output

#### Tree-sitter Query API evaluation
- `repo_parser/parser/queries/python_queries_query_api.py` — isolated spike (not imported by production code)
- `docs/tree-sitter-query-api-evaluation.md` — benchmark results, compatibility analysis, recommendation

### Fixed

- **tree-sitter 0.26 API breaking change** — `Parser.parse()` now requires a `language` argument.
  Added `repo_parser/parser/ts_compat.py` compatibility adapter; without this, all parsing
  silently returned empty `ParsedFile` objects on tree-sitter ≥ 0.26

- **`redact_secrets` empty-username URIs** (`redis://:password@host` not redacted) —
  the username capturing group used `+` (one-or-more) which requires at least one character
  before the colon separator. Changed to `*` (zero-or-more); credentials are now correctly
  replaced with `***` in all URI forms

### Changed

- `README.md` — full rewrite with ToC, detailed command reference, output structure
  examples, incremental caching explanation, and endpoint extraction table;
  clone URL corrected from `github.com/your-org/vegaparser.git` → `github.com/viruchith/VegaParser.git`
- `pyproject.toml` — added `authors = [{ name = "Viruchith" }]`; bumped `version` to `0.2.0`
- `DESIGN.md` — State Management section updated: replaces "No database or cache in v1" with
  documentation of the incremental manifest; added "Known Limitations" subsection under
  "Endpoint Extraction"
- `CHANGELOG.md` — this file (created)

---

## [0.1.0] — 2026-07-15

### Added

- Initial release
- Tree-sitter AST parsing via `tree-sitter-language-pack` across 20+ languages
- `init` command: scans repository, writes `.rag_kb/modules/*.md` + `.rag_kb/project_index.md`
- `bundle` command: concatenates knowledge base into `.rag_kb/full_repo_context.md`
- Regex-based endpoint extraction: HTTP URLs, connection URIs, ENV vars, KV config, dict literals
- `redact_secrets()` replaces passwords in connection strings with `***`
- Jinja2-based Markdown generation with YAML frontmatter (`module.md.j2`, `project_index.md.j2`)
- `RepositoryScanner`: `.gitignore`-aware traversal via `pathspec`, binary file detection
- `ParserEngine.infer_internal_dependencies()`: links import statements to internal modules
- `detect_stack()`: reads `requirements.txt`, `package.json`, `go.mod`, `Cargo.toml`
- Rich progress UI (spinner + bar); all logs written to `repo-parser.log`
- `--verbose` / `-v` flag for DEBUG-level file logging
- `--languages` / `-l` flag for per-language filtering (with `kubernetes` → YAML alias)
- `pyproject.toml` with `repo-parser` console script entry point
- 7 initial fixture files: `.env`, `Dockerfile`, `config_sample.py`, `main.go`, `main.tf`,
  `schema.plsql`, `k8s/deployment.yaml`
