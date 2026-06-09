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

        xconv_menu = menu_bar.addMenu("Xconv")
        settings_action = QAction("Settings...", self.host)
        settings_action.triggered.connect(self.host._show_settings_dialog)
        xconv_menu.addAction(settings_action)

        configure_remote_action = QAction("Configure Remote...", self.host)
        configure_remote_action.triggered.connect(self.host._configure_remote)
        xconv_menu.addAction(configure_remote_action)

        cache_manager_action = QAction("Manage Cache...", self.host)
        cache_manager_action.triggered.connect(self.host._show_cache_manager)
        xconv_menu.addAction(cache_manager_action)

        view_logs_action = QAction("View Logs", self.host)
        view_logs_action.triggered.connect(self.host._view_logs)
        xconv_menu.addAction(view_logs_action)

        xconv_menu.addSeparator()

        quit_action = QAction("Quit", self.host)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.host._quit_application)
        xconv_menu.addAction(quit_action)

        file_menu = menu_bar.addMenu("&Input")


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

        open_remote_action = QAction("Browse Remote...", self.host)
        open_remote_action.triggered.connect(self.host._browse_remote)
        file_menu.addAction(open_remote_action)

        open_uris_action = QAction("Open URIs...", self.host)
        open_uris_action.triggered.connect(self.host._choose_uris)
        file_menu.addAction(open_uris_action)

        load_run_prov_action = QAction("Load & Run Prov", self.host)
        load_run_prov_action.triggered.connect(self.host._input_load_and_run_prov)
        file_menu.addAction(load_run_prov_action)

        self.host.recent_menu = file_menu.addMenu("Recent")
        self.refresh_recent_menu()

        file_menu.addSeparator()

        field_ops_menu = menu_bar.addMenu("Operations")
        apply_selection_action = QAction("Apply Selection", self.host)
        apply_selection_action.triggered.connect(self.host._field_ops_apply_selection)
        field_ops_menu.addAction(apply_selection_action)

        add_bounds_action = QAction("Add Bounds", self.host)
        add_bounds_action.triggered.connect(self.host._field_ops_add_bounds)
        field_ops_menu.addAction(add_bounds_action)

        regrid_action = QAction("Regrid", self.host)
        regrid_action.triggered.connect(self.host._field_ops_regrid)
        field_ops_menu.addAction(regrid_action)

        replay_action = QAction("Replay Last Field Operations", self.host)
        replay_action.triggered.connect(self.host._field_ops_replay_last_operations)
        field_ops_menu.addAction(replay_action)

        field_ops_menu.addSeparator()

        maths_menu = field_ops_menu.addMenu("Maths")

        difference_ab_action = QAction("Difference (A-B)", self.host)
        difference_ab_action.triggered.connect(self.host._field_ops_maths_difference_ab)
        maths_menu.addAction(difference_ab_action)

        difference_ba_action = QAction("Difference (B-A)", self.host)
        difference_ba_action.triggered.connect(self.host._field_ops_maths_difference_ba)
        maths_menu.addAction(difference_ba_action)

        maths_menu.addSeparator()

        grad_action = QAction("Grad", self.host)
        grad_action.triggered.connect(self.host._field_ops_maths_grad)
        maths_menu.addAction(grad_action)

        laplacian_action = QAction("Laplacian", self.host)
        laplacian_action.triggered.connect(self.host._field_ops_maths_laplacian)
        maths_menu.addAction(laplacian_action)

        convolution_action = QAction("Convolution", self.host)
        convolution_action.triggered.connect(self.host._field_ops_maths_convolution)
        maths_menu.addAction(convolution_action)

        moving_window_action = QAction("Moving Window", self.host)
        moving_window_action.triggered.connect(self.host._field_ops_maths_moving_window)
        maths_menu.addAction(moving_window_action)

        file_ops_menu = menu_bar.addMenu("Output")
        save_selected_action = QAction("Save Selected", self.host)
        save_selected_action.triggered.connect(self.host._file_ops_save_selected)
        file_ops_menu.addAction(save_selected_action)

        save_selected_prov_action = QAction("Save Selected Provenance", self.host)
        save_selected_prov_action.triggered.connect(self.host._file_ops_save_selected_provenance)
        file_ops_menu.addAction(save_selected_prov_action)

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
