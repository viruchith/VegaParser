"""Logging configuration for the CLI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

LOG_FILENAME = "repo-parser.log"
LogTarget = Literal["file", "console", "both"]


def setup_logging(
    verbose: bool = False,
    log_dir: Path | None = None,
    log_target: LogTarget = "file",
) -> Path | None:
    """
    Configure logging handlers for file and/or console.

    Args:
        verbose: When True, log level is DEBUG; otherwise INFO.
        log_dir: Directory for the log file (default: current working directory).
        log_target: Where logs are written: "file", "console", or "both".

    Returns:
        Absolute path to the log file when file logging is enabled, else None.
    """
    target = log_target.lower()
    if target not in {"file", "console", "both"}:
        raise ValueError(f"Invalid log_target: {log_target!r}")
    log_level = logging.DEBUG if verbose else logging.INFO

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_path: Path | None = None
    if target in {"file", "both"}:
        log_path = (log_dir or Path.cwd()) / LOG_FILENAME
        file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="a")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if target in {"console", "both"}:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    logging.getLogger("tree_sitter").setLevel(logging.ERROR)

    logger = logging.getLogger("repo_parser.logging")
    if log_path is not None:
        logger.info(
            "Logging initialized (level=%s, target=%s) → %s",
            logging.getLevelName(log_level),
            target,
            log_path,
        )
        return log_path.resolve()
    logger.info(
        "Logging initialized (level=%s, target=%s)",
        logging.getLevelName(log_level),
        target,
    )
    return None
