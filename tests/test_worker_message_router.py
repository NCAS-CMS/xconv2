from __future__ import annotations

from dataclasses import dataclass, field
import base64
import pickle

from xconv2.worker_message_router import WorkerMessageRouter, WorkerStatusHandler


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
