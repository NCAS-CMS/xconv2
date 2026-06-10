"""Worker-backed window classes for cf-view.

This module layers backend interaction onto `CFVCore`:
- starts/stops the worker process
- sends worker tasks
- handles stdout/stderr protocol messages
"""

from __future__ import annotations

import base64
from collections import deque
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
import sys
from urllib.parse import urlparse

import psutil

from PySide6.QtCore import QEventLoop, QProcess, QTimer, Qt
from PySide6.QtGui import QCloseEvent, QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel, QInputDialog, QLineEdit, QListWidgetItem, QMessageBox

from .cf_templates import (
    add_dimension_coordinate_bounds,
    build_vector_overplot_command,
    apply_selection_field_operation,
    binary_field_operation,
    contour_range_from_selection,
    coordinate_list,
    field_list,
    plot_from_selection,
    regrid_fields_operation,
    remove_selected_fields,
    save_data_from_selection,
    save_selected_fields_task,
    filter_axes_for_field,
    filter_field_operation,
    unary_xy_field_operation,
)
from .core_window import CFVCore
from .coordinate_subspace import parse_coordinate_subspace_commands
from .main_window_components import plot_ops as _plot_ops
from .main_window_components import remote_auth_ops as _remote_auth_ops
from .main_window_components import remote_flow_ops as _remote_flow_ops
from .main_window_components import replay_ops as _replay_ops
from .worker_message_router import WorkerMessageRouter
# Remote-access helpers are imported lazily (inside the methods that use them)
# so that p5rem/paramiko are not loaded at GUI startup.
from .ui.dialogs import (
    FilterDialog,
    OpenURIDialog,
    ReplayOperationsDialog,
    RegridDialog,
    RemoteConfigurationDialog,
    RemoteOpenDialog,
    SaveSelectedFieldsDialog,
)

if TYPE_CHECKING:
    from .ui.remote_file_navigator import RemoteLoginLogDialog

logger = logging.getLogger(__name__)


def _normalize_source_path_or_uri(source: str) -> str:
    """Return expanded local paths while preserving URI forms."""
    text = str(source).strip()
    if urlparse(text).scheme:
        return CFVCore._canonical_remote_uri(text)
    return str(Path(text).expanduser())

def _get_worker_path() -> str:
    """Find the worker executable, supporting both dev and frozen (PyInstaller) environments.

    In PyInstaller one-dir builds the worker binary is collected as a BINARY
    type.  At runtime this ends up at different locations:

    * ``dist/xconv2/_internal/cf-worker`` (one-dir collect output, dev/CI)
    * ``Contents/Frameworks/cf-worker`` (macOS ``.app`` bundle - PyInstaller 6
      moves BINARY files from ``_internal/`` to ``Frameworks/`` when assembling
      the macOS bundle; ``sys._MEIPASS`` therefore points to ``Frameworks/``)
    * ``Contents/MacOS/cf-worker`` (legacy two-one-file bundles)

    We search all known locations and return the first that exists.
    """
    name = "cf-worker"
    if sys.platform == "win32":
        name += ".exe"

    if not getattr(sys, "frozen", False):
        # Development / editable install: worker is a sibling of the GUI launcher.
        return str(Path(sys.executable).parent / name)

    # Frozen (PyInstaller) – try candidate paths in priority order.
    candidates: list[Path] = []

    # 1. sys._MEIPASS — set by the bootloader; for one-dir macOS bundles this
    #    resolves to Contents/Frameworks/, which is where BINARY files land.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / name)

    exe_dir = Path(sys.executable).parent
    # 2. Explicit macOS Frameworks directory (one-dir .app layout).
    if sys.platform == "darwin":
        candidates.append(exe_dir.parent / "Frameworks" / name)
    # 3. Same directory as the launcher (legacy two-one-file layout / non-macOS).
    candidates.append(exe_dir / name)

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    # Nothing found – return the most-likely path so the missing-file error
    # message shows a meaningful location.
    return str(candidates[-1] if candidates else exe_dir / name)


class CFVMain(CFVCore):
    """Worker-backed application behavior layered on top of the core GUI."""

    def __init__(self) -> None:
        super().__init__()
        self._memory_status_label = QLabel("Mem: --")
        fixed_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        fixed_font.setPointSize(10)
        self._memory_status_label.setFont(fixed_font)
        self._memory_status_timer = QTimer(self)
        self._memory_status_timer.setInterval(1000)
        self._memory_status_timer.timeout.connect(self._update_memory_status)
        self._plot_request_in_flight = False
        self._plot_request_expects_image = False
        self._suppress_stale_error_status = False
        self._remote_session_id: str | None = None
        self._remote_descriptor_hash: str | None = None
        self._remote_descriptor: dict[str, object] | None = None
        self._last_remote_config: dict[str, object] | None = None
        self._last_remote_navigator_state: tuple[list[str], str] | None = None
        self._pending_worker_task_starts: deque[float] = deque()
        self._pending_prepare_loop: QEventLoop | None = None
        self._pending_prepare_loop_ok: bool = False
        self._pending_prepare_failure_message: str = ""
        self._pending_prepare_log_dialog: RemoteLoginLogDialog | None = None
        self._pending_list_loop: QEventLoop | None = None
        self._pending_list_result: dict | None = None
        self._pending_remote_open_loop: QEventLoop | None = None
        self._pending_remote_open_result: dict[str, object] | None = None
        self._pending_metadata_loop: QEventLoop | None = None
        self._pending_metadata_received: bool = False
        self._pending_metadata_error: str = ""
        self._ssh_session_passwords: dict[str, str] = {}
        self._selected_field_indices: list[int] = []
        self._loaded_file_paths: list[str] = []
        self._pending_metadata_append: bool = False
        self._pending_metadata_source: str | None = None
        self._pending_reselect_field_index: int | None = None
        self._pending_field_op_source: str | None = None
        self._pending_binary_operation_name: str | None = None
        self._shutting_down: bool = False
        self._replay_session_id: str = str(uuid.uuid4())
        self._worker_output_router = WorkerMessageRouter(self, main_cls=CFVMain)

        self.worker = QProcess()
        self.worker.readyReadStandardOutput.connect(self.handle_worker_output)
        self.worker.readyReadStandardError.connect(self.handle_worker_error)
        self.worker.errorOccurred.connect(self.handle_worker_process_error)
        self.worker.finished.connect(self.handle_worker_finished)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_worker)


        _worker_bin = _get_worker_path()
        if not Path(_worker_bin).exists():
            logger.error("Worker executable not found: %s", _worker_bin)
            QTimer.singleShot(0, lambda: (
                QMessageBox.critical(
                    None,
                    "Worker not found",
                    f"The cf-worker executable was not found:\n\n{_worker_bin}\n\n"
                    "Please reinstall xconv2 or check your PATH.",
                ),
                QApplication.instance().exit(1),  # type: ignore[union-attr]
            ))
            return
        self.worker.start(_worker_bin)
        logger.info("Started worker process: %s", self.worker.program())
        self.status.addPermanentWidget(self._memory_status_label)
        self._update_memory_status()
        self._memory_status_timer.start()
        # Show an initialisation indicator immediately after the first paint so
        # the user knows the worker is loading even before it sends STATUS:.
        QTimer.singleShot(0, lambda: self._show_status_message("Initialising worker…"))

    def on_file_selected(self, file_path: str) -> None:
        """Handle file selection by requesting worker metadata."""
        normalized_path = _normalize_source_path_or_uri(file_path)
        if getattr(self, "file_open_mode", "single") == "multi":
            if normalized_path in self._loaded_file_paths:
                self._show_status_message(f"File already loaded: {normalized_path}")
                return

            had_existing_sources = bool(self._loaded_file_paths)
            self._loaded_file_paths.append(normalized_path)
            refresh_menu = getattr(self, "_refresh_open_files_menu", None)
            if callable(refresh_menu):
                refresh_menu()
            self._load_selected_file(
                normalized_path,
                clear_existing=not had_existing_sources,
                append_metadata=had_existing_sources,
            )
            self.setWindowTitle(f"{self.base_window_title}: {len(self._loaded_file_paths)} files")
            return

        self._loaded_file_paths = [normalized_path]
        refresh_menu = getattr(self, "_refresh_open_files_menu", None)
        if callable(refresh_menu):
            refresh_menu()
        self._load_selected_file(normalized_path)

    def on_files_selected(self, file_paths: list[str]) -> None:
        """Handle multi-file selection by requesting combined worker metadata."""
        normalized = [_normalize_source_path_or_uri(path) for path in file_paths]
        if not normalized:
            return

        if getattr(self, "file_open_mode", "single") == "multi":
            for path in normalized:
                if path not in self._loaded_file_paths:
                    self._loaded_file_paths.append(path)

            refresh_menu = getattr(self, "_refresh_open_files_menu", None)
            if callable(refresh_menu):
                refresh_menu()

            self._load_selected_files(list(self._loaded_file_paths))
            self.setWindowTitle(f"{self.base_window_title}: {len(self._loaded_file_paths)} files")
            return

        first = normalized[0]
        self._loaded_file_paths = [first]
        refresh_menu = getattr(self, "_refresh_open_files_menu", None)
        if callable(refresh_menu):
            refresh_menu()
        self._load_selected_file(first)

    def on_field_clicked(self, item: QListWidgetItem) -> None:
        """Show selection details and request slider coordinates for the field."""
        super().on_field_clicked(item)

        selected_items: list[QListWidgetItem] = []
        selected_items_fn = getattr(self.field_list_widget, "selectedItems", None)
        if callable(selected_items_fn):
            selected_items = list(selected_items_fn())

        if len(selected_items) > 1:
            self._set_selection_panel_mode("multi")
            self._selected_field_indices = [
                idx for idx in (self.field_list_widget.row(x) for x in selected_items) if idx >= 0
            ]
            self.build_dynamic_sliders({})
            self._show_status_message(
                f"{len(selected_items)} fields selected. Enter coordinate bounds commands for multi-field operations."
            )
            return

        self._set_selection_panel_mode("single")
        self._reset_ui_for_new_field_selection()

        field_index = self.field_list_widget.row(item)
        if field_index < 0:
            return

        self._selected_field_indices = [field_index]
        self._request_coordinates_for_field(field_index, show_status=False)

    def on_field_selection_changed(self) -> None:
        """Track selected field indices to support mode-specific selection behavior."""
        super().on_field_selection_changed()
        selected_items = self.field_list_widget.selectedItems()
        self._selected_field_indices = [
            idx for idx in (self.field_list_widget.row(item) for item in selected_items) if idx >= 0
        ]
        if len(self._selected_field_indices) > 1:
            self._set_selection_panel_mode("multi")

    def _on_coordinate_bounds_input_changed(self) -> None:
        """Validate command-based coordinate bounds as the user edits input."""
        text = self._coordinate_subspace_command_text()
        if not text:
            return

        try:
            parse_coordinate_subspace_commands(text)
        except ValueError as exc:
            self._show_status_message(str(exc), is_error=True)
            return

        self._show_status_message("Coordinate bounds commands parsed successfully.")

    def _reset_ui_for_new_field_selection(self) -> None:
        """Clear stale error/loading UI state before handling a fresh field selection."""
        self._plot_request_in_flight = False
        self._plot_request_expects_image = False
        self._suppress_stale_error_status = True
        self._set_selection_info_panel_visible(True)
        self._update_selection_info_toggle_button()
        self._set_plot_loading(False)
        self._clear_plot_canvas("Waiting for data...")
        self._show_status_message("Task Complete")

    def handle_worker_output(self) -> None:
        """Process worker stdout messages and route updates to UI."""
        router = getattr(self, "_worker_output_router", None)
        if router is None:
            router = WorkerMessageRouter(self, main_cls=CFVMain)
            self._worker_output_router = router

        while self.worker.canReadLine():
            line = self.worker.readLine().data().decode().strip()
            if not line:
                continue

            logger.debug("Worker stdout line: %s", line)
            router.handle_line(line)

    def handle_worker_error(self) -> None:
        """Log any worker stderr output as errors."""
        stderr_output = self.worker.readAllStandardError().data().decode(errors="replace").strip()
        if not stderr_output:
            return

        for raw_line in stderr_output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            logger.error("Worker stderr: %s", line)
            self._maybe_show_binary_validation_dialog(line)

    def _maybe_show_binary_validation_dialog(self, stderr_line: str) -> None:
        """Show user-facing dialog for any pending binary-operation failure."""
        if not self._pending_binary_operation_name:
            return

        message = stderr_line.strip()
        if not message:
            return

        if message.startswith("Error -"):
            message = message[len("Error -"):].strip()

        for prefix in ("ValueError:", "IndexError:", "RuntimeError:", "TypeError:", "Exception:"):
            if message.startswith(prefix):
                message = message[len(prefix):].strip()
                break

        if not message:
            return

        QMessageBox.warning(self, self._pending_binary_operation_name, message)
        self._show_status_message(message, is_error=True)
        self._pending_binary_operation_name = None

    def handle_worker_process_error(self, process_error: QProcess.ProcessError) -> None:
        """Capture QProcess-level failures, such as start or crash issues."""
        if self._shutting_down:
            return
        logger.error("Worker process error: %s", process_error)
        message = f"Worker process error: {process_error}"
        self._show_status_message(message, is_error=True)
        self._maybe_show_binary_validation_dialog(message)
        if self._plot_request_in_flight:
            self._plot_request_in_flight = False
            self._plot_request_expects_image = False
            self._set_plot_loading(False)
            self._clear_plot_canvas("Plot failed because the worker encountered an error.")

    def handle_worker_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """Capture worker shutdown information."""
        logger.warning("Worker finished with exit_code=%s exit_status=%s", exit_code, exit_status)
        if exit_code != 0:
            message = f"Worker stopped unexpectedly (exit_code={exit_code})."
            self._show_status_message(message, is_error=True)
            self._maybe_show_binary_validation_dialog(message)
        if self._plot_request_in_flight:
            self._plot_request_in_flight = False
            self._plot_request_expects_image = False
            self._set_plot_loading(False)
            self._clear_plot_canvas("Plot failed because the worker stopped.")

    def _load_selected_file(
        self,
        file_path: str,
        *,
        clear_existing: bool = True,
        append_metadata: bool = False,
    ) -> None:
        """Load selected file in worker and publish field metadata."""
        if clear_existing:
            self._clear_loaded_data_views()
        self._pending_metadata_append = append_metadata
        self._pending_metadata_source = file_path
        self._show_status_message(f"Loading file: {file_path}")
        logger.info("Loading file in worker: %s", file_path)

        if append_metadata:
            code = (
                f"_cfview_file_path = {file_path!r}\n"
                "_cfview_field_index = None\n"
                f"_cfview_new_fields = cf.read({file_path!r})\n"
                "try:\n"
                "    f\n"
                "except NameError:\n"
                "    f = []\n"
                "f.extend(_cfview_new_fields)\n"
                "fields = field_info(_cfview_new_fields)\n"
                "send_to_gui('METADATA', fields)"
            )
        else:
            code = (
                f"_cfview_file_path = {file_path!r}\n"
                "_cfview_field_index = None\n"
                f"f = cf.read({file_path!r})\n"
                + field_list
                + "send_to_gui('METADATA', fields)"
            )
        self._send_worker_task(code)

    def _load_selected_files(self, file_paths: list[str]) -> None:
        """Load multiple selected files in worker and publish combined metadata."""
        if not file_paths:
            return

        expanded_paths = [_normalize_source_path_or_uri(path) for path in file_paths]
        self._clear_loaded_data_views()
        self._pending_metadata_append = False
        self._pending_metadata_source = None
        self._show_status_message(f"Loading {len(expanded_paths)} files")
        logger.info("Loading %d files in worker", len(expanded_paths))

        code = (
            f"_cfview_file_path = {expanded_paths!r}\n"
            "_cfview_field_index = None\n"
            f"f = cf.read({expanded_paths!r})\n"
            + field_list
            + "send_to_gui('METADATA', fields)"
        )
        self._send_worker_task(code)

    def _load_remote_selected_file(self, uri: str, remote_path: str) -> None:
        """Load a selected remote file through the worker remote session pool."""
        if not self._remote_session_id or not self._remote_descriptor_hash or not self._remote_descriptor:
            self._show_status_message("Remote worker session is not initialized.", is_error=True)
            return

        is_multi_mode = getattr(self, "file_open_mode", "single") == "multi"
        append_metadata = is_multi_mode and bool(self._loaded_file_paths)

        if is_multi_mode and uri in self._loaded_file_paths:
            self._show_status_message(f"Remote file already loaded: {uri}")
            return

        if not append_metadata:
            self._clear_loaded_data_views()
        self._pending_metadata_append = append_metadata
        self._pending_metadata_source = uri
        self._show_status_message(f"Loading remote file: {uri}")
        self._send_worker_control_task(
            "REMOTE_OPEN",
            {
                "session_id": self._remote_session_id,
                "descriptor_hash": self._remote_descriptor_hash,
                "descriptor": self._remote_descriptor,
                "uri": uri,
                "path": remote_path,
                "append": append_metadata,
            },
        )

    def _release_remote_session_if_active(self) -> None:
        """Release any worker-side warm remote session currently tracked by the UI."""
        if not self._remote_session_id or not self._remote_descriptor_hash:
            return

        self._send_worker_control_task(
            "REMOTE_RELEASE",
            {
                "session_id": self._remote_session_id,
                "descriptor_hash": self._remote_descriptor_hash,
            },
        )
        self._remote_session_id = None
        self._remote_descriptor_hash = None
        self._remote_descriptor = None

    def _open_remote_from_config(self, config: dict[str, object]) -> None:
        """Perform remote login once in the worker, then navigate via IPC using a nested QEventLoop."""
        _remote_flow_ops.open_remote_from_config(
            self,
            config,
            with_cache_defaults_fn=lambda payload: CFVMain._with_cache_defaults(self, payload),
            qeventloop_cls=QEventLoop,
            qapplication_cls=QApplication,
            qdialog_accepted_value=QDialog.Accepted,
            qmessagebox_cls=QMessageBox,
        )

    def _open_remote_uri_direct(
        self,
        *,
        uri: str,
        remote_path: str,
        config: dict[str, object],
        host_alias: str,
    ) -> None:
        """Open a specific remote URI directly without launching the navigator dialog."""
        _remote_flow_ops.open_remote_uri_direct(
            self,
            uri=uri,
            remote_path=remote_path,
            config=config,
            host_alias=host_alias,
            with_cache_defaults_fn=lambda payload: CFVMain._with_cache_defaults(self, payload),
            qeventloop_cls=QEventLoop,
            qapplication_cls=QApplication,
            qmessagebox_cls=QMessageBox,
        )

    def _resolve_remote_uri(self, uri: str) -> tuple[dict[str, object] | None, str, str, bool]:
        """Resolve URI into (config, remote_path, host_alias, unknown_host)."""
        return _remote_flow_ops.resolve_remote_uri(
            self,
            uri,
            canonical_remote_uri=CFVCore._canonical_remote_uri,
        )

    def _with_cache_defaults(self, config: dict[str, object]) -> dict[str, object]:
        """Attach persisted cache settings when a remote config omits cache."""
        return _remote_auth_ops.with_cache_defaults(self, config)

    def _configure_remote_for_uri(self, uri: str) -> None:
        """Open Configure Remote pre-populated for URI-driven add-new workflows."""
        _remote_flow_ops.configure_remote_for_uri(
            self,
            uri,
            remote_configuration_dialog_cls=RemoteConfigurationDialog,
        )

    @staticmethod
    def _probe_ssh_auth_methods(
        hostname: str,
        username: str,
        *,
        port: int = 22,
        timeout: float = 6.0,
    ) -> set[str] | None:
        """Probe SSH server auth methods quickly without waiting for filesystem auth timeout."""
        return _remote_auth_ops.probe_ssh_auth_methods(
            hostname,
            username,
            port=port,
            timeout=timeout,
        )

    @staticmethod
    def _validate_ssh_secret(
        hostname: str,
        username: str,
        secret: str,
        *,
        port: int = 22,
        timeout: float = 6.0,
    ) -> bool | None:
        """Validate an SSH password/secret; returns None when validation is inconclusive."""
        return _remote_auth_ops.validate_ssh_secret(
            hostname,
            username,
            secret,
            port=port,
            timeout=timeout,
        )

    @staticmethod
    def _parse_proxy_jump_target(proxy_jump: str) -> tuple[str | None, str, int]:
        """Parse first ProxyJump hop into (user, host-or-alias, port)."""
        return _remote_auth_ops.parse_proxy_jump_target(proxy_jump)

    @staticmethod
    def _resolve_ssh_alias(alias: str) -> tuple[str, str | None]:
        """Resolve SSH config alias to concrete hostname/user when available."""
        return _remote_auth_ops.resolve_ssh_alias(alias)

    def _prompt_ssh_secret(
        self,
        *,
        title: str,
        prompt: str,
    ) -> tuple[str, bool]:
        """Prompt the user for an SSH secret/response."""
        return _remote_auth_ops.prompt_ssh_secret(
            self,
            title=title,
            prompt=prompt,
            qinputdialog_cls=QInputDialog,
            qlineedit_cls=QLineEdit,
        )

    def _prepare_ssh_config_for_auth(self, config: dict[str, object]) -> dict[str, object] | None:
        """Inject transient SSH password credentials when preflight indicates a challenge."""
        return _remote_auth_ops.prepare_ssh_config_for_auth(
            self,
            config,
            default_probe_ssh_auth_methods_fn=CFVMain._probe_ssh_auth_methods,
            default_prompt_ssh_secret_fn=CFVMain._prompt_ssh_secret,
            validate_ssh_secret_fn=CFVMain._validate_ssh_secret,
            parse_proxy_jump_target_fn=CFVMain._parse_proxy_jump_target,
            resolve_ssh_alias_fn=CFVMain._resolve_ssh_alias,
            qmessagebox_cls=QMessageBox,
        )

    @staticmethod
    def _is_ssh_auth_failure_message(message: str) -> bool:
        """Return True when a worker prepare failure message looks like SSH auth failure."""
        return _remote_auth_ops.is_ssh_auth_failure_message(message)

    def _clear_ssh_cached_secrets_for_config(self, config: dict[str, object]) -> None:
        """Forget cached SSH secrets for target and bastion hosts in a config."""
        _remote_auth_ops.clear_ssh_cached_secrets_for_config(
            self,
            config,
            parse_proxy_jump_target_fn=CFVMain._parse_proxy_jump_target,
            resolve_ssh_alias_fn=CFVMain._resolve_ssh_alias,
        )

    def _maybe_retry_ssh_authentication(self, config: dict[str, object], failure_message: str) -> bool:
        """Offer auth retry for SSH prepare failures that look like authentication problems."""
        return _remote_auth_ops.maybe_retry_ssh_authentication(
            self,
            config,
            failure_message,
            is_ssh_auth_failure_message_fn=CFVMain._is_ssh_auth_failure_message,
            qmessagebox_cls=QMessageBox,
        )

    def _open_uri_entry(self, uri: str, *, from_uri_dialog: bool) -> None:
        """Open a URI from user input or recent list."""
        _remote_flow_ops.open_uri_entry(
            self,
            uri,
            from_uri_dialog=from_uri_dialog,
            canonical_remote_uri=CFVCore._canonical_remote_uri,
            qmessagebox_cls=QMessageBox,
        )

    def _configure_remote(self) -> None:
        """Open the full remote configuration dialog non-modally; Open proceeds to worker-backed navigation."""
        _remote_flow_ops.configure_remote(
            self,
            remote_configuration_dialog_cls=RemoteConfigurationDialog,
        )

    def _choose_remote(self) -> None:
        """Open using existing short names via a streamlined protocol picker dialog."""
        _remote_flow_ops.choose_remote(
            self,
            remote_open_dialog_cls=RemoteOpenDialog,
            with_cache_defaults_fn=lambda payload: CFVMain._with_cache_defaults(self, payload),
        )

    def _browse_remote(self) -> None:
        """Re-browse the active remote session, or prompt for a new one if none is active.

        If a remote session is already live (e.g. after opening a remote file), this skips
        the expensive REMOTE_PREPARE step and opens the navigator directly, restoring the
        previous tree state.  A "New Remote..." button in the navigator lets the user
        switch to a different remote at any time.
        """
        _remote_flow_ops.browse_remote(
            self,
            qdialog_accepted_value=QDialog.Accepted,
            qmessagebox_cls=QMessageBox,
        )

    def _choose_uris(self) -> None:
        """Show URI dialog and open supported URIs directly through the worker."""
        _remote_flow_ops.choose_uris(
            self,
            open_uri_dialog_cls=OpenURIDialog,
        )

    def _open_recent_file(self, file_path: str) -> None:
        """Open a recent entry, routing remote URIs through URI resolution flow."""
        _remote_flow_ops.open_recent_file(
            self,
            file_path,
            super_open_recent_file=super()._open_recent_file,
        )

    def _make_worker_list_callback(self):
        """Return a callable that lists a remote directory via worker IPC using a nested QEventLoop."""
        return _remote_flow_ops.make_worker_list_callback(
            self,
            qeventloop_cls=QEventLoop,
        )

    def _request_coordinates_for_field(self, index: int, show_status: bool = True) -> None:
        """Request coordinate arrays for a selected field index."""
        if show_status:
            self._show_status_message(f"Loading coordinates for field index {index}...")
        self._send_worker_task(coordinate_list(index))

    @staticmethod
    def _json_safe_operation_payload(value: object) -> object:
        """Return a JSON-compatible copy of operation payload data."""
        return _replay_ops.json_safe_operation_payload(value)

    def _last_operations_path(self) -> Path:
        """Return path to the replayable operations history file."""
        return _replay_ops.last_operations_path()

    def _load_last_operations_payload(self) -> dict[str, object]:
        """Load replay payload from disk, returning an empty schema when absent/invalid."""
        return _replay_ops.load_last_operations_payload(self)

    def _record_replayable_operation(self, operation: dict[str, object]) -> None:
        """Append one replayable field operation to disk."""
        _replay_ops.record_replayable_operation(self, operation)

    def _worker_code_for_replay_operation(self, operation: dict[str, object]) -> str | None:
        """Build worker task code for one replayable operation payload."""
        kind = str(operation.get("kind", "")).strip().lower()

        if kind == "unary_xy":
            field_index = operation.get("field_index")
            field_ref = operation.get("field_ref")
            operation_key = operation.get("operation")
            resolved_index = None
            if isinstance(field_ref, dict):
                resolved_index = CFVMain._resolve_field_reference_index(self, field_ref)
            if resolved_index is None and isinstance(field_index, int):
                resolved_index = field_index
            if isinstance(resolved_index, int) and isinstance(operation_key, str) and operation_key.strip():
                return unary_xy_field_operation(resolved_index, operation_key)
            return None

        if kind == "filter":
            field_index = operation.get("field_index")
            field_ref = operation.get("field_ref")
            config = operation.get("config")
            resolved_index = None
            if isinstance(field_ref, dict):
                resolved_index = CFVMain._resolve_field_reference_index(self, field_ref)
            if resolved_index is None and isinstance(field_index, int):
                resolved_index = field_index
            if isinstance(resolved_index, int) and isinstance(config, dict):
                return filter_field_operation(resolved_index, config)
            return None

        if kind == "binary":
            index_a = operation.get("index_a")
            index_b = operation.get("index_b")
            field_ref_a = operation.get("field_ref_a")
            field_ref_b = operation.get("field_ref_b")
            operation_key = operation.get("operation")
            source_files_raw = operation.get("source_files", [])
            source_files = []
            if isinstance(source_files_raw, list):
                source_files = [str(item) for item in source_files_raw if isinstance(item, str)]
            resolved_a = None
            resolved_b = None
            if isinstance(field_ref_a, dict):
                resolved_a = CFVMain._resolve_field_reference_index(self, field_ref_a)
            if isinstance(field_ref_b, dict):
                resolved_b = CFVMain._resolve_field_reference_index(self, field_ref_b)
            if resolved_a is None and isinstance(index_a, int):
                resolved_a = index_a
            if resolved_b is None and isinstance(index_b, int):
                resolved_b = index_b
            if (
                isinstance(resolved_a, int)
                and isinstance(resolved_b, int)
                and isinstance(operation_key, str)
                and operation_key.strip()
            ):
                return binary_field_operation(resolved_a, resolved_b, operation_key, source_files=source_files)
            return None

        if kind == "apply_selection":
            field_index = operation.get("field_index")
            field_ref = operation.get("field_ref")
            selections = operation.get("selections")
            collapse_by_coord = operation.get("collapse_by_coord")
            resolved_index = None
            if isinstance(field_ref, dict):
                resolved_index = CFVMain._resolve_field_reference_index(self, field_ref)
            if resolved_index is None and isinstance(field_index, int):
                resolved_index = field_index
            if (
                isinstance(resolved_index, int)
                and isinstance(selections, dict)
                and isinstance(collapse_by_coord, dict)
            ):
                return apply_selection_field_operation(resolved_index, selections, collapse_by_coord)
            return None

        if kind == "regrid":
            config = operation.get("config")
            if isinstance(config, dict):
                config_copy = dict(config)
                field_refs = operation.get("field_refs")
                if isinstance(field_refs, list) and field_refs:
                    resolved_indices: list[int] = []
                    for raw_ref in field_refs:
                        if not isinstance(raw_ref, dict):
                            continue
                        resolved = CFVMain._resolve_field_reference_index(self, raw_ref)
                        if isinstance(resolved, int):
                            resolved_indices.append(resolved)
                    if resolved_indices:
                        config_copy["field_indices"] = resolved_indices
                return regrid_fields_operation(json.dumps(config_copy, sort_keys=True))
            return None

        return None

    def _describe_replay_operation(self, operation: dict[str, object]) -> str:
        """Build a short, user-facing description for one replayable operation."""
        return _replay_ops.describe_replay_operation(operation)

    def _source_files_for_replay_operation(self, operation: dict[str, object]) -> list[str]:
        """Return ordered source-file hints associated with one replay operation."""
        return _replay_ops.source_files_for_replay_operation(operation)

    @staticmethod
    def _is_remote_source_uri(uri: str) -> bool:
        return _replay_ops.is_remote_source_uri(uri)

    def _open_remote_uri_for_replay_sync(self, uri: str) -> None:
        """Open one remote URI via the normal remote-control path and wait for REMOTE_OPEN_RESULT."""
        canonical_uri = CFVCore._canonical_remote_uri(uri)
        config, remote_path, host_alias, unknown_host = self._resolve_remote_uri(canonical_uri)
        if unknown_host or config is None:
            raise ValueError(f"Replay could not resolve remote source URI: {canonical_uri}")

        self._pending_remote_open_result = None
        self._pending_remote_open_loop = QEventLoop()
        open_loop = self._pending_remote_open_loop

        timeout = QTimer(self)
        timeout.setSingleShot(True)
        timeout.timeout.connect(open_loop.quit)
        timeout.start(15000)

        before_prepare_failure = str(getattr(self, "_pending_prepare_failure_message", "") or "")
        self._open_remote_uri_direct(
            uri=canonical_uri,
            remote_path=remote_path,
            config=config,
            host_alias=host_alias,
        )
        after_prepare_failure = str(getattr(self, "_pending_prepare_failure_message", "") or "")
        if after_prepare_failure and after_prepare_failure != before_prepare_failure:
            self._pending_remote_open_loop = None
            timeout.stop()
            raise ValueError(f"Replay remote preload failed for {canonical_uri}: {after_prepare_failure}")

        if self._pending_remote_open_result is None and self._pending_remote_open_loop is not None:
            open_loop.exec()

        timed_out = not bool(self._pending_remote_open_result)
        if timeout.isActive():
            timeout.stop()
        self._pending_remote_open_loop = None

        payload = self._pending_remote_open_result
        self._pending_remote_open_result = None
        if timed_out or not isinstance(payload, dict):
            raise ValueError(f"Replay remote preload timed out for source: {canonical_uri}")
        if not bool(payload.get("ok")):
            error = str(payload.get("error") or "Remote open failed")
            raise ValueError(f"Replay remote preload failed for {canonical_uri}: {error}")

    def _load_local_source_for_replay_sync(self, file_path: str, *, append: bool) -> None:
        """Load one local source via worker and wait for METADATA response."""
        loader = getattr(self, "_load_selected_file", None)
        if not callable(loader):
            return

        self._pending_metadata_received = False
        self._pending_metadata_error = ""
        self._pending_metadata_loop = QEventLoop()
        metadata_loop = self._pending_metadata_loop

        timeout = QTimer(self)
        timeout.setSingleShot(True)
        timeout.timeout.connect(metadata_loop.quit)
        timeout.start(15000)

        loader(file_path, clear_existing=False, append_metadata=append)

        if self._pending_metadata_loop is not None:
            metadata_loop.exec()

        timed_out = not self._pending_metadata_received and not bool(self._pending_metadata_error)
        if timeout.isActive():
            timeout.stop()
        self._pending_metadata_loop = None

        if self._pending_metadata_error:
            raise ValueError(f"Replay local preload failed for {file_path}: {self._pending_metadata_error}")
        if timed_out:
            raise ValueError(f"Replay local preload timed out for source: {file_path}")

    def _field_reference_for_index(self, index: int) -> dict[str, object] | None:
        """Build a stable field reference for one current list index."""
        item = self.field_list_widget.item(index)
        if item is None:
            return None

        identity = CFVMain._field_identity_from_item(self, item)
        if not identity:
            return None

        source_raw = item.data(Qt.UserRole + 2)
        source = str(source_raw).strip() if isinstance(source_raw, str) and source_raw.strip() else ""
        generated_raw = item.data(Qt.UserRole + 5)
        generated = bool(generated_raw) if isinstance(generated_raw, bool) else False

        occurrence = 0
        for idx in range(self.field_list_widget.count()):
            current = self.field_list_widget.item(idx)
            if current is None:
                continue
            if CFVMain._field_identity_from_item(self, current) != identity:
                continue
            current_source_raw = current.data(Qt.UserRole + 2)
            current_source = (
                str(current_source_raw).strip()
                if isinstance(current_source_raw, str) and current_source_raw.strip()
                else ""
            )
            current_generated_raw = current.data(Qt.UserRole + 5)
            current_generated = bool(current_generated_raw) if isinstance(current_generated_raw, bool) else False
            if current_source == source and current_generated == generated:
                occurrence += 1
            if idx == index:
                break

        return {
            "identity": identity,
            "source_file": source,
            "generated": generated,
            "occurrence": max(occurrence, 1),
        }

    def _resolve_field_reference_index(self, field_ref: dict[str, object]) -> int | None:
        """Resolve a stable field reference to the current list index."""
        identity_raw = field_ref.get("identity")
        if not isinstance(identity_raw, str) or not identity_raw.strip():
            return None
        identity = identity_raw.strip()

        source_raw = field_ref.get("source_file")
        source = str(source_raw).strip() if isinstance(source_raw, str) and source_raw.strip() else ""

        generated_raw = field_ref.get("generated")
        generated_filter = bool(generated_raw) if isinstance(generated_raw, bool) else None

        occurrence_raw = field_ref.get("occurrence")
        try:
            occurrence_target = int(occurrence_raw)
        except (TypeError, ValueError):
            occurrence_target = 1
        if occurrence_target < 1:
            occurrence_target = 1

        seen = 0
        for idx in range(self.field_list_widget.count()):
            item = self.field_list_widget.item(idx)
            if item is None:
                continue
            if CFVMain._field_identity_from_item(self, item) != identity:
                continue

            item_source_raw = item.data(Qt.UserRole + 2)
            item_source = (
                str(item_source_raw).strip()
                if isinstance(item_source_raw, str) and item_source_raw.strip()
                else ""
            )
            if source and item_source != source:
                continue

            if generated_filter is not None:
                item_generated_raw = item.data(Qt.UserRole + 5)
                item_generated = bool(item_generated_raw) if isinstance(item_generated_raw, bool) else False
                if item_generated != generated_filter:
                    continue

            seen += 1
            if seen == occurrence_target:
                return idx

        return None

    def _field_ops_replay_last_operations(self) -> None:
        """Replay persisted field operations by dispatching a worker control task."""
        _replay_ops.field_ops_replay_last_operations(
            self,
            replay_dialog_cls=ReplayOperationsDialog,
        )

    def _build_remote_open_requests_for_sources(self, sources: list[str]) -> list[dict[str, object]]:
        """Build worker remote-open descriptors for a list of replay/provenance sources."""
        return _replay_ops.build_remote_open_requests_for_sources(
            self,
            sources,
            is_remote_source_uri_fn=CFVMain._is_remote_source_uri,
            with_cache_defaults_fn=lambda payload: CFVMain._with_cache_defaults(self, payload),
        )

    def _file_ops_save_selected_provenance(self) -> None:
        """Save field-specific upstream provenance for selected fields."""
        _replay_ops.file_ops_save_selected_provenance(
            self,
            file_dialog_cls=QFileDialog,
        )

    @staticmethod
    def _workflow_payload_from_provenance_document(payload: object) -> dict[str, object] | None:
        """Normalize either internal workflow JSON or PROV-JSON into replay workflow payload."""
        return _replay_ops.workflow_payload_from_provenance_document(payload)

    def _input_load_and_run_prov(self) -> None:
        """Load internal/PROV workflow JSON and replay it through worker control messaging."""
        _replay_ops.input_load_and_run_prov(
            self,
            file_dialog_cls=QFileDialog,
            workflow_payload_from_provenance_document=CFVMain._workflow_payload_from_provenance_document,
        )

    def _field_identity_from_item(self, item: QListWidgetItem | None) -> str:
        """Return stable field identity text when available, else display text."""
        if item is None:
            return ""
        controller = getattr(self, "field_metadata_controller", None)
        resolver = getattr(controller, "field_identity_from_item", None)
        if callable(resolver):
            identity = resolver(item)
            if isinstance(identity, str) and identity:
                return identity
        return item.text() if hasattr(item, "text") else ""

    def _selected_field_index_for_operation(self, operation: str) -> int | None:
        """Return a single selected field index suitable for unary field operations."""
        selected = list(getattr(self, "_selected_field_indices", []))

        if not selected:
            item = self.field_list_widget.currentItem()
            if item is not None:
                idx = self.field_list_widget.row(item)
                if idx >= 0:
                    selected = [idx]

        if len(selected) != 1:
            message = f"{operation} requires exactly one selected field."
            logger.error(message)
            self._show_status_message(message, is_error=True)
            return None

        return selected[0]

    def _run_unary_xy_field_operation(self, operation_name: str, operation_key: str) -> None:
        """Dispatch unary XY field operation through worker-side template/helper code."""
        field_index = self._selected_field_index_for_operation(operation_name)
        if field_index is None:
            return
        self._pending_binary_operation_name = None

        source_file = None
        selected_item = self.field_list_widget.item(field_index)
        if selected_item is not None:
            raw_source = selected_item.data(Qt.UserRole + 2)
            if isinstance(raw_source, str) and raw_source.strip():
                source_file = raw_source
        self._pending_field_op_source = source_file

        self._show_status_message(f"Running {operation_name} on field index {field_index}...")
        logger.info("Running field op %s on field index %d", operation_name, field_index)

        self._record_replayable_operation(
            {
                "kind": "unary_xy",
                "field_index": field_index,
                "field_ref": CFVMain._field_reference_for_index(self, field_index),
                "selected_indices": [field_index],
                "operation": operation_key,
                "source_file": source_file,
            }
        )

        code = unary_xy_field_operation(field_index, operation_key)
        self._send_worker_task(code, emit_image=False)

    def _run_add_bounds_operation(self, operation_name: str) -> None:
        """Dispatch missing dimension-coordinate bounds creation through the worker."""
        field_index = self._selected_field_index_for_operation(operation_name)
        if field_index is None:
            return
        self._pending_binary_operation_name = None

        source_file = None
        selected_item = self.field_list_widget.item(field_index)
        if selected_item is not None:
            raw_source = selected_item.data(Qt.UserRole + 2)
            if isinstance(raw_source, str) and raw_source.strip():
                source_file = raw_source
        self._pending_field_op_source = source_file
        self._pending_metadata_source = source_file
        self._pending_metadata_append = False
        self._pending_reselect_field_index = field_index

        self._show_status_message(f"Adding bounds on field index {field_index}...")
        logger.info("Adding bounds on field index %d", field_index)

        code = add_dimension_coordinate_bounds(field_index)
        self._send_worker_task(code, emit_image=False)

    def _selected_two_field_indices_for_operation(self, operation: str) -> tuple[int, int] | None:
        """Return exactly two selected field indices for binary operations."""
        selected = [int(i) for i in getattr(self, "_selected_field_indices", []) if int(i) >= 0]
        if len(selected) != 2:
            self._show_status_message("Two fields need to be selected for difference", is_error=True)
            return None
        return selected[0], selected[1]

    def _run_binary_field_operation(self, operation_name: str, operation_key: str) -> None:
        """Dispatch binary field operation using exactly two selected fields."""
        pair = self._selected_two_field_indices_for_operation(operation_name)
        if pair is None:
            return
        self._pending_binary_operation_name = operation_name

        idx_a, idx_b = pair
        source_paths: list[str] = []
        for idx in (idx_a, idx_b):
            item = self.field_list_widget.item(idx)
            if item is None:
                continue
            raw_source = item.data(Qt.UserRole + 2)
            if isinstance(raw_source, str) and raw_source.strip():
                source_paths.append(raw_source)
        unique_sources = sorted(set(source_paths))
        self._pending_field_op_source = unique_sources[0] if len(unique_sources) == 1 else None

        self._show_status_message(f"Running {operation_name} on field indices {idx_a} and {idx_b}...")
        logger.info("Running binary field op %s on field indices %d and %d", operation_name, idx_a, idx_b)

        self._record_replayable_operation(
            {
                "kind": "binary",
                "index_a": idx_a,
                "index_b": idx_b,
                "field_ref_a": CFVMain._field_reference_for_index(self, idx_a),
                "field_ref_b": CFVMain._field_reference_for_index(self, idx_b),
                "selected_indices": [idx_a, idx_b],
                "operation": operation_key,
                "source_files": source_paths,
            }
        )

        code = binary_field_operation(idx_a, idx_b, operation_key, source_files=source_paths)
        self._send_worker_task(code, emit_image=False)

    def _process_rss_mib(self, pid: int | None) -> float | None:
        """Return RSS for a process in MiB, or None if unavailable."""
        if not isinstance(pid, int) or pid <= 0:
            return None

        try:
            process = psutil.Process(pid)
            return float(process.memory_info().rss) / (1024.0 * 1024.0)
        except Exception:
            return None

    def _update_memory_status(self) -> None:
        """Refresh the status-bar memory readout."""
        app_rss = self._process_rss_mib(os.getpid())
        worker_rss = self._process_rss_mib(self.worker.processId())

        if app_rss is None and worker_rss is None:
            text = "Mem: unavailable"
        elif worker_rss is None:
            text = f"Mem app: {app_rss:.0f} MiB | worker: --"
        elif app_rss is None:
            text = f"Mem app: -- | worker: {worker_rss:.0f} MiB"
        else:
            text = f"Mem app: {app_rss:.0f} MiB | worker: {worker_rss:.0f} MiB"

        self._memory_status_label.setText(text)

    def _field_ops_regrid(self) -> None:
        """Open the Regrid dialog for the currently selected fields."""
        selected_items = list(self.field_list_widget.selectedItems())
        if not selected_items:
            self._show_status_message("Select one or more fields to regrid.", is_error=True)
            return

        selected_rows: list[dict[str, object]] = []
        for item in selected_items:
            idx = self.field_list_widget.row(item)
            if idx < 0:
                continue
            selected_rows.append(
                {
                    "index": idx,
                    "identity": CFVMain._field_identity_from_item(self, item),
                }
            )

        if not selected_rows:
            self._show_status_message("No valid selected fields to regrid.", is_error=True)
            return

        dialog = RegridDialog(self, selected_rows, on_submit=self._run_regrid_operation)
        dialog.show()

    def _field_ops_apply_selection(self) -> None:
        """Apply current selection/collapse state and append result as a new field."""
        context = self._build_plot_context()
        if context is None:
            return

        field_index = self._selected_field_index_for_operation("Apply Selection")
        if field_index is None:
            return

        selections, collapse_by_coord, _plot_kind = context
        source_file = None
        selected_item = self.field_list_widget.item(field_index)
        if selected_item is not None:
            raw_source = selected_item.data(Qt.UserRole + 2)
            if isinstance(raw_source, str) and raw_source.strip():
                source_file = raw_source

        self._pending_binary_operation_name = None
        self._pending_field_op_source = source_file

        self._show_status_message(f"Applying selection on field index {field_index}...")
        logger.info(
            "Applying selection on field index %d with %d selection(s) and %d collapse(s)",
            field_index,
            len(selections),
            len(collapse_by_coord),
        )

        self._record_replayable_operation(
            {
                "kind": "apply_selection",
                "field_index": field_index,
                "field_ref": CFVMain._field_reference_for_index(self, field_index),
                "selected_indices": [field_index],
                "selections": selections,
                "collapse_by_coord": collapse_by_coord,
                "source_file": source_file,
            }
        )

        code = apply_selection_field_operation(field_index, selections, collapse_by_coord)
        self._send_worker_task(code, emit_image=False)

    def _run_regrid_operation(self, regrid_config: dict[str, object]) -> None:
        """Dispatch a regrid operation through worker-side JSON config parsing."""
        self._pending_binary_operation_name = None
        selected_indices = regrid_config.get("field_indices", [])
        if not isinstance(selected_indices, list) or not selected_indices:
            self._show_status_message("Regrid configuration did not include selected fields.", is_error=True)
            return

        source_paths: set[str] = set()
        for raw_idx in selected_indices:
            try:
                idx = int(raw_idx)
            except (TypeError, ValueError):
                continue
            item = self.field_list_widget.item(idx)
            if item is None:
                continue
            raw_source = item.data(Qt.UserRole + 2)
            if isinstance(raw_source, str) and raw_source.strip():
                source_paths.add(raw_source)

        sorted_sources = sorted(source_paths)
        self._pending_field_op_source = sorted_sources[0] if len(sorted_sources) == 1 else None

        target = str(regrid_config.get("target", "unknown"))
        self._show_status_message(f"Running Regrid for target {target}...")
        logger.info("Running regrid operation target=%s selected_count=%d", target, len(selected_indices))

        self._record_replayable_operation(
            {
                "kind": "regrid",
                "config": regrid_config,
                "field_refs": [
                    ref
                    for ref in (
                        CFVMain._field_reference_for_index(self, int(idx))
                        for idx in selected_indices
                        if isinstance(idx, int)
                    )
                    if isinstance(ref, dict)
                ],
                "source_files": sorted_sources,
            }
        )

        config_json = json.dumps(regrid_config, sort_keys=True)
        code = regrid_fields_operation(config_json)
        self._send_worker_task(code, emit_image=False)

    def _field_ops_add_bounds(self) -> None:
        """Create missing dimension-coordinate bounds on the selected field."""
        self._run_add_bounds_operation("Add Bounds")

    def _field_ops_maths_grad(self) -> None:
        """Create and append grad field via cf.Field.grad_xy."""
        self._run_unary_xy_field_operation("Grad", "grad")

    def _field_ops_maths_difference_ab(self) -> None:
        """Create and append binary difference field using first-selected minus second-selected."""
        self._run_binary_field_operation("Difference (A-B)", "difference_ab")

    def _field_ops_maths_difference_ba(self) -> None:
        """Create and append binary difference field using second-selected minus first-selected."""
        self._run_binary_field_operation("Difference (B-A)", "difference_ba")

    def _field_ops_maths_laplacian(self) -> None:
        """Create and append laplacian field via cf.Field.laplacian_xy."""
        self._run_unary_xy_field_operation("Laplacian", "laplacian")

    def _field_ops_maths_filter(self) -> None:
        """Open filter options dialog for one selected field."""
        field_index = self._selected_field_index_for_operation("Filter")
        if field_index is None:
            return

        axes = self._request_filter_axes_for_field(field_index)
        if not axes:
            self._show_status_message(
                "No filterable axes are available for this field (all candidate axes have size 1).",
                is_error=True,
            )
            return

        selected_item = self.field_list_widget.item(field_index)
        field_label = self._field_identity_from_item(selected_item) or f"field index {field_index}"

        dialog = FilterDialog(
            self,
            field_label=field_label,
            available_axes=axes,
            on_submit=lambda config: self._run_filter_field_operation(field_index, config),
        )
        dialog.show()

    def _request_filter_axes_for_field(self, field_index: int) -> list[str]:
        """Request filterable non-singleton axes from worker for one field index."""
        self._pending_filter_axes_result = None
        self._pending_filter_axes_loop = QEventLoop()
        loop = self._pending_filter_axes_loop

        timeout = QTimer(self)
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        timeout.start(8000)

        code = filter_axes_for_field(field_index)
        self._send_worker_task(code, emit_image=False)

        if self._pending_filter_axes_loop is not None:
            loop.exec()

        if timeout.isActive():
            timeout.stop()
        self._pending_filter_axes_loop = None

        axes_raw = self._pending_filter_axes_result
        self._pending_filter_axes_result = None
        if not isinstance(axes_raw, list):
            return []

        ordered: list[str] = []
        for axis in ("T", "Z", "Y", "X"):
            if axis in axes_raw and axis not in ordered:
                ordered.append(axis)
        return ordered

    def _run_filter_field_operation(self, field_index: int, config: dict[str, object]) -> None:
        """Dispatch configurable filter operation using one selected field."""
        self._pending_binary_operation_name = None

        source_file = None
        selected_item = self.field_list_widget.item(field_index)
        if selected_item is not None:
            raw_source = selected_item.data(Qt.UserRole + 2)
            if isinstance(raw_source, str) and raw_source.strip():
                source_file = raw_source
        self._pending_field_op_source = source_file

        method = str(config.get("method", "filter")).strip() or "filter"
        self._show_status_message(f"Running Filter ({method}) on field index {field_index}...")
        logger.info("Running filter op method=%s on field index %d", method, field_index)

        self._record_replayable_operation(
            {
                "kind": "filter",
                "field_index": field_index,
                "field_ref": CFVMain._field_reference_for_index(self, field_index),
                "selected_indices": [field_index],
                "config": config,
                "source_file": source_file,
            }
        )

        code = filter_field_operation(field_index, config)
        self._send_worker_task(code, emit_image=False)

    def _remove_selected_fields(self) -> None:
        """Remove selected fields from both the UI list and worker field list."""
        selected_items = list(self.field_list_widget.selectedItems())
        if not selected_items:
            self._show_status_message("Select one or more fields to remove.", is_error=True)
            return

        indices = sorted(
            {
                idx
                for idx in (self.field_list_widget.row(item) for item in selected_items)
                if idx >= 0
            },
            reverse=True,
        )
        if not indices:
            return

        self._send_worker_task(remove_selected_fields(list(reversed(indices))), emit_image=False)

        for idx in indices:
            _ = self.field_list_widget.takeItem(idx)

        controller = getattr(self, "field_metadata_controller", None)
        renumber = getattr(controller, "renumber_field_list", None)
        if callable(renumber):
            renumber()

        self._selected_field_indices = []
        remaining = self.field_list_widget.count()
        if remaining <= 0:
            self._set_field_list_hint("Open a file to see fields")
            self.build_dynamic_sliders({})
            self._show_status_message("Removed all fields.")
            return

        next_index = min(indices[-1], remaining - 1)
        next_item = self.field_list_widget.item(next_index)
        if next_item is not None:
            self.field_list_widget.setCurrentItem(next_item)
            self.on_field_clicked(next_item)

        self._show_status_message(f"Removed {len(indices)} field(s).")

    def _file_ops_save_selected(self) -> None:
        """Show save-selected dialog and dispatch worker save task."""
        selected_items = list(self.field_list_widget.selectedItems())
        if not selected_items:
            self._show_status_message("Select one or more fields to save.", is_error=True)
            return

        selected_indices = sorted(
            {
                idx
                for idx in (self.field_list_widget.row(item) for item in selected_items)
                if idx >= 0
            }
        )
        if not selected_indices:
            self._show_status_message("No valid selected fields to save.", is_error=True)
            return

        item_by_index = {
            self.field_list_widget.row(item): item
            for item in selected_items
            if self.field_list_widget.row(item) >= 0
        }
        selected_rows: list[dict[str, object]] = []
        for idx in selected_indices:
            item = item_by_index.get(idx)
            if item is None:
                continue
            selected_rows.append(
                {
                    "index": idx,
                    "identity": CFVMain._field_identity_from_item(self, item),
                    "chunk_shape": str(item.data(Qt.UserRole + 3) or ""),
                }
            )

        if not selected_rows:
            self._show_status_message("No valid selected fields to save.", is_error=True)
            return

        default_destination = str(self._settings.get("last_save_data_dir", str(Path.home())))
        default_output_filename = f"{self._default_plot_filename()}_selected.nc"
        dialog = SaveSelectedFieldsDialog(
            self,
            selected_rows=selected_rows,
            default_destination=default_destination,
            default_output_filename=default_output_filename,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        output_format = dialog.output_format
        destination_folder = Path(dialog.destination_folder).expanduser()
        requested_filename = dialog.output_filename.strip()
        destination_name = Path(requested_filename).name
        expected_suffix = ".zarr" if output_format == "zarr" else ".nc"
        if not destination_name.endswith(expected_suffix):
            destination_name = f"{Path(destination_name).stem}{expected_suffix}"
        destination = destination_folder / destination_name

        output_chunk_by_index: dict[int, str] = {}
        for row_meta, chunk_text in zip(selected_rows, dialog.output_chunk_shapes):
            idx = int(row_meta["index"])
            output_chunk_by_index[idx] = str(chunk_text).strip()

        self._remember_last_save_dir("last_save_data_dir", str(destination))
        code = save_selected_fields_task(
            selected_indices,
            str(destination),
            output_format,
            output_chunk_by_index,
        )
        self._send_worker_task(code, emit_image=False)

    def _apply_saved_selected_status(self, status_text: str) -> None:
        """Adopt selected generated fields into the destination source after save-selected."""
        match = re.match(
            r"^Saved\s+\d+\s+selected field\(s\)\s+to\s+(.+?)\s+\([^)]+\)$",
            status_text,
        )
        if not match:
            return

        destination = str(Path(match.group(1).strip()).expanduser())
        if not destination:
            return

        controller = getattr(self, "field_metadata_controller", None)
        mark_saved = getattr(controller, "mark_selected_items_saved", None)
        if not callable(mark_saved):
            return

        updated = int(mark_saved(destination))
        if updated <= 0:
            return

        if destination not in self._loaded_file_paths:
            self._loaded_file_paths.append(destination)

        refresh_menu = getattr(self, "_refresh_open_files_menu", None)
        if callable(refresh_menu):
            refresh_menu()

    def _normalize_coordinate_metadata(self, payload: object) -> dict[str, dict[str, object]]:
        """Normalize worker coordinate payload into slider metadata mapping."""
        metadata: dict[str, dict[str, object]] = {}
        name_counts: dict[str, int] = {}
        if not isinstance(payload, list):
            return metadata

        for entry in payload:
            if not (isinstance(entry, (tuple, list)) and len(entry) >= 2):
                continue

            name = str(entry[0])
            values = entry[1]
            if values is None:
                continue

            if isinstance(values, list):
                normalized_values = values
            else:
                normalized_values = list(values)

            if len(normalized_values) <= 1:
                continue

            if name in metadata:
                name_counts[name] = name_counts.get(name, 1) + 1
                unique_name = f"{name}_{name_counts[name]}"
            else:
                name_counts[name] = 1
                unique_name = name

            units = ""
            if len(entry) >= 3 and entry[2] is not None:
                units = str(entry[2])

            metadata[unique_name] = {
                "values": normalized_values,
                "units": units,
            }

        return metadata

    def _request_plot_update(self) -> None:
        """Request a new plot using current slider and collapse selections."""
        self._request_plot_task(
            save_code_path=None,
            save_plot_path=None,
            save_data_path=None,
        )

    def _request_plot_code_save(self, file_path: str) -> None:
        """Request plotting and ask the worker to save the generated code to a file."""
        self._request_plot_task(
            save_code_path=file_path,
            save_plot_path=None,
            save_data_path=None,
        )

    def _request_plot_save(self, file_path: str) -> None:
        """Request plotting directly to a file output path."""
        self._request_plot_task(
            save_code_path=None,
            save_plot_path=file_path,
            save_data_path=None,
            emit_image_override=False,
        )

    def _request_plot_data_save(self, file_path: str) -> None:
        """Request saving the currently selected/collapsed field data."""
        self._request_plot_task(
            save_code_path=None,
            save_plot_path=None,
            save_data_path=file_path,
            emit_image_override=False,
        )

    def _request_plot_save_all(
        self,
        save_code_path: str,
        save_plot_path: str,
        save_data_path: str,
    ) -> None:
        """Request saving plot image, data, and generated script in one action."""
        self._request_plot_task(
            save_code_path=save_code_path,
            save_plot_path=save_plot_path,
            save_data_path=save_data_path,
            emit_image_override=False,
        )

    def _request_plot_options(self) -> None:
        """Fetch plot-type specific option context from worker."""
        context = self._build_plot_context()
        if context is None:
            logger.debug("Skipped options request because no controls are available")
            return

        selections, collapse_by_coord, plot_kind = context
        if plot_kind == "lineplot":
            self._show_lineplot_options_dialog()
            return

        if plot_kind == "vector":
            field_index = self._selected_field_index_for_operation("Vector Plot Options")
            if field_index is not None:
                self._show_vector_options_dialog(field_index)
            return

        if plot_kind != "contour":
            self._show_status_message(f"No options dialog available for plot type: {plot_kind}")
            return

        code = contour_range_from_selection(selections, collapse_by_coord)
        self._send_worker_task(code, emit_image=False)

    def _build_plot_context(self) -> tuple[dict[str, tuple[object, object]], dict[str, str], str] | None:
        """Collect current selections/collapse state and infer plot type."""
        return _plot_ops.build_plot_context(
            self,
            parse_coordinate_subspace_commands_fn=parse_coordinate_subspace_commands,
        )

    def _request_plot_task(
        self,
        save_code_path: str | None,
        save_plot_path: str | None,
        save_data_path: str | None,
        emit_image_override: bool | None = None,
    ) -> None:
        """Build and send a plot/data task with optional save targets."""
        _plot_ops.request_plot_task(
            self,
            save_code_path=save_code_path,
            save_plot_path=save_plot_path,
            save_data_path=save_data_path,
            emit_image_override=emit_image_override,
            save_data_from_selection_fn=save_data_from_selection,
            plot_from_selection_fn=plot_from_selection,
            build_vector_overplot_command_fn=build_vector_overplot_command,
        )

    def _send_worker_task(
        self,
        code: str,
        save_code_path: str | None = None,
        emit_image: bool = True,
    ) -> None:
        """Send a code block to the worker process with task terminator."""
        if not code.endswith("\n"):
            code += "\n"

        headers: list[str] = []
        if save_code_path:
            encoded_path = base64.b64encode(save_code_path.encode("utf-8")).decode("ascii")
            headers.append(f"#SAVE_TASK_CODE_PATH_B64:{encoded_path}")
        if not emit_image:
            headers.append("#EMIT_IMAGE:0")

        header_block = ""
        if headers:
            header_block = "\n".join(headers) + "\n"

        payload = header_block + code + "#END_TASK\n"
        CFVMain._record_pending_worker_task(self)
        logger.debug("Sending worker task (%d chars)", len(code))
        self.worker.write(payload.encode())

    def _send_worker_control_task(self, kind: str, payload: dict[str, object]) -> None:
        """Send a non-code control task to the worker using typed task headers."""
        payload_text = json.dumps(payload, sort_keys=True)
        encoded_payload = base64.b64encode(payload_text.encode("utf-8")).decode("ascii")
        task = (
            f"#TASK_KIND:{kind}\n"
            f"#TASK_PAYLOAD_B64:{encoded_payload}\n"
            "#END_TASK\n"
        )
        CFVMain._record_pending_worker_task(self)
        logger.debug("Sending worker control task %s", kind)
        self.worker.write(task.encode())

    def _apply_logging_configuration_from_ui(
        self,
        *,
        scope_levels: dict[str, int | str],
    ) -> None:
        """Apply GUI logging config locally and forward it to the worker."""
        super()._apply_logging_configuration_from_ui(
            scope_levels=scope_levels,
        )
        self._send_worker_control_task(
            "LOGGING_CONFIGURE",
            {
                "scope_levels": scope_levels,
            },
        )

    def _record_pending_worker_task(self) -> None:
        """Store worker task start times so completion statuses can show elapsed time."""
        starts = getattr(self, "_pending_worker_task_starts", None)
        if starts is None:
            starts = deque()
            setattr(self, "_pending_worker_task_starts", starts)
        starts.append(time.monotonic())

    def _complete_pending_worker_task(self, consume: bool = True) -> float | None:
        """Return elapsed seconds for the oldest pending worker task, if any."""
        starts = getattr(self, "_pending_worker_task_starts", None)
        if not starts:
            return None

        start = starts.popleft() if consume else starts[0]
        return max(0.0, time.monotonic() - start)

    def _set_window_title_for_file(self, file_path: str) -> None:
        """Update window title, appending remote host label when a remote session is active."""
        descriptor = getattr(self, "_remote_descriptor", None)
        if not isinstance(descriptor, dict):
            super()._set_window_title_for_file(file_path)
            return

        scheme = str(descriptor.get("uri_scheme", "") or descriptor.get("protocol", ""))
        display = str(descriptor.get("display_name", ""))
        if scheme and display:
            remote_tag = f" ({scheme}:{display})"
        elif display:
            remote_tag = f" ({display})"
        else:
            remote_tag = ""

        self.current_file_path = file_path
        filename = Path(file_path).name
        self.setWindowTitle(f"{self.base_window_title}: {filename}{remote_tag}")

    def _shutdown_worker(self) -> None:
        """Shut down the worker process cleanly, suppressing the crash error signal."""
        if self.worker.state() == QProcess.NotRunning:
            return
        self._shutting_down = True
        logger.info("Shutting down worker process")
        self.worker.terminate()
        if not self.worker.waitForFinished(2000):
            logger.warning("Worker did not terminate in time; killing process")
            self.worker.kill()
            self.worker.waitForFinished(1000)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Ensure worker process is shut down cleanly when GUI exits."""
        self._release_remote_session_if_active()
        self._shutdown_worker()
        super().closeEvent(event)

