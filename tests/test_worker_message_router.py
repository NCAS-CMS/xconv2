from __future__ import annotations

from dataclasses import dataclass, field
import base64
import logging
import pickle
from types import SimpleNamespace

from xconv2.worker_message_router import WorkerMessageRouter, WorkerStatusHandler


def test_coordinate_router_ignores_stale_requests_without_consuming_autoplot() -> None:
    built = []
    plotted = []
    modes = []
    host = SimpleNamespace(
        _pending_coordinate_request=("request-b", 1),
        _pending_initial_autoplot_field_index=1,
        _normalize_coordinate_metadata=lambda coords: {"coords": coords},
        build_dynamic_sliders=built.append,
        _set_selection_panel_mode=modes.append,
        _auto_plot_initial_field_selection=lambda: plotted.append("plot"),
    )
    router = WorkerMessageRouter(host)

    def deliver(request_id, index, coords):
        payload = {"request_id": request_id, "field_index": index, "coords": coords}
        encoded = base64.b64encode(pickle.dumps(payload)).decode("ascii")
        router.handle_line(f"COORD:{encoded}")

    deliver("request-a", 0, ["old coordinates"])
    deliver("request-a", 0, [])
    deliver("request-b", 0, ["wrong field"])
    assert built == []
    assert plotted == []
    assert modes == []
    assert host._pending_coordinate_request == ("request-b", 1)
    assert host._pending_initial_autoplot_field_index == 1

    deliver("request-b", 1, ["new coordinates"])
    assert built == [{"coords": ["new coordinates"]}]
    assert plotted == ["plot"]
    assert host._pending_coordinate_request is None

    # A repeated response, or an old request for the same field, is still stale.
    host._pending_coordinate_request = ("request-b-again", 1)
    deliver("request-b", 1, ["old coordinates for same field"])
    assert len(built) == 1
    assert host._pending_coordinate_request == ("request-b-again", 1)
    host._pending_coordinate_request = None
    deliver("request-b-again", 1, ["deselected field"])
    assert len(built) == 1


def test_current_empty_coordinate_response_clears_only_its_pending_plot() -> None:
    cleared = []
    messages = []
    modes = []
    host = SimpleNamespace(
        _pending_coordinate_request=("current", 2),
        _normalize_coordinate_metadata=lambda coords: {},
        _set_selection_panel_mode=modes.append,
        _clear_pending_initial_autoplot=lambda: cleared.append(True),
        _show_status_message=messages.append,
    )
    payload = {"request_id": "current", "field_index": 2, "coords": []}
    encoded = base64.b64encode(pickle.dumps(payload)).decode("ascii")

    WorkerMessageRouter(host).handle_line(f"COORD:{encoded}")

    assert host._pending_coordinate_request is None
    assert cleared == [True]
    assert modes == ["multi"]
    assert messages == ["No slider-friendly coordinates were found. Use coordinate bounds commands."]


@dataclass
class _DummyLoop:
    quit_called: bool = False

    def quit(self) -> None:
        self.quit_called = True


@dataclass
class _DummyStatusHost:
    _suppress_stale_error_status: bool = False
    _plot_request_in_flight: bool = False
    _plot_request_expects_image: bool = False
    _pending_metadata_loop: _DummyLoop | None = None
    _pending_metadata_error: str = ""
    shown_statuses: list[tuple[str, bool]] = field(default_factory=list)
    saved_statuses: list[str] = field(default_factory=list)
    complete_calls: list[bool] = field(default_factory=list)
    binary_dialog_inputs: list[str] = field(default_factory=list)
    cleared_messages: list[str] = field(default_factory=list)
    loading_calls: list[bool] = field(default_factory=list)

    def _complete_pending_worker_task(self, consume: bool = True) -> float | None:
        self.complete_calls.append(consume)
        return 2.5

    def _apply_saved_selected_status(self, status_text: str) -> None:
        self.saved_statuses.append(status_text)

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        self.shown_statuses.append((message, is_error))

    def _maybe_show_binary_validation_dialog(self, status_text: str) -> None:
        self.binary_dialog_inputs.append(status_text)

    def _clear_plot_canvas(self, message: str = "Plot unavailable") -> None:
        self.cleared_messages.append(message)

    def _set_plot_loading(self, is_loading: bool, message: str = "Rendering plot...") -> None:
        _ = message
        self.loading_calls.append(is_loading)


@dataclass
class _DummyRouterHost(_DummyStatusHost):
    _pending_filter_axes_result: list[str] | None = None
    _pending_filter_axes_loop: _DummyLoop | None = None
    pass


def test_router_delivers_science_stack_report() -> None:
    class _Host:
        report = ""

        def _receive_science_stack_report(self, report: str) -> None:
            self.report = report

    host = _Host()
    encoded = base64.b64encode(pickle.dumps("cf: 3.21.0\ncommit: abc")).decode("ascii")
    WorkerMessageRouter(host).handle_line(f"SCIENCE_STACK_RESULT:{encoded}")
    assert host.report == "cf: 3.21.0\ncommit: abc"


def test_router_reports_invalid_science_stack_payload(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="xconv2.worker_message_router")
    class _Host:
        report = ""

        def _receive_science_stack_report(self, report: str) -> None:
            self.report = report

    host = _Host()
    encoded = base64.b64encode(pickle.dumps({"unexpected": True})).decode("ascii")
    WorkerMessageRouter(host).handle_line(f"SCIENCE_STACK_RESULT:{encoded}")
    assert "Unable to decode" in host.report
    assert "Unexpected SCIENCE_STACK_RESULT" in caplog.text


def test_status_handler_formats_task_complete_with_elapsed_time() -> None:
    host = _DummyStatusHost()
    handler = WorkerStatusHandler(host)

    handler.handle_status_line("STATUS:Task Complete")

    assert host.complete_calls == [True]
    assert host.saved_statuses == ["Task Complete (2.50s)"]
    assert host.shown_statuses == [("Task Complete (2.50s)", False)]


def test_status_handler_ignores_stale_error_when_not_plotting() -> None:
    host = _DummyStatusHost(_suppress_stale_error_status=True, _plot_request_in_flight=False)
    handler = WorkerStatusHandler(host)

    handler.handle_status_line("STATUS:Error - old failure")

    assert host.complete_calls == [True]
    assert host.saved_statuses == []
    assert host.shown_statuses == []
    assert host.binary_dialog_inputs == []


def test_status_handler_sets_metadata_error_and_quits_loop_on_error() -> None:
    loop = _DummyLoop()
    host = _DummyStatusHost(_pending_metadata_loop=loop)
    handler = WorkerStatusHandler(host)

    handler.handle_status_line("STATUS:Error - metadata fetch failed")

    assert host._pending_metadata_error == "Error - metadata fetch failed"
    assert host._pending_metadata_loop is None
    assert loop.quit_called is True
    assert host.binary_dialog_inputs == ["Error - metadata fetch failed"]


def test_status_handler_finishes_plot_on_error() -> None:
    host = _DummyStatusHost(_plot_request_in_flight=True, _plot_request_expects_image=True)
    handler = WorkerStatusHandler(host)

    handler.handle_status_line("STATUS:Error - plotting failed")

    assert host._plot_request_in_flight is False
    assert host._plot_request_expects_image is False
    assert host.cleared_messages == ["Plot failed."]
    assert host.loading_calls == [False]


def test_router_delegates_status_lines_to_status_handler() -> None:
    host = _DummyRouterHost()
    router = WorkerMessageRouter(host)

    router.handle_line("STATUS:Task Complete")

    assert host.saved_statuses == ["Task Complete (2.50s)"]
    assert host.shown_statuses == [("Task Complete (2.50s)", False)]


def test_router_handles_filter_axes_payload_and_quits_loop() -> None:
    loop = _DummyLoop()
    host = _DummyRouterHost(_pending_filter_axes_loop=loop)
    router = WorkerMessageRouter(host)

    payload = base64.b64encode(pickle.dumps(["t", "x"])).decode("ascii")
    router.handle_line(f"FILTER_AXES:{payload}")

    assert host._pending_filter_axes_result == ["T", "X"]
    assert host._pending_filter_axes_loop is None
    assert loop.quit_called is True
