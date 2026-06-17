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
from io import BytesIO
from pathlib import Path
from typing import Any, NamedTuple

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
INTERFACE_EXPORTS: tuple[str, ...] = ()
OMIT4SAVE_TOKEN = "#omit4save"
REMOTE_SESSION_TTL_SECONDS = 180.0
REMOTE_SESSION_MAX = 4
_WORKER_RUNTIME_LOADED = False


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
):
    """Read remote fields using the warmed filesystem and dataset path(s)."""
    session = entry.session
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

    if task_kind == "ANIM_SYNTHETIC":
        _handle_synthetic_animation_task(payload)
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


def _handle_synthetic_animation_task(payload: dict[str, Any]) -> None:
    """Emit synthetic animation frames for GUI playback integration tests."""
    _ensure_worker_runtime_loaded()

    request_id = str(payload.get("request_id") or f"anim-{int(time.time() * 1000)}")
    session_id = str(payload.get("session_id") or request_id)

    try:
        fps_hint = float(payload.get("fps_hint", 8.0) or 8.0)
    except (TypeError, ValueError):
        fps_hint = 8.0
    fps_hint = max(1.0, min(30.0, fps_hint))

    frame_count_raw = payload.get("frame_count", 18)
    try:
        frame_count = int(frame_count_raw)
    except (TypeError, ValueError):
        frame_count = 18
    frame_count = max(2, min(240, frame_count))

    title_template = str(payload.get("title_template") or "Synthetic Animation Preview")

    fig = None
    try:
        _emit_animation_start(
            request_id=request_id,
            session_id=session_id,
            total_frames=frame_count,
            fps_hint=fps_hint,
            title_template=title_template,
        )

        fig = plt.figure(figsize=(6.8, 4.2))
        ax = fig.add_subplot(111)
        x = np.linspace(0.0, 2.0 * np.pi, 240)

        for frame_index in range(frame_count):
            phase = (2.0 * np.pi * frame_index) / frame_count
            y = np.sin(x + phase)

            ax.clear()
            ax.plot(x, y, color="#1f77b4", linewidth=2.0)
            ax.set_ylim(-1.2, 1.2)
            ax.set_xlim(0.0, float(2.0 * np.pi))
            ax.grid(True, alpha=0.25)
            ax.set_xlabel("x")
            ax.set_ylabel("sin(x + phase)")
            ax.set_title(f"{title_template} - Frame {frame_index + 1}/{frame_count}")
            fig.tight_layout()

            _emit_animation_frame(
                request_id=request_id,
                session_id=session_id,
                frame_index=frame_index,
                total_frames=frame_count,
                frame_value_label=f"{frame_index + 1}/{frame_count}",
            )

        _emit_animation_end(
            request_id=request_id,
            session_id=session_id,
            frames_emitted=frame_count,
        )
    except Exception as exc:
        _emit_animation_error(
            request_id=request_id,
            session_id=session_id,
            frame_index=None,
            error_message=str(exc),
        )
        raise
    finally:
        if fig is not None:
            plt.close(fig)


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

            try:
                task_start = time.monotonic()
                rss_before_mb = _worker_rss_mb()
                _ensure_worker_runtime_loaded()
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
                _log_task_memory(
                    "exec_task",
                    started=task_start,
                    rss_before_mb=rss_before_mb,
                )
            except _HANDLED_TASK_EXCEPTIONS as exc:
                error_line = f"{type(exc).__name__}: {exc}"
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
