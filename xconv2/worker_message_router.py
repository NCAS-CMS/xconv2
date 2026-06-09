"""Worker stdout protocol routing for CFVMain.

This module extracts message parsing and dispatch from the main window class
without changing worker protocol behavior.
"""

from __future__ import annotations

import base64
import logging
import os
import pickle
from typing import Any

logger = logging.getLogger(__name__)


class WorkerStatusHandler:
    """Handle STATUS protocol lines and related task/plot state updates."""

    def __init__(self, host: Any, *, main_cls: type[Any] | None = None) -> None:
        self._host = host
        self._main_cls = main_cls

    def _complete_pending_worker_task(self, consume: bool = True) -> float | None:
        if self._main_cls is not None:
            return self._main_cls._complete_pending_worker_task(self._host, consume=consume)

        method = getattr(self._host, "_complete_pending_worker_task", None)
        if callable(method):
            return method(consume=consume)
        return None

    def _apply_saved_selected_status(self, status_text: str) -> None:
        if self._main_cls is not None:
            self._main_cls._apply_saved_selected_status(self._host, status_text)
            return

        method = getattr(self._host, "_apply_saved_selected_status", None)
        if callable(method):
            method(status_text)

    def handle_status_line(self, line: str) -> None:
        """Process one STATUS line and synchronize host state."""
        status_text = line.split(":", 1)[1]
        display_status_text = status_text
        is_error_status = status_text.startswith("Error -")

        if status_text == "Task Complete":
            elapsed = self._complete_pending_worker_task(consume=True)
            if elapsed is not None:
                display_status_text = f"Task Complete ({elapsed:.2f}s)"
        elif is_error_status:
            self._complete_pending_worker_task(consume=True)

        if self._host._suppress_stale_error_status and is_error_status and not self._host._plot_request_in_flight:
            logger.debug("Ignoring stale worker error status after field reset: %s", status_text)
            return

        self._apply_saved_selected_status(display_status_text)
        self._host._show_status_message(
            display_status_text,
            is_error=is_error_status,
        )
        if is_error_status:
            self._host._maybe_show_binary_validation_dialog(status_text)
            metadata_loop = getattr(self._host, "_pending_metadata_loop", None)
            if metadata_loop is not None:
                self._host._pending_metadata_error = status_text
                self._host._pending_metadata_loop = None
                metadata_loop.quit()

        is_plot_error = self._host._plot_request_in_flight and is_error_status
        should_finish = False
        if is_plot_error:
            should_finish = True
        elif (
            self._host._plot_request_in_flight
            and status_text == "Task Complete"
            and not self._host._plot_request_expects_image
        ):
            should_finish = True

        if is_plot_error:
            self._host._clear_plot_canvas("Plot failed.")

        if should_finish:
            self._host._plot_request_in_flight = False
            self._host._plot_request_expects_image = False
            self._host._set_plot_loading(False)


class WorkerMessageRouter:
    """Route one decoded worker stdout line to the corresponding host behavior."""

    def __init__(self, host: Any, *, main_cls: type[Any] | None = None) -> None:
        self._host = host
        self._main_cls = main_cls
        self._status_handler = WorkerStatusHandler(host, main_cls=main_cls)

    @staticmethod
    def _decode_payload(line: str) -> object:
        raw_payload = line.split(":", 1)[1]
        return pickle.loads(base64.b64decode(raw_payload))

    def handle_line(self, line: str) -> None:
        """Process a single worker stdout line."""
        if line.startswith("REMOTE_STATUS:"):
            self._handle_remote_status(line)
        elif line.startswith("REMOTE_LIST_RESULT:"):
            self._handle_remote_list_result(line)
        elif line.startswith("REMOTE_OPEN_RESULT:"):
            self._handle_remote_open_result(line)
        elif line.startswith("STATUS:"):
            self._status_handler.handle_status_line(line)
        elif line.startswith("METADATA:"):
            self._handle_metadata(line)
        elif line.startswith("METADATA_APPEND:"):
            self._handle_metadata_append(line)
        elif line.startswith("IMG_READY:"):
            self._handle_img_ready_payload(line)
        elif line == "READY":
            logger.info("Worker ready signal received")
            self._host._show_status_message("Ready")
        elif line == "IMG_READY":
            self._host._show_status_message("Plot Updated.")
            if self._host._plot_request_in_flight:
                self._host._plot_request_in_flight = False
                self._host._plot_request_expects_image = False
                self._host._set_plot_loading(False)
        elif line.startswith("COORD:"):
            self._handle_coord(line)
        elif line.startswith("CONTOUR_RANGE:"):
            self._handle_contour_range(line)

    def _handle_remote_status(self, line: str) -> None:
        payload = self._decode_payload(line)
        if not isinstance(payload, dict):
            logger.warning("Unexpected REMOTE_STATUS payload type: %s", type(payload).__name__)
            return

        phase = str(payload.get("phase", ""))
        message = str(payload.get("message") or f"Remote worker phase: {phase}")
        is_error = phase == "failed"
        self._host._show_status_message(message, is_error=is_error)

        log_dialog = getattr(self._host, "_pending_prepare_log_dialog", None)
        if log_dialog is not None:
            log_dialog.append_line(message)
            if phase == "failed":
                log_dialog.mark_failed("")
                self._host._pending_prepare_failure_message = message

        if phase in ("ready", "failed"):
            loop = getattr(self._host, "_pending_prepare_loop", None)
            if loop is not None:
                self._host._pending_prepare_loop_ok = phase == "ready"
                self._host._pending_prepare_loop = None
                loop.quit()

    def _handle_remote_list_result(self, line: str) -> None:
        result = self._decode_payload(line)
        if isinstance(result, dict):
            self._host._pending_list_result = result
            loop = getattr(self._host, "_pending_list_loop", None)
            if loop is not None:
                self._host._pending_list_loop = None
                loop.quit()
        else:
            logger.warning("Unexpected REMOTE_LIST_RESULT payload type: %s", type(result).__name__)

    def _handle_remote_open_result(self, line: str) -> None:
        payload = self._decode_payload(line)
        if not isinstance(payload, dict):
            logger.warning("Unexpected REMOTE_OPEN_RESULT payload type: %s", type(payload).__name__)
            return

        self._host._pending_remote_open_result = payload
        open_loop = getattr(self._host, "_pending_remote_open_loop", None)
        if open_loop is not None:
            self._host._pending_remote_open_loop = None
            open_loop.quit()

        if payload.get("ok"):
            uri = str(payload.get("uri", ""))
            if uri:
                if getattr(self._host, "file_open_mode", "single") == "multi":
                    if uri not in self._host._loaded_file_paths:
                        self._host._loaded_file_paths.append(uri)
                    refresh_menu = getattr(self._host, "_refresh_open_files_menu", None)
                    if callable(refresh_menu):
                        refresh_menu()
                    self._host.setWindowTitle(
                        f"{self._host.base_window_title}: {len(self._host._loaded_file_paths)} files"
                    )
                else:
                    self._host._loaded_file_paths = [uri]
                    refresh_menu = getattr(self._host, "_refresh_open_files_menu", None)
                    if callable(refresh_menu):
                        refresh_menu()
                    self._host._set_window_title_for_file(uri)
                self._host._show_status_message(f"Loaded remote file: {uri}")
        else:
            error = str(payload.get("error") or "Remote open failed")
            self._host._show_status_message(error, is_error=True)

    def _handle_metadata(self, line: str) -> None:
        metadata = self._decode_payload(line)
        if isinstance(metadata, list):
            if not all(isinstance(row, dict) for row in metadata):
                logger.warning(
                    "Malformed METADATA payload: expected list of dicts, got mixed types"
                )
                self._host._show_status_message(
                    "Received malformed field metadata from worker.", is_error=True
                )
                return

            logger.info("Received metadata for %d fields", len(metadata))
            row_sources: list[str] = []
            for row in metadata:
                source_raw = row.get("source_file") if isinstance(row, dict) else None
                if not isinstance(source_raw, str) or not source_raw.strip():
                    continue
                source = source_raw.strip()
                if source not in row_sources:
                    row_sources.append(source)

            if row_sources:
                self._host._loaded_file_paths = list(row_sources)
                if len(row_sources) > 1:
                    mode_setter = getattr(self._host, "_set_file_open_mode", None)
                    if callable(mode_setter):
                        mode_setter("multi")
                    else:
                        self._host.file_open_mode = "multi"

            append = bool(getattr(self._host, "_pending_metadata_append", False))
            source_file = getattr(self._host, "_pending_metadata_source", None)
            self._host.populate_field_list(
                metadata,
                append=append,
                source_file=source_file,
            )
            reselect_index = getattr(self._host, "_pending_reselect_field_index", None)
            if isinstance(reselect_index, int) and reselect_index >= 0:
                if reselect_index < self._host.field_list_widget.count():
                    self._host.field_list_widget.setCurrentRow(reselect_index)
            self._host._pending_reselect_field_index = None
            self._host._pending_metadata_append = False
            self._host._pending_metadata_source = None
            metadata_loop = getattr(self._host, "_pending_metadata_loop", None)
            if metadata_loop is not None:
                self._host._pending_metadata_received = True
                self._host._pending_metadata_loop = None
                metadata_loop.quit()
        elif isinstance(metadata, dict):
            logger.info("Received metadata for %d coordinates", len(metadata))
            self._host.build_dynamic_sliders(metadata)
        else:
            logger.warning("Unexpected metadata payload type: %s", type(metadata).__name__)

    def _handle_metadata_append(self, line: str) -> None:
        metadata = self._decode_payload(line)
        if isinstance(metadata, list) and all(isinstance(row, dict) for row in metadata):
            self._host.populate_field_list(
                metadata,
                append=True,
                source_file=getattr(self._host, "_pending_field_op_source", None),
                generated=True,
            )
        else:
            logger.warning("Unexpected METADATA_APPEND payload type: %s", type(metadata).__name__)
        self._host._pending_field_op_source = None
        self._host._pending_binary_operation_name = None

    def _handle_img_ready_payload(self, line: str) -> None:
        logger.info(
            "PLOT_DIAG gui_img_ready pid=%s worker_pid=%s payload_kind=bytes",
            os.getpid(),
            self._host.worker.processId(),
        )
        payload = self._decode_payload(line)
        if isinstance(payload, bytes):
            self._host.set_plot_image(payload)
            self._host._show_status_message("Plot Updated.")
            if self._host._plot_request_in_flight:
                self._host._plot_request_in_flight = False
                self._host._plot_request_expects_image = False
                self._host._set_plot_loading(False)
        else:
            logger.warning("Unexpected IMG_READY payload type: %s", type(payload).__name__)

    def _handle_coord(self, line: str) -> None:
        coords = self._decode_payload(line)
        metadata = self._host._normalize_coordinate_metadata(coords)
        if metadata:
            set_mode = getattr(self._host, "_set_selection_panel_mode", None)
            if callable(set_mode):
                set_mode("single")
            logger.info("Received coordinate metadata for %d sliders", len(metadata))
            self._host.build_dynamic_sliders(metadata)
        else:
            set_mode = getattr(self._host, "_set_selection_panel_mode", None)
            if callable(set_mode):
                set_mode("multi")
            logger.warning("Received empty coordinate metadata payload")
            self._host._show_status_message(
                "No slider-friendly coordinates were found. Use coordinate bounds commands."
            )

    def _handle_contour_range(self, line: str) -> None:
        payload = self._decode_payload(line)
        if isinstance(payload, dict):
            try:
                range_min = float(payload["min"])
                range_max = float(payload["max"])
            except (KeyError, TypeError, ValueError):
                logger.warning("Malformed CONTOUR_RANGE payload: %r", payload)
                return

            suggested_title = payload.get("suggested_title")
            if suggested_title is not None:
                suggested_title = str(suggested_title).strip() or None

            self._host._show_contour_options_dialog(
                range_min,
                range_max,
                suggested_title=suggested_title,
            )
        else:
            logger.warning("Unexpected CONTOUR_RANGE payload type: %s", type(payload).__name__)
