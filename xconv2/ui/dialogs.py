from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class InputDialogCustom(QDialog):
    """Reusable item chooser with optional rich-text documentation below input."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        label: str,
        items: list[str],
        current_index: int,
        editable: bool,
        flags: Qt.WindowType,
        input_method_hints: Qt.InputMethodHint,
        doc_text: str,
    ) -> None:
        super().__init__(parent, flags)
        self.setWindowTitle(title)

        layout = QVBoxLayout(self)

        prompt = QLabel(label)
        layout.addWidget(prompt)

        self.item_combo = QComboBox()
        self.item_combo.addItems(items)
        self.item_combo.setEditable(editable)
        self.item_combo.setInputMethodHints(input_method_hints)
        if items:
            self.item_combo.setCurrentIndex(max(0, min(current_index, len(items) - 1)))
        layout.addWidget(self.item_combo)

        if doc_text:
            doc_label = QLabel(doc_text)
            doc_label.setTextFormat(Qt.RichText)
            doc_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
            doc_label.setOpenExternalLinks(True)
            doc_label.setWordWrap(True)
            layout.addWidget(doc_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @classmethod
    def getItem(
        cls,
        parent: QWidget | None,
        title: str,
        label: str,
        items: list[str],
        current: int = 0,
        editable: bool = True,
        flags: Qt.WindowType = Qt.WindowType.Widget,
        inputMethodHints: Qt.InputMethodHint = Qt.InputMethodHint.ImhNone,
        doc_text: str = "",
    ) -> tuple[str, bool]:
        """Mirror QInputDialog.getItem with extra ``doc_text`` rich-text content."""
        dialog = cls(
            parent,
            title,
            label,
            items,
            current,
            editable,
            flags,
            inputMethodHints,
            doc_text,
        )
        if dialog.exec() != QDialog.Accepted:
            return "", False
        return dialog.item_combo.currentText(), True


class OpenGlobDialog(QDialog):
    """Dialog for selecting a base directory and glob expression."""

    def __init__(self, parent: QWidget | None, initial_directory: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open Glob")

        layout = QVBoxLayout(self)

        directory_label = QLabel("Base folder:")
        layout.addWidget(directory_label)

        directory_row = QHBoxLayout()
        self.directory_edit = QLineEdit(initial_directory)
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._choose_directory)
        directory_row.addWidget(self.directory_edit, 1)
        directory_row.addWidget(browse_button)
        layout.addLayout(directory_row)

        pattern_label = QLabel("Glob pattern:")
        layout.addWidget(pattern_label)

        self.pattern_edit = QLineEdit("*.nc")
        self.pattern_edit.setPlaceholderText("Examples: *.nc, run*/atm_*.nc, **/*.nc")
        layout.addWidget(self.pattern_edit)

        hint = QLabel("Use shell-style wildcards. Recursive matching is supported with **.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_directory(self) -> None:
        """Prompt for a base directory used to resolve glob patterns."""
        start_dir = self.directory_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Select Base Folder", start_dir)
        if selected:
            self.directory_edit.setText(selected)

    @classmethod
    def get_glob_expression(
        cls,
        parent: QWidget | None,
        initial_directory: str,
    ) -> tuple[str, bool]:
        """Return a '<base>/<pattern>' expression and acceptance state."""
        dialog = cls(parent, initial_directory)
        if dialog.exec() != QDialog.Accepted:
            return "", False

        base_dir = dialog.directory_edit.text().strip()
        pattern = dialog.pattern_edit.text().strip()
        if not base_dir or not pattern:
            return "", False

        expression = str((Path(base_dir).expanduser() / pattern))
        return expression, True


class OpenURIDialog(QDialog):
    """Dialog for collecting a URI and placeholder remote access options."""

    _PROTOCOLS = ["S3", "HTTPS", "SSH"]

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open URI")

        layout = QVBoxLayout(self)

        uri_label = QLabel("URI:")
        layout.addWidget(uri_label)

        self.uri_edit = QLineEdit("")
        self.uri_edit.setPlaceholderText("Examples: s3://bucket/path, https://host/path, ssh://user@host/path")
        layout.addWidget(self.uri_edit)

        options_group = QGroupBox("Access options")
        options_layout = QVBoxLayout(options_group)
        self.protocol_tabs = QTabWidget()
        for protocol in self._PROTOCOLS:
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.addWidget(QLabel(f"{protocol} access options are not implemented yet."))
            tab_layout.addStretch(1)
            self.protocol_tabs.addTab(tab, protocol)
        options_layout.addWidget(self.protocol_tabs)
        layout.addWidget(options_group)

        buttons = QDialogButtonBox()
        cancel_button = buttons.addButton(QDialogButtonBox.Cancel)
        quit_button = buttons.addButton("Quit", QDialogButtonBox.AcceptRole)
        cancel_button.clicked.connect(self.reject)
        quit_button.clicked.connect(self.accept)
        layout.addWidget(buttons)

    @classmethod
    def get_uri(cls, parent: QWidget | None) -> tuple[str, str, bool]:
        """Return the entered URI, selected protocol, and acceptance state."""
        dialog = cls(parent)
        if dialog.exec() != QDialog.Accepted:
            return "", "", False

        uri = dialog.uri_edit.text().strip()
        protocol = cls._PROTOCOLS[dialog.protocol_tabs.currentIndex()]
        return uri, protocol, True
