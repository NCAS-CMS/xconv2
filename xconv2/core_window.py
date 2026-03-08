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

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDesktopServices, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
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
from .colour_scales import cscales, get_colour_scale_hexes
from .ui.menu_controller import MenuController
from .ui.settings_store import SettingsStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

FIELD_METADATA_SEPARATOR = "\x1f"
DEFAULT_MAX_RECENT_FILES = 10
SETTINGS_VERSION = 1


class CFVCore(QMainWindow):
    """Base window with GUI-only behavior and extension hooks for app logic."""

    def __init__(self) -> None:
        super().__init__()

        self.base_window_title = "xconv2"
        self.current_file_path: str | None = None
        self.settings_path = Path.home() / ".config" / "cfview" / "settings.json"
        self.recent_log_path = Path.home() / ".cache" / "cfview" / "last_opened.log"
        self.settings_store = SettingsStore(
            settings_path=self.settings_path,
            recent_log_path=self.recent_log_path,
            settings_version=SETTINGS_VERSION,
            default_max_recent_files=DEFAULT_MAX_RECENT_FILES,
        )
        self.menu_controller = MenuController(self)
        self._settings = self._load_settings()
        self.setWindowTitle(self.base_window_title)
        self.resize(1000, 700)

        self.app_icon = self._create_app_icon()
        if not self.app_icon.isNull():
            self.setWindowIcon(self.app_icon)

        # Per-coordinate widget state used by worker-backed window subclasses.
        self.controls = {}
        self.selected_counts: dict[str, int] = {}
        self.selected_collapse_methods: dict[str, str] = {}
        self.plot_options_by_kind: dict[str, dict[str, object]] = {}
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
        self.menu_controller.setup_menu_bar()

    def _setup_help_menu(self, menu_bar, menu_font_size_px: int, menu_font_weight: int) -> None:
        """Attach Help pinned to the right while left-side menus grow normally."""
        self.menu_controller._setup_help_menu(menu_bar, menu_font_size_px, menu_font_weight)

    def _show_about_dialog(self) -> None:
        """Show application identity and runtime details."""
        dialog = QDialog(self)
        dialog.setWindowTitle("About xconv")
        dialog.resize(560, 320)

        layout = QVBoxLayout(dialog)

        heading = QLabel(
            "<h2 style='margin:0;'>xconv2</h2>"
            "<p style='margin-top:8px;'>"
            "High-performance data viewer and simple data converter."
            "</p>"
            "<p> Provides a graphical interface to explore and plot CF-compliant (and near compliant) datasets, " 
            "stored in NetCDF, Zarr, or Met Office pp/fields files. Supports file conversion and simple" 
            "data manipulation. All data saved will be CF-compliant." 
            "</p>"
            "<p>Maintained by NCAS-Computational Modelling Services (NCAS-CMS) at the University of Reading. "
            "Powered by cf-python, pyfive, and Dask."
            "</p>"
        )
        heading.setTextFormat(Qt.RichText)
        heading.setWordWrap(True)

        header_row = QHBoxLayout()
        header_row.addWidget(
            self._build_about_logo_label(
                "cf-python",
                ["cf-logo.png", "cf-logo.svg", "cf-python-logo.png", "cf-python-logo.svg"],
                112,
            )
        )
        header_row.addWidget(heading, 1)

        logos_row = QHBoxLayout()
        logo_specs = [
            ("NCAS", ["ncas-logo.png", "ncas-logo.svg", "ncas.png", "ncas.svg"]),
            ("University of Reading", ["UoR-logo.png", "UoR-logo.svg"]),
            ("PyFive", ["pyfive-logo.png", "pyfive-logo.svg", "pyfive.png", "pyfive.svg"]),
            ("Dask", ["dask-logo.png", "dask-logo.svg", "dask.png", "dask.svg"]),
        ]

        for display_name, candidates in logo_specs:
            logos_row.addWidget(self._build_about_logo_label(display_name, candidates, 45))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)

        layout.addLayout(header_row)
        layout.addLayout(logos_row)
        layout.addWidget(buttons)

        dialog.exec()

    def _build_about_logo_label(
        self,
        display_name: str,
        candidates: Sequence[str],
        max_height: int,
    ) -> QLabel:
        """Build a single logo tile for the About dialog."""
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumHeight(max_height + 14)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        logo_path = self._find_about_logo_path(candidates)
        if logo_path is not None:
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                label.setPixmap(
                    pixmap.scaledToHeight(max_height, Qt.SmoothTransformation)
                )
                label.setToolTip(display_name)
                return label

        label.setText(display_name)
        label.setToolTip(f"Missing logo asset for {display_name}")
        label.setStyleSheet(
            "QLabel {"
            " border: 1px dashed #666;"
            " border-radius: 6px;"
            " color: #b0b0b0;"
            " padding: 6px;"
            "}"
        )
        return label

    def _find_about_logo_path(self, candidates: Sequence[str]) -> Path | None:
        """Return the first existing logo asset path from a list of candidates."""
        assets_dir = Path(__file__).resolve().parent / "assets"
        for name in candidates:
            path = assets_dir / name
            if path.exists() and path.is_file():
                return path
        return None

    def _open_issue_tracker(self) -> None:
        """Open the project issue tracker in the default browser."""
        issues_url = QUrl("https://github.com/NCAS-CMS/xconv2/issues")
        if not QDesktopServices.openUrl(issues_url):
            self.status.showMessage("Unable to open issue tracker URL.")
            logger.warning("Failed to open issue tracker URL: %s", issues_url.toString())

    def _refresh_recent_menu(self) -> None:
        """Refresh the Recent submenu from the persisted log file."""
        self.menu_controller.refresh_recent_menu()

    def _load_recent_files(self) -> list[str]:
        """Load recent files from JSON settings and return a sanitized list."""
        return self.settings_store.load_recent_files()

    def _save_recent_files(self, recent_files: list[str]) -> None:
        """Persist recent files list to JSON settings."""
        self.settings_store.save_recent_files(recent_files)
        self._settings = self.settings_store.data

    def _record_recent_file(self, file_path: str) -> None:
        """Record a file open event and refresh the Recent submenu."""
        try:
            self.settings_store.record_recent_file(file_path)
            self._settings = self.settings_store.data
        except OSError:
            logger.exception("Failed to save recent files log: %s", self.recent_log_path)
            return

        self._refresh_recent_menu()

    def _default_settings(self) -> dict[str, object]:
        """Return default persisted settings schema."""
        return self.settings_store.default_settings()

    def _load_recent_files_legacy(self, settings: dict[str, object] | None = None) -> list[str]:
        """Load old newline-based recent-file log for one-time settings migration."""
        return self.settings_store.load_recent_files_legacy(settings)

    def _load_settings(self) -> dict[str, object]:
        """Load JSON settings with sane defaults and legacy migration."""
        self._settings = self.settings_store.load()
        return self._settings

    def _save_settings(self) -> None:
        """Persist settings dictionary to disk as JSON."""
        self.settings_store.data = self._settings
        self.settings_store.save()

    def _default_save_path(self, settings_key: str, filename: str) -> str:
        """Build default save-file path from last-used directory setting."""
        return self.settings_store.default_save_path(settings_key, filename)

    def _max_recent_files(self, settings: dict[str, object] | None = None) -> int:
        """Return validated max recent-files value from settings."""
        return self.settings_store.max_recent_files(settings)

    def _show_settings_dialog(self) -> None:
        """Show basic settings editor for persisted app preferences."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.resize(640, 220)

        layout = QVBoxLayout(dialog)

        recent_row = QHBoxLayout()
        recent_label = QLabel("How many recent files to keep")
        recent_spin = QSpinBox()
        recent_spin.setRange(1, 100)
        recent_spin.setValue(self._max_recent_files())
        recent_row.addWidget(recent_label)
        recent_row.addStretch(1)
        recent_row.addWidget(recent_spin)

        code_dir_row = QHBoxLayout()
        code_dir_label = QLabel("Default folder for Save Code")
        code_dir_edit = QLineEdit(str(self._settings.get("last_save_code_dir", str(Path.home()))))
        code_dir_browse = QPushButton("Browse...")

        def _choose_code_dir() -> None:
            selected = QFileDialog.getExistingDirectory(
                dialog,
                "Select default Save Code folder",
                code_dir_edit.text().strip() or str(Path.home()),
            )
            if selected:
                code_dir_edit.setText(selected)

        code_dir_browse.clicked.connect(_choose_code_dir)
        code_dir_row.addWidget(code_dir_label)
        code_dir_row.addWidget(code_dir_edit, 1)
        code_dir_row.addWidget(code_dir_browse)

        plot_dir_row = QHBoxLayout()
        plot_dir_label = QLabel("Default folder for Save Plot")
        plot_dir_edit = QLineEdit(str(self._settings.get("last_save_plot_dir", str(Path.home()))))
        plot_dir_browse = QPushButton("Browse...")

        def _choose_plot_dir() -> None:
            selected = QFileDialog.getExistingDirectory(
                dialog,
                "Select default Save Plot folder",
                plot_dir_edit.text().strip() or str(Path.home()),
            )
            if selected:
                plot_dir_edit.setText(selected)

        plot_dir_browse.clicked.connect(_choose_plot_dir)
        plot_dir_row.addWidget(plot_dir_label)
        plot_dir_row.addWidget(plot_dir_edit, 1)
        plot_dir_row.addWidget(plot_dir_browse)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        layout.addLayout(recent_row)
        layout.addLayout(code_dir_row)
        layout.addLayout(plot_dir_row)
        layout.addStretch(1)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        self._settings["max_recent_files"] = int(recent_spin.value())

        code_dir = Path(code_dir_edit.text().strip() or str(Path.home())).expanduser()
        if not code_dir.is_dir():
            code_dir = Path.home()
        self._settings["last_save_code_dir"] = str(code_dir)

        plot_dir = Path(plot_dir_edit.text().strip() or str(Path.home())).expanduser()
        if not plot_dir.is_dir():
            plot_dir = Path.home()
        self._settings["last_save_plot_dir"] = str(plot_dir)

        # Clamp existing stored list to the updated configured size.
        recent_files = self._load_recent_files()
        self._settings["recent_files"] = recent_files

        try:
            self._save_settings()
        except OSError:
            logger.exception("Failed to save settings from dialog")
            self.status.showMessage("Failed to save settings")
            return

        self._refresh_recent_menu()
        self.status.showMessage("Settings saved")

    def _remember_last_save_dir(self, settings_key: str, file_path: str) -> None:
        """Persist the parent folder of a just-saved file for future defaults."""
        self._settings = self.settings_store.data
        self._settings[settings_key] = str(Path(file_path).expanduser().parent)
        try:
            self._save_settings()
        except OSError:
            logger.exception("Failed to save settings key %s", settings_key)

    def _open_recent_file(self, file_path: str) -> None:
        """Open a file selected from the Recent submenu."""
        self._set_window_title_for_file(file_path)
        logger.info("Selected recent file: %s", file_path)
        self._record_recent_file(file_path)
        self.on_file_selected(file_path)

    def _create_left_panel(self) -> QWidget:
        """Create the left panel with framed Fields and Selection sections."""
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._create_fields_frame())
        left_layout.addWidget(self._create_selection_frame(), 1)
        return left_panel

    def _create_fields_frame(self) -> QGroupBox:
        """Create framed fields list section."""
        frame = QGroupBox("Fields")
        layout = QVBoxLayout(frame)

        self.field_list_widget = QListWidget()
        self.field_list_widget.itemClicked.connect(self.on_field_clicked)
        self._set_field_list_visible_rows(5)
        self._set_field_list_hint("Open a file to see fields")

        layout.addWidget(self.field_list_widget)
        return frame

    def _create_selection_frame(self) -> QGroupBox:
        """Create framed selection details and slider controls section."""
        frame = QGroupBox("Selection")
        layout = QVBoxLayout(frame)

        self.selection_output = QPlainTextEdit()
        self.selection_output.setReadOnly(True)
        self.selection_output.setPlaceholderText("Click a field to see details...")
        self.selection_output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.selection_output.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        line_height = self.selection_output.fontMetrics().lineSpacing()
        frame_width = self.selection_output.frameWidth() * 2
        margin = 10
        self.selection_output.setFixedHeight((line_height * 6) + frame_width + margin)

        controls_row = QHBoxLayout()
        properties_button = QPushButton("Properties")
        properties_button.clicked.connect(self._show_selection_properties)
        reset_button = QPushButton("Reset all sliders")
        reset_button.setToolTip("Reset all range sliders to full coordinate extent")
        reset_button.clicked.connect(self._reset_all_sliders)
        controls_row.addWidget(properties_button)
        controls_row.addWidget(reset_button)
        controls_row.addStretch(1)

        layout.addWidget(self.selection_output)
        layout.addLayout(controls_row)
        layout.addWidget(self._create_slider_scroll_area(), 1)
        return frame

    def _create_field_list_area(self) -> QWidget:
        """Backward-compat shim kept for now; use framed builders above."""
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

    def _reset_all_sliders(self) -> None:
        """Reset all slider ranges to full extent and refresh summary state."""
        for name, control in self.controls.items():
            slider = control.get("range_slider")
            values = control.get("values", [])
            if slider is None or not values:
                continue

            slider.blockSignals(True)
            slider.setValue((0, len(values) - 1))
            slider.blockSignals(False)

            self._update_range_labels(name)

        self._refresh_plot_summary()

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
        self.sidebar.setContentsMargins(6, 0, 6, 0)
        self.sidebar.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        sidebar_container = QWidget()
        sidebar_container.setLayout(self.sidebar)
        scroll.setWidget(sidebar_container)
        scroll.setMinimumWidth(300)
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
        self.options_button.clicked.connect(self._on_options_button_clicked)
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

    def _on_options_button_clicked(self) -> None:
        """Request plot-type specific options from worker/UI flow."""
        if not getattr(self, "options_button", None) or not self.options_button.isEnabled():
            return
        self._request_plot_options()

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

        default_path = self._default_save_path("last_save_code_dir", "cfview_plot_code.py")
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

        self._remember_last_save_dir("last_save_code_dir", file_path)
        self._request_plot_code_save(file_path)

    def _on_save_plot_button_clicked(self) -> None:
        """Prompt for destination image file and request worker-side plot save."""
        if not getattr(self, "save_plot_button", None) or not self.save_plot_button.isEnabled():
            return

        default_path = self._default_save_path("last_save_plot_dir", "cfview_plot.png")
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

        self._remember_last_save_dir("last_save_plot_dir", file_path)
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
            row.setSpacing(2)

            header_row = QHBoxLayout()
            header_row.setContentsMargins(0, 0, 0, 0)
            header_row.setSpacing(4)
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
            selection_label.setContentsMargins(0, 0, 0, 0)

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
            slider_row.setContentsMargins(0, 0, 0, 0)
            slider_row.setSpacing(4)
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
        lo_idx = int(min(start_idx, end_idx))
        hi_idx = int(max(start_idx, end_idx))
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
            lo_idx = int(min(start_idx, end_idx))
            hi_idx = int(max(start_idx, end_idx))
            dims.append(1 if (hi_idx - lo_idx) <= 1 else 2)

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
        self.current_file_path = file_path
        filename = Path(file_path).name
        self.setWindowTitle(f"{self.base_window_title}: {filename}")

    def on_file_selected(self, file_path: str) -> None:
        """Hook for worker-backed implementations after file selection."""
        logger.debug("File selected in core UI: %s", file_path)

    def _request_plot_update(self) -> None:
        """Hook for worker-backed implementations."""

    def _request_plot_options(self) -> None:
        """Hook for worker-backed implementations to fetch option context."""

    def _show_contour_options_dialog(
        self,
        range_min: float,
        range_max: float,
        suggested_title: str | None = None,
    ) -> None:
        """Show contour options dialog and persist selected options."""
        existing = self.plot_options_by_kind.get("contour", {})

        dialog = QDialog(self)
        dialog.setWindowTitle("Contour Options")
        dialog.resize(520, 280)

        layout = QVBoxLayout(dialog)

        default_title = existing.get("title")
        if not default_title:
            default_title = suggested_title
        if not default_title:
            default_title = Path(self.current_file_path).name if self.current_file_path else ""
        default_page_title = existing.get("page_title")
        if not default_page_title:
            default_page_title = Path(self.current_file_path).name if self.current_file_path else ""

        titles_group = QGroupBox("Titles")
        titles_layout = QVBoxLayout(titles_group)

        title_row = QHBoxLayout()
        title_label = QLabel("contour title")
        title_edit = QLineEdit(str(default_title))
        title_edit.setPlaceholderText("Contour title")
        title_row.addWidget(title_label)
        title_row.addWidget(title_edit, 1)
        titles_layout.addLayout(title_row)

        page_title_row = QHBoxLayout()
        page_title_label = QLabel("page title")
        page_title_edit = QLineEdit(str(default_page_title))
        page_title_edit.setPlaceholderText("Figure page title")
        page_title_display_checkbox = QCheckBox("display")
        page_title_display_checkbox.setChecked(bool(existing.get("page_title_display", False)))
        page_title_row.addWidget(page_title_label)
        page_title_row.addWidget(page_title_edit, 1)
        page_title_row.addWidget(page_title_display_checkbox)
        titles_layout.addLayout(page_title_row)

        annotations_group = QGroupBox("Choose annotation properties")
        annotations_layout = QVBoxLayout(annotations_group)

        selected_annotation_props: list[tuple[str, str]] = []
        existing_props = existing.get("annotation_properties", [])
        if isinstance(existing_props, list):
            for entry in existing_props:
                if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                    selected_annotation_props.append((str(entry[0]), str(entry[1])))

        free_text_row = QHBoxLayout()
        free_text_label = QLabel("free text")
        free_text_edit = QLineEdit(str(existing.get("annotation_free_text", "")))
        free_text_edit.setPlaceholderText("Optional custom annotation text")
        free_text_row.addWidget(free_text_label)
        free_text_row.addWidget(free_text_edit, 1)
        annotations_layout.addLayout(free_text_row)

        annotation_limit_label = QLabel()
        annotation_limit_label.setStyleSheet("color: #666;")

        def _refresh_annotation_limit_hint() -> None:
            max_selected = 3 if free_text_edit.text().strip() else 4
            annotation_limit_label.setText(
                f"Annotation property limit: {max_selected}"
            )

        free_text_edit.textChanged.connect(lambda _text: _refresh_annotation_limit_hint())
        _refresh_annotation_limit_hint()
        annotations_layout.addWidget(annotation_limit_label)

        top_margin_spin = QDoubleSpinBox()
        top_margin_spin.setRange(0.0, 0.20)
        top_margin_spin.setDecimals(3)
        top_margin_spin.setSingleStep(0.005)
        top_margin_spin.setValue(float(existing.get("page_margin_top", 0.0) or 0.0))
        top_margin_spin.setToolTip("Extra figure-fraction space above plot for page title")

        bottom_margin_spin = QDoubleSpinBox()
        bottom_margin_spin.setRange(0.0, 0.20)
        bottom_margin_spin.setDecimals(3)
        bottom_margin_spin.setSingleStep(0.005)
        bottom_margin_spin.setValue(float(existing.get("page_margin_bottom", 0.0) or 0.0))
        bottom_margin_spin.setToolTip("Extra figure-fraction space below plot for annotations")

        annotation_row = QHBoxLayout()
        choose_annotations_button = QPushButton("Select annotations from properties")
        annotation_display_checkbox = QCheckBox("display annotations")
        annotation_display_checkbox.setChecked(bool(existing.get("annotation_display", False)))

        annotation_preview = QLabel()
        annotation_preview.setWordWrap(True)
        annotation_preview.setStyleSheet("color: #444;")

        def _refresh_annotation_preview() -> None:
            if not selected_annotation_props:
                annotation_preview.setText("No annotation properties selected")
                return
            annotation_preview.setText(
                "\n".join(f"{key}: {value}" for key, value in selected_annotation_props)
            )

        def _maybe_enable_annotation_display() -> None:
            has_free_text = bool(free_text_edit.text().strip())
            has_props = bool(selected_annotation_props)
            if has_free_text or has_props:
                annotation_display_checkbox.setChecked(True)

        def _choose_annotation_properties() -> None:
            selected_item = self.field_list_widget.currentItem()
            if selected_item is None:
                self.status.showMessage("Select a field before choosing annotation properties")
                return

            raw_properties = selected_item.data(Qt.UserRole + 1)
            properties = self._parse_properties_dict(raw_properties)
            if not properties:
                self.status.showMessage("No properties available for annotation")
                return

            max_selected = 3 if free_text_edit.text().strip() else 4
            if len(selected_annotation_props) > max_selected:
                selected_annotation_props[:] = selected_annotation_props[:max_selected]

            chosen = self._show_annotation_properties_chooser(
                properties,
                selected_annotation_props,
                max_selected=max_selected,
            )
            if chosen is not None:
                selected_annotation_props.clear()
                selected_annotation_props.extend(chosen)
                _refresh_annotation_preview()
                _maybe_enable_annotation_display()

        choose_annotations_button.clicked.connect(_choose_annotation_properties)
        free_text_edit.textChanged.connect(lambda _text: _maybe_enable_annotation_display())
        _refresh_annotation_preview()

        annotation_row.addWidget(choose_annotations_button)
        annotation_row.addStretch(1)
        annotation_row.addWidget(annotation_display_checkbox)
        annotations_layout.addLayout(annotation_row)
        annotations_layout.addWidget(annotation_preview)

        margin_row = QHBoxLayout()
        layout_label = QLabel("Layout:")
        top_margin_label = QLabel("top margin")
        bottom_margin_label = QLabel("bottom margin")
        margin_row.addWidget(layout_label)
        margin_row.addWidget(top_margin_label)
        margin_row.addWidget(top_margin_spin)
        margin_row.addSpacing(10)
        margin_row.addWidget(bottom_margin_label)
        margin_row.addWidget(bottom_margin_spin)
        margin_row.addStretch(1)
        annotations_layout.addLayout(margin_row)

        levels_group = QGroupBox("Contour levels")
        levels_layout = QVBoxLayout(levels_group)
        levels_layout.addWidget(QLabel(f"Field range: min={range_min:g}, max={range_max:g}"))

        default_radio = QRadioButton("Default - let matplotlib decide")
        auto_radio = QRadioButton("Use min/max + intervals")
        explicit_radio = QRadioButton("Use explicit contour levels (comma-separated)")
        mode_group = QButtonGroup(dialog)
        mode_group.addButton(default_radio)
        mode_group.addButton(auto_radio)
        mode_group.addButton(explicit_radio)

        auto_row = QHBoxLayout()
        min_label = QLabel("min")
        min_edit = QLineEdit(str(existing.get("min", range_min)))
        max_label = QLabel("max")
        max_edit = QLineEdit(str(existing.get("max", range_max)))
        intervals_label = QLabel("intervals")
        intervals_spin = QSpinBox()
        intervals_spin.setRange(1, 200)
        intervals_spin.setValue(int(existing.get("intervals", 12)))

        auto_row.addWidget(min_label)
        auto_row.addWidget(min_edit)
        auto_row.addWidget(max_label)
        auto_row.addWidget(max_edit)
        auto_row.addWidget(intervals_label)
        auto_row.addWidget(intervals_spin)

        explicit_levels = existing.get("levels", [])
        explicit_levels_text = ""
        if isinstance(explicit_levels, list):
            explicit_levels_text = ", ".join(str(v) for v in explicit_levels)
        explicit_edit = QLineEdit(explicit_levels_text)
        explicit_edit.setPlaceholderText("e.g. -2, -1, 0, 1, 2")

        if existing.get("mode") == "explicit":
            explicit_radio.setChecked(True)
        elif existing.get("mode") == "auto":
            auto_radio.setChecked(True)
        else:
            default_radio.setChecked(True)

        def _sync_mode() -> None:
            use_auto = auto_radio.isChecked()
            use_explicit = explicit_radio.isChecked()
            min_edit.setEnabled(use_auto)
            max_edit.setEnabled(use_auto)
            intervals_spin.setEnabled(use_auto)
            explicit_edit.setEnabled(use_explicit)

        default_radio.toggled.connect(_sync_mode)
        auto_radio.toggled.connect(_sync_mode)
        explicit_radio.toggled.connect(_sync_mode)
        _sync_mode()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        levels_layout.addWidget(default_radio)
        levels_layout.addWidget(auto_radio)
        levels_layout.addLayout(auto_row)
        levels_layout.addWidget(explicit_radio)
        levels_layout.addWidget(explicit_edit)

        style_group = QGroupBox("Contour style")
        style_layout = QVBoxLayout(style_group)

        selected_cscale: dict[str, str | None] = {"value": existing.get("cscale")}

        cscale_row = QVBoxLayout()
        cscale_header_row = QHBoxLayout()
        cscale_label = QLabel("colour scale")
        cscale_value_label = QLabel()
        choose_cscale_button = QPushButton("Choose...")
        cscale_value_label.setStyleSheet("font-weight: 700;")
        cscale_row.setContentsMargins(0, 0, 0, 0)
        cscale_row.setSpacing(2)
        cscale_header_row.setContentsMargins(0, 0, 0, 0)
        cscale_header_row.setSpacing(6)

        def _update_cscale_label() -> None:
            value = selected_cscale.get("value")
            cscale_value_label.setText(str(value) if value else "default")

        def _choose_cscale() -> None:
            chosen = self._show_colour_scale_chooser(selected_cscale.get("value"))
            if chosen:
                selected_cscale["value"] = chosen
                _update_cscale_label()

        choose_cscale_button.clicked.connect(_choose_cscale)
        _update_cscale_label()

        cscale_header_row.addWidget(cscale_label)
        cscale_header_row.addStretch(1)
        cscale_header_row.addWidget(choose_cscale_button)
        cscale_header_row.setAlignment(cscale_label, Qt.AlignTop)
        cscale_header_row.setAlignment(choose_cscale_button, Qt.AlignTop)
        cscale_row.addLayout(cscale_header_row)
        cscale_row.addWidget(cscale_value_label)

        fill_checkbox = QCheckBox("fill")
        fill_checkbox.setChecked(bool(existing.get("fill", True)))

        lines_checkbox = QCheckBox("lines")
        lines_checkbox.setChecked(bool(existing.get("lines", False)))

        line_labels_checkbox = QCheckBox("line_labels")
        line_labels_checkbox.setChecked(bool(existing.get("line_labels", True)))

        negative_row = QHBoxLayout()
        negative_label = QLabel("negative_linestyle")
        negative_style_combo = QComboBox()
        negative_style_combo.addItems(["solid", "dashed"])
        current_negative = str(existing.get("negative_linestyle", "solid"))
        idx = negative_style_combo.findText(current_negative)
        negative_style_combo.setCurrentIndex(idx if idx >= 0 else 0)
        negative_row.addWidget(negative_label)
        negative_row.addWidget(negative_style_combo)

        zero_row = QHBoxLayout()
        zero_label = QLabel("zero_thick")
        zero_thick_spin = QDoubleSpinBox()
        zero_thick_spin.setRange(0.0, 20.0)
        zero_thick_spin.setDecimals(2)
        zero_thick_spin.setSingleStep(0.5)
        zero_thick_spin.setToolTip("0.0 disables thick zero contour")
        existing_zero = existing.get("zero_thick", False)
        zero_thick_spin.setValue(0.0 if existing_zero in (False, None) else float(existing_zero))
        zero_row.addWidget(zero_label)
        zero_row.addWidget(zero_thick_spin)

        blockfill_checkbox = QCheckBox("blockfill")
        blockfill_checkbox.setChecked(bool(existing.get("blockfill", False)))

        blockfill_fast_checkbox = QCheckBox("blockfill_fast (pcolormesh)")
        blockfill_fast_checkbox.setChecked(bool(existing.get("blockfill_fast", None)))

        def _sync_line_labels() -> None:
            line_labels_checkbox.setEnabled(lines_checkbox.isChecked())
            if not lines_checkbox.isChecked():
                line_labels_checkbox.setChecked(False)

        lines_checkbox.toggled.connect(_sync_line_labels)
        _sync_line_labels()

        style_top_row = QHBoxLayout()
        style_checks_col = QVBoxLayout()
        style_cscale_col = QVBoxLayout()

        style_checks_col.addWidget(fill_checkbox)
        style_checks_col.addWidget(lines_checkbox)
        style_checks_col.addWidget(line_labels_checkbox)
        style_checks_col.addStretch(1)

        style_cscale_col.addLayout(cscale_row)
        style_cscale_col.addStretch(1)

        style_top_row.addLayout(style_checks_col, 1)
        style_top_row.addLayout(style_cscale_col, 1)

        style_layout.addLayout(style_top_row)
        style_layout.addLayout(negative_row)
        style_layout.addLayout(zero_row)
        style_layout.addWidget(blockfill_checkbox)
        style_layout.addWidget(blockfill_fast_checkbox)

        layout.addWidget(titles_group)
        layout.addWidget(annotations_group)
        layout.addWidget(levels_group)
        layout.addWidget(style_group)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        if default_radio.isChecked():
            options = {"mode": "default"}
        elif explicit_radio.isChecked():
            raw_levels = [piece.strip() for piece in explicit_edit.text().split(",") if piece.strip()]
            try:
                levels = [float(piece) for piece in raw_levels]
            except ValueError:
                self.status.showMessage("Invalid explicit contour levels; expected comma-separated numbers")
                return

            if len(levels) < 2:
                self.status.showMessage("Please provide at least two contour levels")
                return

            options = {
                "mode": "explicit",
                "levels": levels,
            }
        else:
            try:
                user_min = float(min_edit.text().strip())
                user_max = float(max_edit.text().strip())
            except ValueError:
                self.status.showMessage("Invalid contour min/max values")
                return

            if user_min == user_max:
                self.status.showMessage("Contour min and max must differ")
                return

            lo, hi = sorted((user_min, user_max))
            options = {
                "mode": "auto",
                "min": lo,
                "max": hi,
                "intervals": int(intervals_spin.value()),
            }

        options["fill"] = bool(fill_checkbox.isChecked())
        options["lines"] = bool(lines_checkbox.isChecked())
        options["line_labels"] = bool(line_labels_checkbox.isChecked())
        options["negative_linestyle"] = str(negative_style_combo.currentText())
        zero_thick_value = float(zero_thick_spin.value())
        options["zero_thick"] = zero_thick_value if zero_thick_value > 0 else False
        options["blockfill"] = bool(blockfill_checkbox.isChecked())
        options["blockfill_fast"] = True if blockfill_fast_checkbox.isChecked() else None
        title_text = title_edit.text().strip()
        if title_text:
            options["title"] = title_text
        page_title_text = page_title_edit.text().strip()
        options["page_title_display"] = bool(page_title_display_checkbox.isChecked())
        if options["page_title_display"] and page_title_text:
            options["page_title"] = page_title_text
        options["page_margin_top"] = float(top_margin_spin.value())
        options["page_margin_bottom"] = float(bottom_margin_spin.value())
        free_text = free_text_edit.text().strip()
        if free_text:
            options["annotation_free_text"] = free_text
        options["annotation_display"] = bool(annotation_display_checkbox.isChecked())
        if selected_annotation_props:
            max_selected = 3 if free_text else 4
            options["annotation_properties"] = selected_annotation_props[:max_selected]
        if selected_cscale.get("value"):
            options["cscale"] = selected_cscale["value"]

        self.plot_options_by_kind["contour"] = options
        self.status.showMessage("Updated contour options")
        self._request_plot_update()

    def _show_annotation_properties_chooser(
        self,
        properties: dict[object, object],
        current_selected: list[tuple[str, str]],
        max_selected: int = 4,
    ) -> list[tuple[str, str]] | None:
        """Show a chooser for up to ``max_selected`` annotation properties."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Choose annotation properties")
        dialog.resize(640, 420)

        layout = QVBoxLayout(dialog)
        hint = QLabel(f"Select up to {max_selected} properties to annotate on plots")
        layout.addWidget(hint)

        table = QTableWidget(len(properties), 2, dialog)
        table.setHorizontalHeaderLabels(["Property", "Value"])
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        selected_set = {(str(k), str(v)) for k, v in current_selected}

        for row, (key, value) in enumerate(sorted(properties.items(), key=lambda kv: str(kv[0]).lower())):
            key_text = str(key)
            value_text = str(value)

            key_item = QTableWidgetItem(key_text)
            key_item.setFlags(key_item.flags() | Qt.ItemIsUserCheckable)
            key_item.setCheckState(
                Qt.Checked if (key_text, value_text) in selected_set else Qt.Unchecked
            )
            value_item = QTableWidgetItem(value_text)
            value_item.setToolTip(value_text)

            table.setItem(row, 0, key_item)
            table.setItem(row, 1, value_item)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        layout.addWidget(table)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None

        selected: list[tuple[str, str]] = []
        for row in range(table.rowCount()):
            key_item = table.item(row, 0)
            value_item = table.item(row, 1)
            if key_item is None or value_item is None:
                continue
            if key_item.checkState() == Qt.Checked:
                selected.append((key_item.text(), value_item.text()))

        if len(selected) > max_selected:
            QMessageBox.warning(
                self,
                "Too many properties",
                f"Please select at most {max_selected} annotation properties.",
            )
            return None

        return selected

    def _show_colour_scale_chooser(self, current_scale: str | None) -> str | None:
        """Show colour scale chooser with preview bars and return selected name."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Choose colour scale")
        dialog.resize(760, 560)

        layout = QVBoxLayout(dialog)

        table = QTableWidget(len(cscales), 2, dialog)
        table.setHorizontalHeaderLabels(["Scale", "Preview"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setWordWrap(False)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        selected_row = 0
        for row, name in enumerate(cscales):
            name_item = QTableWidgetItem(name)
            table.setItem(row, 0, name_item)

            preview_label = QLabel()
            preview_label.setPixmap(self._build_colour_scale_preview(name, width=420, height=14))
            table.setCellWidget(row, 1, preview_label)
            table.setRowHeight(row, 22)

            if current_scale and name == current_scale:
                selected_row = row

        table.selectRow(selected_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        table.doubleClicked.connect(dialog.accept)

        layout.addWidget(table)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None

        row = table.currentRow()
        if row < 0:
            return None

        item = table.item(row, 0)
        return item.text() if item else None

    def _build_colour_scale_preview(self, scale_name: str, width: int, height: int) -> QPixmap:
        """Build a small horizontal preview pixmap for a cf-plot colour scale."""
        colors = get_colour_scale_hexes(scale_name)
        if not colors:
            pixmap = QPixmap(width, height)
            pixmap.fill(Qt.lightGray)
            return pixmap

        image = QImage(width, height, QImage.Format_RGB32)
        n = len(colors)
        for x in range(width):
            idx = int((x / max(width - 1, 1)) * max(n - 1, 0))
            color_name = colors[idx]
            color = QColor(color_name)
            if not color.isValid():
                color = QColor("#aaaaaa")
            for y in range(height):
                image.setPixelColor(x, y, color)

        return QPixmap.fromImage(image)

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
