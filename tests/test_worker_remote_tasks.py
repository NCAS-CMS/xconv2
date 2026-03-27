from __future__ import annotations

from io import BytesIO
import logging
from pathlib import Path

import pytest

import xconv2.worker as worker


class _FakeFilesystem:
    def __init__(self, payload: bytes = b"remote-bytes") -> None:
        self.payload = payload
        self.open_calls: list[tuple[str, str]] = []

    def open(self, path: str, mode: str):
        self.open_calls.append((path, mode))
        return BytesIO(self.payload)


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
        "cache": {"cache_strategy": "Readahead"},
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
    assert created == [("sftp", {"cache_strategy": "Readahead"})]


def test_read_remote_fields_uses_filesystem_keyword(monkeypatch) -> None:
    fake_fs = _FakeFilesystem()
    entry = worker.RemoteSessionEntry(
        session_id="session-1",
        descriptor_hash="hash-1",
        descriptor={"protocol": "sftp"},
        filesystem=fake_fs,
    )
    calls: list[tuple[bytes, object]] = []

    def _fake_read(datasets, filesystem=None):
        calls.append((datasets.read(), filesystem))
        return ["fields"]

    monkeypatch.setattr(worker.cf, "read", _fake_read)

    fields = worker._read_remote_fields(
        entry=entry,
        descriptor={"protocol": "sftp"},
        datasets="/data/file.nc",
    )

    assert fields == ["fields"]
    assert fake_fs.open_calls == [("/data/file.nc", "rb")]
    assert calls == [(b"remote-bytes", None)]


def test_read_remote_fields_supports_multiple_paths(monkeypatch) -> None:
    fake_fs = _FakeFilesystem()
    entry = worker.RemoteSessionEntry(
        session_id="session-1",
        descriptor_hash="hash-1",
        descriptor={"protocol": "sftp"},
        filesystem=fake_fs,
    )
    calls: list[tuple[list[bytes], object]] = []

    def _fake_read(datasets, filesystem=None):
        calls.append(([handle.read() for handle in datasets], filesystem))
        return ["fields"]

    monkeypatch.setattr(worker.cf, "read", _fake_read)

    fields = worker._read_remote_fields(
        entry=entry,
        descriptor={"protocol": "sftp"},
        datasets=["/data/file-a.nc", "/data/file-b.nc"],
    )

    assert fields == ["fields"]
    assert fake_fs.open_calls == [
        ("/data/file-a.nc", "rb"),
        ("/data/file-b.nc", "rb"),
    ]
    assert calls == [([b"remote-bytes", b"remote-bytes"], None)]


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
            level="DEBUG",
            trace_remote_fs=True,
            trace_remote_file_io=True,
        )

        updated = worker.RemoteAccessSession.logging_configuration()
        assert updated.level == logging.DEBUG
        assert updated.trace_filesystem is True
        assert updated.trace_file_io is True
        assert pyfive_logger.level == logging.DEBUG
    finally:
        worker._apply_worker_logging_configuration(
            level=original.level,
            trace_remote_fs=original.trace_filesystem,
            trace_remote_file_io=original.trace_file_io,
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
            "level": "ERROR",
            "trace_remote_fs": True,
            "trace_remote_file_io": False,
        },
    )

    assert calls == [
        {
            "level": "ERROR",
            "trace_remote_fs": True,
            "trace_remote_file_io": False,
        }
    ]
    assert messages == [("STATUS:Logging configuration updated", None)]