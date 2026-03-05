import sys
import pickle
import base64
import logging
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QSlider, QLabel, QStatusBar, QScrollArea, QPushButton,
                             QLineEdit, QFileDialog, QSystemTrayIcon, QMenu, QStyle,
                             QListWidget)
from PySide6.QtCore import Qt, QProcess
from PySide6.QtGui import QAction, QKeySequence, QIcon

from .cf_templates import field_list 

logger = logging.getLogger(__name__)




class CFVMainWindow(QMainWindow):
    
    def __init__(self):
        super().__init__()

        self.setWindowTitle("cf-view (2026 Core)")
        self.resize(1000, 700)

        self.app_icon = self._create_app_icon()
        if not self.app_icon.isNull():
            self.setWindowIcon(self.app_icon)

        # 1. State Management
        self.controls = {} # Stores {coord_name: (QSlider, values_list)}
        
        # 2. UI Layout
        self.setup_ui()
        
        # 3. Start the Thick Worker (Isolated Process)
        self.worker = QProcess()
        self.worker.readyReadStandardOutput.connect(self.handle_worker_output)
        self.worker.readyReadStandardError.connect(self.handle_worker_error)
        self.worker.errorOccurred.connect(self.handle_worker_process_error)
        self.worker.finished.connect(self.handle_worker_finished)
        
        self.worker.start("cf-worker")
        logger.info("Started worker process: %s", self.worker.program())

        self._setup_tray_icon()

    def _create_app_icon(self):
        """Create application icon with a stable fallback chain."""
        assets_dir = Path(__file__).resolve().parent / "assets"
        candidate_paths = [
            assets_dir / "cf-logo.png",
            assets_dir / "cf-logo.svg",
        ]

        icon = QIcon()
        for candidate in candidate_paths:
            icon = QIcon(str(candidate))
            if not icon.isNull():
                logger.info("Using app icon asset: %s", candidate)
                break

        if icon.isNull():
            logger.warning("No usable icon asset found in %s", assets_dir)

        if icon.isNull():
            icon = QIcon.fromTheme("applications-science")
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        return icon

    def _setup_tray_icon(self):
        """Declare and show the system tray icon with quick actions."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray is not available on this platform/session")
            self.tray_icon = None
            return

        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        tray_menu = QMenu(self)

        show_action = QAction("Show Window", self)
        show_action.triggered.connect(self._show_main_window)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()
        tray_menu.addAction("Quit", self._quit_application)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setToolTip("cf-view")
        self.tray_icon.activated.connect(self._handle_tray_activation)
        self.tray_icon.show()

        logger.info("System tray icon initialized")

    def _show_main_window(self):
        """Bring the main window to the foreground."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _handle_tray_activation(self, reason):
        """Handle tray click by restoring the main window."""
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_main_window()

    def setup_ui(self):
        """
        Set up the main window layout and top-level widgets.
        """
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)

        left_panel = self._create_left_panel()
        self.plot_area = self._create_plot_area()
        layout.addWidget(left_panel)
        layout.addWidget(self.plot_area, stretch=1)

        self._setup_menu_bar()
        self._setup_status_bar()

    def _setup_menu_bar(self):
        """Create application menu actions."""
        file_menu = self.menuBar().addMenu("&File")

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self._quit_application)

        file_menu.addAction(quit_action)

    def _create_left_panel(self):
        """
        Create the left panel containing file controls, field list, and slider area.
        """
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addLayout(self._create_file_picker_row())
        left_layout.addWidget(self._create_field_list_area())
        left_layout.addWidget(self._create_slider_scroll_area())
        return left_panel

    def _create_field_list_area(self):
        """Create a scrollable list of fields loaded from metadata."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Fields")
        self.field_list_widget = QListWidget()
        self.field_list_widget.setMinimumHeight(120)
        self.field_list_widget.setMaximumHeight(220)

        layout.addWidget(title)
        layout.addWidget(self.field_list_widget)
        return container

    def _create_file_picker_row(self):
        """
        Create the file picker row (path display + browse/quit buttons).
        """
        file_picker_row = QHBoxLayout()

        self.file_path_input = QLineEdit()
        self.file_path_input.setReadOnly(True)
        self.file_path_input.setPlaceholderText("Select a data file...")

        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._choose_file)

        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(self._quit_application)

        file_picker_row.addWidget(self.file_path_input, stretch=1)
        file_picker_row.addWidget(browse_button)
        file_picker_row.addWidget(quit_button)
        return file_picker_row

    def _create_slider_scroll_area(self):
        """
        Create the scrollable container that hosts dynamic sliders.
        """
        self.sidebar = QVBoxLayout()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        sidebar_container = QWidget()
        sidebar_container.setLayout(self.sidebar)
        scroll.setWidget(sidebar_container)
        scroll.setFixedWidth(300)
        return scroll

    def _create_plot_area(self):
        """
        Create the right-side placeholder plot area.
        """
        plot_area = QLabel("Waiting for data...")
        plot_area.setAlignment(Qt.AlignCenter)
        plot_area.setStyleSheet("background-color: #222; color: #888; border: 1px solid #444;")
        return plot_area

    def _setup_status_bar(self):
        """
        Create and initialize the status bar.
        """
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("System Ready. Initialize S3 Load.")

    def handle_worker_output(self):
        """
        Processes the stream from the Worker's stdout.
        """
        while self.worker.canReadLine():
            line = self.worker.readLine().data().decode().strip()
            if not line:
                continue

            logger.debug("Worker stdout line: %s", line)
            
            if line.startswith("STATUS:"):
                self.status.showMessage(line.replace("STATUS:", ""))
            
            elif line.startswith("METADATA:"):
                # Decode the pickled coordinate dictionary
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
                # Trigger the Shared Memory / Pixmap update logic we discussed
                self.status.showMessage("Plot Updated.")

    def handle_worker_error(self):
        """Log worker stderr output for troubleshooting."""
        stderr_output = self.worker.readAllStandardError().data().decode(errors="replace").strip()
        if stderr_output:
            logger.error("Worker stderr: %s", stderr_output)

    def handle_worker_process_error(self, process_error):
        """Capture QProcess-level failures, such as start or crash issues."""
        logger.error("Worker process error: %s", process_error)

    def handle_worker_finished(self, exit_code, exit_status):
        """Capture worker shutdown information."""
        logger.warning("Worker finished with exit_code=%s exit_status=%s", exit_code, exit_status)

    def build_dynamic_sliders(self, metadata):
        """
        The Slider Factory: Builds UI from Coordinate Metadata.
        """
        # Clear sidebar
        for i in reversed(range(self.sidebar.count())): 
            self.sidebar.itemAt(i).widget().setParent(None)

        for name, values in metadata.items():
            container = QWidget()
            row = QVBoxLayout(container)
            
            label = QLabel(f"{name.upper()}: {values[0]}")
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, len(values) - 1)
            
            # Use a lambda with default args to capture current loop state
            slider.valueChanged.connect(
                lambda v, n=name, vals=values, lbl=label: 
                self.on_slider_moved(n, vals[v], lbl)
            )
            
            row.addWidget(label)
            row.addWidget(slider)
            self.sidebar.addWidget(container)
            self.controls[name] = (slider, values)

        logger.info("Built %d dynamic sliders", len(self.controls))

    def populate_field_list(self, fields):
        """Populate the field list UI from worker metadata."""
        self.field_list_widget.clear()
        self.field_list_widget.addItems([str(field) for field in fields])
        logger.info("Displayed %d fields in list", self.field_list_widget.count())

    def on_slider_moved(self, name, val, label):
        """
        Handle slider movement events.

        Parameters:
        - name: The name of the coordinate.
        - val: The new value of the slider.
        - label: The QLabel associated with the slider.
        """
        label.setText(f"{name.upper()}: {val}")
        logger.debug("Slider moved: %s=%r", name, val)
        # Automatically trigger a lightweight 'Preview' update in the worker
        self._request_plot_update()

    def _choose_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Data File",
            "",
            "NetCDF files (*.nc *.nc4 *.cdf);;All files (*)"
        )
        if not file_path:
            return

        self.file_path_input.setText(file_path)
        logger.info("Selected file: %s", file_path)
        self._load_selected_file(file_path)

    def _load_selected_file(self, file_path):
        """Load selected data file in the worker and publish slider metadata."""
        self.status.showMessage(f"Loading file: {file_path}")
        logger.info("Loading file in worker: %s", file_path)

        # Grab the field list
        code = f"f = cf.read({file_path!r})\n" + \
                field_list + \
                "send_to_gui('METADATA', fields)" 
        self._send_worker_task(code)

    def _request_plot_update(self):
        """Request a new plot from the worker for the current slider subspace."""
        if not self.controls:
            logger.debug("Skipped plot update request because no controls are available")
            return

        # Generate the 'subspace' command string based on current slider positions
        cmd = "f_slice = f.subspace("
        slices = [f"{n}={self.controls[n][1][self.controls[n][0].value()]!r}" for n in self.controls]
        cmd += ", ".join(slices) + ")\ncfp.con(f_slice)\n"
        logger.debug("Requesting plot update with %d slider constraints", len(slices))
        self._send_worker_task(cmd)

    def _send_worker_task(self, code):
        """Send a code block to the worker process with task terminator."""
        if not code.endswith("\n"):
            code += "\n"
        logger.debug("Sending worker task (%d chars)", len(code))
        self.worker.write((code + "#END_TASK\n").encode())

    def _quit_application(self):
        """Handle quit button click by closing the main window."""
        logger.info("Quit requested from UI")
        self.close()

    def closeEvent(self, event):
        """Ensure worker process is shut down cleanly when the GUI exits."""
        if getattr(self, "tray_icon", None) is not None:
            self.tray_icon.hide()

        if self.worker.state() != QProcess.NotRunning:
            logger.info("Terminating worker process")
            self.worker.terminate()
            if not self.worker.waitForFinished(2000):
                logger.warning("Worker did not terminate in time; killing process")
                self.worker.kill()
                self.worker.waitForFinished(1000)

        super().closeEvent(event)

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Launching cf-view GUI")

    # 1. Create the Qt Application
    # This must exist before any widgets are created
    app = QApplication.instance() or QApplication(sys.argv)

    # 2. Initialize your Main Window
    window = CFVMainWindow()
    if not window.app_icon.isNull():
        app.setWindowIcon(window.app_icon)
    window.show()

    # 3. Start the event loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()