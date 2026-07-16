"""CLI for VegaParser."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

import typer

from repo_parser.cache import IndexCache, _parsed_file_to_dict, compute_hash
from repo_parser.generator.bundle import BUNDLE_FILENAME, BundleError, bundle_knowledge_base, bundle_stats
from repo_parser.generator.markdown import MarkdownGenerator, sanitize_filename
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
    cache: IndexCache,
    modules_dir: Path,
    force: bool,
) -> tuple[list, int]:
    from repo_parser.models import ParsedFile

    parsed_files: list[ParsedFile] = []
    fresh_count = 0

    def _get_or_parse(rel_str: str, content: str):
        """Return (ParsedFile|None, from_cache) using the incremental cache."""
        nonlocal fresh_count
        content_hash = compute_hash(content)
        module_file = modules_dir / sanitize_filename(rel_str)
        if not force and cache.is_cached(rel_str, content_hash, module_file):
            cached = cache.get_cached_parsed_file(rel_str)
            if cached is not None:
                logger.debug("Cache hit: %s", rel_str)
                return cached, True
        result = engine.parse_file(rel_str, content)
        if result is not None:
            cache.update(rel_str, content_hash, _parsed_file_to_dict(result))
            fresh_count += 1
        return result, False

    for rel_path in files:
        rel_str = rel_path.as_posix()
        progress_update(f"Parsing {truncate_filepath(rel_str)}…")

        content = scanner.read_file(rel_path)
        if content is None:
            logger.warning("Skipped unreadable file: %s", rel_str)
            progress_update(advance=1)
            continue

        sources[rel_str] = content

        if lang_filter:
            detected = detect_language(rel_str)
            if detected not in lang_filter:
                if not (detected == "yaml" and "kubernetes" in lang_filter):
                    logger.debug("Skipped by language filter: %s (%s)", rel_str, detected)
                    progress_update(advance=1)
                    continue
            if detected == "yaml" and "kubernetes" in lang_filter and "yaml" not in lang_filter:
                result, _ = _get_or_parse(rel_str, content)
                if result is not None and result.language == "kubernetes":
                    parsed_files.append(result)
                    logger.debug("Parsed Kubernetes manifest: %s", rel_str)
                elif result is None:
                    logger.debug("Skipped non-Kubernetes YAML: %s", rel_str)
                progress_update(advance=1)
                continue

        result, _ = _get_or_parse(rel_str, content)
        if result is not None:
            parsed_files.append(result)
        else:
            logger.warning("Failed to parse or unsupported file: %s", rel_str)

        progress_update(advance=1)

    return parsed_files, fresh_count


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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
    log_target: Literal["file", "console", "both"] = typer.Option(
        "file",
        "--log-target",
        help="Write logs to file, console, or both.",
        case_sensitive=False,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "--no-cache",
        help="Ignore cache and reparse all files.",
    ),
) -> None:
    """Initialize parsing and generate the .rag_kb knowledge base."""
    log_path = setup_logging(verbose, log_target=log_target.lower())

    root = path.resolve()
    lang_filter: set[str] | None = None
    extensions: set[str] | None = None
    if languages:
        lang_filter = normalize_language_filter(languages)
        extensions = extensions_for_languages(lang_filter)
        logger.info("Language filter: %s", ", ".join(sorted(lang_filter)))

    logger.info("Starting init scan at %s", root)
    logger.debug("Init options: languages=%s force=%s log_target=%s", languages, force, log_target)
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

    rag_kb_dir = root / ".rag_kb"
    modules_dir = rag_kb_dir / "modules"
    cache = IndexCache(rag_kb_dir)
    if not force:
        cache.load()
    else:
        logger.info("Cache bypass enabled via --force/--no-cache")

    # Purge cache entries and module files for sources that no longer exist.
    current_rel_paths = {rel.as_posix() for rel in files}
    for stale_path in sorted(cache.known_paths() - current_rel_paths):
        stale_module = modules_dir / sanitize_filename(stale_path)
        if stale_module.exists():
            stale_module.unlink()
            logger.debug("Removed stale module file: %s", stale_module)
        cache.remove(stale_path)

    with parsing_progress(total_steps, "Parsing repository files…") as progress_update:
        logger.info("Starting parse phase for %d files", len(files))
        parsed_files, fresh_count = _parse_files(
            scanner, files, engine, lang_filter, progress_update, cache, modules_dir, force
        )
        logger.info(
            "Parse phase complete: parsed_files=%d fresh=%d cached=%d",
            len(parsed_files),
            fresh_count,
            len(parsed_files) - fresh_count,
        )

        progress_update("Generating Markdown knowledge base…")
        logger.info("Starting markdown generation")
        engine.infer_internal_dependencies(parsed_files)
        generator = MarkdownGenerator(root)
        index_path = generator.generate(parsed_files)
        cache.save()
        progress_update(advance=1)
        logger.info("Wrote knowledge base to %s (%d modules)", index_path.parent, len(parsed_files))

    cached_count = len(parsed_files) - fresh_count
    console.print(
        f"[green]Generated knowledge base[/green] with [bold]{len(parsed_files)}[/bold] modules "
        f"([bold]{fresh_count}[/bold] parsed, [bold]{cached_count}[/bold] from cache)."
    )
    console.print(f"Project index: [cyan]{index_path}[/cyan]")
    if log_path is not None:
        console.print(f"Log file: [dim]{log_path}[/dim]")
    else:
        console.print("Log target: [dim]console[/dim]")


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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
    log_target: Literal["file", "console", "both"] = typer.Option(
        "file",
        "--log-target",
        help="Write logs to file, console, or both.",
        case_sensitive=False,
    ),
) -> None:
    """Concatenate `.rag_kb/` Markdown files into a single LLM context bundle."""
    log_path = setup_logging(verbose, log_target=log_target.lower())
    root = path.resolve()
    logger.info("Starting bundle at %s", root)
    logger.debug("Bundle options: output=%s log_target=%s", output, log_target)

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
    else:
        console.print("  Log target:  [dim]console[/dim]")
    console.print(
        "\n[yellow]Warning:[/yellow] This file is intended for LLMs with large context windows. "
        "Verify your model's context limit before injecting the full bundle."
    )
