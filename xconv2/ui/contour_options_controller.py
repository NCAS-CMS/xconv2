from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHeaderView,
)

from xconv2.cf_templates import map_projections, map_resolution_options, use_lon_0
from xconv2.colour_scales import cscales, get_colour_scale_hexes
from xconv2.ui.plot_options_shared import build_common_options_sections

if TYPE_CHECKING:
    from xconv2.core_window import CFVCore


class ContourOptionsController:
    """Encapsulate contour options dialog and chooser helpers."""

    def __init__(self, host: "CFVCore") -> None:
        self.host = host
        self._options_dialog: QDialog | None = None

    @staticmethod
    def _normalize_property_cell_text(value: object) -> str:
        """Normalize property text for stable, single-line chooser table cells."""
        text = str(value).replace("\r\n", "\n").replace("\r", "\n")
        return " ".join(text.split()).strip()

    def show_contour_options_dialog(
        self,
        range_min: float,
        range_max: float,
        suggested_title: str | None = None,
    ) -> None:
        """Show contour options dialog and persist selected options."""
        if self._options_dialog is not None and self._options_dialog.isVisible():
            self._options_dialog.raise_()
            self._options_dialog.activateWindow()
            return

        existing = self.host.plot_options_by_kind.get("contour", {})

        dialog = QDialog(self.host)
        self._options_dialog = dialog
        dialog.setWindowTitle("Contour Options")
        dialog.setWindowModality(Qt.NonModal)
        dialog.resize(540, 360)
        dialog.finished.connect(lambda _result: setattr(self, "_options_dialog", None))

        layout = QVBoxLayout(dialog)
        common = build_common_options_sections(
            host=self.host,
            existing=existing,
            plot_title_label="contour title",
            plot_title_placeholder="Contour title",
            suggested_title=suggested_title,
        )

        levels_group = QGroupBox("Contour levels")
        levels_layout = QVBoxLayout(levels_group)
        levels_layout.addWidget(QLabel(f"Field range: min={range_min:g}, max={range_max:g}"))

        default_radio = QRadioButton("Default - let matplotlib decide")
        auto_radio = QRadioButton("Use min/max + intervals")
        explicit_radio = QRadioButton("Use explicit contour levels (comma-separated)")
        mode_group = QButtonGroup(dialog)
        mode_group.addButton(default_radio)
        mode_group.addButton(auto_radio)
        mode_group.addButton(explicit_radio)

        auto_row = QHBoxLayout()
        min_label = QLabel("min")
        min_edit = QLineEdit(str(existing.get("min", range_min)))
        max_label = QLabel("max")
        max_edit = QLineEdit(str(existing.get("max", range_max)))
        intervals_label = QLabel("intervals")
        intervals_spin = QSpinBox()
        intervals_spin.setRange(1, 200)
        intervals_spin.setValue(int(existing.get("intervals", 12)))

        auto_row.addWidget(min_label)
        auto_row.addWidget(min_edit)
        auto_row.addWidget(max_label)
        auto_row.addWidget(max_edit)
        auto_row.addWidget(intervals_label)
        auto_row.addWidget(intervals_spin)

        explicit_levels = existing.get("levels", [])
        explicit_levels_text = ""
        if isinstance(explicit_levels, list):
            explicit_levels_text = ", ".join(str(v) for v in explicit_levels)
        explicit_edit = QLineEdit(explicit_levels_text)
        explicit_edit.setPlaceholderText("e.g. -2, -1, 0, 1, 2")

        if existing.get("mode") == "explicit":
            explicit_radio.setChecked(True)
        elif existing.get("mode") == "auto":
            auto_radio.setChecked(True)
        else:
            default_radio.setChecked(True)

        def _sync_mode() -> None:
            use_auto = auto_radio.isChecked()
            use_explicit = explicit_radio.isChecked()
            min_edit.setEnabled(use_auto)
            max_edit.setEnabled(use_auto)
            intervals_spin.setEnabled(use_auto)
            explicit_edit.setEnabled(use_explicit)

        default_radio.toggled.connect(_sync_mode)
        auto_radio.toggled.connect(_sync_mode)
        explicit_radio.toggled.connect(_sync_mode)
        _sync_mode()

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        plot_button = QPushButton("Apply")
        ok_button = QPushButton("Apply && Close")
        button_row.addWidget(cancel_button)
        button_row.addWidget(plot_button)
        button_row.addWidget(ok_button)
        cancel_button.clicked.connect(dialog.reject)

        levels_layout.addWidget(default_radio)
        levels_layout.addWidget(auto_radio)
        levels_layout.addLayout(auto_row)
        levels_layout.addWidget(explicit_radio)
        levels_layout.addWidget(explicit_edit)

        style_group = QGroupBox("Contour style")
        style_layout = QVBoxLayout(style_group)

        selected_cscale: dict[str, str | None] = {"value": existing.get("cscale")}

        cscale_row = QVBoxLayout()
        cscale_header_row = QHBoxLayout()
        cscale_label = QLabel("colour scale")
        cscale_value_label = QLabel()
        choose_cscale_button = QPushButton("Choose...")
        choose_cscale_button.setAutoDefault(False)
        choose_cscale_button.setDefault(False)
        cscale_value_label.setStyleSheet("font-weight: 700;")
        cscale_row.setContentsMargins(0, 0, 0, 0)
        cscale_row.setSpacing(2)
        cscale_header_row.setContentsMargins(0, 0, 0, 0)
        cscale_header_row.setSpacing(6)

        def _update_cscale_label() -> None:
            value = selected_cscale.get("value")
            cscale_value_label.setText(str(value) if value else "default")

        def _choose_cscale() -> None:
            chosen = self.show_colour_scale_chooser(selected_cscale.get("value"))
            if chosen:
                selected_cscale["value"] = chosen
                _update_cscale_label()

        choose_cscale_button.clicked.connect(_choose_cscale)
        _update_cscale_label()

        cscale_header_row.addWidget(cscale_label)
        cscale_header_row.addStretch(1)
        cscale_header_row.addWidget(choose_cscale_button)
        cscale_header_row.setAlignment(cscale_label, Qt.AlignTop)
        cscale_header_row.setAlignment(choose_cscale_button, Qt.AlignTop)
        cscale_row.addLayout(cscale_header_row)
        cscale_row.addWidget(cscale_value_label)

        fill_checkbox = QCheckBox("fill")
        fill_checkbox.setChecked(bool(existing.get("fill", True)))

        lines_checkbox = QCheckBox("lines")
        lines_checkbox.setChecked(bool(existing.get("lines", False)))

        line_labels_checkbox = QCheckBox("line_labels")
        line_labels_checkbox.setChecked(bool(existing.get("line_labels", True)))

        negative_row = QHBoxLayout()
        negative_label = QLabel("negative_linestyle")
        negative_style_combo = QComboBox()
        negative_style_combo.addItems(["solid", "dashed"])
        current_negative = str(existing.get("negative_linestyle", "solid"))
        idx = negative_style_combo.findText(current_negative)
        negative_style_combo.setCurrentIndex(idx if idx >= 0 else 0)
        negative_row.addWidget(negative_label)
        negative_row.addWidget(negative_style_combo)

        zero_row = QHBoxLayout()
        zero_label = QLabel("zero_thick")
        zero_thick_spin = QDoubleSpinBox()
        zero_thick_spin.setRange(0.0, 20.0)
        zero_thick_spin.setDecimals(2)
        zero_thick_spin.setSingleStep(0.5)
        zero_thick_spin.setToolTip("0.0 disables thick zero contour")
        existing_zero = existing.get("zero_thick", False)
        zero_thick_spin.setValue(0.0 if existing_zero in (False, None) else float(existing_zero))
        zero_row.addWidget(zero_label)
        zero_row.addWidget(zero_thick_spin)

        blockfill_checkbox = QCheckBox("blockfill")
        blockfill_checkbox.setChecked(bool(existing.get("blockfill", False)))

        blockfill_fast_checkbox = QCheckBox("blockfill_fast (pcolormesh)")
        blockfill_fast_checkbox.setChecked(bool(existing.get("blockfill_fast", None)))

        def _sync_line_labels() -> None:
            line_labels_checkbox.setEnabled(lines_checkbox.isChecked())
            if not lines_checkbox.isChecked():
                line_labels_checkbox.setChecked(False)

        lines_checkbox.toggled.connect(_sync_line_labels)
        _sync_line_labels()

        style_top_row = QHBoxLayout()
        style_checks_col = QVBoxLayout()
        style_cscale_col = QVBoxLayout()

        style_checks_col.addWidget(fill_checkbox)
        style_checks_col.addWidget(lines_checkbox)
        style_checks_col.addWidget(line_labels_checkbox)
        style_checks_col.addStretch(1)

        style_cscale_col.addLayout(cscale_row)
        style_cscale_col.addStretch(1)

        style_top_row.addLayout(style_checks_col, 1)
        style_top_row.addLayout(style_cscale_col, 1)

        style_detail_grid = QGridLayout()
        style_detail_grid.setContentsMargins(0, 0, 0, 0)
        style_detail_grid.setHorizontalSpacing(12)
        style_detail_grid.addLayout(negative_row, 0, 0)
        style_detail_grid.addLayout(zero_row, 0, 1)
        style_detail_grid.addWidget(blockfill_fast_checkbox, 1, 0)
        style_detail_grid.addWidget(blockfill_checkbox, 1, 1)
        style_detail_grid.setColumnStretch(0, 1)
        style_detail_grid.setColumnStretch(1, 1)

        style_layout.addLayout(style_top_row)
        style_layout.addLayout(style_detail_grid)

        text_sizes_group = QGroupBox("Text Sizes")
        text_sizes_layout = QGridLayout(text_sizes_group)
        text_sizes_layout.setContentsMargins(9, 9, 9, 9)
        text_sizes_layout.setHorizontalSpacing(12)
        text_sizes_layout.setVerticalSpacing(6)

        contour_title_fontsize_label = QLabel("contour title")
        contour_title_fontsize_spin = QDoubleSpinBox()
        contour_title_fontsize_spin.setRange(1.0, 48.0)
        contour_title_fontsize_spin.setDecimals(1)
        contour_title_fontsize_spin.setSingleStep(0.5)
        contour_title_fontsize_spin.setValue(
            float(existing.get("contour_title_fontsize", self.host._contour_title_fontsize()))
        )

        page_title_fontsize_label = QLabel("page title")
        page_title_fontsize_spin = QDoubleSpinBox()
        page_title_fontsize_spin.setRange(1.0, 48.0)
        page_title_fontsize_spin.setDecimals(1)
        page_title_fontsize_spin.setSingleStep(0.5)
        page_title_fontsize_spin.setValue(
            float(existing.get("page_title_fontsize", self.host._page_title_fontsize()))
        )

        annotation_fontsize_label = QLabel("annotations")
        annotation_fontsize_spin = QDoubleSpinBox()
        annotation_fontsize_spin.setRange(1.0, 48.0)
        annotation_fontsize_spin.setDecimals(1)
        annotation_fontsize_spin.setSingleStep(0.5)
        annotation_fontsize_spin.setValue(
            float(existing.get("annotation_fontsize", self.host._annotation_fontsize()))
        )

        reset_text_sizes_button = QPushButton("Reset to GUI defaults")

        def _reset_text_sizes() -> None:
            contour_title_fontsize_spin.setValue(self.host._contour_title_fontsize())
            page_title_fontsize_spin.setValue(self.host._page_title_fontsize())
            annotation_fontsize_spin.setValue(self.host._annotation_fontsize())

        reset_text_sizes_button.clicked.connect(_reset_text_sizes)

        text_sizes_layout.addWidget(contour_title_fontsize_label, 0, 0)
        text_sizes_layout.addWidget(page_title_fontsize_label, 0, 1)
        text_sizes_layout.addWidget(annotation_fontsize_label, 0, 2)
        text_sizes_layout.addWidget(contour_title_fontsize_spin, 1, 0)
        text_sizes_layout.addWidget(page_title_fontsize_spin, 1, 1)
        text_sizes_layout.addWidget(annotation_fontsize_spin, 1, 2)
        text_sizes_layout.addWidget(reset_text_sizes_button, 2, 0, 1, 3, Qt.AlignRight)
        text_sizes_layout.setColumnStretch(0, 1)
        text_sizes_layout.setColumnStretch(1, 1)
        text_sizes_layout.setColumnStretch(2, 1)

        proj_group = QGroupBox("Map projection")
        proj_layout = QVBoxLayout(proj_group)
        proj_layout.setContentsMargins(9, 4, 9, 4)
        proj_layout.setSpacing(4)

        # Under-construction notice
        uc_notice_row = QHBoxLayout()
        uc_notice_row.setSpacing(6)
        uc_icon_label = QLabel()
        _uc_icon_path = Path(__file__).resolve().parent.parent / "assets" / "under-construction.svg"
        _uc_pixmap = QPixmap(str(_uc_icon_path))
        if not _uc_pixmap.isNull():
            uc_icon_label.setPixmap(_uc_pixmap.scaledToHeight(16, Qt.SmoothTransformation))
        uc_notice_label = QLabel(
            "Some map projections and associated selections may not work properly "
            "in this version of xconv2, we will fix these in a future version."
        )
        uc_notice_label.setWordWrap(True)
        uc_notice_label.setStyleSheet("color: #888888; font-style: italic;")
        uc_notice_row.addWidget(uc_icon_label, 0, Qt.AlignTop)
        uc_notice_row.addWidget(uc_notice_label, 1)
        proj_layout.addLayout(uc_notice_row)

        proj_row = QHBoxLayout()
        proj_combo_label = QLabel("Projection")
        proj_combo = QComboBox()
        proj_combo.setMinimumContentsLength(10)
        proj_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        for name, description in map_projections.items():
            proj_combo.addItem(name)
            proj_combo.setItemData(proj_combo.count() - 1, description, Qt.ToolTipRole)
        current_projection = str(existing.get("map_projection", "cyl") or "cyl")
        current_projection_index = proj_combo.findText(current_projection)
        proj_combo.setCurrentIndex(current_projection_index if current_projection_index >= 0 else 0)

        reset_map_defaults_button = QPushButton("Reset Map")
        reset_map_defaults_button.setAutoDefault(False)
        reset_map_defaults_button.setDefault(False)

        projection_help_tooltip = (
            "<table>"
            + "".join(
                f"<tr><td><b>{name}</b></td><td>{description}</td></tr>"
                for name, description in map_projections.items()
            )
            + "</table>"
        )
        proj_combo_label.setToolTip(projection_help_tooltip)
        proj_row.addWidget(proj_combo_label)
        proj_row.addWidget(proj_combo)
        proj_row.addSpacing(10)
        res_label = QLabel("Resolution")
        res_combo = QComboBox()
        for value in map_resolution_options:
            res_combo.addItem(value)
        current_resolution = str(existing.get("map_resolution", "110m") or "110m")
        current_resolution_index = res_combo.findText(current_resolution)
        res_combo.setCurrentIndex(current_resolution_index if current_resolution_index >= 0 else 0)
        proj_row.addWidget(res_label)
        proj_row.addWidget(res_combo)
        proj_row.addWidget(reset_map_defaults_button)
        proj_row.addStretch(1)
        proj_layout.addLayout(proj_row)

        existing_bbox = existing.get("bbox", [-180.0, -90.0, 180.0, 90.0])
        if not isinstance(existing_bbox, (list, tuple)) or len(existing_bbox) < 4:
            existing_bbox = [-180.0, -90.0, 180.0, 90.0]

        bbox_row = QHBoxLayout()
        bbox_row.setSpacing(4)
        bbox_label = QLabel("Bound Box (WSEN):")
        bbox_w_spin = QDoubleSpinBox()
        bbox_s_spin = QDoubleSpinBox()
        bbox_e_spin = QDoubleSpinBox()
        bbox_n_spin = QDoubleSpinBox()
        bbox_w_spin.setRange(-180.0, 360.0)
        bbox_w_spin.setDecimals(2)
        bbox_w_spin.setSingleStep(1.0)
        bbox_w_spin.setValue(float(existing_bbox[0]))
        bbox_s_spin.setRange(-90.0, 90.0)
        bbox_s_spin.setDecimals(2)
        bbox_s_spin.setSingleStep(1.0)
        bbox_s_spin.setValue(float(existing_bbox[1]))
        bbox_e_spin.setRange(-180.0, 360.0)
        bbox_e_spin.setDecimals(2)
        bbox_e_spin.setSingleStep(1.0)
        bbox_e_spin.setValue(float(existing_bbox[2]))
        bbox_n_spin.setRange(-90.0, 90.0)
        bbox_n_spin.setDecimals(2)
        bbox_n_spin.setSingleStep(1.0)
        bbox_n_spin.setValue(float(existing_bbox[3]))
        bbox_row.addWidget(bbox_label)
        bbox_row.addWidget(bbox_w_spin)
        bbox_row.addWidget(bbox_s_spin)
        bbox_row.addWidget(bbox_e_spin)
        bbox_row.addWidget(bbox_n_spin)
        proj_layout.addLayout(bbox_row)

        lat_lon_row = QHBoxLayout()
        boundinglat_label = QLabel("boundinglat")
        boundinglat_spin = QDoubleSpinBox()
        boundinglat_spin.setRange(-90.0, 90.0)
        boundinglat_spin.setDecimals(2)
        boundinglat_spin.setSingleStep(1.0)
        boundinglat_spin.setValue(float(existing.get("boundinglat", 0.0)))
        lat_0_label = QLabel("lat_0")
        lat_0_spin = QDoubleSpinBox()
        lat_0_spin.setRange(-90.0, 90.0)
        lat_0_spin.setDecimals(2)
        lat_0_spin.setSingleStep(1.0)
        lat_0_spin.setValue(float(existing.get("lat_0", 0.0)))
        lon_0_label = QLabel("lon_0")
        lon_0_spin = QDoubleSpinBox()
        lon_0_spin.setRange(-180.0, 360.0)
        lon_0_spin.setDecimals(2)
        lon_0_spin.setSingleStep(1.0)
        lon_0_spin.setValue(float(existing.get("lon_0", 0.0)))
        lat_lon_row.addWidget(boundinglat_label)
        lat_lon_row.addWidget(boundinglat_spin)
        lat_lon_row.addSpacing(10)
        lat_lon_row.addWidget(lat_0_label)
        lat_lon_row.addWidget(lat_0_spin)
        lat_lon_row.addSpacing(10)
        lat_lon_row.addWidget(lon_0_label)
        lat_lon_row.addWidget(lon_0_spin)
        lat_lon_row.addStretch(1)
        proj_layout.addLayout(lat_lon_row)

        bbox_controls = [bbox_label, bbox_w_spin, bbox_s_spin, bbox_e_spin, bbox_n_spin]
        stereographic_controls = [boundinglat_label, boundinglat_spin]
        other_proj_controls = [lat_0_label, lat_0_spin, lon_0_label, lon_0_spin]

        _projection_state: dict[str, str] = {"previous": proj_combo.currentText()}

        def _sync_projection_controls() -> None:
            projection = proj_combo.currentText()
            description = proj_combo.currentData(Qt.ToolTipRole)
            proj_combo.setToolTip(str(description) if description else projection)
            uses_boundinglat = projection in {"npstere", "spstere"}
            for widget in bbox_controls:
                widget.setEnabled(not uses_boundinglat)
            for widget in stereographic_controls:
                widget.setEnabled(uses_boundinglat)
            # lat_0 only enabled for ortho projection
            lat_0_label.setEnabled(projection == "ortho")
            lat_0_spin.setEnabled(projection == "ortho")
            # lon_0 enabled only for projections in use_lon_0 tuple
            uses_lon_0 = projection in use_lon_0
            lon_0_label.setEnabled(uses_lon_0)
            lon_0_spin.setEnabled(uses_lon_0)

            previous_projection = _projection_state.get("previous", "")
            if projection != previous_projection:
                if projection == "npstere":
                    boundinglat_spin.setValue(45.0)
                elif projection == "spstere":
                    boundinglat_spin.setValue(-45.0)
                elif not uses_boundinglat:
                    boundinglat_spin.setValue(0.0)
                _projection_state["previous"] = projection

        def _reset_map_defaults() -> None:
            proj_combo.setCurrentText("cyl")
            bbox_w_spin.setValue(-180.0)
            bbox_s_spin.setValue(-90.0)
            bbox_e_spin.setValue(180.0)
            bbox_n_spin.setValue(90.0)
            boundinglat_spin.setValue(0.0)
            lat_0_spin.setValue(0.0)
            lon_0_spin.setValue(0.0)
            res_combo.setCurrentText("110m")

        reset_map_defaults_button.clicked.connect(_reset_map_defaults)

        proj_combo.currentTextChanged.connect(lambda _text: _sync_projection_controls())
        _sync_projection_controls()

        layout.addWidget(common.titles_group)
        layout.addWidget(common.annotations_group)
        layout.addWidget(proj_group)
        layout.addWidget(levels_group)
        layout.addWidget(style_group)
        layout.addWidget(text_sizes_group)
        layout.addLayout(button_row)

        def _apply_options() -> bool:
            if default_radio.isChecked():
                options = {"mode": "default"}
            elif explicit_radio.isChecked():
                raw_levels = [piece.strip() for piece in explicit_edit.text().split(",") if piece.strip()]
                try:
                    levels = [float(piece) for piece in raw_levels]
                except ValueError:
                    self.host.status.showMessage(
                        "Invalid explicit contour levels; expected comma-separated numbers"
                    )
                    return False

                if len(levels) < 2:
                    self.host.status.showMessage("Please provide at least two contour levels")
                    return False

                options = {
                    "mode": "explicit",
                    "levels": levels,
                }
            else:
                try:
                    user_min = float(min_edit.text().strip())
                    user_max = float(max_edit.text().strip())
                except ValueError:
                    self.host.status.showMessage("Invalid contour min/max values")
                    return False

                if user_min == user_max:
                    self.host.status.showMessage("Contour min and max must differ")
                    return False

                lo, hi = sorted((user_min, user_max))
                options = {
                    "mode": "auto",
                    "min": lo,
                    "max": hi,
                    "intervals": int(intervals_spin.value()),
                }

            options["fill"] = bool(fill_checkbox.isChecked())
            options["lines"] = bool(lines_checkbox.isChecked())
            options["line_labels"] = bool(line_labels_checkbox.isChecked())
            options["negative_linestyle"] = str(negative_style_combo.currentText())
            zero_thick_value = float(zero_thick_spin.value())
            options["zero_thick"] = zero_thick_value if zero_thick_value > 0 else False
            options["blockfill"] = bool(blockfill_checkbox.isChecked())
            options["blockfill_fast"] = True if blockfill_fast_checkbox.isChecked() else None
            options["contour_title_fontsize"] = float(contour_title_fontsize_spin.value())
            options["page_title_fontsize"] = float(page_title_fontsize_spin.value())
            options["annotation_fontsize"] = float(annotation_fontsize_spin.value())
            proj_text = proj_combo.currentText()
            if proj_text:
                options["map_projection"] = proj_text
            if proj_text in {"npstere", "spstere"}:
                options["boundinglat"] = float(boundinglat_spin.value())
                options.pop("bbox", None)
            else:
                options["bbox"] = [
                    float(bbox_w_spin.value()),
                    float(bbox_s_spin.value()),
                    float(bbox_e_spin.value()),
                    float(bbox_n_spin.value()),
                ]
                options.pop("boundinglat", None)
            options["map_resolution"] = str(res_combo.currentText())
            options["lat_0"] = float(lat_0_spin.value())
            options["lon_0"] = float(lon_0_spin.value())
            options.update(common.as_options())
            if selected_cscale.get("value"):
                options["cscale"] = selected_cscale["value"]

            self.host.plot_options_by_kind["contour"] = options
            self.host.status.showMessage("Updated contour options")
            self.host._request_plot_update()
            return True

        ok_button.clicked.connect(lambda: dialog.accept() if _apply_options() else None)
        plot_button.clicked.connect(_apply_options)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def show_annotation_properties_chooser(
        self,
        properties: dict[object, object],
        current_selected: list[tuple[str, str]],
        max_selected: int = 4,
    ) -> list[tuple[str, str]] | None:
        """Show a chooser for up to ``max_selected`` annotation properties."""
        dialog = QDialog(self.host)
        dialog.setWindowTitle("Choose annotation properties")
        dialog.resize(640, 420)

        layout = QVBoxLayout(dialog)
        hint = QLabel(f"Select up to {max_selected} properties to annotate on plots")
        layout.addWidget(hint)

        table = QTableWidget(len(properties), 2, dialog)
        table.setHorizontalHeaderLabels(["Property", "Value"])
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        selected_set = {
            (
                self._normalize_property_cell_text(k),
                self._normalize_property_cell_text(v),
            )
            for k, v in current_selected
        }

        for row, (key, value) in enumerate(sorted(properties.items(), key=lambda kv: str(kv[0]).lower())):
            key_text = self._normalize_property_cell_text(key)
            value_text = self._normalize_property_cell_text(value)

            key_item = QTableWidgetItem(key_text)
            key_item.setFlags(key_item.flags() | Qt.ItemIsUserCheckable)
            key_item.setCheckState(
                Qt.Checked if (key_text, value_text) in selected_set else Qt.Unchecked
            )
            value_item = QTableWidgetItem(value_text)
            value_item.setToolTip(value_text)

            table.setItem(row, 0, key_item)
            table.setItem(row, 1, value_item)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        layout.addWidget(table)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None

        selected: list[tuple[str, str]] = []
        for row in range(table.rowCount()):
            key_item = table.item(row, 0)
            value_item = table.item(row, 1)
            if key_item is None or value_item is None:
                continue
            if key_item.checkState() == Qt.Checked:
                selected.append((key_item.text(), value_item.text()))

        if len(selected) > max_selected:
            QMessageBox.warning(
                self.host,
                "Too many properties",
                f"Please select at most {max_selected} annotation properties.",
            )
            return None

        return selected

    def show_colour_scale_chooser(self, current_scale: str | None) -> str | None:
        """Show colour scale chooser with preview bars and return selected name."""
        dialog = QDialog(self.host)
        dialog.setWindowTitle("Choose colour scale")
        dialog.resize(760, 560)

        layout = QVBoxLayout(dialog)

        table = QTableWidget(len(cscales), 2, dialog)
        table.setHorizontalHeaderLabels(["Scale", "Preview"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setWordWrap(False)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        selected_row = 0
        for row, name in enumerate(cscales):
            name_item = QTableWidgetItem(name)
            table.setItem(row, 0, name_item)

            preview_label = QLabel()
            preview_label.setPixmap(self.build_colour_scale_preview(name, width=420, height=14))
            table.setCellWidget(row, 1, preview_label)
            table.setRowHeight(row, 22)

            if current_scale and name == current_scale:
                selected_row = row

        table.selectRow(selected_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        table.doubleClicked.connect(dialog.accept)

        layout.addWidget(table)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None

        row = table.currentRow()
        if row < 0:
            return None

        item = table.item(row, 0)
        return item.text() if item else None

    def build_colour_scale_preview(self, scale_name: str, width: int, height: int) -> QPixmap:
        """Build a small horizontal preview pixmap for a cf-plot colour scale."""
        colors = get_colour_scale_hexes(scale_name)
        if not colors:
            pixmap = QPixmap(width, height)
            pixmap.fill(Qt.lightGray)
            return pixmap

        image = QImage(width, height, QImage.Format_RGB32)
        n = len(colors)
        for x in range(width):
            idx = int((x / max(width - 1, 1)) * max(n - 1, 0))
            color_name = colors[idx]
            color = QColor(color_name)
            if not color.isValid():
                color = QColor("#aaaaaa")
            for y in range(height):
                image.setPixelColor(x, y, color)

        return QPixmap.fromImage(image)
