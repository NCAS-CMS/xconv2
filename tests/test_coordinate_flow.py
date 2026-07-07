from __future__ import annotations

import base64
from collections import deque
import tempfile
import pickle
from dataclasses import dataclass, field
import types
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMessageBox, QStyle

from xconv2.cache_utils import prune_disk_cache
from xconv2.main_window import CFVMain
from xconv2.core_window import CFVCore
from xconv2.ui.selection_controller import SelectionController
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
        self.signal_blocks: list[bool] = []

    def value(self) -> tuple[int, int]:
        return self._bounds

    def setValue(self, bounds: tuple[int, int]) -> None:
        self._bounds = bounds

    def blockSignals(self, state: bool) -> None:
        self.signal_blocks.append(state)


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


@dataclass(eq=False)
class _DummySelectedItem:
    name: str


@dataclass
class _DummyContextFieldListWidget:
    selected: list[object] = field(default_factory=list)
    current: object | None = None

    def selectedItems(self) -> list[object]:
        return list(self.selected)

    def currentItem(self) -> object | None:
        return self.current


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
    vector_dialog_calls: list[int] = field(default_factory=list)
    sent_tasks: list[str] = field(default_factory=list)
    status_messages: list[str] = field(default_factory=list)
    selected_field_index: int | None = 0

    def _build_plot_context(self):
        return self._context

    def _show_lineplot_options_dialog(self) -> None:
        self.lineplot_dialog_calls += 1

    def _selected_field_index_for_operation(self, _operation: str) -> int | None:
        return self.selected_field_index

    def _show_vector_options_dialog(self, field_index: int) -> None:
        self.vector_dialog_calls.append(field_index)

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


class _FakeLayoutItem:
    def __init__(self, widget: object | None = None, layout: object | None = None) -> None:
        self._widget = widget
        self._layout = layout

    def widget(self):
        return self._widget

    def layout(self):
        return self._layout


class _FakeLayout:
    def __init__(self, items: list[_FakeLayoutItem]) -> None:
        self._items = list(items)

    def count(self) -> int:
        return len(self._items)

    def takeAt(self, index: int):
        if not self._items:
            return None
        if index != 0:
            raise AssertionError("test fake expects takeAt(0)")
        return self._items.pop(0)


class _FakeWidget:
    def __init__(self) -> None:
        self.deleted = False
        self.parent = object()
        self.hidden = False

    def hide(self) -> None:
        self.hidden = True

    def setParent(self, parent: object | None) -> None:
        self.parent = parent

    def deleteLater(self) -> None:
        self.deleted = True


@dataclass
class _DummyVisibilityButton:
    icon: object | None = None
    text: str = ""
    tooltip: str = ""
    status_tip: str = ""

    def setIcon(self, icon: object) -> None:
        self.icon = icon

    def setText(self, text: str) -> None:
        self.text = text

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

    def _do_flush_disk_at(self, location: Path) -> bool:
        return CFVCore._do_flush_disk_at(self, location)

    def _active_cache_settings(self) -> dict[str, object]:
        return CFVCore._active_cache_settings(self)

    def _disk_cache_usage(self, location: Path) -> tuple[int, int]:
        return CFVCore._disk_cache_usage(self, location)

    def _format_storage_size(self, size_bytes: int) -> str:
        return CFVCore._format_storage_size(size_bytes)


@dataclass
class _DummyVisibilityMain:
    selection_info_toggle_button: _DummyVisibilityButton = field(default_factory=_DummyVisibilityButton)
    _selection_info_visible: bool = False
    _selection_info_expanded_from_width: int | None = None
    opened_dialogs: int = 0
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

    def _open_selection_info_dialog(self) -> None:
        self.opened_dialogs += 1
        self._selection_info_visible = True


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


def test_clear_sidebar_layout_drains_items() -> None:
    fake_widget = _FakeWidget()
    fake_layout = _FakeLayout([_FakeLayoutItem(widget=fake_widget)])
    host = types.SimpleNamespace(sidebar=fake_layout)
    controller = SelectionController.__new__(SelectionController)
    controller.host = host

    SelectionController._clear_sidebar_layout(controller)

    assert fake_layout.count() == 0
    assert fake_widget.hidden is True
    assert fake_widget.parent is None
    assert fake_widget.deleted is True


def test_cache_summary_text_reports_config_and_usage() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        (cache_dir / "a.bin").write_bytes(b"1234")
        (cache_dir / "sub").mkdir()
        (cache_dir / "sub" / "b.bin").write_bytes(b"12")

        host = _DummyCacheManagerHost(
            _settings={
                "last_remote_configuration": {
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


def test_request_plot_options_shows_vector_dialog_when_vector_selected() -> None:
    dummy = _DummyPlotOptionsMain(
        _context=(
            {"lat": (-10, 10), "lon": (0, 20)},
            {},
            "vector",
        ),
        selected_field_index=3,
    )

    CFVMain._request_plot_options(dummy)

    assert dummy.vector_dialog_calls == [3]
    assert dummy.lineplot_dialog_calls == 0
    assert dummy.sent_tasks == []


def test_request_plot_task_delegates_to_plot_ops_with_vector_builder(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_request_plot_task(host, **kwargs):
        captured["host"] = host
        captured.update(kwargs)

    monkeypatch.setattr("xconv2.main_window._plot_ops.request_plot_task", _fake_request_plot_task)

    host = CFVMain.__new__(CFVMain)
    CFVMain._request_plot_task(
        host,
        save_code_path="/tmp/task.py",
        save_plot_path="/tmp/plot.png",
        save_data_path="/tmp/sel.nc",
        emit_image_override=False,
    )

    assert captured["host"] is host
    assert captured["save_code_path"] == "/tmp/task.py"
    assert captured["save_plot_path"] == "/tmp/plot.png"
    assert captured["save_data_path"] == "/tmp/sel.nc"
    assert captured["emit_image_override"] is False
    assert callable(captured["build_vector_overplot_command_fn"])
    assert captured["build_vector_overplot_command_fn"].__name__ == "build_vector_overplot_command"


def test_plot_ops_request_plot_task_uses_vector_overplot_builder_when_context_present() -> None:
    class _DummyListWidget:
        def item(self, _index: int):
            return None

    class _DummyWorker:
        def processId(self) -> int:
            return 999

    class _DummyHost:
        def __init__(self) -> None:
            self.field_list_widget = _DummyListWidget()
            self.worker = _DummyWorker()
            self.plot_options_by_kind = {"vector": {"v_field_index": 7, "stride": 2}}
            self.selected_plot_action = "overplot"
            self._last_contour_plot_context = {
                "field_index": 1,
                "selections": {"lat": (-10, 10)},
                "collapse_by_coord": {},
                "plot_options": {"title": "base"},
            }
            self._plot_request_in_flight = False
            self._plot_request_expects_image = False
            self._suppress_stale_error_status = False
            self.status_messages: list[tuple[str, bool]] = []
            self.loading_messages: list[tuple[bool, str]] = []
            self.sent_tasks: list[tuple[str, str | None, bool]] = []

        def _build_plot_context(self):
            return ({"lat": (-5, 5)}, {}, "vector")

        def _selected_field_index_for_operation(self, _operation: str) -> int | None:
            return 4

        def _field_identity_from_item(self, _item) -> str:
            return "field-4"

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

        def _show_vector_options_dialog(self, _field_index: int) -> None:
            raise AssertionError("Vector options dialog should not be needed when v_field_index is set")

        def _set_plot_loading(self, is_loading: bool, message: str = "") -> None:
            self.loading_messages.append((is_loading, message))

        def _send_worker_task(self, code: str, save_code_path: str | None = None, emit_image: bool = True) -> None:
            self.sent_tasks.append((code, save_code_path, emit_image))

    host = _DummyHost()

    def _fake_save_data_from_selection(_selections, _collapse, _path) -> str:
        return "SAVE_DATA_COMMAND"

    def _fake_plot_from_selection(*_args, **_kwargs) -> str:
        return "PLOT_FROM_SELECTION_COMMAND"

    def _fake_build_vector_overplot_command(**kwargs) -> str:
        assert kwargs["contour_field_index"] == 1
        assert kwargs["vector_field_index"] == 4
        assert kwargs["vector_options"]["v_field_index"] == 7
        return "VECTOR_OVERPLOT_COMMAND"

    from xconv2.main_window_components import plot_ops

    plot_ops.request_plot_task(
        host,
        save_code_path=None,
        save_plot_path=None,
        save_data_path=None,
        emit_image_override=None,
        save_data_from_selection_fn=_fake_save_data_from_selection,
        plot_from_selection_fn=_fake_plot_from_selection,
        build_vector_overplot_command_fn=_fake_build_vector_overplot_command,
    )

    assert host.sent_tasks
    code, save_code_path, emit_image = host.sent_tasks[-1]
    assert "VECTOR_OVERPLOT_COMMAND" in code
    assert "PLOT_FROM_SELECTION_COMMAND" not in code
    assert "_cfview_field_index = 4" in code
    assert save_code_path is None
    assert emit_image is True


def test_on_field_clicked_resets_ui_then_requests_coordinates() -> None:
    """Field click should flow through core handling, reset UI, then request coordinates."""
    window = CFVMain.__new__(CFVMain)
    field_controller = _DummyFieldMetadataController()
    window.field_metadata_controller = field_controller
    window.field_list_widget = _DummyFieldListWidget(index_to_return=7)
    window._allow_initial_autoplot_on_next_field_click = True

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
    assert window._pending_initial_autoplot_field_index == 7
    assert window._allow_initial_autoplot_on_next_field_click is False


def test_on_field_clicked_after_first_click_does_not_set_initial_autoplot_pending() -> None:
    window = CFVMain.__new__(CFVMain)
    field_controller = _DummyFieldMetadataController()
    window.field_metadata_controller = field_controller
    window.field_list_widget = _DummyFieldListWidget(index_to_return=3)
    window._allow_initial_autoplot_on_next_field_click = False

    call_order: list[tuple[str, object]] = []
    window._reset_ui_for_new_field_selection = types.MethodType(
        lambda self: call_order.append(("reset", None)),
        window,
    )
    window._request_coordinates_for_field = types.MethodType(
        lambda self, index, show_status=True: call_order.append(("request", (index, show_status))),
        window,
    )

    CFVMain.on_field_clicked(window, object())

    assert field_controller.clicked_items
    assert call_order == [
        ("reset", None),
        ("request", (3, False)),
    ]
    assert window._pending_initial_autoplot_field_index is None


def test_auto_plot_initial_field_selection_collapses_extra_dims_and_requests_contour() -> None:
    class _DummyFieldList:
        def selectedItems(self):
            return [object()]

        def row(self, _item: object) -> int:
            return 4

    class _DummyPlotViewController:
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], str]] = []

        def set_plot_type_options(self, kinds: list[str], selected_kind: str | None) -> None:
            if selected_kind is None:
                raise AssertionError("selected kind should not be None")
            self.calls.append((list(kinds), selected_kind))

    time_slider = _FakeRangeSlider((0, 2))
    lat_slider = _FakeRangeSlider((0, 3))
    lon_slider = _FakeRangeSlider((0, 4))

    host = types.SimpleNamespace(
        _pending_initial_autoplot_field_index=4,
        field_list_widget=_DummyFieldList(),
        controls={
            "time": {"values": [0, 1, 2], "range_slider": time_slider},
            "latitude": {"values": [-30, -10, 10, 30], "range_slider": lat_slider},
            "longitude": {"values": [0, 90, 180, 270, 360], "range_slider": lon_slider},
        },
        available_plot_kinds=["lineplot", "contour", "vector"],
        selected_plot_kind=None,
        plot_view_controller=_DummyPlotViewController(),
        plot_button=types.SimpleNamespace(isEnabled=lambda: True),
    )
    updated_labels: list[str] = []
    host._update_range_labels = lambda name: updated_labels.append(name)
    host._refresh_plot_summary = lambda: None
    requested: list[str] = []
    host._request_plot_update = lambda: requested.append("plot")

    CFVMain._auto_plot_initial_field_selection(host)

    assert host._pending_initial_autoplot_field_index is None
    assert time_slider.value() == (0, 0)
    assert lat_slider.value() == (0, 3)
    assert lon_slider.value() == (0, 4)
    assert updated_labels == ["time"]
    assert host.selected_plot_kind == "contour"
    assert host.plot_view_controller.calls == [(["lineplot", "contour", "vector"], "contour")]
    assert requested == ["plot"]


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


def test_toggle_selection_info_panel_opens_popup_and_updates_button_state() -> None:
    dummy = _DummyVisibilityMain()

    CFVCore._toggle_selection_info_panel(dummy)

    assert dummy.opened_dialogs == 1
    assert dummy._selection_info_visible is True
    assert isinstance(dummy.selection_info_toggle_button.icon, QIcon)
    assert dummy.selection_info_toggle_button.tooltip == "Open field details popup"
    assert dummy.selection_info_toggle_button.status_tip == "Open field details popup"

    CFVCore._toggle_selection_info_panel(dummy)

    assert dummy.opened_dialogs == 2


def test_update_toggle_button_uses_popup_affordance() -> None:
    dummy = _DummyVisibilityMain()

    CFVCore._update_selection_info_toggle_button(dummy)

    assert isinstance(dummy.selection_info_toggle_button.icon, QIcon)
    assert dummy.selection_info_toggle_button.tooltip == "Open field details popup"


def test_selection_info_dialog_title_uses_origin_file_name() -> None:
    class _DummyItem:
        def data(self, role: int):
            if role == Qt.UserRole + 5:
                return False
            if role == Qt.UserRole + 2:
                return "/tmp/example_field.nc"
            return None

    class _DummyListWidget:
        def currentItem(self):
            return _DummyItem()

    dummy = types.SimpleNamespace(field_list_widget=_DummyListWidget())

    title = CFVCore._selection_info_dialog_title(dummy)

    assert title == "Origin file: example_field.nc"


def test_selection_info_dialog_title_uses_derived_label_for_unsaved_fields() -> None:
    class _DummyItem:
        def data(self, role: int):
            if role == Qt.UserRole + 5:
                return True
            if role == Qt.UserRole + 2:
                return "/tmp/derived.nc"
            return None

    class _DummyListWidget:
        def currentItem(self):
            return _DummyItem()

    dummy = types.SimpleNamespace(field_list_widget=_DummyListWidget())

    title = CFVCore._selection_info_dialog_title(dummy)

    assert title == "Derived, not saved"


def test_selection_info_dialog_key_combines_title_and_detail_text() -> None:
    key = CFVCore._selection_info_dialog_key("Origin file: a.nc", "field detail")

    assert key == "Origin file: a.nc\nfield detail"


def test_show_selected_field_info_from_context_menu_uses_current_item_when_selected() -> None:
    current = _DummySelectedItem("current")
    other = _DummySelectedItem("other")
    clicked: list[object] = []
    opened: list[str] = []
    updated: list[str] = []

    host = types.SimpleNamespace(
        field_list_widget=_DummyContextFieldListWidget(selected=[other, current], current=current),
        field_metadata_controller=types.SimpleNamespace(on_field_clicked=lambda item: clicked.append(item)),
        _open_selection_info_dialog=lambda: opened.append("open"),
        _update_selection_info_toggle_button=lambda: updated.append("update"),
    )

    CFVCore._show_selected_field_info_from_context_menu(host)

    assert clicked == [current]
    assert opened == ["open"]
    assert updated == ["update"]


def test_show_selected_field_info_from_context_menu_falls_back_to_first_selected() -> None:
    first = _DummySelectedItem("first")
    second = _DummySelectedItem("second")
    not_selected = _DummySelectedItem("not-selected")
    clicked: list[object] = []
    opened: list[str] = []
    updated: list[str] = []

    host = types.SimpleNamespace(
        field_list_widget=_DummyContextFieldListWidget(selected=[first, second], current=not_selected),
        field_metadata_controller=types.SimpleNamespace(on_field_clicked=lambda item: clicked.append(item)),
        _open_selection_info_dialog=lambda: opened.append("open"),
        _update_selection_info_toggle_button=lambda: updated.append("update"),
    )

    CFVCore._show_selected_field_info_from_context_menu(host)

    assert clicked == [first]
    assert opened == ["open"]
    assert updated == ["update"]


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


def test_refresh_plot_summary_3d_requests_animation_action_mode() -> None:
    class _Slider:
        def value(self) -> tuple[int, int]:
            return (0, 3)

    class _Label:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, text: str) -> None:
            self.text = text

    class _WidgetToggle:
        def __init__(self) -> None:
            self.visible = False
            self.enabled = False

        def show(self) -> None:
            self.visible = True

        def hide(self) -> None:
            self.visible = False

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    class _Recorder:
        def __init__(self, host) -> None:
            self.host = host
            self.plot_type_calls: list[tuple[list[str], str | None, int | None]] = []
            self.plot_action_calls: list[tuple[bool, bool, int | None]] = []

        def set_plot_type_options(self, options: list[str], selected: str | None, varying_dims: int | None = None) -> None:
            self.plot_type_calls.append((list(options), selected, varying_dims))

        def set_plot_action_options(
            self,
            has_existing_plot: bool,
            supports_animation: bool = False,
            varying_dims: int | None = None,
        ) -> None:
            self.plot_action_calls.append((has_existing_plot, supports_animation, varying_dims))
            if varying_dims == 3:
                self.host.selected_plot_action = "animation"

    host = types.SimpleNamespace(
        controls={
            "time": {"range_slider": _Slider()},
            "level": {"range_slider": _Slider()},
            "member": {"range_slider": _Slider()},
        },
        selected_collapse_methods={},
        plot_summary_label=_Label(),
        plot_info_button=_WidgetToggle(),
        plot_button=_WidgetToggle(),
        options_button=_WidgetToggle(),
        save_target_combo=_WidgetToggle(),
        save_go_button=_WidgetToggle(),
        save_code_button=None,
        save_plot_button=None,
        available_plot_kinds=[],
        selected_plot_kind=None,
        selected_plot_action="plot",
        last_varying_dims=2,
        _plot_pixmap_original=None,
    )
    recorder = _Recorder(host)
    host.plot_view_controller = recorder

    controller = SelectionController(host)
    controller.refresh_plot_summary()

    assert host.selected_plot_action == "animation"
    assert recorder.plot_type_calls[-1] == (["contour"], "contour", 3)
    assert recorder.plot_action_calls[-1] == (False, True, 3)
    assert "Selection Dimensions: 3D" in host.plot_summary_label.text
    assert "Change dimensionality for other options" in host.plot_summary_label.text


def test_refresh_plot_summary_2d_disables_animation_support_flag() -> None:
    class _Slider:
        def __init__(self, bounds: tuple[int, int]) -> None:
            self._bounds = bounds

        def value(self) -> tuple[int, int]:
            return self._bounds

    class _Label:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, text: str) -> None:
            self.text = text

    class _WidgetToggle:
        def __init__(self) -> None:
            self.enabled = False

        def show(self) -> None:
            return

        def hide(self) -> None:
            return

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    class _Recorder:
        def __init__(self) -> None:
            self.plot_action_calls: list[tuple[bool, bool, int | None]] = []

        def set_plot_type_options(self, options: list[str], selected: str | None, varying_dims: int | None = None) -> None:
            _ = (options, selected, varying_dims)

        def set_plot_action_options(
            self,
            has_existing_plot: bool,
            supports_animation: bool = False,
            varying_dims: int | None = None,
        ) -> None:
            self.plot_action_calls.append((has_existing_plot, supports_animation, varying_dims))

    host = types.SimpleNamespace(
        controls={
            "time": {"range_slider": _Slider((0, 3))},
            "lat": {"range_slider": _Slider((0, 2))},
            "lon": {"range_slider": _Slider((1, 1))},
        },
        selected_collapse_methods={},
        plot_summary_label=_Label(),
        plot_info_button=_WidgetToggle(),
        plot_button=_WidgetToggle(),
        options_button=_WidgetToggle(),
        save_target_combo=_WidgetToggle(),
        save_go_button=_WidgetToggle(),
        save_code_button=None,
        save_plot_button=None,
        available_plot_kinds=[],
        selected_plot_kind=None,
        selected_plot_action="plot",
        last_varying_dims=None,
        _plot_pixmap_original=None,
        plot_view_controller=_Recorder(),
    )

    controller = SelectionController(host)
    controller.refresh_plot_summary()

    assert host.plot_view_controller.plot_action_calls[-1] == (False, False, 2)


def test_plot_ops_animation_go_routes_to_worker_animation_task() -> None:
    class _DummyListWidget:
        def item(self, _index: int):
            return None

    class _DummyWorker:
        def processId(self) -> int:
            return 999

    class _DummyHost:
        def __init__(self) -> None:
            self.field_list_widget = _DummyListWidget()
            self.worker = _DummyWorker()
            self.plot_options_by_kind = {"contour": {}}
            self.selected_plot_action = "animation"
            self._plot_request_in_flight = False
            self._plot_request_expects_image = False
            self._suppress_stale_error_status = False
            self.sent_tasks: list[tuple[str, str | None, bool, bool]] = []
            self.status_messages: list[tuple[str, bool]] = []

        def _build_plot_context(self):
            return ({"time": (0, 10), "lat": (-10, 10), "lon": (0, 20)}, {}, "contour")

        def _selected_field_index_for_operation(self, _operation: str) -> int | None:
            return 2

        def _field_identity_from_item(self, _item) -> str:
            return "field-2"

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

        def _show_vector_options_dialog(self, _field_index: int) -> None:
            raise AssertionError("Vector options should not be requested")

        def _set_plot_loading(self, _is_loading: bool, message: str = "") -> None:
            _ = message

        def _contour_title_fontsize(self) -> float:
            return 10.0

        def _page_title_fontsize(self) -> float:
            return 10.0

        def _annotation_fontsize(self) -> float:
            return 8.0

        def _send_worker_task(
            self,
            code: str,
            save_code_path: str | None = None,
            emit_image: bool = True,
            animation_enabled: bool = False,
        ) -> None:
            self.sent_tasks.append((code, save_code_path, emit_image, animation_enabled))

    host = _DummyHost()

    from xconv2.main_window_components import plot_ops

    plot_ops.request_plot_task(
        host,
        save_code_path=None,
        save_plot_path=None,
        save_data_path=None,
        emit_image_override=None,
        save_data_from_selection_fn=lambda *_: "SAVE_DATA",
        plot_from_selection_fn=lambda *_args, **_kwargs: "PLOT_CODE",
        build_vector_overplot_command_fn=lambda **_kwargs: "VECTOR_OVERPLOT",
    )

    assert host.sent_tasks == [("_cfview_field_index = 2\nfld = f[2]\nPLOT_CODE", None, False, True)]


def test_animation_playback_respects_loop_and_fps_interval() -> None:
    class _FakeTimer:
        def __init__(self) -> None:
            self._active = False
            self.interval_ms = 0

        def setInterval(self, interval_ms: int) -> None:
            self.interval_ms = interval_ms

        def start(self) -> None:
            self._active = True

        def stop(self) -> None:
            self._active = False

        def isActive(self) -> bool:
            return self._active

    class _FakeButton:
        def __init__(self) -> None:
            self.text = ""
            self.enabled = False

        def setText(self, text: str) -> None:
            self.text = text

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    host = types.SimpleNamespace(
        _animation_session_controller=__import__("xconv2.animation_session", fromlist=["AnimationSessionController"]).AnimationSessionController(),
        _active_animation_request_id="req-1",
        _animation_playback_timer=_FakeTimer(),
        _animation_is_playing=False,
        selected_plot_action="animation",
        anim_play_pause_button=_FakeButton(),
        anim_stop_button=_FakeButton(),
        anim_export_button=_FakeButton(),
        plot_options_by_kind={"animation": {"loop_playback": True}},
    )
    displayed_frames: list[bytes] = []
    statuses: list[str] = []
    host.set_plot_image = lambda frame: displayed_frames.append(frame)
    host._show_status_message = lambda message, is_error=False: statuses.append(message)
    host._set_plot_loading = lambda *_args, **_kwargs: None
    host._current_animation_session = lambda: CFVMain._current_animation_session(host)
    host._current_animation_options = lambda: CFVMain._current_animation_options(host)
    host._resolved_animation_fps = lambda session: CFVMain._resolved_animation_fps(host, session)

    session = host._animation_session_controller.create_session("req-1", "sess-1")
    session.mark_started(total_frames=2, fps_hint=8.0, title_template="demo")
    session.add_frame(b"frame-0")
    session.add_frame(b"frame-1")
    session.mark_completed()

    CFVMain._on_animation_play_pause(host)
    assert host._animation_playback_timer.isActive() is True
    assert host._animation_playback_timer.interval_ms == 125
    assert "8.0 fps" in statuses[-1]

    CFVMain._advance_animation_playback(host)
    CFVMain._advance_animation_playback(host)
    CFVMain._advance_animation_playback(host)

    assert displayed_frames == [b"frame-0", b"frame-1", b"frame-0"]
    assert host._animation_playback_timer.isActive() is True


def test_animation_playback_prefers_options_fps_hint_over_session_hint() -> None:
    class _FakeTimer:
        def __init__(self) -> None:
            self._active = False
            self.interval_ms = 0

        def setInterval(self, interval_ms: int) -> None:
            self.interval_ms = interval_ms

        def start(self) -> None:
            self._active = True

        def stop(self) -> None:
            self._active = False

        def isActive(self) -> bool:
            return self._active

    class _FakeButton:
        def __init__(self) -> None:
            self.text = ""
            self.enabled = False

        def setText(self, text: str) -> None:
            self.text = text

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    host = types.SimpleNamespace(
        _animation_session_controller=__import__("xconv2.animation_session", fromlist=["AnimationSessionController"]).AnimationSessionController(),
        _active_animation_request_id="req-1",
        _animation_playback_timer=_FakeTimer(),
        _animation_is_playing=False,
        selected_plot_action="animation",
        anim_play_pause_button=_FakeButton(),
        anim_stop_button=_FakeButton(),
        anim_export_button=_FakeButton(),
        plot_options_by_kind={"animation": {"loop_playback": True, "fps_hint": 4}},
    )
    statuses: list[str] = []
    host.set_plot_image = lambda _frame: None
    host._show_status_message = lambda message, is_error=False: statuses.append(message)
    host._set_plot_loading = lambda *_args, **_kwargs: None
    host._current_animation_session = lambda: CFVMain._current_animation_session(host)
    host._current_animation_options = lambda: CFVMain._current_animation_options(host)
    host._resolved_animation_fps = lambda session: CFVMain._resolved_animation_fps(host, session)

    session = host._animation_session_controller.create_session("req-1", "sess-1")
    session.mark_started(total_frames=2, fps_hint=8.0, title_template="demo")
    session.add_frame(b"frame-0")
    session.mark_completed()

    CFVMain._on_animation_play_pause(host)

    assert host._animation_playback_timer.isActive() is True
    assert host._animation_playback_timer.interval_ms == 250
    assert "4.0 fps" in statuses[-1]


def test_animation_export_writes_frame_sequence(tmp_path: Path, monkeypatch) -> None:
    class _FakeSession:
        def __init__(self) -> None:
            self.frames = [b"png-a", b"png-b", b"png-c"]

    class _Host:
        def __init__(self) -> None:
            self.status_messages: list[tuple[str, bool]] = []
            self.last_save_values: list[tuple[str, str]] = []

        def _current_animation_session(self):
            return _FakeSession()

        def _default_plot_filename(self) -> str:
            return "field0"

        def _default_save_path(self, _key: str, filename: str) -> str:
            return str(tmp_path / filename)

        def _remember_last_save_dir(self, key: str, value: str) -> None:
            self.last_save_values.append((key, value))

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

    output_file = tmp_path / "demo_anim.png"
    monkeypatch.setattr(
        "xconv2.core_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(output_file), "PNG files (*.png)"),
    )

    host = _Host()
    CFVCore._on_animation_export(host)

    assert (tmp_path / "demo_anim_0001.png").read_bytes() == b"png-a"
    assert (tmp_path / "demo_anim_0002.png").read_bytes() == b"png-b"
    assert (tmp_path / "demo_anim_0003.png").read_bytes() == b"png-c"
    assert host.last_save_values[-1][0] == "last_save_plot_dir"
    assert host.status_messages[-1][1] is False
    assert "Saved 3 animation frame(s)" in host.status_messages[-1][0]


def test_animation_export_writes_animated_gif(tmp_path: Path, monkeypatch) -> None:
    class _FakeSession:
        def __init__(self) -> None:
            self.frames = [b"png-a", b"png-b"]
            self.fps_hint = 5.0

    class _Host:
        def __init__(self) -> None:
            self.status_messages: list[tuple[str, bool]] = []
            self.last_save_values: list[tuple[str, str]] = []

        def _current_animation_session(self):
            return _FakeSession()

        def _default_plot_filename(self) -> str:
            return "field0"

        def _default_save_path(self, _key: str, filename: str) -> str:
            return str(tmp_path / filename)

        def _remember_last_save_dir(self, key: str, value: str) -> None:
            self.last_save_values.append((key, value))

        def _show_status_message(self, message: str, is_error: bool = False) -> None:
            self.status_messages.append((message, is_error))

    output_file = tmp_path / "demo_anim.gif"
    calls: list[tuple[list[bytes], str, int, int]] = []

    monkeypatch.setattr(
        "xconv2.core_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(output_file), "Animated GIF (*.gif)"),
    )

    def _fake_save_gif(frame_bytes, destination, *, duration_ms, loop):
        calls.append((list(frame_bytes), destination, duration_ms, loop))

    monkeypatch.setattr("xconv2.core_window._save_gif_from_png_bytes", _fake_save_gif)

    host = _Host()
    CFVCore._on_animation_export(host)

    assert len(calls) == 1
    assert calls[0][0] == [b"png-a", b"png-b"]
    assert calls[0][1] == str(output_file)
    assert calls[0][2] == 200
    assert calls[0][3] == 0
    assert host.last_save_values[-1] == ("last_save_plot_dir", str(output_file))
    assert host.status_messages[-1][1] is False
    assert "Saved animated GIF" in host.status_messages[-1][0]
