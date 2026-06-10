from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QTableWidget,
    QWidget,
)

from xconv2.ui.field_metadata_controller import FieldMetadataController


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is not None:
        return app

    try:
        return QApplication([])
    except Exception as exc:  # pragma: no cover - environment specific
        pytest.skip(f"Qt application setup unavailable: {exc}")


class _DummyStatus:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def showMessage(self, message: str) -> None:
        self.messages.append(message)


class _DummyHost(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.field_list_widget = QListWidget(self)
        self.status = _DummyStatus()


def test_show_selection_properties_clicking_key_and_value_exposes_full_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()

    host = _DummyHost()
    controller = FieldMetadataController(host)

    long_key = "key_" + ("A" * 280)
    long_value = "value_" + ("B" * 520)
    item = QListWidgetItem("sample_field")
    item.setData(Qt.UserRole + 1, {long_key: long_value})
    host.field_list_widget.addItem(item)
    host.field_list_widget.setCurrentItem(item)

    dialogs: list[QDialog] = []

    def _fake_open(dialog: QDialog) -> None:
        dialogs.append(dialog)

    monkeypatch.setattr(QDialog, "open", _fake_open)
    controller.show_selection_properties()

    assert dialogs, "Properties dialog was not opened"
    table = dialogs[0].findChild(QTableWidget)
    assert table is not None

    shown: list[dict[str, str]] = []

    def _capture_full_text(*, parent: QWidget, title: str, content: str) -> None:
        _ = parent
        shown.append({"title": title, "content": content})

    monkeypatch.setattr(controller, "_show_full_property_text", _capture_full_text)

    key_item = table.item(0, 0)
    value_item = table.item(0, 1)
    assert key_item is not None
    assert value_item is not None

    table.itemClicked.emit(key_item)
    table.itemClicked.emit(value_item)

    assert shown == [
        {"title": "Property Key: sample_field", "content": long_key},
        {"title": "Property Value: sample_field", "content": long_value},
    ]


def test_show_full_property_text_opens_read_only_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()

    host = _DummyHost()
    controller = FieldMetadataController(host)

    dialogs: list[QDialog] = []

    def _fake_open(dialog: QDialog) -> None:
        dialogs.append(dialog)

    monkeypatch.setattr(QDialog, "open", _fake_open)

    controller._show_full_property_text(
        parent=host,
        title="Property Value: sample_field",
        content="line 1\nline 2\nline 3",
    )

    assert dialogs, "Full-text dialog was not opened"
    viewer = dialogs[0].findChild(QPlainTextEdit)
    assert viewer is not None
    assert viewer.isReadOnly() is True
    assert viewer.toPlainText() == "line 1\nline 2\nline 3"
