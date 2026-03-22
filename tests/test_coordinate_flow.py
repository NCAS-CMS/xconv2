from __future__ import annotations

import base64
from collections import deque
import tempfile
import pickle
from dataclasses import dataclass, field
import types
from pathlib import Path

from PySide6.QtWidgets import QMessageBox, QStyle

from xconv2.cache_utils import prune_disk_cache
from xconv2.main_window import CFVMain
from xconv2.core_window import CFVCore
from xconv2.ui.plot_view_controller import PlotViewController


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
    _selection_info_visible: bool = False
    loading_calls: list[bool] = field(default_factory=list)
    canvas_messages: list[str] = field(default_factory=list)
    status_messages: list[str] = field(default_factory=list)
    panel_visible_calls: list[bool] = field(default_factory=list)
    button_sync_calls: int = 0

    def _set_selection_info_panel_visible(self, visible: bool) -> None:
        self._selection_info_visible = visible
        self.panel_visible_calls.append(visible)

    def _update_selection_info_toggle_button(self) -> None:
        self.button_sync_calls += 1

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
class _DummyPlotOptionsMain:
    _context: tuple[dict[str, tuple[object, object]], dict[str, str], str] | None
    lineplot_dialog_calls: int = 0
    sent_tasks: list[str] = field(default_factory=list)
    status_messages: list[str] = field(default_factory=list)

    def _build_plot_context(self):
        return self._context

    def _show_lineplot_options_dialog(self) -> None:
        self.lineplot_dialog_calls += 1

    def _send_worker_task(self, code: str, emit_image: bool = True) -> None:
        _ = emit_image
        self.sent_tasks.append(code)

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        _ = is_error
        self.status_messages.append(message)


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


@dataclass
class _DummyVisibilityPanel:
    visible: bool = True
    text: str = ""

    def setVisible(self, visible: bool) -> None:
        self.visible = visible

    def setPlainText(self, text: str) -> None:
        self.text = text

    def isVisible(self) -> bool:
        return self.visible

    def isHidden(self) -> bool:
        return not self.visible


@dataclass
class _DummyVisibilityButton:
    icon: object | None = None
    tooltip: str = ""
    status_tip: str = ""

    def setIcon(self, icon: object) -> None:
        self.icon = icon

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip

    def setStatusTip(self, status_tip: str) -> None:
        self.status_tip = status_tip


class _DummyStyle:
    def standardIcon(self, icon_kind: QStyle.StandardPixmap) -> QStyle.StandardPixmap:
        return icon_kind


@dataclass
class _DummyClearLoadedDataMain:
    base_window_title: str = "xconv2 (test)"
    current_file_path: str | None = "/tmp/old.nc"
    current_selection_info_text: str = "old"
    plot_info_output: _DummyVisibilityPanel = field(default_factory=_DummyVisibilityPanel)
    field_hints: list[str] = field(default_factory=list)
    built_slider_payloads: list[dict[str, object]] = field(default_factory=list)
    panel_visible_calls: list[bool] = field(default_factory=list)
    button_sync_calls: int = 0
    loading_calls: list[bool] = field(default_factory=list)
    canvas_messages: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)

    def setWindowTitle(self, title: str) -> None:
        self.titles.append(title)

    def _set_field_list_hint(self, text: str) -> None:
        self.field_hints.append(text)

    def build_dynamic_sliders(self, metadata: dict[str, object]) -> None:
        self.built_slider_payloads.append(metadata)

    def _set_selection_info_panel_visible(self, visible: bool) -> None:
        self.panel_visible_calls.append(visible)

    def _update_selection_info_toggle_button(self) -> None:
        self.button_sync_calls += 1

    def _set_plot_loading(self, is_loading: bool, message: str = "Rendering plot...") -> None:
        _ = message
        self.loading_calls.append(is_loading)

    def _clear_plot_canvas(self, message: str = "Plot unavailable") -> None:
        self.canvas_messages.append(message)


@dataclass
class _DummyCacheManagerHost:
    _settings: dict[str, object]
    _remote_session_id: str | None = None
    _remote_descriptor: dict[str, object] | None = None
    status_messages: list[tuple[str, bool]] = field(default_factory=list)
    released: int = 0

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        self.status_messages.append((message, is_error))

    def _release_remote_session_if_active(self) -> None:
        self.released += 1

    def _active_cache_settings(self) -> dict[str, object]:
        return CFVCore._active_cache_settings(self)

    def _disk_cache_usage(self, location: Path) -> tuple[int, int]:
        return CFVCore._disk_cache_usage(self, location)

    def _format_storage_size(self, size_bytes: int) -> str:
        return CFVCore._format_storage_size(size_bytes)


@dataclass
class _DummyVisibilityMain:
    plot_info_output: _DummyVisibilityPanel = field(default_factory=_DummyVisibilityPanel)
    selection_info_toggle_button: _DummyVisibilityButton = field(default_factory=_DummyVisibilityButton)
    _selection_info_visible: bool = True
    _selection_info_expanded_from_width: int | None = None
    width_value: int = 1000
    height_value: int = 700

    def __post_init__(self) -> None:
        self.plot_view_controller = types.SimpleNamespace(
            adjust_window_width_for_info_panel=lambda _visible: None,
        )

    def style(self) -> _DummyStyle:
        return _DummyStyle()

    def width(self) -> int:
        return self.width_value

    def height(self) -> int:
        return self.height_value

    def _update_selection_info_toggle_button(self) -> None:
        CFVCore._update_selection_info_toggle_button(self)

    def _set_selection_info_panel_visible(self, visible: bool) -> None:
        CFVCore._set_selection_info_panel_visible(self, visible)


@dataclass
class _DummyStartupVisibilityPanel:
    hidden: bool = False

    def isVisible(self) -> bool:
        # Simulate child widget before top-level show(): effectively not visible.
        return False

    def isHidden(self) -> bool:
        return self.hidden


@dataclass
class _DummyResetVisibilityMain:
    _plot_request_in_flight: bool = True
    _plot_request_expects_image: bool = True
    _suppress_stale_error_status: bool = False
    _selection_info_visible: bool = False
    panel_visible_calls: list[bool] = field(default_factory=list)
    button_sync_calls: int = 0
    loading_calls: list[bool] = field(default_factory=list)
    canvas_messages: list[str] = field(default_factory=list)
    status_messages: list[str] = field(default_factory=list)

    def _set_selection_info_panel_visible(self, visible: bool) -> None:
        self._selection_info_visible = visible
        self.panel_visible_calls.append(visible)

    def _update_selection_info_toggle_button(self) -> None:
        self.button_sync_calls += 1

    def _set_plot_loading(self, is_loading: bool, message: str = "Rendering plot...") -> None:
        _ = message
        self.loading_calls.append(is_loading)

    def _clear_plot_canvas(self, message: str = "Plot unavailable") -> None:
        self.canvas_messages.append(message)

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        _ = is_error
        self.status_messages.append(message)


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


def test_clear_loaded_data_views_resets_field_slider_plot_and_details() -> None:
    dummy = _DummyClearLoadedDataMain()

    CFVCore._clear_loaded_data_views(dummy)

    assert dummy.current_file_path is None
    assert dummy.current_selection_info_text == "No selection info available."
    assert dummy.plot_info_output.text == "No selection info available."
    assert dummy.field_hints == ["Open a file to see fields"]
    assert dummy.built_slider_payloads == [{}]
    assert dummy.panel_visible_calls == [True]
    assert dummy.button_sync_calls == 1
    assert dummy.loading_calls == [False]
    assert dummy.canvas_messages == ["Waiting for data..."]
    assert dummy.titles == ["xconv2 (test)"]


def test_cache_summary_text_reports_config_and_usage() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        (cache_dir / "a.bin").write_bytes(b"1234")
        (cache_dir / "sub").mkdir()
        (cache_dir / "sub" / "b.bin").write_bytes(b"12")

        host = _DummyCacheManagerHost(
            _settings={
                "last_remote_configuration": {
                    "cache_blocksize_mb": 2,
                    "cache_ram_buffer_mb": 1024,
                    "cache_strategy": "Block",
                    "disk_mode": "Blocks",
                    "disk_location": str(cache_dir),
                    "disk_limit_gb": 10,
                    "disk_expiry": "7 days",
                }
            },
            _remote_session_id="session-1",
        )

        summary = CFVCore._cache_summary_text(host)

        assert "Active remote session: yes" in summary
        assert "Strategy: Block" in summary
        assert f"Location: {cache_dir}" in summary
        assert "Usage: 6 B across 2 files" in summary
        assert "Expiry: 7 days" in summary


def test_flush_configured_disk_cache_clears_directory_and_releases_remote(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        (cache_dir / "cache").mkdir()
        (cache_dir / "cache" / "entry.bin").write_bytes(b"123")

        host = _DummyCacheManagerHost(
            _settings={
                "last_remote_configuration": {
                    "disk_location": str(cache_dir),
                }
            },
            _remote_session_id="session-1",
        )

        monkeypatch.setattr(
            "xconv2.core_window.QMessageBox.question",
            lambda *args, **kwargs: QMessageBox.Yes,
        )

        ok = CFVCore._flush_configured_disk_cache(host)

        assert ok is True
        assert host.released == 1
        assert list(cache_dir.iterdir()) == []
        assert host.status_messages == [(f"Flushed cache: {cache_dir}", False)]


def test_prune_disk_cache_removes_expired_and_updates_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        old_file = cache_dir / "old.bin"
        new_file = cache_dir / "new.bin"
        old_file.write_bytes(b"1234")
        new_file.write_bytes(b"12")
        metadata = {
            "old": {"fn": "old.bin", "blocks": True, "time": 0},
            "new": {"fn": "new.bin", "blocks": True, "time": 0},
        }
        (cache_dir / "cache").write_text(__import__("json").dumps(metadata), encoding="utf-8")

        old_time = 1
        new_time = __import__("time").time()
        __import__("os").utime(old_file, (old_time, old_time))
        __import__("os").utime(new_file, (new_time, new_time))

        summary = prune_disk_cache(cache_dir, expiry_seconds=60 * 60)

        assert summary["removed_files"] == 1
        assert old_file.exists() is False
        assert new_file.exists() is True
        saved = __import__("json").loads((cache_dir / "cache").read_text(encoding="utf-8"))
        assert set(saved) == {"new"}


def test_prune_configured_disk_cache_releases_remote_and_reports(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        (cache_dir / "entry.bin").write_bytes(b"123")
        host = _DummyCacheManagerHost(
            _settings={
                "last_remote_configuration": {
                    "disk_location": str(cache_dir),
                    "disk_limit_gb": 0,
                    "disk_expiry": "Never",
                }
            },
            _remote_session_id="session-1",
        )

        monkeypatch.setattr("xconv2.core_window.prune_disk_cache", lambda *args, **kwargs: {"removed_files": 1, "removed_bytes": 3, "total_bytes": 0, "total_files": 0})

        ok = CFVCore._prune_configured_disk_cache(host)

        assert ok is True
        assert host.released == 1
        assert host.status_messages == [(f"Pruned cache: removed 1 files from {cache_dir}", False)]


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


def test_request_plot_options_shows_lineplot_dialog_when_lineplot_selected() -> None:
    dummy = _DummyPlotOptionsMain(
        _context=(
            {"time": (1, 2)},
            {},
            "lineplot",
        )
    )

    CFVMain._request_plot_options(dummy)

    assert dummy.lineplot_dialog_calls == 1
    assert dummy.sent_tasks == []
    assert dummy.status_messages == []


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


def test_toggle_selection_info_panel_updates_visibility_and_button_state() -> None:
    dummy = _DummyVisibilityMain()

    CFVCore._toggle_selection_info_panel(dummy)

    assert dummy.plot_info_output.isVisible() is False
    assert dummy._selection_info_visible is False
    assert dummy.selection_info_toggle_button.icon == QStyle.SP_TitleBarUnshadeButton
    assert dummy.selection_info_toggle_button.tooltip == "Show field details"
    assert dummy.selection_info_toggle_button.status_tip == "Show field details"

    CFVCore._toggle_selection_info_panel(dummy)

    assert dummy.plot_info_output.isVisible() is True
    assert dummy._selection_info_visible is True
    assert dummy.selection_info_toggle_button.icon == QStyle.SP_TitleBarShadeButton
    assert dummy.selection_info_toggle_button.tooltip == "Hide field details"
    assert dummy.selection_info_toggle_button.status_tip == "Hide field details"


def test_toggle_selection_info_panel_stores_width_before_hiding() -> None:
    dummy = _DummyVisibilityMain(width_value=1180)

    CFVCore._toggle_selection_info_panel(dummy)

    assert dummy._selection_info_expanded_from_width == 1180


def test_compute_target_window_width_expands_when_plot_is_height_limited() -> None:
    target_width = PlotViewController._compute_target_window_width(
        current_window_width=1000,
        current_plot_width=700,
        current_plot_height=900,
        pixmap_width=1200,
        pixmap_height=800,
        max_window_width=1600,
        min_window_width=640,
    )

    assert target_width == 1600


def test_compute_target_window_width_expands_without_hitting_screen_cap() -> None:
    target_width = PlotViewController._compute_target_window_width(
        current_window_width=1000,
        current_plot_width=700,
        current_plot_height=800,
        pixmap_width=1000,
        pixmap_height=800,
        max_window_width=1600,
        min_window_width=640,
    )

    assert target_width == 1300


def test_compute_target_window_width_keeps_width_when_change_is_tiny() -> None:
    target_width = PlotViewController._compute_target_window_width(
        current_window_width=1000,
        current_plot_width=700,
        current_plot_height=474,
        pixmap_width=1200,
        pixmap_height=800,
        max_window_width=1600,
        min_window_width=640,
    )

    assert target_width == 1000


def test_compute_target_window_width_shrinks_when_plot_is_too_wide_for_height() -> None:
    target_width = PlotViewController._compute_target_window_width(
        current_window_width=1500,
        current_plot_width=1100,
        current_plot_height=600,
        pixmap_width=800,
        pixmap_height=800,
        max_window_width=1800,
        min_window_width=640,
    )

    assert target_width == 1000


def test_update_toggle_button_uses_hidden_state_not_effective_visibility() -> None:
    dummy = _DummyVisibilityMain()
    dummy.plot_info_output = _DummyStartupVisibilityPanel(hidden=False)

    CFVCore._update_selection_info_toggle_button(dummy)

    assert dummy._selection_info_visible is True
    assert dummy.selection_info_toggle_button.icon == QStyle.SP_TitleBarShadeButton
    assert dummy.selection_info_toggle_button.tooltip == "Hide field details"


def test_reset_ui_for_new_field_selection_reveals_details_panel() -> None:
    dummy = _DummyResetVisibilityMain()

    CFVMain._reset_ui_for_new_field_selection(dummy)

    assert dummy._selection_info_visible is True
    assert dummy.panel_visible_calls[-1] is True
    assert dummy.button_sync_calls == 1


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


def test_handle_worker_output_task_complete_includes_elapsed(monkeypatch) -> None:
    dummy = _DummyStaleErrorMain()
    dummy._pending_worker_task_starts = deque([10.0])
    dummy.worker = _FakeWorker(["STATUS:Task Complete\n"])

    monkeypatch.setattr("xconv2.main_window.time.monotonic", lambda: 12.5)

    CFVMain.handle_worker_output(dummy)

    assert dummy.shown_statuses == [("Task Complete (2.50s)", False)]
    assert list(dummy._pending_worker_task_starts) == []
