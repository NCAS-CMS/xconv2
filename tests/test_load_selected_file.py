from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

import pytest

from xconv2.cf_templates import coordinate_list
from xconv2.xconv_cf_interface import coordinate_info, field_info
from xconv2.gui import CFVMain


@dataclass
class _DummyStatus:
    messages: list[str] = field(default_factory=list)

    def showMessage(self, message: str) -> None:
        self.messages.append(message)


@dataclass
class _DummyWindow:
    status: _DummyStatus = field(default_factory=_DummyStatus)
    sent_tasks: list[str] = field(default_factory=list)
    sent_control_tasks: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    _remote_session_id: str | None = None
    _remote_descriptor_hash: str | None = None
    _remote_descriptor: dict[str, object] | None = None

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        _ = is_error
        self.status.showMessage(message)

    def _send_worker_task(self, code: str) -> None:
        self.sent_tasks.append(code)

    def _send_worker_control_task(self, kind: str, payload: dict[str, object]) -> None:
        self.sent_control_tasks.append((kind, payload))


class _FakeWorkerPipe:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    def write(self, payload: bytes) -> None:
        self.payloads.append(payload.decode())


def test_load_selected_file_builds_worker_task() -> None:
    """The GUI should send a worker task when a file is selected."""
    window = _DummyWindow()
    file_path = "/tmp/mock-data.nc"

    CFVMain._load_selected_file(window, file_path)

    assert window.status.messages[-1] == f"Loading file: {file_path}"
    assert len(window.sent_tasks) == 1

    code = window.sent_tasks[0]
    assert f"cf.read({file_path!r})" in code
    assert "fields = field_info(f)" in code
    assert "send_to_gui('METADATA', fields)" in code


def test_load_selected_file_task_executes_with_mock_cf_example_fields() -> None:
    """
    The generated worker code should emit field metadata when executed.
    """
    cf = pytest.importorskip("cf")

    window = _DummyWindow()
    file_path = "/tmp/mock-data.nc"
    CFVMain._load_selected_file(window, file_path)
    code = window.sent_tasks[0]

    messages: list[tuple[str, object]] = []

    class _FakeCF:
        @staticmethod
        def read(_path: str):
            return cf.example_fields(0, 1, 2, 3, 4, 5, 6, 7)

    namespace = {
        "cf": _FakeCF,
        "field_info": field_info,
        "send_to_gui": lambda prefix, payload: messages.append((prefix, payload)),
    }

    exec(code, namespace)

    prefix, payload = messages[-1]
    assert prefix == "METADATA"
    assert isinstance(payload, list)
    assert len(payload) == 8
    assert all(isinstance(item, dict) for item in payload)

    first = payload[0]
    assert str(first["identity"]).startswith("specific_humidity")
    assert "latitude" in str(first["detail"])
    assert isinstance(first["properties"], dict)


def test_coordinate_list_emits_coordinates_for_example_field() -> None:
    """Coordinate template should emit a non-empty coordinate payload for a sample field."""
    cf = pytest.importorskip("cf")

    messages: list[tuple[str, object]] = []
    namespace = {
        "cf": cf,
        "coordinate_info": coordinate_info,
        "send_to_gui": lambda prefix, payload: messages.append((prefix, payload)),
    }

    code = "f = cf.example_fields(0, 1, 2, 3, 4, 5, 6, 7)\n" + coordinate_list(0)
    exec(code, namespace)

    prefix, payload = messages[-1]
    assert prefix == "COORD"
    assert isinstance(payload, list)
    assert payload
    assert isinstance(payload[0], tuple)
    assert len(payload[0]) == 3
    assert payload[0][0].startswith("latitude")
    assert isinstance(payload[0][1], list)
    assert isinstance(payload[0][2], str)


def test_load_remote_selected_file_builds_control_task() -> None:
    window = _DummyWindow(
        _remote_session_id="session-1",
        _remote_descriptor_hash="hash-1",
        _remote_descriptor={"protocol": "sftp"},
    )

    CFVMain._load_remote_selected_file(window, "ssh://host/data/file.nc", "/data/file.nc")

    assert window.status.messages[-1] == "Loading remote file: ssh://host/data/file.nc"
    assert window.sent_control_tasks == [
        (
            "REMOTE_OPEN",
            {
                "session_id": "session-1",
                "descriptor_hash": "hash-1",
                "descriptor": {"protocol": "sftp"},
                "uri": "ssh://host/data/file.nc",
                "path": "/data/file.nc",
            },
        )
    ]


def test_send_worker_control_task_writes_typed_headers() -> None:
    fake_worker = _FakeWorkerPipe()
    window = _DummyWindow()
    window.worker = fake_worker

    CFVMain._send_worker_control_task(window, "REMOTE_PREPARE", {"session_id": "abc", "value": 2})

    assert len(fake_worker.payloads) == 1
    payload = fake_worker.payloads[0]
    assert payload.startswith("#TASK_KIND:REMOTE_PREPARE\n#TASK_PAYLOAD_B64:")
    assert payload.endswith("#END_TASK\n")
    assert hasattr(window, "_pending_worker_task_starts")
    assert len(window._pending_worker_task_starts) == 1

    encoded = payload.split("#TASK_PAYLOAD_B64:", 1)[1].split("\n", 1)[0]
    decoded = json.loads(base64.b64decode(encoded.encode("ascii")).decode("utf-8"))
    assert decoded == {"session_id": "abc", "value": 2}
 