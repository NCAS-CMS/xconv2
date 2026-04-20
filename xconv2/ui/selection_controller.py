from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget
)
from superqt import QRangeSlider

from xconv2.cf_templates import collapse_methods
from cftime import num2date
from .dialogs import InputDialogCustom

if TYPE_CHECKING:
    from xconv2.core_window import CFVCore

logger = logging.getLogger(__name__)


class KeyboardRangeSlider(QRangeSlider):
    """QRangeSlider variant with arrow-key control for the active handle."""

    def __init__(
        self,
        orientation: Qt.Orientation,
        parent: QWidget | None = None,
        navigate_callback: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(orientation, parent)
        self._active_handle = 0
        self._navigate_callback = navigate_callback
        self._default_bar_color = QBrush(self.barColor)
        self._focus_bar_color = QBrush(QColor("magenta"))
        self.setFocusPolicy(Qt.StrongFocus)

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        self._setBarColor(self._focus_bar_color)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self._setBarColor(self._default_bar_color)

    def _event_axis_pos(self, event: QMouseEvent) -> int:
        pos = event.position()
        return int(round(pos.x())) if self.orientation() == Qt.Horizontal else int(round(pos.y()))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        super().mousePressEvent(event)
        self.setFocus(Qt.MouseFocusReason)

        lo, hi = self.value()
        pos_value = self._pixelPosToRangeValue(self._event_axis_pos(event))
        self._active_handle = 0 if abs(pos_value - lo) <= abs(pos_value - hi) else 1

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()

        # Move focus between sliders when using up/down keys.
        if key == Qt.Key_Up and self._navigate_callback is not None:
            self._navigate_callback(-1)
            event.accept()
            return
        if key == Qt.Key_Down and self._navigate_callback is not None:
            self._navigate_callback(1)
            event.accept()
            return

        if self.orientation() == Qt.Horizontal:
            negative_keys = {Qt.Key_Left}
            positive_keys = {Qt.Key_Right}
        else:
            negative_keys = {Qt.Key_Down}
            positive_keys = {Qt.Key_Up}

        if key not in negative_keys and key not in positive_keys:
            super().keyPressEvent(event)
            return

        step = max(int(self.singleStep()), 1)
        delta = -step if key in negative_keys else step

        lo, hi = [int(x) for x in self.value()]
        minimum = int(self.minimum())
        maximum = int(self.maximum())

        if self._active_handle == 0:
            new_lo = max(minimum, min(hi, lo + delta))
            self.setValue((new_lo, hi))
        else:
            new_hi = min(maximum, max(lo, hi + delta))
            self.setValue((lo, new_hi))

        event.accept()


class SelectionController:
    """Manage slider/collapse widgets and plot-summary availability state."""

    def __init__(self, host: "CFVCore") -> None:
        self.host = host

    def _set_save_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable save mode selector and save action button."""
        save_combo = getattr(self.host, "save_target_combo", None)
        save_button = getattr(self.host, "save_go_button", None)
        legacy_save_code_button = getattr(self.host, "save_code_button", None)
        legacy_save_plot_button = getattr(self.host, "save_plot_button", None)
        if save_combo is not None:
            save_combo.setEnabled(enabled)
        if save_button is not None:
            save_button.setEnabled(enabled)
        if legacy_save_code_button is not None:
            legacy_save_code_button.setEnabled(enabled)
        if legacy_save_plot_button is not None:
            legacy_save_plot_button.setEnabled(enabled)

    def reset_all_sliders(self) -> None:
        """Reset all slider ranges to full extent and refresh summary state."""
        self.host.selected_collapse_methods.clear()

        for name, control in self.host.controls.items():
            slider = control.get("range_slider")
            collapse_checkbox = control.get("collapse_checkbox")
            values = control.get("values", [])

            if collapse_checkbox is not None:
                collapse_checkbox.blockSignals(True)
                collapse_checkbox.setChecked(False)
                collapse_checkbox.blockSignals(False)
                collapse_checkbox.setText("")

            if slider is None or not values:
                continue

            slider.blockSignals(True)
            slider.setValue((0, len(values) - 1))
            slider.blockSignals(False)

            self.update_range_labels(name)

        self.refresh_plot_summary()

    def build_dynamic_sliders(self, metadata: dict[str, object]) -> None:
        """Build compact dual-handle range sliders from coordinate metadata."""
        self.host.controls.clear()
        self.host.selected_counts.clear()
        self.host.selected_collapse_methods.clear()
        self.host.available_plot_kinds = []
        self.host.selected_plot_kind = None

        for i in reversed(range(self.host.sidebar.count())):
            widget = self.host.sidebar.itemAt(i).widget()
            if widget is not None:
                widget.setParent(None)

        for name, entry in metadata.items():
            if isinstance(entry, dict):
                values = entry.get("values", [])
                units = str(entry.get("units", "") or "")
            else:
                values = entry
                units = ""

            if not values:
                continue

            container = QWidget()
            row = QVBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)

            header_row = QHBoxLayout()
            header_row.setContentsMargins(0, 0, 0, 0)
            header_row.setSpacing(4)
            name_label = QLabel(f"{name.upper()}:")
            collapse_label = QLabel("collapse")
            collapse_checkbox = QCheckBox("")
            collapse_checkbox.setToolTip("Select a collapse method")
            collapse_checkbox.toggled.connect(
                lambda checked, n=name: self.on_collapse_toggled(n, checked)
            )

            header_row.addWidget(name_label)
            header_row.addStretch(1)
            header_row.addWidget(collapse_label)
            header_row.addWidget(collapse_checkbox)

            selection_label = QLabel()
            selection_label.setWordWrap(True)
            selection_label.setContentsMargins(0, 0, 0, 0)

            bounds_start_label = QLabel(str(values[0]))
            bounds_end_label = QLabel(str(values[-1]))
            bounds_start_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            bounds_end_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            slider = KeyboardRangeSlider(
                Qt.Horizontal,
                navigate_callback=lambda step, n=name: self._focus_adjacent_slider(n, step),
            )
            slider.setRange(0, len(values) - 1)
            slider.setValue((0, len(values) - 1))
            slider.valueChanged.connect(
                lambda _value, n=name: self.on_range_slider_moved(n)
            )

            slider_row = QHBoxLayout()
            slider_row.setContentsMargins(0, 0, 0, 0)
            slider_row.setSpacing(4)
            slider_row.addWidget(bounds_start_label)
            slider_row.addWidget(slider, 1)
            slider_row.addWidget(bounds_end_label)

            row.addLayout(header_row)
            row.addWidget(selection_label)
            row.addLayout(slider_row)
            self.host.sidebar.addWidget(container)
            self.host.controls[name] = {
                "range_slider": slider,
                "name_label": name_label,
                "selection_label": selection_label,
                "bounds_start_label": bounds_start_label,
                "bounds_end_label": bounds_end_label,
                "collapse_checkbox": collapse_checkbox,
                "values": values,
                "units": units,
            }
            self.update_range_labels(name)

        self.host._set_slider_scroll_visible_rows(len(self.host.controls))
        self.refresh_plot_summary()
        logger.info("Built %d dynamic sliders", len(self.host.controls))

    def _focus_adjacent_slider(self, current_name: str, offset: int) -> None:
        """Move keyboard focus to the previous/next slider in display order."""
        names = list(self.host.controls.keys())
        if not names:
            return

        try:
            idx = names.index(current_name)
        except ValueError:
            return

        next_idx = max(0, min(len(names) - 1, idx + offset))
        if next_idx == idx:
            return

        next_slider = self.host.controls[names[next_idx]].get("range_slider")
        if next_slider is not None:
            next_slider.setFocus(Qt.TabFocusReason)

    def on_range_slider_moved(self, name: str) -> None:
        """Handle dual-handle range slider movement."""
        control = self.host.controls.get(name)
        if control is None:
            return

        slider = control["range_slider"]
        start_idx, end_idx = slider.value()

        self.update_range_labels(name)
        self.refresh_plot_summary()
        logger.debug("Range slider moved: %s start=%d end=%d", name, start_idx, end_idx)

    def on_collapse_toggled(self, name: str, checked: bool) -> None:
        """Choose and persist a collapse method for the coordinate."""
        control = self.host.controls.get(name)
        if control is None:
            return

        collapse_checkbox = control["collapse_checkbox"]
        if checked:
            if not collapse_methods:
                collapse_checkbox.blockSignals(True)
                collapse_checkbox.setChecked(False)
                collapse_checkbox.blockSignals(False)
                self.host.selected_collapse_methods.pop(name, None)
                collapse_checkbox.setText("")
                self.host.status.showMessage("No collapse methods configured.")
                return

            current_method = self.host.selected_collapse_methods.get(name, collapse_methods[0])
            current_index = (
                collapse_methods.index(current_method)
                if current_method in collapse_methods
                else 0
            )
            method, ok = InputDialogCustom.getItem(
                self.host,
                "Collapse Method",
                f"Select collapse method for {name}:",
                collapse_methods,
                current_index,
                False,
                doc_text=(
                    'Documentation for collapse methods can be found '
                    '<a href="https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods">online</a>.'
                ),
            )
            if ok and method:
                self.host.selected_collapse_methods[name] = method
                collapse_checkbox.setText(f"({method})")
            else:
                collapse_checkbox.blockSignals(True)
                collapse_checkbox.setChecked(False)
                collapse_checkbox.blockSignals(False)
                self.host.selected_collapse_methods.pop(name, None)
                collapse_checkbox.setText("")
                return
        else:
            self.host.selected_collapse_methods.pop(name, None)
            collapse_checkbox.setText("")

        self.update_range_labels(name)
        self.refresh_plot_summary()

    def update_range_labels(self, name: str) -> None:
        """Refresh compact summary line for current range selection."""
        control = self.host.controls.get(name)
        if control is None:
            return

        values = control["values"]
        units = str(control.get("units", "") or "")
        delta = self._axis_delta(values)
        start_idx, end_idx = control["range_slider"].value()
        lo_idx = int(min(start_idx, end_idx))
        hi_idx = int(max(start_idx, end_idx))
        selected_count = hi_idx - lo_idx
        self.host.selected_counts[name] = selected_count

        singleton_idx = self._singleton_index(lo_idx, hi_idx, len(values))
        if singleton_idx is not None:
            lo_text = self.format_slider_label_value(values[singleton_idx], units, delta)
            hi_text = lo_text
        else:
            lo_text = self.format_slider_label_value(values[lo_idx], units, delta)
            hi_text = self.format_slider_label_value(values[hi_idx], units, delta)

        control["bounds_start_label"].setText(self.format_slider_label_value(values[0], units, delta))
        control["bounds_end_label"].setText(self.format_slider_label_value(values[-1], units, delta))

        if '\n' in lo_text:
            lo_text = lo_text.replace('\n', ' ')
            hi_text = hi_text.replace('\n', ' ')
            output = f"selected: {lo_text} -->\n                {hi_text} ({selected_count})"
        else:
            output = f"selected: {lo_text} --> {hi_text} ({selected_count})"   

        control["selection_label"].setText(
            output
        )

    @staticmethod
    def _singleton_index(lo_idx: int, hi_idx: int, total_count: int) -> int | None:
        """Pick a singleton index for near-collapsed handles (distance <= 1)."""
        if (hi_idx - lo_idx) > 1:
            return None

        if lo_idx == 0:
            return lo_idx
        if hi_idx == (total_count - 1):
            return hi_idx
        return lo_idx

    @staticmethod
    def _format_coord_value(value: object) -> str:
        """Format numeric coordinate labels compactly while preserving text values."""
        if isinstance(value, bool):
            return str(value)

        if isinstance(value, (int, float)):
            return f"{value:g}"

        text = str(value)
        try:
            numeric = float(text)
        except ValueError:
            return text

        return f"{numeric:g}"

    @staticmethod
    def _parse_time_units(units: str) -> tuple[str, str | None] | None:
        """Parse simple CF-like time units and optional calendar token."""
        normalized_units = units.strip()
        if "since" not in normalized_units:
            return None

        parts = normalized_units.split()
        if len(parts) >= 3:
            time_units = " ".join(parts[:3])
        else:
            time_units = normalized_units

        calendar = parts[3] if len(parts) == 4 else None
        return time_units, calendar

    @staticmethod
    def _axis_delta(values: list[object]) -> float | None:
        """Return first-step delta if first two coordinate values are numeric."""
        if len(values) < 2:
            return None

        first, second = values[0], values[1]
        if isinstance(first, bool) or isinstance(second, bool):
            return None

        try:
            return float(second) - float(first)
        except (TypeError, ValueError):
            return None

    def format_slider_label_value(self, value: object, units: str, delta: float | None) -> str:
        """Format a slider label value. Customize this hook for time labels."""
        text = self._format_coord_value(value)
        parsed_time = self._parse_time_units(units)
        if parsed_time is None:
            return text

        time_units, calendar = parsed_time
        #logging.info("Formatting time coordinate value '%s' with units '%s' (%s)", text, time_units, delta)
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                return text
        if calendar:
            date = num2date(value, time_units, calendar=calendar)
        else:
            date = num2date(value, time_units)
        if delta is not None and delta > 86399:
            return date.strftime("%Y-%m-%d")
        else:
            s = date.strftime("%Y-%m-%d\n%H:%M:%S")
            return s

    def refresh_plot_summary(self) -> None:
        """Update plot summary text and plot button availability."""
        if not self.host.controls:
            self.host.plot_summary_label.setText("Open a field to inspect plot options.")
            self.host.plot_info_button.hide()
            self.host.last_varying_dims = None
            self.host.available_plot_kinds = []
            self.host.selected_plot_kind = None
            self.host.plot_view_controller.set_plot_type_options([], None)
            self.host.plot_button.setEnabled(False)
            self.host.options_button.setEnabled(False)
            self._set_save_controls_enabled(False)
            return

        dims: list[int] = []
        for name, control in self.host.controls.items():
            if name in self.host.selected_collapse_methods:
                dims.append(1)
                continue

            start_idx, end_idx = control["range_slider"].value()
            lo_idx = int(min(start_idx, end_idx))
            hi_idx = int(max(start_idx, end_idx))
            dims.append(1 if (hi_idx - lo_idx) <= 1 else 2)

        varying_dims = sum(1 for dim in dims if dim != 1)
        dims_text = f"Selection Dimensions: {varying_dims}D"

        if varying_dims == 1:
            available_plot_kinds = ["lineplot"]
        elif varying_dims == 2:
            available_plot_kinds = ["lineplot", "contour", "vector"]
        else:
            available_plot_kinds = []

        previous_kind = self.host.selected_plot_kind
        previous_varying_dims = self.host.last_varying_dims
        entering_2d = varying_dims == 2 and previous_varying_dims != 2

        if entering_2d:
            selected_kind = "contour"
        elif previous_kind in available_plot_kinds:
            selected_kind = previous_kind
        elif "contour" in available_plot_kinds:
            selected_kind = "contour"
        elif available_plot_kinds:
            selected_kind = available_plot_kinds[0]
        else:
            selected_kind = None

        self.host.available_plot_kinds = available_plot_kinds
        self.host.selected_plot_kind = selected_kind
        self.host.last_varying_dims = varying_dims
        self.host.plot_view_controller.set_plot_type_options(available_plot_kinds, selected_kind)

        if varying_dims == 0:
            self.host.plot_summary_label.setText(f"{dims_text} \nTotal collapse, plot not possible")
            self.host.plot_info_button.show()
            self.host.plot_button.setEnabled(False)
            self.host.options_button.setEnabled(False)
            self._set_save_controls_enabled(False)
        elif varying_dims == 1:
            self.host.plot_summary_label.setText(
                f"{dims_text} \nPlot Type: {selected_kind.title() if selected_kind else 'N/A'}"
            )
            self.host.plot_info_button.show()
            self.host.plot_button.setEnabled(True)
            self.host.options_button.setEnabled(selected_kind in {"contour", "lineplot"})
            self._set_save_controls_enabled(True)
        elif varying_dims == 2:
            self.host.plot_summary_label.setText(
                f"{dims_text} \nPlot Type: {selected_kind.title() if selected_kind else 'N/A'}"
            )
            self.host.plot_info_button.show()
            self.host.plot_button.setEnabled(True)
            self.host.options_button.setEnabled(selected_kind in {"contour", "lineplot"})
            self._set_save_controls_enabled(True)
        else:
            self.host.plot_summary_label.setText(
                f"{dims_text} \nNeed to reduce to 1D or 2D before plotting"
            )
            self.host.plot_info_button.show()
            self.host.plot_button.setEnabled(False)
            self.host.options_button.setEnabled(False)
            self._set_save_controls_enabled(False)
