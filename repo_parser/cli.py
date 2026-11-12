"""CLI for VegaParser."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from repo_parser import __version__
from repo_parser.cache import (
    CACHE_VERSION,
    build_filter_signature,
    cache_is_usable,
    load_cache,
    parsed_file_from_dict,
    parsed_file_to_dict,
    save_cache,
    sha256_bytes,
)
from repo_parser.generator.bundle import BUNDLE_FILENAME, BundleError, bundle_knowledge_base, bundle_stats
from repo_parser.generator.markdown import MarkdownGenerator
from repo_parser.models import ParsedFile
from repo_parser.parser.engine import ParserEngine
from repo_parser.parser.registry import detect_language, extensions_for_languages, normalize_language_filter
from repo_parser.traversal.scanner import RepositoryScanner, decode_source
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


class LogTarget(str, Enum):
    file = "file"
    console = "console"
    both = "both"


@app.callback()
def _cli_root() -> None:
    """VegaParser — generate RAG knowledge bases from code repositories."""


@dataclass
class _ParseStats:
    reused: int = 0
    parsed: int = 0
    # rel_path -> cache entry for every file that was read successfully.
    entries: dict[str, dict] = field(default_factory=dict)


def _restore_cached(entry: object, digest: str) -> tuple[bool, ParsedFile | None]:
    """Return ``(hit, parsed)``. A malformed or stale entry is a miss, never an error."""
    if not isinstance(entry, dict) or entry.get("sha256") != digest:
        return False, None
    payload = entry.get("parsed")
    if payload is None:
        return True, None
    try:
        return True, parsed_file_from_dict(payload)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False, None


def _parse_source(
    engine: ParserEngine,
    rel_str: str,
    content: str,
    lang_filter: set[str] | None,
) -> ParsedFile | None:
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


def _parse_files(
    scanner: RepositoryScanner,
    files: list[Path],
    engine: ParserEngine,
    lang_filter: set[str] | None,
    previous_files: dict,
    progress_update,
    workers: int = 1,
) -> tuple[list[ParsedFile], _ParseStats]:
    """Parse files, reusing cache entries whose SHA-256 matches the bytes on disk."""
    stats = _ParseStats()
    parsed_files: list[ParsedFile] = []

    def _process_one(rel_path: Path) -> tuple[str, str, ParsedFile | None, bool] | None:
        rel_str = rel_path.as_posix()
        data = scanner.read_bytes(rel_path)
        if data is None:
            logger.warning("Skipped unreadable file: %s", rel_str)
            return None
        digest = sha256_bytes(data)
        hit, cached = _restore_cached(previous_files.get(rel_str), digest)
        if hit:
            logger.debug("Cache hit: %s", rel_str)
            return rel_str, digest, cached, True
        parsed = _parse_source(engine, rel_str, decode_source(data), lang_filter)
        return rel_str, digest, parsed, False

    def _record(outcome: tuple[str, str, ParsedFile | None, bool] | None) -> None:
        if outcome is None:
            return
        rel_str, digest, parsed, reused = outcome
        stats.entries[rel_str] = {
            "sha256": digest,
            "module": None,
            "parsed": parsed_file_to_dict(parsed) if parsed is not None else None,
        }
        if reused:
            stats.reused += 1
        else:
            stats.parsed += 1
        if parsed is not None:
            parsed_files.append(parsed)

    if workers <= 1:
        # Sequential path — preserves original progress display behaviour.
        for rel_path in files:
            progress_update(f"Processing {truncate_filepath(rel_path.as_posix())}…")
            _record(_process_one(rel_path))
            progress_update(advance=1)
    else:
        # Parallel path — Rich Progress.update() is thread-safe.
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_path = {executor.submit(_process_one, fp): fp for fp in files}
            for future in as_completed(future_to_path):
                rel_path = future_to_path[future]
                progress_update(f"Processing {truncate_filepath(rel_path.as_posix())}…")
                try:
                    outcome = future.result()
                except Exception as exc:
                    logger.error("Worker failed on %s: %s", rel_path, exc)
                    outcome = None
                _record(outcome)
                progress_update(advance=1)

    parsed_files.sort(key=lambda p: p.filepath)
    return parsed_files, stats


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
    force: bool = typer.Option(
        False,
        "--force",
        "--no-cache",
        help="Ignore the parse cache and reparse every file. The cache is rewritten.",
    ),
    log_target: LogTarget = typer.Option(
        LogTarget.file,
        "--log-target",
        help="Where logs go: file (repo-parser.log in the current directory), console, or both.",
        case_sensitive=False,
    ),
    report_secrets: bool = typer.Option(
        False,
        "--report-secrets",
        help="List likely secrets as category, file and line. Values are never printed.",
    ),
) -> None:
    """Parse the repository and write the .rag_kb knowledge base.

    Files whose content is unchanged since the last run are reused from the parse
    cache. Generated module files whose source was deleted or renamed are removed.
    """
    log_path = setup_logging(verbose, log_target=log_target.value)

    root = path.resolve()
    lang_filter: set[str] | None = None
    extensions: set[str] | None = None
    if languages:
        lang_filter = normalize_language_filter(languages)
        extensions = extensions_for_languages(lang_filter)
        logger.info("Language filter: %s", ", ".join(sorted(lang_filter)))

    filter_signature = build_filter_signature(lang_filter, extensions)
    effective_workers = workers if workers > 0 else min(os.cpu_count() or 4, 8)
    logger.info("Starting init scan at %s (workers=%d, version=%s)", root, effective_workers, __version__)
    scanner = RepositoryScanner(root, languages=lang_filter, extensions=extensions)

    files = run_with_spinner(
        f"Discovering and filtering files in {truncate_filepath(str(root))}…",
        scanner.discover,
    )

    if not files:
        logger.warning("No parseable files found under %s", root)
        console.print("[yellow]No parseable files found.[/yellow]")

    previous = load_cache(root)
    previous_files: dict = {}
    if force:
        logger.info("Ignoring parse cache (--force)")
    elif previous is None:
        logger.info("No parse cache found; parsing every file")
    elif cache_is_usable(previous, filter_signature):
        previous_files = previous["files"]
    else:
        logger.info("Parse cache is from another version or language filter; rebuilding it")

    logger.info("Discovered %d files to process", len(files))
    engine = ParserEngine()
    total_steps = len(files) + 1

    with parsing_progress(total_steps, "Parsing repository files…") as progress_update:
        parsed_files, stats = _parse_files(
            scanner, files, engine, lang_filter, previous_files, progress_update, workers=effective_workers
        )

        progress_update("Generating Markdown knowledge base…")
        engine.infer_internal_dependencies(parsed_files)
        generator = MarkdownGenerator(root)
        names = generator.kb_name_map(parsed_files)
        index_path = generator.generate(parsed_files)
        removed = generator.remove_stale_modules(keep=set(names.values()))

        for rel_str, entry in stats.entries.items():
            entry["module"] = names.get(rel_str)
        save_cache(
            root,
            {
                "version": CACHE_VERSION,
                "tool_version": __version__,
                "filter": filter_signature,
                "files": stats.entries,
            },
        )
        progress_update(advance=1)
        logger.info(
            "Wrote knowledge base to %s (%d modules, %d reused, %d parsed, %d stale removed)",
            index_path.parent,
            len(parsed_files),
            stats.reused,
            stats.parsed,
            len(removed),
        )

    console.print(
        f"[green]Generated knowledge base[/green] with [bold]{len(parsed_files)}[/bold] modules."
    )
    console.print(
        f"Parse cache: [bold]{stats.reused}[/bold] reused, [bold]{stats.parsed}[/bold] parsed, "
        f"[bold]{len(removed)}[/bold] stale module(s) removed."
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
    if log_path is not None:
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
    log_target: LogTarget = typer.Option(
        LogTarget.file,
        "--log-target",
        help="Where logs go: file (repo-parser.log in the current directory), console, or both.",
        case_sensitive=False,
    ),
) -> None:
    """Concatenate `.rag_kb/` Markdown files into a single LLM context bundle."""
    log_path = setup_logging(verbose, log_target=log_target.value)
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
    if log_path is not None:
        console.print(f"  Log file:    [dim]{log_path}[/dim]")
    console.print(
        "\n[yellow]Warning:[/yellow] This file is intended for LLMs with large context windows. "
        "Verify your model's context limit before injecting the full bundle."
    )
