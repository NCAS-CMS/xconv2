from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

if TYPE_CHECKING:
    from xconv2.core_window import CFVCore


class LineplotOptionsController:
    """Encapsulate the lineplot options dialog scaffold."""

    def __init__(self, host: "CFVCore") -> None:
        self.host = host

    def show_lineplot_options_dialog(self) -> None:
        """Show placeholder lineplot options dialog."""
        dialog = QDialog(self.host)
        dialog.setWindowTitle("Lineplot Options")
        dialog.resize(420, 180)

        layout = QVBoxLayout(dialog)

        message = QLabel("Lineplot options are not yet operational.")
        message.setWordWrap(True)
        message.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(message)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.exec()