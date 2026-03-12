from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
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
