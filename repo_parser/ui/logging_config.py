"""Logging configuration for the CLI."""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Literal

from repo_parser.redaction import redact_text

LOG_FILENAME = "repo-parser.log"
LogTarget = Literal["file", "console", "both"]


class RedactingFilter(logging.Filter):
    """Second line of defence: redact secrets from every log message and traceback.

    Attached to handlers rather than the root logger, because logger-level
    filters do not run for records propagated from child loggers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + "".join(traceback.format_exception(*record.exc_info))
        record.msg = redact_text(message)
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


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
    for handler in root.handlers:
        # Close before dropping so repeated runs in one process do not leak file handles.
        handler.close()
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

    for handler in root.handlers:
        handler.addFilter(RedactingFilter())

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
