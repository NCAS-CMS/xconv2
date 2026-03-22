"""Core GUI classes for cf-view.

This module contains presentation-only code:
- widget creation
- layout composition
- menu/tray setup
- local UI state updates

Worker orchestration and request/response handling live in `main_window.py`.
"""

from __future__ import annotations

from datetime import datetime
import glob
from pathlib import Path
import logging
from typing import Sequence
from urllib.parse import urlparse

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .ui.contour_options_controller import ContourOptionsController
from .ui.field_metadata_controller import FieldMetadataController
from .ui.lineplot_options_controller import LineplotOptionsController
from .ui.menu_controller import MenuController
from .ui.plot_view_controller import PlotViewController
from .ui.selection_controller import SelectionController
from .ui.dialogs import OpenGlobDialog, OpenURIDialog, RemoteConfigurationDialog, RemoteOpenDialog
from .ui.remote_file_navigator import RemoteFileNavigatorDialog
from .ui.settings_store import SettingsStore
from .cache_utils import disk_cache_usage, parse_disk_expiry_seconds, prune_disk_cache
from .logging_utils import get_log_file_path

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

DEFAULT_MAX_RECENT_FILES = 10
SETTINGS_VERSION = 1
STATUSBAR_NORMAL_STYLE = ""
STATUSBAR_ERROR_STYLE = "QStatusBar { color: #c62828; font-weight: 600; }"


class LogViewerDialog(QDialog):
    """Tail and display the shared application log file."""

    def __init__(self, parent: QWidget | None, log_path: Path) -> None:
        super().__init__(parent)
        self._log_path = log_path
        self._read_pos = 0

        self.setWindowTitle("xconv2 Logs")
        self.resize(900, 520)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(str(log_path)))

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        flush_button = buttons.addButton("Flush Log", QDialogButtonBox.ActionRole)
        flush_button.clicked.connect(self._flush_log)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._refresh_from_file)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._refresh_from_file()
        self._timer.start()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._timer.stop()
        super().hideEvent(event)

    def _refresh_from_file(self) -> None:
        """Append only new log bytes and keep viewport pinned to the end."""
        try:
            size = self._log_path.stat().st_size
        except OSError:
            return

        if size < self._read_pos:
            # File was truncated/rotated; reset view and start from top again.
            self._read_pos = 0
            self.log_view.clear()

        if size == self._read_pos:
            return

        try:
            with self._log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self._read_pos)
                chunk = handle.read()
                self._read_pos = handle.tell()
        except OSError:
            return

        if not chunk:
            return

        cursor = self.log_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(chunk)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _flush_log(self) -> None:
        """Truncate the backing log file and reset the live view state."""
        self._timer.stop()
        try:
            with self._log_path.open("w", encoding="utf-8"):
                pass
        except OSError as exc:
            QMessageBox.warning(self, "Flush Log Failed", str(exc))
        else:
            self._read_pos = 0
            self.log_view.clear()
        finally:
            self._timer.start()


class CacheManagerDialog(QDialog):
    """Summarize remote cache configuration and allow disk cache flushes."""

    def __init__(self, parent: "CFVCore") -> None:
        super().__init__(parent)
        self._host = parent

        self.setWindowTitle("xconv2 Cache")
        self.resize(760, 420)

        layout = QVBoxLayout(self)
        self.summary_view = QPlainTextEdit()
        self.summary_view.setReadOnly(True)
        self.summary_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.summary_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.refresh_button = buttons.addButton("Refresh", QDialogButtonBox.ActionRole)
        self.prune_button = buttons.addButton("Prune Cache", QDialogButtonBox.ActionRole)
        self.flush_button = buttons.addButton("Flush Cache", QDialogButtonBox.ActionRole)
        self.refresh_button.clicked.connect(self.refresh_summary)
        self.prune_button.clicked.connect(self._prune_cache)
        self.flush_button.clicked.connect(self._flush_cache)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.refresh_summary()

    def refresh_summary(self) -> None:
        """Refresh the cache summary text from current host state."""
        self.summary_view.setPlainText(self._host._cache_summary_text())

    def _flush_cache(self) -> None:
        """Flush configured disk cache and refresh summary view."""
        if self._host._flush_configured_disk_cache():
            self.refresh_summary()

    def _prune_cache(self) -> None:
        """Prune configured disk cache and refresh summary view."""
        if self._host._prune_configured_disk_cache():
            self.refresh_summary()


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
        self.field_metadata_controller = FieldMetadataController(self)
        self.plot_view_controller = PlotViewController(self)
        self.contour_options_controller = ContourOptionsController(self)
        self.lineplot_options_controller = LineplotOptionsController(self)
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
        self.current_selection_info_text = "No selection info available."
        self.slider_scroll_area: QScrollArea | None = None
        self.selection_info_toggle_button: QToolButton | None = None
        self._selection_info_visible = True
        self._selection_info_expanded_from_width: int | None = None
        self._log_viewer_dialog: LogViewerDialog | None = None
        self._cache_manager_dialog: CacheManagerDialog | None = None

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
        self._update_selection_info_toggle_button()

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
                ["cf-logo-t.svg",],
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


    def _open_roadmap(self) -> None:
        """Open the project roadmap page in the default browser."""
        roadmap_url = QUrl("https://github.com/NCAS-CMS/xconv2/milestones")
        if not QDesktopServices.openUrl(roadmap_url):
            self.status.showMessage("Unable to open roadmap URL.")
            logger.warning("Failed to open roadmap URL: %s", roadmap_url.toString())

    def _view_logs(self) -> None:
        """Show a live in-app view of the shared application log file."""
        log_path = get_log_file_path()
        try:
            log_path.touch(exist_ok=True)
        except OSError:
            self._show_status_message(f"Unable to create log file: {log_path}", is_error=True)
            logger.exception("Failed to ensure log file exists: %s", log_path)
            return

        if self._log_viewer_dialog is None:
            self._log_viewer_dialog = LogViewerDialog(self, log_path)

        self._log_viewer_dialog.show()
        self._log_viewer_dialog.raise_()
        self._log_viewer_dialog.activateWindow()

    @staticmethod
    def _format_storage_size(size_bytes: int) -> str:
        """Format byte counts for cache/log summaries."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        value = float(size_bytes)
        for unit in ("KB", "MB", "GB", "TB"):
            value /= 1024.0
            if value < 1024.0 or unit == "TB":
                text = f"{value:.1f}".rstrip("0").rstrip(".")
                return f"{text} {unit}"
        return f"{size_bytes} B"

    def _active_cache_settings(self) -> dict[str, object]:
        """Return active remote cache settings, preferring current remote session state."""
        descriptor = getattr(self, "_remote_descriptor", None)
        if isinstance(descriptor, dict):
            cache = descriptor.get("cache")
            if isinstance(cache, dict):
                return dict(cache)

        raw = self._settings.get("last_remote_configuration", {})
        if isinstance(raw, dict):
            return {
                "blocksize_mb": int(raw.get("cache_blocksize_mb", 2)),
                "ram_buffer_mb": int(raw.get("cache_ram_buffer_mb", 1024)),
                "cache_strategy": str(raw.get("cache_strategy", "Block")),
                "max_blocks": max(1, int(raw.get("cache_ram_buffer_mb", 1024)) // max(1, int(raw.get("cache_blocksize_mb", 2)))),
                "disk_mode": str(raw.get("disk_mode", "Disabled")),
                "disk_location": str(raw.get("disk_location", str(Path.home() / ".cache/xconv2"))),
                "disk_limit_gb": int(raw.get("disk_limit_gb", 10)),
                "disk_expiry": str(raw.get("disk_expiry", "1 day")),
            }
        return {}

    def _disk_cache_usage(self, location: Path) -> tuple[int, int]:
        """Return total bytes and file count under the configured disk cache directory."""
        return disk_cache_usage(location)

    def _cache_summary_text(self) -> str:
        """Build a human-readable summary of current cache configuration and usage."""
        cache = self._active_cache_settings()
        strategy = str(cache.get("cache_strategy", "None"))
        blocksize_mb = int(cache.get("blocksize_mb", 0) or 0)
        ram_buffer_mb = int(cache.get("ram_buffer_mb", 0) or 0)
        max_blocks = int(cache.get("max_blocks", 0) or 0)
        disk_mode = str(cache.get("disk_mode", "Disabled"))
        disk_location = Path(str(cache.get("disk_location", str(Path.home() / ".cache/xconv2")))).expanduser()
        disk_limit_gb = int(cache.get("disk_limit_gb", 0) or 0)
        disk_expiry = str(cache.get("disk_expiry", "Never"))
        disk_bytes, disk_files = self._disk_cache_usage(disk_location)
        has_active_remote = bool(getattr(self, "_remote_session_id", None))

        lines = [
            "Remote Cache Summary",
            "",
            f"Active remote session: {'yes' if has_active_remote else 'no'}",
            "",
            "Memory cache",
            f"  Strategy: {strategy}",
            f"  Block size: {blocksize_mb} MB",
            f"  RAM buffer: {ram_buffer_mb} MB",
            f"  Max blocks: {max_blocks}",
            "",
            "Disk cache",
            f"  Mode: {disk_mode}",
            f"  Location: {disk_location}",
            f"  Usage: {self._format_storage_size(disk_bytes)} across {disk_files} files",
            f"  Limit: {disk_limit_gb} GB",
            f"  Expiry: {disk_expiry}",
        ]
        return "\n".join(lines)

    def _flush_configured_disk_cache(self) -> bool:
        """Flush configured disk cache contents after user confirmation."""
        cache = self._active_cache_settings()
        disk_location = Path(str(cache.get("disk_location", str(Path.home() / ".cache/xconv2")))).expanduser()
        if not disk_location.exists():
            self._show_status_message(f"Cache directory does not exist: {disk_location}")
            return True

        response = QMessageBox.question(
            self,
            "Flush Cache",
            f"Delete all files under cache location?\n\n{disk_location}",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if response != QMessageBox.Yes:
            return False

        release_remote = getattr(self, "_release_remote_session_if_active", None)
        if callable(release_remote):
            release_remote()

        try:
            for child in disk_location.iterdir():
                if child.is_dir() and not child.is_symlink():
                    import shutil
                    shutil.rmtree(child)
                else:
                    child.unlink()
        except OSError:
            logger.exception("Failed to flush cache directory: %s", disk_location)
            self._show_status_message(f"Failed to flush cache: {disk_location}", is_error=True)
            return False

        self._show_status_message(f"Flushed cache: {disk_location}")
        return True

    def _prune_configured_disk_cache(self) -> bool:
        """Prune configured disk cache by expiry and size limit."""
        cache = self._active_cache_settings()
        disk_location = Path(str(cache.get("disk_location", str(Path.home() / ".cache/xconv2")))).expanduser()
        disk_limit_gb = int(cache.get("disk_limit_gb", 0) or 0)
        disk_expiry = parse_disk_expiry_seconds(cache.get("disk_expiry"))

        release_remote = getattr(self, "_release_remote_session_if_active", None)
        if callable(release_remote):
            release_remote()

        summary = prune_disk_cache(
            disk_location,
            limit_bytes=disk_limit_gb * 1024 * 1024 * 1024,
            expiry_seconds=disk_expiry,
        )
        self._show_status_message(
            f"Pruned cache: removed {summary['removed_files']} files from {disk_location}"
        )
        return True

    def _show_cache_manager(self) -> None:
        """Open the in-app cache manager dialog."""
        if self._cache_manager_dialog is None:
            self._cache_manager_dialog = CacheManagerDialog(self)
        else:
            self._cache_manager_dialog.refresh_summary()

        self._cache_manager_dialog.show()
        self._cache_manager_dialog.raise_()
        self._cache_manager_dialog.activateWindow()

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

    def _record_recent_uri(self, uri: str, host_alias: str | None = None) -> None:
        """Record a remote URI and optional alias for display in the Recent submenu."""
        canonical_uri = CFVCore._canonical_remote_uri(uri)
        self._record_recent_file(canonical_uri)

        aliases = self._settings.get("recent_uri_aliases")
        alias_map = dict(aliases) if isinstance(aliases, dict) else {}
        if host_alias:
            alias_map[canonical_uri] = host_alias
        else:
            alias_map.pop(canonical_uri, None)

        recent = set(self._load_recent_files())
        alias_map = {key: value for key, value in alias_map.items() if key in recent}
        self._settings["recent_uri_aliases"] = alias_map
        try:
            self._save_settings()
        except OSError:
            logger.exception("Failed to persist URI alias map")

    def _recent_menu_label(self, file_path: str) -> str:
        """Return display label for a recent-file menu entry."""
        canonical_path = CFVCore._canonical_remote_uri(file_path)
        parsed = urlparse(canonical_path)
        if parsed.scheme:
            filename = Path(parsed.path).name or canonical_path
            aliases = self._settings.get("recent_uri_aliases")
            alias_map = aliases if isinstance(aliases, dict) else {}
            alias = alias_map.get(canonical_path) or alias_map.get(file_path)
            if isinstance(alias, str) and alias.strip():
                return f"{filename} ({alias.strip()})"

            if parsed.hostname:
                return f"{filename} ({parsed.hostname})"
            return filename

        return Path(file_path).name

    def _recent_menu_tooltip(self, file_path: str) -> str:
        """Return tooltip text for a recent-file menu entry."""
        canonical_path = CFVCore._canonical_remote_uri(file_path)
        parsed = urlparse(canonical_path)
        if parsed.scheme:
            return CFVCore._shareable_remote_uri(self, canonical_path)
        return file_path

    def _default_open_uri_value(self) -> str:
        """Return the most recent remote URI as the Open URI default value."""
        for item in self._load_recent_files():
            if urlparse(item).scheme:
                canonical = CFVCore._canonical_remote_uri(item)
                return CFVCore._shareable_remote_uri(self, canonical)
        return ""

    def _shareable_remote_uri(self, uri: str) -> str:
        """Return a user-facing URI form suitable for sharing outside xconv2."""
        parsed = urlparse(uri)
        if parsed.scheme != "s3":
            return uri

        alias = ""
        aliases = self._settings.get("recent_uri_aliases")
        if isinstance(aliases, dict):
            value = aliases.get(uri)
            if isinstance(value, str):
                alias = value.strip()

        if not alias:
            return uri

        locations = RemoteConfigurationDialog._load_s3_locations()
        details = locations.get(alias, {})
        if not isinstance(details, dict):
            return uri

        endpoint = str(details.get("url", "")).strip()
        endpoint_host = urlparse(endpoint).netloc.strip()
        if not endpoint_host:
            return uri

        path = f"{parsed.netloc}{parsed.path}".lstrip("/")
        if not path:
            return uri
        return f"s3://{endpoint_host}/{path}"

    @staticmethod
    def _canonical_remote_uri(uri: str) -> str:
        """Normalize user-visible remote URI strings for internal consistency."""
        text = str(uri).strip()
        if text.startswith("s3:/") and not text.startswith("s3://"):
            return "s3://" + text[len("s3:/") :].lstrip("/")
        return text

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

    def _field_list_rows(self, settings: dict[str, object] | None = None) -> int:
        """Return validated default visible row count for the fields list."""
        source = settings if settings is not None else self._settings
        raw = source.get("field_list_rows", 12)
        if isinstance(raw, int) and raw > 0:
            return raw
        return 12

    def _visible_coordinate_rows(self, settings: dict[str, object] | None = None) -> int:
        """Return validated visible slider row cap for the selection frame."""
        source = settings if settings is not None else self._settings
        raw = source.get("visible_coordinate_rows", 4)
        if isinstance(raw, int) and raw > 0:
            return raw
        return 4

    def _contour_title_fontsize(self, settings: dict[str, object] | None = None) -> float:
        """Return validated default contour title font size."""
        source = settings if settings is not None else self._settings
        raw = source.get("contour_title_fontsize", 10.5)
        if isinstance(raw, (int, float)) and float(raw) > 0:
            return float(raw)
        return 10.5

    def _page_title_fontsize(self, settings: dict[str, object] | None = None) -> float:
        """Return validated default page title font size."""
        source = settings if settings is not None else self._settings
        raw = source.get("page_title_fontsize", 10.0)
        if isinstance(raw, (int, float)) and float(raw) > 0:
            return float(raw)
        return 10.0

    def _annotation_fontsize(self, settings: dict[str, object] | None = None) -> float:
        """Return validated default annotation font size."""
        source = settings if settings is not None else self._settings
        raw = source.get("annotation_fontsize", 8.0)
        if isinstance(raw, (int, float)) and float(raw) > 0:
            return float(raw)
        return 8.0

    @staticmethod
    def _timestamp_plot_filename() -> str:
        """Return default timestamp-based plot filename stem."""
        return datetime.now().strftime("%y%m%d_%H%M")

    @staticmethod
    def _default_plot_filename_template() -> str:
        """Return the dynamic template used for default plot filenames."""
        return "xconv_{timestamp}"

    @staticmethod
    def _sanitize_plot_filename_stem(stem: str) -> str:
        """Normalize a filename stem and strip supported plot extensions."""
        normalized = stem.strip()
        if not normalized:
            return ""

        suffix = Path(normalized).suffix.lower().lstrip(".")
        if suffix in {"png", "svg", "pdf"}:
            return Path(normalized).stem
        return normalized

    def _plot_filename_template(self, settings: dict[str, object] | None = None) -> str:
        """Return configured filename template, defaulting to dynamic timestamp token."""
        source = settings if settings is not None else self._settings
        raw = source.get("default_plot_filename", "")
        if isinstance(raw, str):
            sanitized = self._sanitize_plot_filename_stem(raw)
            if sanitized:
                return sanitized
        return self._default_plot_filename_template()

    def _default_plot_filename(self, settings: dict[str, object] | None = None) -> str:
        """Return resolved default plot filename stem for save operations."""
        template = self._plot_filename_template(settings)
        return template.replace("{timestamp}", self._timestamp_plot_filename())

    def _default_plot_output_format(self, settings: dict[str, object] | None = None) -> str:
        """Return configured default plot output format."""
        source = settings if settings is not None else self._settings
        raw = source.get("default_plot_format", "png")
        if raw in {"png", "svg", "pdf"}:
            return str(raw)
        return "png"

    def _show_settings_dialog(self) -> None:
        """Show basic settings editor for persisted app preferences."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.resize(700, 380)

        layout = QVBoxLayout(dialog)

        recent_row = QHBoxLayout()
        recent_label = QLabel("How many recent files to keep")
        recent_spin = QSpinBox()
        recent_spin.setRange(1, 100)
        recent_spin.setValue(self._max_recent_files())
        recent_row.addWidget(recent_label)
        recent_row.addStretch(1)
        recent_row.addWidget(recent_spin)

        field_rows_row = QHBoxLayout()
        field_rows_label = QLabel("Default visible rows in Fields list")
        field_rows_spin = QSpinBox()
        field_rows_spin.setRange(1, 50)
        field_rows_spin.setValue(self._field_list_rows())
        field_rows_row.addWidget(field_rows_label)
        field_rows_row.addStretch(1)
        field_rows_row.addWidget(field_rows_spin)

        coord_rows_row = QHBoxLayout()
        coord_rows_label = QLabel("Visible coordinate sliders before scrolling")
        coord_rows_spin = QSpinBox()
        coord_rows_spin.setRange(1, 12)
        coord_rows_spin.setValue(self._visible_coordinate_rows())
        coord_rows_row.addWidget(coord_rows_label)
        coord_rows_row.addStretch(1)
        coord_rows_row.addWidget(coord_rows_spin)

        contour_title_fontsize_row = QHBoxLayout()
        contour_title_fontsize_label = QLabel("Default contour title font size")
        contour_title_fontsize_spin = QDoubleSpinBox()
        contour_title_fontsize_spin.setRange(1.0, 48.0)
        contour_title_fontsize_spin.setDecimals(1)
        contour_title_fontsize_spin.setSingleStep(0.5)
        contour_title_fontsize_spin.setValue(self._contour_title_fontsize())
        contour_title_fontsize_row.addWidget(contour_title_fontsize_label)
        contour_title_fontsize_row.addStretch(1)
        contour_title_fontsize_row.addWidget(contour_title_fontsize_spin)

        page_title_fontsize_row = QHBoxLayout()
        page_title_fontsize_label = QLabel("Default page title font size")
        page_title_fontsize_spin = QDoubleSpinBox()
        page_title_fontsize_spin.setRange(1.0, 48.0)
        page_title_fontsize_spin.setDecimals(1)
        page_title_fontsize_spin.setSingleStep(0.5)
        page_title_fontsize_spin.setValue(self._page_title_fontsize())
        page_title_fontsize_row.addWidget(page_title_fontsize_label)
        page_title_fontsize_row.addStretch(1)
        page_title_fontsize_row.addWidget(page_title_fontsize_spin)

        annotation_fontsize_row = QHBoxLayout()
        annotation_fontsize_label = QLabel("Default annotation font size")
        annotation_fontsize_spin = QDoubleSpinBox()
        annotation_fontsize_spin.setRange(1.0, 48.0)
        annotation_fontsize_spin.setDecimals(1)
        annotation_fontsize_spin.setSingleStep(0.5)
        annotation_fontsize_spin.setValue(self._annotation_fontsize())
        annotation_fontsize_row.addWidget(annotation_fontsize_label)
        annotation_fontsize_row.addStretch(1)
        annotation_fontsize_row.addWidget(annotation_fontsize_spin)

        plot_filename_row = QHBoxLayout()
        plot_filename_label = QLabel("Default plot filename")
        plot_filename_edit = QLineEdit(self._plot_filename_template())
        plot_filename_reset = QPushButton("Reset")

        def _reset_plot_filename() -> None:
            plot_filename_edit.setText(self._default_plot_filename_template())

        plot_filename_reset.clicked.connect(_reset_plot_filename)
        plot_filename_row.addWidget(plot_filename_label)
        plot_filename_row.addWidget(plot_filename_edit, 1)
        plot_filename_row.addWidget(plot_filename_reset)

        plot_format_row = QHBoxLayout()
        plot_format_label = QLabel("Default plot output format")
        plot_format_combo = QComboBox()
        plot_formats = ["png", "svg", "pdf"]
        plot_format_combo.addItems(plot_formats)
        default_plot_format = self._default_plot_output_format()
        plot_format_combo.setCurrentIndex(plot_formats.index(default_plot_format))
        plot_format_row.addWidget(plot_format_label)
        plot_format_row.addStretch(1)
        plot_format_row.addWidget(plot_format_combo)

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

        gui_defaults_frame = QGroupBox("GUI Defaults")
        gui_defaults_layout = QVBoxLayout(gui_defaults_frame)
        gui_defaults_layout.addLayout(recent_row)
        gui_defaults_layout.addLayout(field_rows_row)
        gui_defaults_layout.addLayout(coord_rows_row)
        gui_defaults_layout.addLayout(contour_title_fontsize_row)
        gui_defaults_layout.addLayout(page_title_fontsize_row)
        gui_defaults_layout.addLayout(annotation_fontsize_row)

        output_frame = QGroupBox("Output")
        output_layout = QVBoxLayout(output_frame)
        output_layout.addLayout(plot_filename_row)
        output_layout.addLayout(plot_format_row)
        output_layout.addLayout(code_dir_row)
        output_layout.addLayout(plot_dir_row)

        layout.addWidget(gui_defaults_frame)
        layout.addWidget(output_frame)
        layout.addStretch(1)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        self._settings["max_recent_files"] = int(recent_spin.value())
        self._settings["field_list_rows"] = int(field_rows_spin.value())
        self._settings["visible_coordinate_rows"] = int(coord_rows_spin.value())
        self._settings["contour_title_fontsize"] = float(contour_title_fontsize_spin.value())
        self._settings["page_title_fontsize"] = float(page_title_fontsize_spin.value())
        self._settings["annotation_fontsize"] = float(annotation_fontsize_spin.value())
        self._settings["default_plot_filename"] = self._sanitize_plot_filename_stem(
            plot_filename_edit.text()
        )
        self._settings["default_plot_format"] = plot_formats[plot_format_combo.currentIndex()]

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

        self._set_field_list_visible_rows(self._field_list_rows())
        self._set_slider_scroll_visible_rows(len(self.controls), self._visible_coordinate_rows())
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
        parsed = urlparse(file_path)
        if parsed.scheme:
            self._show_status_message("Open recent URI is handled by worker-backed windows.", is_error=True)
            return

        self._set_window_title_for_file(file_path)
        logger.info("Selected recent file: %s", file_path)
        self._record_recent_file(file_path)
        self.on_file_selected(file_path)

    def _create_left_panel(self) -> QWidget:
        """Create the left panel with framed Fields and Selection sections."""
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        fields_frame = self._create_fields_frame()
        selection_frame = self._create_selection_frame()
        selection_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        left_layout.addWidget(fields_frame, 0, Qt.AlignTop)
        left_layout.addStretch(1)
        left_layout.addWidget(selection_frame, 0, Qt.AlignBottom)
        return left_panel

    def _create_fields_frame(self) -> QGroupBox:
        """Create framed fields list section."""
        frame = QGroupBox("Fields")
        layout = QVBoxLayout(frame)

        self.field_list_widget = QListWidget()
        self.field_list_widget.itemClicked.connect(self.on_field_clicked)
        self._set_field_list_visible_rows(self._field_list_rows())
        self._set_field_list_hint("Open a file to see fields")

        layout.addWidget(self.field_list_widget)
        return frame

    def _create_selection_frame(self) -> QGroupBox:
        """Create framed selection details and slider controls section."""
        frame = QGroupBox("Selection")
        layout = QVBoxLayout(frame)

        controls_row = QHBoxLayout()
        properties_button = QPushButton("Properties")
        properties_button.clicked.connect(self._show_selection_properties)
        reset_button = QPushButton("Reset all sliders")
        reset_button.setToolTip("Reset all range sliders to full coordinate extent")
        reset_button.clicked.connect(self._reset_all_sliders)
        self.selection_info_toggle_button = QToolButton()
        self.selection_info_toggle_button.setAutoRaise(True)
        self.selection_info_toggle_button.clicked.connect(self._toggle_selection_info_panel)
        controls_row.addWidget(properties_button)
        controls_row.addWidget(reset_button)
        controls_row.addStretch(1)
        controls_row.addWidget(self.selection_info_toggle_button)

        layout.addLayout(controls_row)
        layout.addWidget(self._create_slider_scroll_area())
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
        self._set_field_list_visible_rows(self._field_list_rows())
        self._set_field_list_hint("Open a file to see fields")

        selection_header = QHBoxLayout()
        output_title = QLabel("Selection")
        output_title.setStyleSheet("color: #9a9a9a; font-weight: 600;")
        properties_button = QPushButton("Properties")
        properties_button.clicked.connect(self._show_selection_properties)

        selection_header.addWidget(output_title)
        selection_header.addStretch(1)
        selection_header.addWidget(properties_button)

        layout.addWidget(title)
        layout.addWidget(self.field_list_widget)
        layout.addLayout(selection_header)
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

    def _toggle_selection_info_panel(self) -> None:
        """Show or hide the field-detail panel above the plot."""
        if self._selection_info_visible:
            self._selection_info_expanded_from_width = self.width()
        self._set_selection_info_panel_visible(not self._selection_info_visible)
        self._update_selection_info_toggle_button()
        self.plot_view_controller.adjust_window_width_for_info_panel(self._selection_info_visible)

    def _set_selection_info_panel_visible(self, visible: bool) -> None:
        """Set the details panel visibility without toggling width behavior."""
        self._selection_info_visible = visible
        if hasattr(self, "plot_info_output") and self.plot_info_output is not None:
            self.plot_info_output.setVisible(visible)

    def _update_selection_info_toggle_button(self) -> None:
        """Sync the details-toggle button icon and tooltip with panel visibility."""
        button = self.selection_info_toggle_button
        if button is None:
            return

        if hasattr(self, "plot_info_output") and self.plot_info_output is not None:
            # Use explicit hidden state: isVisible() is false before the top-level window is shown.
            self._selection_info_visible = not self.plot_info_output.isHidden()

        if self._selection_info_visible:
            icon = self.style().standardIcon(QStyle.SP_TitleBarShadeButton)
            tooltip = "Hide field details"
        else:
            icon = self.style().standardIcon(QStyle.SP_TitleBarUnshadeButton)
            tooltip = "Show field details"

        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setStatusTip(tooltip)

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
        scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.slider_scroll_area = scroll
        self._set_slider_scroll_visible_rows(1)
        return scroll

    def _set_slider_scroll_visible_rows(self, total_rows: int, max_visible_rows: int | None = None) -> None:
        """Size slider area to visible rows (up to ``max_visible_rows``), then scroll."""
        if self.slider_scroll_area is None:
            return

        row_cap = max_visible_rows if max_visible_rows is not None else self._visible_coordinate_rows()
        visible_rows = max(1, min(total_rows, row_cap))
        row_heights: list[int] = []

        for idx in range(visible_rows):
            item = self.sidebar.itemAt(idx)
            if item is None:
                continue
            widget = item.widget()
            if widget is None:
                continue
            row_heights.append(widget.sizeHint().height())

        if row_heights:
            rows_height = sum(row_heights)
        else:
            # Fallback before any slider widgets exist.
            rows_height = self.fontMetrics().lineSpacing() * 6

        spacing = self.sidebar.spacing() * max(0, visible_rows - 1)
        margins = self.sidebar.contentsMargins()
        frame = self.slider_scroll_area.frameWidth() * 2
        target_height = rows_height + spacing + margins.top() + margins.bottom() + frame

        self.slider_scroll_area.setMinimumHeight(target_height)
        self.slider_scroll_area.setMaximumHeight(target_height)

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

    def _clear_loaded_data_views(self) -> None:
        """Clear field, slider, details, and plot UI for a fresh dataset open."""
        self.current_file_path = None
        self.setWindowTitle(self.base_window_title)
        self.current_selection_info_text = "No selection info available."
        info_widget = getattr(self, "plot_info_output", None)
        if info_widget is not None:
            info_widget.setPlainText(self.current_selection_info_text)

        self._set_field_list_hint("Open a file to see fields")
        self.build_dynamic_sliders({})
        self._set_selection_info_panel_visible(True)
        self._update_selection_info_toggle_button()
        self._set_plot_loading(False)
        self._clear_plot_canvas("Waiting for data...")

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
            "NetCDF files (*.nc *.nc4 *.cdf);;All files (*)"
        )
        if not file_path:
            return
        self._set_window_title_for_file(file_path)
        logger.info("Selected file: %s", file_path)
        self._record_recent_file(file_path)
        self.on_file_selected(file_path)


    def _choose_folder(self) -> None:
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "Select Data Folder",
            ""
        )
        if not folder_path:
            return
        self._set_window_title_for_file(folder_path)
        logger.info("Selected folder: %s", folder_path)
        self._record_recent_file(folder_path)
        self.on_file_selected(folder_path)

    def _show_not_implemented_dialog(self, capability: str) -> None:
        """Show a temporary placeholder dialog for unfinished open modes."""
        QMessageBox.information(
            self,
            "Not implemented",
            f"{capability} is not implemented yet.",
        )

    def _choose_glob(self) -> None:
        """Open files using a user-provided local glob expression."""
        initial_directory = str(Path.home())
        if self.current_file_path:
            current_path = Path(self.current_file_path).expanduser()
            initial_directory = str(current_path if current_path.is_dir() else current_path.parent)

        expression, ok = OpenGlobDialog.get_glob_expression(self, initial_directory)
        if not ok:
            return

        matches = glob.glob(expression, recursive=True)
        if not matches:
            QMessageBox.warning(
                self,
                "No matches",
                f"No files matched:\n{expression}",
            )
            return

        self._set_window_title_for_file(expression)
        logger.info("Selected glob expression: %s (%d matches)", expression, len(matches))
        self._record_recent_file(expression)
        self.on_file_selected(expression)

    def _choose_uris(self) -> None:
        """Show URI dialog and return selected URI for worker-backed windows."""
        default_uri = self._default_open_uri_value()
        uri, ok, quit_requested = OpenURIDialog.get_uri(self, default_uri=default_uri)
        if quit_requested:
            return
        if not ok or not uri:
            return
        self._show_status_message("Open URI is handled by worker-backed windows.", is_error=True)

    def _configure_remote(self) -> None:
        """Show remote configuration dialog and optionally open with the chosen config."""
        raw_state = self._settings.get("last_remote_configuration", {})
        state = dict(raw_state) if isinstance(raw_state, dict) else {}
        https_locations = self._settings.get("remote_https_locations")
        if not isinstance(https_locations, dict):
            https_locations = self._settings.get("remote_http_locations")
        if isinstance(https_locations, dict) and https_locations:
            state["https_locations"] = dict(https_locations)
        config, ok, next_state = RemoteConfigurationDialog.get_configuration(self, state=state)
        self._settings["last_remote_configuration"] = next_state
        if isinstance(next_state, dict):
            persisted_https = next_state.get("https_locations")
            if not isinstance(persisted_https, dict):
                persisted_https = next_state.get("http_locations")
            if isinstance(persisted_https, dict):
                self._settings["remote_https_locations"] = dict(persisted_https)
        self._save_settings()
        if not ok or config is None:
            return
        selected_uri, selected_ok = RemoteFileNavigatorDialog.get_remote_selection(self, config)
        if not selected_ok:
            return
        self._set_window_title_for_file(selected_uri)
        self._show_status_message(f"Selected remote file: {selected_uri}")

    def _choose_remote(self) -> None:
        """Show open-remote dialog and open using the selected saved short name."""
        raw_state = self._settings.get("last_remote_open", {})
        state = raw_state if isinstance(raw_state, dict) else {}
        if isinstance(state, dict):
            merged_http: dict[str, object] = {}

            configured_state = self._settings.get("last_remote_configuration")
            if isinstance(configured_state, dict):
                cfg_http = configured_state.get("https_locations")
                if not isinstance(cfg_http, dict):
                    cfg_http = configured_state.get("http_locations")
                if isinstance(cfg_http, dict):
                    merged_http.update(cfg_http)

            http_locations = self._settings.get("remote_https_locations")
            if not isinstance(http_locations, dict):
                http_locations = self._settings.get("remote_http_locations")
            if isinstance(http_locations, dict):
                merged_http.update(http_locations)

            if merged_http:
                state = dict(state)
                state["https_locations"] = dict(merged_http)

        config, ok, next_state = RemoteOpenDialog.get_configuration(self, state=state)
        self._settings["last_remote_open"] = next_state
        self._save_settings()
        if isinstance(next_state, dict) and bool(next_state.get("configure_new_remote")):
            self._configure_remote()
            return
        if not ok or config is None:
            return
        selected_uri, selected_ok = RemoteFileNavigatorDialog.get_remote_selection(self, config)
        if not selected_ok:
            return
        self._set_window_title_for_file(selected_uri)
        self._show_status_message(f"Selected remote file: {selected_uri}")

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

    def _show_lineplot_options_dialog(self) -> None:
        """Show lineplot options dialog and persist selected options."""
        self.lineplot_options_controller.show_lineplot_options_dialog()

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

    def _request_plot_data_save(self, file_path: str) -> None:
        """Hook for worker-backed implementations to save selected field data."""
        logger.debug("Requested data save to: %s", file_path)

    def _request_plot_save_all(
        self,
        save_code_path: str,
        save_plot_path: str,
        save_data_path: str,
    ) -> None:
        """Hook for worker-backed implementations to save code, plot, and data."""
        logger.debug(
            "Requested save-all code=%s plot=%s data=%s",
            save_code_path,
            save_plot_path,
            save_data_path,
        )

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
