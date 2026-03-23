import sys
import os
import pickle
import base64
import traceback
import logging
import warnings
import inspect
import json
import re
import textwrap
import time
from io import BytesIO
from pathlib import Path
from typing import Any, NamedTuple

import matplotlib
import numpy as np
from matplotlib.backend_bases import FigureManagerBase

# Worker renders to bytes/files only, so force a headless backend and
# avoid spawning a separate matplotlib GUI app/window (e.g. extra dock icon).
matplotlib.use("Agg", force=True)

import cf
import cfplot as cfp
from matplotlib import pyplot as plt

from . import xconv_cf_interface
from . import lineplot as xconv_lineplot
from . import cell_method_handler as xconv_cell_method_handler
from . import __version__
from .logging_utils import apply_runtime_log_level, configure_logging
from .remote_access import (
    RemoteAccessSession,
    create_filesystem,
    descriptor_to_spec,
    normalize_remote_datasets_for_cf_read as _normalize_remote_datasets_for_cf_read_shared,
)

# cf-plot may still call show(); in Agg mode this is non-interactive and noisy.
plt.show = lambda *args, **kwargs: None  # type: ignore[assignment]
plt.ioff()
# Some plotting paths call the backend manager directly; force no-op.
FigureManagerBase.show = lambda self: None  # type: ignore[assignment]
# LinePlot imports pyplot in its own module namespace; disable there too.
xconv_lineplot.plt.show = lambda *args, **kwargs: None  # type: ignore[assignment]
xconv_lineplot.plt.ioff()
warnings.filterwarnings(
    "ignore",
    message="FigureCanvasAgg is non-interactive, and thus cannot be shown",
    category=UserWarning,
)

# Ensure cf-plot never tries to open an external viewer (e.g. ImageMagick
# display) when running worker-generated contour plots.
try:
    cfp.setvars(viewer=None)
    cfp.plotvars.viewer = None
except Exception:
    logger = logging.getLogger(__name__)
    logger.exception("Failed to set cfplot viewer=None in worker")


logger = logging.getLogger(__name__)
SAVE_TASK_HEADER = "#SAVE_TASK_CODE_PATH_B64:"
EMIT_IMAGE_HEADER = "#EMIT_IMAGE:"
TASK_KIND_HEADER = "#TASK_KIND:"
TASK_PAYLOAD_HEADER = "#TASK_PAYLOAD_B64:"
INTERFACE_EXPORTS = tuple(getattr(xconv_cf_interface, "__all__", ()))
OMIT4SAVE_TOKEN = "#omit4save"
REMOTE_SESSION_TTL_SECONDS = 180.0
REMOTE_SESSION_MAX = 4


class TaskHeaders(NamedTuple):
    """Parsed preamble headers extracted from a worker task code block."""

    save_path: str | None
    emit_image: bool
    task_kind: str | None
    task_payload: dict[str, Any] | None
    code: str


class RemoteSessionEntry:
    """Worker-side cached remote session state keyed by descriptor hash."""

    def __init__(
        self,
        *,
        session_id: str,
        descriptor_hash: str,
        descriptor: dict[str, Any],
        filesystem: Any,
    ) -> None:
        now = time.monotonic()
        self.session_id = session_id
        self.descriptor_hash = descriptor_hash
        self.descriptor = descriptor
        self.filesystem = filesystem
        self.created_at = now
        self.last_used = now


remote_session_pool: dict[str, RemoteSessionEntry] = {}

# This dictionary persists data (like 'f') between GUI commands
worker_globals = {
    'cf': cf,
    'cfp': cfp,
    'plt': plt,
    'np': np,
}

# Expose helper functions/constants from the interface module to generated code.
worker_globals.update(
    {
        name: getattr(xconv_cf_interface, name)
        for name in INTERFACE_EXPORTS
    }
)

def send_to_gui(prefix, data=None):
    """Helper to format messages for the GUI pipe."""
    if data is not None:
        payload = base64.b64encode(pickle.dumps(data)).decode()
        print(f"{prefix}:{payload}", flush=True)
        logger.debug("Sent message to GUI with payload prefix=%s size=%d", prefix, len(payload))
    else:
        print(prefix, flush=True)
        logger.debug("Sent message to GUI: %s", prefix)


def _extract_task_headers(code: str) -> TaskHeaders:
    """Parse leading ``#``-prefixed control headers from a worker task block.

    Headers are consumed one per line until the first non-header line.  The
    remaining text is the executable code body.

    Returns a :class:`TaskHeaders` named tuple with fields:

    * ``save_path``   – destination path for ``#SAVE_TASK_CODE_PATH_B64:``
    * ``emit_image``  – False when ``#EMIT_IMAGE:0`` is present
    * ``task_kind``   – value of ``#TASK_KIND:`` (control tasks only)
    * ``task_payload``– decoded JSON dict from ``#TASK_PAYLOAD_B64:``
    * ``code``        – remaining executable code after all headers
    """
    save_path: str | None = None
    emit_image = True
    task_kind: str | None = None
    task_payload: dict[str, Any] | None = None
    payload = code

    while payload.startswith("#"):
        first_newline = payload.find("\n")
        if first_newline < 0:
            break

        header = payload[:first_newline].strip()
        payload = payload[first_newline + 1 :]

        if header.startswith(SAVE_TASK_HEADER):
            encoded = header[len(SAVE_TASK_HEADER) :]
            try:
                save_path = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
            except Exception:
                logger.exception("Invalid save-code header in worker task")
                save_path = None
        elif header.startswith(EMIT_IMAGE_HEADER):
            emit_image = header[len(EMIT_IMAGE_HEADER) :] != "0"
        elif header.startswith(TASK_KIND_HEADER):
            task_kind = header[len(TASK_KIND_HEADER) :].strip() or None
        elif header.startswith(TASK_PAYLOAD_HEADER):
            encoded = header[len(TASK_PAYLOAD_HEADER) :]
            try:
                decoded = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
                raw_payload = json.loads(decoded)
                if isinstance(raw_payload, dict):
                    task_payload = raw_payload
            except Exception:
                logger.exception("Invalid task payload header in worker task")
                task_payload = None
        else:
            # Unknown preamble line; stop parsing and preserve remaining payload.
            payload = header + "\n" + payload
            break

    return TaskHeaders(
        save_path=save_path,
        emit_image=emit_image,
        task_kind=task_kind,
        task_payload=task_payload,
        code=payload,
    )


def _close_remote_session_entry(entry: RemoteSessionEntry) -> None:
    """Best-effort cleanup for cached remote session resources."""
    try:
        RemoteAccessSession(entry.filesystem).close()
    except Exception:
        logger.exception("Failed to close remote session for %s", entry.descriptor_hash)


def _send_remote_status(
    phase: str,
    *,
    session_id: str,
    descriptor_hash: str,
    message: str,
) -> None:
    """Emit a structured remote-status update to the GUI."""
    send_to_gui(
        "REMOTE_STATUS",
        {
            "phase": phase,
            "session_id": session_id,
            "descriptor_hash": descriptor_hash,
            "message": message,
        },
    )


def _cleanup_remote_session_pool() -> None:
    """Evict expired or excess cached sessions."""
    now = time.monotonic()

    expired_keys = [
        key
        for key, entry in remote_session_pool.items()
        if (now - entry.last_used) > REMOTE_SESSION_TTL_SECONDS
    ]
    for key in expired_keys:
        entry = remote_session_pool.pop(key)
        _close_remote_session_entry(entry)

    if len(remote_session_pool) <= REMOTE_SESSION_MAX:
        return

    by_age = sorted(remote_session_pool.items(), key=lambda item: item[1].last_used)
    for key, entry in by_age[: max(0, len(remote_session_pool) - REMOTE_SESSION_MAX)]:
        remote_session_pool.pop(key, None)
        _close_remote_session_entry(entry)


def _prepare_remote_session(
    *,
    session_id: str,
    descriptor_hash: str,
    descriptor: dict[str, Any],
) -> RemoteSessionEntry:
    """Prepare or reuse a cached worker-side remote filesystem session."""
    _cleanup_remote_session_pool()

    entry = remote_session_pool.get(descriptor_hash)
    if entry is not None:
        entry.session_id = session_id
        entry.descriptor = descriptor
        entry.last_used = time.monotonic()
        _send_remote_status(
            "ready",
            session_id=session_id,
            descriptor_hash=descriptor_hash,
            message="Remote worker session reused.",
        )
        return entry

    _send_remote_status(
        "preparing",
        session_id=session_id,
        descriptor_hash=descriptor_hash,
        message="Preparing remote worker session...",
    )
    spec = descriptor_to_spec(descriptor)
    filesystem = create_filesystem(
        spec,
        log=lambda message: _send_remote_status(
            "preparing",
            session_id=session_id,
            descriptor_hash=descriptor_hash,
            message=message,
        ),
        cache=descriptor.get("cache") if isinstance(descriptor.get("cache"), dict) else None,
    )
    entry = RemoteSessionEntry(
        session_id=session_id,
        descriptor_hash=descriptor_hash,
        descriptor=descriptor,
        filesystem=filesystem,
    )
    remote_session_pool[descriptor_hash] = entry
    _cleanup_remote_session_pool()
    _send_remote_status(
        "ready",
        session_id=session_id,
        descriptor_hash=descriptor_hash,
        message="Remote worker session ready.",
    )
    return entry


def _release_remote_session(*, session_id: str, descriptor_hash: str) -> None:
    """Release a cached session when the UI no longer needs it."""
    entry = remote_session_pool.get(descriptor_hash)
    if entry is None:
        _send_remote_status(
            "released",
            session_id=session_id,
            descriptor_hash=descriptor_hash,
            message="Remote worker session already absent.",
        )
        return

    remote_session_pool.pop(descriptor_hash, None)
    _close_remote_session_entry(entry)
    _send_remote_status(
        "released",
        session_id=session_id,
        descriptor_hash=descriptor_hash,
        message="Remote worker session released.",
    )


def _read_remote_fields(
    *,
    entry: RemoteSessionEntry,
    descriptor: dict[str, Any],
    datasets: str | list[str],
):
    """Read remote fields using the warmed filesystem and dataset path(s)."""
    session = RemoteAccessSession(entry.filesystem)
    normalized_datasets = _normalize_remote_datasets_for_cf_read(
        descriptor=descriptor,
        datasets=datasets,
    )
    return session.read_fields(
        descriptor=descriptor,
        datasets=normalized_datasets,
        reader=cf.read,
    )


def _normalize_remote_datasets_for_cf_read(
    *,
    descriptor: dict[str, Any],
    datasets: str | list[str],
) -> str | list[str]:
    """Normalize remote dataset paths to forms cf.read can open with a filesystem."""
    normalized = _normalize_remote_datasets_for_cf_read_shared(
        descriptor=descriptor,
        datasets=datasets,
    )

    logger.info(
        "REMOTE_OPEN normalized HTTP datasets from %r to %r",
        datasets,
        normalized,
    )
    return normalized


def _apply_worker_logging_configuration(
    *,
    level: int | str | None = None,
    trace_remote_fs: bool | None = None,
    trace_remote_file_io: bool | None = None,
) -> None:
    """Apply runtime logging settings for the worker and shared remote access."""
    config = RemoteAccessSession.configure_logging(
        level=level,
        trace_filesystem=trace_remote_fs,
        trace_file_io=trace_remote_file_io,
    )
    apply_runtime_log_level(config.level)
    logging.getLogger("pyfive").setLevel(config.level)
    logger.info(
        "Logging configuration updated level=%s trace_remote_fs=%s trace_remote_file_io=%s",
        logging.getLevelName(config.level),
        config.trace_filesystem,
        config.trace_file_io,
    )


def _handle_control_task(task_kind: str, task_payload: dict[str, Any] | None) -> None:
    """Execute a typed worker control task."""
    payload = task_payload or {}
    session_id = str(payload.get("session_id", ""))
    descriptor_hash = str(payload.get("descriptor_hash", ""))
    descriptor = payload.get("descriptor")

    if task_kind == "REMOTE_PREPARE":
        if not isinstance(descriptor, dict) or not session_id or not descriptor_hash:
            raise ValueError("REMOTE_PREPARE requires session_id, descriptor_hash, and descriptor")
        _prepare_remote_session(
            session_id=session_id,
            descriptor_hash=descriptor_hash,
            descriptor=descriptor,
        )
        return

    if task_kind == "REMOTE_RELEASE":
        if not session_id or not descriptor_hash:
            raise ValueError("REMOTE_RELEASE requires session_id and descriptor_hash")
        _release_remote_session(session_id=session_id, descriptor_hash=descriptor_hash)
        return

    if task_kind == "LOGGING_CONFIGURE":
        _apply_worker_logging_configuration(
            level=payload.get("level"),
            trace_remote_fs=payload.get("trace_remote_fs"),
            trace_remote_file_io=payload.get("trace_remote_file_io"),
        )
        send_to_gui("STATUS:Logging configuration updated")
        return

    if task_kind == "REMOTE_LIST":
        if not isinstance(descriptor, dict) or not session_id or not descriptor_hash:
            raise ValueError("REMOTE_LIST requires session_id, descriptor_hash, and descriptor")
        path = str(payload.get("path", ""))
        # Reuse an already-warm session without sending redundant REMOTE_STATUS messages.
        entry = remote_session_pool.get(descriptor_hash)
        if entry is None:
            entry = _prepare_remote_session(
                session_id=session_id,
                descriptor_hash=descriptor_hash,
                descriptor=descriptor,
            )
        else:
            entry.last_used = time.monotonic()
        try:
            session = RemoteAccessSession(entry.filesystem)
            entries = session.list_entries(path)
            send_to_gui("REMOTE_LIST_RESULT", {
                "path": path,
                "entries": entries,
                "error": None,
            })
        except Exception as exc:
            send_to_gui("REMOTE_LIST_RESULT", {
                "path": path,
                "entries": [],
                "error": str(exc),
            })
        return

    if task_kind == "REMOTE_OPEN":
        if not isinstance(descriptor, dict) or not session_id or not descriptor_hash:
            raise ValueError("REMOTE_OPEN requires session_id, descriptor_hash, and descriptor")

        uri = str(payload.get("uri", ""))
        raw_paths = payload.get("paths")
        if isinstance(raw_paths, list):
            paths = [str(item) for item in raw_paths if str(item)]
        else:
            path = str(payload.get("path", ""))
            paths = [path] if path else []
        if not uri or not paths:
            raise ValueError("REMOTE_OPEN requires uri and at least one path")
        datasets: str | list[str] = paths[0] if len(paths) == 1 else paths

        _send_remote_status(
            "preparing",
            session_id=session_id,
            descriptor_hash=descriptor_hash,
            message=f"Opening remote file: {uri}",
        )
        entry = _prepare_remote_session(
            session_id=session_id,
            descriptor_hash=descriptor_hash,
            descriptor=descriptor,
        )
        entry.last_used = time.monotonic()
        fields = _read_remote_fields(
            entry=entry,
            descriptor=descriptor,
            datasets=datasets,
        )

        worker_globals["_cfview_file_path"] = uri
        worker_globals["_cfview_field_index"] = None
        worker_globals["_cfview_remote_descriptor"] = descriptor
        worker_globals["f"] = fields

        send_to_gui("METADATA", xconv_cf_interface.field_info(fields))
        send_to_gui(
            "REMOTE_OPEN_RESULT",
            {
                "session_id": session_id,
                "uri": uri,
                "ok": True,
            },
        )
        _send_remote_status(
            "ready",
            session_id=session_id,
            descriptor_hash=descriptor_hash,
            message=f"Remote file loaded: {uri}",
        )
        return

    raise ValueError(f"Unknown worker control task kind: {task_kind}")


def _build_saved_plot_script(exec_code: str) -> str:
    """Build a reproducible script with worker state preamble plus plot code."""
    lines: list[str] = [
        "from __future__ import annotations",
        "import cf",
        "import cfplot as cfp",
        "from matplotlib import pyplot as plt",
    ]

    helper_sources: dict[str, str] = {}
    for name in INTERFACE_EXPORTS:
        obj = getattr(xconv_cf_interface, name, None)
        if obj is None or not callable(obj):
            continue
        try:
            helper_sources[name] = textwrap.dedent(inspect.getsource(obj)).rstrip()
        except (OSError, TypeError):
            logger.exception("Unable to inline helper source for %s", name)

    needed_helpers: set[str] = {
        name for name in helper_sources if re.search(rf"\b{re.escape(name)}\b", exec_code)
    }

    # Include transitive helper references so inlined functions stay runnable.
    queue = list(needed_helpers)
    while queue:
        name = queue.pop()
        source = helper_sources.get(name, "")
        for candidate in helper_sources:
            if candidate == name or candidate in needed_helpers:
                continue
            if re.search(rf"\b{re.escape(candidate)}\b", source):
                needed_helpers.add(candidate)
                queue.append(candidate)

    # Collect auxiliary functions from cell_method_handler that are referenced by
    # inlined helpers but are not exported from xconv_cf_interface directly.
    aux_module_funcs: list[tuple[str, object]] = [
        (name, obj)
        for name, obj in vars(xconv_cell_method_handler).items()
        if callable(obj) and not name.startswith("_")
    ]
    aux_sources: dict[str, str] = {}
    for name, obj in aux_module_funcs:
        try:
            aux_sources[name] = textwrap.dedent(inspect.getsource(obj)).rstrip()
        except (OSError, TypeError):
            pass

    # Extend the transitive scan to also catch aux functions referenced by helpers.
    queue = list(needed_helpers)
    while queue:
        name = queue.pop()
        source = helper_sources.get(name, "")
        for candidate in aux_sources:
            if candidate in needed_helpers:
                continue
            if re.search(rf"\b{re.escape(candidate)}\b", source):
                needed_helpers.add(candidate)
                queue.append(candidate)

    include_lineplot_class = (
        "run_line_plot" in needed_helpers
        or bool(re.search(r"\bLinePlot\b", exec_code))
    )
    if include_lineplot_class:
        lines.extend([
            "import numpy as np",
            "import pandas as pd",
            "",
            "# Inlined LinePlot class from xconv2.lineplot for standalone execution.",
            "",
        ])
        try:
            lines.append(textwrap.dedent(inspect.getsource(xconv_lineplot.LinePlot)).rstrip())
            lines.append("")
        except (OSError, TypeError):
            logger.exception("Unable to inline helper source for LinePlot")
            lines.append("# NOTE: helper source unavailable for LinePlot")
            lines.append("")

    # Inline auxiliary helpers (e.g. from cell_method_handler) referenced transitively.
    needed_aux = {name for name in aux_sources if name in needed_helpers}
    if needed_aux:
        lines.extend([
            "",
            "# Inlined auxiliary helpers for standalone execution.",
            "",
        ])
        for name in sorted(needed_aux):
            source = aux_sources[name]
            lines.append(source)
            lines.append("")

    lines.extend([
        "",
        "# Inlined helpers from xconv2.xconv_cf_interface for standalone execution.",
        "",
    ])

    for name in INTERFACE_EXPORTS:
        if name not in needed_helpers:
            continue
        source = helper_sources.get(name)
        if source is None:
            lines.append(f"# NOTE: helper source unavailable for {name}")
            lines.append("")
            continue
        try:
            lines.append(source)
            lines.append("")
        except Exception:
            logger.exception("Unable to append helper source for %s", name)
            lines.append(f"# NOTE: helper source unavailable for {name}")
            lines.append("")

    source_path = worker_globals.get("_cfview_file_path")
    if isinstance(source_path, str) and source_path:
        lines.append(f"f = cf.read({source_path!r})")
    else:
        lines.append("# NOTE: source file path unavailable in worker state")

    field_index = worker_globals.get("_cfview_field_index")
    if isinstance(field_index, int):
        lines.append(f"fld = f[{field_index}]")
    else:
        lines.append("# NOTE: field index unavailable; select a field before saving code")

    # Drop GUI-only task lines from the saved standalone script.
    save_exec_code = "\n".join(
        line for line in exec_code.splitlines() if OMIT4SAVE_TOKEN not in line
    ).rstrip()
    lines.append("")
    lines.append(save_exec_code)
    lines.append("")
    lines.append("plt.show(block=True)")
    lines.append("")
    return "\n".join(lines)


def _emit_latest_plot_image() -> None:
    """Send the latest matplotlib figure to GUI as PNG bytes, if available."""
    fig_numbers = plt.get_fignums()
    logger.info(
        "PLOT_DIAG worker_emit pid=%s backend=%s fig_count=%d",
        os.getpid(),
        matplotlib.get_backend(),
        len(fig_numbers),
    )
    if not fig_numbers:
        return

    fig = plt.figure(fig_numbers[-1])
    buffer = BytesIO()
    dpi = fig.get_dpi() if hasattr(fig, "get_dpi") else 120
    fig.savefig(buffer, format="png", dpi=dpi)
    buffer.seek(0)
    send_to_gui("IMG_READY", buffer.getvalue())
    buffer.close()
    plt.close("all")


def main():
    """Entry point for the cf-worker command."""
    log_file = configure_logging()
    _apply_worker_logging_configuration(level=logging.INFO)

    logger.info("Worker starting")
    logger.info("Log file: %s", log_file)
    logger.info(
        "PLOT_DIAG worker_runtime version=%s module_dir=%s backend=%s",
        __version__,
        Path(__file__).resolve().parent,
        matplotlib.get_backend(),
    )

    # Expose helper in the exec namespace so GUI-issued tasks can emit messages.
    worker_globals['send_to_gui'] = send_to_gui
    send_to_gui("STATUS:Worker Initialized (Pure-Python/pyfive)")

    current_block = []

    while True:
        line = sys.stdin.readline()
        if not line:
            logger.info("Worker stdin closed; shutting down")
            break

        if line.strip() == "#END_TASK":
            code = "".join(current_block)
            headers = _extract_task_headers(code)
            save_path, emit_image, task_kind, task_payload, exec_code = (
                headers.save_path,
                headers.emit_image,
                headers.task_kind,
                headers.task_payload,
                headers.code,
            )
            # Some dependency paths can adjust logger levels at runtime.
            # Re-assert the runtime logging policy before each task.
            _apply_worker_logging_configuration()
            logger.info("Executing task block (%d lines, %d chars)", len(current_block), len(exec_code))

            if task_kind is not None:
                task_start = time.monotonic()
                try:
                    _handle_control_task(task_kind, task_payload)
                    send_to_gui("STATUS:Task Complete")
                    logger.info(
                        "Control task complete kind=%s elapsed=%.3fs",
                        task_kind,
                        time.monotonic() - task_start,
                    )
                except Exception:
                    err = traceback.format_exc()
                    error_line = err.splitlines()[-1]
                    send_to_gui(
                        "REMOTE_OPEN_RESULT",
                        {
                            "session_id": str((task_payload or {}).get("session_id", "")),
                            "uri": str((task_payload or {}).get("uri", "")),
                            "ok": False,
                            "error": error_line,
                        },
                    )
                    descriptor_hash = str((task_payload or {}).get("descriptor_hash", ""))
                    session_id = str((task_payload or {}).get("session_id", ""))
                    if descriptor_hash and session_id:
                        _send_remote_status(
                            "failed",
                            session_id=session_id,
                            descriptor_hash=descriptor_hash,
                            message=error_line,
                        )
                    send_to_gui(f"STATUS:Error - {error_line}")
                    logger.exception("Control task failed: %s", task_kind)
                current_block = []
                continue

            if save_path:
                try:
                    destination = Path(save_path).expanduser()
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    script_text = _build_saved_plot_script(exec_code)
                    destination.write_text(script_text, encoding="utf-8")
                    send_to_gui(f"STATUS:Saved plot code: {destination}")
                    logger.info("Saved plot code to %s", destination)
                except OSError:
                    logger.exception("Failed to save plot code to %s", save_path)
                    send_to_gui(f"STATUS:Error - failed to save plot code: {save_path}")

            try:
                # Execute the code block in our persistent global namespace
                logger.info(
                    "PLOT_DIAG worker_exec_start pid=%s backend=%s emit_image=%s",
                    os.getpid(),
                    matplotlib.get_backend(),
                    emit_image,
                )
                exec(exec_code, worker_globals)
                if emit_image:
                    _emit_latest_plot_image()
                send_to_gui("STATUS:Task Complete")
                logger.info("Task complete")
            except Exception:
                # Send the full error back to the GUI for debugging
                err = traceback.format_exc()
                send_to_gui(f"STATUS:Error - {err.splitlines()[-1]}")
                logger.exception("Task failed")

            current_block = []
        else:
            current_block.append(line)


if __name__ == "__main__":
    main()
