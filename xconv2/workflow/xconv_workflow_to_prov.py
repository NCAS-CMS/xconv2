"""Helpers for converting xconv replay workflow JSON to/from PROV-JSON.

The canonical internal format remains the xconv replay JSON payload:
{
  "schema_version": 1,
  "session_id": "...",
  "saved_at": "...",
  "operations": [...]
}

This module exports a compact PROV-JSON document that embeds the canonical
workflow JSON in the session activity as ``xconv:workflow_json`` so round-trip
conversion back to internal JSON is lossless.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

_XCONV_NS = "https://github.com/NCAS-CMS/xconv2#"


def _json_safe(value: object) -> object:
    """Return a JSON-safe deep copy of ``value``."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _source_files_for_operation(operation: dict[str, object]) -> list[str]:
    """Extract ordered source-file hints from one replay operation."""
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


def _source_qname(uri: str) -> str:
    """Return a stable source entity identifier for a URI/path."""
    parsed = urlparse(uri)
    if parsed.scheme in {"http", "https", "s3"}:
        return f"xconv:source_{abs(hash(uri))}"
    return f"xconv:source_{abs(hash(Path(uri).as_posix()))}"


def workflow_to_prov_json_dict(
    workflow: dict[str, object],
    *,
    source_uri_overrides: dict[str, str] | None = None,
) -> dict[str, object]:
    """Convert xconv internal replay workflow JSON into a PROV-JSON mapping."""
    schema_version = int(workflow.get("schema_version", 1) or 1)
    session_id = str(workflow.get("session_id", "") or "")
    saved_at = str(workflow.get("saved_at", "") or "")
    runtime_versions_raw = workflow.get("runtime_versions")
    runtime_versions = runtime_versions_raw if isinstance(runtime_versions_raw, dict) else {}
    operations_raw = workflow.get("operations", [])
    operations = [op for op in operations_raw if isinstance(op, dict)] if isinstance(operations_raw, list) else []

    doc: dict[str, object] = {
        "prefix": {
            "prov": "http://www.w3.org/ns/prov#",
            "dcterms": "http://purl.org/dc/terms/",
            "xconv": _XCONV_NS,
        },
        "entity": {},
        "activity": {},
        "agent": {},
        "used": {},
        "wasGeneratedBy": {},
        "wasDerivedFrom": {},
        "wasAssociatedWith": {},
    }

    entities = doc["entity"]
    activities = doc["activity"]
    agents = doc["agent"]
    used = doc["used"]
    was_generated_by = doc["wasGeneratedBy"]
    was_derived_from = doc["wasDerivedFrom"]
    was_associated_with = doc["wasAssociatedWith"]

    assert isinstance(entities, dict)
    assert isinstance(activities, dict)
    assert isinstance(agents, dict)
    assert isinstance(used, dict)
    assert isinstance(was_generated_by, dict)
    assert isinstance(was_derived_from, dict)
    assert isinstance(was_associated_with, dict)

    agent_id = "xconv:xconv2"
    agents[agent_id] = {
        "prov:type": "prov:SoftwareAgent",
        "prov:label": "xconv2",
        "xconv:repository": "https://github.com/NCAS-CMS/xconv2",
    }
    xconv2_version = runtime_versions.get("xconv2")
    if isinstance(xconv2_version, str) and xconv2_version.strip():
        agents[agent_id]["xconv:xconv2_version"] = xconv2_version.strip()
    cf_python_version = runtime_versions.get("cf_python")
    if isinstance(cf_python_version, str) and cf_python_version.strip():
        agents[agent_id]["xconv:cf_python_version"] = cf_python_version.strip()

    session_activity_id = f"xconv:session_{session_id or 'unknown'}"
    activities[session_activity_id] = {
        "prov:type": "xconv:session",
        "prov:label": f"xconv2 session {session_id or 'unknown'}",
        "prov:startTime": saved_at,
        "prov:endTime": saved_at,
        "xconv:schema_version": schema_version,
        "xconv:session_id": session_id,
        # Embed the canonical workflow payload so round-trip back to internal
        # replay JSON is exact and lossless.
        "xconv:workflow_json": json.dumps(_json_safe(workflow), sort_keys=True),
    }
    was_associated_with["xconv:waw_session"] = {
        "prov:activity": session_activity_id,
        "prov:agent": agent_id,
    }

    source_entities: dict[str, str] = {}
    source_properties_raw = workflow.get("source_properties")
    source_properties = source_properties_raw if isinstance(source_properties_raw, dict) else {}
    next_field_entity = 0
    next_used = 0
    next_generated = 0
    next_derived = 0

    prior_generated_ids: list[str] = []

    def _ensure_source_entity(uri: str) -> str:
        effective_uri = uri
        if isinstance(source_uri_overrides, dict):
            override = source_uri_overrides.get(uri)
            if isinstance(override, str) and override.strip():
                effective_uri = override.strip()

        entity_id = source_entities.get(effective_uri)
        if entity_id:
            return entity_id
        entity_id = _source_qname(effective_uri)
        entity: dict[str, object] = {
            "prov:type": "prov:Collection",
            "prov:label": Path(effective_uri).name if effective_uri else "source",
            "xconv:uri": effective_uri,
        }
        source_meta = source_properties.get(uri)
        if not isinstance(source_meta, dict):
            source_meta = source_properties.get(effective_uri)
        if isinstance(source_meta, dict):
            tracking_id = source_meta.get("tracking_id")
            if isinstance(tracking_id, str) and tracking_id.strip():
                entity["dcterms:identifier"] = tracking_id.strip()

        entities[entity_id] = entity
        source_entities[effective_uri] = entity_id
        return entity_id

    for seq, operation in enumerate(operations):
        op_kind = str(operation.get("kind", "")).strip().lower() or "unknown"
        op_activity_id = f"xconv:op_{seq}"
        activities[op_activity_id] = {
            "prov:type": f"xconv:{op_kind}",
            "prov:label": f"{op_kind} (step {seq})",
            "xconv:seq": seq,
            "xconv:operation_json": json.dumps(_json_safe(operation), sort_keys=True),
        }

        output_count = 1
        if op_kind == "regrid":
            config = operation.get("config")
            if isinstance(config, dict):
                field_indices = config.get("field_indices")
                if isinstance(field_indices, list) and field_indices:
                    output_count = max(1, len(field_indices))

        current_output_ids: list[str] = []
        for _ in range(output_count):
            field_id = f"xconv:field_{next_field_entity}"
            next_field_entity += 1
            current_output_ids.append(field_id)
            entities[field_id] = {
                "prov:type": "xconv:field",
                "prov:label": f"field_{next_field_entity - 1}",
                "xconv:generated": True,
                "xconv:producer_seq": seq,
            }
            was_generated_by[f"xconv:wgb_{next_generated}"] = {
                "prov:entity": field_id,
                "prov:activity": op_activity_id,
            }
            next_generated += 1

        source_files = _source_files_for_operation(operation)
        for source in source_files:
            src_id = _ensure_source_entity(source)
            used[f"xconv:used_{next_used}"] = {
                "prov:activity": op_activity_id,
                "prov:entity": src_id,
            }
            next_used += 1
            for out_id in current_output_ids:
                was_derived_from[f"xconv:wdf_{next_derived}"] = {
                    "prov:generatedEntity": out_id,
                    "prov:usedEntity": src_id,
                }
                next_derived += 1

        for parent_id in prior_generated_ids:
            used[f"xconv:used_{next_used}"] = {
                "prov:activity": op_activity_id,
                "prov:entity": parent_id,
            }
            next_used += 1
            for out_id in current_output_ids:
                was_derived_from[f"xconv:wdf_{next_derived}"] = {
                    "prov:generatedEntity": out_id,
                    "prov:usedEntity": parent_id,
                }
                next_derived += 1

        prior_generated_ids.extend(current_output_ids)

    return doc


def prov_json_dict_to_workflow(prov_json: dict[str, object]) -> dict[str, object]:
    """Convert a PROV-JSON mapping back to canonical xconv workflow JSON."""
    if not isinstance(prov_json, dict):
        raise TypeError("PROV-JSON payload must be a mapping")

    activities = prov_json.get("activity")
    if isinstance(activities, dict):
        for attrs in activities.values():
            if not isinstance(attrs, dict):
                continue
            raw_workflow = attrs.get("xconv:workflow_json")
            if isinstance(raw_workflow, str) and raw_workflow.strip():
                try:
                    workflow = json.loads(raw_workflow)
                except json.JSONDecodeError as exc:
                    raise ValueError("Embedded xconv:workflow_json is invalid JSON") from exc
                if not isinstance(workflow, dict):
                    raise ValueError("Embedded xconv:workflow_json must decode to a mapping")
                return {
                    "schema_version": int(workflow.get("schema_version", 1) or 1),
                    "session_id": str(workflow.get("session_id", "") or ""),
                    "saved_at": str(workflow.get("saved_at", "") or ""),
                    "operations": list(workflow.get("operations", []))
                    if isinstance(workflow.get("operations", []), list)
                    else [],
                }

    raise ValueError("PROV-JSON does not contain embedded xconv:workflow_json")


def workflow_to_prov(workflow: dict[str, object]) -> dict[str, object]:
    """Backward-compatible alias returning a PROV-JSON mapping."""
    return workflow_to_prov_json_dict(workflow)


def workflow_file_to_prov_file(input_path: str | Path, output_path: str | Path) -> None:
    """Read internal workflow JSON and write PROV-JSON."""
    source = Path(input_path)
    destination = Path(output_path)
    workflow = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict):
        raise ValueError("Input workflow JSON must be a mapping")
    prov_json = workflow_to_prov_json_dict(workflow)
    destination.write_text(json.dumps(prov_json, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: xconv_workflow_to_prov.py <input.json> [output.prov.json]")
        return 1

    input_path = Path(argv[1])
    if len(argv) >= 3:
        output_path = Path(argv[2])
    else:
        output_path = input_path.with_suffix("").with_suffix(".prov.json")

    workflow_file_to_prov_file(input_path, output_path)
    print(f"Written PROV-JSON to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(__import__("sys").argv))
