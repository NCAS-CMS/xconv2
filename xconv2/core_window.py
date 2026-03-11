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

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
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
    QSizePolicy,
    QStatusBar,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .ui.contour_options_controller import ContourOptionsController
from .ui.field_metadata_controller import FieldMetadataController
from .ui.menu_controller import MenuController
from .ui.plot_view_controller import PlotViewController
from .ui.selection_controller import SelectionController
from .ui.settings_store import SettingsStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

FIELD_METADATA_SEPARATOR = "\x1f"
DEFAULT_MAX_RECENT_FILES = 10
SETTINGS_VERSION = 1
STATUSBAR_NORMAL_STYLE = ""
STATUSBAR_ERROR_STYLE = "QStatusBar { color: #c62828; font-weight: 600; }"


class CFVCore(QMainWindow):
    """Base window with GUI-only behavior and extension hooks for app logic."""

    def __init__(self) -> None:
        super().__init__()

        self.base_window_title = f"xconv2 ({__version__})"
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
        self.selection_controller = SelectionController(self)
        self.field_metadata_controller = FieldMetadataController(self, FIELD_METADATA_SEPARATOR)
        self.plot_view_controller = PlotViewController(self)
        self.contour_options_controller = ContourOptionsController(self)
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
        self.last_varying_dims: int | None = None
        self.available_plot_kinds: list[str] = []
        self.selected_plot_kind: str | None = None
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
        dialog.setWindowTitle(f"About xconv2 ({__version__})")
        dialog.resize(560, 320)

        layout = QVBoxLayout(dialog)

        heading = QLabel(
            f"<h2 style='margin:0;'>xconv2 ({__version__})</h2>"
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
        left_logo_column = QVBoxLayout()
        left_logo_column.setContentsMargins(0, 0, 0, 0)
        left_logo_column.setSpacing(6)
        left_logo_column.addWidget(
            self._build_about_logo_label(
                "Under construction",
                ["under-construction.svg"],
                56,
            ),
            alignment=Qt.AlignHCenter,
        )
        left_logo_column.addWidget(
            self._build_about_logo_label(
                "cf-python",
                ["cf-logo.png", "cf-logo.svg", "cf-python-logo.png", "cf-python-logo.svg"],
                112,
            ),
            alignment=Qt.AlignHCenter,
        )
        left_logo_column.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        left_logo_widget = QWidget()
        left_logo_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        left_logo_widget.setLayout(left_logo_column)
        header_row.addWidget(left_logo_widget)
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
        self.selection_controller.reset_all_sliders()

    def _set_field_list_hint(self, text: str) -> None:
        """Show a non-selectable hint message in the fields list."""
        self.field_metadata_controller.set_field_list_hint(text)

    def _show_selection_properties(self) -> None:
        """Show properties for the currently selected field."""
        self.field_metadata_controller.show_selection_properties()

    def _save_properties_to_csv(
        self,
        properties: dict[object, object],
        field_name: str,
        parent: QWidget | None = None,
    ) -> None:
        """Save properties dictionary to a CSV file with Key/Value columns."""
        self.field_metadata_controller.save_properties_to_csv(properties, field_name, parent)

    def _parse_properties_dict(self, raw_properties: object) -> dict[object, object]:
        """Parse properties payload into a dictionary when possible."""
        return self.field_metadata_controller.parse_properties_dict(raw_properties)

    def _parse_properties_lines(self, text: str) -> dict[str, str]:
        """Parse key/value properties from multi-line text representations."""
        return self.field_metadata_controller.parse_properties_lines(text)

    def _set_field_list_visible_rows(self, row_count: int) -> None:
        """Size the field list to show a target number of rows by default."""
        self.field_metadata_controller.set_field_list_visible_rows(row_count)

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
        return self.plot_view_controller.create_plot_area()

    def _on_plot_button_clicked(self) -> None:
        """Request a plot refresh when the current selection is plottable."""
        self.plot_view_controller.on_plot_button_clicked()

    def _on_options_button_clicked(self) -> None:
        """Request plot-type specific options from worker/UI flow."""
        self.plot_view_controller.on_options_button_clicked()

    def set_plot_image(self, png_bytes: bytes) -> None:
        """Render PNG bytes from worker output into the plot frame."""
        self.plot_view_controller.set_plot_image(png_bytes)

    def _fit_window_to_plot_aspect(self) -> None:
        """Nudge window height to match plot aspect ratio without exceeding screen bounds."""
        self.plot_view_controller.fit_window_to_plot_aspect()

    def _refresh_plot_pixmap(self) -> None:
        """Scale current plot pixmap to fit the visible plot frame."""
        self.plot_view_controller.refresh_plot_pixmap()

    def _set_plot_loading(self, is_loading: bool, message: str = "Rendering plot...") -> None:
        """Toggle the plot loading overlay state."""
        self.plot_view_controller.set_plot_loading(is_loading, message)

    def _clear_plot_canvas(self, message: str = "Plot unavailable") -> None:
        """Clear the rendered plot image and display a fallback message."""
        self.plot_view_controller.clear_plot_canvas(message)

    def _on_save_code_button_clicked(self) -> None:
        """Prompt for destination file and request worker-side plot code save."""
        self.plot_view_controller.on_save_code_button_clicked()

    def _on_save_plot_button_clicked(self) -> None:
        """Prompt for destination image file and request worker-side plot save."""
        self.plot_view_controller.on_save_plot_button_clicked()

    def _setup_status_bar(self) -> None:
        """Create and initialize the status bar."""
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self._show_status_message("System Ready. Initialize S3 Load.")

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        """Show a status-bar message with optional error styling."""
        style = STATUSBAR_ERROR_STYLE if is_error else STATUSBAR_NORMAL_STYLE
        self.status.setStyleSheet(style)
        self.status.showMessage(message)

    def populate_field_list(self, fields: Sequence[object]) -> None:
        """Populate the field list UI from worker metadata."""
        self.field_metadata_controller.populate_field_list(fields)

    def on_field_clicked(self, item: QListWidgetItem) -> None:
        """Display selected field details in the output panel."""
        self.field_metadata_controller.on_field_clicked(item)

    def build_dynamic_sliders(self, metadata: dict[str, object]) -> None:
        """Build compact dual-handle range sliders from coordinate metadata."""
        self.selection_controller.build_dynamic_sliders(metadata)

    def on_range_slider_moved(self, name: str) -> None:
        """Handle dual-handle range slider movement."""
        self.selection_controller.on_range_slider_moved(name)

    def on_collapse_toggled(self, name: str, checked: bool) -> None:
        """Choose and persist a collapse method for the coordinate."""
        self.selection_controller.on_collapse_toggled(name, checked)

    def _update_range_labels(self, name: str) -> None:
        """Refresh compact summary line for current range selection."""
        self.selection_controller.update_range_labels(name)

    def _refresh_plot_summary(self) -> None:
        """Update plot summary text and plot button availability."""
        self.selection_controller.refresh_plot_summary()

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
        self.contour_options_controller.show_contour_options_dialog(
            range_min=range_min,
            range_max=range_max,
            suggested_title=suggested_title,
        )

    def _show_annotation_properties_chooser(
        self,
        properties: dict[object, object],
        current_selected: list[tuple[str, str]],
        max_selected: int = 4,
    ) -> list[tuple[str, str]] | None:
        """Show a chooser for up to ``max_selected`` annotation properties."""
        return self.contour_options_controller.show_annotation_properties_chooser(
            properties=properties,
            current_selected=current_selected,
            max_selected=max_selected,
        )

    def _show_colour_scale_chooser(self, current_scale: str | None) -> str | None:
        """Show colour scale chooser with preview bars and return selected name."""
        return self.contour_options_controller.show_colour_scale_chooser(current_scale)

    def _build_colour_scale_preview(self, scale_name: str, width: int, height: int) -> QPixmap:
        """Build a small horizontal preview pixmap for a cf-plot colour scale."""
        return self.contour_options_controller.build_colour_scale_preview(
            scale_name=scale_name,
            width=width,
            height=height,
        )

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
