from __future__ import annotations

from pathlib import Path

import pytest

import xconv2.worker as worker


@pytest.mark.integration
def test_worker_remote_open_from_minio_emits_metadata(minio_service, temp_bucket) -> None:
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "nested/test1.nc"

    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    descriptor = {
        "protocol": "s3",
        "storage_options": {
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": "http://localhost:9000"},
        },
        "root_path": "",
        "display_name": "fake-alias",
        "uri_scheme": "s3",
        "uri_authority": "",
        "proxy_jump": None,
    }
    descriptor_hash = "integration-hash"
    session_id = "integration-session"
    uri = f"s3://{temp_bucket}/{object_name}"
    path = f"{temp_bucket}/{object_name}"

    messages: list[tuple[str, object]] = []
    original_send_to_gui = worker.send_to_gui
    worker.remote_session_pool.clear()

    try:
        worker.send_to_gui = lambda prefix, data=None: messages.append((prefix, data))

        worker._handle_control_task(
            "REMOTE_PREPARE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
            },
        )
        worker._handle_control_task(
            "REMOTE_OPEN",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
                "uri": uri,
                "path": path,
            },
        )
    finally:
        worker.send_to_gui = original_send_to_gui
        worker.remote_session_pool.clear()

    prefixes = [prefix for prefix, _ in messages]
    assert "METADATA" in prefixes
    assert "REMOTE_OPEN_RESULT" in prefixes

    metadata_payload = next(data for prefix, data in messages if prefix == "METADATA")
    assert isinstance(metadata_payload, list)
    assert metadata_payload

    open_result = next(data for prefix, data in messages if prefix == "REMOTE_OPEN_RESULT")
    assert open_result == {
        "session_id": session_id,
        "uri": uri,
        "ok": True,
    }