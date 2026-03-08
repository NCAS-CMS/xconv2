from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

if TYPE_CHECKING:
    from xconv2.core_window import CFVCore

logger = logging.getLogger(__name__)


class PlotViewController:
    """Manage plot area widgets, pixmap rendering, and save actions."""

    def __init__(self, host: "CFVCore") -> None:
        self.host = host

    def create_plot_area(self) -> QWidget:
        """Create right-side plot frame plus plot-type summary and button."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.host.plot_frame = QLabel("Waiting for data...")
        self.host.plot_frame.setAlignment(Qt.AlignCenter)
        self.host.plot_frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.host.plot_frame.setMinimumSize(120, 120)
        self.host.plot_frame.setStyleSheet("background-color: #222; color: #888; border: 1px solid #444;")

        summary_row = QHBoxLayout()
        self.host.plot_summary_label = QLabel("Open a field to inspect plot options.")
        self.host.plot_button = QPushButton("Plot")
        self.host.plot_button.setEnabled(False)
        self.host.plot_button.clicked.connect(self.on_plot_button_clicked)
        self.host.options_button = QPushButton("Options")
        self.host.options_button.setEnabled(False)
        self.host.options_button.clicked.connect(self.on_options_button_clicked)
        self.host.save_code_button = QPushButton("Save Code...")
        self.host.save_code_button.setEnabled(False)
        self.host.save_code_button.clicked.connect(self.on_save_code_button_clicked)
        self.host.save_plot_button = QPushButton("Save Plot...")
        self.host.save_plot_button.setEnabled(False)
        self.host.save_plot_button.clicked.connect(self.on_save_plot_button_clicked)

        summary_row.addWidget(self.host.plot_summary_label, 1)
        summary_row.addWidget(self.host.plot_button)
        summary_row.addWidget(self.host.options_button)
        summary_row.addWidget(self.host.save_code_button)
        summary_row.addWidget(self.host.save_plot_button)

        layout.addWidget(self.host.plot_frame, 1)
        layout.addLayout(summary_row)
        return container

    def on_plot_button_clicked(self) -> None:
        """Request a plot refresh when the current selection is plottable."""
        if not getattr(self.host, "plot_button", None) or not self.host.plot_button.isEnabled():
            return
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
        self.fit_window_to_plot_aspect()
        self.refresh_plot_pixmap()

    def fit_window_to_plot_aspect(self) -> None:
        """Nudge window height to match plot aspect ratio without exceeding screen bounds."""
        if self.host._plot_pixmap_original is None:
            return

        plot_height = self.host._plot_pixmap_original.height()
        plot_width = self.host._plot_pixmap_original.width()
        if plot_height <= 0 or plot_width <= 0:
            return

        aspect_ratio = plot_width / plot_height
        current_plot_width = max(self.host.plot_frame.width(), 1)
        desired_plot_height = max(1, int(current_plot_width / aspect_ratio))
        current_plot_height = max(self.host.plot_frame.height(), 1)
        height_delta = desired_plot_height - current_plot_height

        if abs(height_delta) < 12:
            return

        screen = self.host.screen() or QApplication.primaryScreen()
        if screen is None:
            return

        available_height = screen.availableGeometry().height()
        min_height = max(self.host.minimumHeight(), 420)
        max_height = max(min_height, int(available_height * 0.9))
        target_height = max(min_height, min(self.host.height() + height_delta, max_height))

        if target_height != self.host.height():
            self.host.resize(self.host.width(), target_height)

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

    def on_save_code_button_clicked(self) -> None:
        """Prompt for destination file and request worker-side plot code save."""
        if not getattr(self.host, "save_code_button", None) or not self.host.save_code_button.isEnabled():
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

    def on_save_plot_button_clicked(self) -> None:
        """Prompt for destination image file and request worker-side plot save."""
        if not getattr(self.host, "save_plot_button", None) or not self.host.save_plot_button.isEnabled():
            return

        default_path = self.host._default_save_path("last_save_plot_dir", "cfview_plot.png")
        file_path, _ = QFileDialog.getSaveFileName(
            self.host,
            "Save Plot",
            default_path,
            "PNG files (*.png);;PDF files (*.pdf);;PostScript files (*.ps);;All files (*)",
        )
        if not file_path:
            return

        if not Path(file_path).suffix:
            file_path += ".png"

        self.host._remember_last_save_dir("last_save_plot_dir", file_path)
        self.host._request_plot_save(file_path)
