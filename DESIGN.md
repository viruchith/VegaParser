# VegaParser — Architecture & Design

## Overview

VegaParser is a standalone CLI that walks a local repository, parses source files with Tree-sitter (`tree-sitter-language-pack`), enriches results with regex-based endpoint extraction, and writes an LLM-optimized Markdown knowledge base under `.rag_kb/` at the project root.

## Module Structure

```
vegaparser/
├── main.py                         # Entry point
├── pyproject.toml                  # pip install + repo-parser console script
├── requirements.txt
├── DESIGN.md
├── TODO.md
├── README.md
├── tests/fixtures/                 # Manual test corpus
└── repo_parser/
    ├── __init__.py
    ├── cli.py                      # Typer CLI: `init` command
    ├── models.py                   # ParsedFile, ClassInfo, FunctionInfo,
    │                               # ExternalCall, ExternalUrl, DatabaseEndpoint
    ├── traversal/
    │   ├── __init__.py
    │   └── scanner.py              # pathspec + gitignore + skip rules
    ├── parser/
    │   ├── __init__.py
    │   ├── engine.py               # Tree-sitter orchestration + enrichment
    │   ├── registry.py             # Extension/filename → language mapping
    │   ├── extractors/
    │   │   ├── __init__.py
    │   │   └── endpoints.py        # URL & DB connection extraction (all files)
    │   └── queries/
    │       ├── __init__.py
    │       ├── base.py             # Node traversal helpers
    │       ├── python_queries.py
    │       ├── javascript_queries.py
    │       ├── common_queries.py   # Profile-driven parsers (Go, Rust, Java, …)
    │       ├── docker_queries.py
    │       ├── yaml_queries.py     # YAML + Kubernetes manifest detection
    │       ├── sql_queries.py      # SQL + PL/SQL heuristics
    │       ├── hcl_queries.py      # Terraform / HCL blocks
    │       ├── shell_queries.py
    │       └── env_queries.py      # .env / .properties / .ini
    ├── generator/
    │   ├── __init__.py
    │   ├── markdown.py             # Jinja2 render + file naming
    │   ├── bundle.py               # Concatenate .rag_kb → full_repo_context.md
    │   └── templates/
    │       ├── module.md.j2
    │       └── project_index.md.j2
    ├── ui/
    │   ├── console.py              # Shared Rich Console
    │   ├── progress.py             # Discovery spinner + parsing progress bar
    │   └── logging_config.py       # File-only logging (repo-parser.log)
    └── stack/
        ├── __init__.py
        └── detector.py             # requirements.txt, package.json, etc.
```

## Data Flow

```
CLI init [PATH]
    → RepositoryScanner.discover()
    → For each file:
        ParserEngine.parse_file(path, content)
            → Language-specific tree-sitter extractor
            → enrich_parsed_file() — URL & DB endpoint scan
    → ParserEngine.infer_internal_dependencies()
    → MarkdownGenerator.generate()
        → .rag_kb/modules/<sanitized_path>.md  (per file)
        → .rag_kb/project_index.md             (aggregated)

CLI bundle [PATH]
    → bundle_knowledge_base()
        → .rag_kb/full_repo_context.md         (concatenated for LLM injection)
```

## State Management

- **Stateless per run.** Each `init` invocation is independent.
- **In-memory aggregation:** `list[ParsedFile]` collected during traversal; passed to generator at end.
- **Output:** `.rag_kb/modules/<sanitized_path>.md` + `.rag_kb/project_index.md`
- **No database or cache** in v1.

## File Traversal Strategy

1. Resolve root path (default: cwd).
2. Load `.gitignore` from root via `pathspec.PathSpec`.
3. Skip hidden/package directories (`.git`, `node_modules`, `venv`, `.rag_kb`, …).
4. Skip binary files (null-byte heuristic + extension blocklist).
5. Only include files where `detect_language()` returns a supported language.
6. Filter by `--languages` flag when provided (including `kubernetes` → `.yaml` files).

### Special filenames

| Filename | Language |
|----------|----------|
| `Dockerfile`, `Containerfile` | dockerfile |
| `docker-compose.yml`, `compose.yaml` | yaml |
| `.env`, `.env.*` | env |

## Extension → Language Registry

See `repo_parser/parser/registry.py` for the full mapping. Highlights:

| Extensions / Files | Language |
|--------------------|----------|
| `.py` | python |
| `.js`, `.ts`, `.jsx`, `.tsx` | javascript / typescript |
| `.go`, `.rs`, `.java`, `.rb`, `.cs`, `.kt`, `.scala`, `.php`, `.swift` | respective grammars |
| `.c`, `.h`, `.cpp`, `.hpp` | c / cpp |
| `.yaml`, `.yml` | yaml (→ kubernetes when `apiVersion` present) |
| `.tf`, `.hcl` | terraform / hcl |
| `.sql`, `.plsql` | sql (→ plsql via heuristics) |
| `.sh`, `.bash` | bash |
| `.env`, `.properties`, `.ini` | env |

## Tree-sitter Parsing Strategy

Parsing uses `tree-sitter-language-pack` with **AST node traversal** (not the Query API) due to binding compatibility across Python versions.

### Shared node helpers (`base.py`)

- `node_text(source, node)` — UTF-8 byte-range slicing
- `iter_nodes(root, kind)` — depth-first traversal
- `line_number(node)`, `line_end(node)`

### Language-specific extractors

| Module | Approach |
|--------|----------|
| `python_queries.py` | Dedicated: imports, classes, functions, decorators, SDK call patterns |
| `javascript_queries.py` | Dedicated: imports, classes, functions, fetch/axios patterns |
| `common_queries.py` | `LanguageProfile` dataclass drives extraction for Go, Rust, Java, Ruby, C/C++, C#, PHP, Kotlin, Scala, Swift |
| `docker_queries.py` | FROM, RUN, COPY, CMD, EXPOSE instructions |
| `yaml_queries.py` | K8s manifest metadata + container images |
| `sql_queries.py` | CREATE TABLE/PROCEDURE + PL/SQL pattern heuristics |
| `hcl_queries.py` | Terraform resource/module/variable blocks |
| `shell_queries.py` | Functions + CLI tool detection |
| `env_queries.py` | Key listing + endpoint enrichment |

## Endpoint Extraction (`extractors/endpoints.py`)

Runs on **all** parsed files after tree-sitter extraction. Regex-based, language-agnostic.

### Extracted types

| Model | Fields |
|-------|--------|
| `ExternalUrl` | `url`, `line`, `context` |
| `DatabaseEndpoint` | `connection_type`, `host`, `port`, `user`, `schema`, `database`, `line`, `raw_redacted` |

### Pattern categories

1. **URLs** — `https?://`, `wss?://`, `ftp://`
2. **Connection URIs** — `postgres://`, `mysql://`, `mongodb://`, `redis://`, `jdbc:`
3. **Environment variables** — `DATABASE_URL`, `DB_HOST`, `REDIS_URL`, `PGUSER`, …
4. **Config key-values** — `host=`, `port=`, `user=`, `schema=`, `database=`
5. **Dict literals** — `"host": "value"` in Python/JSON
6. **Driver strings** — `create_engine("…")`

Passwords are redacted in output (`***`).

## Markdown Generation

### Module file (`module.md.j2`)

YAML frontmatter plus sections: Overview, Imports, Classes, Functions, External Service Calls, External URLs, Database & Connection Endpoints.

### Project index (`project_index.md.j2`)

- Tech stack, language counts, dependency graph
- **External URLs (project-wide)** — deduplicated
- **Database Endpoints (project-wide)** — all connections with source file refs
- File index table with module links

### Filename sanitization

`src/app.py` → `modules/src_app_py.md`

## CLI Interface

```bash
python main.py init [PATH] [--languages python,kubernetes,env] [--verbose]
python main.py bundle [PATH] [--output full_repo_context.md] [--verbose]
# or after pip install -e .:
repo-parser init [PATH] [OPTIONS]
repo-parser bundle [PATH] [OPTIONS]
```

### `bundle` command

- Requires an existing `.rag_kb/` directory (run `init` first)
- Order: `project_index.md` → `modules/*.md` (alphabetical)
- Delimiter before each module file; frontmatter preserved
- Prints size (KB/MB), character/word counts, ~token estimate, and context-window warning
- Raises a clear error if `.rag_kb/` is missing or empty

### Logging

- All logs written to `repo-parser.log` in the current working directory
- No stdout logging (preserves Rich progress bar rendering)
- `--verbose` sets file log level to `DEBUG`; default is `INFO`

## Error Handling

- Unparseable files: log warning, skip (don't fail entire run)
- Unsupported extension: excluded at scan time
- Missing tree-sitter grammar: skip with warning

## Testing Strategy

1. `python main.py init tests/fixtures -v` — Docker, K8s, SQL, .env, Go, Terraform, Python
2. `python main.py init --verbose` — full self-index of vegaparser repo
3. Inspect `.rag_kb/project_index.md` for aggregated URLs and DB endpoints
