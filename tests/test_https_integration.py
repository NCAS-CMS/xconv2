from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

import pytest
import pyfive

from xconv2.remote_access import RemoteAccessSession, RemoteFilesystemSpec, create_filesystem


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        find_spec("cryptography") is None,
        reason="Local HTTPS tests require cryptography; install xconv2[test]. No Docker is used.",
    ),
]


def test_https_serves_netcdf_for_remote_filesystem(
    tmp_path: Path,
    https_service,
) -> None:
    """Exercise HTTPS file reads through RemoteFileSystemFactory/ShimmyFS."""
    spec = RemoteFilesystemSpec(
        protocol="http",
        storage_options=dict(https_service["storage_options"]),
        root_path=str(https_service["base_url"]),
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

    target = str(https_service["test_file_url"])
    with filesystem.open(target, "rb") as handle:
        with pyfive.File(handle) as remote_file:
            names = list(remote_file.keys())

    assert names


def test_https_with_index_html_directory_listing_behavior(
    https_index_service,
) -> None:
    """Exercise remote browser listing path when server root serves index.html.

    This verifies whether our listing layer crashes or simply returns whatever the
    HTTP backend can infer from the served index page.
    """
    spec = RemoteFilesystemSpec(
        protocol="http",
        storage_options=dict(https_index_service["storage_options"]),
        root_path=str(https_index_service["base_url"]),
        display_name="HTTPS",
        uri_scheme="",
        uri_authority="",
    )
    filesystem = create_filesystem(spec, cache=None)
    session = RemoteAccessSession(filesystem)

    entries = session.list_entries(str(https_index_service["base_url"]))

    assert isinstance(entries, list)
