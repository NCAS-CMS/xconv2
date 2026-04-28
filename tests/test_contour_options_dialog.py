from __future__ import annotations

from dataclasses import dataclass

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QWidget

from xconv2.ui.contour_options_controller import ContourOptionsController


@dataclass
class _FakeStatus:
    messages: list[str]

    def showMessage(self, message: str) -> None:
        self.messages.append(message)


class _FakeItem:
    def __init__(self, properties: str) -> None:
        self._properties = properties

    def data(self, _role: int) -> str:
        return self._properties


class _FakeFieldList:
    def __init__(self, item: object | None) -> None:
        self._item = item

    def currentItem(self) -> object | None:
        return self._item


class _FakeHost(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.plot_options_by_kind: dict[str, dict[str, object]] = {}
        self.current_file_path: str | None = None
        self.status = _FakeStatus(messages=[])
        self.field_list_widget = _FakeFieldList(_FakeItem("{}"))
        self.annotation_chooser_calls = 0

    def _parse_properties_dict(self, _raw_properties: object) -> dict[str, str]:
        return {"a": "1", "b": "2"}

    def _show_annotation_properties_chooser(self, properties, current_selected, max_selected=4):  # type: ignore[no-untyped-def]
        _ = (properties, current_selected, max_selected)
        self.annotation_chooser_calls += 1
        return []

    def _show_colour_scale_chooser(self, _current_scale: object) -> None:
        return None

    def _contour_title_fontsize(self) -> float:
        return 10.5

    def _page_title_fontsize(self) -> float:
        return 10.0

    def _annotation_fontsize(self) -> float:
        return 8.0

    def _request_plot_update(self) -> None:
        return



def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is not None:
        return app

    try:
        return QApplication([])
    except Exception as exc:  # pragma: no cover - environment specific
        pytest.skip(f"Qt application setup unavailable: {exc}")



def test_enter_in_contour_title_does_not_open_annotation_chooser(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _ensure_qapp()
    host = _FakeHost()
    controller = ContourOptionsController(host)

    def _fake_show(self: QDialog) -> None:
        edits = self.findChildren(QLineEdit)
        assert edits
        title_edit = edits[0]
        title_edit.setFocus()
        title_edit.setText("My title")

        QTest.keyClick(title_edit, Qt.Key_Return)
        app.processEvents()

        self.reject()

    monkeypatch.setattr(QDialog, "show", _fake_show)

    controller.show_contour_options_dialog(range_min=0.0, range_max=1.0, suggested_title="s")

    assert host.annotation_chooser_calls == 0
