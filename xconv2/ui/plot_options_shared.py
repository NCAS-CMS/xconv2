from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from xconv2.core_window import CFVCore


def _normalize_annotation_display_text(value: object) -> str:
    """Normalize annotation key/value text for compact dialog display."""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@dataclass
class CommonOptionsSection:
    """UI and state handles for shared title/annotation option sections."""

    titles_group: QGroupBox
    annotations_group: QGroupBox
    title_edit: QLineEdit
    page_title_edit: QLineEdit
    page_title_display_checkbox: QCheckBox
    annotation_display_checkbox: QCheckBox
    free_text_edit: QLineEdit
    top_margin_spin: QDoubleSpinBox
    bottom_margin_spin: QDoubleSpinBox
    selected_annotation_props: list[tuple[str, str]]

    def as_options(self) -> dict[str, object]:
        """Serialize common section widget values into plot options."""
        options: dict[str, object] = {}

        title_text = self.title_edit.text().strip()
        if title_text:
            options["title"] = title_text

        page_title_text = self.page_title_edit.text().strip()
        options["page_title_display"] = bool(self.page_title_display_checkbox.isChecked())
        if options["page_title_display"] and page_title_text:
            options["page_title"] = page_title_text

        options["page_margin_top"] = float(self.top_margin_spin.value())
        options["page_margin_bottom"] = float(self.bottom_margin_spin.value())

        free_text = self.free_text_edit.text().strip()
        if free_text:
            options["annotation_free_text"] = free_text

        options["annotation_display"] = bool(self.annotation_display_checkbox.isChecked())
        if self.selected_annotation_props:
            max_selected = 3 if free_text else 4
            options["annotation_properties"] = self.selected_annotation_props[:max_selected]

        return options


def build_common_options_sections(
    *,
    host: "CFVCore",
    existing: dict[str, object],
    plot_title_label: str,
    plot_title_placeholder: str,
    suggested_title: str | None = None,
) -> CommonOptionsSection:
    """Build reusable Titles and Annotations sections for plot options dialogs."""
    default_title = existing.get("title")
    if not default_title:
        default_title = suggested_title
    if not default_title:
        default_title = Path(host.current_file_path).name if host.current_file_path else ""

    default_page_title = existing.get("page_title")
    if not default_page_title:
        default_page_title = Path(host.current_file_path).name if host.current_file_path else ""

    titles_group = QGroupBox("Titles")
    titles_layout = QVBoxLayout(titles_group)

    title_row = QHBoxLayout()
    title_label = QLabel(plot_title_label)
    title_edit = QLineEdit(str(default_title))
    title_edit.setPlaceholderText(plot_title_placeholder)
    title_row.addWidget(title_label)
    title_row.addWidget(title_edit, 1)
    titles_layout.addLayout(title_row)

    page_title_row = QHBoxLayout()
    page_title_label = QLabel("page title")
    page_title_edit = QLineEdit(str(default_page_title))
    page_title_edit.setPlaceholderText("Figure page title")
    page_title_display_checkbox = QCheckBox("display")
    page_title_display_checkbox.setChecked(bool(existing.get("page_title_display", False)))
    page_title_row.addWidget(page_title_label)
    page_title_row.addWidget(page_title_edit, 1)
    page_title_row.addWidget(page_title_display_checkbox)
    titles_layout.addLayout(page_title_row)

    annotations_group = QGroupBox("Choose annotation properties")
    annotations_layout = QVBoxLayout(annotations_group)

    selected_annotation_props: list[tuple[str, str]] = []
    existing_props = existing.get("annotation_properties", [])
    if isinstance(existing_props, list):
        for entry in existing_props:
            if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                selected_annotation_props.append(
                    (
                        _normalize_annotation_display_text(entry[0]),
                        _normalize_annotation_display_text(entry[1]),
                    )
                )

    free_text_row = QHBoxLayout()
    free_text_label = QLabel("free text")
    free_text_edit = QLineEdit(str(existing.get("annotation_free_text", "")))
    free_text_edit.setPlaceholderText("Optional custom annotation text")
    free_text_row.addWidget(free_text_label)
    free_text_row.addWidget(free_text_edit, 1)
    annotations_layout.addLayout(free_text_row)

    annotation_limit_label = QLabel()
    annotation_limit_label.setStyleSheet("color: #666;")

    def _refresh_annotation_limit_hint() -> None:
        max_selected = 3 if free_text_edit.text().strip() else 4
        annotation_limit_label.setText(f"Annotation property limit: {max_selected}")

    free_text_edit.textChanged.connect(lambda _text: _refresh_annotation_limit_hint())
    _refresh_annotation_limit_hint()
    annotations_layout.addWidget(annotation_limit_label)

    top_margin_spin = QDoubleSpinBox()
    top_margin_spin.setRange(0.0, 0.20)
    top_margin_spin.setDecimals(3)
    top_margin_spin.setSingleStep(0.005)
    top_margin_spin.setValue(float(existing.get("page_margin_top", 0.0) or 0.0))
    top_margin_spin.setToolTip("Extra figure-fraction space above plot for page title")

    bottom_margin_spin = QDoubleSpinBox()
    bottom_margin_spin.setRange(0.0, 0.20)
    bottom_margin_spin.setDecimals(3)
    bottom_margin_spin.setSingleStep(0.005)
    bottom_margin_spin.setValue(float(existing.get("page_margin_bottom", 0.0) or 0.0))
    bottom_margin_spin.setToolTip("Extra figure-fraction space below plot for annotations")

    annotation_row = QHBoxLayout()
    choose_annotations_button = QPushButton("Select annotations from properties")
    # Keep annotation selection explicit: Enter in text fields should not invoke this button.
    choose_annotations_button.setAutoDefault(False)
    choose_annotations_button.setDefault(False)
    annotation_display_checkbox = QCheckBox("display annotations")
    annotation_display_checkbox.setChecked(bool(existing.get("annotation_display", False)))

    annotation_preview = QLabel()
    annotation_preview.setWordWrap(True)
    annotation_preview.setStyleSheet("color: #444;")

    def _refresh_annotation_preview() -> None:
        if not selected_annotation_props:
            annotation_preview.setText("No annotation properties selected")
            annotation_preview.setToolTip("")
            return
        count = len(selected_annotation_props)
        annotation_preview.setText(f"Selected annotation properties: {count}")
        annotation_preview.setToolTip(
            "\n".join(f"{key}: {value}" for key, value in selected_annotation_props)
        )

    def _maybe_enable_annotation_display() -> None:
        has_free_text = bool(free_text_edit.text().strip())
        has_props = bool(selected_annotation_props)
        if has_free_text or has_props:
            annotation_display_checkbox.setChecked(True)

    def _choose_annotation_properties() -> None:
        selected_item = host.field_list_widget.currentItem()
        if selected_item is None:
            host.status.showMessage("Select a field before choosing annotation properties")
            return

        raw_properties = selected_item.data(Qt.UserRole + 1)
        properties = host._parse_properties_dict(raw_properties)
        if not properties:
            host.status.showMessage("No properties available for annotation")
            return

        max_selected = 3 if free_text_edit.text().strip() else 4
        if len(selected_annotation_props) > max_selected:
            selected_annotation_props[:] = selected_annotation_props[:max_selected]

        chosen = host._show_annotation_properties_chooser(
            properties,
            selected_annotation_props,
            max_selected=max_selected,
        )
        if chosen is not None:
            selected_annotation_props.clear()
            selected_annotation_props.extend(chosen)
            _refresh_annotation_preview()
            _maybe_enable_annotation_display()

    choose_annotations_button.clicked.connect(_choose_annotation_properties)
    free_text_edit.textChanged.connect(lambda _text: _maybe_enable_annotation_display())
    _refresh_annotation_preview()

    annotation_row.addWidget(choose_annotations_button)
    annotation_row.addStretch(1)
    annotation_row.addWidget(annotation_display_checkbox)
    annotations_layout.addLayout(annotation_row)
    annotations_layout.addWidget(annotation_preview)

    margin_row = QHBoxLayout()
    layout_label = QLabel("Layout:")
    top_margin_label = QLabel("top margin")
    bottom_margin_label = QLabel("bottom margin")
    margin_row.addWidget(layout_label)
    margin_row.addWidget(top_margin_label)
    margin_row.addWidget(top_margin_spin)
    margin_row.addSpacing(10)
    margin_row.addWidget(bottom_margin_label)
    margin_row.addWidget(bottom_margin_spin)
    margin_row.addStretch(1)
    annotations_layout.addLayout(margin_row)

    return CommonOptionsSection(
        titles_group=titles_group,
        annotations_group=annotations_group,
        title_edit=title_edit,
        page_title_edit=page_title_edit,
        page_title_display_checkbox=page_title_display_checkbox,
        annotation_display_checkbox=annotation_display_checkbox,
        free_text_edit=free_text_edit,
        top_margin_spin=top_margin_spin,
        bottom_margin_spin=bottom_margin_spin,
        selected_annotation_props=selected_annotation_props,
    )