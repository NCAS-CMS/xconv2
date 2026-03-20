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
        format="%(asctime)s %(levelname)s %(processName)s %(name)s: %(message)s",
        handlers=[file_handler, console_handler],
        force=True,
    )
    return log_file
