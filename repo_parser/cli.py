"""CLI for VegaParser."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from repo_parser.cache import (
    CACHE_VERSION,
    build_filter_signature,
    file_signature,
    load_cache,
    parsed_file_from_dict,
    parsed_file_to_dict,
    save_cache,
)
from repo_parser.generator.bundle import BUNDLE_FILENAME, BundleError, bundle_knowledge_base, bundle_stats
from repo_parser.generator.markdown import MarkdownGenerator
from repo_parser.parser.engine import ParserEngine
from repo_parser.parser.registry import detect_language, extensions_for_languages, normalize_language_filter
from repo_parser.traversal.scanner import RepositoryScanner
from repo_parser.ui.console import console
from repo_parser.ui.logging_config import setup_logging
from repo_parser.ui.progress import parsing_progress, run_with_spinner, truncate_filepath

app = typer.Typer(
    name="repo-parser",
    help="Parse a code repository and generate an LLM-optimized RAG knowledge base.",
    add_completion=False,
    no_args_is_help=True,
)

logger = logging.getLogger(__name__)


@app.callback()
def _cli_root() -> None:
    """VegaParser — generate RAG knowledge bases from code repositories."""


def _parse_files(
    scanner: RepositoryScanner,
    files: list[Path],
    engine: ParserEngine,
    lang_filter: set[str] | None,
    progress_update,
    workers: int = 1,
) -> list:
    from repo_parser.models import ParsedFile

    parsed_files: list[ParsedFile] = []
    cached_files = {}
    cache_payload = load_cache(scanner.root)
    if cache_payload and cache_payload.get("version") == CACHE_VERSION and cache_payload.get("filter") == build_filter_signature(lang_filter, scanner.extensions):
        cached_files = cache_payload.get("files", {})

    def _is_cache_hit(rel_path: Path) -> bool:
        rel_str = rel_path.as_posix()
        cached = cached_files.get(rel_str)
        if not cached:
            return False
        try:
            return cached.get("meta") == file_signature(scanner.root / rel_path)
        except OSError:
            return False

    def _parse_one(rel_path: Path):
        rel_str = rel_path.as_posix()
        content = scanner.read_file(rel_path)
        if content is None:
            logger.warning("Skipped unreadable file: %s", rel_str)
            return None

        if lang_filter:
            detected = detect_language(rel_str)
            if detected not in lang_filter:
                if not (detected == "yaml" and "kubernetes" in lang_filter):
                    logger.debug("Skipped by language filter: %s (%s)", rel_str, detected)
                    return None
            if detected == "yaml" and "kubernetes" in lang_filter and "yaml" not in lang_filter:
                result = engine.parse_file(rel_str, content)
                if result is not None and result.language != "kubernetes":
                    logger.debug("Skipped non-Kubernetes YAML: %s", rel_str)
                    return None
                return result

        result = engine.parse_file(rel_str, content)
        if result is None:
            logger.warning("Failed to parse or unsupported file: %s", rel_str)
        return result

    def _process_one(rel_path: Path):
        """Cache-aware wrapper: return cached result or freshly parse the file."""
        rel_str = rel_path.as_posix()
        if _is_cache_hit(rel_path):
            logger.debug("Cache hit: %s", rel_str)
            return parsed_file_from_dict(cached_files[rel_str]["parsed"])
        return _parse_one(rel_path)

    if workers <= 1:
        # Sequential path — preserves original progress display behaviour.
        for rel_path in files:
            rel_str = rel_path.as_posix()
            progress_update(f"Processing {truncate_filepath(rel_str)}…")
            result = _process_one(rel_path)
            if result is not None:
                parsed_files.append(result)
            progress_update(advance=1)
    else:
        # Parallel path — Rich Progress.update() is thread-safe.
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_path = {executor.submit(_process_one, fp): fp for fp in files}
            for future in as_completed(future_to_path):
                rel_path = future_to_path[future]
                progress_update(f"Processing {truncate_filepath(rel_path.as_posix())}…")
                try:
                    result = future.result()
                except Exception as exc:
                    logger.error("Worker failed on %s: %s", rel_path, exc)
                    result = None
                if result is not None:
                    parsed_files.append(result)
                progress_update(advance=1)

    parsed_files.sort(key=lambda p: p.filepath)
    return parsed_files


def _print_secret_report(rows: list[tuple[str, int, str]]) -> None:
    """Print category, file and line for each likely secret. Values are never available here."""
    if not rows:
        console.print("[green]No likely secrets found.[/green]")
        return
    table = Table(title="Likely secrets (values hidden)")
    table.add_column("Category", style="yellow")
    table.add_column("File")
    table.add_column("Line", justify="right")
    for path, line, category in rows:
        table.add_row(category, path, str(line) if line else "-")
    console.print(table)


@app.command("init")
def init(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the repository root to parse.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    languages: Optional[str] = typer.Option(
        None,
        "--languages",
        "-l",
        help="Comma-separated languages to parse (e.g. python,javascript).",
    ),
    workers: int = typer.Option(
        0,
        "--workers",
        "-j",
        help=(
            "Parallel file-parse workers. "
            "0 = auto (min(CPU count, 8)); 1 = sequential (default behaviour)."
        ),
        min=0,
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug file logging."),
    report_secrets: bool = typer.Option(
        False,
        "--report-secrets",
        help="List likely secrets as category, file and line. Values are never printed.",
    ),
) -> None:
    """Initialize parsing and generate the .rag_kb knowledge base."""
    log_path = setup_logging(verbose)

    root = path.resolve()
    lang_filter: set[str] | None = None
    extensions: set[str] | None = None
    if languages:
        lang_filter = normalize_language_filter(languages)
        extensions = extensions_for_languages(lang_filter)
        logger.info("Language filter: %s", ", ".join(sorted(lang_filter)))

    effective_workers = workers if workers > 0 else min(os.cpu_count() or 4, 8)
    logger.info("Starting init scan at %s (workers=%d)", root, effective_workers)
    scanner = RepositoryScanner(root, languages=lang_filter, extensions=extensions)

    files = run_with_spinner(
        f"Discovering and filtering files in {truncate_filepath(str(root))}…",
        scanner.discover,
    )

    if not files:
        logger.warning("No parseable files found under %s", root)
        console.print("[yellow]No parseable files found.[/yellow]")
        raise typer.Exit(code=0)

    logger.info("Discovered %d files to process", len(files))
    engine = ParserEngine()
    total_steps = len(files) + 1

    with parsing_progress(total_steps, "Parsing repository files…") as progress_update:
        parsed_files = _parse_files(scanner, files, engine, lang_filter, progress_update, workers=effective_workers)

        progress_update("Generating Markdown knowledge base…")
        engine.infer_internal_dependencies(parsed_files)
        save_cache(
            root,
            {
                "version": CACHE_VERSION,
                "filter": build_filter_signature(lang_filter, extensions),
                "files": {
                    pf.filepath: {
                        "meta": file_signature(root / pf.filepath),
                        "parsed": parsed_file_to_dict(pf),
                    }
                    for pf in parsed_files
                },
            },
        )
        generator = MarkdownGenerator(root)
        index_path = generator.generate(parsed_files)
        progress_update(advance=1)
        logger.info("Wrote knowledge base to %s (%d modules)", index_path.parent, len(parsed_files))

    console.print(
        f"[green]Generated knowledge base[/green] with [bold]{len(parsed_files)}[/bold] modules."
    )
    console.print(f"Project index: [cyan]{index_path}[/cyan]")
    secret_rows = sorted(
        ((pf.filepath, finding.line, finding.category) for pf in parsed_files for finding in pf.secret_findings),
    )
    files_with_secrets = len({path for path, _, _ in secret_rows})
    logger.info("Likely secrets redacted: %d in %d file(s)", len(secret_rows), files_with_secrets)
    console.print(
        f"Likely secrets redacted: [bold]{len(secret_rows)}[/bold] in {files_with_secrets} file(s)."
    )
    if report_secrets:
        _print_secret_report(secret_rows)
    console.print(f"Log file: [dim]{log_path}[/dim]")


@app.command("bundle")
def bundle(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the repository root containing `.rag_kb/`.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    output: str = typer.Option(
        BUNDLE_FILENAME,
        "--output",
        "-o",
        help=f"Output filename inside `.rag_kb/` (default: {BUNDLE_FILENAME}).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug file logging."),
) -> None:
    """Concatenate `.rag_kb/` Markdown files into a single LLM context bundle."""
    log_path = setup_logging(verbose)
    root = path.resolve()
    logger.info("Starting bundle at %s", root)

    try:
        bundle_path = run_with_spinner(
            "Bundling knowledge base files…",
            lambda: bundle_knowledge_base(root, output_name=output),
        )
    except BundleError as exc:
        logger.error("Bundle failed: %s", exc)
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    stats = bundle_stats(bundle_path)
    logger.info("Bundle written to %s (%s)", bundle_path, stats["size_human"])
    console.print("[green]Bundle created successfully.[/green]")
    console.print(f"  Output:      {bundle_path}")
    console.print(f"  Size:        {stats['size_human']} ({stats['bytes']:,} bytes)")
    console.print(f"  Characters:  {stats['characters']:,}")
    console.print(f"  Words:       {stats['words']:,}")
    console.print(f"  ~Tokens:     {stats['tokens_estimate']:,} (rough estimate, ~4 chars/token)")
    console.print(f"  Log file:    [dim]{log_path}[/dim]")
    console.print(
        "\n[yellow]Warning:[/yellow] This file is intended for LLMs with large context windows. "
        "Verify your model's context limit before injecting the full bundle."
    )
