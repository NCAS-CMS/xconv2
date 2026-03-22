from __future__ import annotations

from io import BytesIO

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
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(worker.cf, "read", lambda datasets, filesystem=None: calls.append((datasets, filesystem)) or ["fields"])

    fields = worker._read_remote_fields(
        entry=entry,
        descriptor={"protocol": "sftp"},
        datasets="/data/file.nc",
    )

    assert fields == ["fields"]
    assert calls == [("/data/file.nc", fake_fs)]


def test_read_remote_fields_supports_multiple_paths(monkeypatch) -> None:
    fake_fs = _FakeFilesystem()
    entry = worker.RemoteSessionEntry(
        session_id="session-1",
        descriptor_hash="hash-1",
        descriptor={"protocol": "sftp"},
        filesystem=fake_fs,
    )
    calls: list[tuple[object, object]] = []

    monkeypatch.setattr(worker.cf, "read", lambda datasets, filesystem=None: calls.append((datasets, filesystem)) or ["fields"])

    fields = worker._read_remote_fields(
        entry=entry,
        descriptor={"protocol": "sftp"},
        datasets=["/data/file-a.nc", "/data/file-b.nc"],
    )

    assert fields == ["fields"]
    assert calls == [(["/data/file-a.nc", "/data/file-b.nc"], fake_fs)]


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