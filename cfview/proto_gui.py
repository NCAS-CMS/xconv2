import sys
import pickle
import base64
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QSlider, QLabel, QStatusBar, QPushButton, QScrollArea)
from PySide6.QtCore import Qt, QProcess

class CMSMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NCAS-CMS: cf-view (2026 Core)")
        self.resize(1000, 700)

        # 1. State Management
        self.controls = {} # Stores {coord_name: (QSlider, values_list)}
        
        # 2. UI Layout
        self.setup_ui()
        
        # 3. Start the Thick Worker (Isolated Process)
        self.worker = QProcess()
        self.worker.readyReadStandardOutput.connect(self.handle_worker_output)
        # Using -u for unbuffered output to ensure real-time status updates
        self.worker.start("python", ["-u", "worker.py"])

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)

        # Left Panel: Dynamic Sliders
        self.sidebar = QVBoxLayout()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        sidebar_container = QWidget()
        sidebar_container.setLayout(self.sidebar)
        scroll.setWidget(sidebar_container)
        scroll.setFixedWidth(300)
        
        # Right Panel: Plot Preview (Placeholder)
        self.plot_area = QLabel("Waiting for data...")
        self.plot_area.setAlignment(Qt.AlignCenter)
        self.plot_area.setStyleSheet("background-color: #222; color: #888; border: 1px solid #444;")

        layout.addWidget(scroll)
        layout.addWidget(self.plot_area, stretch=1)

        # Status Bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("System Ready. Initialize S3 Load.")

    def handle_worker_output(self):
        """Processes the stream from the Worker's stdout."""
        while self.worker.canReadLine():
            line = self.worker.readLine().data().decode().strip()
            
            if line.startswith("STATUS:"):
                self.status.showMessage(line.replace("STATUS:", ""))
            
            elif line.startswith("METADATA:"):
                # Decode the pickled coordinate dictionary
                raw_payload = line.split(":")[1]
                metadata = pickle.loads(base64.b64decode(raw_payload))
                self.build_dynamic_sliders(metadata)
                
            elif line.startswith("IMG_READY:"):
                # Trigger the Shared Memory / Pixmap update logic we discussed
                self.status.showMessage("Plot Updated.")

    def build_dynamic_sliders(self, metadata):
        """The Slider Factory: Builds UI from Coordinate Metadata."""
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

    def on_slider_moved(self, name, val, label):
        label.setText(f"{name.upper()}: {val}")
        # Automatically trigger a lightweight 'Preview' update in the worker
        self.request_plot_update()

    def request_plot_update(self):
        # Generate the 'subspace' command string based on current slider positions
        cmd = "f_slice = f.subspace("
        slices = [f"{n}={self.controls[n][1][self.controls[n][0].value()]!r}" for n in self.controls]
        cmd += ", ".join(slices) + ")\ncfp.con(f_slice)\n#END_TASK\n"
        self.worker.write(cmd.encode())