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
import resource
import uuid
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, NamedTuple

import cf
 
from . import cell_method_handler as xconv_cell_method_handler
from . import __version__
from .logging_utils import apply_scoped_runtime_logging, configure_logging
from .workflow.provenance_export import handle_save_provenance_task as _handle_save_provenance_task_impl
from .remote_access import (
    RemoteAccessSession,
    create_filesystem,
    descriptor_to_spec,
    normalize_remote_datasets_for_cf_read as _normalize_remote_datasets_for_cf_read_shared,
)


logger = logging.getLogger(__name__)
SAVE_TASK_HEADER = "#SAVE_TASK_CODE_PATH_B64:"
EMIT_IMAGE_HEADER = "#EMIT_IMAGE:"
TASK_KIND_HEADER = "#TASK_KIND:"
TASK_PAYLOAD_HEADER = "#TASK_PAYLOAD_B64:"
ANIMATION_HEADER = "#ANIMATION:"
INTERFACE_EXPORTS: tuple[str, ...] = ()
OMIT4SAVE_TOKEN = "#omit4save"
REMOTE_SESSION_TTL_SECONDS = 180.0
REMOTE_SESSION_MAX = 4
_WORKER_RUNTIME_LOADED = False
_MMAP_COUNTER_RE = re.compile(
    r"mmap:\s*(\d+)\s+hits,\s*(\d+)\s+misses,\s*(\d+)\s+total requested bytes",
    re.IGNORECASE,
)
_MMAP_BLOCK_RANGE_RE = re.compile(r"MMap get blocks\s+\d+-\d+\s+\((\d+)-(\d+)\)")


class TaskHeaders(NamedTuple):
    """Parsed preamble headers extracted from a worker task code block."""

    save_path: str | None
    emit_image: bool
    animation_enabled: bool
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
        session: RemoteAccessSession | None = None,
    ) -> None:
        now = time.monotonic()
        self.session_id = session_id
        self.descriptor_hash = descriptor_hash
        self.descriptor = descriptor
        self.filesystem = filesystem
        self.session = session or RemoteAccessSession(filesystem)
        self.created_at = now
        self.last_used = now


class _RemoteOpenCacheDiagnostics:
    """Collect fsspec cache stats for one REMOTE_OPEN operation."""

    def __init__(self) -> None:
        self._first_hits: int | None = None
        self._first_misses: int | None = None
        self._first_requested_bytes: int | None = None
        self._last_hits: int | None = None
        self._last_misses: int | None = None
        self._last_requested_bytes: int | None = None
        self.http_requests = 0
        self.block_fetches = 0
        self.block_fetch_bytes = 0

    def consume(self, record: logging.LogRecord) -> None:
        message = record.getMessage()

        counter_match = _MMAP_COUNTER_RE.search(message)
        if counter_match:
            hits = int(counter_match.group(1))
            misses = int(counter_match.group(2))
            requested_bytes = int(counter_match.group(3))

            if self._first_hits is None:
                self._first_hits = hits
                self._first_misses = misses
                self._first_requested_bytes = requested_bytes

            self._last_hits = hits
            self._last_misses = misses
            self._last_requested_bytes = requested_bytes

        block_match = _MMAP_BLOCK_RANGE_RE.search(message)
        if block_match:
            start = int(block_match.group(1))
            end = int(block_match.group(2))
            if end >= start:
                self.block_fetches += 1
                self.block_fetch_bytes += end - start

        if record.name == "fsspec.http" and message.startswith(("http://", "https://")):
            self.http_requests += 1

    def summary(self) -> dict[str, int | float | bool | None]:
        available = self._first_hits is not None and self._last_hits is not None
        delta_hits: int | None = None
        delta_misses: int | None = None
        delta_requested_bytes: int | None = None
        hit_ratio: float | None = None

        if available:
            first_hits = int(self._first_hits)
            first_misses = int(self._first_misses)
            first_requested = int(self._first_requested_bytes)
            last_hits = int(self._last_hits)
            last_misses = int(self._last_misses)
            last_requested = int(self._last_requested_bytes)

            delta_hits = max(0, last_hits - first_hits)
            delta_misses = max(0, last_misses - first_misses)
            delta_requested_bytes = max(0, last_requested - first_requested)
            total = delta_hits + delta_misses
            if total > 0:
                hit_ratio = delta_hits / total

        return {
            "available": available,
            "first_hits": self._first_hits,
            "first_misses": self._first_misses,
            "first_requested_bytes": self._first_requested_bytes,
            "last_hits": self._last_hits,
            "last_misses": self._last_misses,
            "last_requested_bytes": self._last_requested_bytes,
            "delta_hits": delta_hits,
            "delta_misses": delta_misses,
            "delta_requested_bytes": delta_requested_bytes,
            "hit_ratio": hit_ratio,
            "block_fetches": self.block_fetches,
            "block_fetch_bytes": self.block_fetch_bytes,
            "http_requests": self.http_requests,
        }


@contextmanager
def _capture_remote_open_cache_diagnostics() -> Any:
    """Capture fsspec DEBUG logs and summarize cache activity for one open."""

    diagnostics = _RemoteOpenCacheDiagnostics()

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            diagnostics.consume(record)

    logger_names = ["fsspec", "fsspec.caching", "fsspec.cached", "fsspec.http"]
    states: list[tuple[logging.Logger, logging.Handler, int, bool, bool]] = []

    for name in logger_names:
        target = logging.getLogger(name)
        handler = _CaptureHandler(level=logging.DEBUG)
        previous_level = target.level
        previous_propagate = target.propagate
        previous_disabled = target.disabled

        target.addHandler(handler)
        target.setLevel(logging.DEBUG)
        target.propagate = True
        target.disabled = False
        states.append((target, handler, previous_level, previous_propagate, previous_disabled))

    try:
        yield diagnostics
    finally:
        for target, handler, previous_level, previous_propagate, previous_disabled in states:
            target.removeHandler(handler)
            target.setLevel(previous_level)
            target.propagate = previous_propagate
            target.disabled = previous_disabled


def _log_remote_open_cache_summary(
    *,
    descriptor_hash: str,
    session_reused: bool,
    diagnostics: _RemoteOpenCacheDiagnostics,
    cache_mode: str,
    cache_location: str,
) -> None:
    """Emit a compact one-line summary of cache activity for REMOTE_OPEN."""

    stats = diagnostics.summary()
    if not stats["available"]:
        logger.info(
            "REMOTE_CACHE_SUMMARY descriptor_hash=%s session_reused=%s cache_mode=%s cache_location=%r stats=unavailable block_fetches=%d block_fetch_bytes=%d http_requests=%d",
            descriptor_hash,
            session_reused,
            cache_mode,
            cache_location,
            int(stats["block_fetches"]),
            int(stats["block_fetch_bytes"]),
            int(stats["http_requests"]),
        )
        return

    hit_ratio = stats["hit_ratio"]
    hit_ratio_text = "n/a" if hit_ratio is None else f"{hit_ratio:.3f}"
    logger.info(
        "REMOTE_CACHE_SUMMARY descriptor_hash=%s session_reused=%s cache_mode=%s cache_location=%r mmap_start=%d/%d mmap_end=%d/%d delta_hits=%d delta_misses=%d hit_ratio=%s delta_requested_bytes=%d block_fetches=%d block_fetch_bytes=%d http_requests=%d",
        descriptor_hash,
        session_reused,
        cache_mode,
        cache_location,
        int(stats["first_hits"]),
        int(stats["first_misses"]),
        int(stats["last_hits"]),
        int(stats["last_misses"]),
        int(stats["delta_hits"]),
        int(stats["delta_misses"]),
        hit_ratio_text,
        int(stats["delta_requested_bytes"]),
        int(stats["block_fetches"]),
        int(stats["block_fetch_bytes"]),
        int(stats["http_requests"]),
    )


def _remote_cache_diagnostics_enabled() -> bool:
    """Return whether REMOTE_OPEN cache diagnostics capture is enabled."""
    raw = str(os.environ.get("XCONV2_REMOTE_CACHE_DIAGNOSTICS", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


remote_session_pool: dict[str, RemoteSessionEntry] = {}

_HANDLED_TASK_EXCEPTIONS = (ValueError, IndexError)

# This dictionary persists data (like 'f') between GUI commands.
worker_globals: dict[str, Any] = {'cf': cf}


def _ensure_worker_runtime_loaded() -> None:
    """Load the heavy scientific runtime on demand."""
    global _WORKER_RUNTIME_LOADED
    global INTERFACE_EXPORTS
    global worker_globals
    global matplotlib
    global FigureManagerBase
    global np
    global cfp
    global plt
    global cf_interface
    global xconv_lineplot

    if _WORKER_RUNTIME_LOADED:
        return

    import matplotlib as _matplotlib

    # Worker renders to bytes/files only, so force a headless backend and
    # avoid spawning a separate matplotlib GUI app/window (e.g. extra dock icon).
    _matplotlib.use("Agg", force=True)

    import numpy as _np
    from matplotlib.backend_bases import FigureManagerBase as _FigureManagerBase
    import cfplot as _cfp
    from matplotlib import pyplot as _plt
    from . import cf_interface as _cf_interface
    from .cf_interface import lineplot as _xconv_lineplot

    matplotlib = _matplotlib
    FigureManagerBase = _FigureManagerBase
    np = _np
    cfp = _cfp
    plt = _plt
    cf_interface = _cf_interface
    xconv_lineplot = _xconv_lineplot

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
        logger.exception("Failed to set cfplot viewer=None in worker")

    INTERFACE_EXPORTS = tuple(getattr(cf_interface, "__all__", ()))

    worker_globals.update({"cfp": cfp, "plt": plt, "np": np})
    worker_globals.update({name: getattr(cf_interface, name) for name in INTERFACE_EXPORTS})
    _WORKER_RUNTIME_LOADED = True


def __getattr__(name: str) -> Any:
    """Lazily expose deferred runtime modules while keeping import-time light."""
    if name in {"cf_interface", "xconv_lineplot", "cfp", "plt", "np", "matplotlib", "FigureManagerBase"}:
        _ensure_worker_runtime_loaded()
        value = globals().get(name)
        if value is not None:
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def send_to_gui(prefix, data=None):
    """Helper to format messages for the GUI pipe."""
    if data is not None:
        payload = base64.b64encode(pickle.dumps(data)).decode()
        print(f"{prefix}:{payload}", flush=True)
        logger.debug("Sent message to GUI with payload prefix=%s size=%d", prefix, len(payload))
    else:
        print(prefix, flush=True)
        logger.debug("Sent message to GUI: %s", prefix)


def _worker_rss_mb() -> float:
    """Return current worker RSS in MiB (best effort)."""
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        return 0.0

    # macOS reports bytes; Linux reports KiB.
    if sys.platform == "darwin":
        return float(rss) / (1024.0 * 1024.0)
    return float(rss) / 1024.0


def _log_task_memory(prefix: str, *, started: float, rss_before_mb: float) -> None:
    """Log task timing and RSS deltas for crash/leak diagnostics."""
    rss_after_mb = _worker_rss_mb()
    elapsed = max(0.0, time.monotonic() - started)
    delta = rss_after_mb - rss_before_mb
    logger.info(
        "MEM_DIAG %s elapsed=%.3fs rss_before=%.1fMiB rss_after=%.1fMiB delta=%+.1fMiB",
        prefix,
        elapsed,
        rss_before_mb,
        rss_after_mb,
        delta,
    )


def _extract_task_headers(code: str) -> TaskHeaders:
    """Parse leading ``#``-prefixed control headers from a worker task block.

    Headers are consumed one per line until the first non-header line.  The
    remaining text is the executable code body.

    Returns a :class:`TaskHeaders` named tuple with fields:

    * ``save_path``   – destination path for ``#SAVE_TASK_CODE_PATH_B64:``
    * ``emit_image``  – False when ``#EMIT_IMAGE:0`` is present
    * ``animation_enabled`` – True when ``#ANIMATION:1`` is present
    * ``task_kind``   – value of ``#TASK_KIND:`` (control tasks only)
    * ``task_payload``– decoded JSON dict from ``#TASK_PAYLOAD_B64:``
    * ``code``        – remaining executable code after all headers
    """
    save_path: str | None = None
    emit_image = True
    animation_enabled = False
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
        elif header.startswith(ANIMATION_HEADER):
            animation_enabled = header[len(ANIMATION_HEADER) :] == "1"
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
        animation_enabled=animation_enabled,
        task_kind=task_kind,
        task_payload=task_payload,
        code=payload,
    )


def _close_remote_session_entry(entry: RemoteSessionEntry) -> None:
    """Best-effort cleanup for cached remote session resources."""
    try:
        entry.session.close()
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
        logger.info(
            "REMOTE_SESSION reuse descriptor_hash=%s session_id=%s protocol=%s cache=%r",
            descriptor_hash,
            session_id,
            descriptor.get("protocol"),
            descriptor.get("cache"),
        )
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

    logger.info(
        "REMOTE_SESSION create descriptor_hash=%s session_id=%s protocol=%s cache=%r",
        descriptor_hash,
        session_id,
        descriptor.get("protocol"),
        descriptor.get("cache"),
    )
    started = time.monotonic()
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
    elapsed = max(0.0, time.monotonic() - started)
    _send_remote_status(
        "ready",
        session_id=session_id,
        descriptor_hash=descriptor_hash,
        message=f"Remote worker session ready in {elapsed:.2f}s.",
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
    session_reused: bool = False,
):
    """Read remote fields using the warmed filesystem and dataset path(s)."""
    session = entry.session
    normalized_datasets = _normalize_remote_datasets_for_cf_read(
        descriptor=descriptor,
        datasets=datasets,
    )
    cache_cfg = descriptor.get("cache") if isinstance(descriptor, dict) else None
    if isinstance(cache_cfg, dict):
        cache_mode = str(cache_cfg.get("disk_mode", "Disabled"))
        cache_location = str(cache_cfg.get("disk_location", ""))
    else:
        cache_mode = "Disabled"
        cache_location = ""

    if not _remote_cache_diagnostics_enabled():
        fields = session.read_fields(
            descriptor=descriptor,
            datasets=normalized_datasets,
            reader=cf.read,
        )
        logger.info(
            "REMOTE_CACHE_SUMMARY descriptor_hash=%s session_reused=%s cache_mode=%s cache_location=%r diagnostics=disabled",
            entry.descriptor_hash,
            session_reused,
            cache_mode,
            cache_location,
        )
        return fields

    with _capture_remote_open_cache_diagnostics() as diagnostics:
        fields = session.read_fields(
            descriptor=descriptor,
            datasets=normalized_datasets,
            reader=cf.read,
        )

    _log_remote_open_cache_summary(
        descriptor_hash=entry.descriptor_hash,
        session_reused=session_reused,
        diagnostics=diagnostics,
        cache_mode=cache_mode,
        cache_location=cache_location,
    )
    return fields


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
    scope_levels: dict[str, int | str] | None = None,
) -> None:
    """Apply runtime logging settings for the worker and shared remote access."""
    if scope_levels is None:
        scope_levels = RemoteAccessSession.logging_configuration().scope_levels

    applied = apply_scoped_runtime_logging(scope_levels)
    config = RemoteAccessSession.configure_logging(scope_levels=applied)

    logger.info(
        "Logging configuration updated scopes=%s",
        config.scope_levels,
    )


def _replay_source_files_for_operation(operation: dict[str, Any]) -> list[str]:
    """Return ordered source URIs/paths referenced by one replay operation."""
    kind = str(operation.get("kind", "")).strip().lower()

    if kind in {"unary_xy", "apply_selection", "filter"}:
        source_file = operation.get("source_file")
        if isinstance(source_file, str) and source_file.strip():
            return [source_file.strip()]
        return []

    if kind in {"binary", "regrid"}:
        source_files = operation.get("source_files")
        if isinstance(source_files, list):
            return [str(item).strip() for item in source_files if isinstance(item, str) and str(item).strip()]
        return []

    return []


def _replay_resolve_field_reference_index(
    fields: list,
    provenance: list[dict[str, Any]],
    field_ref: dict[str, Any],
    fallback_index: object,
) -> int | None:
    """Resolve a persisted field reference against current worker replay state."""
    identity_raw = field_ref.get("identity")
    identity = identity_raw.strip() if isinstance(identity_raw, str) else ""

    source_raw = field_ref.get("source_file")
    source_file = source_raw.strip() if isinstance(source_raw, str) else ""

    generated_raw = field_ref.get("generated")
    generated_filter = generated_raw if isinstance(generated_raw, bool) else None

    occurrence_raw = field_ref.get("occurrence")
    try:
        occurrence_target = int(occurrence_raw)
    except (TypeError, ValueError):
        occurrence_target = 1
    if occurrence_target < 1:
        occurrence_target = 1

    if identity:
        rows = cf_interface.field_info(fields)
        seen = 0
        for idx, row in enumerate(rows):
            row_identity_raw = row.get("identity") if isinstance(row, dict) else None
            row_identity = row_identity_raw.strip() if isinstance(row_identity_raw, str) else ""
            if row_identity != identity:
                continue

            prov = provenance[idx] if idx < len(provenance) else {}
            if source_file:
                prov_source_raw = prov.get("source_file") if isinstance(prov, dict) else ""
                prov_source = prov_source_raw.strip() if isinstance(prov_source_raw, str) else ""
                if prov_source != source_file:
                    continue

            if generated_filter is not None:
                prov_generated = bool(prov.get("generated", False)) if isinstance(prov, dict) else False
                if prov_generated != generated_filter:
                    continue

            seen += 1
            if seen == occurrence_target:
                return idx

    if isinstance(fallback_index, int) and 0 <= fallback_index < len(fields):
        return fallback_index
    return None


def _replay_normalize_loaded_fields(loaded: Any) -> list:
    """Normalize cf.read output into a plain list of fields."""
    _ensure_worker_runtime_loaded()
    if isinstance(loaded, cf.FieldList):
        return list(loaded)
    if isinstance(loaded, (list, tuple)):
        return list(loaded)
    return [loaded]


def _handle_replay_fields_task(payload: dict[str, Any]) -> None:
    """Replay persisted field operations entirely on the worker side."""
    _ensure_worker_runtime_loaded()
    operations_raw = payload.get("operations")
    if not isinstance(operations_raw, list) or not operations_raw:
        raise ValueError("REPLAY_FIELDS requires a non-empty operations list")

    operations = [op for op in operations_raw if isinstance(op, dict)]
    if not operations:
        raise ValueError("REPLAY_FIELDS operations list contains no valid mappings")

    replay_sources: list[str] = []
    for operation in operations:
        for source in _replay_source_files_for_operation(operation):
            if source not in replay_sources:
                replay_sources.append(source)

    remote_open_requests_raw = payload.get("remote_open_requests")
    remote_open_requests: dict[str, dict[str, Any]] = {}
    if isinstance(remote_open_requests_raw, list):
        for request in remote_open_requests_raw:
            if not isinstance(request, dict):
                continue
            uri_raw = request.get("uri")
            uri = uri_raw.strip() if isinstance(uri_raw, str) else ""
            if uri:
                remote_open_requests[uri] = request

    fields: list = []
    provenance: list[dict[str, Any]] = []

    for source in replay_sources:
        request = remote_open_requests.get(source)
        if isinstance(request, dict):
            session_id = str(request.get("session_id", "")).strip()
            descriptor_hash = str(request.get("descriptor_hash", "")).strip()
            descriptor = request.get("descriptor")
            paths = request.get("paths")
            if not isinstance(descriptor, dict) or not session_id or not descriptor_hash:
                raise ValueError(f"Replay remote preload request is invalid for source: {source}")
            if isinstance(paths, list):
                datasets = [str(item) for item in paths if str(item)]
            else:
                datasets = []
            if not datasets:
                raise ValueError(f"Replay remote preload paths missing for source: {source}")

            entry = _prepare_remote_session(
                session_id=session_id,
                descriptor_hash=descriptor_hash,
                descriptor=descriptor,
            )
            entry.last_used = time.monotonic()
            loaded_fields = _replay_normalize_loaded_fields(
                _read_remote_fields(
                    entry=entry,
                    descriptor=descriptor,
                    datasets=datasets[0] if len(datasets) == 1 else datasets,
                )
            )
        else:
            loaded_fields = _replay_normalize_loaded_fields(cf.read(source))

        fields.extend(loaded_fields)
        provenance.extend({"source_file": source, "generated": False} for _ in loaded_fields)

    worker_globals["f"] = fields
    worker_globals["_cfview_file_path"] = replay_sources[-1] if replay_sources else ""
    worker_globals["_cfview_field_index"] = None
    initial_rows = cf_interface.field_info(fields)
    for idx, row in enumerate(initial_rows):
        if not isinstance(row, dict):
            continue
        prov = provenance[idx] if idx < len(provenance) else {}
        source_file = prov.get("source_file") if isinstance(prov, dict) else ""
        generated = bool(prov.get("generated", False)) if isinstance(prov, dict) else False
        row["source_file"] = source_file if isinstance(source_file, str) else ""
        row["generated"] = generated
    send_to_gui("METADATA", initial_rows)

    applied = 0
    skipped = 0

    for operation in operations:
        kind = str(operation.get("kind", "")).strip().lower()
        metadata_rows: list[dict[str, object]] | None = None

        if kind == "unary_xy":
            resolved_index = _replay_resolve_field_reference_index(
                fields,
                provenance,
                operation.get("field_ref") if isinstance(operation.get("field_ref"), dict) else {},
                operation.get("field_index"),
            )
            operation_key = operation.get("operation")
            if isinstance(resolved_index, int) and isinstance(operation_key, str) and operation_key.strip():
                metadata_rows = cf_interface.append_unary_xy_field_operation(fields, resolved_index, operation_key)

        elif kind == "binary":
            resolved_a = _replay_resolve_field_reference_index(
                fields,
                provenance,
                operation.get("field_ref_a") if isinstance(operation.get("field_ref_a"), dict) else {},
                operation.get("index_a"),
            )
            resolved_b = _replay_resolve_field_reference_index(
                fields,
                provenance,
                operation.get("field_ref_b") if isinstance(operation.get("field_ref_b"), dict) else {},
                operation.get("index_b"),
            )
            operation_key = operation.get("operation")
            source_files_raw = operation.get("source_files")
            source_files = [str(item) for item in source_files_raw if isinstance(item, str)] if isinstance(source_files_raw, list) else []
            if (
                isinstance(resolved_a, int)
                and isinstance(resolved_b, int)
                and isinstance(operation_key, str)
                and operation_key.strip()
            ):
                metadata_rows = cf_interface.append_binary_field_operation(
                    fields,
                    resolved_a,
                    resolved_b,
                    operation_key,
                    source_files=source_files,
                )

        elif kind == "filter":
            resolved_index = _replay_resolve_field_reference_index(
                fields,
                provenance,
                operation.get("field_ref") if isinstance(operation.get("field_ref"), dict) else {},
                operation.get("field_index"),
            )
            config = operation.get("config")
            if isinstance(resolved_index, int) and isinstance(config, dict):
                metadata_rows = cf_interface.append_filter_field_operation(
                    fields,
                    resolved_index,
                    config,
                )

        elif kind == "apply_selection":
            resolved_index = _replay_resolve_field_reference_index(
                fields,
                provenance,
                operation.get("field_ref") if isinstance(operation.get("field_ref"), dict) else {},
                operation.get("field_index"),
            )
            selections = operation.get("selections")
            collapse_by_coord = operation.get("collapse_by_coord")
            if (
                isinstance(resolved_index, int)
                and isinstance(selections, dict)
                and isinstance(collapse_by_coord, dict)
            ):
                metadata_rows = cf_interface.append_selection_field_operation(
                    fields,
                    resolved_index,
                    selections,
                    collapse_by_coord,
                )

        elif kind == "regrid":
            config = operation.get("config")
            if isinstance(config, dict):
                config_copy = dict(config)
                field_refs = operation.get("field_refs")
                if isinstance(field_refs, list) and field_refs:
                    resolved_indices: list[int] = []
                    for raw_ref in field_refs:
                        if not isinstance(raw_ref, dict):
                            continue
                        resolved = _replay_resolve_field_reference_index(fields, provenance, raw_ref, None)
                        if isinstance(resolved, int):
                            resolved_indices.append(resolved)
                    if resolved_indices:
                        config_copy["field_indices"] = resolved_indices

                metadata_rows = cf_interface.regrid_from_config(fields, json.dumps(config_copy, sort_keys=True))

        if isinstance(metadata_rows, list) and metadata_rows:
            for row in metadata_rows:
                if isinstance(row, dict):
                    row["source_file"] = ""
                    row["generated"] = True
            send_to_gui("METADATA_APPEND", metadata_rows)
            provenance.extend({"source_file": "", "generated": True} for _ in metadata_rows)
            applied += 1
        else:
            skipped += 1

    send_to_gui(f"STATUS:Replay complete: applied {applied} operation(s), skipped {skipped}.")


def _handle_save_provenance_task(payload: dict[str, Any]) -> None:
    """Build field-specific provenance and save to disk in requested format."""
    status_message = _handle_save_provenance_task_impl(
        payload,
        replay_source_files_for_operation=_replay_source_files_for_operation,
        prepare_remote_session=_prepare_remote_session,
        replay_normalize_loaded_fields=_replay_normalize_loaded_fields,
        read_remote_fields=_read_remote_fields,
        resolve_field_reference_index=_replay_resolve_field_reference_index,
    )
    send_to_gui(status_message)


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
            scope_levels=payload.get("scope_levels") if isinstance(payload.get("scope_levels"), dict) else None,
        )
        send_to_gui("STATUS:Logging configuration updated")
        return

    if task_kind == "REMOTE_LIST":
        if not isinstance(descriptor, dict) or not session_id or not descriptor_hash:
            raise ValueError("REMOTE_LIST requires session_id, descriptor_hash, and descriptor")
        path = str(payload.get("path", ""))
        list_started = time.monotonic()
        _send_remote_status(
            "preparing",
            session_id=session_id,
            descriptor_hash=descriptor_hash,
            message=f"Listing remote path: {path or '/'}",
        )
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
            entries = entry.session.list_entries(path)
            elapsed = max(0.0, time.monotonic() - list_started)
            _send_remote_status(
                "preparing",
                session_id=session_id,
                descriptor_hash=descriptor_hash,
                message=f"Listed {len(entries)} entries from {path or '/'} in {elapsed:.2f}s",
            )
            send_to_gui("REMOTE_LIST_RESULT", {
                "path": path,
                "entries": entries,
                "error": None,
            })
        except Exception as exc:
            elapsed = max(0.0, time.monotonic() - list_started)
            _send_remote_status(
                "failed",
                session_id=session_id,
                descriptor_hash=descriptor_hash,
                message=f"Listing failed for {path or '/'} after {elapsed:.2f}s: {exc}",
            )
            send_to_gui("REMOTE_LIST_RESULT", {
                "path": path,
                "entries": [],
                "error": str(exc),
            })
        return

    if task_kind == "REMOTE_OPEN":
        if not isinstance(descriptor, dict) or not session_id or not descriptor_hash:
            raise ValueError("REMOTE_OPEN requires session_id, descriptor_hash, and descriptor")

        _ensure_worker_runtime_loaded()

        uri = str(payload.get("uri", ""))
        append = bool(payload.get("append", False))
        raw_paths = payload.get("paths")
        if isinstance(raw_paths, list):
            paths = [str(item) for item in raw_paths if str(item)]
        else:
            path = str(payload.get("path", ""))
            paths = [path] if path else []
        if not uri or not paths:
            raise ValueError("REMOTE_OPEN requires uri and at least one path")
        datasets: str | list[str] = paths[0] if len(paths) == 1 else paths

        logger.info(
            "REMOTE_OPEN request session_id=%s descriptor_hash=%s uri=%s datasets=%r cache=%r",
            session_id,
            descriptor_hash,
            uri,
            datasets,
            descriptor.get("cache") if isinstance(descriptor, dict) else None,
        )

        _send_remote_status(
            "preparing",
            session_id=session_id,
            descriptor_hash=descriptor_hash,
            message=f"Opening remote file: {uri}",
        )
        session_reused = descriptor_hash in remote_session_pool
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
            session_reused=session_reused,
        )

        worker_globals["_cfview_file_path"] = uri
        worker_globals["_cfview_field_index"] = None
        worker_globals["_cfview_remote_descriptor"] = descriptor
        if append:
            existing = worker_globals.get("f")
            if existing is None:
                worker_globals["f"] = fields
            else:
                existing.extend(fields)
                worker_globals["f"] = existing
            send_to_gui("METADATA", cf_interface.field_info(fields))
        else:
            worker_globals["f"] = fields
            send_to_gui("METADATA", cf_interface.field_info(fields))
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

    if task_kind == "REPLAY_FIELDS":
        _handle_replay_fields_task(payload)
        return

    if task_kind == "SAVE_PROVENANCE":
        _handle_save_provenance_task(payload)
        return

    raise ValueError(f"Unknown worker control task kind: {task_kind}")


def _build_saved_plot_script(exec_code: str) -> str:
    """Build a reproducible script with worker state preamble plus plot code."""
    _ensure_worker_runtime_loaded()
    lines: list[str] = [
        "from __future__ import annotations",
        "import cf",
        "import cfplot as cfp",
        "from matplotlib import pyplot as plt",
    ]

    helper_sources: dict[str, str] = {}
    for name in INTERFACE_EXPORTS:
        obj = getattr(cf_interface, name, None)
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
    # inlined helpers but are not exported from cf_interface directly.
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
            "# Inlined LinePlot class from xconv2.cf_interface.lineplot for standalone execution.",
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
        "# Inlined helpers from xconv2.cf_interface for standalone execution.",
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
    _ensure_worker_runtime_loaded()
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


def _emit_animation_start(
    request_id: str,
    session_id: str | None,
    total_frames: int | None,
    fps_hint: float | None,
    title_template: str | None,
) -> None:
    """Notify GUI of animation session start."""
    import time

    payload = {
        "request_id": request_id,
        "session_id": session_id,
        "total_frames": total_frames,
        "fps_hint": fps_hint,
        "title_template": title_template,
        "started_at": time.time(),
    }
    send_to_gui("ANIM_START", payload)


def _emit_animation_frame(
    request_id: str,
    session_id: str | None,
    frame_index: int,
    total_frames: int | None,
    frame_value_label: str | None = None,
) -> None:
    """Emit a single animation frame to GUI."""
    import time

    _ensure_worker_runtime_loaded()
    fig_numbers = plt.get_fignums()
    if not fig_numbers:
        logger.warning("No matplotlib figure available for animation frame")
        return

    fig = plt.figure(fig_numbers[-1])
    buffer = BytesIO()
    dpi = fig.get_dpi() if hasattr(fig, "get_dpi") else 120
    fig.savefig(buffer, format="png", dpi=dpi)
    buffer.seek(0)
    png_bytes = buffer.getvalue()
    buffer.close()

    payload = {
        "request_id": request_id,
        "session_id": session_id,
        "frame_index": frame_index,
        "total_frames": total_frames,
        "png_bytes": png_bytes,
        "frame_value_label": frame_value_label,
        "emitted_at": time.time(),
    }
    send_to_gui("ANIM_FRAME", payload)
    logger.info(
        "ANIM_DIAG worker_emit_frame request_id=%s frame_index=%s png_bytes=%d",
        request_id,
        frame_index,
        len(png_bytes),
    )


def _emit_animation_end(request_id: str, session_id: str | None, frames_emitted: int) -> None:
    """Notify GUI of animation session completion."""
    import time

    payload = {
        "request_id": request_id,
        "session_id": session_id,
        "frames_emitted": frames_emitted,
        "completed_at": time.time(),
    }
    send_to_gui("ANIM_END", payload)


def _emit_animation_error(
    request_id: str, session_id: str | None, frame_index: int | None, error_message: str
) -> None:
    """Notify GUI of animation session error."""
    import time

    payload = {
        "request_id": request_id,
        "session_id": session_id,
        "frame_index": frame_index,
        "error": error_message,
        "failed_at": time.time(),
    }
    send_to_gui("ANIM_ERROR", payload)


def _configure_animation_streaming_for_exec() -> tuple[Callable[[str | None], None], str]:
    """Install temporary cf-plot wrappers that emit ANIM_* messages for this task."""
    _ensure_worker_runtime_loaded()

    request_id = str(uuid.uuid4())
    session_id = request_id
    original_gopen = cfp.gopen
    original_con = cfp.con

    state: dict[str, object] = {
        "started": False,
        "frames_emitted": 0,
        "total_frames": None,
        "fps_hint": None,
        "title_template": None,
    }

    def _emit_start_if_needed() -> None:
        if bool(state["started"]):
            return
        _emit_animation_start(
            request_id=request_id,
            session_id=session_id,
            total_frames=state["total_frames"] if isinstance(state["total_frames"], int) else None,
            fps_hint=float(state["fps_hint"]) if isinstance(state["fps_hint"], (int, float)) else None,
            title_template=str(state["title_template"]) if isinstance(state["title_template"], str) else None,
        )
        state["started"] = True

    def _on_meta(meta: dict[str, object]) -> None:
        if isinstance(meta, dict):
            total_frames = meta.get("total_frames")
            if isinstance(total_frames, int):
                state["total_frames"] = total_frames
            fps_hint = meta.get("fps_hint")
            if isinstance(fps_hint, (int, float)):
                state["fps_hint"] = float(fps_hint)
            title_template = meta.get("title_template")
            if isinstance(title_template, str):
                state["title_template"] = title_template
        _emit_start_if_needed()

    def _on_frame(frame_event: dict[str, object]) -> None:
        _emit_start_if_needed()

        frame_index = int(state["frames_emitted"])
        frame_value_label: str | None = None
        if isinstance(frame_event, dict):
            raw_index = frame_event.get("frame_index")
            if isinstance(raw_index, int):
                frame_index = raw_index

            raw_frame_value = frame_event.get("frame_value")
            if raw_frame_value is not None:
                frame_value_label = str(raw_frame_value)

        _emit_animation_frame(
            request_id=request_id,
            session_id=session_id,
            frame_index=frame_index,
            total_frames=state["total_frames"] if isinstance(state["total_frames"], int) else None,
            frame_value_label=frame_value_label,
        )

        next_count = frame_index + 1
        if next_count > int(state["frames_emitted"]):
            state["frames_emitted"] = next_count

    def _wrapped_gopen(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("animation_session_id", session_id)
        kwargs.setdefault("animation_meta_callback", _on_meta)
        kwargs.setdefault("animation_frame_callback", _on_frame)
        return original_gopen(*args, **kwargs)

    def _wrapped_con(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("animation", True)
        animation_reference = worker_globals.get("f")
        if animation_reference is not None:
            kwargs.setdefault("animation_reference", animation_reference)
        kwargs.setdefault("reuse_map_background", True)
        kwargs.setdefault("clear_previous_frame", True)
        kwargs.setdefault("animation_axis", "auto")
        kwargs.setdefault("animation_title_template", "{title} [{frame}]")
        try:
            return original_con(*args, **kwargs)
        except Warning as exc:
            message = str(exc).lower()
            if "too many dimensions" not in message:
                raise

            if not args:
                raise

            first_arg = args[0]
            squeeze_method = getattr(first_arg, "squeeze", None)
            if not callable(squeeze_method):
                raise

            squeezed = squeeze_method()
            if squeezed is first_arg:
                raise

            logger.info("ANIM_DIAG retrying contour render with squeezed field after dimensionality warning")
            retry_args = (squeezed, *args[1:])
            return original_con(*retry_args, **kwargs)

    cfp.gopen = _wrapped_gopen
    cfp.con = _wrapped_con

    def _finalize(error_message: str | None = None) -> None:
        cfp.gopen = original_gopen
        cfp.con = original_con
        if error_message is not None:
            _emit_animation_error(
                request_id=request_id,
                session_id=session_id,
                frame_index=None,
                error_message=error_message,
            )
            return

        _emit_start_if_needed()
        _emit_animation_end(
            request_id=request_id,
            session_id=session_id,
            frames_emitted=int(state["frames_emitted"]),
        )

    return _finalize, request_id


def main():
    """Entry point for the cf-worker command."""
    log_file = configure_logging()
    _apply_worker_logging_configuration(
        scope_levels={
            "all": "INFO",
            "xconv2": "INFO",
        }
    )

    logger.info("Worker starting")
    logger.info("Log file: %s", log_file)
    logger.info(
        "PLOT_DIAG worker_runtime version=%s module_dir=%s backend=%s",
        __version__,
        Path(__file__).resolve().parent,
        "deferred",
    )

    # Expose helper in the exec namespace so GUI-issued tasks can emit messages.
    worker_globals['send_to_gui'] = send_to_gui
    # Signal that the lightweight worker control loop is ready. Heavy imports
    # are loaded lazily when the first data/plot task needs them.
    send_to_gui("STATUS:Worker Initialized")
    print("READY", flush=True)
    logger.info("MEM_DIAG worker_initialized rss=%.1fMiB", _worker_rss_mb())

    current_block = []

    while True:
        line = sys.stdin.readline()
        if not line:
            logger.info("Worker stdin closed; shutting down")
            break

        if line.strip() == "#END_TASK":
            code = "".join(current_block)
            headers = _extract_task_headers(code)
            save_path, emit_image, animation_enabled, task_kind, task_payload, exec_code = (
                headers.save_path,
                headers.emit_image,
                headers.animation_enabled,
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
                rss_before_mb = _worker_rss_mb()
                try:
                    _handle_control_task(task_kind, task_payload)
                    send_to_gui("STATUS:Task Complete")
                    logger.info(
                        "Control task complete kind=%s elapsed=%.3fs",
                        task_kind,
                        time.monotonic() - task_start,
                    )
                    _log_task_memory(
                        f"control kind={task_kind}",
                        started=task_start,
                        rss_before_mb=rss_before_mb,
                    )
                except _HANDLED_TASK_EXCEPTIONS as exc:
                    error_line = f"{type(exc).__name__}: {exc}"
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
                    logger.error("Control task handled error kind=%s: %s", task_kind, error_line)
                    _log_task_memory(
                        f"control_failed kind={task_kind}",
                        started=task_start,
                        rss_before_mb=rss_before_mb,
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
                    _log_task_memory(
                        f"control_failed kind={task_kind}",
                        started=task_start,
                        rss_before_mb=rss_before_mb,
                    )
                current_block = []
                continue

            if save_path:
                try:
                    _ensure_worker_runtime_loaded()
                    destination = Path(save_path).expanduser()
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    script_text = _build_saved_plot_script(exec_code)
                    destination.write_text(script_text, encoding="utf-8")
                    send_to_gui(f"STATUS:Saved plot code: {destination}")
                    logger.info("Saved plot code to %s", destination)
                except OSError:
                    logger.exception("Failed to save plot code to %s", save_path)
                    send_to_gui(f"STATUS:Error - failed to save plot code: {save_path}")

            finalize_animation: Callable[[str | None], None] | None = None
            try:
                task_start = time.monotonic()
                rss_before_mb = _worker_rss_mb()
                _ensure_worker_runtime_loaded()
                if animation_enabled:
                    finalize_animation, animation_request_id = _configure_animation_streaming_for_exec()
                    logger.info("ANIM_DIAG worker_animation_enabled request_id=%s", animation_request_id)
                # Execute the code block in our persistent global namespace
                logger.info(
                    "PLOT_DIAG worker_exec_start pid=%s backend=%s emit_image=%s",
                    os.getpid(),
                    matplotlib.get_backend(),
                    emit_image,
                )
                exec(exec_code, worker_globals)
                if finalize_animation is not None:
                    finalize_animation(None)
                    finalize_animation = None
                if emit_image:
                    _emit_latest_plot_image()
                send_to_gui("STATUS:Task Complete")
                logger.info("Task complete")
                _log_task_memory(
                    "exec_task",
                    started=task_start,
                    rss_before_mb=rss_before_mb,
                )
            except _HANDLED_TASK_EXCEPTIONS as exc:
                error_line = f"{type(exc).__name__}: {exc}"
                if finalize_animation is not None:
                    finalize_animation(error_line)
                    finalize_animation = None
                send_to_gui(f"STATUS:Error - {error_line}")
                logger.error("Task handled error: %s", error_line)
                _log_task_memory(
                    "exec_task_failed",
                    started=task_start,
                    rss_before_mb=rss_before_mb,
                )
            except Exception:
                # Send the full error back to the GUI for debugging
                err = traceback.format_exc()
                if finalize_animation is not None:
                    finalize_animation(err.splitlines()[-1])
                    finalize_animation = None
                send_to_gui(f"STATUS:Error - {err.splitlines()[-1]}")
                logger.exception("Task failed")
                _log_task_memory(
                    "exec_task_failed",
                    started=task_start,
                    rss_before_mb=rss_before_mb,
                )

            current_block = []
        else:
            current_block.append(line)


if __name__ == "__main__":
    main()
