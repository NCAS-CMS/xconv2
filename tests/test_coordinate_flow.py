from __future__ import annotations

import base64
import pickle
from dataclasses import dataclass, field
import types

from xconv2.main_window import CFVMain


@dataclass
class _DummyMain:
    built_slider_payloads: list[dict[str, object]] = field(default_factory=list)

    def build_dynamic_sliders(self, metadata: dict[str, object]) -> None:
        self.built_slider_payloads.append(metadata)

    def _show_status_message(self, _message: str, is_error: bool = False) -> None:
        _ = is_error

    def _set_plot_loading(self, _is_loading: bool, message: str = "Rendering plot...") -> None:
        _ = message

    def _clear_plot_canvas(self, message: str = "Plot unavailable") -> None:
        _ = message


@dataclass
class _FakeLine:
    text: str

    def data(self) -> bytes:
        return self.text.encode()


class _FakeWorker:
    def __init__(self, lines: list[str]) -> None:
        self._lines = [_FakeLine(line) for line in lines]

    def canReadLine(self) -> bool:
        return bool(self._lines)

    def readLine(self) -> _FakeLine:
        return self._lines.pop(0)


class _FakeRangeSlider:
    def __init__(self, bounds: tuple[int, int]) -> None:
        self._bounds = bounds

    def value(self) -> tuple[int, int]:
        return self._bounds


@dataclass
class _DummyFieldMetadataController:
    clicked_items: list[object] = field(default_factory=list)

    def on_field_clicked(self, item: object) -> None:
        self.clicked_items.append(item)


@dataclass
class _DummyFieldListWidget:
    index_to_return: int

    def row(self, _item: object) -> int:
        return self.index_to_return


@dataclass
class _DummyResetMain:
    _plot_request_in_flight: bool = True
    _plot_request_expects_image: bool = True
    loading_calls: list[bool] = field(default_factory=list)
    canvas_messages: list[str] = field(default_factory=list)
    status_messages: list[str] = field(default_factory=list)

    def _set_plot_loading(self, is_loading: bool, message: str = "Rendering plot...") -> None:
        _ = message
        self.loading_calls.append(is_loading)

    def _clear_plot_canvas(self, message: str = "Plot unavailable") -> None:
        self.canvas_messages.append(message)

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        _ = is_error
        self.status_messages.append(message)


@dataclass
class _DummyCoordRequestMain:
    status_messages: list[str] = field(default_factory=list)
    sent_tasks: list[str] = field(default_factory=list)

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        _ = is_error
        self.status_messages.append(message)

    def _send_worker_task(self, code: str) -> None:
        self.sent_tasks.append(code)


@dataclass
class _DummyStaleErrorMain:
    _plot_request_in_flight: bool = False
    _plot_request_expects_image: bool = False
    _suppress_stale_error_status: bool = True
    shown_statuses: list[tuple[str, bool]] = field(default_factory=list)
    cleared_messages: list[str] = field(default_factory=list)
    loading_calls: list[bool] = field(default_factory=list)

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        self.shown_statuses.append((message, is_error))

    def _clear_plot_canvas(self, message: str = "Plot unavailable") -> None:
        self.cleared_messages.append(message)

    def _set_plot_loading(self, is_loading: bool, message: str = "Rendering plot...") -> None:
        _ = message
        self.loading_calls.append(is_loading)


def test_normalize_coordinate_metadata_filters_and_coerces() -> None:
    payload = [
        ("time", ["1850-01-16", "1850-02-16"], "days since 1850-01-01 gregorian"),
        ("lat", ("-90", "0", "90")),
        ("empty", []),
        ("none", None),
        "bad-entry",
        ("too-short",),
    ]

    normalized = CFVMain._normalize_coordinate_metadata(None, payload)

    assert normalized == {
        "time": {
            "values": ["1850-01-16", "1850-02-16"],
            "units": "days since 1850-01-01 gregorian",
        },
        "lat": {
            "values": ["-90", "0", "90"],
            "units": "",
        },
    }


def test_handle_worker_output_coord_routes_to_slider_builder() -> None:
    coord_payload = [
        ("time", ["1850-01-16", "1850-02-16"], "days since 1850-01-01 gregorian"),
        ("lat", ["-90", "0", "90"], "degrees_north"),
    ]
    encoded = base64.b64encode(pickle.dumps(coord_payload)).decode()
    line = f"COORD:{encoded}\n"

    dummy = _DummyMain()
    dummy._normalize_coordinate_metadata = lambda payload: CFVMain._normalize_coordinate_metadata(None, payload)
    dummy.worker = _FakeWorker([line])

    CFVMain.handle_worker_output(dummy)

    assert len(dummy.built_slider_payloads) == 1
    assert dummy.built_slider_payloads[0] == {
        "time": {
            "values": ["1850-01-16", "1850-02-16"],
            "units": "days since 1850-01-01 gregorian",
        },
        "lat": {
            "values": ["-90", "0", "90"],
            "units": "degrees_north",
        },
    }


def test_build_plot_context_treats_adjacent_first_value_singletons_as_1d() -> None:
    dummy = _DummyMain()
    dummy.controls = {
        "time": {
            "values": ["t0", "t1", "t2"],
            "range_slider": _FakeRangeSlider((0, 1)),
        },
        "lat": {
            "values": ["-90", "0", "90"],
            "range_slider": _FakeRangeSlider((0, 2)),
        },
        "lon": {
            "values": ["0", "120", "240", "360"],
            "range_slider": _FakeRangeSlider((1, 3)),
        },
    }
    dummy.selected_collapse_methods = {}

    context = CFVMain._build_plot_context(dummy)

    assert context is not None
    selections, collapse_by_coord, plot_kind = context
    assert selections["time"] == ("t0", "t0")
    assert collapse_by_coord == {}
    assert plot_kind == "contour"


def test_build_plot_context_treats_adjacent_last_value_singletons_as_1d() -> None:
    dummy = _DummyMain()
    dummy.controls = {
        "time": {
            "values": [1, 2, 3],
            "range_slider": _FakeRangeSlider((1, 2)),
        },
        "lat": {
            "values": [-90, 0, 90],
            "range_slider": _FakeRangeSlider((0, 2)),
        },
    }
    dummy.selected_collapse_methods = {}

    context = CFVMain._build_plot_context(dummy)

    assert context is not None
    selections, collapse_by_coord, plot_kind = context
    assert selections["time"] == (3, 3)
    assert selections["lat"] == (-90, 90)
    assert collapse_by_coord == {}
    assert plot_kind == "lineplot"


def test_reset_ui_for_new_field_selection_clears_error_state() -> None:
    dummy = _DummyResetMain()

    CFVMain._reset_ui_for_new_field_selection(dummy)

    assert dummy._plot_request_in_flight is False
    assert dummy._plot_request_expects_image is False
    assert dummy.loading_calls[-1] is False
    assert dummy.canvas_messages[-1] == "Waiting for data..."
    assert dummy.status_messages[-1] == "Task Complete"


def test_request_coordinates_can_skip_status_message(monkeypatch) -> None:
    dummy = _DummyCoordRequestMain()

    monkeypatch.setattr(
        "xconv2.main_window.coordinate_list",
        lambda index: f"TASK_FOR_{index}",
    )

    CFVMain._request_coordinates_for_field(dummy, 4, show_status=False)

    assert dummy.status_messages == []
    assert dummy.sent_tasks == ["TASK_FOR_4"]


def test_on_field_clicked_resets_ui_then_requests_coordinates() -> None:
    """Field click should flow through core handling, reset UI, then request coordinates."""
    window = CFVMain.__new__(CFVMain)
    field_controller = _DummyFieldMetadataController()
    window.field_metadata_controller = field_controller
    window.field_list_widget = _DummyFieldListWidget(index_to_return=7)

    call_order: list[tuple[str, object]] = []

    window._reset_ui_for_new_field_selection = types.MethodType(
        lambda self: call_order.append(("reset", None)),
        window,
    )
    window._request_coordinates_for_field = types.MethodType(
        lambda self, index, show_status=True: call_order.append(("request", (index, show_status))),
        window,
    )

    fake_item = object()
    CFVMain.on_field_clicked(window, fake_item)

    # The core-window behavior should still run first.
    assert field_controller.clicked_items == [fake_item]
    # Then CFVMain-specific flow should reset stale state and request coordinates.
    assert call_order == [
        ("reset", None),
        ("request", (7, False)),
    ]


def test_handle_worker_output_ignores_stale_error_after_field_reset() -> None:
    dummy = _DummyStaleErrorMain()
    dummy.worker = _FakeWorker(["STATUS:Error - old failure from previous field\n"])

    CFVMain.handle_worker_output(dummy)

    assert dummy.shown_statuses == []
    assert dummy._suppress_stale_error_status is True


def test_handle_worker_output_ignores_stale_error_after_coord_message() -> None:
    coord_payload = [("time", ["1", "2"])]
    encoded = base64.b64encode(pickle.dumps(coord_payload)).decode()

    dummy = _DummyStaleErrorMain()
    dummy._normalize_coordinate_metadata = lambda payload: CFVMain._normalize_coordinate_metadata(None, payload)
    dummy.build_dynamic_sliders = lambda metadata: None
    dummy.worker = _FakeWorker(
        [
            f"COORD:{encoded}\n",
            "STATUS:Error - old failure from previous field\n",
        ]
    )

    CFVMain.handle_worker_output(dummy)

    assert dummy.shown_statuses == []
    assert dummy._suppress_stale_error_status is True


def test_handle_worker_output_remote_status_routes_message() -> None:
    payload = {
        "phase": "preparing",
        "session_id": "abc",
        "descriptor_hash": "hash",
        "message": "Preparing remote worker session...",
    }
    encoded = base64.b64encode(pickle.dumps(payload)).decode()

    dummy = _DummyStaleErrorMain()
    dummy.worker = _FakeWorker([f"REMOTE_STATUS:{encoded}\n"])

    CFVMain.handle_worker_output(dummy)

    assert dummy.shown_statuses == [("Preparing remote worker session...", False)]


def test_handle_worker_output_remote_open_failure_shows_error() -> None:
    payload = {
        "session_id": "abc",
        "uri": "ssh://host/file.nc",
        "ok": False,
        "error": "Remote open failed",
    }
    encoded = base64.b64encode(pickle.dumps(payload)).decode()

    dummy = _DummyStaleErrorMain()
    dummy.worker = _FakeWorker([f"REMOTE_OPEN_RESULT:{encoded}\n"])

    CFVMain.handle_worker_output(dummy)

    assert dummy.shown_statuses == [("Remote open failed", True)]
