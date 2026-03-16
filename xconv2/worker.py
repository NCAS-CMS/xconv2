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
import shutil
import textwrap
import time
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

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
from . import __version__
from .ui.remote_file_navigator import create_filesystem, descriptor_to_spec

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


def _extract_task_headers(code: str) -> tuple[str | None, bool, str]:
    """Extract optional task headers and return save path, emit flag, and code."""
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

    return save_path, emit_image, task_kind, task_payload, payload


def _cf_read_supports_filesystem() -> bool:
    """Return True when the installed cf.read accepts a filesystem keyword."""
    try:
        return "filesystem" in inspect.signature(cf.read).parameters
    except Exception:
        return False


def _close_remote_session_entry(entry: RemoteSessionEntry) -> None:
    """Best-effort cleanup for cached remote session resources."""
    filesystem = entry.filesystem
    close = getattr(filesystem, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logger.exception("Failed to close remote filesystem for %s", entry.descriptor_hash)

    jump_client = getattr(filesystem, "_xconv_jump_client", None)
    if jump_client is not None:
        try:
            jump_client.close()
        except Exception:
            logger.exception("Failed to close jump client for %s", entry.descriptor_hash)


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
    uri: str,
    path: str,
):
    """Read remote fields using warmed sessions when possible, else protocol fallbacks."""
    filesystem = entry.filesystem
    protocol = str(descriptor.get("protocol", "")).lower()

    if _cf_read_supports_filesystem():
        return cf.read(path, filesystem=filesystem)

    if protocol == "s3":
        storage_options = descriptor.get("storage_options")
        # cfdm's s3 path handling reads u.path[1:] and ignores URI authority,
        # so use s3:///bucket/key form to keep bucket in the path component.
        cfdm_s3_uri = f"s3:///{path.lstrip('/')}"
        if isinstance(storage_options, dict):
            return cf.read(cfdm_s3_uri, storage_options=storage_options)
        return cf.read(cfdm_s3_uri)

    if protocol == "http":
        return cf.read(uri)

    suffix = Path(path).suffix or ".nc"
    with filesystem.open(path, "rb") as remote_file:
        with NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            shutil.copyfileobj(remote_file, tmp)

    try:
        return cf.read(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.exception("Failed to remove staged remote file %s", tmp_path)


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

    if task_kind == "REMOTE_OPEN":
        if not isinstance(descriptor, dict) or not session_id or not descriptor_hash:
            raise ValueError("REMOTE_OPEN requires session_id, descriptor_hash, and descriptor")

        uri = str(payload.get("uri", ""))
        path = str(payload.get("path", ""))
        if not uri or not path:
            raise ValueError("REMOTE_OPEN requires uri and path")

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
            uri=uri,
            path=path,
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    logger.info("Worker starting")
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
            save_path, emit_image, task_kind, task_payload, exec_code = _extract_task_headers(code)
            logger.info("Executing task block (%d lines, %d chars)", len(current_block), len(exec_code))

            if task_kind is not None:
                try:
                    _handle_control_task(task_kind, task_payload)
                except Exception:
                    err = traceback.format_exc()
                    send_to_gui(
                        "REMOTE_OPEN_RESULT",
                        {
                            "session_id": str((task_payload or {}).get("session_id", "")),
                            "uri": str((task_payload or {}).get("uri", "")),
                            "ok": False,
                            "error": err.splitlines()[-1],
                        },
                    )
                    descriptor_hash = str((task_payload or {}).get("descriptor_hash", ""))
                    session_id = str((task_payload or {}).get("session_id", ""))
                    if descriptor_hash and session_id:
                        _send_remote_status(
                            "failed",
                            session_id=session_id,
                            descriptor_hash=descriptor_hash,
                            message=err.splitlines()[-1],
                        )
                    print(err, file=sys.stderr)
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
                print(err, file=sys.stderr) 
                logger.exception("Task failed")
            
            current_block = [] # Reset for the next GUI command
        else:
            current_block.append(line)

if __name__ == "__main__":
    main()