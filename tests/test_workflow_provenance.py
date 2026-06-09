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
