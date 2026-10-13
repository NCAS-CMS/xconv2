"""Non-modal scientific environment viewer."""

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QDialog, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class ScienceStackDialog(QDialog):
    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Science Stack")
        self.setModal(False)
        self.resize(850, 600)
        layout = QVBoxLayout(self)
        self.report = QPlainTextEdit(self)
        self.report.setReadOnly(True)
        self.report.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.report.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.report)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)
