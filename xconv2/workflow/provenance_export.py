"""Worker-facing helpers for field-specific provenance export.

This module keeps SAVE_PROVENANCE control-task logic out of worker.py so the
worker can focus on task routing and runtime concerns.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import cf

from .. import __version__
from .. import cf_interface
from .xconv_workflow_to_prov import workflow_to_prov_json_dict


def _tracking_id_from_field(field: Any) -> str:
    """Return a normalized tracking_id from one field-like object, if available."""
    if isinstance(field, dict):
        value = field.get("tracking_id")
        if isinstance(value, str) and value.strip():
            return value.strip()

    getter = getattr(field, "get_property", None)
    if callable(getter):
        try:
            value = getter("tracking_id", None)
        except TypeError:
            try:
                value = getter("tracking_id")
            except Exception:
                value = None
        except Exception:
            value = None
        if isinstance(value, str) and value.strip():
            return value.strip()

    props_method = getattr(field, "properties", None)
    if callable(props_method):
        try:
            props = props_method()
        except Exception:
            props = None
        if isinstance(props, dict):
            value = props.get("tracking_id")
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _tracking_id_from_loaded_fields(loaded_fields: list[Any]) -> str:
    """Return the first non-empty tracking_id discovered across loaded fields."""
    for field in loaded_fields:
        tracking_id = _tracking_id_from_field(field)
        if tracking_id:
            return tracking_id
    return ""


def build_fields_provenance_slice(
    payload: dict[str, Any],
    *,
    replay_source_files_for_operation: Callable[[dict[str, Any]], list[str]],
    prepare_remote_session: Callable[..., Any],
    replay_normalize_loaded_fields: Callable[[Any], list[Any]],
    read_remote_fields: Callable[..., Any],
    resolve_field_reference_index: Callable[[list[Any], list[dict[str, Any]], dict[str, Any], object], int | None],
) -> dict[str, Any]:
    """Build a workflow slice containing only operations upstream of selected fields."""
    operations_raw = payload.get("operations")
    if not isinstance(operations_raw, list) or not operations_raw:
        raise ValueError("SAVE_PROVENANCE requires a non-empty operations list")

    operations = [op for op in operations_raw if isinstance(op, dict)]
    if not operations:
        raise ValueError("SAVE_PROVENANCE operations list contains no valid mappings")

    replay_sources: list[str] = []
    for operation in operations:
        for source in replay_source_files_for_operation(operation):
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

    fields: list[Any] = []
    provenance: list[dict[str, Any]] = []
    lineage: list[set[int]] = []
    source_properties: dict[str, dict[str, str]] = {}

    for source in replay_sources:
        request = remote_open_requests.get(source)
        if isinstance(request, dict):
            session_id = str(request.get("session_id", "")).strip()
            descriptor_hash = str(request.get("descriptor_hash", "")).strip()
            descriptor = request.get("descriptor")
            paths = request.get("paths")
            if not isinstance(descriptor, dict) or not session_id or not descriptor_hash:
                raise ValueError(f"Provenance remote preload request is invalid for source: {source}")
            if isinstance(paths, list):
                datasets = [str(item) for item in paths if str(item)]
            else:
                datasets = []
            if not datasets:
                raise ValueError(f"Provenance remote preload paths missing for source: {source}")

            entry = prepare_remote_session(
                session_id=session_id,
                descriptor_hash=descriptor_hash,
                descriptor=descriptor,
            )
            entry.last_used = time.monotonic()
            loaded_fields = replay_normalize_loaded_fields(
                read_remote_fields(
                    entry=entry,
                    descriptor=descriptor,
                    datasets=datasets[0] if len(datasets) == 1 else datasets,
                )
            )
        else:
            loaded_fields = replay_normalize_loaded_fields(cf.read(source))

        fields.extend(loaded_fields)
        provenance.extend({"source_file": source, "generated": False} for _ in loaded_fields)
        lineage.extend(set() for _ in loaded_fields)
        tracking_id = _tracking_id_from_loaded_fields(loaded_fields)
        if tracking_id:
            source_properties[source] = {"tracking_id": tracking_id}

    for op_seq, operation in enumerate(operations):
        kind = str(operation.get("kind", "")).strip().lower()
        metadata_rows: list[dict[str, object]] | None = None
        input_indices: list[int] = []

        if kind == "unary_xy":
            resolved_index = resolve_field_reference_index(
                fields,
                provenance,
                operation.get("field_ref") if isinstance(operation.get("field_ref"), dict) else {},
                operation.get("field_index"),
            )
            operation_key = operation.get("operation")
            if isinstance(resolved_index, int) and isinstance(operation_key, str) and operation_key.strip():
                input_indices = [resolved_index]
                before = len(fields)
                metadata_rows = cf_interface.append_unary_xy_field_operation(fields, resolved_index, operation_key)
                added = max(0, len(fields) - before)
            else:
                added = 0

        elif kind == "binary":
            resolved_a = resolve_field_reference_index(
                fields,
                provenance,
                operation.get("field_ref_a") if isinstance(operation.get("field_ref_a"), dict) else {},
                operation.get("index_a"),
            )
            resolved_b = resolve_field_reference_index(
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
                input_indices = [resolved_a, resolved_b]
                before = len(fields)
                metadata_rows = cf_interface.append_binary_field_operation(
                    fields,
                    resolved_a,
                    resolved_b,
                    operation_key,
                    source_files=source_files,
                )
                added = max(0, len(fields) - before)
            else:
                added = 0

        elif kind == "filter":
            resolved_index = resolve_field_reference_index(
                fields,
                provenance,
                operation.get("field_ref") if isinstance(operation.get("field_ref"), dict) else {},
                operation.get("field_index"),
            )
            config = operation.get("config")
            if isinstance(resolved_index, int) and isinstance(config, dict):
                input_indices = [resolved_index]
                before = len(fields)
                metadata_rows = cf_interface.append_filter_field_operation(fields, resolved_index, config)
                added = max(0, len(fields) - before)
            else:
                added = 0

        elif kind == "apply_selection":
            resolved_index = resolve_field_reference_index(
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
                input_indices = [resolved_index]
                before = len(fields)
                metadata_rows = cf_interface.append_selection_field_operation(
                    fields,
                    resolved_index,
                    selections,
                    collapse_by_coord,
                )
                added = max(0, len(fields) - before)
            else:
                added = 0

        elif kind == "regrid":
            config = operation.get("config")
            if isinstance(config, dict):
                config_copy = dict(config)
                resolved_indices: list[int] = []

                field_refs = operation.get("field_refs")
                if isinstance(field_refs, list) and field_refs:
                    for raw_ref in field_refs:
                        if not isinstance(raw_ref, dict):
                            continue
                        resolved = resolve_field_reference_index(fields, provenance, raw_ref, None)
                        if isinstance(resolved, int):
                            resolved_indices.append(resolved)

                if not resolved_indices:
                    raw_indices = config_copy.get("field_indices")
                    if isinstance(raw_indices, list):
                        for raw in raw_indices:
                            if isinstance(raw, int):
                                resolved_indices.append(raw)

                if resolved_indices:
                    input_indices = list(resolved_indices)
                    config_copy["field_indices"] = resolved_indices
                    before = len(fields)
                    metadata_rows = cf_interface.regrid_from_config(fields, json.dumps(config_copy, sort_keys=True))
                    added = max(0, len(fields) - before)
                else:
                    added = 0
            else:
                added = 0
        else:
            added = 0

        if metadata_rows is None or added <= 0:
            continue

        inherited: set[int] = {op_seq}
        for idx in input_indices:
            if 0 <= idx < len(lineage):
                inherited.update(lineage[idx])

        for _ in range(added):
            lineage.append(set(inherited))
            provenance.append({"source_file": "", "generated": True})

    selected_refs_raw = payload.get("selected_field_refs")
    selected_refs = [ref for ref in selected_refs_raw if isinstance(ref, dict)] if isinstance(selected_refs_raw, list) else []
    if not selected_refs:
        raise ValueError("SAVE_PROVENANCE requires selected_field_refs")

    required_ops: set[int] = set()
    for field_ref in selected_refs:
        idx = resolve_field_reference_index(fields, provenance, field_ref, None)
        if isinstance(idx, int) and 0 <= idx < len(lineage):
            required_ops.update(lineage[idx])

    filtered_operations = [op for seq, op in enumerate(operations) if seq in required_ops]
    filtered_sources: set[str] = set()
    for operation in filtered_operations:
        for source in replay_source_files_for_operation(operation):
            filtered_sources.add(source)

    result: dict[str, Any] = {
        "schema_version": int(payload.get("schema_version", 1) or 1),
        "session_id": str(payload.get("session_id", "") or ""),
        "saved_at": str(payload.get("saved_at", "") or ""),
        "operations": filtered_operations,
        "runtime_versions": {
            "xconv2": str(__version__),
            "cf_python": str(getattr(cf, "__version__", "unknown")),
        },
    }
    filtered_source_properties = {
        source: props
        for source, props in source_properties.items()
        if source in filtered_sources and isinstance(props, dict)
    }
    if filtered_source_properties:
        result["source_properties"] = filtered_source_properties

    return result


def shareable_provenance_source_overrides(payload: dict[str, Any]) -> dict[str, str]:
    """Return export-time source URI overrides for portable provenance records."""
    remote_open_requests_raw = payload.get("remote_open_requests")
    if not isinstance(remote_open_requests_raw, list):
        return {}

    overrides: dict[str, str] = {}
    for request in remote_open_requests_raw:
        if not isinstance(request, dict):
            continue

        uri_raw = request.get("uri")
        source_uri = uri_raw.strip() if isinstance(uri_raw, str) else ""
        if not source_uri:
            continue

        parsed_source = urlparse(source_uri)
        if parsed_source.scheme.lower() != "s3":
            continue

        descriptor = request.get("descriptor")
        if not isinstance(descriptor, dict):
            continue

        storage_options = descriptor.get("storage_options")
        if not isinstance(storage_options, dict):
            continue

        client_kwargs = storage_options.get("client_kwargs")
        if not isinstance(client_kwargs, dict):
            continue

        endpoint_url = str(client_kwargs.get("endpoint_url", "") or "").strip()
        if not endpoint_url:
            continue

        normalized_endpoint = endpoint_url if "://" in endpoint_url else f"https://{endpoint_url}"
        endpoint_host = urlparse(normalized_endpoint).netloc.strip()
        if not endpoint_host:
            continue

        path = f"{parsed_source.netloc}{parsed_source.path}".lstrip("/")
        if not path:
            continue

        overrides[source_uri] = f"s3://{endpoint_host}/{path}"

    return overrides


def handle_save_provenance_task(
    payload: dict[str, Any],
    *,
    replay_source_files_for_operation: Callable[[dict[str, Any]], list[str]],
    prepare_remote_session: Callable[..., Any],
    replay_normalize_loaded_fields: Callable[[Any], list[Any]],
    read_remote_fields: Callable[..., Any],
    resolve_field_reference_index: Callable[[list[Any], list[dict[str, Any]], dict[str, Any], object], int | None],
) -> str:
    """Build field-specific provenance and save to disk in requested format."""
    destination_raw = payload.get("destination")
    if not isinstance(destination_raw, str) or not destination_raw.strip():
        raise ValueError("SAVE_PROVENANCE requires destination")
    destination = Path(destination_raw).expanduser()

    output_format = str(payload.get("output_format", "xconv-json") or "xconv-json").strip().lower()
    if output_format not in {"xconv-json", "prov-json"}:
        raise ValueError(f"Unsupported provenance output format: {output_format}")

    workflow_slice = build_fields_provenance_slice(
        payload,
        replay_source_files_for_operation=replay_source_files_for_operation,
        prepare_remote_session=prepare_remote_session,
        replay_normalize_loaded_fields=replay_normalize_loaded_fields,
        read_remote_fields=read_remote_fields,
        resolve_field_reference_index=resolve_field_reference_index,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    if output_format == "prov-json":
        prov_payload = workflow_to_prov_json_dict(
            workflow_slice,
            source_uri_overrides=shareable_provenance_source_overrides(payload),
        )
        destination.write_text(json.dumps(prov_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return f"STATUS:Saved selected provenance to {destination} (prov-json)"

    destination.write_text(json.dumps(workflow_slice, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return f"STATUS:Saved selected provenance to {destination} (xconv-json)"
