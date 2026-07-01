"""Replay/provenance helper operations extracted from CFVMain."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import time
import uuid
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def json_safe_operation_payload(value: object) -> object:
    """Return a JSON-compatible copy of operation payload data."""
    try:
        return json.loads(json.dumps(value, sort_keys=True))
    except TypeError:
        return json.loads(json.dumps(value, sort_keys=True, default=str))


def last_operations_path() -> Path:
    """Return path to the replayable operations history file."""
    return Path.home() / ".xconv2" / "last_operations.json"


def load_last_operations_payload(host: object) -> dict[str, object]:
    """Load replay payload from disk, returning an empty schema when absent/invalid."""
    path = host._last_operations_path()
    default_payload: dict[str, object] = {
        "schema_version": 1,
        "session_id": "",
        "saved_at": "",
        "operations": [],
    }
    if not path.exists():
        return default_payload

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read last operations file: %s", path)
        return default_payload

    if not isinstance(payload, dict):
        logger.warning("Ignoring malformed last operations payload: expected dict")
        return default_payload

    operations = payload.get("operations")
    if not isinstance(operations, list):
        logger.warning("Ignoring malformed last operations payload: operations is not a list")
        return default_payload

    return {
        "schema_version": int(payload.get("schema_version", 1) or 1),
        "session_id": str(payload.get("session_id", "") or ""),
        "saved_at": str(payload.get("saved_at", "") or ""),
        "operations": operations,
    }


def record_replayable_operation(host: object, operation: dict[str, object]) -> None:
    """Append one replayable field operation to disk."""
    path = host._last_operations_path()
    payload = host._load_last_operations_payload()

    operations_raw = payload.get("operations", [])
    operations = operations_raw if isinstance(operations_raw, list) else []
    active_session_id = str(getattr(host, "_replay_session_id", "") or "")
    payload_session_id = str(payload.get("session_id", "") or "")
    if active_session_id and payload_session_id != active_session_id:
        operations = []
    json_safe_fn = getattr(host, "_json_safe_operation_payload", None)
    if callable(json_safe_fn):
        safe_operation = json_safe_fn(operation)
    else:
        safe_operation = json_safe_operation_payload(operation)
    operations.append(safe_operation)

    payload["schema_version"] = 1
    payload["session_id"] = active_session_id
    payload["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["operations"] = operations

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        logger.exception("Failed to persist replayable operations to %s", path)


def describe_replay_operation(operation: dict[str, object]) -> str:
    """Build a short, user-facing description for one replayable operation."""
    kind = str(operation.get("kind", "")).strip().lower()

    if kind == "unary_xy":
        op = str(operation.get("operation", "unknown"))
        idx = operation.get("field_index")
        return f"Maths {op} on field index {idx}"

    if kind == "binary":
        op = str(operation.get("operation", "unknown"))
        idx_a = operation.get("index_a")
        idx_b = operation.get("index_b")
        return f"Maths {op} on field indices {idx_a} and {idx_b}"

    if kind == "filter":
        idx = operation.get("field_index")
        config = operation.get("config")
        method = "unknown"
        if isinstance(config, dict):
            method = str(config.get("method", "unknown"))
        return f"Maths filter ({method}) on field index {idx}"

    if kind == "apply_selection":
        idx = operation.get("field_index")
        return f"Apply Selection on field index {idx}"

    if kind == "regrid":
        config = operation.get("config")
        if isinstance(config, dict):
            target = str(config.get("target", "unknown"))
            field_indices = config.get("field_indices", [])
            if isinstance(field_indices, list):
                return f"Regrid target {target} for {len(field_indices)} field(s)"
            return f"Regrid target {target}"
        return "Regrid"

    return f"Unknown operation kind: {kind or 'unknown'}"


def source_files_for_replay_operation(operation: dict[str, object]) -> list[str]:
    """Return ordered source-file hints associated with one replay operation."""
    kind = str(operation.get("kind", "")).strip().lower()

    if kind in {"unary_xy", "apply_selection", "filter"}:
        source_file = operation.get("source_file")
        if isinstance(source_file, str) and source_file.strip():
            return [source_file.strip()]
        return []

    if kind == "binary":
        source_files = operation.get("source_files")
        if isinstance(source_files, list):
            return [str(item).strip() for item in source_files if isinstance(item, str) and str(item).strip()]
        return []

    if kind == "regrid":
        source_files = operation.get("source_files")
        if isinstance(source_files, list):
            return [str(item).strip() for item in source_files if isinstance(item, str) and str(item).strip()]
        return []

    return []


def is_remote_source_uri(uri: str) -> bool:
    scheme = urlparse(uri).scheme.lower()
    return scheme in {"s3", "ssh", "http", "https"}


def build_remote_open_requests_for_sources(
    host: object,
    sources: list[str],
    *,
    is_remote_source_uri_fn,
    with_cache_defaults_fn,
) -> list[dict[str, object]]:
    """Build worker remote-open descriptors for replay/provenance sources."""
    remote_open_requests: list[dict[str, object]] = []
    for source in sources:
        if not is_remote_source_uri_fn(source):
            continue

        config, remote_path, _host_alias, unknown_host = host._resolve_remote_uri(source)
        if unknown_host or not isinstance(config, dict):
            continue

        try:
            from ..remote_access import build_remote_filesystem_spec, remote_descriptor_hash, spec_to_descriptor  # noqa: PLC0415

            config_with_cache = with_cache_defaults_fn(config)
            spec = build_remote_filesystem_spec(config_with_cache)
            descriptor = spec_to_descriptor(
                spec,
                cache=config_with_cache.get("cache") if isinstance(config_with_cache, dict) else None,
            )
            remote_open_requests.append(
                {
                    "uri": source,
                    "session_id": uuid.uuid4().hex,
                    "descriptor_hash": remote_descriptor_hash(descriptor),
                    "descriptor": descriptor,
                    "paths": [remote_path],
                }
            )
        except Exception:
            logger.exception("Failed to prepare remote preload request for %s", source)

    return remote_open_requests


def workflow_payload_from_provenance_document(payload: object) -> dict[str, object] | None:
    """Normalize either internal workflow JSON or PROV-JSON into replay workflow payload."""
    from ..workflow.xconv_workflow_to_prov import prov_json_dict_to_workflow  # noqa: PLC0415

    if not isinstance(payload, dict):
        return None

    operations = payload.get("operations")
    if isinstance(operations, list):
        return {
            "schema_version": int(payload.get("schema_version", 1) or 1),
            "session_id": str(payload.get("session_id", "") or ""),
            "saved_at": str(payload.get("saved_at", "") or ""),
            "operations": operations,
        }

    try:
        workflow = prov_json_dict_to_workflow(payload)
    except ValueError:
        return None

    return {
        "schema_version": int(workflow.get("schema_version", 1) or 1),
        "session_id": str(workflow.get("session_id", "") or ""),
        "saved_at": str(workflow.get("saved_at", "") or ""),
        "operations": list(workflow.get("operations", []))
        if isinstance(workflow.get("operations", []), list)
        else [],
    }


def field_ops_replay_last_operations(host: object, *, replay_dialog_cls: type[object]) -> None:
    """Replay persisted field operations by dispatching a worker control task."""
    from ..main_window import CFVMain  # noqa: PLC0415

    payload = host._load_last_operations_payload()
    operations_raw = payload.get("operations", [])
    operations = operations_raw if isinstance(operations_raw, list) else []
    if not operations:
        host._show_status_message("No replayable field operations found.", is_error=True)
        return

    candidates: list[dict[str, object]] = []
    skipped = 0
    for raw in operations:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        candidates.append(raw)

    if not candidates:
        host._show_status_message("No valid replayable operations found in history.", is_error=True)
        return

    chooser = replay_dialog_cls(
        host,
        operation_labels=[host._describe_replay_operation(operation) for operation in candidates],
    )
    if chooser.exec() != 1:
        return

    selected_indices = chooser.selected_indices()
    if not selected_indices:
        host._show_status_message("No operations selected for replay.", is_error=True)
        return

    selected_operations = [
        candidates[idx]
        for idx in selected_indices
        if 0 <= idx < len(candidates)
    ]

    if not selected_operations:
        host._show_status_message("No valid replayable operations found in history.", is_error=True)
        return

    skipped += len(candidates) - len(selected_operations)

    replay_sources: list[str] = []
    for operation in selected_operations:
        for source in host._source_files_for_replay_operation(operation):
            if source in replay_sources:
                continue
            replay_sources.append(source)

    remote_builder = getattr(host, "_build_remote_open_requests_for_sources", None)
    if callable(remote_builder):
        remote_open_requests = remote_builder(replay_sources)
    else:
        remote_open_requests = CFVMain._build_remote_open_requests_for_sources(host, replay_sources)

    host._show_status_message(
        f"Replaying {len(selected_operations)} field operation(s){f' (skipped {skipped})' if skipped else ''}..."
    )
    logger.info("Dispatching replay control task for %d field operations (skipped=%d)", len(selected_operations), skipped)
    host._send_worker_control_task(
        "REPLAY_FIELDS",
        {
            "operations": selected_operations,
            "remote_open_requests": remote_open_requests,
        },
    )


def file_ops_save_selected_provenance(host: object, *, file_dialog_cls: type[object]) -> None:
    """Save field-specific upstream provenance for selected fields."""
    from ..main_window import CFVMain  # noqa: PLC0415

    selected_items = list(host.field_list_widget.selectedItems())
    if not selected_items:
        host._show_status_message("Select one or more fields to save provenance.", is_error=True)
        return

    selected_indices = sorted(
        {
            idx
            for idx in (host.field_list_widget.row(item) for item in selected_items)
            if idx >= 0
        }
    )
    if not selected_indices:
        host._show_status_message("No valid selected fields for provenance export.", is_error=True)
        return

    selected_field_refs: list[dict[str, object]] = []
    for idx in selected_indices:
        field_ref_resolver = getattr(host, "_field_reference_for_index", None)
        if callable(field_ref_resolver):
            field_ref = field_ref_resolver(idx)
        else:
            field_ref = CFVMain._field_reference_for_index(host, idx)
        if isinstance(field_ref, dict):
            selected_field_refs.append(field_ref)

    if not selected_field_refs:
        host._show_status_message("Selected fields could not be resolved for provenance export.", is_error=True)
        return

    payload = host._load_last_operations_payload()
    operations_raw = payload.get("operations", [])
    operations = operations_raw if isinstance(operations_raw, list) else []
    if not operations:
        host._show_status_message("No replayable operations available for provenance export.", is_error=True)
        return

    replay_sources: list[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        for source in host._source_files_for_replay_operation(operation):
            if source not in replay_sources:
                replay_sources.append(source)

    remote_builder = getattr(host, "_build_remote_open_requests_for_sources", None)
    if callable(remote_builder):
        remote_open_requests = remote_builder(replay_sources)
    else:
        remote_open_requests = CFVMain._build_remote_open_requests_for_sources(host, replay_sources)

    default_destination = str(host._settings.get("last_save_data_dir", str(Path.home())))
    default_filename = f"{host._default_plot_filename()}_selected.prov.json"
    suggested = str(Path(default_destination).expanduser() / default_filename)
    destination, _selected_filter = file_dialog_cls.getSaveFileName(
        host,
        "Save Selected Provenance",
        suggested,
        "PROV JSON (*.prov.json);;xconv Workflow JSON (*.json)",
    )
    if not destination:
        return

    destination_path = Path(destination).expanduser()
    output_format = "prov-json" if destination_path.name.endswith(".prov.json") else "xconv-json"

    if output_format == "prov-json" and destination_path.suffix != ".json":
        destination_path = destination_path.with_suffix(".prov.json")
    elif output_format == "xconv-json" and destination_path.suffix != ".json":
        destination_path = destination_path.with_suffix(".json")

    host._remember_last_save_dir("last_save_data_dir", str(destination_path))
    host._send_worker_control_task(
        "SAVE_PROVENANCE",
        {
            "schema_version": int(payload.get("schema_version", 1) or 1),
            "session_id": str(payload.get("session_id", "") or ""),
            "saved_at": str(payload.get("saved_at", "") or ""),
            "operations": operations,
            "selected_field_refs": selected_field_refs,
            "remote_open_requests": remote_open_requests,
            "destination": str(destination_path),
            "output_format": output_format,
        },
    )
    host._show_status_message(
        f"Saving field-specific provenance for {len(selected_field_refs)} selected field(s)..."
    )


def input_load_and_run_prov(
    host: object,
    *,
    file_dialog_cls: type[object],
    workflow_payload_from_provenance_document: object,
) -> None:
    """Load internal/PROV workflow JSON and replay it through worker control messaging."""
    from ..main_window import CFVMain  # noqa: PLC0415

    default_dir = str(host._settings.get("last_open_data_dir", str(Path.home())))
    selected_path, _selected_filter = file_dialog_cls.getOpenFileName(
        host,
        "Load & Run Prov",
        default_dir,
        "Workflow/PROV JSON (*.json *.prov.json)",
    )
    if not selected_path:
        return

    path = Path(selected_path).expanduser()
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        host._show_status_message(f"Failed to load provenance file: {exc}", is_error=True)
        return

    workflow_payload = workflow_payload_from_provenance_document(raw_payload)
    if workflow_payload is None:
        host._show_status_message(
            "Loaded file is not recognized as xconv workflow JSON or PROV-JSON.",
            is_error=True,
        )
        return

    operations_raw = workflow_payload.get("operations", [])
    operations = operations_raw if isinstance(operations_raw, list) else []
    if not operations:
        host._show_status_message("No replayable operations found in provenance file.", is_error=True)
        return

    replay_sources: list[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        for source in host._source_files_for_replay_operation(operation):
            if source not in replay_sources:
                replay_sources.append(source)

    remote_builder = getattr(host, "_build_remote_open_requests_for_sources", None)
    if callable(remote_builder):
        remote_open_requests = remote_builder(replay_sources)
    else:
        remote_open_requests = CFVMain._build_remote_open_requests_for_sources(host, replay_sources)

    if replay_sources:
        mode_setter = getattr(host, "_set_file_open_mode", None)
        if callable(mode_setter):
            mode_setter("multi")
        else:
            host.file_open_mode = "multi"
        host._loaded_file_paths = list(replay_sources)
        refresh_menu = getattr(host, "_refresh_open_files_menu", None)
        if callable(refresh_menu):
            refresh_menu()

    host._send_worker_control_task(
        "REPLAY_FIELDS",
        {
            "operations": operations,
            "remote_open_requests": remote_open_requests,
        },
    )
    host._show_status_message(f"Running provenance workflow from {path.name} ({len(operations)} operation(s))...")
