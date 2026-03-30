from __future__ import annotations

import logging
from types import SimpleNamespace
import pytest

from xconv2.remote_access import (
    RemoteAccessSession,
    build_remote_filesystem_spec,
    create_filesystem,
    normalize_remote_datasets_for_cf_read,
    normalize_remote_entries,
    resolve_link_entries,
)
from xconv2.logging_utils import apply_scoped_runtime_logging


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


def test_remote_access_read_fields_opens_http_dataset_handles() -> None:
    calls: list[object] = []

    class _Handle:
        def __init__(self, path: str) -> None:
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    class _Fs:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def open(self, path: str, mode: str = "rb", **kwargs):
            assert mode == "rb"
            assert kwargs == {}
            self.paths.append(path)
            return _Handle(path)

    def _reader(datasets, filesystem=None):
        calls.append((datasets, filesystem))
        return ["fields"]

    fs = _Fs()
    session = RemoteAccessSession(fs)
    result = session.read_fields(
        descriptor={"protocol": "http", "root_path": "https://server/public/canari"},
        datasets="/public/canari/data.nc",
        reader=_reader,
    )

    assert result == ["fields"]
    assert fs.paths == ["https://server/public/canari/data.nc"]
    assert len(calls) == 1
    handle, filesystem = calls[0]
    assert getattr(handle, "path", None) == "https://server/public/canari/data.nc"
    assert filesystem is None


def test_remote_access_read_fields_opens_s3_dataset_handles() -> None:
    calls: list[object] = []

    class _Handle:
        def __init__(self, path: str) -> None:
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    class _Fs:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def open(self, path: str, mode: str = "rb", **kwargs):
            assert mode == "rb"
            assert kwargs == {}
            self.paths.append(path)
            return _Handle(path)

    def _reader(datasets, filesystem=None):
        calls.append((datasets, filesystem))
        return ["fields"]

    fs = _Fs()
    session = RemoteAccessSession(fs)
    result = session.read_fields(
        descriptor={"protocol": "s3", "root_path": ""},
        datasets="bucket/data.nc",
        reader=_reader,
    )

    assert result == ["fields"]
    assert fs.paths == ["bucket/data.nc"]
    assert len(calls) == 1
    handle, filesystem = calls[0]
    assert getattr(handle, "path", None) == "bucket/data.nc"
    assert filesystem is None


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
            scope_levels={
                "all": "WARNING",
                "xconv2": "DEBUG",
                "fsspec": "INFO",
            },
        )

        assert updated.scope_level("xconv2") == logging.DEBUG
        assert updated.scope_level("fsspec") == logging.INFO
        assert updated.scope_level("all") == logging.WARNING
        assert RemoteAccessSession.logging_configuration() == updated
    finally:
        RemoteAccessSession.configure_logging(
            scope_levels=original.scope_levels,
        )


def test_apply_scoped_runtime_logging_updates_existing_fsspec_child_loggers() -> None:
    cached_logger = logging.getLogger("fsspec.cached")
    http_logger = logging.getLogger("fsspec.http")
    original_cached_level = cached_logger.level
    original_http_level = http_logger.level

    try:
        cached_logger.setLevel(logging.WARNING)
        http_logger.setLevel(logging.ERROR)

        applied = apply_scoped_runtime_logging(
            {
                "all": "WARNING",
                "fsspec": "DEBUG",
            }
        )

        assert applied["fsspec"] == logging.DEBUG
        assert cached_logger.level == logging.DEBUG
        assert http_logger.level == logging.DEBUG
    finally:
        cached_logger.setLevel(original_cached_level)
        http_logger.setLevel(original_http_level)


def test_build_remote_filesystem_spec_s3_requires_endpoint_url() -> None:
    with pytest.raises(ValueError, match="requires an endpoint URL"):
        build_remote_filesystem_spec(
            {
                "protocol": "S3",
                "remote": {
                    "details": {
                        "accessKey": "abc",
                        "secretKey": "xyz",
                    }
                },
            }
        )


def test_create_filesystem_s3_uses_caching_wrapper() -> None:
    """Verify S3 filesystem creation always goes through caching wrapper."""
    spec = build_remote_filesystem_spec(
        {
            "protocol": "S3",
            "remote": {
                "alias": "ceda",
                "details": {
                    "url": "https://uor-aces-o.s3-ext.jc.rl.ac.uk",
                    "accessKey": "abc",
                    "secretKey": "xyz",
                },
            },
        }
    )

    # Create without cache - should still work, just no disk caching layer
    fs = create_filesystem(spec, cache=None)
    assert fs is not None

    # Create with cache - should include caching layer
    fs_cached = create_filesystem(spec, cache={"disk_mode": "Blocks", "disk_location": "/tmp/cache"})
    assert fs_cached is not None


def test_create_filesystem_s3_accepts_schemeless_endpoint_with_bucket_path() -> None:
    spec = build_remote_filesystem_spec(
        {
            "protocol": "S3",
            "remote": {
                "alias": "local-minio",
                "details": {
                    "url": "localhost:50686/bucket",
                    "accessKey": "abc",
                    "secretKey": "xyz",
                },
            },
        }
    )

    fs = create_filesystem(spec, cache=None)
    assert fs is not None


def test_build_remote_filesystem_spec_ssh_includes_remote_python_and_login_shell() -> None:
    spec = build_remote_filesystem_spec(
        {
            "protocol": "SSH",
            "remote": {
                "alias": "my-host",
                "details": {
                    "hostname": "example.org",
                    "user": "alice",
                    "remote_python": "conda run -n work26 python",
                    "login_shell": "true",
                },
            },
        }
    )

    assert spec.protocol == "sftp"
    assert spec.storage_options["host"] == "example.org"
    assert spec.storage_options["username"] == "alice"
    assert spec.storage_options["remote_python"] == "conda run -n work26 python"
    assert spec.storage_options["login_shell"] is True


def test_build_remote_filesystem_spec_ssh_reads_remote_python_from_add_new_shape() -> None:
    spec = build_remote_filesystem_spec(
        {
            "protocol": "SSH",
            "remote": {
                "alias": "my-host",
                "hostname": "example.org",
                "user": "alice",
                "remote_python": "conda run -p /opt/miniforge3/envs/work26 --no-capture-output python",
                "login_shell": False,
            },
        }
    )

    assert spec.protocol == "sftp"
    assert spec.storage_options["host"] == "example.org"
    assert spec.storage_options["username"] == "alice"
    assert spec.storage_options["remote_python"] == "conda run -p /opt/miniforge3/envs/work26 --no-capture-output python"
    assert spec.storage_options["login_shell"] is False
