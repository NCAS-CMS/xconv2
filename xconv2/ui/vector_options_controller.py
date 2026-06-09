from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from xconv2.core_window import CFVCore


class VectorOptionsController:
    """Encapsulate vector plot options dialog behavior."""

    def __init__(self, host: "CFVCore") -> None:
        self.host = host
        self._dialog: QDialog | None = None

    def show_vector_options_dialog(self, current_field_index: int) -> None:
        """Show vector options dialog and persist selected options."""
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.raise_()
            self._dialog.activateWindow()
            return

        existing = self.host.plot_options_by_kind.get("vector", {})

        field_count = self.host.field_list_widget.count()
        field_entries: list[tuple[int, str]] = []
        for i in range(field_count):
            item = self.host.field_list_widget.item(i)
            label = item.text() if item else f"Field {i}"
            field_entries.append((i, f"[{i}] {label}"))

        dialog = QDialog(self.host)
        self._dialog = dialog
        dialog.setWindowTitle("Vector Options")
        dialog.setWindowModality(Qt.NonModal)
        dialog.resize(560, 380)
        dialog.finished.connect(lambda _result: setattr(self, "_dialog", None))

        layout = QVBoxLayout(dialog)

        # --- Field Assignment Group ---
        fields_group = QGroupBox("Vector Field Assignment")
        fields_layout = QGridLayout(fields_group)
        fields_layout.setContentsMargins(9, 9, 9, 9)
        fields_layout.setHorizontalSpacing(12)
        fields_layout.setVerticalSpacing(6)

        u_label = QLabel("U component (X / eastward)")
        current_label = field_entries[current_field_index][1] if field_entries else f"[{current_field_index}] current field"
        u_value_label = QLabel(current_label)
        u_value_label.setStyleSheet("font-weight: 700;")
        u_value_label.setToolTip("The currently selected field is used as the U (eastward) component")

        v_label = QLabel("V component (Y / northward)")
        v_combo = QComboBox()
        for idx, label in field_entries:
            v_combo.addItem(label, userData=idx)

        saved_v_idx = existing.get("v_field_index")
        if isinstance(saved_v_idx, int) and 0 <= saved_v_idx < field_count:
            v_combo.setCurrentIndex(saved_v_idx)
        elif field_count > 1:
            default_v = 1 if current_field_index != 1 else 0
            v_combo.setCurrentIndex(default_v)

        fields_layout.addWidget(u_label, 0, 0)
        fields_layout.addWidget(u_value_label, 0, 1)
        fields_layout.addWidget(v_label, 1, 0)
        fields_layout.addWidget(v_combo, 1, 1)
        fields_layout.setColumnStretch(1, 1)

        # --- Vector Parameters Group ---
        params_group = QGroupBox("Vector Parameters")
        params_layout = QGridLayout(params_group)
        params_layout.setContentsMargins(9, 9, 9, 9)
        params_layout.setHorizontalSpacing(12)
        params_layout.setVerticalSpacing(6)

        stride_label = QLabel("stride")
        stride_spin = QSpinBox()
        stride_spin.setRange(1, 100)
        stride_spin.setValue(int(existing.get("stride", 1) or 1))
        stride_spin.setToolTip("Plot every Nth vector (1 = all vectors)")

        scale_label = QLabel("scale (0 = auto)")
        scale_spin = QDoubleSpinBox()
        scale_spin.setRange(0.0, 1e6)
        scale_spin.setDecimals(3)
        scale_spin.setSingleStep(0.1)
        scale_spin.setValue(float(existing.get("scale", 0.0) or 0.0))
        scale_spin.setToolTip("Scaling factor for arrow length; 0 lets cfplot choose automatically")

        key_length_label = QLabel("key_length (0 = auto)")
        key_length_spin = QDoubleSpinBox()
        key_length_spin.setRange(0.0, 1e6)
        key_length_spin.setDecimals(3)
        key_length_spin.setSingleStep(0.1)
        key_length_spin.setValue(float(existing.get("key_length", 0.0) or 0.0))
        key_length_spin.setToolTip("Length of the reference key vector; 0 lets cfplot choose automatically")

        key_label_label = QLabel("key_label")
        key_label_edit = QLineEdit(str(existing.get("key_label", "") or ""))
        key_label_edit.setPlaceholderText("e.g. m/s")
        key_label_edit.setToolTip("Label shown next to the reference key vector")

        title_label = QLabel("title")
        title_edit = QLineEdit(str(existing.get("title", "") or ""))
        title_edit.setPlaceholderText("Vector plot title")

        params_layout.addWidget(stride_label, 0, 0)
        params_layout.addWidget(stride_spin, 0, 1)
        params_layout.addWidget(scale_label, 1, 0)
        params_layout.addWidget(scale_spin, 1, 1)
        params_layout.addWidget(key_length_label, 2, 0)
        params_layout.addWidget(key_length_spin, 2, 1)
        params_layout.addWidget(key_label_label, 3, 0)
        params_layout.addWidget(key_label_edit, 3, 1)
        params_layout.addWidget(title_label, 4, 0)
        params_layout.addWidget(title_edit, 4, 1)
        params_layout.setColumnStretch(1, 1)

        # --- Buttons ---
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        apply_button = QPushButton("Apply")
        ok_button = QPushButton("Apply && Close")
        button_row.addWidget(cancel_button)
        button_row.addWidget(apply_button)
        button_row.addWidget(ok_button)
        cancel_button.clicked.connect(dialog.reject)

        layout.addWidget(fields_group)
        layout.addWidget(params_group)
        layout.addLayout(button_row)

        def _apply() -> bool:
            v_idx = v_combo.currentData()
            if v_idx is None:
                self.host.status.showMessage("Please select a V component field")
                return False
            if int(v_idx) == current_field_index:
                self.host.status.showMessage("U and V fields must be different fields")
                return False

            options: dict[str, object] = {
                "v_field_index": int(v_idx),
                "stride": int(stride_spin.value()),
            }

            scale_val = float(scale_spin.value())
            if scale_val > 0:
                options["scale"] = scale_val

            key_len_val = float(key_length_spin.value())
            if key_len_val > 0:
                options["key_length"] = key_len_val

            kl = key_label_edit.text().strip()
            if kl:
                options["key_label"] = kl

            title = title_edit.text().strip()
            if title:
                options["title"] = title

            self.host.plot_options_by_kind["vector"] = options
            self.host.status.showMessage("Updated vector options")
            self.host._request_plot_update()
            return True

        apply_button.clicked.connect(_apply)
        ok_button.clicked.connect(lambda: dialog.accept() if _apply() else None)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
