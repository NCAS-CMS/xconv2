from __future__ import annotations

import logging
import sys
from pathlib import Path


def get_log_file_path() -> Path:
    """Return the shared application log file path, creating directories as needed."""
    log_dir = Path.home() / ".xconv2" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "xconv2.log"


def configure_logging(level: int = logging.INFO) -> Path:
    """Configure root logging for file-first logging with error-only console output."""
    log_file = get_log_file_path()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.ERROR)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[file_handler, console_handler],
        force=True,
    )
    return log_file


def coerce_log_level(level: int | str | None, *, default: int = logging.INFO) -> int:
    """Normalize logging level inputs from ints or level-name strings."""
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        normalized = level.strip().upper()
        if normalized:
            resolved = getattr(logging, normalized, None)
            if isinstance(resolved, int):
                return resolved
    return default


def apply_runtime_log_level(level: int | str) -> int:
    """Apply a new runtime log level while keeping console stderr at ERROR."""
    resolved = coerce_log_level(level)
    root = logging.getLogger()
    root.setLevel(resolved)

    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.setLevel(resolved)

    for name, existing in logging.root.manager.loggerDict.items():
        if isinstance(existing, logging.Logger):
            logging.getLogger(name).setLevel(resolved)

    return resolved
