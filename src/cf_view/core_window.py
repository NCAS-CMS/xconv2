"""Core GUI classes for cf-view.

This module contains presentation-only code:
- widget creation
- layout composition
- menu/tray setup
- local UI state updates

Worker orchestration and request/response handling live in `main_window.py`.
"""

from __future__ import annotations

import ast
import csv
from pathlib import Path
import logging
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from superqt import QRangeSlider

from .cf_templates import collapse_methods

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

FIELD_METADATA_SEPARATOR = "\x1f"
MAX_RECENT_FILES = 5


class CFVCore(QMainWindow):
    """Base window with GUI-only behavior and extension hooks for app logic."""

    def __init__(self) -> None:
        super().__init__()

        self.base_window_title = "cf-view (2026)"
        self.recent_log_path = Path.home() / ".cache" / "cfview" / "last_opened.log"
        self.setWindowTitle(self.base_window_title)
        self.resize(1000, 700)

        self.app_icon = self._create_app_icon()
        if not self.app_icon.isNull():
            self.setWindowIcon(self.app_icon)

        # Per-coordinate widget state used by worker-backed window subclasses.
        self.controls = {}
        self.selected_counts: dict[str, int] = {}
        self.selected_collapse_methods: dict[str, str] = {}
        self._plot_pixmap_original: QPixmap | None = None

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
        # Keep menus inside the window for consistent cross-platform behavior.
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(False)
        menu_bar.setStyleSheet(
            "QMenuBar {"
            " background-color: #186f4d;"
            " border-bottom: 1px solid #555;"
            " padding: 2px;"
            "}"
            "QMenuBar::item {"
            " color: #f0f0f0;"
            " padding: 4px 10px;"
            " background: transparent;"
            " border-radius: 4px;"
            "}"
            "QMenuBar::item:selected {"
            " background-color: #4a4a4a;"
            "}"
        )

        file_menu = menu_bar.addMenu("&File")

        open_action = QAction("Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._choose_file)
        file_menu.addAction(open_action)

        self.recent_menu = file_menu.addMenu("Recent")
        self._refresh_recent_menu()

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self._quit_application)

        file_menu.addAction(quit_action)

    def _refresh_recent_menu(self) -> None:
        """Refresh the Recent submenu from the persisted log file."""
        self.recent_menu.clear()
        recent_files = self._load_recent_files()

        if not recent_files:
            empty_action = QAction("No recent files", self)
            empty_action.setEnabled(False)
            self.recent_menu.addAction(empty_action)
            return

        for file_path in recent_files:
            action = QAction(Path(file_path).name, self)
            action.setToolTip(file_path)
            action.triggered.connect(lambda checked=False, p=file_path: self._open_recent_file(p))
            self.recent_menu.addAction(action)

    def _load_recent_files(self) -> list[str]:
        """Load recent files from disk and return a sanitized list."""
        if not self.recent_log_path.exists():
            return []

        try:
            lines = self.recent_log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            logger.exception("Failed to read recent files log: %s", self.recent_log_path)
            return []

        recent_files: list[str] = []
        for line in lines:
            path = line.strip()
            if not path or path in recent_files:
                continue
            recent_files.append(path)
            if len(recent_files) >= MAX_RECENT_FILES:
                break

        return recent_files

    def _save_recent_files(self, recent_files: list[str]) -> None:
        """Persist recent files list to disk."""
        self.recent_log_path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(recent_files) + "\n"
        self.recent_log_path.write_text(content, encoding="utf-8")

    def _record_recent_file(self, file_path: str) -> None:
        """Record a file open event and refresh the Recent submenu."""
        normalized_path = str(Path(file_path).expanduser())
        recent_files = [p for p in self._load_recent_files() if p != normalized_path]
        recent_files.insert(0, normalized_path)
        recent_files = recent_files[:MAX_RECENT_FILES]

        try:
            self._save_recent_files(recent_files)
        except OSError:
            logger.exception("Failed to save recent files log: %s", self.recent_log_path)
            return

        self._refresh_recent_menu()

    def _open_recent_file(self, file_path: str) -> None:
        """Open a file selected from the Recent submenu."""
        self._set_window_title_for_file(file_path)
        logger.info("Selected recent file: %s", file_path)
        self._record_recent_file(file_path)
        self.on_file_selected(file_path)

    def _create_left_panel(self) -> QWidget:
        """Create the left panel containing controls, field list, and sliders."""
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(self._create_field_list_area())
        left_layout.addWidget(self._create_slider_scroll_area())
        return left_panel

    def _create_field_list_area(self) -> QWidget:
        """Create field list plus a six-line details output area."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Fields")
        title.setStyleSheet("color: #9a9a9a; font-weight: 600;")
        self.field_list_widget = QListWidget()
        self.field_list_widget.itemClicked.connect(self.on_field_clicked)
        self._set_field_list_visible_rows(5)
        self._set_field_list_hint("Open a file to see fields")

        selection_header = QHBoxLayout()
        output_title = QLabel("Selection")
        output_title.setStyleSheet("color: #9a9a9a; font-weight: 600;")
        properties_button = QPushButton("Properties")
        properties_button.clicked.connect(self._show_selection_properties)

        selection_header.addWidget(output_title)
        selection_header.addStretch(1)
        selection_header.addWidget(properties_button)

        self.selection_output = QPlainTextEdit()
        self.selection_output.setReadOnly(True)
        self.selection_output.setPlaceholderText("Click a field to see details...")
        self.selection_output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.selection_output.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        line_height = self.selection_output.fontMetrics().lineSpacing()
        frame = self.selection_output.frameWidth() * 2
        margin = 10
        self.selection_output.setFixedHeight((line_height * 6) + frame + margin)

        layout.addWidget(title)
        layout.addWidget(self.field_list_widget)
        layout.addLayout(selection_header)
        layout.addWidget(self.selection_output)
        return container

    def _set_field_list_hint(self, text: str) -> None:
        """Show a non-selectable hint message in the fields list."""
        self.field_list_widget.clear()
        hint_item = QListWidgetItem(text)
        hint_item.setFlags(Qt.NoItemFlags)
        self.field_list_widget.addItem(hint_item)

    def _show_selection_properties(self) -> None:
        """Show properties for the currently selected field."""
        selected_item = self.field_list_widget.currentItem()
        if selected_item is None:
            self.status.showMessage("Select a field to view properties.")
            return

        selected_field = selected_item.text()
        raw_properties = selected_item.data(Qt.UserRole + 1)
        properties = self._parse_properties_dict(raw_properties)

        if not properties:
            self.status.showMessage("No properties available for this field.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Properties: {selected_field}")
        dialog.resize(700, 420)

        layout = QVBoxLayout(dialog)
        table = QTableWidget(dialog)
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Key", "Value"])
        table.setRowCount(len(properties))
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideRight)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.verticalHeader().setDefaultSectionSize(table.fontMetrics().height() + 6)
        table.verticalHeader().setMinimumSectionSize(table.fontMetrics().height() + 6)

        for row, (key, value) in enumerate(sorted(properties.items(), key=lambda kv: str(kv[0]).lower())):
            key_text = str(key)
            value_text = str(value)

            key_item = QTableWidgetItem(key_text)
            key_item.setToolTip(key_text)
            value_item = QTableWidgetItem(value_text)
            value_item.setToolTip(value_text)

            table.setItem(row, 0, key_item)
            table.setItem(row, 1, value_item)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        key_max_width = 260
        if table.columnWidth(0) > key_max_width:
            table.setColumnWidth(0, key_max_width)

        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)

        controls_row = QHBoxLayout()
        controls_row.addStretch(1)
        save_button = QPushButton("Save CSV...")
        save_button.clicked.connect(
            lambda: self._save_properties_to_csv(properties, selected_field, dialog)
        )
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        controls_row.addWidget(save_button)
        controls_row.addWidget(close_button)

        layout.addWidget(table)
        layout.addLayout(controls_row)
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.open()

    def _save_properties_to_csv(
        self,
        properties: dict[object, object],
        field_name: str,
        parent: QWidget | None = None,
    ) -> None:
        """Save properties dictionary to a CSV file with Key/Value columns."""
        safe_field_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in field_name)
        default_name = f"{safe_field_name or 'field'}_properties.csv"
        default_path = str(Path.home() / default_name)

        file_path, _ = QFileDialog.getSaveFileName(
            parent or self,
            "Save Properties as CSV",
            default_path,
            "CSV files (*.csv);;All files (*)",
        )
        if not file_path:
            return

        if not file_path.lower().endswith(".csv"):
            file_path += ".csv"

        rows = sorted(properties.items(), key=lambda kv: str(kv[0]).lower())
        with open(file_path, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["Key", "Value"])
            for key, value in rows:
                writer.writerow([str(key), str(value)])

        self.status.showMessage(f"Saved properties CSV: {file_path}")
        logger.info("Saved properties CSV: %s", file_path)

    def _parse_properties_dict(self, raw_properties: object) -> dict[object, object]:
        """Parse properties payload into a dictionary when possible."""
        logger.info("Parsing properties payload of type %s", type(raw_properties).__name__)
        logger.info("Raw properties content: %r", raw_properties)
        if isinstance(raw_properties, dict):
            return raw_properties

        if isinstance(raw_properties, str) and raw_properties.strip():
            text = raw_properties.strip()

            # Handle OrderedDict([...]) style string representations.
            if text.startswith("OrderedDict(") and text.endswith(")"):
                inner = text[len("OrderedDict(") : -1]
                try:
                    ordered_items = ast.literal_eval(inner)
                    if isinstance(ordered_items, list):
                        return dict(ordered_items)
                except (SyntaxError, ValueError, TypeError):
                    pass

            try:
                parsed = ast.literal_eval(text)
                if isinstance(parsed, dict):
                    return parsed
            except (SyntaxError, ValueError):
                pass

            fallback = self._parse_properties_lines(text)
            if fallback:
                return fallback

            logger.warning(
                "Could not parse properties payload into dict (type=%s, preview=%r)",
                type(raw_properties).__name__,
                text[:240],
            )

        return {}

    def _parse_properties_lines(self, text: str) -> dict[str, str]:
        """Parse key/value properties from multi-line text representations."""
        parsed: dict[str, str] = {}

        normalized = text.strip()
        if normalized.startswith("{") and normalized.endswith("}"):
            normalized = normalized[1:-1]

        raw_lines = normalized.splitlines()
        if len(raw_lines) == 1 and "," in normalized:
            raw_lines = normalized.split(",")

        for raw_line in raw_lines:
            line = raw_line.strip()
            if not line:
                continue

            line = line.strip("{}")

            if " = " in line:
                key, value = line.split(" = ", 1)
            elif ": " in line:
                key, value = line.split(": ", 1)
            elif ":" in line:
                key, value = line.split(":", 1)
            else:
                continue

            key = key.strip().strip("'\"")
            value = value.strip().strip(",").strip().strip("'\"")
            if key:
                parsed[key] = value

        return parsed

    def _set_field_list_visible_rows(self, row_count: int) -> None:
        """Size the field list to show a target number of rows by default."""
        row_height = self.field_list_widget.sizeHintForRow(0)
        if row_height <= 0:
            row_height = self.field_list_widget.fontMetrics().lineSpacing() + 6

        frame = self.field_list_widget.frameWidth() * 2
        height = (row_height * row_count) + frame
        self.field_list_widget.setMinimumHeight(height)
        self.field_list_widget.setMaximumHeight(height)

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

    def _create_plot_area(self) -> QWidget:
        """Create right-side plot frame plus plot-type summary and button."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_frame = QLabel("Waiting for data...")
        self.plot_frame.setAlignment(Qt.AlignCenter)
        # Ignore pixmap size hints so large rendered plots do not force window growth.
        self.plot_frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.plot_frame.setMinimumSize(120, 120)
        self.plot_frame.setStyleSheet("background-color: #222; color: #888; border: 1px solid #444;")

        summary_row = QHBoxLayout()
        self.plot_summary_label = QLabel("Open a field to inspect plot options.")
        self.plot_button = QPushButton("Plot")
        self.plot_button.setEnabled(False)
        self.plot_button.clicked.connect(self._on_plot_button_clicked)
        self.options_button = QPushButton("Options")
        self.options_button.setEnabled(False)
        self.save_code_button = QPushButton("Save Code...")
        self.save_code_button.setEnabled(False)
        self.save_code_button.clicked.connect(self._on_save_code_button_clicked)
        self.save_plot_button = QPushButton("Save Plot...")
        self.save_plot_button.setEnabled(False)
        self.save_plot_button.clicked.connect(self._on_save_plot_button_clicked)

        summary_row.addWidget(self.plot_summary_label, 1)
        summary_row.addWidget(self.plot_button)
        summary_row.addWidget(self.options_button)
        summary_row.addWidget(self.save_code_button)
        summary_row.addWidget(self.save_plot_button)

        layout.addWidget(self.plot_frame, 1)
        layout.addLayout(summary_row)
        return container

    def _on_plot_button_clicked(self) -> None:
        """Request a plot refresh when the current selection is plottable."""
        if not getattr(self, "plot_button", None) or not self.plot_button.isEnabled():
            return
        self._request_plot_update()

    def set_plot_image(self, png_bytes: bytes) -> None:
        """Render PNG bytes from worker output into the plot frame."""
        if not png_bytes:
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(png_bytes, "PNG"):
            logger.warning("Failed to decode plot PNG payload")
            return

        self._plot_pixmap_original = pixmap
        self._fit_window_to_plot_aspect()
        self._refresh_plot_pixmap()

    def _fit_window_to_plot_aspect(self) -> None:
        """Nudge window height to match plot aspect ratio without exceeding screen bounds."""
        if self._plot_pixmap_original is None:
            return

        plot_height = self._plot_pixmap_original.height()
        plot_width = self._plot_pixmap_original.width()
        if plot_height <= 0 or plot_width <= 0:
            return

        aspect_ratio = plot_width / plot_height
        current_plot_width = max(self.plot_frame.width(), 1)
        desired_plot_height = max(1, int(current_plot_width / aspect_ratio))
        current_plot_height = max(self.plot_frame.height(), 1)
        height_delta = desired_plot_height - current_plot_height

        # Avoid jitter from tiny adjustments.
        if abs(height_delta) < 12:
            return

        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return

        available_height = screen.availableGeometry().height()
        min_height = max(self.minimumHeight(), 420)
        max_height = max(min_height, int(available_height * 0.9))
        target_height = max(min_height, min(self.height() + height_delta, max_height))

        if target_height != self.height():
            self.resize(self.width(), target_height)

    def _refresh_plot_pixmap(self) -> None:
        """Scale current plot pixmap to fit the visible plot frame."""
        if self._plot_pixmap_original is None:
            return

        target_size = self.plot_frame.size()
        if target_size.width() <= 0 or target_size.height() <= 0:
            return

        scaled = self._plot_pixmap_original.scaled(
            target_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.plot_frame.setPixmap(scaled)
        self.plot_frame.setText("")

    def _on_save_code_button_clicked(self) -> None:
        """Prompt for destination file and request worker-side plot code save."""
        if not getattr(self, "save_code_button", None) or not self.save_code_button.isEnabled():
            return

        default_path = str(Path.home() / "cfview_plot_code.py")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Plot Code",
            default_path,
            "Python files (*.py);;Text files (*.txt);;All files (*)",
        )
        if not file_path:
            return

        if not Path(file_path).suffix:
            file_path += ".py"

        self._request_plot_code_save(file_path)

    def _on_save_plot_button_clicked(self) -> None:
        """Prompt for destination image file and request worker-side plot save."""
        if not getattr(self, "save_plot_button", None) or not self.save_plot_button.isEnabled():
            return

        default_path = str(Path.home() / "cfview_plot.png")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Plot",
            default_path,
            "PNG files (*.png);;PDF files (*.pdf);;PostScript files (*.ps);;All files (*)",
        )
        if not file_path:
            return

        if not Path(file_path).suffix:
            file_path += ".png"

        self._request_plot_save(file_path)

    def _setup_status_bar(self) -> None:
        """Create and initialize the status bar."""
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("System Ready. Initialize S3 Load.")

    def populate_field_list(self, fields: Sequence[object]) -> None:
        """Populate the field list UI from worker metadata."""
        self.field_list_widget.clear()

        for field in fields:
            if isinstance(field, str) and FIELD_METADATA_SEPARATOR in field:
                parts = field.split(FIELD_METADATA_SEPARATOR, 2)
                identity = parts[0]
                detail = parts[1] if len(parts) > 1 else parts[0]
                properties = parts[2] if len(parts) > 2 else ""
            elif isinstance(field, (tuple, list)) and len(field) >= 2:
                # Backward compatibility if tuple payloads are still encountered.
                identity = str(field[0])
                detail = str(field[1])
                properties = str(field[2]) if len(field) > 2 else ""
            else:
                identity = str(field)
                detail = str(field)
                properties = ""

            item = QListWidgetItem(identity)
            item.setData(Qt.UserRole, detail)
            item.setData(Qt.UserRole + 1, properties)
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
        """Build compact dual-handle range sliders from coordinate metadata."""
        self.controls.clear()
        self.selected_counts.clear()
        self.selected_collapse_methods.clear()

        for i in reversed(range(self.sidebar.count())):
            widget = self.sidebar.itemAt(i).widget()
            if widget is not None:
                widget.setParent(None)

        for name, values in metadata.items():
            if not values:
                continue

            container = QWidget()
            row = QVBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)

            header_row = QHBoxLayout()
            name_label = QLabel(f"{name.upper()}:")
            collapse_label = QLabel("collapse")
            collapse_checkbox = QCheckBox("")
            collapse_checkbox.setToolTip("Select a collapse method")
            collapse_checkbox.toggled.connect(
                lambda checked, n=name: self.on_collapse_toggled(n, checked)
            )

            header_row.addWidget(name_label)
            header_row.addStretch(1)
            header_row.addWidget(collapse_label)
            header_row.addWidget(collapse_checkbox)

            selection_label = QLabel()
            selection_label.setWordWrap(True)

            # Show the fixed coordinate bounds around the slider track.
            bounds_start_label = QLabel(str(values[0]))
            bounds_end_label = QLabel(str(values[-1]))
            bounds_start_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            bounds_end_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            slider = QRangeSlider(Qt.Horizontal)
            slider.setRange(0, len(values) - 1)
            slider.setValue((0, len(values) - 1))
            slider.valueChanged.connect(
                lambda _value, n=name: self.on_range_slider_moved(n)
            )

            slider_row = QHBoxLayout()
            slider_row.addWidget(bounds_start_label)
            slider_row.addWidget(slider, 1)
            slider_row.addWidget(bounds_end_label)

            row.addLayout(header_row)
            row.addWidget(selection_label)
            row.addLayout(slider_row)
            self.sidebar.addWidget(container)
            self.controls[name] = {
                "range_slider": slider,
                "name_label": name_label,
                "selection_label": selection_label,
                "bounds_start_label": bounds_start_label,
                "bounds_end_label": bounds_end_label,
                "collapse_checkbox": collapse_checkbox,
                "values": values,
            }

            self._update_range_labels(name)

        self._refresh_plot_summary()
        logger.info("Built %d dynamic sliders", len(self.controls))

    def on_range_slider_moved(self, name: str) -> None:
        """Handle dual-handle range slider movement."""
        control = self.controls.get(name)
        if control is None:
            return

        slider = control["range_slider"]
        start_idx, end_idx = slider.value()

        self._update_range_labels(name)
        self._refresh_plot_summary()
        logger.debug("Range slider moved: %s start=%d end=%d", name, start_idx, end_idx)

    def on_collapse_toggled(self, name: str, checked: bool) -> None:
        """Choose and persist a collapse method for the coordinate."""
        control = self.controls.get(name)
        if control is None:
            return

        collapse_checkbox = control["collapse_checkbox"]
        if checked:
            if not collapse_methods:
                collapse_checkbox.blockSignals(True)
                collapse_checkbox.setChecked(False)
                collapse_checkbox.blockSignals(False)
                self.selected_collapse_methods.pop(name, None)
                collapse_checkbox.setText("")
                self.status.showMessage("No collapse methods configured.")
                return

            current_method = self.selected_collapse_methods.get(name, collapse_methods[0])
            current_index = (
                collapse_methods.index(current_method)
                if current_method in collapse_methods
                else 0
            )
            method, ok = QInputDialog.getItem(
                self,
                "Collapse Method",
                f"Select collapse method for {name}:",
                collapse_methods,
                current_index,
                False,
            )
            if ok and method:
                self.selected_collapse_methods[name] = method
                collapse_checkbox.setText(f"({method})")
            else:
                collapse_checkbox.blockSignals(True)
                collapse_checkbox.setChecked(False)
                collapse_checkbox.blockSignals(False)
                self.selected_collapse_methods.pop(name, None)
                collapse_checkbox.setText("")
                return
        else:
            self.selected_collapse_methods.pop(name, None)
            collapse_checkbox.setText("")

        self._update_range_labels(name)
        self._refresh_plot_summary()

    def _update_range_labels(self, name: str) -> None:
        """Refresh compact summary line for current range selection."""
        control = self.controls.get(name)
        if control is None:
            return

        values = control["values"]
        start_idx, end_idx = control["range_slider"].value()
        lo_idx = min(start_idx, end_idx)
        hi_idx = max(start_idx, end_idx)
        selected_count = hi_idx - lo_idx
        self.selected_counts[name] = selected_count

        control["bounds_start_label"].setText(str(values[0]))
        control["bounds_end_label"].setText(str(values[-1]))
        control["selection_label"].setText(
            f"selected: {values[lo_idx]}..{values[hi_idx]} ({selected_count})"
        )

    def _refresh_plot_summary(self) -> None:
        """Update plot summary text and plot button availability."""
        if not self.controls:
            self.plot_summary_label.setText("Open a field to inspect plot options.")
            self.plot_button.setEnabled(False)
            self.options_button.setEnabled(False)
            self.save_code_button.setEnabled(False)
            self.save_plot_button.setEnabled(False)
            return

        dims: list[int] = []
        for name, control in self.controls.items():
            if name in self.selected_collapse_methods:
                dims.append(1)
                continue

            start_idx, end_idx = control["range_slider"].value()
            dims.append(1 if start_idx == end_idx else 2)

        varying_dims = sum(1 for dim in dims if dim != 1)
        dims_text = f"Selection dimensions = {dims}"

        if varying_dims == 0:
            self.plot_summary_label.setText(f"{dims_text} Total collapse, plot not possible")
            self.plot_button.setEnabled(False)
            self.options_button.setEnabled(False)
            self.save_code_button.setEnabled(False)
            self.save_plot_button.setEnabled(False)
        elif varying_dims == 1:
            self.plot_summary_label.setText(f"{dims_text} Lineplot possible")
            self.plot_button.setEnabled(True)
            self.options_button.setEnabled(True)
            self.save_code_button.setEnabled(True)
            self.save_plot_button.setEnabled(True)
        elif varying_dims == 2:
            self.plot_summary_label.setText(f"{dims_text} Contour possible")
            self.plot_button.setEnabled(True)
            self.options_button.setEnabled(True)
            self.save_code_button.setEnabled(True)
            self.save_plot_button.setEnabled(True)
        else:
            self.plot_summary_label.setText(
                f"{dims_text} Need to reduce to 1 or 2 dimensions before plotting"
            )
            self.plot_button.setEnabled(False)
            self.options_button.setEnabled(False)
            self.save_code_button.setEnabled(False)
            self.save_plot_button.setEnabled(False)

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

        self._set_window_title_for_file(file_path)
        logger.info("Selected file: %s", file_path)
        self._record_recent_file(file_path)
        self.on_file_selected(file_path)

    def _set_window_title_for_file(self, file_path: str) -> None:
        """Update the window title to reflect the selected file."""
        filename = Path(file_path).name
        self.setWindowTitle(f"{self.base_window_title}: {filename}")

    def on_file_selected(self, file_path: str) -> None:
        """Hook for worker-backed implementations after file selection."""
        logger.debug("File selected in core UI: %s", file_path)

    def _request_plot_update(self) -> None:
        """Hook for worker-backed implementations."""

    def _request_plot_code_save(self, file_path: str) -> None:
        """Hook for worker-backed implementations to save generated plot code."""
        logger.debug("Requested plot code save to: %s", file_path)

    def _request_plot_save(self, file_path: str) -> None:
        """Hook for worker-backed implementations to save rendered plot output."""
        logger.debug("Requested plot save to: %s", file_path)

    def _quit_application(self) -> None:
        """Quit the whole application, even when modal dialogs are open."""
        logger.info("Quit requested from UI")
        app = QApplication.instance()
        if app is None:
            self.close()
            return

        app.closeAllWindows()
        app.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Ensure tray resources are released when the GUI exits."""
        if getattr(self, "tray_icon", None) is not None:
            self.tray_icon.hide()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Keep rendered plot image scaled when the window is resized."""
        super().resizeEvent(event)
        self._refresh_plot_pixmap()
