from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
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
