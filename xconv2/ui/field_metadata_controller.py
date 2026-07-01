from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QListWidgetItem,
    QPlainTextEdit,
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

    def __init__(self, host: "CFVCore") -> None:
        self.host = host

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

        table.itemClicked.connect(
            lambda item: self._show_full_property_text(
                parent=dialog,
                title=(
                    f"Property Key: {selected_field}"
                    if item.column() == 0
                    else f"Property Value: {selected_field}"
                ),
                content=item.text(),
            )
        )

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

    def _show_full_property_text(self, *, parent: QWidget, title: str, content: str) -> None:
        """Show a read-only popup containing full property text."""
        dialog = QDialog(parent)
        dialog.setWindowTitle(title)
        dialog.resize(760, 420)

        layout = QVBoxLayout(dialog)
        viewer = QPlainTextEdit(dialog)
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QPlainTextEdit.NoWrap)
        viewer.setPlainText(content)
        viewer.moveCursor(QTextCursor.MoveOperation.Start)

        controls_row = QHBoxLayout()
        controls_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        controls_row.addWidget(close_button)

        layout.addWidget(viewer)
        layout.addLayout(controls_row)
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
        if isinstance(raw_properties, Mapping):
            return dict(raw_properties)
        logger.warning(
            "Expected structured properties mapping, got %s",
            type(raw_properties).__name__,
        )
        return {}

    def set_field_list_visible_rows(self, row_count: int) -> None:
        """Size the field list to show a target number of rows by default."""
        row_height = self.host.field_list_widget.sizeHintForRow(0)
        if row_height <= 0:
            row_height = self.host.field_list_widget.fontMetrics().lineSpacing() + 6

        frame = self.host.field_list_widget.frameWidth() * 2
        height = (row_height * row_count) + frame
        self.host.field_list_widget.setMinimumHeight(height)
        self.host.field_list_widget.setMaximumHeight(height)

    def populate_field_list(
        self,
        fields: Sequence[object],
        *,
        append: bool = False,
        source_file: str | None = None,
        generated: bool = False,
    ) -> None:
        """Populate the field list UI from worker metadata."""
        if not append:
            self.host.field_list_widget.clear()
            setattr(self.host, "_field_source_color_by_path", {})
            setattr(self.host, "_field_source_color_index", 0)

        added_count = 0

        for field in fields:
            if not isinstance(field, Mapping):
                raise TypeError(
                    "Field metadata row must be a mapping with keys "
                    "'identity', 'detail', and 'properties'"
                )

            identity = str(field.get("identity", ""))
            detail = str(field.get("detail", identity))
            properties = field.get("properties", {})
            chunk_shape = str(field.get("chunk_shape", ""))
            if not isinstance(properties, Mapping):
                raise TypeError("Field metadata 'properties' must be a mapping")

            row_generated_raw = field.get("generated")
            row_generated = bool(row_generated_raw) if isinstance(row_generated_raw, bool) else bool(generated)
            row_source_raw = field.get("source_file")
            if isinstance(row_source_raw, str) and row_source_raw.strip():
                row_source = row_source_raw.strip()
            elif source_file and not row_generated:
                row_source = source_file
            else:
                row_source = ""
            row_color = QColor("#ffffff") if row_generated else (self._source_color(row_source) if row_source else None)

            item = QListWidgetItem(identity)
            item.setData(Qt.UserRole, detail)
            item.setData(Qt.UserRole + 1, properties)
            item.setData(Qt.UserRole + 3, chunk_shape)
            item.setData(Qt.UserRole + 4, identity)
            item.setData(Qt.UserRole + 5, row_generated)
            if row_source and not row_generated:
                item.setData(Qt.UserRole + 2, row_source)
            if row_color is not None:
                item.setBackground(row_color)
            if row_generated:
                item.setForeground(QColor("#b00020"))
            font = item.font()
            font.setItalic(row_generated)
            item.setFont(font)
            self.host.field_list_widget.addItem(item)
            added_count += 1

        self.renumber_field_list()
        self.set_field_list_visible_rows(self.host._field_list_rows())
        total_count = self.host.field_list_widget.count()
        if append:
            source_note = f" from {Path(source_file).name}" if (source_file and not generated) else ""
            self.set_selection_info_text(
                f"Added {added_count} fields{source_note}. Total loaded: {total_count}.\n"
                "Click an entry to show field details."
            )
        else:
            self.set_selection_info_text(
                f"Loaded {total_count} fields.\n"
                "Click an entry to show field details."
            )

        refresh_menu = getattr(self.host, "_refresh_open_files_menu", None)
        if callable(refresh_menu):
            refresh_menu()
        logger.info("Displayed %d fields in list", self.host.field_list_widget.count())

    @staticmethod
    def _format_field_display_label(index: int, identity: str) -> str:
        """Format list row labels with a fixed-width numeric prefix."""
        return f"{index:02d} {identity}"

    def field_identity_from_item(self, item: QListWidgetItem | None) -> str:
        """Return stable field identity text independent of displayed list prefix."""
        if item is None:
            return ""
        raw = item.data(Qt.UserRole + 4)
        if isinstance(raw, str) and raw:
            return raw
        return item.text()

    def renumber_field_list(self) -> None:
        """Reapply fixed-width index prefixes for all visible field list rows."""
        for idx in range(self.host.field_list_widget.count()):
            item = self.host.field_list_widget.item(idx)
            if item is None:
                continue
            identity = self.field_identity_from_item(item)
            item.setText(self._format_field_display_label(idx, identity))

    def mark_selected_items_saved(self, source_file: str) -> int:
        """
        Mark currently selected generated rows as saved under a source file.
        """
        #Note that we are making use of Qt's use of UserRole and above 
        #for application specific data (so we use the slots to define
        #them for specific purposes).  In this case, we use UserRole+2 to 
        #store the source file) and UserRole+5 to store whether the field 
        #is generated (True) or not (False).
        selected_items = list(self.host.field_list_widget.selectedItems())
        if not selected_items:
            return 0

        color = self._source_color(source_file)
        updated = 0
        for item in selected_items:
            is_generated = item.data(Qt.UserRole + 5)
            if is_generated is not True:
                 continue
            item.setData(Qt.UserRole + 2, source_file)
            item.setData(Qt.UserRole + 5, False)
            item.setBackground(color)
            item.setForeground(QColor("#000000"))
            font = item.font()
            font.setItalic(False)
            item.setFont(font)
            updated += 1

        return updated

    def _source_color(self, source_file: str) -> QColor:
        """Return a stable light tint for each source file in multi-file mode."""
        color_by_path = getattr(self.host, "_field_source_color_by_path", None)
        if not isinstance(color_by_path, dict):
            color_by_path = {}
            setattr(self.host, "_field_source_color_by_path", color_by_path)

        existing = color_by_path.get(source_file)
        if isinstance(existing, QColor):
            return existing

        palette = (
            QColor("#f6f8d7"),
            QColor("#d9f0ff"),
            QColor("#ffe7d6"),
            QColor("#e6defa"),
            QColor("#d7f6ea"),
        )

        color_index = int(getattr(self.host, "_field_source_color_index", 0))
        color = palette[color_index % len(palette)]
        setattr(self.host, "_field_source_color_index", color_index + 1)
        color_by_path[source_file] = color
        return color

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
