from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QInputDialog, QLabel, QVBoxLayout, QWidget
from superqt import QRangeSlider

from xconv2.cf_templates import collapse_methods
from cftime import num2date 

if TYPE_CHECKING:
    from xconv2.core_window import CFVCore

logger = logging.getLogger(__name__)


class SelectionController:
    """Manage slider/collapse widgets and plot-summary availability state."""

    def __init__(self, host: "CFVCore") -> None:
        self.host = host

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

            slider = QRangeSlider(Qt.Horizontal)
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

        self.refresh_plot_summary()
        logger.info("Built %d dynamic sliders", len(self.host.controls))

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
            method, ok = QInputDialog.getItem(
                self.host,
                "Collapse Method",
                f"Select collapse method for {name}:",
                collapse_methods,
                current_index,
                False,
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
        logging.info("Formatting time coordinate value '%s' with units '%s' (%s)", text, time_units, delta)
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                return text
        date = num2date(value, time_units, calendar=calendar)
        if delta is not None and delta > 86399:
            return date.strftime("%Y-%m-%d")
        else:
            s = date.strftime("%Y-%m-%d\n%H:%M:%S")
            return s

    def refresh_plot_summary(self) -> None:
        """Update plot summary text and plot button availability."""
        if not self.host.controls:
            self.host.plot_summary_label.setText("Open a field to inspect plot options.")
            self.host.plot_button.setEnabled(False)
            self.host.options_button.setEnabled(False)
            self.host.save_code_button.setEnabled(False)
            self.host.save_plot_button.setEnabled(False)
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
        dims_text = f"Selection dimensions = {dims}"

        if varying_dims == 0:
            self.host.plot_summary_label.setText(f"{dims_text} Total collapse, plot not possible")
            self.host.plot_button.setEnabled(False)
            self.host.options_button.setEnabled(False)
            self.host.save_code_button.setEnabled(False)
            self.host.save_plot_button.setEnabled(False)
        elif varying_dims == 1:
            self.host.plot_summary_label.setText(f"{dims_text} Lineplot possible")
            self.host.plot_button.setEnabled(True)
            self.host.options_button.setEnabled(True)
            self.host.save_code_button.setEnabled(True)
            self.host.save_plot_button.setEnabled(True)
        elif varying_dims == 2:
            self.host.plot_summary_label.setText(f"{dims_text} Contour possible")
            self.host.plot_button.setEnabled(True)
            self.host.options_button.setEnabled(True)
            self.host.save_code_button.setEnabled(True)
            self.host.save_plot_button.setEnabled(True)
        else:
            self.host.plot_summary_label.setText(
                f"{dims_text} Need to reduce to 1 or 2 dimensions before plotting"
            )
            self.host.plot_button.setEnabled(False)
            self.host.options_button.setEnabled(False)
            self.host.save_code_button.setEnabled(False)
            self.host.save_plot_button.setEnabled(False)
