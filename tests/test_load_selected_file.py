from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
import types

import pytest
from PySide6.QtCore import Qt

from xconv2.cf_templates import coordinate_list
from xconv2.cf_interface import coordinate_info, field_info
from xconv2.gui import CFVMain
import xconv2.main_window as main_window
import xconv2.remote_access as _remote_access_mod
import xconv2.ui.remote_file_navigator as _remote_file_nav_mod


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

    def _clear_loaded_data_views(self) -> None:
        pass

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


class _DummyFieldItem:
    def __init__(self, source: str | None = None) -> None:
        self._source = source

    def data(self, _role: int) -> str | None:
        return self._source


class _DummyFieldListWidgetForOps:
    def __init__(self, item: _DummyFieldItem | None = None) -> None:
        self._item = item

    def currentItem(self) -> _DummyFieldItem | None:
        return self._item

    def row(self, _item: _DummyFieldItem) -> int:
        return 0

    def item(self, index: int) -> _DummyFieldItem | None:
        return self._item if index == 0 else None


class _DummyMemoryLabel:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:
        self.text = text


class _DummyWorkerForMemory:
    def __init__(self, pid: int) -> None:
        self._pid = pid

    def processId(self) -> int:
        return self._pid


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


def test_coordinate_list_builds_non_mutating_squeeze_task() -> None:
    code = coordinate_list(3)

    assert "fld = f[3]" in code
    assert "fld = fld.squeeze()" in code
    assert "inplace=True" not in code


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


def test_load_selected_files_builds_worker_task() -> None:
    """The GUI should send one worker task when multiple files are selected."""
    window = _DummyWindow()
    file_paths = ["/tmp/mock-a.nc", "/tmp/mock-b.nc"]

    CFVMain._load_selected_files(window, file_paths)

    assert window.status.messages[-1] == "Loading 2 files"
    assert len(window.sent_tasks) == 1

    code = window.sent_tasks[0]
    assert "cf.read(['/tmp/mock-a.nc', '/tmp/mock-b.nc'])" in code
    assert "fields = field_info(f)" in code
    assert "send_to_gui('METADATA', fields)" in code


def test_on_file_selected_single_mode_replaces_loaded_paths() -> None:
    class _DummyLoadHost:
        def __init__(self) -> None:
            self.file_open_mode = "single"
            self._loaded_file_paths: list[str] = ["/tmp/old.nc"]
            self.base_window_title = "xconv2 (test)"
            self.single_calls: list[tuple[str, dict[str, object]]] = []
            self.multi_calls: list[list[str]] = []
            self.titles: list[str] = []

        def _load_selected_file(self, path: str, **kwargs: object) -> None:
            self.single_calls.append((path, dict(kwargs)))

        def _load_selected_files(self, paths: list[str]) -> None:
            self.multi_calls.append(paths)

        def setWindowTitle(self, title: str) -> None:
            self.titles.append(title)

    host = _DummyLoadHost()
    CFVMain.on_file_selected(host, "/tmp/new.nc")

    assert host._loaded_file_paths == ["/tmp/new.nc"]
    assert host.single_calls == [("/tmp/new.nc", {})]
    assert host.multi_calls == []
    assert host.titles == []


def test_on_file_selected_multi_mode_accumulates_paths() -> None:
    class _DummyLoadHost:
        def __init__(self) -> None:
            self.file_open_mode = "multi"
            self._loaded_file_paths: list[str] = ["/tmp/old.nc"]
            self.base_window_title = "xconv2 (test)"
            self.single_calls: list[tuple[str, dict[str, object]]] = []
            self.multi_calls: list[list[str]] = []
            self.titles: list[str] = []

        def _load_selected_file(self, path: str, **kwargs: object) -> None:
            self.single_calls.append((path, dict(kwargs)))

        def _load_selected_files(self, paths: list[str]) -> None:
            self.multi_calls.append(paths)

        def setWindowTitle(self, title: str) -> None:
            self.titles.append(title)

    host = _DummyLoadHost()
    CFVMain.on_file_selected(host, "/tmp/new.nc")

    assert host._loaded_file_paths == ["/tmp/old.nc", "/tmp/new.nc"]
    assert host.single_calls == []
    assert host.multi_calls == [["/tmp/old.nc", "/tmp/new.nc"]]
    assert host.titles[-1] == "xconv2 (test): 2 files"


def test_on_file_selected_multi_mode_first_file_clears_placeholder() -> None:
    class _DummyLoadHost:
        def __init__(self) -> None:
            self.file_open_mode = "multi"
            self._loaded_file_paths: list[str] = []
            self.base_window_title = "xconv2 (test)"
            self.single_calls: list[tuple[str, dict[str, object]]] = []
            self.multi_calls: list[list[str]] = []
            self.titles: list[str] = []

        def _load_selected_file(self, path: str, **kwargs: object) -> None:
            self.single_calls.append((path, dict(kwargs)))

        def _load_selected_files(self, paths: list[str]) -> None:
            self.multi_calls.append(paths)

        def setWindowTitle(self, title: str) -> None:
            self.titles.append(title)

    host = _DummyLoadHost()
    CFVMain.on_file_selected(host, "/tmp/first.nc")

    assert host._loaded_file_paths == ["/tmp/first.nc"]
    assert host.single_calls == []
    assert host.multi_calls == [["/tmp/first.nc"]]
    assert host.titles[-1] == "xconv2 (test): 1 files"


def test_load_selected_file_append_builds_worker_append_task() -> None:
    """Append mode should extend worker field state and emit only new metadata rows."""
    window = _DummyWindow()
    file_path = "/tmp/mock-data.nc"

    CFVMain._load_selected_file(window, file_path, clear_existing=False, append_metadata=True)

    assert len(window.sent_tasks) == 1
    code = window.sent_tasks[0]
    assert f"_cfview_new_fields = cf.read({file_path!r})" in code
    assert "f.extend(_cfview_new_fields)" in code
    assert "fields = field_info(_cfview_new_fields)" in code
    assert "send_to_gui('METADATA', fields)" in code


def test_load_selected_file_does_not_release_remote_session() -> None:
    class _DummyWindowNoRelease(_DummyWindow):
        def __init__(self) -> None:
            super().__init__()
            self.released = 0
            self._remote_session_id = "session-1"
            self._remote_descriptor_hash = "hash-1"
            self._remote_descriptor = {"protocol": "sftp"}

        def _release_remote_session_if_active(self) -> None:
            self.released += 1

    window = _DummyWindowNoRelease()
    file_path = "/tmp/local-data.nc"

    CFVMain._load_selected_file(window, file_path)

    assert window.released == 0
    assert len(window.sent_tasks) == 1


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
                "append": False,
            },
        )
    ]


def test_load_remote_selected_file_multi_mode_appends_without_clearing() -> None:
    class _DummyRemoteAppendWindow(_DummyWindow):
        def __init__(self) -> None:
            super().__init__(
                _remote_session_id="session-1",
                _remote_descriptor_hash="hash-1",
                _remote_descriptor={"protocol": "sftp"},
            )
            self.file_open_mode = "multi"
            self._loaded_file_paths = ["ssh://host/data/first.nc"]
            self.clear_calls = 0

        def _clear_loaded_data_views(self) -> None:
            self.clear_calls += 1

    window = _DummyRemoteAppendWindow()

    CFVMain._load_remote_selected_file(window, "ssh://host/data/second.nc", "/data/second.nc")

    assert window.clear_calls == 0
    assert window._pending_metadata_append is True
    assert window._pending_metadata_source == "ssh://host/data/second.nc"
    assert window.sent_control_tasks == [
        (
            "REMOTE_OPEN",
            {
                "session_id": "session-1",
                "descriptor_hash": "hash-1",
                "descriptor": {"protocol": "sftp"},
                "uri": "ssh://host/data/second.nc",
                "path": "/data/second.nc",
                "append": True,
            },
        )
    ]


def test_maybe_show_binary_validation_dialog_warns_user(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyOpsWindow:
        def __init__(self) -> None:
            self._pending_binary_operation_name = "Difference (A-B)"
            self.status_messages: list[tuple[str, bool]] = []

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

    seen: list[tuple[object, str, str]] = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda parent, title, message: seen.append((parent, title, message)))

    host = _DummyOpsWindow()
    CFVMain._maybe_show_binary_validation_dialog(host, "ValueError: Two fields need the same coordinates")

    assert seen == [(host, "Difference (A-B)", "Two fields need the same coordinates")]
    assert host.status_messages[-1] == ("Two fields need the same coordinates", True)
    assert host._pending_binary_operation_name is None


def test_maybe_show_binary_validation_dialog_accepts_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyOpsWindow:
        def __init__(self) -> None:
            self._pending_binary_operation_name = "Difference (A-B)"
            self.status_messages: list[tuple[str, bool]] = []

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

    seen: list[tuple[object, str, str]] = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda parent, title, message: seen.append((parent, title, message)))

    host = _DummyOpsWindow()
    CFVMain._maybe_show_binary_validation_dialog(host, "Error - something else")

    assert seen == [(host, "Difference (A-B)", "something else")]
    assert host.status_messages[-1] == ("something else", True)
    assert host._pending_binary_operation_name is None


def test_maybe_show_binary_validation_dialog_accepts_status_error_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyOpsWindow:
        def __init__(self) -> None:
            self._pending_binary_operation_name = "Difference (A-B)"
            self.status_messages: list[tuple[str, bool]] = []

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

    seen: list[tuple[object, str, str]] = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda parent, title, message: seen.append((parent, title, message)))

    host = _DummyOpsWindow()
    CFVMain._maybe_show_binary_validation_dialog(host, "Error - ValueError: Two fields need the same coordinates")

    assert seen == [(host, "Difference (A-B)", "Two fields need the same coordinates")]
    assert host.status_messages[-1] == ("Two fields need the same coordinates", True)
    assert host._pending_binary_operation_name is None


def test_maybe_show_binary_validation_dialog_accepts_detailed_coordinate_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummyOpsWindow:
        def __init__(self) -> None:
            self._pending_binary_operation_name = "Difference (A-B)"
            self.status_messages: list[tuple[str, bool]] = []

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

    seen: list[tuple[object, str, str]] = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda parent, title, message: seen.append((parent, title, message)))

    host = _DummyOpsWindow()
    detail = "Two fields need the same coordinates: latitude values differ"
    CFVMain._maybe_show_binary_validation_dialog(host, f"Error - ValueError: {detail}")

    assert seen == [(host, "Difference (A-B)", detail)]
    assert host.status_messages[-1] == (detail, True)
    assert host._pending_binary_operation_name is None


def test_maybe_show_binary_validation_dialog_accepts_cf_time_combine_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummyOpsWindow:
        def __init__(self) -> None:
            self._pending_binary_operation_name = "Difference (A-B)"
            self.status_messages: list[tuple[str, bool]] = []

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

    seen: list[tuple[object, str, str]] = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda parent, title, message: seen.append((parent, title, message)))

    host = _DummyOpsWindow()
    detail = "Can't combine size 12 'time' axes with non-matching coordinate values"
    CFVMain._maybe_show_binary_validation_dialog(host, f"Error - ValueError: {detail}")

    assert seen == [(host, "Difference (A-B)", detail)]
    assert host.status_messages[-1] == (detail, True)
    assert host._pending_binary_operation_name is None


def test_maybe_show_binary_validation_dialog_strips_valueerror_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummyOpsWindow:
        def __init__(self) -> None:
            self._pending_binary_operation_name = "Difference (A-B)"
            self.status_messages: list[tuple[str, bool]] = []

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

    seen: list[tuple[object, str, str]] = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda parent, title, message: seen.append((parent, title, message)))

    host = _DummyOpsWindow()
    detail = "Can't set Units to <Units: days since 1850-01-01 00:00:00 standard> that are not equivalent"
    CFVMain._maybe_show_binary_validation_dialog(host, f"Error - ValueError: {detail}")

    assert seen == [(host, "Difference (A-B)", detail)]
    assert host.status_messages[-1] == (detail, True)
    assert host._pending_binary_operation_name is None


def test_load_remote_selected_file_multi_mode_skips_duplicate_uri() -> None:
    class _DummyRemoteAppendWindow(_DummyWindow):
        def __init__(self) -> None:
            super().__init__(
                _remote_session_id="session-1",
                _remote_descriptor_hash="hash-1",
                _remote_descriptor={"protocol": "sftp"},
            )
            self.file_open_mode = "multi"
            self._loaded_file_paths = ["ssh://host/data/existing.nc"]
            self.clear_calls = 0

        def _clear_loaded_data_views(self) -> None:
            self.clear_calls += 1

    window = _DummyRemoteAppendWindow()

    CFVMain._load_remote_selected_file(window, "ssh://host/data/existing.nc", "/data/existing.nc")

    assert window.clear_calls == 0
    assert window.sent_control_tasks == []
    assert window.status.messages[-1] == "Remote file already loaded: ssh://host/data/existing.nc"


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


def test_field_ops_grad_builds_worker_task_for_selected_field() -> None:
    class _DummyOpsWindow:
        def __init__(self) -> None:
            self._selected_field_indices = [0]
            self._pending_field_op_source = None
            self.field_list_widget = _DummyFieldListWidgetForOps(_DummyFieldItem("/tmp/a.nc"))
            self.sent_tasks: list[tuple[str, bool]] = []
            self.status_messages: list[str] = []
            self.replay_operations: list[dict[str, object]] = []

        def _send_worker_task(self, code: str, save_code_path: str | None = None, emit_image: bool = True) -> None:
            _ = save_code_path
            self.sent_tasks.append((code, emit_image))

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            _ = is_error
            self.status_messages.append(message)

        def _selected_field_index_for_operation(self, operation: str) -> int | None:
            return CFVMain._selected_field_index_for_operation(self, operation)

        def _run_unary_xy_field_operation(self, operation_name: str, method_name: str) -> None:
            return CFVMain._run_unary_xy_field_operation(self, operation_name, method_name)

        def _record_replayable_operation(self, operation: dict[str, object]) -> None:
            self.replay_operations.append(operation)

    window = _DummyOpsWindow()

    CFVMain._field_ops_maths_grad(window)

    assert window._pending_field_op_source == "/tmp/a.nc"
    assert len(window.sent_tasks) == 1
    code, emit_image = window.sent_tasks[0]
    assert emit_image is False
    assert "append_unary_xy_field_operation" in code
    assert "_cfview_operation = 'grad'" in code
    assert "send_to_gui('METADATA_APPEND'" in code
    assert window.replay_operations == [
        {
            "kind": "unary_xy",
            "field_index": 0,
            "field_ref": None,
            "selected_indices": [0],
            "operation": "grad",
            "source_file": "/tmp/a.nc",
        }
    ]


def test_field_ops_grad_requires_exactly_one_selected_field() -> None:
    class _DummyOpsWindow:
        def __init__(self) -> None:
            self._selected_field_indices: list[int] = []
            self._pending_field_op_source = None
            self.field_list_widget = _DummyFieldListWidgetForOps(None)
            self.sent_tasks: list[tuple[str, bool]] = []
            self.status_messages: list[tuple[str, bool]] = []

        def _send_worker_task(self, code: str, save_code_path: str | None = None, emit_image: bool = True) -> None:
            _ = save_code_path
            self.sent_tasks.append((code, emit_image))

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

        def _selected_field_index_for_operation(self, operation: str) -> int | None:
            return CFVMain._selected_field_index_for_operation(self, operation)

        def _run_unary_xy_field_operation(self, operation_name: str, method_name: str) -> None:
            return CFVMain._run_unary_xy_field_operation(self, operation_name, method_name)

    window = _DummyOpsWindow()

    CFVMain._field_ops_maths_grad(window)

    assert window.sent_tasks == []
    assert window.status_messages[-1] == ("Grad requires exactly one selected field.", True)


def test_field_ops_add_bounds_builds_worker_task_for_selected_field() -> None:
    class _DummyOpsWindow:
        def __init__(self) -> None:
            self._selected_field_indices = [0]
            self._pending_field_op_source = None
            self.field_list_widget = _DummyFieldListWidgetForOps(_DummyFieldItem("/tmp/a.nc"))
            self.sent_tasks: list[tuple[str, bool]] = []
            self.status_messages: list[str] = []

        def _send_worker_task(self, code: str, save_code_path: str | None = None, emit_image: bool = True) -> None:
            _ = save_code_path
            self.sent_tasks.append((code, emit_image))

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            _ = is_error
            self.status_messages.append(message)

        def _selected_field_index_for_operation(self, operation: str) -> int | None:
            return CFVMain._selected_field_index_for_operation(self, operation)

        def _run_add_bounds_operation(self, operation_name: str) -> None:
            return CFVMain._run_add_bounds_operation(self, operation_name)

    window = _DummyOpsWindow()

    CFVMain._field_ops_add_bounds(window)

    assert window._pending_field_op_source == "/tmp/a.nc"
    assert len(window.sent_tasks) == 1
    code, emit_image = window.sent_tasks[0]
    assert emit_image is False
    assert "add_dimension_coordinate_bounds(f, _cfview_field_index)" in code
    assert "send_to_gui('METADATA', metadata_rows)" in code


def test_field_ops_apply_selection_builds_worker_task() -> None:
    class _DummyOpsWindow:
        def __init__(self) -> None:
            self._selected_field_indices = [0]
            self._pending_field_op_source = None
            self._pending_binary_operation_name = None
            self.field_list_widget = _DummyFieldListWidgetForOps(_DummyFieldItem("/tmp/a.nc"))
            self.sent_tasks: list[tuple[str, bool]] = []
            self.status_messages: list[str] = []
            self.replay_operations: list[dict[str, object]] = []

        def _send_worker_task(self, code: str, save_code_path: str | None = None, emit_image: bool = True) -> None:
            _ = save_code_path
            self.sent_tasks.append((code, emit_image))

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            _ = is_error
            self.status_messages.append(message)

        def _selected_field_index_for_operation(self, operation: str) -> int | None:
            return CFVMain._selected_field_index_for_operation(self, operation)

        def _build_plot_context(self):
            return ({"latitude": (-10, 10)}, {"time": "mean"}, "contour")

        def _record_replayable_operation(self, operation: dict[str, object]) -> None:
            self.replay_operations.append(operation)

    window = _DummyOpsWindow()

    CFVMain._field_ops_apply_selection(window)

    assert window._pending_field_op_source == "/tmp/a.nc"
    assert len(window.sent_tasks) == 1
    code, emit_image = window.sent_tasks[0]
    assert emit_image is False
    assert "append_selection_field_operation" in code
    assert "_cfview_selection_spec" in code
    assert "_cfview_collapse_by_coord" in code
    assert window.replay_operations == [
        {
            "kind": "apply_selection",
            "field_index": 0,
            "field_ref": None,
            "selected_indices": [0],
            "selections": {"latitude": (-10, 10)},
            "collapse_by_coord": {"time": "mean"},
            "source_file": "/tmp/a.nc",
        }
    ]


def test_record_replayable_operation_persists_last_operations(tmp_path: Path) -> None:
    class _DummyReplayStore:
        def __init__(self) -> None:
            self.path = tmp_path / "last_operations.json"

        def _last_operations_path(self) -> Path:
            return self.path

        def _load_last_operations_payload(self) -> dict[str, object]:
            return CFVMain._load_last_operations_payload(self)

    host = _DummyReplayStore()

    CFVMain._record_replayable_operation(
        host,
        {"kind": "unary_xy", "field_index": 2, "operation": "grad"},
    )
    CFVMain._record_replayable_operation(
        host,
        {"kind": "binary", "index_a": 1, "index_b": 3, "operation": "difference_ab"},
    )

    payload = json.loads(host.path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["session_id"] == ""
    assert isinstance(payload["saved_at"], str)
    assert payload["operations"] == [
        {"kind": "unary_xy", "field_index": 2, "operation": "grad"},
        {"kind": "binary", "index_a": 1, "index_b": 3, "operation": "difference_ab"},
    ]


def test_replay_last_operations_builds_single_worker_task(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SelectAllDialog:
        def __init__(self, _parent, *, operation_labels: list[str]) -> None:
            self._labels = operation_labels

        def exec(self) -> int:
            return 1

        def selected_indices(self) -> list[int]:
            return list(range(len(self._labels)))

    monkeypatch.setattr(main_window, "ReplayOperationsDialog", _SelectAllDialog)

    class _DummyReplayWindow:
        def __init__(self) -> None:
            self.sent_control_tasks: list[tuple[str, dict[str, object]]] = []
            self.status_messages: list[tuple[str, bool]] = []
            self._pending_binary_operation_name: str | None = None
            self._settings: dict[str, object] = {}

        def _load_last_operations_payload(self) -> dict[str, object]:
            return {
                "schema_version": 1,
                "saved_at": "2026-06-08T00:00:00Z",
                "operations": [
                    {"kind": "unary_xy", "field_index": 0, "operation": "grad"},
                    {
                        "kind": "binary",
                        "index_a": 0,
                        "index_b": 1,
                        "operation": "difference_ab",
                    },
                    {
                        "kind": "apply_selection",
                        "field_index": 1,
                        "selections": {"latitude": [-10, 10]},
                        "collapse_by_coord": {"time": "mean"},
                    },
                    {
                        "kind": "regrid",
                        "config": {
                            "field_indices": [0],
                            "target": "regular_lon_lat",
                            "method": "linear",
                        },
                    },
                ],
            }

        def _describe_replay_operation(self, operation: dict[str, object]) -> str:
            return CFVMain._describe_replay_operation(self, operation)

        def _source_files_for_replay_operation(self, operation: dict[str, object]) -> list[str]:
            return CFVMain._source_files_for_replay_operation(self, operation)

        def _is_remote_source_uri(self, uri: str) -> bool:
            return CFVMain._is_remote_source_uri(uri)

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

        def _send_worker_control_task(self, kind: str, payload: dict[str, object]) -> None:
            self.sent_control_tasks.append((kind, payload))

    window = _DummyReplayWindow()

    CFVMain._field_ops_replay_last_operations(window)

    assert len(window.sent_control_tasks) == 1
    kind, payload = window.sent_control_tasks[0]
    assert kind == "REPLAY_FIELDS"
    operations = payload.get("operations")
    assert isinstance(operations, list)
    assert len(operations) == 4
    assert window.status_messages[-1] == ("Replaying 4 field operation(s)...", False)


def test_replay_last_operations_respects_unchecked_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SelectSubsetDialog:
        def __init__(self, _parent, *, operation_labels: list[str]) -> None:
            assert len(operation_labels) == 3

        def exec(self) -> int:
            return 1

        def selected_indices(self) -> list[int]:
            return [0, 2]

    monkeypatch.setattr(main_window, "ReplayOperationsDialog", _SelectSubsetDialog)

    class _DummyReplayWindow:
        def __init__(self) -> None:
            self.sent_control_tasks: list[tuple[str, dict[str, object]]] = []
            self.status_messages: list[tuple[str, bool]] = []
            self._pending_binary_operation_name: str | None = None

        def _load_last_operations_payload(self) -> dict[str, object]:
            return {
                "schema_version": 1,
                "saved_at": "2026-06-08T00:00:00Z",
                "operations": [
                    {"kind": "unary_xy", "field_index": 0, "operation": "grad"},
                    {
                        "kind": "binary",
                        "index_a": 0,
                        "index_b": 1,
                        "operation": "difference_ab",
                    },
                    {
                        "kind": "apply_selection",
                        "field_index": 1,
                        "selections": {"latitude": [-10, 10]},
                        "collapse_by_coord": {"time": "mean"},
                    },
                ],
            }

        def _describe_replay_operation(self, operation: dict[str, object]) -> str:
            return CFVMain._describe_replay_operation(self, operation)

        def _source_files_for_replay_operation(self, operation: dict[str, object]) -> list[str]:
            return CFVMain._source_files_for_replay_operation(self, operation)

        def _is_remote_source_uri(self, uri: str) -> bool:
            return CFVMain._is_remote_source_uri(uri)

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

        def _send_worker_control_task(self, kind: str, payload: dict[str, object]) -> None:
            self.sent_control_tasks.append((kind, payload))

    window = _DummyReplayWindow()

    CFVMain._field_ops_replay_last_operations(window)

    assert len(window.sent_control_tasks) == 1
    kind, payload = window.sent_control_tasks[0]
    assert kind == "REPLAY_FIELDS"
    operations = payload.get("operations")
    assert isinstance(operations, list)
    assert [str(op.get("kind")) for op in operations if isinstance(op, dict)] == ["unary_xy", "apply_selection"]
    assert window.status_messages[-1] == ("Replaying 2 field operation(s) (skipped 1)...", False)


def test_replay_last_operations_preloads_required_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SelectAllDialog:
        def __init__(self, _parent, *, operation_labels: list[str]) -> None:
            self._labels = operation_labels

        def exec(self) -> int:
            return 1

        def selected_indices(self) -> list[int]:
            return list(range(len(self._labels)))

    monkeypatch.setattr(main_window, "ReplayOperationsDialog", _SelectAllDialog)

    class _DummyReplayWindow:
        def __init__(self) -> None:
            self.sent_control_tasks: list[tuple[str, dict[str, object]]] = []
            self.status_messages: list[tuple[str, bool]] = []
            self._pending_binary_operation_name: str | None = None

        def _load_last_operations_payload(self) -> dict[str, object]:
            return {
                "schema_version": 1,
                "saved_at": "2026-06-08T00:00:00Z",
                "operations": [
                    {
                        "kind": "unary_xy",
                        "field_index": 0,
                        "operation": "grad",
                        "source_file": "/tmp/a.nc",
                    },
                    {
                        "kind": "binary",
                        "index_a": 0,
                        "index_b": 1,
                        "operation": "difference_ab",
                        "source_files": ["/tmp/a.nc", "/tmp/b.nc"],
                    },
                ],
            }

        def _describe_replay_operation(self, operation: dict[str, object]) -> str:
            return CFVMain._describe_replay_operation(self, operation)

        def _source_files_for_replay_operation(self, operation: dict[str, object]) -> list[str]:
            return CFVMain._source_files_for_replay_operation(self, operation)

        def _is_remote_source_uri(self, uri: str) -> bool:
            return CFVMain._is_remote_source_uri(uri)

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

        def _send_worker_control_task(self, kind: str, payload: dict[str, object]) -> None:
            self.sent_control_tasks.append((kind, payload))

        def _resolve_remote_uri(self, _uri: str):
            return None, "", "", True

    window = _DummyReplayWindow()

    CFVMain._field_ops_replay_last_operations(window)

    assert len(window.sent_control_tasks) == 1
    kind, payload = window.sent_control_tasks[0]
    assert kind == "REPLAY_FIELDS"
    assert payload.get("remote_open_requests") == []
    operations = payload.get("operations")
    assert isinstance(operations, list)
    assert len(operations) == 2


def test_replay_last_operations_uses_single_path_read_for_single_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SelectAllDialog:
        def __init__(self, _parent, *, operation_labels: list[str]) -> None:
            self._labels = operation_labels

        def exec(self) -> int:
            return 1

        def selected_indices(self) -> list[int]:
            return list(range(len(self._labels)))

    monkeypatch.setattr(main_window, "ReplayOperationsDialog", _SelectAllDialog)

    class _DummyReplayWindow:
        def __init__(self) -> None:
            self.sent_control_tasks: list[tuple[str, dict[str, object]]] = []
            self.status_messages: list[tuple[str, bool]] = []
            self._pending_binary_operation_name: str | None = None

        def _load_last_operations_payload(self) -> dict[str, object]:
            return {
                "schema_version": 1,
                "saved_at": "2026-06-08T00:00:00Z",
                "operations": [
                    {
                        "kind": "unary_xy",
                        "field_index": 0,
                        "operation": "grad",
                        "source_file": "/tmp/a.nc",
                    }
                ],
            }

        def _describe_replay_operation(self, operation: dict[str, object]) -> str:
            return CFVMain._describe_replay_operation(self, operation)

        def _source_files_for_replay_operation(self, operation: dict[str, object]) -> list[str]:
            return CFVMain._source_files_for_replay_operation(self, operation)

        def _is_remote_source_uri(self, uri: str) -> bool:
            return CFVMain._is_remote_source_uri(uri)

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

        def _send_worker_control_task(self, kind: str, payload: dict[str, object]) -> None:
            self.sent_control_tasks.append((kind, payload))

        def _resolve_remote_uri(self, _uri: str):
            return None, "", "", True

    window = _DummyReplayWindow()

    CFVMain._field_ops_replay_last_operations(window)

    assert len(window.sent_control_tasks) == 1
    kind, payload = window.sent_control_tasks[0]
    assert kind == "REPLAY_FIELDS"
    assert payload.get("remote_open_requests") == []


def test_replay_last_operations_uses_remote_preload_helper_for_remote_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SelectAllDialog:
        def __init__(self, _parent, *, operation_labels: list[str]) -> None:
            self._labels = operation_labels

        def exec(self) -> int:
            return 1

        def selected_indices(self) -> list[int]:
            return list(range(len(self._labels)))

    monkeypatch.setattr(main_window, "ReplayOperationsDialog", _SelectAllDialog)

    class _DummyReplayWindow:
        def __init__(self) -> None:
            self.sent_control_tasks: list[tuple[str, dict[str, object]]] = []
            self.status_messages: list[tuple[str, bool]] = []
            self._pending_binary_operation_name: str | None = None
            self._settings: dict[str, object] = {}

        def _load_last_operations_payload(self) -> dict[str, object]:
            return {
                "schema_version": 1,
                "saved_at": "2026-06-08T00:00:00Z",
                "operations": [
                    {
                        "kind": "unary_xy",
                        "field_index": 0,
                        "operation": "grad",
                        "source_file": "s3://bnl/CMIP6-test.nc",
                    }
                ],
            }

        def _describe_replay_operation(self, operation: dict[str, object]) -> str:
            return CFVMain._describe_replay_operation(self, operation)

        def _source_files_for_replay_operation(self, operation: dict[str, object]) -> list[str]:
            return CFVMain._source_files_for_replay_operation(self, operation)

        def _is_remote_source_uri(self, uri: str) -> bool:
            return CFVMain._is_remote_source_uri(uri)

        def _resolve_remote_uri(self, uri: str):
            assert uri == "s3://bnl/CMIP6-test.nc"
            return {"protocol": "S3", "remote": {"alias": "S3", "details": {}}}, "bnl/CMIP6-test.nc", "S3", False

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

        def _send_worker_control_task(self, kind: str, payload: dict[str, object]) -> None:
            self.sent_control_tasks.append((kind, payload))

    import xconv2.remote_access as _remote_access_mod

    monkeypatch.setattr(
        _remote_access_mod,
        "build_remote_filesystem_spec",
        lambda _config: type("_Spec", (), {"display_name": "S3"})(),
    )

    window = _DummyReplayWindow()

    monkeypatch.setattr(_remote_access_mod, "remote_descriptor_hash", lambda _descriptor: "hash-1")
    monkeypatch.setattr(
        _remote_access_mod,
        "spec_to_descriptor",
        lambda _spec, cache=None: {"protocol": "s3", "cache": cache},
    )

    CFVMain._field_ops_replay_last_operations(window)

    assert len(window.sent_control_tasks) == 1
    kind, payload = window.sent_control_tasks[0]
    assert kind == "REPLAY_FIELDS"
    requests = payload.get("remote_open_requests")
    assert isinstance(requests, list)
    assert len(requests) == 1


def test_record_replayable_operation_resets_when_session_changes(tmp_path: Path) -> None:
    class _DummyReplayStore:
        def __init__(self) -> None:
            self.path = tmp_path / "last_operations.json"
            self._replay_session_id = "session-B"

        def _last_operations_path(self) -> Path:
            return self.path

        def _load_last_operations_payload(self) -> dict[str, object]:
            return CFVMain._load_last_operations_payload(self)

    host = _DummyReplayStore()

    host.path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": "session-A",
                "saved_at": "2026-06-08T00:00:00Z",
                "operations": [{"kind": "unary_xy", "field_index": 9, "operation": "grad"}],
            }
        ),
        encoding="utf-8",
    )

    CFVMain._record_replayable_operation(
        host,
        {"kind": "unary_xy", "field_index": 2, "operation": "laplacian"},
    )

    payload = json.loads(host.path.read_text(encoding="utf-8"))
    assert payload["session_id"] == "session-B"
    assert payload["operations"] == [{"kind": "unary_xy", "field_index": 2, "operation": "laplacian"}]


def test_remove_selected_fields_updates_ui_and_sends_worker_task() -> None:
    class _DummyItem:
        def __init__(self, idx: int) -> None:
            self.idx = idx

    class _DummyFieldListWidget:
        def __init__(self) -> None:
            self.items = [_DummyItem(0), _DummyItem(1), _DummyItem(2)]
            self.current_item: _DummyItem | None = None

        def selectedItems(self):
            return [self.items[0], self.items[2]]

        def row(self, item) -> int:
            return self.items.index(item)

        def takeItem(self, index: int):
            self.items.pop(index)

        def count(self) -> int:
            return len(self.items)

        def item(self, index: int):
            return self.items[index]

        def setCurrentItem(self, item) -> None:
            self.current_item = item

    class _DummyMain:
        def __init__(self) -> None:
            self.field_list_widget = _DummyFieldListWidget()
            self.sent_tasks: list[tuple[str, bool]] = []
            self._selected_field_indices = [0, 2]
            self.status_messages: list[str] = []
            self.clicked: list[object] = []

        def _send_worker_task(self, code: str, save_code_path: str | None = None, emit_image: bool = True) -> None:
            _ = save_code_path
            self.sent_tasks.append((code, emit_image))

        def _set_field_list_hint(self, text: str) -> None:
            _ = text

        def build_dynamic_sliders(self, metadata: dict[str, object]) -> None:
            _ = metadata

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            _ = is_error
            self.status_messages.append(message)

        def on_field_clicked(self, item) -> None:
            self.clicked.append(item)

    host = _DummyMain()

    CFVMain._remove_selected_fields(host)

    assert len(host.sent_tasks) == 1
    code, emit_image = host.sent_tasks[0]
    assert emit_image is False
    assert "remove_fields_by_index" in code
    assert host.field_list_widget.count() == 1
    assert host.status_messages[-1] == "Removed 2 field(s)."


def test_file_ops_save_selected_builds_worker_task(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyItem:
        def __init__(self, text: str) -> None:
            self._text = text

        def text(self) -> str:
            return self._text

        def data(self, _role: object):
            return ""

    class _DummyFieldListWidget:
        def __init__(self) -> None:
            self._items = [_DummyItem("a"), _DummyItem("b"), _DummyItem("c")]

        def selectedItems(self):
            return [self._items[0], self._items[2]]

        def row(self, item) -> int:
            return self._items.index(item)

    class _AcceptedDialog:
        def __init__(
            self,
            _parent,
            *,
            selected_rows: list[dict[str, object]],
            default_destination: str,
            default_output_filename: str,
        ) -> None:
            assert [str(row["identity"]) for row in selected_rows] == ["a", "c"]
            self.output_format = "zarr"
            self.destination_folder = "/tmp"
            self.output_filename = "custom_name"
            self.output_chunk_shapes = ["(10, 20)", "(5, 5)"]
            assert default_destination == "/tmp/default-save"
            assert default_output_filename == "cfv_plot_selected.nc"

        def exec(self) -> int:
            return 1

    class _DummyMain:
        def __init__(self) -> None:
            self.field_list_widget = _DummyFieldListWidget()
            self._settings = {"last_save_data_dir": "/tmp/default-save"}
            self.remembered: list[tuple[str, str]] = []
            self.sent: list[tuple[str, bool]] = []

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            raise AssertionError(f"unexpected status: {message} error={is_error}")

        def _default_plot_filename(self) -> str:
            return "cfv_plot"

        def _remember_last_save_dir(self, key: str, path: str) -> None:
            self.remembered.append((key, path))

        def _send_worker_task(self, code: str, save_code_path: str | None = None, emit_image: bool = True) -> None:
            _ = save_code_path
            self.sent.append((code, emit_image))

    monkeypatch.setattr(main_window, "SaveSelectedFieldsDialog", _AcceptedDialog)
    host = _DummyMain()

    CFVMain._file_ops_save_selected(host)

    assert host.remembered == [("last_save_data_dir", "/tmp/custom_name.zarr")]
    assert len(host.sent) == 1
    code, emit_image = host.sent[0]
    assert emit_image is False
    assert "save_selected_fields(" in code
    assert "_cfview_output_format = 'zarr'" in code
    assert "_cfview_output_chunk_by_index = {0: '(10, 20)', 2: '(5, 5)'}" in code


def test_file_ops_save_selected_provenance_dispatches_worker_control_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummyItem:
        def __init__(self, identity: str, source_file: str, generated: bool) -> None:
            self._identity = identity
            self._source_file = source_file
            self._generated = generated

        def text(self) -> str:
            return self._identity

        def data(self, role: object):
            if role == Qt.UserRole + 2:
                return self._source_file
            if role == Qt.UserRole + 4:
                return self._identity
            if role == Qt.UserRole + 5:
                return self._generated
            return ""

    class _DummyFieldListWidget:
        def __init__(self) -> None:
            self._items = [
                _DummyItem("src-field", "/tmp/a.nc", False),
                _DummyItem("derived-field", "", True),
            ]

        def selectedItems(self):
            return [self._items[1]]

        def row(self, item) -> int:
            return self._items.index(item)

        def count(self) -> int:
            return len(self._items)

        def item(self, index: int):
            return self._items[index]

    class _DummyMain:
        def __init__(self) -> None:
            self.field_list_widget = _DummyFieldListWidget()
            self._settings = {"last_save_data_dir": "/tmp"}
            self.saved_dirs: list[tuple[str, str]] = []
            self.control_tasks: list[tuple[str, dict[str, object]]] = []
            self.status_messages: list[str] = []

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            _ = is_error
            self.status_messages.append(message)

        def _default_plot_filename(self) -> str:
            return "cfv_plot"

        def _remember_last_save_dir(self, key: str, path: str) -> None:
            self.saved_dirs.append((key, path))

        def _load_last_operations_payload(self) -> dict[str, object]:
            return {
                "schema_version": 1,
                "session_id": "session-1",
                "saved_at": "2026-06-09T00:00:00Z",
                "operations": [
                    {
                        "kind": "unary_xy",
                        "field_index": 0,
                        "field_ref": {
                            "identity": "src-field",
                            "source_file": "/tmp/a.nc",
                            "generated": False,
                            "occurrence": 1,
                        },
                        "operation": "grad",
                        "source_file": "/tmp/a.nc",
                    }
                ],
            }

        def _source_files_for_replay_operation(self, operation: dict[str, object]) -> list[str]:
            return CFVMain._source_files_for_replay_operation(self, operation)

        def _build_remote_open_requests_for_sources(self, _sources: list[str]) -> list[dict[str, object]]:
            return []

        def _send_worker_control_task(self, kind: str, payload: dict[str, object]) -> None:
            self.control_tasks.append((kind, payload))

    monkeypatch.setattr(
        main_window.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: ("/tmp/selected.prov.json", "PROV JSON (*.prov.json)"),
    )

    host = _DummyMain()
    CFVMain._file_ops_save_selected_provenance(host)

    assert host.saved_dirs == [("last_save_data_dir", "/tmp/selected.prov.json")]
    assert len(host.control_tasks) == 1
    kind, payload = host.control_tasks[0]
    assert kind == "SAVE_PROVENANCE"
    assert payload["output_format"] == "prov-json"
    assert isinstance(payload.get("selected_field_refs"), list)
    assert host.status_messages[-1].startswith("Saving field-specific provenance")


def test_update_memory_status_formats_app_and_worker_rss(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeProcess:
        def __init__(self, pid: int) -> None:
            self._pid = pid

        def memory_info(self):
            class _Info:
                def __init__(self, rss: int) -> None:
                    self.rss = rss

            rss_by_pid = {111: 128 * 1024 * 1024, 222: 256 * 1024 * 1024}
            return _Info(rss_by_pid[self._pid])

    dummy = types.SimpleNamespace(
        _memory_status_label=_DummyMemoryLabel(),
        worker=_DummyWorkerForMemory(222),
    )
    dummy._process_rss_mib = types.MethodType(main_window.CFVMain._process_rss_mib, dummy)

    monkeypatch.setattr(main_window.os, "getpid", lambda: 111)
    monkeypatch.setattr(main_window.psutil, "Process", _FakeProcess)

    main_window.CFVMain._update_memory_status(dummy)

    assert dummy._memory_status_label.text == "Mem app: 128 MiB | worker: 256 MiB"


# ---------------------------------------------------------------------------
# Window title tests
# ---------------------------------------------------------------------------

@dataclass
class _DummyTitleWindow:
    base_window_title: str = "xconv2 (test)"
    current_file_path: str = ""
    _remote_descriptor: dict | None = None
    titles: list[str] = field(default_factory=list)

    def setWindowTitle(self, title: str) -> None:
        self.titles.append(title)

    def _set_window_title_for_file(self, file_path: str) -> None:  # super() fallback
        from pathlib import Path
        self.current_file_path = file_path
        self.setWindowTitle(f"{self.base_window_title}: {Path(file_path).name}")


def test_set_window_title_for_remote_file_includes_host_tag() -> None:
    window = _DummyTitleWindow(
        _remote_descriptor={
            "protocol": "sftp",
            "uri_scheme": "ssh",
            "display_name": "sci1",
        }
    )

    CFVMain._set_window_title_for_file(window, "/data/archive/model.nc")

    assert window.titles == ["xconv2 (test): model.nc (ssh:sci1)"]
    assert window.current_file_path == "/data/archive/model.nc"


def test_set_window_title_for_local_file_no_tag() -> None:
    # Without a remote descriptor the CFVMain override delegates to CFVCore,
    # which just shows the bare filename. Test CFVCore directly.
    from xconv2.core_window import CFVCore

    window = _DummyTitleWindow()

    CFVCore._set_window_title_for_file(window, "/home/user/data/local.nc")

    assert window.titles == ["xconv2 (test): local.nc"]
    assert window.current_file_path == "/home/user/data/local.nc"


def test_recent_menu_label_for_remote_uri_uses_filename_and_alias() -> None:
    from xconv2.core_window import CFVCore

    class _RecentLabelHost:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {
                    "https://example.org/archive/test1.nc": "canari",
                }
            }

    host = _RecentLabelHost()

    label = CFVCore._recent_menu_label(host, "https://example.org/archive/test1.nc")

    assert label == "test1.nc (canari)"


def test_default_open_uri_value_returns_most_recent_uri() -> None:
    from xconv2.core_window import CFVCore

    class _RecentDefaultHost:
        def __init__(self) -> None:
            self._recent = [
                "/tmp/local.nc",
                "ssh://alpha.example.org/data/field.nc",
                "https://example.org/archive/test2.nc",
            ]

        def _load_recent_files(self):
            return list(self._recent)

    host = _RecentDefaultHost()

    value = CFVCore._default_open_uri_value(host)

    assert value == "ssh://alpha.example.org/data/field.nc"


def test_default_open_uri_value_for_s3_uses_endpoint_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from xconv2.core_window import CFVCore

    class _RecentDefaultHost:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {
                    "s3://bnl/CMIP6-test.nc": "hpos",
                }
            }
            self._recent = [
                "s3://bnl/CMIP6-test.nc",
            ]

        def _load_recent_files(self):
            return list(self._recent)

        def _shareable_remote_uri(self, uri: str) -> str:
            return CFVCore._shareable_remote_uri(self, uri)

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_s3_locations",
        staticmethod(
            lambda: {
                "hpos": {
                    "url": "https://hpos.example.org",
                    "accessKey": "minioadmin",
                    "secretKey": "minioadmin",
                    "api": "S3v4",
                }
            }
        ),
    )

    host = _RecentDefaultHost()

    value = CFVCore._default_open_uri_value(host)

    assert value == "s3://hpos.example.org/bnl/CMIP6-test.nc"


def test_recent_menu_tooltip_for_s3_uses_shareable_host_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    from xconv2.core_window import CFVCore

    class _RecentTooltipHost:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {
                    "s3://bnl/CMIP6-test.nc": "hpos",
                }
            }

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_s3_locations",
        staticmethod(
            lambda: {
                "hpos": {
                    "url": "https://hpos.example.org",
                    "accessKey": "minioadmin",
                    "secretKey": "minioadmin",
                    "api": "S3v4",
                }
            }
        ),
    )

    host = _RecentTooltipHost()

    tooltip = CFVCore._recent_menu_tooltip(host, "s3://bnl/CMIP6-test.nc")

    assert tooltip == "s3://hpos.example.org/bnl/CMIP6-test.nc"


def test_https_locations_from_configure_are_passed_to_open_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyRemoteFlowWindow:
        def __init__(self) -> None:
            self._settings = {
                "last_remote_configuration": {},
                "last_remote_open": {},
            }
            self.saved = 0

        def _save_settings(self) -> None:
            self.saved += 1

        def _open_remote_from_config(self, _config: dict[str, object]) -> None:
            pass

        def _configure_remote(self) -> None:
            CFVMain._configure_remote(self)

    window = _DummyRemoteFlowWindow()

    configured_https = {
        "archive": {"url": "https://archive.example.org/data"},
    }

    def _fake_show_non_modal(cls, parent, state=None, on_finished=None):
        """Mock show_non_modal to call on_finished directly without creating a dialog."""
        next_state = dict(state or {})
        next_state["https_locations"] = configured_https
        if on_finished:
            on_finished(None, False, next_state)
        return None

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "show_non_modal",
        classmethod(_fake_show_non_modal)
    )

    captured_state: dict[str, object] = {}

    def _fake_open_config(_parent, state=None):
        nonlocal captured_state
        captured_state = dict(state or {})
        return None, False, dict(state or {})

    monkeypatch.setattr(main_window.RemoteOpenDialog, "get_configuration", _fake_open_config)

    # Save-only configure should persist HTTPS aliases into shared settings.
    CFVMain._configure_remote(window)
    assert window._settings["remote_https_locations"] == configured_https

    # Open dialog should receive merged HTTPS aliases from settings/config state.
    CFVMain._choose_remote(window)
    assert captured_state.get("https_locations") == configured_https


def test_choose_remote_injects_cache_defaults_when_open_dialog_returns_no_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummyRemoteFlowWindow:
        def __init__(self) -> None:
            self._settings = {
                "last_remote_configuration": {
                    "disk_mode": "Blocks",
                    "disk_location": "/tmp/xconv-cache",
                    "disk_limit_gb": 5,
                    "disk_expiry": "7 days",
                },
                "last_remote_open": {},
            }
            self.saved = 0
            self.opened_config: dict[str, object] | None = None

        def _save_settings(self) -> None:
            self.saved += 1

        def _open_remote_from_config(self, config: dict[str, object]) -> None:
            self.opened_config = dict(config)

        def _with_cache_defaults(self, config: dict[str, object]) -> dict[str, object]:
            return CFVMain._with_cache_defaults(self, config)

    window = _DummyRemoteFlowWindow()

    monkeypatch.setattr(
        main_window.RemoteOpenDialog,
        "get_configuration",
        lambda _parent, state=None: (
            {"protocol": "HTTPS", "remote": {"mode": "Select from existing", "alias": "archive", "details": {"url": "https://example.org"}}},
            True,
            dict(state or {}),
        ),
    )

    CFVMain._choose_remote(window)

    assert window.opened_config is not None
    cache = window.opened_config.get("cache")
    assert isinstance(cache, dict)
    assert cache == {
        "disk_mode": "Blocks",
        "disk_location": "/tmp/xconv-cache",
        "disk_limit_gb": 5,
        "disk_expiry": "7 days",
    }


def test_open_remote_from_config_keeps_existing_session_and_clears_loaded_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummyRemoteOpenWindow:
        def __init__(self) -> None:
            self._settings = {}
            self.released = 0
            self.cleared = 0
            self._remote_session_id = None
            self._remote_descriptor_hash = None
            self._remote_descriptor = None
            self._pending_prepare_log_dialog = None
            self._pending_prepare_loop = None
            self._pending_prepare_loop_ok = True
            self._pending_prepare_failure_message = ""

        def _prepare_ssh_config_for_auth(self, config: dict[str, object]) -> dict[str, object]:
            return config

        def _release_remote_session_if_active(self) -> None:
            self.released += 1

        def _clear_loaded_data_views(self) -> None:
            self.cleared += 1

        def _maybe_retry_ssh_authentication(self, _config: dict[str, object], _failure_message: str) -> bool:
            return False

        def _send_worker_control_task(self, _kind: str, _payload: dict[str, object]) -> None:
            return None

        def _make_worker_list_callback(self):
            return lambda _path: []

        def _show_status_message(self, _message: str, is_error: bool = False) -> None:
            _ = is_error

        def _set_window_title_for_file(self, _file_path: str) -> None:
            return None

        def _record_recent_uri(self, _uri: str, _host_alias: str | None = None) -> None:
            return None

        def _record_recent_file(self, _file_path: str) -> None:
            return None

        def _load_remote_selected_file(self, _uri: str, _remote_path: str) -> None:
            return None

    window = _DummyRemoteOpenWindow()

    monkeypatch.setattr(
        _remote_access_mod,
        "build_remote_filesystem_spec",
        lambda _config: types.SimpleNamespace(display_name="HTTP"),
    )
    monkeypatch.setattr(_remote_access_mod, "spec_to_descriptor", lambda _spec, cache=None: {"protocol": "http", "cache": cache})
    monkeypatch.setattr(_remote_access_mod, "remote_descriptor_hash", lambda _descriptor: "hash-1")

    class _FakeLogDialog:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def show(self) -> None:
            return None

        def close(self) -> None:
            return None

        def exec(self) -> int:
            return 0

    class _FakeNavigator:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(_remote_file_nav_mod, "RemoteLoginLogDialog", _FakeLogDialog)
    monkeypatch.setattr(_remote_file_nav_mod, "RemoteFileNavigatorDialog", _FakeNavigator)
    monkeypatch.setattr(main_window.QApplication, "processEvents", staticmethod(lambda: None))

    class _FakeLoop:
        def exec(self) -> None:
            return None

    monkeypatch.setattr(main_window, "QEventLoop", _FakeLoop)

    CFVMain._open_remote_from_config(window, {"protocol": "HTTP", "remote": {"details": {"url": "http://server/public"}}})

    assert window.released == 0
    assert window.cleared == 1


def test_browse_remote_shutdown_session_releases_active_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummyBrowseWindow:
        def __init__(self) -> None:
            self._remote_session_id = "session-1"
            self._remote_descriptor_hash = "hash-1"
            self._remote_descriptor = {"protocol": "sftp"}
            self._last_remote_config = {"protocol": "SSH", "remote": {"alias": "alpha", "details": {"hostname": "alpha.example"}}}
            self._last_remote_navigator_state = None
            self.released = 0
            self.messages: list[tuple[str, bool]] = []
            self.chosen = 0

        def _make_worker_list_callback(self):
            return lambda _path: []

        def _release_remote_session_if_active(self) -> None:
            self.released += 1

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.messages.append((message, is_error))

        def _choose_remote(self) -> None:
            self.chosen += 1

        def _set_window_title_for_file(self, _file_path: str) -> None:
            return None

        def _record_recent_uri(self, _uri: str, _alias: str) -> None:
            return None

        def _record_recent_file(self, _uri: str) -> None:
            return None

        def _load_remote_selected_file(self, _uri: str, _path: str) -> None:
            return None

    class _FakeNavigator:
        def __init__(self, *_args, **_kwargs) -> None:
            self.new_remote_requested = False
            self.shutdown_session_requested = True

        def exec(self) -> int:
            return 0

        def _collect_tree_state(self):
            return ([], "")

        def selected_uri(self) -> str:
            return ""

        def selected_path(self) -> str:
            return ""

    window = _DummyBrowseWindow()

    monkeypatch.setattr(
        _remote_access_mod,
        "build_remote_filesystem_spec",
        lambda _config: types.SimpleNamespace(display_name="SSH", protocol="sftp", root_path="."),
    )
    monkeypatch.setattr(_remote_file_nav_mod, "RemoteFileNavigatorDialog", _FakeNavigator)

    CFVMain._browse_remote(window)

    assert window.released == 1
    assert window.chosen == 0
    assert ("Remote session shut down.", False) in window.messages


def test_resolve_remote_uri_s3_prefers_recent_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyResolveWindow:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {
                    "s3://bnl/CMIP6-test.nc": "hpos",
                },
                "last_remote_configuration": {
                    "s3_existing_alias": "hpos",
                },
            }

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_s3_locations",
        staticmethod(
            lambda: {
                "hpos": {
                    "url": "https://hpos.example.org",
                    "accessKey": "minioadmin",
                    "secretKey": "minioadmin",
                    "api": "S3v4",
                }
            }
        ),
    )

    window = _DummyResolveWindow()

    config, remote_path, host_alias, unknown_host = CFVMain._resolve_remote_uri(
        window,
        "s3://bnl/CMIP6-test.nc",
    )

    assert unknown_host is False
    assert remote_path == "bnl/CMIP6-test.nc"
    assert host_alias == "hpos"
    assert config is not None
    assert config["protocol"] == "S3"
    remote = config["remote"]
    assert isinstance(remote, dict)
    assert remote.get("alias") == "hpos"
    details = remote.get("details")
    assert isinstance(details, dict)
    assert details.get("url") == "https://hpos.example.org"


def test_resolve_remote_uri_s3_accepts_legacy_single_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyResolveWindow:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {
                    "s3://bnl/CMIP6-test.nc": "hpos",
                },
                "last_remote_configuration": {
                    "s3_existing_alias": "hpos",
                },
            }

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_s3_locations",
        staticmethod(
            lambda: {
                "hpos": {
                    "url": "https://hpos.example.org",
                    "accessKey": "minioadmin",
                    "secretKey": "minioadmin",
                    "api": "S3v4",
                }
            }
        ),
    )

    window = _DummyResolveWindow()

    config, remote_path, host_alias, unknown_host = CFVMain._resolve_remote_uri(
        window,
        "s3:/bnl/CMIP6-test.nc",
    )

    assert unknown_host is False
    assert remote_path == "bnl/CMIP6-test.nc"
    assert host_alias == "hpos"
    assert config is not None


def test_resolve_remote_uri_s3_accepts_host_based_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyResolveWindow:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {},
                "last_remote_configuration": {
                    "s3_existing_alias": "hpos",
                },
            }

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_s3_locations",
        staticmethod(
            lambda: {
                "hpos": {
                    "url": "https://hpos.example.org",
                    "accessKey": "minioadmin",
                    "secretKey": "minioadmin",
                    "api": "S3v4",
                }
            }
        ),
    )

    window = _DummyResolveWindow()

    config, remote_path, host_alias, unknown_host = CFVMain._resolve_remote_uri(
        window,
        "s3://hpos.example.org/bnl/CMIP6-test.nc",
    )

    assert unknown_host is False
    assert remote_path == "bnl/CMIP6-test.nc"
    assert host_alias == "hpos"
    assert config is not None


def test_resolve_remote_uri_ssh_strips_leading_slash_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyResolveWindow:
        def __init__(self) -> None:
            self._settings = {
                "last_remote_configuration": {},
                "last_remote_open": {},
            }

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_ssh_hosts",
        staticmethod(
            lambda: {
                "alpha": {
                    "hostname": "alpha.example.org",
                    "user": "alice",
                }
            }
        ),
    )

    window = _DummyResolveWindow()

    config, remote_path, host_alias, unknown_host = CFVMain._resolve_remote_uri(
        window,
        "ssh://alpha.example.org/home/alice/data/file.nc",
    )

    assert unknown_host is False
    assert config is not None
    assert host_alias == "alpha"
    assert remote_path == "home/alice/data/file.nc"


def test_resolve_remote_uri_ssh_applies_runtime_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyResolveWindow:
        def __init__(self) -> None:
            self._settings = {
                "last_remote_configuration": {
                    "ssh_runtime_preferences": {
                        "alpha": {
                            "remote_python": "conda run -p /opt/env --no-capture-output python",
                            "remote_python_options": {
                                "env": "conda run -p /opt/env --no-capture-output python"
                            },
                            "login_shell": True,
                        }
                    }
                },
                "last_remote_open": {},
            }

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_ssh_hosts",
        staticmethod(
            lambda: {
                "alpha": {
                    "hostname": "alpha.example.org",
                    "user": "alice",
                    "identityfile": "~/.ssh/id_alpha",
                }
            }
        ),
    )

    window = _DummyResolveWindow()

    config, remote_path, host_alias, unknown_host = CFVMain._resolve_remote_uri(
        window,
        "ssh://alpha.example.org/data/test.nc",
    )

    assert unknown_host is False
    assert remote_path == "data/test.nc"
    assert host_alias == "alpha"
    assert config is not None
    remote = config["remote"]
    assert isinstance(remote, dict)
    details = remote.get("details")
    assert isinstance(details, dict)
    assert details.get("remote_python") == "conda run -p /opt/env --no-capture-output python"
    assert details.get("login_shell") is True


def test_open_remote_uri_direct_reuses_active_matching_session(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyUriWindow:
        def __init__(self) -> None:
            self._settings = {"last_remote_configuration": {}}
            self._remote_session_id = "session-existing"
            self._remote_descriptor_hash = "hash-ssh"
            self._remote_descriptor = {"protocol": "sftp"}
            self._last_remote_config = None
            self._last_remote_navigator_state = None
            self.sent_control_tasks: list[tuple[str, dict[str, object]]] = []
            self.loaded: list[tuple[str, str]] = []

        def _prepare_ssh_config_for_auth(self, config: dict[str, object]) -> dict[str, object]:
            return config

        def _clear_loaded_data_views(self) -> None:
            return None

        def _send_worker_control_task(self, kind: str, payload: dict[str, object]) -> None:
            self.sent_control_tasks.append((kind, payload))

        def _show_status_message(self, _message: str, is_error: bool = False) -> None:
            _ = is_error

        def _set_window_title_for_file(self, _uri: str) -> None:
            return None

        def _record_recent_uri(self, _uri: str, _alias: str) -> None:
            return None

        def _load_remote_selected_file(self, uri: str, remote_path: str) -> None:
            self.loaded.append((uri, remote_path))

    window = _DummyUriWindow()

    monkeypatch.setattr(
        _remote_access_mod,
        "build_remote_filesystem_spec",
        lambda _config: types.SimpleNamespace(display_name="SSH"),
    )
    monkeypatch.setattr(
        _remote_access_mod,
        "spec_to_descriptor",
        lambda _spec, cache=None: {"protocol": "sftp", "cache": cache},
    )
    monkeypatch.setattr(_remote_access_mod, "remote_descriptor_hash", lambda _descriptor: "hash-ssh")

    CFVMain._open_remote_uri_direct(
        window,
        uri="ssh://alpha.example.org/data/file.nc",
        remote_path="/data/file.nc",
        config={"protocol": "SSH", "remote": {"mode": "Select from existing", "alias": "alpha", "details": {"hostname": "alpha.example.org", "user": "alice"}}},
        host_alias="alpha",
    )

    assert window.sent_control_tasks == []
    assert window.loaded == [("ssh://alpha.example.org/data/file.nc", "/data/file.nc")]
    assert isinstance(window._last_remote_config, dict)
    assert window._last_remote_config.get("protocol") == "SSH"
