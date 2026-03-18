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
import pickle
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QEventLoop, QProcess
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QListWidgetItem, QMessageBox

from .cf_templates import (
    contour_range_from_selection,
    coordinate_list,
    field_list,
    plot_from_selection,
)
from .core_window import CFVCore
from .ui.dialogs import OpenURIDialog, RemoteConfigurationDialog, RemoteOpenDialog
from .ui.remote_file_navigator import (
    RemoteEntry,
    RemoteFileNavigatorDialog,
    RemoteLoginLogDialog,
    build_remote_filesystem_spec,
    remote_descriptor_hash,
    spec_to_descriptor,
)

logger = logging.getLogger(__name__)


class CFVMain(CFVCore):
    """Worker-backed application behavior layered on top of the core GUI."""

    def __init__(self) -> None:
        super().__init__()
        self._plot_request_in_flight = False
        self._plot_request_expects_image = False
        self._suppress_stale_error_status = False
        self._remote_session_id: str | None = None
        self._remote_descriptor_hash: str | None = None
        self._remote_descriptor: dict[str, object] | None = None
        self._pending_worker_task_starts: deque[float] = deque()
        self._pending_prepare_loop: QEventLoop | None = None
        self._pending_prepare_loop_ok: bool = False
        self._pending_prepare_log_dialog: RemoteLoginLogDialog | None = None
        self._pending_list_loop: QEventLoop | None = None
        self._pending_list_result: dict | None = None

        self.worker = QProcess()
        self.worker.readyReadStandardOutput.connect(self.handle_worker_output)
        self.worker.readyReadStandardError.connect(self.handle_worker_error)
        self.worker.errorOccurred.connect(self.handle_worker_process_error)
        self.worker.finished.connect(self.handle_worker_finished)

        self.worker.start("cf-worker")
        logger.info("Started worker process: %s", self.worker.program())

    def on_file_selected(self, file_path: str) -> None:
        """Handle file selection by requesting worker metadata."""
        self._load_selected_file(file_path)

    def on_field_clicked(self, item: QListWidgetItem) -> None:
        """Show selection details and request slider coordinates for the field."""
        super().on_field_clicked(item)
        self._reset_ui_for_new_field_selection()

        field_index = self.field_list_widget.row(item)
        if field_index < 0:
            return

        self._request_coordinates_for_field(field_index, show_status=False)

    def _reset_ui_for_new_field_selection(self) -> None:
        """Clear stale error/loading UI state before handling a fresh field selection."""
        self._plot_request_in_flight = False
        self._plot_request_expects_image = False
        self._suppress_stale_error_status = True
        self._set_plot_loading(False)
        self._clear_plot_canvas("Waiting for data...")
        self._show_status_message("Task Complete")

    def handle_worker_output(self) -> None:
        """Process worker stdout messages and route updates to UI."""
        while self.worker.canReadLine():
            line = self.worker.readLine().data().decode().strip()
            if not line:
                continue

            logger.debug("Worker stdout line: %s", line)

            if line.startswith("REMOTE_STATUS:"):
                raw_payload = line.split(":", 1)[1]
                payload = pickle.loads(base64.b64decode(raw_payload))
                if not isinstance(payload, dict):
                    logger.warning("Unexpected REMOTE_STATUS payload type: %s", type(payload).__name__)
                    continue

                phase = str(payload.get("phase", ""))
                message = str(payload.get("message") or f"Remote worker phase: {phase}")
                is_error = phase == "failed"
                self._show_status_message(message, is_error=is_error)

                log_dialog = getattr(self, "_pending_prepare_log_dialog", None)
                if log_dialog is not None:
                    log_dialog.append_line(message)
                    if phase == "failed":
                        log_dialog.mark_failed("")

                if phase in ("ready", "failed"):
                    loop = getattr(self, "_pending_prepare_loop", None)
                    if loop is not None:
                        self._pending_prepare_loop_ok = phase == "ready"
                        self._pending_prepare_loop = None
                        loop.quit()

            elif line.startswith("REMOTE_LIST_RESULT:"):
                raw_payload = line.split(":", 1)[1]
                result = pickle.loads(base64.b64decode(raw_payload))
                if isinstance(result, dict):
                    self._pending_list_result = result
                    loop = getattr(self, "_pending_list_loop", None)
                    if loop is not None:
                        self._pending_list_loop = None
                        loop.quit()
                else:
                    logger.warning("Unexpected REMOTE_LIST_RESULT payload type: %s", type(result).__name__)

            elif line.startswith("REMOTE_OPEN_RESULT:"):
                raw_payload = line.split(":", 1)[1]
                payload = pickle.loads(base64.b64decode(raw_payload))
                if not isinstance(payload, dict):
                    logger.warning("Unexpected REMOTE_OPEN_RESULT payload type: %s", type(payload).__name__)
                    continue

                if payload.get("ok"):
                    uri = str(payload.get("uri", ""))
                    if uri:
                        self._set_window_title_for_file(uri)
                        self._show_status_message(f"Loaded remote file: {uri}")
                else:
                    error = str(payload.get("error") or "Remote open failed")
                    self._show_status_message(error, is_error=True)

            elif line.startswith("STATUS:"):
                status_text = line.split(":", 1)[1]
                display_status_text = status_text
                is_error_status = status_text.startswith("Error -")

                if status_text == "Task Complete":
                    elapsed = CFVMain._complete_pending_worker_task(self, consume=True)
                    if elapsed is not None:
                        display_status_text = f"Task Complete ({elapsed:.2f}s)"
                elif is_error_status:
                    CFVMain._complete_pending_worker_task(self, consume=True)

                # Ignore delayed worker errors from a previous task right after field reselection.
                if self._suppress_stale_error_status and is_error_status and not self._plot_request_in_flight:
                    logger.debug("Ignoring stale worker error status after field reset: %s", status_text)
                    continue

                self._show_status_message(
                    display_status_text,
                    is_error=is_error_status,
                )

                is_plot_error = self._plot_request_in_flight and is_error_status
                should_finish = False
                if is_plot_error:
                    should_finish = True
                elif (
                    self._plot_request_in_flight
                    and status_text == "Task Complete"
                    and not self._plot_request_expects_image
                ):
                    should_finish = True

                if is_plot_error:
                    self._clear_plot_canvas("Plot failed.")

                if should_finish:
                    self._plot_request_in_flight = False
                    self._plot_request_expects_image = False
                    self._set_plot_loading(False)

            elif line.startswith("METADATA:"):
                raw_payload = line.split(":", 1)[1]
                metadata = pickle.loads(base64.b64decode(raw_payload))
                if isinstance(metadata, list):
                    if not all(isinstance(row, dict) for row in metadata):
                        raise TypeError(
                            "Field metadata payload must be a list of dict rows "
                            "with identity/detail/properties"
                        )
                    logger.info("Received metadata for %d fields", len(metadata))
                    self.populate_field_list(metadata)
                elif isinstance(metadata, dict):
                    logger.info("Received metadata for %d coordinates", len(metadata))
                    self.build_dynamic_sliders(metadata)
                else:
                    logger.warning("Unexpected metadata payload type: %s", type(metadata).__name__)

            elif line.startswith("IMG_READY:"):
                logger.info(
                    "PLOT_DIAG gui_img_ready pid=%s worker_pid=%s payload_kind=bytes",
                    os.getpid(),
                    self.worker.processId(),
                )
                raw_payload = line.split(":", 1)[1]
                payload = pickle.loads(base64.b64decode(raw_payload))
                if isinstance(payload, bytes):
                    self.set_plot_image(payload)
                    self._show_status_message("Plot Updated.")
                    if self._plot_request_in_flight:
                        self._plot_request_in_flight = False
                        self._plot_request_expects_image = False
                        self._set_plot_loading(False)
                else:
                    logger.warning("Unexpected IMG_READY payload type: %s", type(payload).__name__)

            elif line == "IMG_READY":
                self._show_status_message("Plot Updated.")
                if self._plot_request_in_flight:
                    self._plot_request_in_flight = False
                    self._plot_request_expects_image = False
                    self._set_plot_loading(False)

            elif line.startswith("COORD:"):
                raw_payload = line.split(":", 1)[1]
                coords = pickle.loads(base64.b64decode(raw_payload))
                metadata = self._normalize_coordinate_metadata(coords)
                if metadata:
                    logger.info("Received coordinate metadata for %d sliders", len(metadata))
                    self.build_dynamic_sliders(metadata)
                else:
                    logger.warning("Received empty coordinate metadata payload")

            elif line.startswith("CONTOUR_RANGE:"):
                raw_payload = line.split(":", 1)[1]
                payload = pickle.loads(base64.b64decode(raw_payload))
                if isinstance(payload, dict):
                    try:
                        range_min = float(payload["min"])
                        range_max = float(payload["max"])
                    except (KeyError, TypeError, ValueError):
                        logger.warning("Malformed CONTOUR_RANGE payload: %r", payload)
                        continue

                    suggested_title = payload.get("suggested_title")
                    if suggested_title is not None:
                        suggested_title = str(suggested_title).strip() or None

                    self._show_contour_options_dialog(
                        range_min,
                        range_max,
                        suggested_title=suggested_title,
                    )
                else:
                    logger.warning("Unexpected CONTOUR_RANGE payload type: %s", type(payload).__name__)

    def handle_worker_error(self) -> None:
        """Log worker stderr output with best-effort severity mapping."""
        stderr_output = self.worker.readAllStandardError().data().decode(errors="replace").strip()
        if not stderr_output:
            return

        for raw_line in stderr_output.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if " ERROR " in line or line.startswith("ERROR") or line.startswith("Traceback"):
                logger.error("Worker stderr: %s", line)
            elif " WARNING " in line or line.startswith("WARNING"):
                logger.warning("Worker stderr: %s", line)
            elif " INFO " in line or line.startswith("INFO"):
                logger.info("Worker: %s", line)
            else:
                logger.info("Worker stderr: %s", line)

    def handle_worker_process_error(self, process_error: QProcess.ProcessError) -> None:
        """Capture QProcess-level failures, such as start or crash issues."""
        logger.error("Worker process error: %s", process_error)
        self._show_status_message(f"Worker process error: {process_error}", is_error=True)
        if self._plot_request_in_flight:
            self._plot_request_in_flight = False
            self._plot_request_expects_image = False
            self._set_plot_loading(False)
            self._clear_plot_canvas("Plot failed because the worker encountered an error.")

    def handle_worker_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """Capture worker shutdown information."""
        logger.warning("Worker finished with exit_code=%s exit_status=%s", exit_code, exit_status)
        if exit_code != 0:
            self._show_status_message(
                f"Worker stopped unexpectedly (exit_code={exit_code}).",
                is_error=True,
            )
        if self._plot_request_in_flight:
            self._plot_request_in_flight = False
            self._plot_request_expects_image = False
            self._set_plot_loading(False)
            self._clear_plot_canvas("Plot failed because the worker stopped.")

    def _load_selected_file(self, file_path: str) -> None:
        """Load selected file in worker and publish field metadata."""
        release_remote = getattr(self, "_release_remote_session_if_active", None)
        if callable(release_remote):
            release_remote()
        self._show_status_message(f"Loading file: {file_path}")
        logger.info("Loading file in worker: %s", file_path)

        code = (
            f"_cfview_file_path = {file_path!r}\n"
            "_cfview_field_index = None\n"
            f"f = cf.read({file_path!r})\n"
            + field_list
            + "send_to_gui('METADATA', fields)"
        )
        self._send_worker_task(code)

    def _load_remote_selected_file(self, uri: str, remote_path: str) -> None:
        """Load a selected remote file through the worker remote session pool."""
        if not self._remote_session_id or not self._remote_descriptor_hash or not self._remote_descriptor:
            self._show_status_message("Remote worker session is not initialized.", is_error=True)
            return

        self._show_status_message(f"Loading remote file: {uri}")
        self._send_worker_control_task(
            "REMOTE_OPEN",
            {
                "session_id": self._remote_session_id,
                "descriptor_hash": self._remote_descriptor_hash,
                "descriptor": self._remote_descriptor,
                "uri": uri,
                "path": remote_path,
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
        if not isinstance(config, dict):
            return

        if str(config.get("protocol", "")).upper() in {"HTTP", "HTTPS"}:
            http_locations = self._settings.get("remote_https_locations")
            if not isinstance(http_locations, dict):
                http_locations = self._settings.get("remote_http_locations")
            if isinstance(http_locations, dict):
                updated = dict(http_locations)
            else:
                updated = {}

            remote = config.get("remote")
            if isinstance(remote, dict):
                alias = str(remote.get("alias", "")).strip()
                details = remote.get("details")
                if alias and isinstance(details, dict):
                    url = details.get("url") or details.get("base_url")
                    if isinstance(url, str) and url.strip():
                        updated[alias] = {"url": url.strip()}

            self._settings["remote_https_locations"] = updated

        try:
            spec = build_remote_filesystem_spec(config)
        except Exception as exc:
            QMessageBox.critical(self, "Remote configuration invalid", str(exc))
            return

        self._release_remote_session_if_active()

        descriptor = spec_to_descriptor(spec, cache=config.get("cache") if isinstance(config, dict) else None)
        session_id = uuid.uuid4().hex
        descriptor_hash = remote_descriptor_hash(descriptor)
        self._remote_session_id = session_id
        self._remote_descriptor_hash = descriptor_hash
        self._remote_descriptor = descriptor

        # Show login progress dialog and spin a QEventLoop until the worker signals ready/failed.
        log_dialog = RemoteLoginLogDialog(self, spec.display_name)
        self._pending_prepare_log_dialog = log_dialog
        self._pending_prepare_loop = QEventLoop()
        self._pending_prepare_loop_ok = False
        log_dialog.show()
        QApplication.processEvents()

        self._send_worker_control_task(
            "REMOTE_PREPARE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
            },
        )
        self._pending_prepare_loop.exec()
        self._pending_prepare_log_dialog = None

        if not self._pending_prepare_loop_ok:
            # mark_failed was already called in handle_worker_output; show the dialog modally.
            log_dialog.exec()
            self._release_remote_session_if_active()
            return

        log_dialog.close()

        # Open the navigator backed entirely by worker-side directory listing via IPC.
        list_callback = self._make_worker_list_callback()
        dialog = RemoteFileNavigatorDialog(self, config, spec=spec, list_callback=list_callback)
        if dialog.exec() != QDialog.Accepted:
            self._release_remote_session_if_active()
            return

        selected_uri = dialog.selected_uri()
        selected_path = dialog.selected_path()
        if not selected_uri or not selected_path:
            self._show_status_message("Remote file selection was incomplete.", is_error=True)
            self._release_remote_session_if_active()
            return

        remote = config.get("remote") if isinstance(config, dict) else None
        host_alias = str(remote.get("alias", "")).strip() if isinstance(remote, dict) else ""
        self._set_window_title_for_file(selected_uri)
        self._show_status_message(f"Selected remote file: {selected_uri}")
        if host_alias:
            self._record_recent_uri(selected_uri, host_alias)
        else:
            self._record_recent_file(selected_uri)
        self._load_remote_selected_file(selected_uri, selected_path)

    def _open_remote_uri_direct(
        self,
        *,
        uri: str,
        remote_path: str,
        config: dict[str, object],
        host_alias: str,
    ) -> None:
        """Open a specific remote URI directly without launching the navigator dialog."""
        if not isinstance(config, dict):
            return

        if str(config.get("protocol", "")).upper() in {"HTTP", "HTTPS"} and host_alias:
            details = {}
            remote = config.get("remote")
            if isinstance(remote, dict):
                raw_details = remote.get("details")
                if isinstance(raw_details, dict):
                    details = dict(raw_details)
                if not details and isinstance(remote.get("url"), str):
                    details = {"url": str(remote.get("url"))}

            https_locations = self._settings.get("remote_https_locations")
            merged = dict(https_locations) if isinstance(https_locations, dict) else {}
            if details:
                merged[host_alias] = details
            self._settings["remote_https_locations"] = merged

        try:
            spec = build_remote_filesystem_spec(config)
        except Exception as exc:
            QMessageBox.critical(self, "Remote configuration invalid", str(exc))
            return

        self._release_remote_session_if_active()
        descriptor = spec_to_descriptor(spec, cache=config.get("cache") if isinstance(config, dict) else None)
        session_id = uuid.uuid4().hex
        descriptor_hash = remote_descriptor_hash(descriptor)
        self._remote_session_id = session_id
        self._remote_descriptor_hash = descriptor_hash
        self._remote_descriptor = descriptor

        log_dialog = RemoteLoginLogDialog(self, spec.display_name)
        self._pending_prepare_log_dialog = log_dialog
        self._pending_prepare_loop = QEventLoop()
        self._pending_prepare_loop_ok = False
        log_dialog.show()
        QApplication.processEvents()

        self._send_worker_control_task(
            "REMOTE_PREPARE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
            },
        )
        self._pending_prepare_loop.exec()
        self._pending_prepare_log_dialog = None

        if not self._pending_prepare_loop_ok:
            log_dialog.exec()
            self._release_remote_session_if_active()
            return

        log_dialog.close()
        self._set_window_title_for_file(uri)
        self._show_status_message(f"Selected remote file: {uri}")
        self._record_recent_uri(uri, host_alias or spec.display_name)
        self._load_remote_selected_file(uri, remote_path)

    def _resolve_remote_uri(self, uri: str) -> tuple[dict[str, object] | None, str, str, bool]:
        """Resolve URI into (config, remote_path, host_alias, unknown_host)."""
        canonical_uri = CFVCore._canonical_remote_uri(uri)
        parsed = urlparse(canonical_uri)
        scheme = parsed.scheme.lower()

        if scheme == "s3":
            locations = RemoteConfigurationDialog._load_s3_locations()

            endpoint_to_alias: dict[str, str] = {}
            for alias_name, details in locations.items():
                if not isinstance(alias_name, str) or not isinstance(details, dict):
                    continue
                endpoint_url = str(details.get("url", "")).strip()
                endpoint_host = urlparse(endpoint_url).netloc.strip()
                if endpoint_host:
                    endpoint_to_alias[endpoint_host] = alias_name

            netloc = parsed.netloc.strip()
            endpoint_alias = endpoint_to_alias.get(netloc, "")
            if endpoint_alias:
                path = parsed.path.lstrip("/")
            else:
                path = f"{parsed.netloc}{parsed.path}".lstrip("/")

            aliases = self._settings.get("recent_uri_aliases")
            alias_map = aliases if isinstance(aliases, dict) else {}
            preferred_alias = alias_map.get(canonical_uri) or alias_map.get(uri)
            if not isinstance(preferred_alias, str):
                preferred_alias = ""
            preferred_alias = preferred_alias.strip()

            if endpoint_alias:
                preferred_alias = endpoint_alias

            if not preferred_alias:
                raw_state = self._settings.get("last_remote_configuration")
                state = raw_state if isinstance(raw_state, dict) else {}
                candidate = state.get("s3_existing_alias")
                if isinstance(candidate, str) and candidate.strip():
                    preferred_alias = candidate.strip()

            chosen_alias = preferred_alias if preferred_alias in locations else ""
            if not chosen_alias and len(locations) == 1:
                chosen_alias = next(iter(locations.keys()))

            details = dict(locations.get(chosen_alias, {})) if chosen_alias else {}
            config: dict[str, object] = {
                "protocol": "S3",
                "remote": {
                    "mode": "Select from existing",
                    "alias": chosen_alias or "S3",
                    "details": details,
                },
            }
            return config, path, chosen_alias or "S3", False

        if scheme == "ssh":
            host = (parsed.hostname or parsed.netloc or "").strip()
            user = (parsed.username or "").strip()
            remote_path = unquote(parsed.path or "/")
            hosts = RemoteConfigurationDialog._load_ssh_hosts()

            matched_alias = ""
            matched_details: dict[str, object] | None = None
            for alias, details in hosts.items():
                if alias == host or str(details.get("hostname", "")) == host:
                    matched_alias = alias
                    matched_details = dict(details)
                    break

            if matched_details is None:
                return None, remote_path, host or "SSH", True

            if user and not matched_details.get("user"):
                matched_details["user"] = user

            config = {
                "protocol": "SSH",
                "remote": {
                    "mode": "Select from existing",
                    "alias": matched_alias,
                    "details": matched_details,
                },
            }
            return config, remote_path, matched_alias, False

        if scheme in {"http", "https"}:
            https_locations = self._settings.get("remote_https_locations")
            locations = dict(https_locations) if isinstance(https_locations, dict) else {}
            if not locations:
                cfg_state = self._settings.get("last_remote_configuration")
                if isinstance(cfg_state, dict):
                    raw = cfg_state.get("https_locations")
                    if isinstance(raw, dict):
                        locations = dict(raw)

            matched_alias = ""
            matched_url = ""
            for alias, details in locations.items():
                if not isinstance(details, dict):
                    continue
                base_url = str(details.get("url") or details.get("base_url") or "").strip()
                if base_url and uri.startswith(base_url):
                    if len(base_url) > len(matched_url):
                        matched_alias = str(alias)
                        matched_url = base_url

            remote_path = unquote(parsed.path or "/")
            if not matched_alias:
                return None, remote_path, (parsed.hostname or "HTTPS"), True

            config = {
                "protocol": "HTTPS",
                "remote": {
                    "mode": "Select from existing",
                    "alias": matched_alias,
                    "details": locations.get(matched_alias, {}),
                },
            }
            return config, remote_path, matched_alias, False

        return None, "", "", False

    def _configure_remote_for_uri(self, uri: str) -> None:
        """Open Configure Remote pre-populated for URI-driven add-new workflows."""
        parsed = urlparse(uri)
        scheme = parsed.scheme.lower()
        raw_state = self._settings.get("last_remote_configuration", {})
        state = dict(raw_state) if isinstance(raw_state, dict) else {}
        https_locations = self._settings.get("remote_https_locations")
        if isinstance(https_locations, dict):
            state["https_locations"] = dict(https_locations)

        if scheme in {"http", "https"}:
            state.update(
                {
                    "protocol_index": 1,
                    "https_mode": "Add new",
                    "https_alias": (parsed.hostname or "https").strip(),
                    "https_url": f"{scheme}://{parsed.netloc}",
                }
            )
        elif scheme == "ssh":
            state.update(
                {
                    "protocol_index": 2,
                    "ssh_mode": "Add new",
                    "ssh_alias": (parsed.hostname or parsed.netloc or "ssh").strip(),
                    "ssh_hostname": (parsed.hostname or parsed.netloc or "").strip(),
                    "ssh_user": (parsed.username or "").strip(),
                }
            )

        config, _ok, next_state = RemoteConfigurationDialog.get_configuration(self, state=state)
        self._settings["last_remote_configuration"] = next_state
        if isinstance(next_state, dict):
            persisted_https = next_state.get("https_locations")
            if isinstance(persisted_https, dict):
                self._settings["remote_https_locations"] = dict(persisted_https)
        self._save_settings()

    def _open_uri_entry(self, uri: str, *, from_uri_dialog: bool) -> None:
        """Open a URI from user input or recent list."""
        canonical_uri = CFVCore._canonical_remote_uri(uri)
        parsed = urlparse(canonical_uri)
        scheme = parsed.scheme.lower()

        if not scheme:
            self._open_recent_file(canonical_uri)
            return

        if scheme not in {"s3", "ssh", "http", "https"}:
            QMessageBox.critical(self, "Unsupported URI", f"Unsupported URI protocol: {scheme}")
            return

        config, remote_path, host_alias, unknown_host = self._resolve_remote_uri(canonical_uri)
        if unknown_host and from_uri_dialog:
            self._configure_remote_for_uri(canonical_uri)
            config, remote_path, host_alias, _unknown_host_after = self._resolve_remote_uri(canonical_uri)

        if config is None:
            QMessageBox.critical(self, "Unknown host", "Host route is not known. Configure a remote first.")
            return

        self._open_remote_uri_direct(
            uri=canonical_uri,
            remote_path=remote_path,
            config=config,
            host_alias=host_alias,
        )

    def _configure_remote(self) -> None:
        """Open the full remote configuration dialog; Open proceeds to worker-backed navigation."""
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
        self._open_remote_from_config(config)

    def _choose_remote(self) -> None:
        """Open using existing short names via a streamlined protocol picker dialog."""
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
        self._open_remote_from_config(config)

    def _choose_uris(self) -> None:
        """Show URI dialog and open supported URIs directly through the worker."""
        default_uri = self._default_open_uri_value()
        uri, ok, quit_requested = OpenURIDialog.get_uri(self, default_uri=default_uri)
        if quit_requested:
            return
        if not ok:
            return
        self._open_uri_entry(uri, from_uri_dialog=True)

    def _open_recent_file(self, file_path: str) -> None:
        """Open a recent entry, routing remote URIs through URI resolution flow."""
        if urlparse(file_path).scheme:
            self._open_uri_entry(file_path, from_uri_dialog=False)
            return
        super()._open_recent_file(file_path)

    def _make_worker_list_callback(self):
        """Return a callable that lists a remote directory via worker IPC using a nested QEventLoop."""
        def list_dir(path: str) -> list[RemoteEntry]:
            loop = QEventLoop()
            self._pending_list_loop = loop
            self._pending_list_result = None
            self._send_worker_control_task(
                "REMOTE_LIST",
                {
                    "session_id": self._remote_session_id,
                    "descriptor_hash": self._remote_descriptor_hash,
                    "descriptor": self._remote_descriptor,
                    "path": path,
                },
            )
            loop.exec()
            result = self._pending_list_result
            self._pending_list_loop = None
            self._pending_list_result = None
            if result is None:
                raise RuntimeError(f"No response from worker for directory listing of {path!r}")
            error = result.get("error")
            if error:
                raise RuntimeError(str(error))
            return list(result.get("entries", []))

        return list_dir

    def _request_coordinates_for_field(self, index: int, show_status: bool = True) -> None:
        """Request coordinate arrays for a selected field index."""
        if show_status:
            self._show_status_message(f"Loading coordinates for field index {index}...")
        self._send_worker_task(coordinate_list(index))

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
        self._request_plot_task(save_code_path=None, save_plot_path=None)

    def _request_plot_code_save(self, file_path: str) -> None:
        """Request plotting and ask the worker to save the generated code to a file."""
        self._request_plot_task(save_code_path=file_path, save_plot_path=None)

    def _request_plot_save(self, file_path: str) -> None:
        """Request plotting directly to a file output path."""
        self._request_plot_task(save_code_path=None, save_plot_path=file_path)

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

        if plot_kind != "contour":
            self._show_status_message(f"No options dialog available for plot type: {plot_kind}")
            return

        code = contour_range_from_selection(selections, collapse_by_coord)
        self._send_worker_task(code, emit_image=False)

    def _build_plot_context(self) -> tuple[dict[str, tuple[object, object]], dict[str, str], str] | None:
        """Collect current selections/collapse state and infer plot type."""
        if not self.controls:
            return None

        selections: dict[str, tuple[object, object]] = {}
        collapse_by_coord: dict[str, str] = {}
        dims: list[int] = []

        for name, control in self.controls.items():
            values = control["values"]
            start_idx, end_idx = control["range_slider"].value()
            lo_idx = int(min(start_idx, end_idx))
            hi_idx = int(max(start_idx, end_idx))
            is_singleton = (hi_idx - lo_idx) <= 1

            if is_singleton:
                if lo_idx == 0:
                    singleton_idx = lo_idx
                elif hi_idx == (len(values) - 1):
                    singleton_idx = hi_idx
                else:
                    singleton_idx = lo_idx
                lo = values[singleton_idx]
                hi = values[singleton_idx]
            else:
                lo = values[lo_idx]
                hi = values[hi_idx]
            selections[name] = (lo, hi)

            collapse_method = self.selected_collapse_methods.get(name)
            if collapse_method:
                collapse_by_coord[name] = collapse_method
                dims.append(1)
            else:
                dims.append(1 if is_singleton else 2)

        varying_dims = sum(1 for dim in dims if dim != 1)
        available_kinds = getattr(self, "available_plot_kinds", [])
        selected_kind = getattr(self, "selected_plot_kind", None)

        if varying_dims == 0:
            plot_kind = "collapsed"
        elif varying_dims > 2:
            plot_kind = "unsupported"
        elif isinstance(selected_kind, str) and selected_kind in available_kinds:
            plot_kind = selected_kind
        elif varying_dims == 1:
            plot_kind = "lineplot"
        elif varying_dims == 2:
            # Keep contour as a sensible default in 2D when no explicit selection exists.
            plot_kind = "contour"
        else:
            plot_kind = "unsupported"

        return selections, collapse_by_coord, plot_kind

    def _request_plot_task(self, save_code_path: str | None, save_plot_path: str | None) -> None:
        """Build and send a plot task with optional code-save and plot-save paths."""
        context = self._build_plot_context()
        if context is None:
            logger.info("PLOT_DIAG gui_plot_skip reason=no_controls")
            return
        selections, collapse_by_coord, plot_kind = context

        if plot_kind in {"collapsed", "unsupported"}:
            logger.info(
                "PLOT_DIAG gui_plot_skip reason=dimensionality kind=%s coords=%d collapses=%d",
                plot_kind,
                len(selections),
                len(collapse_by_coord),
            )
            return

        plot_options = dict(self.plot_options_by_kind.get(plot_kind, {}))
        if plot_kind == "contour":
            plot_options.setdefault("contour_title_fontsize", self._contour_title_fontsize())
            plot_options.setdefault("page_title_fontsize", self._page_title_fontsize())
            plot_options.setdefault("annotation_fontsize", self._annotation_fontsize())

        if save_plot_path:
            plot_options["filename"] = str(Path(save_plot_path).expanduser())
        elif not plot_options:
            plot_options = None

        try:
            cmd = plot_from_selection(selections, collapse_by_coord, plot_kind, plot_options)
        except (ValueError, NotImplementedError) as exc:
            self._show_status_message(f"Plot request unavailable: {exc}", is_error=True)
            logger.warning("Plot template unavailable for kind=%s: %s", plot_kind, exc)
            return

        save_target = None
        if save_code_path:
            save_target = str(Path(save_code_path).expanduser())

        emit_image = save_plot_path is None

        logger.debug(
            "Requesting plot update kind=%s coords=%d collapses=%d save_code=%s save_plot=%s",
            plot_kind,
            len(selections),
            len(collapse_by_coord),
            bool(save_target),
            bool(save_plot_path),
        )
        logger.info(
            "PLOT_DIAG gui_plot_request pid=%s worker_pid=%s kind=%s emit_image=%s",
            os.getpid(),
            self.worker.processId(),
            plot_kind,
            save_plot_path is None,
        )

        if save_plot_path:
            loading_message = "Rendering and saving plot..."
        elif save_code_path:
            loading_message = "Rendering plot and saving code..."
        else:
            loading_message = "Rendering plot..."

        self._plot_request_in_flight = True
        self._plot_request_expects_image = emit_image
        self._suppress_stale_error_status = False
        self._set_plot_loading(True, loading_message)
        self._send_worker_task(cmd, save_code_path=save_target, emit_image=emit_image)

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

    def closeEvent(self, event: QCloseEvent) -> None:
        """Ensure worker process is shut down cleanly when GUI exits."""
        self._release_remote_session_if_active()
        if self.worker.state() != QProcess.NotRunning:
            logger.info("Terminating worker process")
            self.worker.terminate()
            if not self.worker.waitForFinished(2000):
                logger.warning("Worker did not terminate in time; killing process")
                self.worker.kill()
                self.worker.waitForFinished(1000)

        super().closeEvent(event)

