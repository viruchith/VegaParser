# Changelog

All notable changes to VegaParser are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.2.0] — 2026-07-16

### Added

- **Automated test suite** — 155 tests, 89% coverage on `repo_parser/`
  - `tests/unit/` — full unit coverage for scanner, registry, engine, endpoints,
    markdown generator, bundle, and stack detector
  - `tests/integration/` — end-to-end `init` + `bundle` tests with syrupy snapshots
  - 10 new language fixtures: JavaScript, TypeScript, Java, Rust, Ruby, Bash, C#, SQL,
    docker-compose, INI
  - `requirements-dev.txt` (pytest ≥8, pytest-cov ≥5, syrupy ≥4, hypothesis ≥6.100)

- **CI workflow** (`.github/workflows/ci.yml`) — matrix over Python 3.10, 3.11, 3.12;
  fails on coverage below 80%

- **Incremental indexing / caching**
  - `repo_parser/cache.py` — SHA-256 manifest at `.rag_kb/.cache/manifest.json`;
    unchanged files are skipped entirely (zero `parse_file` calls on a clean re-run)
  - Stale module files are deleted when source files are removed from the repo
  - `init --force` / `--no-cache` flag to bypass the cache and force a full reparse

- **Endpoint extraction hardening**
  - Table-driven security test matrix covering all URI schemes, ENV vars, KV config,
    dict literals, `create_engine()` strings, and false-positive cases
  - Hypothesis property test: 200 random credential inputs confirmed to never leak
  - Known limitations documented in `DESIGN.md` under "Endpoint Extraction → Known Limitations"

- **Tree-sitter Query API evaluation** (`docs/tree-sitter-query-api-evaluation.md`)
  - Spike implementation: `repo_parser/parser/queries/python_queries_query_api.py`
  - Benchmark: manual traversal ≈ 0.43 ms · per-call Query API ≈ 7.4 ms ·
    precompiled query ≈ 0.09 ms (on `tests/fixtures/config_sample.py`, 100 iterations)
  - **Recommendation:** keep manual traversal; precompiled queries are a viable
    future optimisation path once the API is confirmed stable across all grammars

### Fixed

- **tree-sitter 0.26 compatibility** — added `repo_parser/parser/ts_compat.py` to
  handle the `Parser.parse()` API change (now requires a language argument); without
  this fix all parsing silently produced empty results on tree-sitter ≥ 0.26

- **`redact_secrets` empty-username URIs** — `redis://:password@host` was not
  redacted because the username group used `+` (one-or-more) instead of `*`
  (zero-or-more). Credentials are now correctly replaced with `***`

### Changed

- README installation URL corrected: `github.com/your-org/vegaparser.git` →
  `github.com/viruchith/VegaParser.git`
- `pyproject.toml` — added `authors` field; bumped version to `0.2.0`
- `DESIGN.md` — State Management section updated to document incremental caching
  behaviour (replaces "No database or cache in v1")
- `init` command table in README updated with `--force`/`--no-cache` flag

---

## [0.1.0] — 2026-07-15

### Added

- Initial release — Tree-sitter AST parsing across 20+ languages
- `init` command: scans repo, writes `.rag_kb/modules/*.md` + `project_index.md`
- `bundle` command: concatenates knowledge base into `full_repo_context.md`
- Endpoint extraction: URLs, connection URIs, ENV vars, KV config, dict literals
- Jinja2-based Markdown generation with YAML frontmatter
- Rich progress UI; file logging to `repo-parser.log`
- `.gitignore`-aware traversal; binary file detection
