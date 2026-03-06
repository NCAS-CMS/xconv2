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

        field_index = self.field_list_widget.row(item)
        if field_index < 0:
            return

        self._request_coordinates_for_field(field_index)

    def handle_worker_output(self) -> None:
        """Process worker stdout messages and route updates to UI."""
        while self.worker.canReadLine():
            line = self.worker.readLine().data().decode().strip()
            if not line:
                continue

            logger.debug("Worker stdout line: %s", line)

            if line.startswith("STATUS:"):
                self.status.showMessage(line.replace("STATUS:", ""))

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
                    self.status.showMessage("Plot Updated.")
                else:
                    logger.warning("Unexpected IMG_READY payload type: %s", type(payload).__name__)

            elif line == "IMG_READY":
                self.status.showMessage("Plot Updated.")

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

                    self._show_contour_options_dialog(range_min, range_max)
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

    def handle_worker_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """Capture worker shutdown information."""
        logger.warning("Worker finished with exit_code=%s exit_status=%s", exit_code, exit_status)

    def _load_selected_file(self, file_path: str) -> None:
        """Load selected file in worker and publish field metadata."""
        self.status.showMessage(f"Loading file: {file_path}")
        logger.info("Loading file in worker: %s", file_path)

        code = (
            f"_cfview_file_path = {file_path!r}\n"
            "_cfview_field_index = None\n"
            f"f = cf.read({file_path!r})\n"
            + field_list
            + "send_to_gui('METADATA', fields)"
        )
        self._send_worker_task(code)

    def _request_coordinates_for_field(self, index: int) -> None:
        """Request coordinate arrays for a selected field index."""
        self.status.showMessage(f"Loading coordinates for field index {index}...")
        self._send_worker_task(coordinate_list(index))

    def _normalize_coordinate_metadata(self, payload: object) -> dict[str, list[object]]:
        """Normalize worker coordinate payload into slider metadata mapping."""
        metadata: dict[str, list[object]] = {}
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

            metadata[unique_name] = normalized_values

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
            self.status.showMessage(f"No options dialog available for plot type: {plot_kind}")
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

            lo = values[min(start_idx, end_idx)]
            hi = values[max(start_idx, end_idx)]
            selections[name] = (lo, hi)

            collapse_method = self.selected_collapse_methods.get(name)
            if collapse_method:
                collapse_by_coord[name] = collapse_method
                dims.append(1)
            else:
                dims.append(1 if start_idx == end_idx else 2)

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
        if plot_kind == "contour" and not plot_options.get("title"):
            current_file = getattr(self, "current_file_path", None)
            if isinstance(current_file, str) and current_file:
                plot_options["title"] = Path(current_file).name
        if save_plot_path:
            plot_options["filename"] = str(Path(save_plot_path).expanduser())
        elif not plot_options:
            plot_options = None

        try:
            cmd = plot_from_selection(selections, collapse_by_coord, plot_kind, plot_options)
        except (ValueError, NotImplementedError) as exc:
            self.status.showMessage(f"Plot request unavailable: {exc}")
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
            bool(plot_options),
        )
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

