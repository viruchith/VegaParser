"""Unit tests for logging setup configuration."""

from __future__ import annotations

import logging

import pytest

from repo_parser.ui.logging_config import LOG_FILENAME, setup_logging


def test_setup_logging_file_target(tmp_path):
    log_path = setup_logging(verbose=True, log_dir=tmp_path, log_target="file")
    assert log_path is not None
    assert log_path.name == LOG_FILENAME
    assert log_path.exists()
    assert any(isinstance(h, logging.FileHandler) for h in logging.getLogger().handlers)


def test_setup_logging_console_target(tmp_path):
    log_path = setup_logging(verbose=False, log_dir=tmp_path, log_target="console")
    assert log_path is None
    handlers = logging.getLogger().handlers
    assert any(isinstance(h, logging.StreamHandler) for h in handlers)
    assert not any(isinstance(h, logging.FileHandler) for h in handlers)


def test_setup_logging_both_target(tmp_path):
    log_path = setup_logging(verbose=False, log_dir=tmp_path, log_target="both")
    assert log_path is not None
    handlers = logging.getLogger().handlers
    assert any(isinstance(h, logging.StreamHandler) for h in handlers)
    assert any(isinstance(h, logging.FileHandler) for h in handlers)


def test_setup_logging_invalid_target_raises(tmp_path):
    with pytest.raises(ValueError):
        setup_logging(verbose=False, log_dir=tmp_path, log_target="invalid")  # type: ignore[arg-type]
