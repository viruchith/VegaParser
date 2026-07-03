# VegaParser — TODO Tracker

## Phase 1: Planning & Setup
- [x] Create DESIGN.md
- [x] Create TODO.md
- [x] Create requirements.txt and project structure
- [x] Create CLI entry point
- [x] Add pyproject.toml with `repo-parser` console script

## Phase 2: File Traversal
- [x] Implement scanner with pathspec + .gitignore
- [x] Skip binary, hidden, and package directories
- [x] Filter to recognized language mappings only
- [x] Support extensionless files (Dockerfile, .env)

## Phase 3: Tree-sitter Parsing
- [x] Define shared models (ParsedFile, ClassInfo, FunctionInfo)
- [x] Implement language registry (extension → language)
- [x] Implement Python and JavaScript/TypeScript parsers
- [x] Implement profile-driven parsers (Go, Rust, Java, Ruby, C/C++, C#, PHP, Kotlin, Scala, Swift)
- [x] Implement Dockerfile, YAML/K8s, SQL/PL/SQL, Terraform/HCL, Shell parsers
- [x] Implement .env / properties / ini config parser
- [x] Implement external SDK call detection

## Phase 4: Endpoint & Connection Indexing
- [x] Add ExternalUrl and DatabaseEndpoint models
- [x] Build regex-based endpoint extractor (URLs, URIs, env vars, config KV)
- [x] Redact passwords in connection strings
- [x] Enrich all parsed files automatically
- [x] Add per-module and project-wide endpoint sections to Markdown output

## Phase 5: Markdown Generation
- [x] Create Jinja2 templates (module + project_index)
- [x] Implement markdown generator with endpoint aggregation
- [x] Implement stack detector (requirements.txt, package.json, go.mod, Cargo.toml)
- [x] Test full output generation

## Phase 6: Integration & Docs
- [x] Wire CLI `init` command end-to-end
- [x] Add tests/fixtures corpus (Docker, K8s, SQL, .env, Go, Terraform)
- [x] Write and maintain README.md
- [x] Update DESIGN.md to reflect current architecture
- [x] Run full init on vegaparser repo and verify output

## Phase 7: Context Bundle
- [x] Implement `bundle` CLI command
- [x] Concatenate `project_index.md` + `modules/*.md` with delimiters
- [x] Output `full_repo_context.md` with size/token stats and warning
- [x] Update README and DESIGN docs

## Phase 8: Progress UI
- [x] Add `rich` dependency
- [x] Spinner for file discovery phase
- [x] Determinate progress bar for parsing + Markdown generation
- [x] RichHandler for warnings/logs without breaking the bar
- [x] Dynamic per-file description with path truncation

## Phase 9: Open-Source Release
- [x] File-based logging to `repo-parser.log` (no stdout by default)
- [x] `--verbose` enables DEBUG file logging
- [x] Logging integrated in scanner, parser engine, and CLI loops
- [x] SEO-optimized README with shields.io badges
- [x] GPLv3 LICENSE file and pyproject.toml metadata

## Future Ideas
- [ ] JSON / TOML structured config parsers
- [ ] HTML / CSS / Markdown content indexing
- [ ] Merge multi-line DB config dicts into single endpoint records
- [ ] Automated test suite (pytest)
- [ ] `watch` command for incremental re-indexing
