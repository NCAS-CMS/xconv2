import pytest

from PySide6.QtCore import QCoreApplication, QEvent, QProcess, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from xconv2.main_window import CFVMain
from xconv2.ui.menu_controller import MenuController
from xconv2.ui.science_stack_dialog import ScienceStackDialog


@pytest.fixture
def widgets():
    app = QApplication.instance() or QApplication([])
    owned = []

    def track(widget):
        owned.append(widget)
        return widget

    try:
        yield app, track
    finally:
        for widget in reversed(owned):
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_science_stack_dialog_is_non_modal_and_read_only(widgets) -> None:
    app, track = widgets
    dialog = track(ScienceStackDialog(None))
    dialog.report.setPlainText("cf: 3.21.0\ncfdm: git install; commit: abc")
    dialog.show()
    app.processEvents()
    assert dialog.isVisible()
    assert not dialog.isModal()
    assert dialog.windowModality() == Qt.NonModal
    assert dialog.report.isReadOnly()
    assert "commit: abc" in dialog.report.toPlainText()
    dialog.close()


def test_help_action_requests_science_stack(widgets) -> None:
    app, track = widgets

    class _Host(QMainWindow):
        requested = False

        def _show_about_dialog(self) -> None:
            pass

        def _show_science_stack(self) -> None:
            self.requested = True

        def _open_issue_tracker(self) -> None:
            pass

        def _open_roadmap(self) -> None:
            pass

    host = track(_Host())
    MenuController(host)._setup_help_menu(host.menuBar(), 10, 400)
    menu = host.menuBar().cornerWidget(Qt.TopRightCorner).menu()
    action = next(action for action in menu.actions() if action.text() == "Science Stack...")
    action.trigger()
    app.processEvents()
    assert host.requested
    host.close()


def test_window_requests_report_once_and_refreshes_on_reopen(widgets) -> None:
    app, track = widgets

    class _Worker:
        def state(self):
            return QProcess.Running

    class _Host(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._science_stack_dialog = None
            self.worker = _Worker()
            self.requests = []

        def _send_worker_control_task(self, kind, payload) -> None:
            self.requests.append((kind, payload))

    host = track(_Host())
    CFVMain._show_science_stack(host)
    app.processEvents()
    dialog = host._science_stack_dialog
    assert "Waiting" in dialog.report.toPlainText()
    assert host.requests == [("SCIENCE_STACK", {})]
    assert not dialog.isModal()

    CFVMain._receive_science_stack_report(host, "cf: 3.21.0\ncommit: abc")
    assert dialog.report.toPlainText() == "cf: 3.21.0\ncommit: abc"
    CFVMain._show_science_stack(host)
    assert len(host.requests) == 1

    dialog.close()
    CFVMain._show_science_stack(host)
    assert host._science_stack_dialog is dialog
    assert len(host.requests) == 2
    dialog.close()
    host.close()


def test_window_reports_stopped_worker_without_sending_request(widgets) -> None:
    app, track = widgets

    class _Worker:
        def state(self):
            return QProcess.NotRunning

    class _Host(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._science_stack_dialog = None
            self.worker = _Worker()

        def _send_worker_control_task(self, kind, payload) -> None:
            raise AssertionError("Request sent to stopped worker")

    host = track(_Host())
    CFVMain._show_science_stack(host)
    app.processEvents()
    assert "not running" in host._science_stack_dialog.report.toPlainText()
    host._science_stack_dialog.close()
    host.close()
