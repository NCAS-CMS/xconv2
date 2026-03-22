from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from xconv2.ui.plot_options_shared import build_common_options_sections

if TYPE_CHECKING:
    from xconv2.core_window import CFVCore


class LineplotOptionsController:
    """Encapsulate lineplot options dialog behavior."""

    _LEGEND_LOCATIONS = (
        "best",
        "upper right",
        "upper left",
        "lower left",
        "lower right",
        "right",
        "center left",
        "center right",
        "lower center",
        "upper center",
        "center",
    )

    def __init__(self, host: "CFVCore") -> None:
        self.host = host
        self._dialog: QDialog | None = None

    def show_lineplot_options_dialog(self) -> None:
        """Show lineplot options dialog and persist selected options."""
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.raise_()
            self._dialog.activateWindow()
            return

        existing = self.host.plot_options_by_kind.get("lineplot", {})

        dialog = QDialog(self.host)
        self._dialog = dialog
        dialog.setWindowTitle("Lineplot Options")
        dialog.setWindowModality(Qt.NonModal)
        dialog.resize(540, 320)
        dialog.finished.connect(lambda _result: setattr(self, "_dialog", None))

        layout = QVBoxLayout(dialog)

        common = build_common_options_sections(
            host=self.host,
            existing=existing,
            plot_title_label="lineplot title",
            plot_title_placeholder="Lineplot title",
        )

        legend_group = QGroupBox("Title Legend Details")
        legend_layout = QHBoxLayout(legend_group)
        legend_display_checkbox = QCheckBox("display legend")
        legend_display_checkbox.setChecked(bool(existing.get("legend_display", True)))
        legend_location_combo = QComboBox()
        legend_location_combo.addItems(list(self._LEGEND_LOCATIONS))
        current_location = str(existing.get("legend_location", "best"))
        location_index = legend_location_combo.findText(current_location)
        legend_location_combo.setCurrentIndex(location_index if location_index >= 0 else 0)

        def _sync_legend_controls() -> None:
            legend_location_combo.setEnabled(legend_display_checkbox.isChecked())

        legend_display_checkbox.toggled.connect(_sync_legend_controls)
        _sync_legend_controls()

        legend_layout.addWidget(legend_display_checkbox)
        legend_layout.addStretch(1)
        legend_layout.addWidget(legend_location_combo)

        text_sizes_group = QGroupBox("Text Sizes")
        text_sizes_layout = QGridLayout(text_sizes_group)
        text_sizes_layout.setContentsMargins(9, 9, 9, 9)
        text_sizes_layout.setHorizontalSpacing(12)
        text_sizes_layout.setVerticalSpacing(6)

        lineplot_title_fontsize_label = QLabel("lineplot title")
        lineplot_title_fontsize_spin = QDoubleSpinBox()
        lineplot_title_fontsize_spin.setRange(1.0, 48.0)
        lineplot_title_fontsize_spin.setDecimals(1)
        lineplot_title_fontsize_spin.setSingleStep(0.5)
        lineplot_title_fontsize_spin.setValue(
            float(existing.get("lineplot_title_fontsize", self.host._contour_title_fontsize()))
        )

        page_title_fontsize_label = QLabel("page title")
        page_title_fontsize_spin = QDoubleSpinBox()
        page_title_fontsize_spin.setRange(1.0, 48.0)
        page_title_fontsize_spin.setDecimals(1)
        page_title_fontsize_spin.setSingleStep(0.5)
        page_title_fontsize_spin.setValue(
            float(existing.get("page_title_fontsize", self.host._page_title_fontsize()))
        )

        annotation_fontsize_label = QLabel("annotations")
        annotation_fontsize_spin = QDoubleSpinBox()
        annotation_fontsize_spin.setRange(1.0, 48.0)
        annotation_fontsize_spin.setDecimals(1)
        annotation_fontsize_spin.setSingleStep(0.5)
        annotation_fontsize_spin.setValue(
            float(existing.get("annotation_fontsize", self.host._annotation_fontsize()))
        )

        reset_text_sizes_button = QPushButton("Reset to GUI defaults")

        def _reset_text_sizes() -> None:
            lineplot_title_fontsize_spin.setValue(self.host._contour_title_fontsize())
            page_title_fontsize_spin.setValue(self.host._page_title_fontsize())
            annotation_fontsize_spin.setValue(self.host._annotation_fontsize())

        reset_text_sizes_button.clicked.connect(_reset_text_sizes)

        text_sizes_layout.addWidget(lineplot_title_fontsize_label, 0, 0)
        text_sizes_layout.addWidget(page_title_fontsize_label, 0, 1)
        text_sizes_layout.addWidget(annotation_fontsize_label, 0, 2)
        text_sizes_layout.addWidget(lineplot_title_fontsize_spin, 1, 0)
        text_sizes_layout.addWidget(page_title_fontsize_spin, 1, 1)
        text_sizes_layout.addWidget(annotation_fontsize_spin, 1, 2)
        text_sizes_layout.addWidget(reset_text_sizes_button, 2, 0, 1, 3)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        plot_button = QPushButton("Apply")
        ok_button = QPushButton("Apply && Close")
        button_row.addWidget(cancel_button)
        button_row.addWidget(plot_button)
        button_row.addWidget(ok_button)
        cancel_button.clicked.connect(dialog.reject)

        layout.addWidget(common.titles_group)
        layout.addWidget(common.annotations_group)
        layout.addWidget(legend_group)
        layout.addWidget(text_sizes_group)
        layout.addLayout(button_row)

        def _apply_options() -> bool:
            options = common.as_options()
            options["legend_display"] = bool(legend_display_checkbox.isChecked())
            if options["legend_display"]:
                options["legend_location"] = str(legend_location_combo.currentText())
            options["lineplot_title_fontsize"] = float(lineplot_title_fontsize_spin.value())
            options["page_title_fontsize"] = float(page_title_fontsize_spin.value())
            options["annotation_fontsize"] = float(annotation_fontsize_spin.value())

            self.host.plot_options_by_kind["lineplot"] = options
            self.host.status.showMessage("Updated lineplot options")
            self.host._request_plot_update()
            return True

        ok_button.clicked.connect(lambda: dialog.accept() if _apply_options() else None)
        plot_button.clicked.connect(_apply_options)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()