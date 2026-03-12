from __future__ import annotations

import ast
import csv
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

if TYPE_CHECKING:
    from xconv2.core_window import CFVCore

logger = logging.getLogger(__name__)


class FieldMetadataController:
    """Handle field list population, selection detail, and properties UI."""

    def __init__(self, host: "CFVCore", field_metadata_separator: str) -> None:
        self.host = host
        self.field_metadata_separator = field_metadata_separator

    def set_field_list_hint(self, text: str) -> None:
        """Show a non-selectable hint message in the fields list."""
        self.host.field_list_widget.clear()
        hint_item = QListWidgetItem(text)
        hint_item.setFlags(Qt.NoItemFlags)
        self.host.field_list_widget.addItem(hint_item)

    def set_selection_info_text(self, text: str) -> None:
        """Update selection detail text in the right-hand info panel."""
        self.host.current_selection_info_text = text
        info_widget = getattr(self.host, "plot_info_output", None)
        if info_widget is not None:
            info_widget.setPlainText(text)

    def show_selection_properties(self) -> None:
        """Show properties for the currently selected field."""
        selected_item = self.host.field_list_widget.currentItem()
        if selected_item is None:
            self.host.status.showMessage("Select a field to view properties.")
            return

        selected_field = selected_item.text()
        raw_properties = selected_item.data(Qt.UserRole + 1)
        properties = self.parse_properties_dict(raw_properties)

        if not properties:
            self.host.status.showMessage("No properties available for this field.")
            return

        dialog = QDialog(self.host)
        dialog.setWindowTitle(f"Properties: {selected_field}")
        dialog.resize(700, 420)

        layout = QVBoxLayout(dialog)
        table = QTableWidget(dialog)
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Key", "Value"])
        table.setRowCount(len(properties))
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideRight)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.verticalHeader().setDefaultSectionSize(table.fontMetrics().height() + 6)
        table.verticalHeader().setMinimumSectionSize(table.fontMetrics().height() + 6)

        for row, (key, value) in enumerate(sorted(properties.items(), key=lambda kv: str(kv[0]).lower())):
            key_text = str(key)
            value_text = str(value)

            key_item = QTableWidgetItem(key_text)
            key_item.setToolTip(key_text)
            value_item = QTableWidgetItem(value_text)
            value_item.setToolTip(value_text)

            table.setItem(row, 0, key_item)
            table.setItem(row, 1, value_item)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        key_max_width = 260
        if table.columnWidth(0) > key_max_width:
            table.setColumnWidth(0, key_max_width)

        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)

        controls_row = QHBoxLayout()
        controls_row.addStretch(1)
        save_button = QPushButton("Save CSV...")
        save_button.clicked.connect(
            lambda: self.save_properties_to_csv(properties, selected_field, dialog)
        )
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        controls_row.addWidget(save_button)
        controls_row.addWidget(close_button)

        layout.addWidget(table)
        layout.addLayout(controls_row)
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.open()

    def save_properties_to_csv(
        self,
        properties: dict[object, object],
        field_name: str,
        parent: QWidget | None = None,
    ) -> None:
        """Save properties dictionary to a CSV file with Key/Value columns."""
        safe_field_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in field_name)
        default_name = f"{safe_field_name or 'field'}_properties.csv"
        default_path = str(Path.home() / default_name)

        file_path, _ = QFileDialog.getSaveFileName(
            parent or self.host,
            "Save Properties as CSV",
            default_path,
            "CSV files (*.csv);;All files (*)",
        )
        if not file_path:
            return

        if not file_path.lower().endswith(".csv"):
            file_path += ".csv"

        rows = sorted(properties.items(), key=lambda kv: str(kv[0]).lower())
        with open(file_path, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["Key", "Value"])
            for key, value in rows:
                writer.writerow([str(key), str(value)])

        self.host.status.showMessage(f"Saved properties CSV: {file_path}")
        logger.info("Saved properties CSV: %s", file_path)

    def parse_properties_dict(self, raw_properties: object) -> dict[object, object]:
        """Parse properties payload into a dictionary when possible."""
        logger.info("Parsing properties payload of type %s", type(raw_properties).__name__)
        logger.info("Raw properties content: %r", raw_properties)
        if isinstance(raw_properties, dict):
            return raw_properties

        if isinstance(raw_properties, str) and raw_properties.strip():
            text = raw_properties.strip()

            if text.startswith("OrderedDict(") and text.endswith(")"):
                inner = text[len("OrderedDict(") : -1]
                try:
                    ordered_items = ast.literal_eval(inner)
                    if isinstance(ordered_items, list):
                        return dict(ordered_items)
                except (SyntaxError, ValueError, TypeError):
                    pass

            try:
                parsed = ast.literal_eval(text)
                if isinstance(parsed, dict):
                    return parsed
            except (SyntaxError, ValueError):
                pass

            fallback = self.parse_properties_lines(text)
            if fallback:
                return fallback

            logger.warning(
                "Could not parse properties payload into dict (type=%s, preview=%r)",
                type(raw_properties).__name__,
                text[:240],
            )

        return {}

    def parse_properties_lines(self, text: str) -> dict[str, str]:
        """Parse key/value properties from multi-line text representations."""
        parsed: dict[str, str] = {}

        normalized = text.strip()
        if normalized.startswith("{") and normalized.endswith("}"):
            normalized = normalized[1:-1]

        raw_lines = normalized.splitlines()
        if len(raw_lines) == 1 and "," in normalized:
            raw_lines = normalized.split(",")

        for raw_line in raw_lines:
            line = raw_line.strip()
            if not line:
                continue

            line = line.strip("{}")

            if " = " in line:
                key, value = line.split(" = ", 1)
            elif ": " in line:
                key, value = line.split(": ", 1)
            elif ":" in line:
                key, value = line.split(":", 1)
            else:
                continue

            key = key.strip().strip("'\"")
            value = value.strip().strip(",").strip().strip("'\"")
            if key:
                parsed[key] = value

        return parsed

    def set_field_list_visible_rows(self, row_count: int) -> None:
        """Size the field list to show a target number of rows by default."""
        row_height = self.host.field_list_widget.sizeHintForRow(0)
        if row_height <= 0:
            row_height = self.host.field_list_widget.fontMetrics().lineSpacing() + 6

        frame = self.host.field_list_widget.frameWidth() * 2
        height = (row_height * row_count) + frame
        self.host.field_list_widget.setMinimumHeight(height)
        self.host.field_list_widget.setMaximumHeight(height)

    def populate_field_list(self, fields: Sequence[object]) -> None:
        """Populate the field list UI from worker metadata."""
        self.host.field_list_widget.clear()

        for field in fields:
            if isinstance(field, str) and self.field_metadata_separator in field:
                parts = field.split(self.field_metadata_separator, 2)
                identity = parts[0]
                detail = parts[1] if len(parts) > 1 else parts[0]
                properties = parts[2] if len(parts) > 2 else ""
            elif isinstance(field, (tuple, list)) and len(field) >= 2:
                identity = str(field[0])
                detail = str(field[1])
                properties = str(field[2]) if len(field) > 2 else ""
            else:
                identity = str(field)
                detail = str(field)
                properties = ""

            item = QListWidgetItem(identity)
            item.setData(Qt.UserRole, detail)
            item.setData(Qt.UserRole + 1, properties)
            self.host.field_list_widget.addItem(item)

        self.set_field_list_visible_rows(self.host._field_list_rows())
        self.set_selection_info_text(
            f"Loaded {self.host.field_list_widget.count()} fields.\n"
            "Click an entry to show field details."
        )
        logger.info("Displayed %d fields in list", self.host.field_list_widget.count())

    def on_field_clicked(self, item: QListWidgetItem) -> None:
        """Display selected field details in the output panel."""
        selected_field = item.text()
        detail = item.data(Qt.UserRole)
        if detail:
            detail = "\n".join(detail.splitlines()[2:])
            self.set_selection_info_text(detail)
        else:
            self.set_selection_info_text("No additional detail available.")
        logger.info("Field selected: %s", selected_field)
