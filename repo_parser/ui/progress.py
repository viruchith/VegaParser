"""Rich progress helpers for the init command."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from typing import TypeVar

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from repo_parser.ui.console import console

T = TypeVar("T")

DISCOVERY_COLUMNS = (
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
)

PARSING_COLUMNS = (
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    MofNCompleteColumn(),
    TimeRemainingColumn(),
)


def truncate_filepath(filepath: str, max_width: int = 48) -> str:
    """Truncate long paths for terminal display, keeping the tail."""
    if len(filepath) <= max_width:
        return filepath
    return f"…{filepath[-(max_width - 1):]}"


@contextmanager
def discovery_progress(description: str = "Discovering and filtering files…"):
    """Indeterminate spinner while scanning the repository."""
    with Progress(*DISCOVERY_COLUMNS, console=console, transient=True) as progress:
        task_id = progress.add_task(description, total=None)
        yield lambda msg: progress.update(task_id, description=msg)


@contextmanager
def parsing_progress(total: int, description: str = "Processing files…"):
    """Determinate progress bar for parsing and generation."""
    with Progress(*PARSING_COLUMNS, console=console, transient=False) as progress:
        task_id = progress.add_task(description, total=max(total, 1))

        def update(description: str | None = None, advance: int = 0) -> None:
            kwargs: dict = {"advance": advance}
            if description is not None:
                kwargs["description"] = description
            progress.update(task_id, **kwargs)

        yield update


def run_with_spinner(description: str, fn: Callable[[], T]) -> T:
    """Run a callable under an indeterminate discovery spinner."""
    with discovery_progress(description):
        return fn()
