from __future__ import annotations

import inspect

import pytest
from PySide6.QtWidgets import QApplication

from xconv2.cf_interface.filtering import apply_moving_window_to_field, apply_window_to_field
from xconv2.ui.dialogs import FilterDialog


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is not None:
        return app

    try:
        return QApplication([])
    except Exception as exc:  # pragma: no cover - environment specific
        pytest.skip(f"Qt application setup unavailable: {exc}")


def test_filter_dialog_uses_filtering_docstrings() -> None:
    _ensure_qapp()
    dialog = FilterDialog(None, field_label="air_temperature", available_axes=["T", "Y", "X"])

    convolution_doc = inspect.getdoc(apply_window_to_field) or ""
    assert convolution_doc
    assert convolution_doc.splitlines()[0] in dialog._method_doc_label.text()
    assert "convolution updates relevant coordinate bounds" in dialog._method_behavior_label.text().lower()

    dialog._method_combo.setCurrentIndex(1)
    dialog._sync_method_ui()
    moving_doc = inspect.getdoc(apply_moving_window_to_field) or ""
    assert moving_doc
    assert moving_doc.splitlines()[0] in dialog._method_doc_label.text()
    assert "does not update coordinate bounds" in dialog._method_behavior_label.text().lower()


def test_filter_dialog_submit_returns_expected_config() -> None:
    _ensure_qapp()
    captured: list[dict[str, object]] = []
    dialog = FilterDialog(
        None,
        field_label="air_temperature",
        available_axes=["T", "Y", "X"],
        on_submit=lambda config: captured.append(config),
    )

    dialog._method_combo.setCurrentIndex(1)
    dialog._axis_combo.setCurrentText("Y")
    dialog._size_spin.setValue(7)
    dialog._moving_method_combo.setCurrentText("sum")
    dialog._moving_mode_combo.setCurrentText("wrap")
    dialog._weights_checkbox.setChecked(True)
    dialog._on_accept()

    assert captured == [
        {
            "method": "moving_window",
            "axis": "Y",
            "size": 7,
            "moving_method": "sum",
            "mode": "wrap",
            "weights": True,
        }
    ]
