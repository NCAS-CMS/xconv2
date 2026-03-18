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
    created: list[str] = []

    monkeypatch.setattr(worker, "create_filesystem", lambda spec, log=None: created.append(spec.protocol) or fake_fs)
    monkeypatch.setattr(worker, "_send_remote_status", lambda *args, **kwargs: None)

    descriptor = {
        "protocol": "sftp",
        "storage_options": {"host": "alpha.example.org"},
        "root_path": ".",
        "display_name": "SSH",
        "uri_scheme": "ssh",
        "uri_authority": "alpha.example.org",
        "proxy_jump": None,
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
    assert created == ["sftp"]


def test_read_remote_fields_uses_filesystem_keyword_when_supported(monkeypatch) -> None:
    fake_fs = _FakeFilesystem()
    entry = worker.RemoteSessionEntry(
        session_id="session-1",
        descriptor_hash="hash-1",
        descriptor={"protocol": "sftp"},
        filesystem=fake_fs,
    )
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(worker, "_cf_read_supports_filesystem", lambda: True)
    monkeypatch.setattr(worker.cf, "read", lambda path, filesystem=None: calls.append((path, filesystem)) or ["fields"])

    fields = worker._read_remote_fields(
        entry=entry,
        descriptor={"protocol": "sftp"},
        uri="ssh://alpha.example.org/data/file.nc",
        path="/data/file.nc",
    )

    assert fields == ["fields"]
    assert calls == [("/data/file.nc", fake_fs)]


def test_read_remote_fields_stages_sftp_file_when_filesystem_keyword_missing(monkeypatch, tmp_path) -> None:
    fake_fs = _FakeFilesystem(b"abc123")
    entry = worker.RemoteSessionEntry(
        session_id="session-1",
        descriptor_hash="hash-1",
        descriptor={"protocol": "sftp"},
        filesystem=fake_fs,
    )
    calls: list[str] = []

    monkeypatch.setattr(worker, "_cf_read_supports_filesystem", lambda: False)

    def _fake_cf_read(path: str, **kwargs):
        _ = kwargs
        calls.append(path)
        with open(path, "rb") as handle:
            assert handle.read() == b"abc123"
        return ["fields"]

    monkeypatch.setattr(worker.cf, "read", _fake_cf_read)

    fields = worker._read_remote_fields(
        entry=entry,
        descriptor={"protocol": "sftp"},
        uri="ssh://alpha.example.org/data/file.nc",
        path="/data/file.nc",
    )

    assert fields == ["fields"]
    assert len(calls) == 1
    assert not fake_fs.open_calls == []