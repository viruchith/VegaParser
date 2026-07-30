# VegaParser

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/viruchith/VegaParser/actions/workflows/ci.yml/badge.svg)](https://github.com/viruchith/VegaParser/actions/workflows/ci.yml)
[![Coverage: 91%](https://img.shields.io/badge/coverage-91%25-brightgreen.svg)](#testing)

**VegaParser** is a command-line tool that turns any local code repository into a structured **Markdown knowledge base** optimised for Large Language Models. It walks your source tree, parses every file with **Tree-sitter AST analysis**, enriches results with regex-based endpoint and secret extraction, and writes clean, YAML-frontmatted Markdown that can be injected directly into an LLM's context window.

> **v0.2.0** — incremental caching, hardened secret redaction, 342-test suite (91 % coverage).
> See [CHANGELOG.md](CHANGELOG.md) for the full release history.

---

## Table of Contents

1. [Features](#features)
2. [How It Works](#how-it-works)
3. [Supported Languages](#supported-languages)
4. [Installation](#installation)
5. [Quick Start](#quick-start)
6. [Command Reference](#command-reference)
   - [init](#init--build-the-knowledge-base)
   - [bundle](#bundle--create-an-llm-context-file)
7. [Output Structure](#output-structure)
8. [Incremental Caching](#incremental-caching)
9. [Endpoint & Secret Extraction](#endpoint--secret-extraction)
10. [Logging](#logging)
11. [Project Layout](#project-layout)
12. [Benchmarking](#benchmarking)
13. [Testing](#testing)
14. [Contributing](#contributing)
15. [License](#license)

---

## Features

| Feature | Details |
|---------|---------|
| **Tree-sitter AST parsing** | Semantic extraction — imports, classes, methods, docstrings, signatures — across 20+ languages |
| **RAG-optimised Markdown** | Per-module `.md` files with YAML frontmatter; ready for vector store ingestion or direct injection |
| **Project-wide index** | `project_index.md` aggregates tech stack, dependency graph, all external URLs, and DB endpoints |
| **LLM context bundle** | `bundle` command concatenates everything into a single `full_repo_context.md` with size/token estimates |
| **Endpoint discovery** | Detects HTTP URLs, connection URIs, `.env` variables, JDBC strings, `create_engine()` calls, KV config |
| **Secret redaction** | Passwords in connection strings are replaced with `***` before writing to any output file |
| **Incremental caching** | SHA-256 manifest skips unchanged files on re-runs; stale module files are automatically removed |
| **Infrastructure-aware** | Dockerfile, Kubernetes manifests, Terraform/HCL, PL/SQL heuristics |
| **Smart traversal** | Respects `.gitignore`, skips hidden dirs, `node_modules`, binaries, and the `.rag_kb/` output dir itself |
| **Rich progress UI** | Spinner + progress bar; all logs written to `repo-parser.log`, never to stdout |

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────────┐
│  Repository root                                                    │
│                                                                     │
│  RepositoryScanner                                                  │
│    • loads .gitignore via pathspec                                  │
│    • skips hidden dirs, binaries, .rag_kb/                          │
│    • filters by --languages if supplied                             │
│         │                                                           │
│         ▼  list[Path]                                               │
│  ParserEngine.parse_file(path, content)                             │
│    • detects language via registry                                  │
│    • dispatches to language-specific extractor                      │
│    • runs enrich_parsed_file() — URL & DB regex scan                │
│    • returns ParsedFile dataclass                                   │
│         │                                                           │
│         ▼  list[ParsedFile]                                         │
│  IndexCache (incremental)                                           │
│    • SHA-256 hash per file → .rag_kb/.cache/manifest.json           │
│    • unchanged files: restore ParsedFile from cache, skip parse     │
│         │                                                           │
│         ▼                                                           │
│  ParserEngine.infer_internal_dependencies()                         │
│    • links imports to internal module paths                         │
│         │                                                           │
│         ▼                                                           │
│  MarkdownGenerator.generate()                                       │
│    • Jinja2: module.md.j2  → .rag_kb/modules/<name>.md             │
│    • Jinja2: project_index.md.j2 → .rag_kb/project_index.md        │
│         │                                                           │
│         ▼  (optional)                                               │
│  bundle_knowledge_base()                                            │
│    • project_index.md first, then modules/ alphabetically           │
│    • → .rag_kb/full_repo_context.md                                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Supported Languages

| Category | Languages | Recognised files |
|----------|-----------|-----------------|
| **General purpose** | Python, JavaScript, TypeScript, Go, Rust, Java, Kotlin, Scala, C#, Ruby, PHP, Swift, C, C++ | `*.py` `*.js` `*.ts` `*.go` `*.rs` `*.java` `*.kt` `*.scala` `*.cs` `*.rb` `*.php` `*.swift` `*.c` `*.cpp` `*.h` `*.hpp` |
| **Infrastructure** | Dockerfile, Kubernetes YAML, Terraform, HCL | `Dockerfile` `Containerfile` `*.yaml` `*.yml` `*.tf` `*.tfvars` `*.hcl` |
| **Database** | SQL, PL/SQL | `*.sql` `*.plsql` `*.pls` `*.pkb` `*.pks` |
| **Config / Env** | Environment files, Properties, INI | `.env` `.env.*` `*.properties` `*.ini` `*.cfg` |
| **Shell** | Bash, Makefile | `*.sh` `*.bash` `*.zsh` `Makefile` |

Language aliases accepted by `--languages`: `py`, `js`, `ts`, `golang`, `rs`, `k8s`, `kubernetes`, `docker`, `dockerfile`, `tf`, `terraform`, `shell`, `env`, `plsql`, …

---

## Installation

### From source (recommended)

```bash
git clone https://github.com/viruchith/VegaParser.git
cd VegaParser

# Create and activate a virtual environment
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Install runtime dependencies
pip install -r requirements.txt

# Install as an editable package (gives you the `repo-parser` console script)
pip install -e .
```

### Verify the installation

```bash
repo-parser --help
repo-parser init --help
```

**Requirements:** Python 3.10 or newer. No system-level packages needed — Tree-sitter grammars are bundled via `tree-sitter-language-pack`.

---

## Quick Start

```bash
# 1. Index any repository
repo-parser init /path/to/my-project

# 2. Bundle the knowledge base into a single LLM context file
repo-parser bundle /path/to/my-project

# 3. Feed it to your LLM
cat /path/to/my-project/.rag_kb/full_repo_context.md | pbcopy   # macOS clipboard
```

### Self-index VegaParser

```bash
cd VegaParser
repo-parser init --verbose          # parse the repo itself
repo-parser bundle                  # merge into full_repo_context.md
cat .rag_kb/project_index.md        # inspect the global index
```

---

## Command Reference

### `init` — Build the knowledge base

```
repo-parser init [PATH] [OPTIONS]
```

Scans `PATH` (default: current directory), parses every supported source file, and writes the knowledge base to `<PATH>/.rag_kb/`.

| Argument / Option | Short | Type | Default | Description |
|-------------------|-------|------|---------|-------------|
| `PATH` | — | directory | `.` | Repository root to index |
| `--languages` | `-l` | string | all | Comma-separated language filter, e.g. `python,go,kubernetes` |
| `--verbose` | `-v` | flag | off | Write DEBUG-level details to `repo-parser.log` |
| `--log-target` | — | `file\\|console\\|both` | `file` | Choose where logs are written |
| `--force` / `--no-cache` | — | flag | off | Bypass the incremental cache; reparse every file from scratch |

**Examples:**

```bash
# Index everything
repo-parser init

# Index a specific path, Python and Kubernetes only
repo-parser init ~/projects/api-server -l python,kubernetes

# Force a full re-index, verbose logging
repo-parser init . --force --verbose

# Index with shorthand aliases
repo-parser init . -l py,go,docker,tf
```

**Output on success:**

```
Generated knowledge base with 42 modules (38 parsed, 4 from cache).
Project index: /your/project/.rag_kb/project_index.md
Log file: /your/project/repo-parser.log
```

---

### `bundle` — Create an LLM context file

```
repo-parser bundle [PATH] [OPTIONS]
```

Concatenates all files in `.rag_kb/` into a single `full_repo_context.md` ordered for LLM attention: `project_index.md` first, then module files alphabetically.

| Argument / Option | Short | Type | Default | Description |
|-------------------|-------|------|---------|-------------|
| `PATH` | — | directory | `.` | Repository root (must contain `.rag_kb/`) |
| `--output` | `-o` | string | `full_repo_context.md` | Output filename inside `.rag_kb/` |
| `--verbose` | `-v` | flag | off | Write DEBUG-level details to `repo-parser.log` |
| `--log-target` | — | `file\\|console\\|both` | `file` | Choose where logs are written |

**Examples:**

```bash
# Default bundle
repo-parser bundle

# Custom output name
repo-parser bundle -o context_for_gpt.md

# Bundle a different project
repo-parser bundle ~/projects/api-server
```

**Output on success:**

```
Bundle created successfully.
  Output:      /your/project/.rag_kb/full_repo_context.md
  Size:        284.3 KB (291,145 bytes)
  Characters:  291,059
  Words:       42,318
  ~Tokens:     72,764 (rough estimate, ~4 chars/token)
  Log file:    /your/project/repo-parser.log

Warning: This file is intended for LLMs with large context windows.
```

> **Tip:** Run `init` before `bundle`. `bundle` raises an error if `.rag_kb/` does not exist.

---

## Output Structure

After `init`, the following tree is created inside your repository:

```
.rag_kb/
├── project_index.md          ← global project map (tech stack, URLs, DB endpoints)
├── modules/
│   ├── src_app_py.md          ← one file per source module
│   ├── src_db_models_py.md
│   ├── Dockerfile.md
│   └── …
├── .cache/
│   └── manifest.json         ← incremental cache (SHA-256 hashes + serialised ParsedFile)
└── full_repo_context.md      ← created by `bundle`
```

### Module file anatomy (`modules/*.md`)

Each module file has a **YAML frontmatter block** followed by Markdown sections:

```yaml
---
filepath: src/app.py
language: python
imports:
  - "import os"
  - "from flask import Flask"
exports:
  - "create_app"
  - "AppConfig"
external_urls:
  - url: "https://api.stripe.com/v1"
    line: 14
database_endpoints:
  - type: "postgres"
    host: "db.internal"
    port: "5432"
    user: "app_user"
    schema: "public"
    database: "myapp"
    line: 22
---

# File: `src/app.py`

## Overview
Flask application factory.

## Imports
- `import os`
- `from flask import Flask`

## Classes
### `AppConfig`
…

## Functions
### `create_app`
```def create_app(env: str = "production") -> Flask```
…

## Database & Connection Endpoints
| Type     | Host        | Port | User     | Database |
|----------|-------------|------|----------|----------|
| postgres | db.internal | 5432 | app_user | myapp    |
```

### Project index (`project_index.md`)

Aggregates across all parsed files:
- **Tech stack** — Python packages, Node.js deps, Go modules, Rust crates
- **Files by language** — counts per language
- **Module dependency graph** — detected `import` links between internal modules
- **External URLs (project-wide)** — deduplicated, with source file and line
- **Database endpoints (project-wide)** — all connection details, source-referenced
- **File index table** — filepath, language, class count, function count, link to module doc

---

## Incremental Caching

By default, `init` maintains a SHA-256 content-hash manifest at `.rag_kb/.cache/manifest.json`. On subsequent runs:

| File state | Action |
|-----------|--------|
| **Unchanged** (hash matches, module `.md` exists) | Restore `ParsedFile` from cache — Tree-sitter is not invoked |
| **Changed** (hash mismatch) | Re-parse; update manifest entry |
| **New** | Parse normally; add manifest entry |
| **Deleted** (in manifest but not on disk) | Remove module `.md`; remove from manifest |

The cache is transparent — output is byte-identical to a full parse. Use `--force`/`--no-cache` to bypass it:

```bash
repo-parser init --force          # full reparse, then update cache
```

On a warm cache, re-running `init` on an unchanged `tests/fixtures/` produces **zero `parse_file` calls** — only project index regeneration occurs.

---

## Endpoint & Secret Extraction

After Tree-sitter parsing, every file is scanned by `endpoints.py` regardless of language. It extracts:

| Pattern type | Examples detected |
|-------------|------------------|
| HTTP/WS/FTP URLs | `https://api.example.com/v1/charges` |
| DB connection URIs | `postgres://user:***@db:5432/mydb` |
| JDBC strings | `jdbc:postgresql://host:5432/db` |
| `create_engine()` calls | `create_engine("postgresql://user:***@host/db")` |
| Env var assignments | `DATABASE_URL`, `PGHOST`, `REDIS_URL`, `SPRING_DATASOURCE_URL`, … |
| KV config lines | `host = db.internal`, `port: 5432`, `"database": "myapp"` |

**Passwords are always redacted** to `***` in all output. Known limitations (multi-line concatenations, f-string interpolation) are documented in `DESIGN.md → Endpoint Extraction → Known Limitations`.

---

## Logging

VegaParser supports configurable log output targets so debugging can happen either in files, in the terminal, or both.

| Option | Effect |
|--------|--------|
| `--verbose` | Sets log level to `DEBUG` (default is `INFO`) |
| `--log-target file` | Write logs to `repo-parser.log` (default) |
| `--log-target console` | Stream logs to terminal output |
| `--log-target both` | Write to file and terminal simultaneously |

```bash
# File logging (default)
repo-parser init --verbose --log-target file

# Console-only logging
repo-parser init --verbose --log-target console

# File + console logging
repo-parser bundle --verbose --log-target both
```

---

## Project Layout

```
VegaParser/
├── main.py                         ← entry point (delegates to repo_parser.cli)
├── pyproject.toml                  ← package metadata, console_scripts
├── requirements.txt                ← runtime dependencies
├── requirements-dev.txt            ← pytest, pytest-cov, syrupy, hypothesis
├── DESIGN.md                       ← architecture deep-dive
├── CONTRIBUTING.md                 ← developer guide and module reference
├── CHANGELOG.md
├── LICENSE                         ← GPLv3
├── docs/
│   └── tree-sitter-query-api-evaluation.md
├── tests/
│   ├── fixtures/                   ← sample corpus (17 files, 11 languages)
│   ├── unit/                       ← one test module per source module (17 modules)
│   └── integration/                ← end-to-end init + bundle + cache tests
└── repo_parser/
    ├── cli.py                      ← Typer app: init, bundle
    ├── models.py                   ← ParsedFile, ClassInfo, FunctionInfo, …
    ├── cache.py                    ← IndexCache (incremental manifest)
    ├── traversal/
    │   └── scanner.py              ← RepositoryScanner
    ├── parser/
    │   ├── engine.py               ← ParserEngine
    │   ├── registry.py             ← extension → language mapping
    │   ├── extractors/
    │   │   └── endpoints.py        ← URL & DB extraction + secret redaction
    │   └── queries/
    │       ├── base.py             ← node helpers
    │       ├── python_queries.py
    │       ├── javascript_queries.py
    │       ├── common_queries.py   ← Go, Rust, Java, Ruby, C/C++, C#, …
    │       ├── docker_queries.py
    │       ├── yaml_queries.py
    │       ├── sql_queries.py
    │       ├── hcl_queries.py
    │       ├── shell_queries.py
    │       └── env_queries.py
    ├── generator/
    │   ├── markdown.py             ← MarkdownGenerator
    │   ├── bundle.py               ← bundle_knowledge_base
    │   └── templates/
    │       ├── module.md.j2
    │       └── project_index.md.j2
    ├── stack/
    │   └── detector.py             ← detect_stack
    └── ui/
        ├── console.py
        ├── progress.py
        └── logging_config.py
```

---

## Benchmarking

Use the benchmark runner to measure VegaParser across curated heavy/light open-source repositories per language group.

### Prerequisites

- Python 3.10+
- `git` available on PATH
- Enough disk/network capacity for cloned benchmark repos (heavy targets can be very large)
- On Windows, Git long paths should be enabled; if you still hit checkout limits, point
  `--workspace` at a short path such as `C:\vegaparser-bench`.

### Quick commands

```bash
# Show all benchmark targets (id, tier, languages, repo)
python scripts/benchmark_vegaparser.py --list

# Run every heavy target once (cold run)
python scripts/benchmark_vegaparser.py --tier heavy

# Run only Java targets with a warm-cache pass
python scripts/benchmark_vegaparser.py --language java --warm

# Show detailed live logs for each clone/run step
python scripts/benchmark_vegaparser.py --tier light --verbose

# Pin exact targets and average 3 cold runs
python scripts/benchmark_vegaparser.py \
  --repo java-heavy --repo java-light \
  --repeat 3

# Save machine-readable output
python scripts/benchmark_vegaparser.py --json benchmark-results.json

# Run all light targets using 4 parallel workers
python scripts/benchmark_vegaparser.py --tier light --workers 4
```

### Example runs

```bash
# 1) Full smoke run (all targets, one cold run each)
python scripts/benchmark_vegaparser.py

# 2) Heavy repos only, force fresh clone to avoid stale state
python scripts/benchmark_vegaparser.py --tier heavy --refresh

# 3) Java comparison with averaging + warm-cache pass
python scripts/benchmark_vegaparser.py \
  --repo java-heavy --repo java-light \
  --repeat 5 \
  --warm \
  --json results/java-benchmark.json

# 4) Language-focused run across multiple families
python scripts/benchmark_vegaparser.py \
  --language python \
  --language javascript \
  --repeat 3 \
  --json results/py-js.json

# 5) Run in a custom workspace directory
python scripts/benchmark_vegaparser.py \
  --workspace /tmp/vegaparser-bench \
  --tier light \
  --json /tmp/vegaparser-bench/light.json

# 6) Run all light targets in parallel with 5 workers and save results
python scripts/benchmark_vegaparser.py \
  --tier light \
  --workers 5 \
  --json results/light-parallel.json

# 7) Interrupt-safe full suite run (Ctrl+C writes partial results)
python scripts/benchmark_vegaparser.py \
  --workers 4 \
  --json results/full-run.json
```

### Common options

| Option | Purpose |
|---|---|
| `--tier {heavy,light,all}` | Filter target size profile |
| `--language <lang>` (repeatable) | Keep targets containing one of the given languages |
| `--repo <id>` (repeatable) | Run exact benchmark ids from `--list` |
| `--repeat <n>` | Average `n` cold runs per target |
| `--warm` | Add one warm-cache run after cold run(s) |
| `--refresh` | Re-clone repositories before benchmarking |
| `--workspace <path>` | Custom clone/cache directory (default: `~/.cache/vegaparser-benchmarks`) |
| `--json <file>` | Export full results as JSON |
| `--verbose` | Print detailed step-by-step progress logs during clone and runs |
| `--workers <n>` | Run up to `n` targets in parallel (default: `1`) |
| `--max-heavy-workers <n>` | Cap concurrent `heavy` targets when running in parallel (default: `2`) |

### Parallel execution

Use `--workers N` to run multiple benchmark targets concurrently (one thread per target, each
spawning its own VegaParser subprocess):

```bash
# Run light-tier targets 5 at a time
python scripts/benchmark_vegaparser.py --tier light --workers 5

# Run heavy targets 2 at a time with verbose per-target logs
python scripts/benchmark_vegaparser.py --tier heavy --workers 2 --verbose

# Use 6 total workers but cap heavy repos to 2 concurrent parsers
python scripts/benchmark_vegaparser.py --workers 6 --max-heavy-workers 2 --verbose
```

Each worker prefixes its log lines with `[target-id]` so interleaved output is always
attributable. The final summary table is always printed in original suite order regardless
of completion order.

Targets that resolve to the same clone path (for example `go-heavy` and `yaml-heavy`, both
using `kubernetes/kubernetes`) are automatically serialized with a repository lock so they
cannot delete each other's `.rag_kb` state during parallel runs.

**Graceful Ctrl+C:** pressing Ctrl+C at any point sets a shared shutdown signal:
- In-flight workers finish their current run and exit with `status=interrupted`.
- Pending tasks that have not yet started are recorded as `status=cancelled`.
- A partial summary table is printed and `--json` output is written before exit.
- Exit code is `1` on interrupt, `0` on clean completion.

### Cold vs warm runs

VegaParser has a file-level incremental cache (`parse_cache.json` inside `.rag_kb/`).
Each cache entry stores the file's `mtime_ns` and `size`.
On re-index, if those values match, the tree-sitter parse step is skipped entirely and
the cached `ParsedFile` record is reused — only markdown generation runs.

The benchmark exploits this to measure two distinct performance profiles:

| Mode | What happens | When `.rag_kb` exists? | Measures |
|---|---|---|---|
| **Cold** | `.rag_kb` deleted before every run | No | Full parse: file I/O + tree-sitter + markdown write |
| **Warm** | `.rag_kb` kept from the cold run | Yes (all cache hits) | Incremental reindex: cache load + markdown write only |

**Enabling a warm pass:**

```bash
# Run cold once, then warm once, for all Java targets
python scripts/benchmark_vegaparser.py --language java --warm --verbose

# Average 3 cold runs and follow with a warm pass, save to JSON
python scripts/benchmark_vegaparser.py \
  --repo kotlin-heavy \
  --repeat 3 \
  --warm \
  --json results/kotlin-cold-vs-warm.json
```

**What to expect:**
On an unchanged repo (the benchmark case), warm times are dramatically lower than cold.
For example, a `kotlin-heavy` cold run of ~50 s typically drops to ~2–5 s warm, because
all 67 000+ modules are cache hits and tree-sitter is never invoked.
The `warm_s` column in the result table and JSON captures this timing.

> **Note:** `warm_s` is `null` / `-` when `--warm` is not passed.

### Output columns

- `cold_avg_s`: average of cold-run timings (`--repeat`)
- `cold_min_s` / `cold_max_s`: spread across cold runs (only meaningful when `--repeat` > 1)
- `warm_s`: warm-cache timing (`--warm`) — `null` when not requested
- `modules`: number of module markdown files generated
- `status`: `ok`, `clone failed: …`, `cold run failed`, `warm run failed`, `interrupted` (Ctrl+C mid-run), `cancelled` (never started after Ctrl+C), or `exit <rc>`
- `artifact_rag_kb`: target-specific snapshot path to generated `.rag_kb` (when present)
- `artifact_log`: target-specific snapshot path to `repo-parser.log` (when present)

### Recommended workflow for reliable comparisons

1. Run once with `--refresh --repeat 1` to establish a clean cold baseline.
2. Run again with `--warm --repeat 3` to compare cold vs warm behavior.
3. Keep the same machine/load conditions (CPU governor, background jobs, network) across runs.
4. Track result JSON files over time to compare branches or releases.

### Notes

- By default, repositories are cloned and reused in `~/.cache/vegaparser-benchmarks/`.
- Each target writes a snapshot to `~/.cache/vegaparser-benchmarks/.benchmark-artifacts/<target-id>/`
  so shared-repo targets keep independent `.rag_kb` outputs for later quality analysis.
- A single target failing clone/parse does **not** abort the whole suite; status is recorded per target.
- For quick local checks, prefer a small subset (`--repo` or `--language`) before full heavy runs.
- Use `--workers` to speed up suites with many light targets; avoid very high worker counts on
  heavy repos since each worker runs a VegaParser subprocess that can be CPU/IO intensive.

---

## Testing

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run the full suite
pytest

# With coverage report
pytest --cov=repo_parser --cov-report=term-missing

# Run a specific module
pytest tests/unit/test_endpoints_security.py -v

# Run integration tests only
pytest tests/integration/ -v
```

Current status: **342 tests · 91 % coverage** on `repo_parser/`.

The CI pipeline (`.github/workflows/ci.yml`) runs on every push and pull request against `main` across Python 3.10, 3.11, and 3.12. It includes three jobs:

- **build** — builds the sdist + wheel and verifies `repo-parser --help` works from the installed wheel
- **test** — runs the full pytest suite with coverage on all three Python versions; fails if coverage drops below 91 %
- **coverage** — generates and uploads an HTML coverage report artifact (Python 3.12 only)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full developer guide — module-by-module technical reference, how to add a new language, how to write tests, and the contribution workflow.

---

## License

Licensed under the **GNU General Public License v3.0 (GPLv3)**. See [LICENSE](LICENSE) for the full text.
Derivative works must also be released under GPLv3.
