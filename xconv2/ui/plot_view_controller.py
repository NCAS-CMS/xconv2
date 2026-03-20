from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from xconv2.core_window import CFVCore

logger = logging.getLogger(__name__)


class CircularSpinner(QWidget):
    """Simple animated circular spinner drawn with Qt painter primitives."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tick = 0
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._advance)
        self.setMinimumSize(52, 52)
        self.setMaximumSize(52, 52)

    def start(self) -> None:
        """Start spinner animation."""
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self) -> None:
        """Stop spinner animation."""
        self._timer.stop()
        self.hide()

    def _advance(self) -> None:
        """Advance animated segment and repaint."""
        self._tick = (self._tick + 1) % 12
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        """Paint radial segments with a rotating bright head."""
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        size = min(self.width(), self.height())
        radius = size * 0.35
        center_x = self.width() / 2
        center_y = self.height() / 2
        segment_count = 12

        for index in range(segment_count):
            # Fade trailing segments behind the active head.
            distance = (index - self._tick) % segment_count
            alpha = max(35, 255 - (distance * 18))
            color = QColor(120, 185, 255, alpha)

            angle = (2 * math.pi * index) / segment_count
            inner = radius * 0.55
            outer = radius
            x1 = center_x + inner * math.cos(angle)
            y1 = center_y + inner * math.sin(angle)
            x2 = center_x + outer * math.cos(angle)
            y2 = center_y + outer * math.sin(angle)

            pen = QPen(color)
            pen.setWidth(4)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))


class PlotViewController:
    """Manage plot area widgets, pixmap rendering, and save actions."""

    SAVE_MODE_PLOT = "plot"
    SAVE_MODE_DATA = "data"
    SAVE_MODE_CODE = "code"
    SAVE_MODE_ALL = "all"

    def __init__(self, host: "CFVCore") -> None:
        self.host = host

    def create_plot_area(self) -> QWidget:
        """Create right-side plot frame plus plot-type summary and button."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.host.plot_info_output = QPlainTextEdit()
        self.host.plot_info_output.setReadOnly(True)
        self.host.plot_info_output.setPlaceholderText("Click a field to see details...")
        self.host.plot_info_output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.host.plot_info_output.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.host.plot_info_output.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.host.plot_info_output.setPlainText(
            getattr(self.host, "current_selection_info_text", "No selection info available.")
        )

        line_height = self.host.plot_info_output.fontMetrics().lineSpacing()
        frame_width = self.host.plot_info_output.frameWidth() * 2
        margin = 10
        self.host.plot_info_output.setFixedHeight((line_height * 6) + frame_width + margin)

        self.host.plot_frame = QLabel("Waiting for data...")
        self.host.plot_frame.setAlignment(Qt.AlignCenter)
        self.host.plot_frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.host.plot_frame.setMinimumSize(120, 120)
        self.host.plot_frame.setStyleSheet("background-color: #222; color: #888; border: 1px solid #444;")

        self.host.plot_loading_overlay = QWidget()
        self.host.plot_loading_overlay.setStyleSheet(
            "background-color: rgba(20, 20, 20, 160);"
            "color: #f0f0f0;"
            "border: 1px solid rgba(120, 120, 120, 120);"
        )

        overlay_layout = QVBoxLayout(self.host.plot_loading_overlay)
        overlay_layout.setContentsMargins(12, 12, 12, 12)
        overlay_layout.addStretch(1)

        self.host.plot_loading_spinner = CircularSpinner(self.host.plot_loading_overlay)

        self.host.plot_loading_text = QLabel("Rendering plot...")
        self.host.plot_loading_text.setAlignment(Qt.AlignCenter)
        self.host.plot_loading_text.setWordWrap(True)
        self.host.plot_loading_text.setStyleSheet("font-size: 14px; font-weight: 600;")

        overlay_layout.addWidget(self.host.plot_loading_spinner)
        overlay_layout.addWidget(self.host.plot_loading_text)
        overlay_layout.addStretch(1)
        self.host.plot_loading_overlay.hide()

        plot_stack = QStackedLayout()
        plot_stack.setStackingMode(QStackedLayout.StackAll)
        plot_stack.addWidget(self.host.plot_frame)
        plot_stack.addWidget(self.host.plot_loading_overlay)

        plot_stack_container = QWidget()
        plot_stack_container.setLayout(plot_stack)

        summary_row = QHBoxLayout()
        self.host.plot_summary_label = QLabel("Open a field to inspect plot options.")
        self.host.plot_type_combo = QComboBox()
        self.host.plot_type_combo.setMinimumWidth(130)
        self.host.plot_type_combo.setEnabled(False)
        self.host.plot_type_combo.currentIndexChanged.connect(self.on_plot_type_changed)
        self.host.plot_button = QPushButton("Plot")
        self.host.plot_button.setEnabled(False)
        self.host.plot_button.clicked.connect(self.on_plot_button_clicked)
        self.host.options_button = QPushButton("Options")
        self.host.options_button.setEnabled(False)
        self.host.options_button.clicked.connect(self.on_options_button_clicked)
        self.host.save_target_combo = QComboBox()
        self.host.save_target_combo.setMinimumWidth(110)
        self.host.save_target_combo.addItem("Plot", self.SAVE_MODE_PLOT)
        self.host.save_target_combo.addItem("Data", self.SAVE_MODE_DATA)
        self.host.save_target_combo.addItem("Code", self.SAVE_MODE_CODE)
        self.host.save_target_combo.addItem("All", self.SAVE_MODE_ALL)
        self.host.save_target_combo.setEnabled(False)
        self.host.save_go_button = QPushButton("Export")
        self.host.save_go_button.setEnabled(False)
        self.host.save_go_button.clicked.connect(self.on_save_go_clicked)

        combo_height = max(
            self.host.plot_type_combo.sizeHint().height(),
            self.host.save_target_combo.sizeHint().height(),
        )
        for button in (self.host.plot_button, self.host.options_button, self.host.save_go_button):
            button.setMinimumHeight(combo_height)

        plot_controls_group = QFrame()
        plot_controls_group.setObjectName("plot_controls_group")
        plot_controls_group.setFrameShape(QFrame.StyledPanel)
        plot_controls_group.setFrameShadow(QFrame.Plain)
        plot_controls_group.setStyleSheet(
            "QFrame#plot_controls_group {"
            " border: 1px solid palette(mid);"
            " border-radius: 4px;"
            "}"
        )
        plot_controls_layout = QHBoxLayout(plot_controls_group)
        plot_controls_layout.setContentsMargins(6, 2, 6, 2)
        plot_controls_layout.setSpacing(6)
        plot_controls_layout.addWidget(self.host.plot_type_combo)
        plot_controls_layout.addWidget(self.host.plot_button)
        plot_controls_layout.addWidget(self.host.options_button)

        export_controls_group = QFrame()
        export_controls_group.setObjectName("export_controls_group")
        export_controls_group.setFrameShape(QFrame.StyledPanel)
        export_controls_group.setFrameShadow(QFrame.Plain)
        export_controls_group.setStyleSheet(
            "QFrame#export_controls_group {"
            " border: 1px solid palette(mid);"
            " border-radius: 4px;"
            "}"
        )
        export_controls_layout = QHBoxLayout(export_controls_group)
        export_controls_layout.setContentsMargins(6, 2, 6, 2)
        export_controls_layout.setSpacing(6)
        export_controls_layout.addWidget(self.host.save_go_button)
        export_controls_layout.addWidget(self.host.save_target_combo)

        summary_row.addWidget(self.host.plot_summary_label, 1, Qt.AlignVCenter)
        summary_row.addWidget(plot_controls_group, 0, Qt.AlignVCenter)
        summary_row.addWidget(export_controls_group, 0, Qt.AlignVCenter)

        layout.addWidget(self.host.plot_info_output)
        layout.addWidget(plot_stack_container, 1)
        layout.addLayout(summary_row)
        return container

    def set_plot_type_options(self, options: list[str], selected: str | None) -> None:
        """Populate the plot-type selector from available plot kinds."""
        combo = getattr(self.host, "plot_type_combo", None)
        if combo is None:
            return

        combo.blockSignals(True)
        combo.clear()
        for kind in options:
            combo.addItem(kind.title(), kind)

        if options:
            combo.setEnabled(True)
            selected_kind = selected if selected in options else options[0]
            selected_index = combo.findData(selected_kind)
            combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        else:
            combo.setEnabled(False)

        combo.blockSignals(False)

    def on_plot_type_changed(self) -> None:
        """Persist selected plot type and refresh context-sensitive actions."""
        combo = getattr(self.host, "plot_type_combo", None)
        if combo is None:
            return
        selected_kind = combo.currentData()
        if isinstance(selected_kind, str):
            self.host.selected_plot_kind = selected_kind
            self.host.selection_controller.refresh_plot_summary()

    def set_plot_loading(self, is_loading: bool, message: str = "Rendering plot...") -> None:
        """Show or hide an inline loading overlay while worker plot tasks run."""
        overlay = getattr(self.host, "plot_loading_overlay", None)
        if overlay is None:
            return

        if is_loading:
            self.host.plot_loading_text.setText(message)
            self.host.plot_loading_spinner.start()
            overlay.show()
            overlay.raise_()
            return

        self.host.plot_loading_spinner.stop()
        overlay.hide()

    def clear_plot_canvas(self, message: str = "Plot unavailable") -> None:
        """Clear any rendered plot image and show a fallback message."""
        self.host._plot_pixmap_original = None
        self.host.plot_frame.setPixmap(QPixmap())
        self.host.plot_frame.setText(message)

    def on_plot_button_clicked(self) -> None:
        """Request a plot refresh when the current selection is plottable."""
        button = getattr(self.host, "plot_button", None)
        if not button or not button.isEnabled():
            logger.info(
                "PLOT_DIAG plot_click_ignored enabled=%s controls=%d selected_kind=%s",
                bool(button and button.isEnabled()),
                len(getattr(self.host, "controls", {})),
                getattr(self.host, "selected_plot_kind", None),
            )
            return

        logger.info(
            "PLOT_DIAG plot_click enabled=%s controls=%d selected_kind=%s",
            button.isEnabled(),
            len(getattr(self.host, "controls", {})),
            getattr(self.host, "selected_plot_kind", None),
        )
        self.host._request_plot_update()

    def on_options_button_clicked(self) -> None:
        """Request plot-type specific options from worker/UI flow."""
        if not getattr(self.host, "options_button", None) or not self.host.options_button.isEnabled():
            return
        self.host._request_plot_options()

    def set_plot_image(self, png_bytes: bytes) -> None:
        """Render PNG bytes from worker output into the plot frame."""
        if not png_bytes:
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(png_bytes, "PNG"):
            logger.warning("Failed to decode plot PNG payload")
            return

        self.host._plot_pixmap_original = pixmap
        self.set_plot_loading(False)
        self.refresh_plot_pixmap()
        QTimer.singleShot(0, self.fit_window_to_plot_aspect)

    def adjust_window_width_for_info_panel(self, info_panel_visible: bool) -> None:
        """Re-fit window geometry to current plot aspect after layout toggles."""
        QTimer.singleShot(0, lambda: self._apply_window_width_for_info_panel(info_panel_visible))

    @staticmethod
    def _compute_target_window_width(
        current_window_width: int,
        current_plot_width: int,
        current_plot_height: int,
        pixmap_width: int,
        pixmap_height: int,
        max_window_width: int,
        min_window_width: int,
    ) -> int:
        """Return a window width target that reduces side/top-bottom letterboxing."""
        if min(
            current_window_width,
            current_plot_width,
            current_plot_height,
            pixmap_width,
            pixmap_height,
            max_window_width,
            min_window_width,
        ) <= 0:
            return current_window_width

        desired_plot_width = int(round(current_plot_height * (pixmap_width / pixmap_height)))
        width_delta = desired_plot_width - current_plot_width
        if abs(width_delta) <= 12:
            return current_window_width

        return max(
            min_window_width,
            min(current_window_width + width_delta, max_window_width),
        )

    def _apply_window_width_for_info_panel(self, info_panel_visible: bool) -> None:
        """Apply width expansion when hiding details and restore width when showing them."""
        if info_panel_visible:
            restore_width = getattr(self.host, "_selection_info_expanded_from_width", None)
            if restore_width is not None and restore_width != self.host.width():
                self.host.resize(max(self.host.minimumWidth(), restore_width), self.host.height())

            pixmap = getattr(self.host, "_plot_pixmap_original", None)
            if pixmap is not None:
                current_plot_width = max(self.host.plot_frame.width(), 1)
                current_plot_height = max(self.host.plot_frame.height(), 1)

                screen = self.host.screen() or QApplication.primaryScreen()
                if screen is not None:
                    available_width = screen.availableGeometry().width()
                    min_width = max(self.host.minimumWidth(), 640)
                    base_max_width = max(min_width, int(available_width * 0.95))
                    if restore_width is not None:
                        max_width = max(min_width, min(base_max_width, restore_width))
                    else:
                        max_width = base_max_width

                    target_width = self._compute_target_window_width(
                        current_window_width=self.host.width(),
                        current_plot_width=current_plot_width,
                        current_plot_height=current_plot_height,
                        pixmap_width=pixmap.width(),
                        pixmap_height=pixmap.height(),
                        max_window_width=max_width,
                        min_window_width=min_width,
                    )
                    if target_width != self.host.width():
                        self.host.resize(target_width, self.host.height())

            self.host._selection_info_expanded_from_width = None
            self.refresh_plot_pixmap()
            return

        pixmap = getattr(self.host, "_plot_pixmap_original", None)
        if pixmap is None:
            self.refresh_plot_pixmap()
            return

        current_plot_width = max(self.host.plot_frame.width(), 1)
        current_plot_height = max(self.host.plot_frame.height(), 1)

        screen = self.host.screen() or QApplication.primaryScreen()
        if screen is None:
            self.refresh_plot_pixmap()
            return

        available_width = screen.availableGeometry().width()
        min_width = max(self.host.minimumWidth(), 640)
        max_width = max(min_width, int(available_width * 0.95))
        target_width = self._compute_target_window_width(
            current_window_width=self.host.width(),
            current_plot_width=current_plot_width,
            current_plot_height=current_plot_height,
            pixmap_width=pixmap.width(),
            pixmap_height=pixmap.height(),
            max_window_width=max_width,
            min_window_width=min_width,
        )

        if target_width != self.host.width():
            self.host.resize(target_width, self.host.height())

        self.refresh_plot_pixmap()

    def fit_window_to_plot_aspect(self) -> None:
        """Resize window to keep plot viewport aligned with the image aspect ratio."""
        if self.host._plot_pixmap_original is None:
            return

        pixmap_height = self.host._plot_pixmap_original.height()
        pixmap_width = self.host._plot_pixmap_original.width()
        if pixmap_height <= 0 or pixmap_width <= 0:
            return

        current_plot_width = max(self.host.plot_frame.width(), 1)
        current_plot_height = max(self.host.plot_frame.height(), 1)
        aspect_ratio = pixmap_width / pixmap_height
        desired_plot_width = max(1, int(round(current_plot_height * aspect_ratio)))
        desired_plot_height = max(1, int(round(current_plot_width / aspect_ratio)))

        width_delta = desired_plot_width - current_plot_width
        height_delta = desired_plot_height - current_plot_height

        if abs(width_delta) < 10 and abs(height_delta) < 10:
            return

        chrome_width = max(0, self.host.width() - current_plot_width)
        chrome_height = max(0, self.host.height() - current_plot_height)
        target_width = self.host.width() + width_delta
        target_height = self.host.height() + height_delta

        screen = self.host.screen() or QApplication.primaryScreen()
        if screen is None:
            return

        available_width = screen.availableGeometry().width()
        available_height = screen.availableGeometry().height()
        min_width = max(self.host.minimumWidth(), 640)
        min_height = max(self.host.minimumHeight(), 420)
        max_width = max(min_width, int(available_width * 0.95))
        max_height = max(min_height, int(available_height * 0.9))
        target_width = max(min_width, min(target_width, max_width))
        target_height = max(min_height, min(target_height, max_height))

        # If one axis was clamped by screen bounds, recompute the other axis
        # from the available plot viewport so aspect fitting remains consistent.
        if target_width != self.host.width() or target_height != self.host.height():
            fitted_plot_width = max(1, target_width - chrome_width)
            fitted_plot_height = max(1, target_height - chrome_height)
            fitted_ratio = fitted_plot_width / fitted_plot_height

            if fitted_ratio > aspect_ratio:
                fitted_plot_width = max(1, int(round(fitted_plot_height * aspect_ratio)))
                target_width = max(min_width, min(chrome_width + fitted_plot_width, max_width))
            else:
                fitted_plot_height = max(1, int(round(fitted_plot_width / aspect_ratio)))
                target_height = max(min_height, min(chrome_height + fitted_plot_height, max_height))

        if target_width != self.host.width() or target_height != self.host.height():
            self.host.resize(target_width, target_height)

        self.refresh_plot_pixmap()

    def refresh_plot_pixmap(self) -> None:
        """Scale current plot pixmap to fit the visible plot frame."""
        if self.host._plot_pixmap_original is None:
            return

        target_size = self.host.plot_frame.size()
        if target_size.width() <= 0 or target_size.height() <= 0:
            return

        scaled = self.host._plot_pixmap_original.scaled(
            target_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.host.plot_frame.setPixmap(scaled)
        self.host.plot_frame.setText("")

    def on_save_go_clicked(self) -> None:
        """Run save action for the currently selected save target mode."""
        if not getattr(self.host, "save_go_button", None) or not self.host.save_go_button.isEnabled():
            return

        mode = self.host.save_target_combo.currentData()
        if mode == self.SAVE_MODE_CODE:
            self.on_save_code_button_clicked()
        elif mode == self.SAVE_MODE_DATA:
            self.on_save_data_button_clicked()
        elif mode == self.SAVE_MODE_ALL:
            self.on_save_all_button_clicked()
        else:
            self.on_save_plot_button_clicked()

    def on_save_code_button_clicked(self) -> None:
        """Prompt for destination file and request worker-side plot code save."""
        if not getattr(self.host, "save_go_button", None) or not self.host.save_go_button.isEnabled():
            return

        default_path = self.host._default_save_path("last_save_code_dir", "cfview_plot_code.py")
        file_path, _ = QFileDialog.getSaveFileName(
            self.host,
            "Save Plot Code",
            default_path,
            "Python files (*.py);;Text files (*.txt);;All files (*)",
        )
        if not file_path:
            return

        if not Path(file_path).suffix:
            file_path += ".py"

        self.host._remember_last_save_dir("last_save_code_dir", file_path)
        self.host._request_plot_code_save(file_path)

    def on_save_data_button_clicked(self) -> None:
        """Prompt for destination file and request worker-side selected-data save."""
        if not getattr(self.host, "save_go_button", None) or not self.host.save_go_button.isEnabled():
            return

        default_stem = self.host._default_plot_filename()
        default_path = self.host._default_save_path("last_save_data_dir", f"{default_stem}.nc")
        file_path, _ = QFileDialog.getSaveFileName(
            self.host,
            "Save Plot Data",
            default_path,
            "NetCDF files (*.nc);;All files (*)",
        )
        if not file_path:
            return

        if not Path(file_path).suffix:
            file_path += ".nc"

        self.host._remember_last_save_dir("last_save_data_dir", file_path)
        self.host._request_plot_data_save(file_path)

    def on_save_plot_button_clicked(self) -> None:
        """Prompt for destination image file and request worker-side plot save."""
        if not getattr(self.host, "save_go_button", None) or not self.host.save_go_button.isEnabled():
            return

        format_filters = {
            "png": "PNG files (*.png)",
            "svg": "SVG files (*.svg)",
            "pdf": "PDF files (*.pdf)",
        }
        default_format = self.host._default_plot_output_format()
        default_filename = self.host._default_plot_filename()
        default_path = self.host._default_save_path(
            "last_save_plot_dir",
            f"{default_filename}.{default_format}",
        )

        ordered_formats = [default_format] + [fmt for fmt in ("png", "svg", "pdf") if fmt != default_format]
        selected_filter = format_filters[default_format]
        filters = ";;".join(format_filters[fmt] for fmt in ordered_formats)

        file_path, selected_filter = QFileDialog.getSaveFileName(
            self.host,
            "Save Plot",
            default_path,
            filters,
            selected_filter,
        )
        if not file_path:
            return

        selected_ext = default_format
        for ext, filt in format_filters.items():
            if selected_filter == filt:
                selected_ext = ext
                break

        if not Path(file_path).suffix:
            file_path += f".{selected_ext}"

        self.host._remember_last_save_dir("last_save_plot_dir", file_path)
        self.host._request_plot_save(file_path)

    def on_save_all_button_clicked(self) -> None:
        """Prompt for a stem path and save plot/data/code outputs in one action."""
        if not getattr(self.host, "save_go_button", None) or not self.host.save_go_button.isEnabled():
            return

        default_stem = self.host._default_plot_filename()
        default_path = self.host._default_save_path("last_save_plot_dir", default_stem)
        stem_path, _ = QFileDialog.getSaveFileName(
            self.host,
            "Save Plot/Data/Code (Choose stem)",
            default_path,
            "All files (*)",
        )
        if not stem_path:
            return

        stem = Path(stem_path).expanduser()
        if stem.suffix.lower() in {".png", ".svg", ".pdf", ".py", ".nc"}:
            stem = stem.with_suffix("")

        plot_ext = self.host._default_plot_output_format()
        save_plot_path = str(stem.with_suffix(f".{plot_ext}"))
        save_data_path = str(stem.with_suffix(".nc"))
        save_code_path = str(stem.with_suffix(".py"))

        self.host._remember_last_save_dir("last_save_plot_dir", save_plot_path)
        self.host._remember_last_save_dir("last_save_data_dir", save_data_path)
        self.host._remember_last_save_dir("last_save_code_dir", save_code_path)
        self.host._request_plot_save_all(save_code_path, save_plot_path, save_data_path)
