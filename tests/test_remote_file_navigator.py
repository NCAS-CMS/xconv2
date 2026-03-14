from __future__ import annotations

from xconv2.ui.remote_file_navigator import (
    RemoteFilesystemSpec,
    build_remote_filesystem_spec,
    build_remote_uri,
    normalize_remote_entries,
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
        "key_filename": "~/.ssh/id_alpha",
    }
    assert spec.root_path == "/"


def test_normalize_remote_entries_sorts_dirs_first() -> None:
    entries = normalize_remote_entries([
        {"name": "bucket/file.nc", "type": "file", "size": 12},
        {"name": "bucket/folder", "type": "directory", "size": 0},
    ])

    assert [entry.name for entry in entries] == ["folder", "file.nc"]
    assert entries[0].is_dir is True
    assert entries[1].size == 12


def test_build_remote_uri_for_s3_and_ssh() -> None:
    s3_spec = RemoteFilesystemSpec("s3", {}, "", "S3", "s3", "")
    ssh_spec = RemoteFilesystemSpec("sftp", {}, "/", "SSH", "ssh", "alpha.example.org")

    assert build_remote_uri(s3_spec, "bucket/folder/file.nc") == "s3://bucket/folder/file.nc"
    assert build_remote_uri(ssh_spec, "/data/file.nc") == "ssh://alpha.example.org/data/file.nc"