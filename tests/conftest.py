from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


MINIO_IMAGE = "quay.io/minio/minio:latest"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
MINIO_PORT = 9000
MINIO_CONSOLE_PORT = 9001


def _docker_status() -> tuple[bool, str | None]:
    """Return Docker readiness and an actionable message when unavailable."""
    if shutil.which("docker") is None:
        return False, "Docker CLI not found. Install Docker and ensure `docker` is on PATH."

    try:
        import docker  # type: ignore
    except ImportError:
        return False, "Python package `docker` is not installed in this environment."

    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        return (
            False,
            "Docker daemon is not reachable. Start Docker Desktop/daemon and rerun tests. "
            f"Original error: {exc}",
        )

    return True, None


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
    docker_ready, docker_msg = _docker_status()
    if not docker_ready:
        if docker_msg and "daemon is not reachable" in docker_msg:
            pytest.fail(docker_msg)
        pytest.skip(docker_msg or "Docker-backed MinIO integration tests require docker and docker-py")

    import docker  # type: ignore
    from minio import Minio  # type: ignore

    client = docker.from_env()
    container = client.containers.run(
        MINIO_IMAGE,
        command=["server", "/data", "--console-address", f":{MINIO_CONSOLE_PORT}"],
        environment={
            "MINIO_ROOT_USER": MINIO_ACCESS_KEY,
            "MINIO_ROOT_PASSWORD": MINIO_SECRET_KEY,
            # Expose Prometheus metrics without auth so tests can sample counters.
            "MINIO_PROMETHEUS_AUTH_TYPE": "public",
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
    setattr(minio_client, "metrics_url", f"{endpoint_url}/minio/v2/metrics/cluster")

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


@pytest.fixture(scope="session")
def nginx_https_service(tmp_path_factory: pytest.TempPathFactory):
    """Run a temporary nginx HTTPS server in Docker and return connection details."""
    docker_ready, docker_msg = _docker_status()
    if not docker_ready:
        if docker_msg and "daemon is not reachable" in docker_msg:
            pytest.fail(docker_msg)
        pytest.skip(docker_msg or "Docker-backed nginx HTTPS integration tests require docker and docker-py")

    try:
        import docker  # type: ignore
    except ImportError:
        pytest.skip("docker-py is required for nginx HTTPS integration tests")

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        pytest.skip("cryptography is required for nginx HTTPS integration tests")

    workspace = tmp_path_factory.mktemp("nginx_https")
    content_dir = workspace / "www"
    cert_dir = workspace / "certs"
    content_dir.mkdir(parents=True, exist_ok=True)
    cert_dir.mkdir(parents=True, exist_ok=True)

    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    if not sample_file.is_file():
        pytest.skip("HTTPS integration test requires data/test1.nc")
    shutil.copyfile(sample_file, content_dir / "test1.nc")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "GB"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "xconv2 tests"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_path = cert_dir / "localhost.crt"
    key_path = cert_dir / "localhost.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    nginx_conf = workspace / "default.conf"
    nginx_conf.write_text(
        """
server {
    listen 443 ssl;
    server_name localhost;

    ssl_certificate /etc/nginx/certs/localhost.crt;
    ssl_certificate_key /etc/nginx/certs/localhost.key;

    root /usr/share/nginx/html;
    location / {
        autoindex off;
    }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    client = docker.from_env()
    container = client.containers.run(
        "nginx:stable-alpine",
        ports={"443/tcp": None},
        volumes={
            str(content_dir): {"bind": "/usr/share/nginx/html", "mode": "ro"},
            str(cert_path): {"bind": "/etc/nginx/certs/localhost.crt", "mode": "ro"},
            str(key_path): {"bind": "/etc/nginx/certs/localhost.key", "mode": "ro"},
            str(nginx_conf): {"bind": "/etc/nginx/conf.d/default.conf", "mode": "ro"},
        },
        detach=True,
        remove=True,
        name=f"xconv2-nginx-https-test-{uuid.uuid4()}",
    )

    container.reload()
    network_ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
    https_mappings = network_ports.get("443/tcp") or []
    if not https_mappings:
        logs = container.logs().decode(errors="replace")
        container.stop()
        pytest.fail(f"nginx container did not expose HTTPS port mapping. Logs:\n{logs}")

    host_port = int(https_mappings[0]["HostPort"])
    base_url = f"https://localhost:{host_port}"

    import ssl
    import urllib.request

    context = ssl._create_unverified_context()
    try:
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"{base_url}/test1.nc", context=context, timeout=2) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(0.2)
        else:
            logs = container.logs().decode(errors="replace")
            pytest.fail(f"nginx HTTPS service did not start in time. Logs:\n{logs}")

        yield {
            "base_url": base_url,
            "test_file_url": f"{base_url}/test1.nc",
            "storage_options": {"ssl": False},
        }
    finally:
        try:
            container.stop()
        except Exception:
            pass