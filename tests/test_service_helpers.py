from __future__ import annotations

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import find_spec
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from service_helpers import (
    DATA_DIR, S3TestService, _proxy_handler, run_https_service, run_s3_service, running_server,
)


def test_s3_proxy_counts_response_bytes_and_preserves_signed_headers() -> None:
    requests = []

    class Upstream(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", "8")
            self.end_headers()

        def do_GET(self):
            requests.append((
                self.path, self.headers["Host"], self.headers["Authorization"], self.headers["Range"],
            ))
            self.send_response(206)
            self.send_header("Content-Length", "4")
            self.send_header("Content-Range", "bytes 2-5/8")
            self.end_headers()
            self.wfile.write(b"2345")

    service = S3TestService(None, "")
    with running_server(ThreadingHTTPServer(("127.0.0.1", 0), Upstream)) as upstream:
        proxy = ThreadingHTTPServer(
            ("127.0.0.1", 0), _proxy_handler(service, upstream.server_port),
        )
        with running_server(proxy):
            connection = HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
            try:
                connection.request("HEAD", "/bucket/key")
                assert connection.getresponse().read() == b""
                assert service.bytes_sent == 0
                for _ in range(2):
                    connection.request("GET", "/bucket/key?versionId=example", headers={
                        "Host": "signed-host", "Authorization": "signature", "Range": "bytes=2-5",
                    })
                    response = connection.getresponse()
                    assert response.status == 206
                    assert response.getheader("Content-Range") == "bytes 2-5/8"
                    assert response.read() == b"2345"
                assert service.bytes_sent == 8
                assert requests == [
                    ("/bucket/key?versionId=example", "signed-host", "signature", "bytes=2-5"),
                ] * 2
            finally:
                connection.close()


def test_s3_service_reports_missing_binary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="Install the SeaweedFS 'weed' binary"):
        with run_s3_service(tmp_path):
            pytest.fail("Missing SeaweedFS must not yield a working service")


def test_s3_service_reports_startup_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: sys.executable)
    with pytest.raises(RuntimeError, match="SeaweedFS failed to start") as error:
        with run_s3_service(tmp_path):
            pytest.fail("Broken SeaweedFS must fail rather than skip")
    assert "can't open file" in str(error.value)


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("weed") is None,
    reason="S3 integration requires the native SeaweedFS 'weed' binary on PATH; no Docker is used.",
)
def test_native_s3_service_stops_process_after_test_failure(tmp_path: Path, monkeypatch) -> None:
    processes = []
    original_popen = subprocess.Popen

    def capture_process(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", capture_process)
    with pytest.raises(ValueError, match="simulated test failure"):
        with run_s3_service(tmp_path) as service:
            assert processes[0].poll() is None
            result = service.filesystem.call_s3("list_buckets")
            assert result["ResponseMetadata"]["HTTPStatusCode"] == 200
            raise ValueError("simulated test failure")
    assert processes[0].poll() is not None


@pytest.mark.skipif(
    find_spec("cryptography") is None,
    reason="Local HTTPS tests require cryptography; install xconv2[test].",
)
@pytest.mark.parametrize("index", [False, True], ids=["no-index", "index-page"])
def test_https_service_range_reads_and_directory_behavior(tmp_path: Path, index: bool) -> None:
    context = ssl._create_unverified_context()
    expected = (DATA_DIR / "test1.nc").read_bytes()
    with run_https_service(tmp_path, index=index) as service:
        url = service["test_file_url"]
        with urlopen(url, context=context, timeout=5) as response:
            assert response.read() == expected
        for byte_range, body in [
            ("bytes=10-29", expected[10:30]),
            ("bytes=-20", expected[-20:]),
            (f"bytes={len(expected)-20}-", expected[-20:]),
        ]:
            request = Request(url, headers={"Range": byte_range})
            with urlopen(request, context=context, timeout=5) as response:
                assert response.status == 206
                assert response.read() == body
                assert int(response.headers["Content-Length"]) == len(body)
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url, headers={"Range": "bytes=999999999-"}), context=context, timeout=5)
        assert error.value.code == 416
        if index:
            with urlopen(service["base_url"], context=context, timeout=5) as response:
                assert b"Dataset landing page" in response.read()
        else:
            with pytest.raises(HTTPError) as error:
                urlopen(service["base_url"], context=context, timeout=5)
            assert error.value.code == 403
