from __future__ import annotations

from xconv2.workflow.xconv_workflow_to_prov import (
    prov_json_dict_to_workflow,
    workflow_to_prov_json_dict,
)


def test_workflow_to_prov_json_roundtrip_preserves_internal_payload() -> None:
    workflow = {
        "schema_version": 1,
        "session_id": "session-abc",
        "saved_at": "2026-06-09T00:00:00Z",
        "operations": [
            {
                "kind": "unary_xy",
                "field_index": 0,
                "field_ref": {
                    "identity": "air_temperature(10, 20)",
                    "source_file": "/tmp/a.nc",
                    "generated": False,
                    "occurrence": 1,
                },
                "operation": "grad",
                "source_file": "/tmp/a.nc",
            },
            {
                "kind": "binary",
                "index_a": 1,
                "index_b": 2,
                "operation": "difference_ab",
                "source_files": ["/tmp/a.nc", "/tmp/b.nc"],
            },
            {
                "kind": "filter",
                "field_index": 2,
                "config": {
                    "method": "convolution",
                    "window": "hann",
                    "axis": "X",
                    "size": 5,
                },
                "source_file": "/tmp/b.nc",
            },
        ],
    }

    prov_json = workflow_to_prov_json_dict(workflow)
    recovered = prov_json_dict_to_workflow(prov_json)

    assert recovered == workflow


def test_workflow_to_prov_json_embeds_session_and_workflow_blob() -> None:
    workflow = {
        "schema_version": 1,
        "session_id": "session-x",
        "saved_at": "",
        "operations": [],
    }

    prov_json = workflow_to_prov_json_dict(workflow)

    assert "activity" in prov_json
    activities = prov_json["activity"]
    assert isinstance(activities, dict)
    session_activity = activities["xconv:session_session-x"]
    assert session_activity["xconv:session_id"] == "session-x"
    assert isinstance(session_activity.get("xconv:workflow_json"), str)


def test_workflow_to_prov_json_applies_source_uri_overrides_only_to_entities() -> None:
    workflow = {
        "schema_version": 1,
        "session_id": "session-x",
        "saved_at": "",
        "operations": [
            {
                "kind": "unary_xy",
                "field_index": 0,
                "field_ref": {
                    "identity": "tas",
                    "source_file": "s3://bnl/CMIP6-test.nc",
                    "generated": False,
                    "occurrence": 1,
                },
                "operation": "grad",
                "source_file": "s3://bnl/CMIP6-test.nc",
            }
        ],
    }

    prov_json = workflow_to_prov_json_dict(
        workflow,
        source_uri_overrides={
            "s3://bnl/CMIP6-test.nc": "s3://object.example.org/bnl/CMIP6-test.nc",
        },
    )

    entities = prov_json["entity"]
    assert isinstance(entities, dict)
    source_entities = [
        attrs
        for entity_id, attrs in entities.items()
        if isinstance(entity_id, str)
        and entity_id.startswith("xconv:source_")
        and isinstance(attrs, dict)
    ]
    assert len(source_entities) == 1
    assert source_entities[0]["xconv:uri"] == "s3://object.example.org/bnl/CMIP6-test.nc"

    recovered = prov_json_dict_to_workflow(prov_json)
    assert recovered == workflow


def test_workflow_to_prov_json_includes_dcterms_identifier_for_tracking_id() -> None:
    workflow = {
        "schema_version": 1,
        "session_id": "session-y",
        "saved_at": "",
        "operations": [
            {
                "kind": "unary_xy",
                "field_index": 0,
                "field_ref": {
                    "identity": "tas",
                    "source_file": "/tmp/a.nc",
                    "generated": False,
                    "occurrence": 1,
                },
                "operation": "grad",
                "source_file": "/tmp/a.nc",
            }
        ],
        "source_properties": {
            "/tmp/a.nc": {
                "tracking_id": "track-abc-123",
            }
        },
    }

    prov_json = workflow_to_prov_json_dict(workflow)

    prefixes = prov_json["prefix"]
    assert isinstance(prefixes, dict)
    assert prefixes["dcterms"] == "http://purl.org/dc/terms/"

    entities = prov_json["entity"]
    assert isinstance(entities, dict)
    source_entities = [
        attrs
        for entity_id, attrs in entities.items()
        if isinstance(entity_id, str)
        and entity_id.startswith("xconv:source_")
        and isinstance(attrs, dict)
    ]
    assert len(source_entities) == 1
    assert source_entities[0]["dcterms:identifier"] == "track-abc-123"


def test_workflow_to_prov_json_includes_runtime_versions_on_agent() -> None:
    workflow = {
        "schema_version": 1,
        "session_id": "session-z",
        "saved_at": "",
        "operations": [],
        "runtime_versions": {
            "xconv2": "beta-1.2.3",
            "cf_python": "3.16.0",
        },
    }

    prov_json = workflow_to_prov_json_dict(workflow)

    agents = prov_json["agent"]
    assert isinstance(agents, dict)
    software_agent = agents["xconv:xconv2"]
    assert software_agent["xconv:xconv2_version"] == "beta-1.2.3"
    assert software_agent["xconv:cf_python_version"] == "3.16.0"
