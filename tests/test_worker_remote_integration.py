from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

import pytest

from xconv2.cf_templates import coordinate_list, plot_from_selection
from xconv2.ui.settings_store import SettingsStore
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
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
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


@pytest.mark.integration
def test_worker_open_recent_s3_netcdf_from_minio(minio_service, temp_bucket) -> None:
    """Round-trip an S3 URI through recent storage and reopen successfully from MinIO."""
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "nested/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    uri = f"s3://{temp_bucket}/{object_name}"
    parsed = urlparse(uri)
    path = f"{parsed.netloc}{parsed.path}".lstrip("/")

    with TemporaryDirectory(prefix="xconv2-recent-") as tmpdir:
        base = Path(tmpdir)
        store = SettingsStore(
            settings_path=base / "settings.json",
            recent_log_path=base / "recent.log",
            settings_version=1,
            default_max_recent_files=10,
        )
        store.data = store.default_settings()
        store.record_recent_file(uri)
        recent_uri = store.load_recent_files()[0]

    assert recent_uri == uri

    descriptor = {
        "protocol": "s3",
        "storage_options": {
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        "root_path": "",
        "display_name": "fake-alias",
        "uri_scheme": "s3",
        "uri_authority": "",
        "proxy_jump": None,
    }
    descriptor_hash = "integration-recent-hash"
    session_id = "integration-recent-session"

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
                "uri": recent_uri,
                "path": path,
            },
        )
    finally:
        worker.send_to_gui = original_send_to_gui
        worker.remote_session_pool.clear()

    prefixes = [prefix for prefix, _ in messages]
    assert "METADATA" in prefixes
    assert "REMOTE_OPEN_RESULT" in prefixes

    open_result = next(data for prefix, data in messages if prefix == "REMOTE_OPEN_RESULT")
    assert open_result == {
        "session_id": session_id,
        "uri": recent_uri,
        "ok": True,
    }


@pytest.mark.integration
@pytest.mark.xfail(
    reason="Upstream cfdm/cf remote S3 PP read path currently fails; enable when upstream fix lands",
    strict=False,
)
def test_worker_remote_pp_from_minio_can_plot_default_contour_with_time_collapse(
    minio_service,
    temp_bucket,
) -> None:
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test2.pp"
    object_name = "nested/test2.pp"

    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    descriptor = {
        "protocol": "s3",
        "storage_options": {
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        "root_path": "",
        "display_name": "fake-alias",
        "uri_scheme": "s3",
        "uri_authority": "",
        "proxy_jump": None,
    }
    descriptor_hash = "integration-pp-hash"
    session_id = "integration-pp-session"
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

        # Match GUI flow: choose first field before plotting.
        exec(coordinate_list(0), worker.worker_globals)

        contour_code = plot_from_selection(
            selections={},
            collapse_by_coord={"time": "mean"},
            plot_kind="contour",
            plot_options=None,
        )
        exec(contour_code, worker.worker_globals)
        worker._emit_latest_plot_image()
    finally:
        worker.send_to_gui = original_send_to_gui
        worker.remote_session_pool.clear()

    prefixes = [prefix for prefix, _ in messages]
    assert "REMOTE_OPEN_RESULT" in prefixes
    assert "IMG_READY" in prefixes

    open_result = next(data for prefix, data in messages if prefix == "REMOTE_OPEN_RESULT")
    assert open_result == {
        "session_id": session_id,
        "uri": uri,
        "ok": True,
    }

    image_payload = next(data for prefix, data in messages if prefix == "IMG_READY")
    assert isinstance(image_payload, bytes)
    assert image_payload