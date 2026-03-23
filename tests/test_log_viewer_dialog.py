from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from xconv2.core_window import LogViewerDialog
from xconv2.remote_access import RemoteAccessSession


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is not None:
        return app
    return QApplication([])


class _HostWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[bool, bool, str]] = []

    def _current_logging_configuration(self):
        return RemoteAccessSession.logging_configuration()

    def _apply_logging_configuration_from_ui(
        self,
        *,
        trace_remote_fs: bool,
        trace_remote_file_io: bool,
        level_name: str,
    ) -> None:
        self.calls.append((trace_remote_fs, trace_remote_file_io, level_name))


def test_log_viewer_dialog_applies_runtime_logging_via_host_callback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    original = RemoteAccessSession.logging_configuration()
    host = _HostWidget()
    log_path = tmp_path / "xconv2.log"
    log_path.write_text("", encoding="utf-8")

    try:
        RemoteAccessSession.configure_logging(level=logging.WARNING)
        dialog = LogViewerDialog(host, log_path)
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *args, **kwargs: QMessageBox.Ok))

        dialog.trace_fs_cb.setChecked(True)
        dialog.trace_fileio_cb.setChecked(True)
        dialog.log_level_combo.setCurrentText("DEBUG")
        dialog._apply_advanced_options()

        assert host.calls == [(True, True, "DEBUG")]
    finally:
        RemoteAccessSession.configure_logging(
            level=original.level,
            trace_filesystem=original.trace_filesystem,
            trace_file_io=original.trace_file_io,
        )
        host.deleteLater()