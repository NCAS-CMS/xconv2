"""Local integration-test servers. No container runtime or Python Docker SDK."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from functools import partial
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
from threading import Lock, Thread
import time
from typing import Generator
import uuid


S3_ACCESS_KEY = "xconv2-test"
S3_SECRET_KEY = "xconv2-test-secret"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@contextmanager
def running_server(server: ThreadingHTTPServer) -> Generator[ThreadingHTTPServer]:
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class S3TestService:
    def __init__(self, filesystem, endpoint_url: str) -> None:
        self.filesystem = filesystem
        self.endpoint_url = endpoint_url
        self._bytes_sent = 0
        self._lock = Lock()

    @property
    def bytes_sent(self) -> int:
        with self._lock:
            return self._bytes_sent

    def record_bytes(self, count: int) -> None:
        with self._lock:
            self._bytes_sent += count

    def upload_file(self, bucket: str, key: str, filename: str) -> None:
        self.filesystem.put_file(filename, f"{bucket}/{key}")

    def create_bucket(self) -> str:
        bucket = f"test-{uuid.uuid4()}"
        self.filesystem.mkdir(bucket)
        return bucket


def _proxy_handler(service: S3TestService, upstream_port: int):
    class CountingProxy(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            self._forward()

        def do_GET(self) -> None:
            self._forward()

        def _forward(self) -> None:
            with closing(HTTPConnection("127.0.0.1", upstream_port, timeout=30)) as upstream:
                # Preserve Host: it is part of the S3 request signature.
                upstream.request(self.command, self.path, headers=dict(self.headers))
                response = upstream.getresponse()
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in {"connection", "transfer-encoding"}:
                        self.send_header(name, value)
                self.end_headers()
                if self.command == "GET":
                    while chunk := response.read(64 * 1024):
                        service.record_bytes(len(chunk))
                        self.wfile.write(chunk)

        def log_message(self, format: str, *args) -> None:
            logging.getLogger(__name__).debug(format, *args)

    return CountingProxy


@contextmanager
def run_s3_service(workspace: Path) -> Generator[S3TestService]:
    import s3fs
    from botocore.exceptions import BotoCoreError, ClientError

    executable = shutil.which("weed")
    if executable is None:
        raise RuntimeError("Install the SeaweedFS 'weed' binary and add it to PATH.")

    config = workspace / "s3.json"
    config.write_text(
        json.dumps({"identities": [{
            "name": "tests",
            "credentials": [{"accessKey": S3_ACCESS_KEY, "secretKey": S3_SECRET_KEY}],
            "actions": ["Admin", "Read", "Write", "List", "Tagging"],
        }]}),
        encoding="utf-8",
    )
    port_names = [
        "master.port", "master.port.grpc",
        "volume.port", "volume.port.grpc",
        "filer.port", "filer.port.grpc", "s3.port", "s3.port.grpc",
    ]
    reservations = [socket.socket() for _ in port_names]
    try:
        for reservation in reservations:
            reservation.bind(("127.0.0.1", 0))
        ports = dict(zip(port_names, (s.getsockname()[1] for s in reservations)))
    finally:
        for reservation in reservations:
            reservation.close()

    command = [
        executable, "mini", f"-dir={workspace}", "-ip=127.0.0.1",
        "-ip.bind=127.0.0.1", "-admin.ui=false", "-webdav=false",
        "-s3.port.iceberg=0", "-s3.port.lance=0",
        "-master.telemetry=false", f"-s3.config={config}",
        *(f"-{name}={port}" for name, port in ports.items()),
    ]
    filesystem = s3fs.S3FileSystem(
        key=S3_ACCESS_KEY,
        secret=S3_SECRET_KEY,
        client_kwargs={"endpoint_url": f"http://127.0.0.1:{ports['s3.port']}"},
        config_kwargs={"s3": {"addressing_style": "path"}, "retries": {"max_attempts": 0}},
    )
    log_path = workspace / "seaweedfs.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=workspace, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            last_error = None
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    filesystem.call_s3("list_buckets")
                    break
                except (OSError, BotoCoreError, ClientError) as exc:
                    last_error = exc
                    time.sleep(0.2)
            else:
                raise RuntimeError(
                    f"SeaweedFS failed to start (exit code {process.poll()}): "
                    f"{last_error}\n{log_path.read_text()}"
                )

            service = S3TestService(filesystem, "")
            proxy = ThreadingHTTPServer(
                ("127.0.0.1", 0), _proxy_handler(service, ports["s3.port"]),
            )
            service.endpoint_url = f"http://127.0.0.1:{proxy.server_port}"
            with running_server(proxy):
                yield service
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


@contextmanager
def run_https_service(workspace: Path, *, index: bool = False) -> Generator[dict]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    content = workspace / "www"
    content.mkdir()
    shutil.copyfile(DATA_DIR / "test1.nc", content / "test1.nc")
    if index:
        (content / "index.html").write_text(
            "<!doctype html><html><body><h1>Dataset landing page</h1></body></html>",
            encoding="utf-8",
        )

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = workspace / "cert.pem", workspace / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)

    class FileHandler(SimpleHTTPRequestHandler):
        def send_head(self):
            path = Path(self.translate_path(self.path))
            requested_range = self.headers.get("Range")
            self._remaining = None
            if not requested_range or not path.is_file():
                return super().send_head()
            size = path.stat().st_size
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested_range)
            if match and any(match.groups()):
                start_text, end_text = match.groups()
                start = int(start_text) if start_text else max(0, size - int(end_text))
                end = min(int(end_text), size - 1) if start_text and end_text else size - 1
                if 0 <= start <= end < size:
                    source = path.open("rb")
                    source.seek(start)
                    self._remaining = end - start + 1
                    self.send_response(206)
                    self.send_header("Content-Type", self.guess_type(str(path)))
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(self._remaining))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()
                    return source
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        def copyfile(self, source, outputfile) -> None:
            if self._remaining is None:
                super().copyfile(source, outputfile)
                return
            while self._remaining > 0:
                chunk = source.read(min(64 * 1024, self._remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                self._remaining -= len(chunk)

        def list_directory(self, _path):
            self.send_error(403, "Directory listing disabled")

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(FileHandler, directory=str(content)))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    with running_server(server):
        base_url = f"https://localhost:{server.server_port}"
        yield {
            "base_url": base_url,
            "index_url": f"{base_url}/index.html",
            "test_file_url": f"{base_url}/test1.nc",
            "storage_options": {"ssl": False},
        }
