"""Worker-backed window classes for cf-view.

This module layers backend interaction onto `CFVCore`:
- starts/stops the worker process
- sends worker tasks
- handles stdout/stderr protocol messages
"""

from __future__ import annotations

import base64
import logging
import pickle
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QListWidgetItem

from .cf_templates import (
    contour_range_from_selection,
    coordinate_list,
    field_list,
    plot_from_selection,
)
from .core_window import CFVCore

logger = logging.getLogger(__name__)


class CFVMain(CFVCore):
    """Worker-backed application behavior layered on top of the core GUI."""

    def __init__(self) -> None:
        super().__init__()
        self._plot_request_in_flight = False
        self._plot_request_expects_image = False
        self._suppress_stale_error_status = False

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

            if line.startswith("STATUS:"):
                status_text = line.split(":", 1)[1]
                is_error_status = status_text.startswith("Error -")

                # Ignore delayed worker errors from a previous task right after field reselection.
                if self._suppress_stale_error_status and is_error_status and not self._plot_request_in_flight:
                    logger.debug("Ignoring stale worker error status after field reset: %s", status_text)
                    continue

                self._show_status_message(
                    status_text,
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
                    logger.info("Received metadata for %d fields", len(metadata))
                    self.populate_field_list(metadata)
                elif isinstance(metadata, dict):
                    logger.info("Received metadata for %d coordinates", len(metadata))
                    self.build_dynamic_sliders(metadata)
                else:
                    logger.warning("Unexpected metadata payload type: %s", type(metadata).__name__)

            elif line.startswith("IMG_READY:"):
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
        if varying_dims == 1:
            plot_kind = "lineplot"
        elif varying_dims == 2:
            plot_kind = "contour"
        elif varying_dims == 0:
            plot_kind = "collapsed"
        else:
            plot_kind = "unsupported"

        return selections, collapse_by_coord, plot_kind

    def _request_plot_task(self, save_code_path: str | None, save_plot_path: str | None) -> None:
        """Build and send a plot task with optional code-save and plot-save paths."""
        context = self._build_plot_context()
        if context is None:
            logger.debug("Skipped plot update request because no controls are available")
            return
        selections, collapse_by_coord, plot_kind = context

        if plot_kind in {"collapsed", "unsupported"}:
            logger.debug("Skipped plot request due to unsupported dimensionality kind=%s", plot_kind)
            return

        plot_options = dict(self.plot_options_by_kind.get(plot_kind, {}))
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
        logger.debug("Sending worker task (%d chars)", len(code))
        self.worker.write(payload.encode())

    def closeEvent(self, event: QCloseEvent) -> None:
        """Ensure worker process is shut down cleanly when GUI exits."""
        if self.worker.state() != QProcess.NotRunning:
            logger.info("Terminating worker process")
            self.worker.terminate()
            if not self.worker.waitForFinished(2000):
                logger.warning("Worker did not terminate in time; killing process")
                self.worker.kill()
                self.worker.waitForFinished(1000)

        super().closeEvent(event)

