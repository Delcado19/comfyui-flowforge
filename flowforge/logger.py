"""Project logging helpers."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path


_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def setup_logger(
    name: str,
    log_dir: str | Path | None = None,
) -> logging.Logger:
    """Return a FlowForge logger.

    Console logging is enabled by default. File logging is opt-in via
    ``log_dir`` so ordinary CLI, API, and diagnostic runs do not create one
    timestamped file per imported module.

    Explicit file handlers use ``delay=True``: the path is not created until
    the logger actually emits a file-level record.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    formatter = logging.Formatter(_FORMAT)

    if not any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in logger.handlers
    ):
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(formatter)
        logger.addHandler(console)

    if log_dir is not None:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe_name = "".join(
            character
            if character.isalnum() or character in ("_", "-")
            else "_"
            for character in name
        ).strip("_")
        log_file = path / f"{timestamp}_{safe_name or 'flowforge'}.log"
        if not any(
            isinstance(handler, logging.FileHandler)
            for handler in logger.handlers
        ):
            file_handler = logging.FileHandler(
                log_file,
                encoding="utf-8",
                delay=True,
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    return logger
