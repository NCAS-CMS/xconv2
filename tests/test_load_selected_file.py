from __future__ import annotations

import ast
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

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        _ = is_error
        self.status.showMessage(message)

    def _send_worker_task(self, code: str) -> None:
        self.sent_tasks.append(code)


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
    assert all(isinstance(item, str) for item in payload)

    parts = payload[0].split("\x1f", 2)
    assert len(parts) == 3
    assert parts[0].startswith("specific_humidity")
    assert "latitude" in parts[1]

    properties = ast.literal_eval(parts[2])
    assert isinstance(properties, dict)


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
    assert len(payload[0]) == 2
    assert payload[0][0].startswith("latitude")
    assert isinstance(payload[0][1], list)      
 