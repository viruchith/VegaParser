# VegaParser

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Typer](https://img.shields.io/badge/CLI-Typer-00b4ab.svg)](https://typer.tiangolo.com/)
[![Rich](https://img.shields.io/badge/UX-Rich-fee715.svg)](https://rich.readthedocs.io/)

**VegaParser** is a Python CLI for **codebase indexing** and **RAG** (Retrieval-Augmented Generation). It turns any local repository into a structured **knowledge base** for **Large Language Models** using **Tree-sitter AST parsing**, then emits LLM-ready **Markdown** for **context injection** into models with massive context windows.

Index Python, Go, Rust, Kubernetes YAML, SQL, Dockerfiles, and 20+ more languages — complete with imports, signatures, docstrings, external URLs, and database endpoints.

## Features

- **Tree-sitter AST parsing** — semantic extraction across 20+ languages (Python, JS/TS, Go, Rust, Java, C#, SQL, …)
- **RAG-optimized Markdown** — per-module docs with YAML frontmatter in `.rag_kb/modules/`
- **Knowledge base index** — global project map with tech stack, dependency graph, URLs, and DB endpoints
- **Context injection bundle** — `bundle` command merges everything into `full_repo_context.md`
- **Endpoint discovery** — external URLs, connection strings, `.env` variables, host/user/schema metadata
- **Infrastructure-aware** — Dockerfile, Kubernetes manifests, Terraform/HCL, PL/SQL heuristics
- **Smart traversal** — respects `.gitignore`, skips binaries and `node_modules`
- **Rich progress UI** — spinner + progress bar; logs go to `repo-parser.log` (never breaks the terminal)
- **Verbose file logging** — `--verbose` captures DEBUG-level AST and skip details in `repo-parser.log`

## Installation

```bash
git clone https://github.com/your-org/vegaparser.git
cd vegaparser

python -m venv .venv

# Windows
.\.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Editable install with console script:

```bash
pip install -e .
repo-parser --help
```

**Requirements:** Python 3.10+

## Usage

### Index a repository (`init`)

Build the `.rag_kb/` knowledge base from the current directory:

```bash
python main.py init
python main.py init /path/to/your/project
python main.py init -l python,go,kubernetes --verbose
```

| Flag | Description |
|------|-------------|
| `PATH` | Repository root (default: `.`) |
| `-l`, `--languages` | Filter languages (e.g. `python,javascript,terraform`) |
| `-v`, `--verbose` | DEBUG logging to `repo-parser.log` |

### Bundle for LLM context injection (`bundle`)

Concatenate the knowledge base into one file for full-repo **context injection**:

```bash
python main.py init          # generate .rag_kb/ first
python main.py bundle        # → .rag_kb/full_repo_context.md
python main.py bundle -o my_context.md -v
```

The bundle places `project_index.md` first, then all module files with clear delimiters. The CLI reports file size, word count, and estimated tokens.

### Logging

All runtime logs are written to **`repo-parser.log`** in the current working directory. Nothing is printed to stdout during parsing (protecting the Rich progress bar). Use `--verbose` for detailed DEBUG output including skipped files and AST parse results.

## How It Works

```
Repository  →  Scanner (.gitignore)  →  Tree-sitter AST  →  Endpoint extractor
                    ↓                        ↓                      ↓
              .rag_kb/modules/*.md    classes, functions    URLs, DB hosts
                    ↓
              .rag_kb/project_index.md  (global map)
                    ↓
              .rag_kb/full_repo_context.md  (bundle command)
```

1. **Discovery** — `pathspec` walks the repo, applies `.gitignore`, and filters to known languages.
2. **AST parsing** — Tree-sitter extracts imports, classes, functions, docstrings, and SDK call patterns per language.
3. **Endpoint enrichment** — regex scan finds URLs, `DATABASE_URL`, JDBC strings, and config key-values (passwords redacted).
4. **Markdown generation** — Jinja2 templates produce structured module docs and a project index.
5. **Bundling** — optional single-file output ordered for LLM attention (index first, then modules).

## Supported Languages

| Category | Languages | Files |
|----------|-----------|-------|
| General purpose | Python, JavaScript, TypeScript, Go, Rust, Java, Kotlin, Scala, C#, Ruby, PHP, Swift, C/C++ | `*.py`, `*.js`, `*.go`, … |
| Infrastructure | Dockerfile, Kubernetes, Terraform/HCL | `Dockerfile`, `*.yaml`, `*.tf` |
| Database | SQL, PL/SQL | `*.sql`, `*.plsql` |
| Config | Environment files | `.env`, `*.properties`, `*.ini` |
| Shell | Bash | `*.sh`, `Makefile` |

## Project Layout

```
vegaparser/
├── repo_parser/
│   ├── cli.py              # init & bundle commands
│   ├── parser/             # Tree-sitter engines + extractors
│   ├── generator/          # Markdown + bundle output
│   ├── traversal/          # Scanner + gitignore
│   └── ui/                 # Rich progress + file logging
├── tests/fixtures/         # Sample corpus
├── DESIGN.md               # Architecture reference
└── LICENSE                 # GPLv3
```

## Development

```bash
python main.py init --verbose
python main.py bundle
cat repo-parser.log          # inspect file logs
cat .rag_kb/project_index.md
```

## License

This project is licensed under the **GNU General Public License v3.0 (GPLv3)**. See the [LICENSE](LICENSE) file for the full text.

You are free to use, modify, and distribute this software under the terms of GPLv3. Derivative works must also be released under GPLv3.
