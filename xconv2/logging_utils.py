from __future__ import annotations

import logging
import sys
from pathlib import Path


LOG_SCOPE_ORDER = (
    "all",
    "pyfive",
    "p5rem",
    "fsspec",
    "paramiko",
    "xconv2",
    "cfdm_cf_python",
    "cfplot",
)

LOG_SCOPE_DISPLAY_NAMES = {
    "all": "All",
    "pyfive": "pyfive",
    "p5rem": "p5rem",
    "fsspec": "fsspec",
    "paramiko": "paramiko",
    "xconv2": "xconv2",
    "cfdm_cf_python": "cfdm and cf-python",
    "cfplot": "cfplot",
}

LOG_SCOPE_LOGGERS = {
    "pyfive": ("pyfive",),
    "p5rem": ("p5rem",),
    "fsspec": ("fsspec",),
    "paramiko": ("paramiko",),
    "xconv2": ("xconv2",),
    "cfdm_cf_python": ("cfdm", "cf"),
    # Placeholder row for future cfplot logger integration.
    "cfplot": ("cfplot", "cf_plot", "cfp"),
}

LOG_LEVEL_OPTIONS = ("WARNING", "INFO", "DEBUG")


def _set_logger_family_level(logger_name: str, level: int) -> None:
    """Apply *level* to a logger and any already-instantiated descendants."""
    logging.getLogger(logger_name).setLevel(level)

    prefix = logger_name + "."
    for existing_name, existing in logging.root.manager.loggerDict.items():
        if not isinstance(existing, logging.Logger):
            continue
        if existing_name.startswith(prefix):
            logging.getLogger(existing_name).setLevel(level)


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


def normalize_scope_levels(
    scope_levels: dict[str, int | str] | None,
    *,
    default: int = logging.WARNING,
) -> dict[str, int]:
    """Return a complete and normalized scope->level mapping."""
    normalized = {scope: default for scope in LOG_SCOPE_ORDER}
    if not scope_levels:
        return normalized

    for scope, level in scope_levels.items():
        key = str(scope)
        if key not in normalized:
            continue
        normalized[key] = coerce_log_level(level, default=normalized[key])

    return normalized


def apply_scoped_runtime_logging(scope_levels: dict[str, int | str]) -> dict[str, int]:
    """Apply scoped runtime logging while keeping stderr output at ERROR."""
    normalized = normalize_scope_levels(scope_levels)
    all_level = normalized["all"]

    root = logging.getLogger()
    root.setLevel(all_level)

    file_handler_level = min(normalized.values())

    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.setLevel(file_handler_level)
        elif isinstance(handler, logging.StreamHandler):
            handler.setLevel(logging.ERROR)

    for scope, logger_names in LOG_SCOPE_LOGGERS.items():
        level = normalized.get(scope, all_level)
        for logger_name in logger_names:
            _set_logger_family_level(logger_name, level)

    return normalized


def summarize_scope_levels(scope_levels: dict[str, int | str]) -> str:
    """Return compact human-readable scope status text."""
    normalized = normalize_scope_levels(scope_levels)
    parts: list[str] = []
    for scope in LOG_SCOPE_ORDER:
        label = LOG_SCOPE_DISPLAY_NAMES.get(scope, scope)
        parts.append(f"{label}={logging.getLevelName(normalized[scope])}")
    return " | ".join(parts)
