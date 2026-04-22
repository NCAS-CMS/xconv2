from __future__ import annotations

from pathlib import Path

import pytest

from xconv2.remote_access import RemoteFilesystemSpec, create_filesystem


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
