from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from xconv2.ui.remote_file_navigator import (
    RemoteFilesystemSpec,
    _parse_proxy_jump,
    build_remote_filesystem_spec,
    build_remote_uri,
    directory_contains_zarr_metadata,
    filter_hidden_entries,
    filter_type_entries,
    format_size,
    is_zarr_path,
    normalize_remote_entries,
    resolve_link_entries,
)


def test_build_s3_filesystem_spec_uses_endpoint_and_credentials() -> None:
    config = {
        "protocol": "S3",
        "remote": {
            "mode": "Select from existing",
            "alias": "cedadev",
            "details": {
                "url": "https://example.invalid",
                "accessKey": "abc",
                "secretKey": "xyz",
            },
        },
    }

    spec = build_remote_filesystem_spec(config)

    assert spec.protocol == "s3"
    assert spec.storage_options["key"] == "abc"
    assert spec.storage_options["secret"] == "xyz"
    assert spec.storage_options["client_kwargs"] == {"endpoint_url": "https://example.invalid"}
    assert spec.root_path == ""


def test_build_ssh_filesystem_spec_uses_existing_host_details() -> None:
    config = {
        "protocol": "SSH",
        "remote": {
            "mode": "Select from existing",
            "alias": "alpha",
            "details": {
                "hostname": "alpha.example.org",
                "user": "alice",
                "identityfile": "~/.ssh/id_alpha",
            },
        },
    }

    spec = build_remote_filesystem_spec(config)

    assert spec.protocol == "sftp"
    assert spec.storage_options == {
        "host": "alpha.example.org",
        "username": "alice",
        "key_filename": str((Path.home() / ".ssh/id_alpha").expanduser()),
    }
    assert spec.root_path == "."


def test_normalize_remote_entries_sorts_dirs_first() -> None:
    entries = normalize_remote_entries([
        {"name": "bucket/file.nc", "type": "file", "size": 12},
        {"name": "bucket/folder", "type": "directory", "size": 0},
    ])

    assert [entry.name for entry in entries] == ["folder", "file.nc"]
    assert entries[0].is_dir is True
    assert entries[1].size == 12


def test_normalize_remote_entries_marks_symlinks() -> None:
    entries = normalize_remote_entries([
        {"name": "link_to_dir", "type": "link", "size": 0},
        {"name": "link_to_file.nc", "type": "symlink", "size": 10},
    ])

    assert entries[0].is_link is True
    assert entries[1].is_link is True


def test_resolve_link_entries_promotes_directory_symlink() -> None:
    entries = normalize_remote_entries([
        {"name": "folder_link", "type": "link", "size": 0},
        {"name": "data.nc", "type": "file", "size": 10},
    ])

    fake_fs = SimpleNamespace(isdir=lambda path: path == "folder_link")
    resolved = resolve_link_entries(entries, fake_fs)

    assert resolved[0].name == "folder_link"
    assert resolved[0].is_link is True
    assert resolved[0].is_dir is True
    assert resolved[1].name == "data.nc"


def test_filter_hidden_entries_excludes_dot_prefixed_names_by_default() -> None:
    entries = normalize_remote_entries([
        {"name": ".ssh", "type": "directory", "size": 0},
        {"name": "visible.txt", "type": "file", "size": 12},
    ])

    visible_entries = filter_hidden_entries(entries, show_hidden=False)

    assert [entry.name for entry in visible_entries] == ["visible.txt"]
    assert [entry.name for entry in filter_hidden_entries(entries, show_hidden=True)] == [".ssh", "visible.txt"]


def test_filter_type_entries_keeps_only_nc_pp_and_dirs_by_default() -> None:
    entries = normalize_remote_entries([
        {"name": "folder", "type": "directory", "size": 0},
        {"name": "data.nc", "type": "file", "size": 100},
        {"name": "model.pp", "type": "file", "size": 200},
        {"name": "readme.txt", "type": "file", "size": 50},
        {"name": "archive.zip", "type": "file", "size": 1024},
    ])

    filtered = filter_type_entries(entries, show_all=False)
    assert [entry.name for entry in filtered] == ["folder", "data.nc", "model.pp"]

    all_entries = filter_type_entries(entries, show_all=True)
    assert len(all_entries) == 5


def test_build_ssh_filesystem_spec_captures_proxy_jump() -> None:
    config = {
        "protocol": "SSH",
        "remote": {
            "mode": "Select from existing",
            "alias": "target",
            "details": {
                "hostname": "target.example.org",
                "user": "bob",
                "identityfile": "~/.ssh/id_target",
                "proxyjump": "login.example.org",
            },
        },
    }

    spec = build_remote_filesystem_spec(config)

    assert spec.protocol == "sftp"
    assert spec.storage_options["host"] == "target.example.org"
    assert spec.proxy_jump == "login.example.org"


def test_parse_proxy_jump_handles_user_host_and_port() -> None:
    assert _parse_proxy_jump("login.example.org") == (None, "login.example.org", 22)
    assert _parse_proxy_jump("alice@login.example.org") == ("alice", "login.example.org", 22)
    assert _parse_proxy_jump("alice@login.example.org:2222") == ("alice", "login.example.org", 2222)
    # chained jumps – only first hop used
    assert _parse_proxy_jump("hop1.example.org,hop2.example.org") == (None, "hop1.example.org", 22)


def test_build_remote_uri_for_s3_and_ssh() -> None:
    s3_spec = RemoteFilesystemSpec("s3", {}, "", "S3", "s3", "")
    ssh_spec = RemoteFilesystemSpec("sftp", {}, "/", "SSH", "ssh", "alpha.example.org")

    assert build_remote_uri(s3_spec, "bucket/folder/file.nc") == "s3://bucket/folder/file.nc"
    assert build_remote_uri(ssh_spec, "/data/file.nc") == "ssh://alpha.example.org/data/file.nc"


def test_format_size_uses_human_units() -> None:
    assert format_size(None) == ""
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1 KB"
    assert format_size(1536) == "1.5 KB"
    assert format_size(1024 * 1024) == "1 MB"
    assert format_size(5 * 1024 * 1024 * 1024) == "5 GB"


def test_is_zarr_path_detects_zarr_suffix() -> None:
    assert is_zarr_path("dataset.zarr") is True
    assert is_zarr_path("bucket/path/to/dataset.zarr/") is True
    assert is_zarr_path("bucket/path/to/dataset.ZARR") is True
    assert is_zarr_path("dataset.nc") is False


def test_directory_contains_zarr_metadata_detects_v2_and_v3_markers() -> None:
    v2_entries = normalize_remote_entries([
        {"name": "store/.zgroup", "type": "file", "size": 1},
        {"name": "store/0.0", "type": "file", "size": 1},
    ])
    v3_entries = normalize_remote_entries([
        {"name": "store/zarr.json", "type": "file", "size": 1},
    ])
    other_entries = normalize_remote_entries([
        {"name": "store/readme.txt", "type": "file", "size": 1},
    ])

    assert directory_contains_zarr_metadata(v2_entries) is True
    assert directory_contains_zarr_metadata(v3_entries) is True
    assert directory_contains_zarr_metadata(other_entries) is False