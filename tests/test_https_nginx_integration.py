from __future__ import annotations

from pathlib import Path

import pytest

from xconv2.remote_access import RemoteAccessSession, RemoteFilesystemSpec, create_filesystem


@pytest.mark.integration
def test_https_nginx_serves_netcdf_for_remote_filesystem(
    tmp_path: Path,
    nginx_https_service,
) -> None:
    """Exercise HTTPS file reads through RemoteFileSystemFactory/ShimmyFS."""
    pyfive = pytest.importorskip("pyfive")

    spec = RemoteFilesystemSpec(
        protocol="http",
        storage_options=dict(nginx_https_service["storage_options"]),
        root_path=str(nginx_https_service["base_url"]),
        display_name="HTTPS",
        uri_scheme="",
        uri_authority="",
    )

    filesystem = create_filesystem(
        spec,
        cache={
            "disk_mode": "Blocks",
            "disk_location": str(tmp_path / "https-cache"),
            "disk_expiry": "1 day",
            "disk_limit_gb": 1,
        },
    )

    target = str(nginx_https_service["test_file_url"])
    with filesystem.open(target, "rb") as handle:
        with pyfive.File(handle) as remote_file:
            names = list(remote_file.keys())

    assert names


@pytest.mark.integration
def test_https_nginx_with_index_html_directory_listing_behavior(
    nginx_https_index_service,
) -> None:
    """Exercise remote browser listing path when server root serves index.html.

    This verifies whether our listing layer crashes or simply returns whatever the
    HTTP backend can infer from the served index page.
    """
    spec = RemoteFilesystemSpec(
        protocol="http",
        storage_options=dict(nginx_https_index_service["storage_options"]),
        root_path=str(nginx_https_index_service["base_url"]),
        display_name="HTTPS",
        uri_scheme="",
        uri_authority="",
    )
    filesystem = create_filesystem(spec, cache=None)
    session = RemoteAccessSession(filesystem)

    entries = session.list_entries(str(nginx_https_index_service["base_url"]))

    assert isinstance(entries, list)
