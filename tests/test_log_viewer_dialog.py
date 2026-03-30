from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from xconv2.core_window import LogViewerDialog, ScopedLoggingConfigDialog
from xconv2.remote_access import RemoteAccessSession


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is not None:
        return app
    return QApplication([])


class _HostWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, str]] = []

    def _current_logging_configuration(self):
        return RemoteAccessSession.logging_configuration()

    def _apply_logging_configuration_from_ui(
        self,
        *,
        scope_levels: dict[str, str],
    ) -> None:
        self.calls.append(scope_levels)


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
        RemoteAccessSession.configure_logging(
            scope_levels={
                "all": "WARNING",
                "pyfive": "WARNING",
                "p5rem": "WARNING",
                "fsspec": "WARNING",
                "paramiko": "WARNING",
                "xconv2": "WARNING",
                "cfdm_cf_python": "WARNING",
                "cfplot": "WARNING",
            }
        )
        dialog = LogViewerDialog(host, log_path)
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *args, **kwargs: QMessageBox.Ok))

        class _AcceptedConfigDialog:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def exec(self):
                return 1

            def selected_scope_levels(self):
                return {
                    "all": "WARNING",
                    "pyfive": "WARNING",
                    "p5rem": "WARNING",
                    "fsspec": "INFO",
                    "paramiko": "WARNING",
                    "xconv2": "DEBUG",
                    "cfdm_cf_python": "WARNING",
                    "cfplot": "WARNING",
                }

        monkeypatch.setattr("xconv2.core_window.ScopedLoggingConfigDialog", _AcceptedConfigDialog)

        dialog._open_logging_configuration()

        assert host.calls == [
            {
                "all": "WARNING",
                "pyfive": "WARNING",
                "p5rem": "WARNING",
                "fsspec": "INFO",
                "paramiko": "WARNING",
                "xconv2": "DEBUG",
                "cfdm_cf_python": "WARNING",
                "cfplot": "WARNING",
            }
        ]
    finally:
        RemoteAccessSession.configure_logging(
            scope_levels=original.scope_levels,
        )
        host.deleteLater()


def test_scoped_logging_dialog_apply_all_level_updates_every_scope() -> None:
    _ensure_qapp()
    dialog = ScopedLoggingConfigDialog(
        None,
        scope_levels={
            "all": logging.WARNING,
            "pyfive": logging.WARNING,
            "p5rem": logging.INFO,
            "fsspec": logging.WARNING,
            "paramiko": logging.WARNING,
            "xconv2": logging.DEBUG,
            "cfdm_cf_python": logging.WARNING,
            "cfplot": logging.WARNING,
        },
    )

    dialog._rows["all"]["DEBUG"].setChecked(True)
    dialog._apply_all_level_to_all_scopes()

    selected = dialog.selected_scope_levels()
    assert selected == {
        "all": "DEBUG",
        "pyfive": "DEBUG",
        "p5rem": "DEBUG",
        "fsspec": "DEBUG",
        "paramiko": "DEBUG",
        "xconv2": "DEBUG",
        "cfdm_cf_python": "DEBUG",
        "cfplot": "DEBUG",
    }