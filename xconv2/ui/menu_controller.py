from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu, QToolButton

if TYPE_CHECKING:
    from xconv2.core_window import CFVCore


class MenuController:
    """Build and refresh top-level menu UI for the core window."""

    def __init__(self, host: "CFVCore") -> None:
        self.host = host

    def setup_menu_bar(self) -> None:
        """Create application menu actions."""
        menu_bar = self.host.menuBar()
        menu_bar.setNativeMenuBar(False)
        menu_font = menu_bar.font()
        menu_font_size_px = max(int(round(menu_font.pointSizeF())), 10)
        menu_font_weight = int(menu_font.weight())
        menu_bar.setStyleSheet(
            "QMenuBar {"
            " background-color: #186f4d;"
            " border-bottom: 1px solid #555;"
            " padding: 2px;"
            "}"
            "QMenuBar::item {"
            f" font-size: {menu_font_size_px}px;"
            f" font-weight: {menu_font_weight};"
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


        open_file_action = QAction("Open File... ", self.host)
        open_file_action.setShortcut(QKeySequence.StandardKey.Open)
        open_file_action.triggered.connect(self.host._choose_file)
        file_menu.addAction(open_file_action)

        open_zarr_action = QAction("Open Folder/Zarr...", self.host)
        open_zarr_action.setShortcut("Ctrl+Shift+O")
        open_zarr_action.triggered.connect(self.host._choose_folder)
        file_menu.addAction(open_zarr_action)

        open_glob_action = QAction("Open Glob...", self.host)
        open_glob_action.triggered.connect(self.host._choose_glob)
        file_menu.addAction(open_glob_action)

        configure_remote_action = QAction("Configure Remote...", self.host)
        configure_remote_action.triggered.connect(self.host._configure_remote)
        file_menu.addAction(configure_remote_action)

        open_remote_action = QAction("Open Remote...", self.host)
        open_remote_action.triggered.connect(self.host._choose_remote)
        file_menu.addAction(open_remote_action)

        open_uris_action = QAction("Open URIs...", self.host)
        open_uris_action.triggered.connect(self.host._choose_uris)
        file_menu.addAction(open_uris_action)

        self.host.recent_menu = file_menu.addMenu("Recent")
        self.refresh_recent_menu()

        file_menu.addSeparator()

        settings_action = QAction("Settings...", self.host)
        settings_action.triggered.connect(self.host._show_settings_dialog)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self.host)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.host._quit_application)
        file_menu.addAction(quit_action)

        self._setup_help_menu(menu_bar, menu_font_size_px, menu_font_weight)

    def _setup_help_menu(self, menu_bar, menu_font_size_px: int, menu_font_weight: int) -> None:
        """Attach Help pinned to the right while left-side menus grow normally."""
        help_menu = QMenu("Help", self.host)

        about_action = QAction("About", self.host)
        about_action.triggered.connect(self.host._show_about_dialog)
        help_menu.addAction(about_action)

        report_issue_action = QAction("Report Issue", self.host)
        report_issue_action.triggered.connect(self.host._open_issue_tracker)
        help_menu.addAction(report_issue_action)

        roadmap_action = QAction("xconv2 Roadmap", self.host)
        roadmap_action.triggered.connect(self.host._open_roadmap)
        help_menu.addAction(roadmap_action)

        help_button = QToolButton(menu_bar)
        help_button.setText("Help")
        help_button.setFont(menu_bar.font())
        help_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        help_button.setAutoRaise(True)
        help_button.setPopupMode(QToolButton.InstantPopup)
        help_button.setMenu(help_menu)
        help_button.setStyleSheet(
            "QToolButton {"
            f" font-size: {menu_font_size_px}px;"
            f" font-weight: {menu_font_weight};"
            " color: #f0f0f0;"
            " padding: 4px 10px;"
            " background: transparent;"
            " border: none;"
            " border-radius: 4px;"
            "}"
            "QToolButton:hover {"
            " background-color: #4a4a4a;"
            "}"
            "QToolButton::menu-indicator {"
            " image: none;"
            " width: 0px;"
            "}"
        )

        menu_bar.setCornerWidget(help_button, Qt.TopRightCorner)

    def refresh_recent_menu(self) -> None:
        """Refresh the Recent submenu from persisted settings."""
        self.host.recent_menu.clear()
        recent_files = self.host._load_recent_files()

        if not recent_files:
            empty_action = QAction("No recent files", self.host)
            empty_action.setEnabled(False)
            self.host.recent_menu.addAction(empty_action)
            return

        for file_path in recent_files:
            label = self.host._recent_menu_label(file_path)
            action = QAction(label, self.host)
            action.setToolTip(self.host._recent_menu_tooltip(file_path))
            action.triggered.connect(
                lambda checked=False, p=file_path: self.host._open_recent_file(p)
            )
            self.host.recent_menu.addAction(action)
