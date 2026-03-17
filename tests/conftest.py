from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import pytest


MINIO_IMAGE = "quay.io/minio/minio:latest"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
MINIO_PORT = 9000
MINIO_CONSOLE_PORT = 9001


def _docker_available() -> bool:
    """Return True when Docker is installed and the daemon is reachable."""
    if shutil.which("docker") is None:
        return False

    try:
        import docker  # type: ignore
    except ImportError:
        return False

    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return False

    return True


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: tests that exercise real external services such as a temporary MinIO server",
    )


@pytest.fixture(autouse=True)
def silence_noisy_loggers() -> None:
    """Keep third-party logging from dominating test output."""
    logging.getLogger("cfdm").setLevel(logging.WARNING)
    logging.getLogger("cfdm.read_write.netcdf.netcdfwrite").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)


@pytest.fixture(scope="session")
def minio_service():
    """Run a temporary MinIO server in Docker and return a configured client."""
    if not _docker_available():
        pytest.skip("Docker-backed MinIO integration tests require docker, docker-py, and a reachable daemon")

    import docker  # type: ignore
    from minio import Minio  # type: ignore

    client = docker.from_env()
    container = client.containers.run(
        MINIO_IMAGE,
        command=["server", "/data", "--console-address", f":{MINIO_CONSOLE_PORT}"],
        environment={
            "MINIO_ROOT_USER": MINIO_ACCESS_KEY,
            "MINIO_ROOT_PASSWORD": MINIO_SECRET_KEY,
        },
        # Use ephemeral host ports to avoid conflicts with local services.
        ports={f"{MINIO_PORT}/tcp": None, f"{MINIO_CONSOLE_PORT}/tcp": None},
        detach=True,
        remove=True,
        name=f"xconv2-minio-test-{uuid.uuid4()}",
    )

    container.reload()
    network_ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
    api_mappings = network_ports.get(f"{MINIO_PORT}/tcp") or []
    if not api_mappings:
        container_logs = container.logs().decode(errors="replace")
        container.stop()
        pytest.fail(f"MinIO container did not expose API port mapping. Logs:\n{container_logs}")

    host_port = int(api_mappings[0]["HostPort"])
    endpoint_url = f"http://localhost:{host_port}"

    minio_client = Minio(
        f"localhost:{host_port}",
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )
    setattr(minio_client, "endpoint_url", endpoint_url)

    try:
        for _ in range(30):
            try:
                minio_client.list_buckets()
                break
            except Exception:
                time.sleep(0.5)
        else:
            logs = container.logs().decode(errors="replace")
            pytest.fail(f"MinIO did not start in time. Logs:\n{logs}")

        yield minio_client
    finally:
        try:
            container.stop()
        except Exception:
            pass


@pytest.fixture
def temp_bucket(minio_service):
    """Create a temporary bucket for an individual integration test."""
    bucket_name = f"test-{uuid.uuid4()}"
    minio_service.make_bucket(bucket_name)
    try:
        yield bucket_name
    finally:
        try:
            for obj in minio_service.list_objects(bucket_name, recursive=True):
                minio_service.remove_object(bucket_name, obj.object_name)
            minio_service.remove_bucket(bucket_name)
        except Exception:
            pass


@pytest.fixture
def fake_mc_config(monkeypatch: pytest.MonkeyPatch, minio_service) -> Path:
    """Simulate a MinIO-style ~/.mc/config.json file for S3 configuration loading."""
    tmpdir = Path(tempfile.mkdtemp(prefix="mc_cfg_"))
    mc_dir = tmpdir / ".mc"
    mc_dir.mkdir(parents=True, exist_ok=True)
    config_path = mc_dir / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "10",
                "aliases": {
                    "fake-alias": {
                        "url": getattr(minio_service, "endpoint_url", f"http://localhost:{MINIO_PORT}"),
                        "accessKey": MINIO_ACCESS_KEY,
                        "secretKey": MINIO_SECRET_KEY,
                        "api": "S3v4",
                        "path": "auto",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmpdir))
    return config_path