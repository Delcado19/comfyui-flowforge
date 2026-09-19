"""Tests for FlowForge logging configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from flowforge.logger import setup_logger


def _close_logger(name: str) -> None:
    logger = logging.getLogger(name)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def test_default_logger_does_not_create_log_files(tmp_path: Path, monkeypatch) -> None:
    name = "flowforge.test.default_no_file"
    _close_logger(name)
    monkeypatch.chdir(tmp_path)

    logger = setup_logger(name)
    logger.info("console only")

    assert not (tmp_path / "logs").exists()
    assert not any(
        isinstance(handler, logging.FileHandler)
        for handler in logger.handlers
    )
    _close_logger(name)


def test_explicit_file_logging_is_delayed_until_first_record(tmp_path: Path) -> None:
    name = "flowforge.test.explicit_file"
    _close_logger(name)
    log_dir = tmp_path / "logs"

    logger = setup_logger(name, log_dir=log_dir)

    assert log_dir.is_dir()
    assert list(log_dir.iterdir()) == []

    logger.debug("written on demand")
    files = list(log_dir.iterdir())

    assert len(files) == 1
    assert "Fehlerhaftes" not in files[0].name
    assert "flowforge_test_explicit_file" in files[0].name
    assert "written on demand" in files[0].read_text(encoding="utf-8")
    _close_logger(name)


def test_repeated_setup_does_not_duplicate_handlers(tmp_path: Path) -> None:
    name = "flowforge.test.no_duplicates"
    _close_logger(name)

    first = setup_logger(name, log_dir=tmp_path / "logs")
    second = setup_logger(name, log_dir=tmp_path / "logs")

    assert first is second
    assert sum(
        isinstance(handler, logging.FileHandler)
        for handler in first.handlers
    ) == 1
    assert sum(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in first.handlers
    ) == 1
    _close_logger(name)
