"""File-based logging configuration for the CLI."""

from __future__ import annotations

import logging
from pathlib import Path

LOG_FILENAME = "repo-parser.log"


def setup_logging(verbose: bool = False, log_dir: Path | None = None) -> Path:
    """
    Configure file-only logging (no stdout) to avoid breaking Rich progress bars.

    Args:
        verbose: When True, file log level is DEBUG; otherwise INFO.
        log_dir: Directory for the log file (default: current working directory).

    Returns:
        Absolute path to the log file.
    """
    log_path = (log_dir or Path.cwd()) / LOG_FILENAME
    file_level = logging.DEBUG if verbose else logging.INFO

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="a")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(file_handler)

    logging.getLogger("tree_sitter").setLevel(logging.ERROR)

    logger = logging.getLogger("repo_parser.logging")
    logger.info("Logging initialized (level=%s) → %s", logging.getLevelName(file_level), log_path)
    return log_path.resolve()
