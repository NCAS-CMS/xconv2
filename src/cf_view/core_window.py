"""Core GUI classes for cf-view.

This module contains presentation-only code:
- widget creation
- layout composition
- menu/tray setup
- local UI state updates

Worker orchestration and request/response handling live in `main_window.py`.
"""

from __future__ import annotations

from pathlib import Path
import logging
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class CFVCore(QMainWindow):
    """Base window with GUI-only behavior and extension hooks for app logic."""

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("cf-view (2026 Core)")
        self.resize(1000, 700)

        self.app_icon = self._create_app_icon()
        if not self.app_icon.isNull():
            self.setWindowIcon(self.app_icon)

        # Stores {coord_name: (QSlider, values_list)} for preview requests.
        self.controls = {}

        self.setup_ui()
        self._setup_tray_icon()

    def _create_app_icon(self) -> QIcon:
        """Create application icon with a stable fallback chain."""
        assets_dir = Path(__file__).resolve().parent / "assets"
        candidate_paths = [
            assets_dir / "cf-logo.png",
            assets_dir / "cf-logo.svg",
        ]

        icon = QIcon()
        for candidate in candidate_paths:
            icon = QIcon(str(candidate))
            if not icon.isNull():
                logger.info("Using app icon asset: %s", candidate)
                break

        if icon.isNull():
            logger.warning("No usable icon asset found in %s", assets_dir)

        if icon.isNull():
            icon = QIcon.fromTheme("applications-science")
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        return icon

    def _setup_tray_icon(self) -> None:
        """Declare and show the system tray icon with quick actions."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray is not available on this platform/session")
            self.tray_icon = None
            return

        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        tray_menu = QMenu(self)

        show_action = QAction("Show Window", self)
        show_action.triggered.connect(self._show_main_window)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()
        tray_menu.addAction("Quit", self._quit_application)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setToolTip("cf-view")
        self.tray_icon.activated.connect(self._handle_tray_activation)
        self.tray_icon.show()

        logger.info("System tray icon initialized")

    def _show_main_window(self) -> None:
        """Bring the main window to the foreground."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _handle_tray_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray click by restoring the main window."""
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_main_window()

    def setup_ui(self) -> None:
        """Set up the main window layout and top-level widgets."""
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)

        left_panel = self._create_left_panel()
        self.plot_area = self._create_plot_area()
        layout.addWidget(left_panel)
        layout.addWidget(self.plot_area, stretch=1)

        self._setup_menu_bar()
        self._setup_status_bar()

    def _setup_menu_bar(self) -> None:
        """Create application menu actions."""
        file_menu = self.menuBar().addMenu("&File")

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self._quit_application)

        file_menu.addAction(quit_action)

    def _create_left_panel(self) -> QWidget:
        """Create the left panel containing controls, field list, and sliders."""
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addLayout(self._create_file_picker_row())
        left_layout.addWidget(self._create_field_list_area())
        left_layout.addWidget(self._create_slider_scroll_area())
        return left_panel

    def _create_field_list_area(self) -> QWidget:
        """Create field list plus a six-line details output area."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Fields")
        self.field_list_widget = QListWidget()
        self.field_list_widget.itemClicked.connect(self.on_field_clicked)
        self._set_field_list_visible_rows(5)

        output_title = QLabel("Selection Output")
        self.selection_output = QPlainTextEdit()
        self.selection_output.setReadOnly(True)
        self.selection_output.setPlaceholderText("Click a field to see details...")

        line_height = self.selection_output.fontMetrics().lineSpacing()
        frame = self.selection_output.frameWidth() * 2
        margin = 10
        self.selection_output.setFixedHeight((line_height * 6) + frame + margin)

        layout.addWidget(title)
        layout.addWidget(self.field_list_widget)
        layout.addWidget(output_title)
        layout.addWidget(self.selection_output)
        return container

    def _set_field_list_visible_rows(self, row_count: int) -> None:
        """Size the field list to show a target number of rows by default."""
        row_height = self.field_list_widget.sizeHintForRow(0)
        if row_height <= 0:
            row_height = self.field_list_widget.fontMetrics().lineSpacing() + 6

        frame = self.field_list_widget.frameWidth() * 2
        height = (row_height * row_count) + frame
        self.field_list_widget.setMinimumHeight(height)
        self.field_list_widget.setMaximumHeight(height)

    def _create_file_picker_row(self) -> QHBoxLayout:
        """Create the file picker row (path display + browse/quit buttons)."""
        file_picker_row = QHBoxLayout()

        self.file_path_input = QLineEdit()
        self.file_path_input.setReadOnly(True)
        self.file_path_input.setPlaceholderText("Select a data file...")

        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._choose_file)

        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(self._quit_application)

        file_picker_row.addWidget(self.file_path_input, stretch=1)
        file_picker_row.addWidget(browse_button)
        file_picker_row.addWidget(quit_button)
        return file_picker_row

    def _create_slider_scroll_area(self) -> QScrollArea:
        """Create the scrollable container that hosts dynamic sliders."""
        self.sidebar = QVBoxLayout()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        sidebar_container = QWidget()
        sidebar_container.setLayout(self.sidebar)
        scroll.setWidget(sidebar_container)
        scroll.setFixedWidth(300)
        return scroll

    def _create_plot_area(self) -> QLabel:
        """Create the right-side placeholder plot area."""
        plot_area = QLabel("Waiting for data...")
        plot_area.setAlignment(Qt.AlignCenter)
        plot_area.setStyleSheet("background-color: #222; color: #888; border: 1px solid #444;")
        return plot_area

    def _setup_status_bar(self) -> None:
        """Create and initialize the status bar."""
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("System Ready. Initialize S3 Load.")

    def populate_field_list(self, fields: Sequence[object]) -> None:
        """Populate the field list UI from worker metadata."""
        self.field_list_widget.clear()

        for field in fields:
            if isinstance(field, (tuple, list)) and len(field) >= 2:
                identity = str(field[0])
                detail = str(field[1])
            else:
                identity = str(field)
                detail = str(field)

            item = QListWidgetItem(identity)
            item.setData(Qt.UserRole, detail)
            self.field_list_widget.addItem(item)

        self._set_field_list_visible_rows(5)
        self.selection_output.setPlainText(
            f"Loaded {self.field_list_widget.count()} fields.\n"
            "Click an entry to show field details."
        )
        logger.info("Displayed %d fields in list", self.field_list_widget.count())

    def on_field_clicked(self, item: QListWidgetItem) -> None:
        """Display selected field details in the output panel."""
        selected_field = item.text()
        detail = item.data(Qt.UserRole)
        if detail:
            detail = '\n'.join(detail.splitlines()[2:])
            self.selection_output.setPlainText(detail)
        else:
            self.selection_output.setPlainText("No additional detail available.")
        logger.info("Field selected: %s", selected_field)

    def build_dynamic_sliders(self, metadata: dict[str, list[object]]) -> None:
        """Build sliders from coordinate metadata."""
        self.controls.clear()

        for i in reversed(range(self.sidebar.count())):
            widget = self.sidebar.itemAt(i).widget()
            if widget is not None:
                widget.setParent(None)

        for name, values in metadata.items():
            container = QWidget()
            row = QVBoxLayout(container)

            label = QLabel(f"{name.upper()}: {values[0]}")
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, len(values) - 1)
            slider.valueChanged.connect(
                lambda v, n=name, vals=values, lbl=label: self.on_slider_moved(n, vals[v], lbl)
            )

            row.addWidget(label)
            row.addWidget(slider)
            self.sidebar.addWidget(container)
            self.controls[name] = (slider, values)

        logger.info("Built %d dynamic sliders", len(self.controls))

    def on_slider_moved(self, name: str, val: object, label: QLabel) -> None:
        """Handle slider movement events."""
        label.setText(f"{name.upper()}: {val}")
        logger.debug("Slider moved: %s=%r", name, val)
        self._request_plot_update()

    def _choose_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Data File",
            "",
            "NetCDF files (*.nc *.nc4 *.cdf);;All files (*)",
        )
        if not file_path:
            return

        self.file_path_input.setText(file_path)
        logger.info("Selected file: %s", file_path)
        self.on_file_selected(file_path)

    def on_file_selected(self, file_path: str) -> None:
        """Hook for worker-backed implementations after file selection."""
        logger.debug("File selected in core UI: %s", file_path)

    def _request_plot_update(self) -> None:
        """Hook for worker-backed implementations."""

    def _quit_application(self) -> None:
        """Handle quit button click by closing the main window."""
        logger.info("Quit requested from UI")
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Ensure tray resources are released when the GUI exits."""
        if getattr(self, "tray_icon", None) is not None:
            self.tray_icon.hide()
        super().closeEvent(event)
