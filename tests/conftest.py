from __future__ import annotations

import logging
import os
import pytest

from service_helpers import run_https_service, run_s3_service


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def silence_noisy_loggers() -> None:
    """Keep third-party logging from dominating test output."""
    logging.getLogger("cfdm").setLevel(logging.WARNING)
    logging.getLogger("cfdm.read_write.netcdf.netcdfwrite").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)


@pytest.fixture(scope="session")
def s3_service(tmp_path_factory: pytest.TempPathFactory):
    """Run an isolated native SeaweedFS process, never a container."""
    with run_s3_service(tmp_path_factory.mktemp("seaweedfs")) as service:
        yield service


@pytest.fixture
def temp_bucket(s3_service):
    bucket = s3_service.create_bucket()
    try:
        yield bucket
    finally:
        s3_service.filesystem.rm(bucket, recursive=True)


@pytest.fixture(scope="session")
def https_service(tmp_path_factory: pytest.TempPathFactory):
    with run_https_service(tmp_path_factory.mktemp("https")) as service:
        yield service


@pytest.fixture(scope="session")
def https_index_service(tmp_path_factory: pytest.TempPathFactory):
    with run_https_service(tmp_path_factory.mktemp("https_index"), index=True) as service:
        yield service
