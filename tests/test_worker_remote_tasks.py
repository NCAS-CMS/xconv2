from __future__ import annotations

from io import BytesIO
import json
import logging
from pathlib import Path

import cf
import pytest

import xconv2.worker as worker


class _FakeFilesystem:
    def __init__(self, payload: bytes = b"remote-bytes") -> None:
        self.payload = payload
        self.open_calls: list[tuple[str, str]] = []

    def open(self, path: str, mode: str):
        self.open_calls.append((path, mode))
        return BytesIO(self.payload)


def _build_example_netcdf_bytes(tmp_path: Path, *, tracking_id: str | None = None) -> bytes:
    """Create a tiny NetCDF payload from a cf example field for IO-oriented tests."""
    field = cf.example_field(0)
    if tracking_id:
        field.set_property("tracking_id", tracking_id)
    target = tmp_path / "example.nc"
    cf.write(field, str(target))
    return target.read_bytes()


def _field_identity_for_path(path: Path) -> str:
    """Return the worker metadata identity string for the first field in a file."""
    worker._ensure_worker_runtime_loaded()
    loaded = worker.cf.read(str(path))
    row = worker.cf_interface.field_info(list(loaded))[0]
    return str(row["identity"])


def _derived_identity_for_unary(field: object, operation: str = "grad") -> str:
    """Return metadata identity for a real unary-derived field."""
    worker._ensure_worker_runtime_loaded()
    fields = [field.copy()]
    rows = worker.cf_interface.append_unary_xy_field_operation(fields, 0, operation)
    return str(rows[0]["identity"])


def test_prepare_remote_session_reuses_cached_entry(monkeypatch) -> None:
    worker.remote_session_pool.clear()
    fake_fs = _FakeFilesystem()
    created: list[tuple[str, object]] = []

    monkeypatch.setattr(
        worker,
        "create_filesystem",
        lambda spec, log=None, cache=None: created.append((spec.protocol, cache)) or fake_fs,
    )
    monkeypatch.setattr(worker, "_send_remote_status", lambda *args, **kwargs: None)

    descriptor = {
        "protocol": "sftp",
        "storage_options": {"host": "alpha.example.org"},
        "root_path": ".",
        "display_name": "SSH",
        "uri_scheme": "ssh",
        "uri_authority": "alpha.example.org",
        "proxy_jump": None,
        "cache": {},
    }

    first = worker._prepare_remote_session(
        session_id="session-1",
        descriptor_hash="hash-1",
        descriptor=descriptor,
    )
    second = worker._prepare_remote_session(
        session_id="session-2",
        descriptor_hash="hash-1",
        descriptor=descriptor,
    )

    assert first is second
    assert second.session_id == "session-2"
    assert created == [("sftp", {})]


def test_read_remote_fields_uses_filesystem_keyword(tmp_path: Path) -> None:
    fake_fs = _FakeFilesystem(payload=_build_example_netcdf_bytes(tmp_path))
    entry = worker.RemoteSessionEntry(
        session_id="session-1",
        descriptor_hash="hash-1",
        descriptor={"protocol": "sftp"},
        filesystem=fake_fs,
    )

    fields = worker._read_remote_fields(
        entry=entry,
        descriptor={"protocol": "sftp"},
        datasets="/data/file.nc",
    )

    assert len(fields) == 1
    assert hasattr(fields[0], "identity")
    assert fake_fs.open_calls == [("/data/file.nc", "rb")]


def test_read_remote_fields_supports_multiple_paths(tmp_path: Path) -> None:
    fake_fs = _FakeFilesystem(payload=_build_example_netcdf_bytes(tmp_path))
    entry = worker.RemoteSessionEntry(
        session_id="session-1",
        descriptor_hash="hash-1",
        descriptor={"protocol": "sftp"},
        filesystem=fake_fs,
    )

    fields = worker._read_remote_fields(
        entry=entry,
        descriptor={"protocol": "sftp"},
        datasets=["/data/file-a.nc", "/data/file-b.nc"],
    )

    assert len(fields) == 2
    assert all(hasattr(field, "identity") for field in fields)
    assert fake_fs.open_calls == [
        ("/data/file-a.nc", "rb"),
        ("/data/file-b.nc", "rb"),
    ]


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_read_remote_fields_from_s3_via_minio(minio_service, temp_bucket) -> None:
    """_read_remote_fields returns real cf fields when given a live MinIO S3 filesystem."""
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    descriptor = {
        "protocol": "s3",
        "storage_options": {
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        "root_path": "",
        "display_name": "minio-test",
        "uri_scheme": "s3",
        "uri_authority": "",
        "proxy_jump": None,
    }

    worker.remote_session_pool.clear()
    original_send_to_gui = worker.send_to_gui
    try:
        worker.send_to_gui = lambda prefix, data=None: None
        entry = worker._prepare_remote_session(
            session_id="integration-tasks-session",
            descriptor_hash="integration-tasks-hash",
            descriptor=descriptor,
        )
        fields = worker._read_remote_fields(
            entry=entry,
            descriptor=descriptor,
            datasets=f"{temp_bucket}/{object_name}",
        )
    finally:
        worker.send_to_gui = original_send_to_gui
        worker.remote_session_pool.clear()

    assert fields


def test_normalize_remote_datasets_for_http_overlap_prefix() -> None:
    normalized = worker._normalize_remote_datasets_for_cf_read(
        descriptor={
            "protocol": "http",
            "root_path": "http://server/public/canari",
        },
        datasets="/public/canari/file.nc",
    )

    assert normalized == "http://server/public/canari/file.nc"


def test_normalize_remote_datasets_for_http_relative_path() -> None:
    normalized = worker._normalize_remote_datasets_for_cf_read(
        descriptor={
            "protocol": "http",
            "root_path": "http://server/public/canari",
        },
        datasets="file.nc",
    )

    assert normalized == "http://server/public/canari/file.nc"


def test_normalize_remote_datasets_for_http_list() -> None:
    normalized = worker._normalize_remote_datasets_for_cf_read(
        descriptor={
            "protocol": "http",
            "root_path": "http://server/public/canari",
        },
        datasets=["/public/canari/a.nc", "b.nc"],
    )

    assert normalized == [
        "http://server/public/canari/a.nc",
        "http://server/public/canari/b.nc",
    ]


def test_apply_worker_logging_configuration_updates_remote_runtime_state() -> None:
    original = worker.RemoteAccessSession.logging_configuration()
    pyfive_logger = logging.getLogger("pyfive")
    original_pyfive_level = pyfive_logger.level
    try:
        worker._apply_worker_logging_configuration(
            scope_levels={
                "all": "WARNING",
                "pyfive": "DEBUG",
                "xconv2": "INFO",
            },
        )

        updated = worker.RemoteAccessSession.logging_configuration()
        assert updated.scope_level("all") == logging.WARNING
        assert updated.scope_level("pyfive") == logging.DEBUG
        assert updated.scope_level("xconv2") == logging.INFO
        assert pyfive_logger.level == logging.DEBUG
    finally:
        worker._apply_worker_logging_configuration(
            scope_levels=original.scope_levels,
        )
        pyfive_logger.setLevel(original_pyfive_level)


def test_handle_control_task_logging_configure_forwards_runtime_settings(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    messages: list[tuple[str, object | None]] = []

    monkeypatch.setattr(
        worker,
        "_apply_worker_logging_configuration",
        lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(worker, "send_to_gui", lambda prefix, data=None: messages.append((prefix, data)))

    worker._handle_control_task(
        "LOGGING_CONFIGURE",
        {
            "scope_levels": {
                "all": "ERROR",
                "xconv2": "DEBUG",
            },
        },
    )

    assert calls == [
        {
            "scope_levels": {
                "all": "ERROR",
                "xconv2": "DEBUG",
            },
        }
    ]
    assert messages == [("STATUS:Logging configuration updated", None)]


def test_handle_save_provenance_task_writes_internal_slice(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "slice.json"
    source_path = tmp_path / "source.nc"
    source_field = cf.example_field(0)
    cf.write(source_field, str(source_path))
    source_identity = _field_identity_for_path(source_path)
    derived_identity = _derived_identity_for_unary(source_field, "grad")

    payload = {
        "schema_version": 1,
        "session_id": "session-1",
        "saved_at": "2026-06-09T00:00:00Z",
        "operations": [
            {
                "kind": "unary_xy",
                "field_index": 0,
                "field_ref": {
                    "identity": source_identity,
                    "source_file": str(source_path),
                    "generated": False,
                    "occurrence": 1,
                },
                "operation": "grad",
                "source_file": str(source_path),
            }
        ],
        "selected_field_refs": [
            {
                "identity": derived_identity,
                "source_file": "",
                "generated": True,
                "occurrence": 1,
            }
        ],
        "remote_open_requests": [],
        "destination": str(destination),
        "output_format": "xconv-json",
    }

    messages: list[tuple[str, object | None]] = []
    monkeypatch.setattr(worker, "send_to_gui", lambda prefix, data=None: messages.append((prefix, data)))

    worker._handle_save_provenance_task(payload)

    saved = json.loads(destination.read_text(encoding="utf-8"))
    assert saved["session_id"] == "session-1"
    assert len(saved["operations"]) == 1
    assert saved["operations"][0]["kind"] == "unary_xy"
    assert messages[-1][0].startswith("STATUS:Saved selected provenance")


def test_handle_save_provenance_task_writes_shareable_s3_uri_in_prov_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / "slice.prov.json"
    source_field = cf.example_field(0)
    source_field.set_property("tracking_id", "track-xyz-789")

    # Build identity using the same metadata path the worker uses.
    worker._ensure_worker_runtime_loaded()
    source_identity = str(worker.cf_interface.field_info([source_field])[0]["identity"])
    derived_identity = _derived_identity_for_unary(source_field, "grad")

    monkeypatch.setattr(
        worker,
        "_prepare_remote_session",
        lambda **_kwargs: worker.RemoteSessionEntry(
            session_id="session-remote",
            descriptor_hash="hash-remote",
            descriptor={"protocol": "s3"},
            filesystem=_FakeFilesystem(),
        ),
    )
    monkeypatch.setattr(
        worker,
        "_read_remote_fields",
        lambda **_kwargs: [source_field],
    )

    payload = {
        "schema_version": 1,
        "session_id": "session-1",
        "saved_at": "2026-06-09T00:00:00Z",
        "operations": [
            {
                "kind": "unary_xy",
                "field_index": 0,
                "field_ref": {
                    "identity": source_identity,
                    "source_file": "s3://bnl/CMIP6-test.nc",
                    "generated": False,
                    "occurrence": 1,
                },
                "operation": "grad",
                "source_file": "s3://bnl/CMIP6-test.nc",
            }
        ],
        "selected_field_refs": [
            {
                "identity": derived_identity,
                "source_file": "",
                "generated": True,
                "occurrence": 1,
            }
        ],
        "remote_open_requests": [
            {
                "uri": "s3://bnl/CMIP6-test.nc",
                "session_id": "session-remote",
                "descriptor_hash": "hash-remote",
                "descriptor": {
                    "protocol": "s3",
                    "storage_options": {
                        "client_kwargs": {
                            "endpoint_url": "https://object.example.org",
                        },
                    },
                    "root_path": "",
                    "display_name": "S3",
                    "uri_scheme": "s3",
                    "uri_authority": "",
                    "proxy_jump": None,
                },
                "paths": ["bnl/CMIP6-test.nc"],
            }
        ],
        "destination": str(destination),
        "output_format": "prov-json",
    }

    messages: list[tuple[str, object | None]] = []
    monkeypatch.setattr(worker, "send_to_gui", lambda prefix, data=None: messages.append((prefix, data)))

    worker._handle_save_provenance_task(payload)

    saved = json.loads(destination.read_text(encoding="utf-8"))
    entities = saved["entity"]
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
    assert source_entities[0]["dcterms:identifier"] == "track-xyz-789"

    activities = saved["activity"]
    assert isinstance(activities, dict)
    workflow_blob = activities["xconv:session_session-1"]["xconv:workflow_json"]
    assert isinstance(workflow_blob, str)
    assert '"source_file": "s3://bnl/CMIP6-test.nc"' in workflow_blob
    assert '"runtime_versions"' in workflow_blob
    assert '"cf_python"' in workflow_blob
    assert '"xconv2"' in workflow_blob

    agents = saved["agent"]
    assert isinstance(agents, dict)
    software_agent = agents["xconv:xconv2"]
    assert isinstance(software_agent.get("xconv:xconv2_version"), str)
    assert software_agent.get("xconv:xconv2_version")
    assert isinstance(software_agent.get("xconv:cf_python_version"), str)
    assert software_agent.get("xconv:cf_python_version")
    assert messages[-1][0].startswith("STATUS:Saved selected provenance")