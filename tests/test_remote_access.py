from __future__ import annotations

import logging
from types import SimpleNamespace

from xconv2.remote_access import (
    RemoteAccessSession,
    normalize_remote_datasets_for_cf_read,
    normalize_remote_entries,
    resolve_link_entries,
)


def test_remote_access_list_entries_normalizes_and_resolves_links() -> None:
    class _FakeFs:
        def ls(self, path: str, detail: bool = True):
            assert path == "bucket"
            assert detail is True
            return [
                {"name": "bucket/folder", "type": "directory", "size": 0},
                {"name": "bucket/link_to_dir", "type": "link", "size": 0},
                {"name": "bucket/file.nc", "type": "file", "size": 10},
            ]

        def isdir(self, path: str) -> bool:
            return path == "bucket/link_to_dir"

    session = RemoteAccessSession(_FakeFs())
    entries = session.list_entries("bucket")

    assert [entry.name for entry in entries] == ["folder", "link_to_dir", "file.nc"]
    assert entries[1].is_dir is True
    assert entries[1].is_link is True


def test_remote_access_read_fields_normalizes_http_datasets() -> None:
    calls: list[tuple[object, object]] = []

    def _reader(datasets, filesystem=None):
        calls.append((datasets, filesystem))
        return ["fields"]

    fs = SimpleNamespace()
    session = RemoteAccessSession(fs)
    result = session.read_fields(
        descriptor={"protocol": "http", "root_path": "https://server/public/canari"},
        datasets="/public/canari/data.nc",
        reader=_reader,
    )

    assert result == ["fields"]
    assert calls == [
        ("https://server/public/canari/data.nc", fs),
    ]


def test_normalize_remote_datasets_for_cf_read_passthrough_non_http() -> None:
    datasets = ["bucket/a.nc", "bucket/b.nc"]
    normalized = normalize_remote_datasets_for_cf_read(
        descriptor={"protocol": "s3", "root_path": ""},
        datasets=datasets,
    )
    assert normalized == datasets


def test_normalize_remote_entries_and_resolve_link_entries_helpers() -> None:
    entries = normalize_remote_entries(
        [
            {"name": "root/file.nc", "type": "file", "size": 11},
            {"name": "root/link", "type": "symlink", "size": 0},
        ]
    )

    resolved = resolve_link_entries(entries, SimpleNamespace(isdir=lambda p: p == "root/link"))
    assert resolved[0].name == "link"
    assert resolved[0].is_dir is True
    assert resolved[1].name == "file.nc"


def test_remote_access_session_configure_logging_updates_shared_runtime_state() -> None:
    original = RemoteAccessSession.logging_configuration()
    try:
        updated = RemoteAccessSession.configure_logging(
            level="DEBUG",
            trace_filesystem=True,
            trace_file_io=True,
        )

        assert updated.level == logging.DEBUG
        assert updated.trace_filesystem is True
        assert updated.trace_file_io is True
        assert RemoteAccessSession.logging_configuration() == updated
    finally:
        RemoteAccessSession.configure_logging(
            level=original.level,
            trace_filesystem=original.trace_filesystem,
            trace_file_io=original.trace_file_io,
        )
