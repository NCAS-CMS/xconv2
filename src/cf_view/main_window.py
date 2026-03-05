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

from PySide6.QtCore import QProcess
from PySide6.QtGui import QCloseEvent

from .cf_templates import field_list
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
                self.status.showMessage("Plot Updated.")

    def handle_worker_error(self) -> None:
        """Log worker stderr output for troubleshooting."""
        stderr_output = self.worker.readAllStandardError().data().decode(errors="replace").strip()
        if stderr_output:
            logger.error("Worker stderr: %s", stderr_output)

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

        code = f"f = cf.read({file_path!r})\n" + field_list + "send_to_gui('METADATA', fields)"
        self._send_worker_task(code)

    def _request_plot_update(self) -> None:
        """Request a new plot from worker for the current slider subspace."""
        if not self.controls:
            logger.debug("Skipped plot update request because no controls are available")
            return

        cmd = "f_slice = f.subspace("
        slices = [f"{n}={self.controls[n][1][self.controls[n][0].value()]!r}" for n in self.controls]
        cmd += ", ".join(slices) + ")\ncfp.con(f_slice)\n"
        logger.debug("Requesting plot update with %d slider constraints", len(slices))
        self._send_worker_task(cmd)

    def _send_worker_task(self, code: str) -> None:
        """Send a code block to the worker process with task terminator."""
        if not code.endswith("\n"):
            code += "\n"
        logger.debug("Sending worker task (%d chars)", len(code))
        self.worker.write((code + "#END_TASK\n").encode())

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

